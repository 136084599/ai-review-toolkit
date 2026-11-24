import base64
import math
import re
import subprocess
import tempfile
import threading
import time
from bisect import bisect_right
from pathlib import Path

import av
import imageio_ffmpeg

from app.review import Cancelled, ReviewError, check_cancel
from app.quality import parse_technical
from app.schemas import VideoOptions

_ocr = None
_asr = None
_asr_path = None
_model_lock = threading.Lock()


def probe(path):
    try:
        with av.open(str(path), options={"protocol_whitelist": "file,pipe", "format_whitelist": "mov,matroska,webm"}) as container:
            if not container.streams.video:
                raise ReviewError("文件没有可读取的视频轨道")
            stream = container.streams.video[0]
            duration = float(container.duration or 0) / av.time_base
            if duration <= 0 and stream.duration is not None:
                duration = float(stream.duration * stream.time_base)
            if not math.isfinite(duration) or duration <= 0:
                raise ReviewError("无法确认视频时长，请先导出为标准 MP4")
            audio = container.streams.audio[0] if container.streams.audio else None
            return {"duration": round(duration, 3), "width": stream.width, "height": stream.height,
                    "fps": round(float(stream.average_rate or 0), 2), "has_audio": bool(audio),
                    "video_codec": stream.codec_context.name, "video_bitrate": stream.bit_rate,
                    "audio_codec": audio.codec_context.name if audio else None,
                    "sample_rate": audio.codec_context.sample_rate if audio else None,
                    "channels": audio.codec_context.channels if audio else 0}
    except ReviewError:
        raise
    except (av.FFmpegError, OSError, ValueError) as exc:
        raise ReviewError("无法读取视频，请检查文件是否损坏或重新导出为 MP4") from exc


def run_ffmpeg(args, event, timeout=600):
    # Tempfile prevents pipe deadlocks and unbounded in-memory FFmpeg logs.
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-y", *args],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        start = time.monotonic()
        try:
            while process.poll() is None:
                check_cancel(event)
                if time.monotonic() - start > timeout:
                    raise ReviewError("视频处理超时，请分段后重试")
                event.wait(0.2)
            log.seek(0)
            output = log.read().decode("utf-8", errors="replace")
            if process.returncode:
                raise ReviewError("视频解码失败，请转为 H.264 MP4 后重试")
            return output
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def technical_checks(path, metadata, event, options=None, details=False):
    options = options or VideoOptions()
    filters = ["scale=w='min(640,iw)':h=-2", "blackdetect=d=0.4:pix_th=0.10:pic_th=0.98"]
    if options.freeze_check:
        filters.append(f"freezedetect=n=-50dB:d={options.freeze_seconds:g}")
    if options.scene_check:
        filters.append("scdet=threshold=10")
    args = ["-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm", "-i", str(path), "-threads", "2", "-vf", ",".join(filters)]
    if metadata["has_audio"]:
        audio_filters = "silencedetect=noise=-45dB:d=2"
        if options.audio_check:
            audio_filters += ",astats=metadata=0:reset=0,ebur128=peak=true:framelog=verbose"
        args += ["-af", audio_filters]
    output = run_ffmpeg([*args, "-f", "null", "-"], event)
    issues = []
    def add(title, evidence, start, end):
        issues.append({"severity": "medium", "category": "音画技术", "title": title, "evidence": evidence,
            "suggestion": "回放该时间段，确认是否为有意的片头、转场或留白。", "source_id": "technical",
            "source": "technical", "start": float(start), "end": float(end), "engine": "ffmpeg", "needs_verification": True})
    for start, end in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", output):
        add("检测到连续黑场", f"画面接近全黑：{start}–{end} 秒", start, end)
    silence_start = None
    for match in re.finditer(r"silence_(start|end):\s*(-?[\d.]+)", output):
        kind, value = match.groups()
        if kind == "start":
            silence_start = str(max(0, float(value)))
        elif silence_start is not None:
            add("检测到长静音", f"音量低于 -45dB：{silence_start}–{value} 秒", silence_start, value)
            silence_start = None
    if silence_start is not None:
        add("片尾持续静音", "低于 -45dB 的静音延续到片尾", silence_start, metadata["duration"])
    if not metadata["has_audio"]:
        add("视频没有音轨", "未发现音频轨道", 0, metadata["duration"])
    extended = parse_technical(output, metadata, options)
    extended["issues"] = issues + extended["issues"]
    return extended if details else extended["issues"]


def read_subtitles(text, duration):
    if len(text) > 200000:
        raise ReviewError("字幕文件过大，请限制在 20 万字符以内")
    pattern = r"(?:(\d{1,3}):)?(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(?:(\d{1,3}):)?(\d{2}):(\d{2})[,.](\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)"
    sources = []
    parsed = 0
    for match in re.finditer(pattern, text.replace("\r\n", "\n"), re.S):
        parsed += 1
        parts = match.groups()
        def seconds(values):
            hour, minute, second, ms = values
            if int(minute) > 59 or int(second) > 59:
                raise ReviewError("字幕时间格式有误")
            return int(hour or 0) * 3600 + int(minute) * 60 + int(second) + int(ms) / 1000
        start, end = seconds(parts[:4]), seconds(parts[4:8])
        content = re.sub(r"<[^>]+>", "", parts[8]).strip()
        if start < 0 or end <= start or end > duration + 1:
            raise ReviewError("字幕时间超出视频范围或起止时间有误，请确认字幕匹配当前视频")
        if content:
            sources.append({"id": f"subtitle-{len(sources)+1}", "kind": "subtitle", "text": content, "start": start, "end": min(end, duration)})
    if not sources or parsed != text.count("-->"):
        raise ReviewError("没有解析到有效 SRT/VTT 字幕，请检查时间码格式")
    return sources


def transcribe(path, directory, settings, event):
    global _asr, _asr_path
    if not (settings.model_dir / "model.bin").exists():
        raise ReviewError("语音模型尚未安装。运行“安装语音模型.cmd”后可自动转写；本次未检查实际口播")
    audio_path = directory / "audio.wav"
    run_ffmpeg(["-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(audio_path)], event)
    try:
        from faster_whisper import WhisperModel
        with _model_lock:
            if _asr is None or _asr_path != str(settings.model_dir):
                _asr = WhisperModel(str(settings.model_dir), device="cpu", compute_type="int8", cpu_threads=4,
                                    local_files_only=True)
                _asr_path = str(settings.model_dir)
        segments, _ = _asr.transcribe(str(audio_path), language="zh", beam_size=5, vad_filter=True, word_timestamps=True,
            condition_on_previous_text=False, initial_prompt="以下是中文新闻报道、政务宣传、央媒供稿。")
        sources = []
        deadline = time.monotonic() + 1800
        for segment in segments:
            check_cancel(event)
            if time.monotonic() > deadline:
                raise ReviewError("语音转写超时，请拆分长视频后重试")
            if segment.text.strip():
                sources.append({"id": f"speech-{len(sources)+1}", "kind": "speech", "text": segment.text.strip(),
                                "start": round(segment.start, 2), "end": round(segment.end, 2),
                                "words": [{"text":word.word, "start":round(word.start,2), "end":round(word.end,2)}
                                          for word in segment.words or []]})
        return sources
    finally:
        audio_path.unlink(missing_ok=True)


def get_ocr():
    global _ocr
    with _model_lock:
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    return _ocr


def scan_frames(path, directory, metadata, event, progress, interval=2, scenes=None):
    engine = get_ocr()
    frames, sources = [], []
    next_sample, last_ocr, low_confidence = 0.0, None, 0
    scenes = scenes or []
    # At most 300 extra scene representatives; regular interval sampling is preserved.
    representatives = scenes if len(scenes) <= 300 else [scenes[round(i*(len(scenes)-1)/299)] for i in range(300)]
    scene_samples = iter((scene["start"]+scene["end"])/2 for scene in representatives)
    next_scene = next(scene_samples, float("inf"))
    scene_starts = [scene["start"] for scene in scenes]
    deadline = time.monotonic() + 1200
    with av.open(str(path), options={"protocol_whitelist": "file,pipe", "format_whitelist": "mov,matroska,webm"}) as container:
        stream = container.streams.video[0]
        stream.thread_count = 2
        origin = float((stream.start_time or 0) * stream.time_base)
        for frame in container.decode(stream):
            check_cancel(event)
            if time.monotonic() > deadline:
                raise ReviewError("画面识别超时，请拆分长视频后重试")
            timestamp = max(0, float(frame.time or 0) - origin)
            regular, representative = timestamp + .001 >= next_sample, timestamp + .001 >= next_scene
            if not regular and not representative:
                continue
            if regular:
                next_sample = timestamp + interval
            if representative:
                while next_scene <= timestamp + .001:
                    next_scene = next(scene_samples, float("inf"))
            image = frame.to_image()
            image.thumbnail((1280, 1280))
            frame_id = f"frame-{len(frames)+1}"
            image_path = directory / f"{frame_id}.jpg"
            image.save(image_path, quality=82)
            frames.append({"id": frame_id, "kind": "frame", "text": f"视频画面 {timestamp:.2f} 秒",
                           "start": round(timestamp, 2), "end": round(timestamp, 2), "file": image_path.name,
                           "fingerprint": image.resize((32, 18)).convert("L").tobytes().hex(),
                           "shot": bisect_right(scene_starts,timestamp) if scenes else None,
                           "representative": representative})
            result, _ = engine(str(image_path))
            rows = result or []
            frames[-1]["boxes"] = [{"text":str(row[1]),"confidence":round(float(row[2]),3),
                "bounds":[round(min(point[0] for point in row[0])/image.width,4),
                          round(min(point[1] for point in row[0])/image.height,4),
                          round(max(point[0] for point in row[0])/image.width,4),
                          round(max(point[1] for point in row[0])/image.height,4)]}
                for row in rows if float(row[2]) >= .8]
            low_confidence += sum(float(row[2]) < 0.8 for row in rows)
            text = "\n".join(str(row[1]) for row in rows if float(row[2]) >= 0.8).strip()
            if text:
                if last_ocr and last_ocr["text"] == text and timestamp - last_ocr["end"] <= interval * 1.6:
                    last_ocr["end"] = round(timestamp, 2)
                else:
                    last_ocr = {"id": f"ocr-{len(sources)+1}", "kind": "ocr", "text": text,
                                "start": round(timestamp, 2), "end": round(timestamp, 2), "frame_id": frame_id}
                    sources.append(last_ocr)
            else:
                last_ocr = None
            if len(frames) % 5 == 0:
                progress(25 + int(30 * min(timestamp / metadata["duration"], 1)), f"识别字幕与画面 · {timestamp:.0f}/{metadata['duration']:.0f} 秒")
    if not frames:
        raise ReviewError("视频未解码出有效画面")
    return frames, sources, low_confidence


def pick_vision_frames(frames, limit=24):
    # Merge adjacent near-identical scenes. OCR still runs at every sampling point.
    representatives = {}
    for frame in frames:
        if frame.get("shot") and (frame["shot"] not in representatives or frame.get("representative")):
            representatives[frame["shot"]] = frame
    # Prefer one stable frame per detected shot; include changes within very long shots.
    priority = list(representatives.values())
    candidates = []
    previous = None
    for frame in frames:
        fingerprint = bytes.fromhex(frame["fingerprint"]) if frame.get("fingerprint") else None
        if fingerprint and previous and sum(abs(a-b) for a,b in zip(fingerprint, previous)) / len(fingerprint) < 2.5:
            continue
        candidates.append(frame)
        previous = fingerprint
    if len(priority) > limit:
        return [priority[round(i*(len(priority)-1)/(limit-1))] for i in range(limit)]
    preferred_ids = {frame["id"] for frame in priority}
    extras = []
    for frame in candidates:
        if frame.get("id") in preferred_ids:
            continue
        representative = representatives.get(frame.get("shot"))
        if representative and frame.get("fingerprint") and representative.get("fingerprint"):
            first, second = bytes.fromhex(frame["fingerprint"]), bytes.fromhex(representative["fingerprint"])
            if sum(abs(a-b) for a,b in zip(first,second))/len(first) < 2.5:
                continue
        extras.append(frame)
    slots = limit-len(priority)
    if slots and len(extras) > slots:
        extras = [extras[round(i*(len(extras)-1)/max(1,slots-1))] for i in range(slots)]
    selected = [*priority, *extras[:slots]] if priority else extras[:limit]
    return sorted(selected, key=lambda frame: frame.get("start",0))


def image_data(directory, frame):
    return "data:image/jpeg;base64," + base64.b64encode((directory / frame["file"]).read_bytes()).decode("ascii")
