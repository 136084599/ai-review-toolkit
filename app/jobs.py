import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from app import media
from app.content import content_overview, select_context, timed_context
from app.quality import alignment_checks, caption_checks, caption_sources, glossary_checks
from app.schemas import VideoOptions
from app.review import Cancelled, DeepSeek, ReviewError, check_cancel, finalize, local_review, split_sources

logger = logging.getLogger(__name__)


class Runner:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="review")
        self.video_lock = threading.Lock()
        self.events = {}
        self.lock = threading.Lock()

    def submit(self, job_id):
        event = threading.Event()
        with self.lock:
            self.events[job_id] = event
        self.pool.submit(self.run, job_id, event)

    def cancel(self, job_id):
        with self.lock:
            event = self.events.get(job_id)
            if event:
                event.set()

    def close(self):
        with self.lock:
            for event in self.events.values():
                event.set()
        self.pool.shutdown(wait=True, cancel_futures=True)

    def run(self, job_id, event):
        job = self.store.get(job_id)
        if job is None:
            return
        def progress(value, stage):
            check_cancel(event)
            self.store.update(job_id, status="running", progress=value, stage=stage)
        try:
            check_cancel(event)
            if job["kind"] == "video":
                while not self.video_lock.acquire(timeout=0.3):
                    check_cancel(event)
                try:
                    report = self.video(job, event, progress)
                finally:
                    self.video_lock.release()
            else:
                report = self.text(job["payload"], event, progress)
            check_cancel(event)
            self.store.update(job_id, status="completed", progress=100, stage="预审完成", report=report)
        except Cancelled:
            self.store.update(job_id, status="cancelled", stage="已取消")
        except ReviewError as exc:
            self.store.update(job_id, status="failed", stage="未能完成", error=str(exc))
        except Exception:
            # Do not log request text, credentials, or third-party exception bodies.
            logger.error("Review job failed: %s", job_id)
            self.store.update(job_id, status="failed", stage="未能完成", error="处理发生异常，请确认文件格式，或重启后重试")
        finally:
            with self.lock:
                self.events.pop(job_id, None)

    def text(self, payload, event, progress):
        progress(15, "检查文字与格式")
        sources = [{"id": "text-1", "kind": "text", "text": payload["text"]}]
        if payload.get("title") and payload["title"] != "未命名稿件":
            sources.append({"id": "title-1", "kind": "title", "text": payload["title"]})
        issues, warnings, summaries = local_review(sources), [], []
        coverage = [{"name": "文字规则检查", "status": "complete", "detail": "日期、重复标点、隐私信息与数字格式"}]
        if payload["use_ai"]:
            progress(40, "DeepSeek 正在预审正文与参考资料")
            try:
                found, summary, notes = DeepSeek(self.settings.key, self.settings.model).review(
                    sources, payload["reference"], payload["instructions"], event)
                issues.extend(found)
                summaries.append(summary)
                warnings.extend(notes)
                coverage.append({"name": "AI 语义与资料比对", "status": "complete", "detail": "全文预审；外部事实仍需人工核验"})
            except ReviewError as exc:
                warnings.append(str(exc))
                coverage.append({"name": "AI 语义与资料比对", "status": "failed", "detail": str(exc)})
        else:
            coverage.append({"name": "AI 语义与资料比对", "status": "skipped", "detail": "仅运行本地规则检查"})
        return finalize(issues, coverage, warnings, summaries, sources)

    def video(self, job, event, progress):
        payload = job["payload"]
        directory = self.settings.data / job["id"]
        path = directory / payload["filename"]
        metadata = payload["metadata"]
        options = VideoOptions.model_validate(payload.get("options", {}))
        issues, warnings, summaries, sources, coverage, frames = [], [], [], [], [], []
        technical, aligned, speech, subtitles = {"metrics":{}, "scenes":[]}, [], [], []
        def failed(name, exc):
            detail = str(exc) if isinstance(exc, ReviewError) else f"{name}执行失败，请人工复核该部分"
            warnings.append(detail)
            coverage.append({"name": name, "status": "failed", "detail": detail})
        progress(5, "全片检测音画质量与镜头变化")
        try:
            technical = media.technical_checks(path, metadata, event, options, True)
            issues.extend({**issue, "scope":"technical"} for issue in technical["issues"])
            coverage.append({"name": "音画技术检测", "status": "complete", "detail": "全片检测黑场、静音及已启用的冻结、短镜头检查；静态图卡可能正常"})
            if options.audio_check and metadata["has_audio"]:
                measured = "integrated_lufs" in technical["metrics"]
                coverage.append({"name":"响度与声道", "status":"complete" if measured else "failed", "detail":"全片测量 LUFS、真峰值及各声道 RMS；阈值为本次自设" if measured else "未得到有效音频测量结果"})
            if options.scene_check:
                coverage.append({"name":"镜头切分", "status":"complete", "detail":f"检测到 {len(technical['scenes'])} 个镜头；闪光、渐变可能造成误切或漏切"})
        except Cancelled:
            raise
        except Exception as exc:
            failed("音画技术检测", exc)
        progress(15, "识别实际口播")
        if metadata["has_audio"]:
            try:
                speech = media.transcribe(path, directory, self.settings, event)
                sources.extend(speech)
                if not speech:
                    raise ReviewError("没有识别出有效语音，不能确认口播内容，请人工试听")
                coverage.append({"name": "语音转写", "status": "complete", "detail": "本地中文转写；专名、数字仍需试听确认"})
            except Cancelled:
                raise
            except Exception as exc:
                failed("语音转写", exc)
        else:
            coverage.append({"name": "语音转写", "status": "not_applicable", "detail": "视频没有音轨"})
        if payload.get("subtitles"):
            subtitles = media.read_subtitles(payload["subtitles"], metadata["duration"])
            sources.extend(subtitles)
            coverage.append({"name": "上传字幕", "status": "complete", "detail": "字幕仅作比对依据，不代表实际口播"})
        progress(25, "抽帧识别画面文字")
        try:
            frames, ocr, low_conf = media.scan_frames(path, directory, metadata, event, progress, payload["interval"], technical["scenes"])
            sources.extend(ocr)
            coverage.append({"name": "画面文字 OCR", "status": "sampled", "detail": f"每 {payload['interval']} 秒采样并补充镜头代表帧，共 {len(frames)} 帧；跳过 {low_conf} 个低置信度文本框。短暂字幕可能遗漏"})
            if low_conf:
                warnings.append(f"{low_conf} 个低置信度文字框未参与审核，请在画面证据中复核小字与模糊字幕。")
        except Cancelled:
            raise
        except Exception as exc:
            failed("画面文字 OCR", exc)
        issues.extend(local_review(sources))
        progress(57, "检查字幕版式、时间轴及音字差异")
        captions = caption_sources(frames, payload["interval"], metadata["duration"])
        sources.extend(captions)
        if options.caption_check:
            issues.extend({**issue, "scope":"technical"} for issue in caption_checks(frames, subtitles, options))
            coverage.append({"name":"字幕版式与时间轴", "status":"sampled" if frames or subtitles else "failed",
                "detail":f"检查下半屏文字边距、字号；已{'检查上传字幕阅读速度与重叠' if subtitles else '跳过需配套 SRT/VTT 的速度与重叠检查'}。图卡下方文字也可能被当作字幕"})
        if options.alignment_check:
            if speech and (subtitles or captions):
                aligned, compared = alignment_checks(speech, subtitles or captions, authored=bool(subtitles))
                issues.extend({**issue, "scope":"content" if "文字差异" in issue["title"] else "technical", "dimension":"consistency"} for issue in compared)
                coverage.append({"name":"口播字幕时间对齐", "status":"sampled", "detail":f"对照 {len(aligned)} 条{'上传字幕' if subtitles else '底部 OCR 文字'}；检查文字差异{'、明显时间偏移与未覆盖口播' if subtitles else '，OCR 时间码为近似值'}；不是口型检测"})
            else:
                coverage.append({"name":"口播字幕时间对齐", "status":"skipped", "detail":"缺少有效口播或字幕证据，无法判断差异"})
        if options.glossary.strip():
            issues.extend({**issue, "scope":"content", "dimension":"identity"} for issue in glossary_checks(sources, options.glossary))
            coverage.append({"name":"自定义易错词", "status":"complete", "detail":"只在已识别文字中匹配本次词表；不代表核实专名或最新职务"})
        content_runs, content_gaps = [], []
        text_complete = vision_complete = False
        titles = ([{"id":"video-title", "kind":"title", "text":payload["publication_title"]}]
                  if payload.get("publication_title") else [])
        sources.extend(titles)
        if not titles:
            content_gaps.append("未填写拟发布标题；仅能检查已识别的片内标题/封面文字，不能评价发布标题。")
        if not payload["reference"].strip():
            content_gaps.append("未提供原始采访记录或权威参考资料；事实真伪、政策时效、身份与授权未核实。")
        def accept(result):
            content_runs.append(result)
            issues.extend(result["issues"])
            summaries.append(result["summary"])
            warnings.extend(result["warnings"])
            content_gaps.extend(result["warnings"])
        if payload["use_ai"] and sources:
            progress(60, "审读视频内容、新闻语境与政策表述")
            # Group by time so OCR, actual speech and supplied captions can be compared together.
            ordered = sorted((source for source in sources if source["kind"] not in ("caption", "title")), key=lambda s: s.get("start", 0))
            groups = split_sources(ordered)
            batch_limit = 12
            indexes = list(range(len(groups))) if len(groups) <= batch_limit else [round(i*(len(groups)-1)/(batch_limit-1)) for i in range(batch_limit)]
            if len(groups) > batch_limit:
                gap = f"文字证据过多，本次仅均匀抽取 {batch_limit}/{len(groups)} 批做 AI 审核；其余识别文字已保留，请拆分视频补查。"
                warnings.append(gap)
                content_gaps.append(gap)
            done = 0
            api_failed = False
            # One whole-video call suffices for short videos; longer videos also get local checks.
            for position, index in enumerate(indexes if len(groups) > 1 else []):
                group = groups[index]
                progress(60 + int(12 * position / len(indexes)), f"分段内容审读 · {position+1}/{len(indexes)}")
                try:
                    accept(DeepSeek(self.settings.key, self.settings.model).content(
                        titles + group, payload["reference"], payload["instructions"], event, options.content_profile, "segment"))
                    done += 1
                except ReviewError as exc:
                    warnings.append(str(exc))
                    content_gaps.append(str(exc))
                    api_failed = True
                    break
            if not api_failed:
                progress(75, "整片内容复审 · 标题、前后逻辑与新闻要素")
                context, sampled = select_context(ordered)
                if sampled:
                    gap = "整片叙事审读使用均匀抽样的文字证据，可能遗漏远距离矛盾；完整转写已保留。"
                    warnings.append(gap)
                    content_gaps.append(gap)
                try:
                    accept(DeepSeek(self.settings.key, self.settings.model).content(
                        titles + context, payload["reference"], payload["instructions"] + ("\n本次整片文字为抽样，不得声称覆盖全文。" if sampled else ""),
                        event, options.content_profile, "global"))
                    text_complete = not sampled and (len(groups) <= 1 or done == len(groups))
                except ReviewError as exc:
                    warnings.append(str(exc))
                    content_gaps.append(str(exc))
            coverage.append({"name": "AI 视频内容与整片叙事", "status": "complete" if text_complete else "failed",
                             "detail": "按八个内容维度审读；包含拟发布标题与参考资料比对，检查完成不代表事实已核实" if text_complete else "内容审读存在失败或抽样缺口，请补充复核"})
        else:
            content_gaps.append("AI 文字内容审读未执行：未启用 AI 或未取得文字证据。")
            coverage.append({"name": "AI 视频内容与整片叙事", "status": "skipped", "detail": content_gaps[-1]})
        if payload["use_ai"] and payload["vision"] and frames:
            selected = media.pick_vision_frames(frames, options.vision_limit)
            done = 0
            checked_ids = set()
            for index in range(0, len(selected), 6):
                group = selected[index:index+6]
                progress(80 + int(15 * index / len(selected)), f"画面内容抽查 · {index+1}/{len(selected)}")
                try:
                    nearby, sampled = timed_context(group, sources)
                    if sampled:
                        content_gaps.append("部分画面对应的文字过多，声画联合审读使用抽样上下文。")
                    accept(DeepSeek(self.settings.key, self.settings.vision_model).content(
                        titles + group + nearby, payload["reference"], payload["instructions"], event, options.content_profile, "visual",
                        [(frame["id"], media.image_data(directory, frame)) for frame in group]))
                    done += len(group)
                    checked_ids.update(frame["id"] for frame in group)
                except ReviewError as exc:
                    warnings.append(str(exc))
                    content_gaps.append(str(exc))
                    break
            vision_complete = bool(selected) and done == len(selected)
            coverage.append({"name": "AI 声画联合内容审读", "status": "sampled" if vision_complete else "failed",
                             "detail": f"完成 {done}/{len(selected)} 个送检画面，联合对应时段口播和字幕；最多 {options.vision_limit} 帧，不代表逐帧审核"})
            for frame in frames:
                frame["vision_checked"] = frame["id"] in checked_ids
        else:
            content_gaps.append("AI 画面内容未审：未启用或画面提取失败，不能凭文字确认画面含义。")
            coverage.append({"name": "AI 声画联合内容审读", "status": "skipped", "detail": content_gaps[-1]})
        for frame in frames:
            frame.pop("fingerprint", None)
        checked_shots = {frame["shot"] for frame in frames if frame.get("vision_checked") and frame.get("shot")}
        for scene in technical["scenes"]:
            scene["vision_checked"] = scene["index"] in checked_shots
        for issue in issues:
            if "scope" not in issue:
                issue["scope"] = "technical" if issue["source"] in ("technical", "frame") else "content"
                if issue["scope"] == "content":
                    issue["dimension"] = "ethics" if "隐私" in issue["category"] and "标点" not in issue["title"] else "consistency" if "对照" in issue["category"] or "差异" in issue["title"] else "facts"
        content_gaps.extend(c["detail"] for c in coverage if c["status"] in ("failed", "skipped")
                            and c["name"] in ("语音转写", "画面文字 OCR", "AI 视频内容与整片叙事", "AI 声画联合内容审读"))
        content_gaps.extend(warnings)
        if any(c["name"] in ("语音转写", "画面文字 OCR") and c["status"] == "failed" for c in coverage):
            text_complete = False
        if any(run["warnings"] for run in content_runs):
            text_complete = False
        report = finalize(issues, coverage, warnings, summaries, sources, frames=frames, metadata=metadata,
                        sampling={"ocr_interval": payload["interval"], "vision_max_frames": options.vision_limit},
                        options=options.model_dump(), metrics=technical["metrics"], scenes=technical["scenes"], alignment=aligned)
        report["content_review"] = content_overview(content_runs, report["issues"], options.content_profile,
            content_gaps, text_complete, vision_complete)
        return report
