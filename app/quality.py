"""Local evidence-based checks. Thresholds are user preferences, not broadcaster certification."""
import re
from difflib import SequenceMatcher


NUMBER = r"[-+]?(?:\d+(?:\.\d+)?|inf)"


def finding(source, title, evidence, suggestion, category, severity="medium", **extra):
    return {"severity": severity, "category": category, "title": title, "evidence": evidence,
            "suggestion": suggestion, "source_id": source["id"], "source": source["kind"],
            "start": source.get("start"), "end": source.get("end"), "engine": "quality",
            "needs_verification": True, **extra}


def parse_technical(output, metadata, options):
    duration = metadata["duration"]
    issues, metrics = [], {}
    source = {"id": "technical", "kind": "technical", "start": 0, "end": duration}
    if options.freeze_check:
        start = None
        for match in re.finditer(r"lavfi\.freezedetect\.freeze_(start|end):\s*([\d.]+)", output):
            kind, value = match.groups()
            if kind == "start":
                start = float(value)
            elif start is not None:
                issues.append(finding({**source, "id": f"freeze-{start}", "start": start, "end": float(value)},
                    "画面长时间静止", f"{start:.2f}–{float(value):.2f} 秒画面几乎无变化", "回放确认是否卡帧；静态图卡、照片和有意定格可标记无需处理。", "冻结与镜头"))
                start = None
        if start is not None:
            issues.append(finding({**source, "id": f"freeze-{start}", "start": start}, "静止画面延续到片尾",
                f"{start:.2f}–{duration:.2f} 秒画面几乎无变化", "确认片尾是否正常定格，或是否存在冻结、尾帧异常延长。", "冻结与镜头"))
    cuts = [0.0]
    if options.scene_check:
        for value in re.findall(r"lavfi\.scd\.time:\s*([\d.]+)", output):
            value = float(value)
            if 0 < value < duration and value - cuts[-1] > .01:
                cuts.append(value)
    scenes = [{"index": index+1, "start": start, "end": end} for index, (start,end) in enumerate(zip(cuts, [*cuts[1:],duration]))]
    if options.scene_check:
        for scene in scenes:
            if .03 < scene["end"] - scene["start"] < .35:
                issues.append(finding({**source, **scene, "id": f"shot-{scene['index']}"}, "检测到很短的镜头",
                    f"镜头 {scene['index']} 仅持续 {scene['end']-scene['start']:.2f} 秒", "检查是否闪帧、误插素材或有意的快速剪辑；镜头检测也可能被闪光触发。", "冻结与镜头", "low"))
    if options.audio_check and metadata["has_audio"]:
        for label, pattern in {
            "integrated_lufs": rf"Integrated loudness:\s+I\:\s*({NUMBER}) LUFS",
            "loudness_range_lu": rf"Loudness range:\s+LRA:\s*({NUMBER}) LU",
            "true_peak_dbfs": rf"True peak:\s+Peak:\s*({NUMBER}) dBFS",
        }.items():
            matches = re.findall(pattern, output)
            if matches:
                value = float(matches[-1])
                metrics[label] = value if -1000 < value < 1000 else None
        channel_levels = []
        for block in re.split(r"\[Parsed_astats_\d+ @ [^\]]+\] Channel: \d+", output)[1:]:
            block = block.split("Overall")[0]
            match = re.search(rf"RMS level dB:\s*({NUMBER})", block)
            if match:
                value = float(match.group(1))
                channel_levels.append(value if -1000 < value < 1000 else None)
        metrics["channel_rms_dbfs"] = channel_levels
        if len(channel_levels) == 2:
            levels = [-120 if v is None else v for v in channel_levels]
            if max(levels) > -50 and abs(levels[0]-levels[1]) > 12:
                labels = ["无可测信号（-∞ dBFS）" if v is None else f"{v:.1f} dBFS" for v in channel_levels]
                issues.append(finding(source, "左右声道音量差异较大", f"左声道 RMS {labels[0]}，右声道 {labels[1]}",
                    "分别试听左右声道，确认是否为单边无声、通道遗漏或有意的分轨混音。", "音频质量"))
        loudness = metrics.get("integrated_lufs")
        if loudness is not None and loudness > -65 and abs(loudness-options.target_lufs) > options.loudness_tolerance:
            issues.append(finding(source, "整体响度超出自设范围", f"实测 {loudness:.1f} LUFS；本次目标 {options.target_lufs:g}±{options.loudness_tolerance:g} LU",
                "按接收方实际要求调整混音。这是本次自查阈值，不代表央媒统一技术标准；音量检查针对全片。", "音频质量"))
        peak = metrics.get("true_peak_dbfs")
        if peak is not None and peak > -1:
            issues.append(finding(source, "音频真峰值接近满幅", f"全片真峰值 {peak:.1f} dBFS（提示线 -1 dBFS）",
                "试听高音量片段，检查爆音并适当保留峰值余量；接近满幅不等于已发生削波。", "音频质量"))
    if options.expected_aspect != "any":
        width, height = metadata["width"], metadata["height"]
        numerator, denominator = map(int, options.expected_aspect.split(":"))
        if abs(width/height - numerator/denominator) > .03:
            issues.append(finding(source, "画面比例与本次要求不符", f"编码尺寸 {width}×{height}，期望 {options.expected_aspect}",
                "核对视频旋转标记和接收方要求，确认横竖屏与画面比例。", "交付规格"))
    return {"issues": issues, "metrics": metrics, "scenes": scenes if options.scene_check else []}


def glossary_checks(sources, glossary):
    entries = [[p.strip() for p in line.split("=>")] for line in glossary.splitlines() if line.strip()]
    issues = []
    for source in sources:
        if source["kind"] == "caption":
            continue  # Already present in full-frame OCR.
        for wrong, correct in entries:
            if wrong in source["text"]:
                issues.append(finding(source, "命中自定义易错词", wrong, f"按本次提供的词表核对是否应改为“{correct}”；引述内容请结合上下文确认。", "专名与用语"))
    return issues


def caption_sources(frames, interval, duration):
    sources, previous = [], None
    for frame in frames:
        boxes = [box for box in frame.get("boxes", []) if box["bounds"][1] >= .55]
        text = "\n".join(box["text"] for box in boxes).strip()
        start = frame["start"]
        if text and previous and text == previous["text"] and start - previous["last_seen"] <= interval * 1.6:
            previous["last_seen"] = start
            previous["end"] = min(duration, start + interval)
        elif text:
            if previous:
                previous["end"] = min(previous["end"], start)
            previous = {"id": f"caption-{len(sources)+1}", "kind":"caption", "text":text,
                "start":start, "end":min(duration,start+interval), "last_seen":start, "frame_id":frame["id"]}
            sources.append(previous)
        else:
            if previous:
                previous["end"] = min(previous["end"], start)
            previous = None
    return sources


def caption_checks(frames, subtitles, options):
    issues, seen = [], set()
    for frame in frames:
        for box in frame.get("boxes", []):
            left, top, right, bottom = box["bounds"]
            if top < .55 or len(box["text"].strip()) < 2:
                continue
            rules = []
            if left < options.safe_margin or right > 1-options.safe_margin or bottom > 1-options.safe_margin:
                rules.append(("字幕靠近画面边缘", "按本次自设安全边距检查字幕是否被裁切或被平台控件遮挡；不代表平台统一安全区。"))
            if bottom-top < options.min_text_height:
                rules.append(("字幕字号可能偏小", "OCR 文字框高度低于本次阈值，建议在手机及成片尺寸下检查可读性。"))
            for title, suggestion in rules:
                key = (title, box["text"])
                if key not in seen:
                    issues.append(finding(frame, title, box["text"], suggestion, "字幕版式", "low"))
                    seen.add(key)
    # Reading speed only uses authored SRT/VTT timings, never inferred OCR durations.
    for source in subtitles:
        length = len(re.sub(r"[\s，。！？、,.!?]", "", source["text"]))
        duration = source["end"] - source["start"]
        cps = length / duration
        if length >= 5 and (cps > options.max_cps or duration < .6):
            issues.append(finding(source, "字幕停留偏短或阅读速度偏快", source["text"],
                f"{length} 字显示 {duration:.2f} 秒，约 {cps:.1f} 字/秒；本次阈值 {options.max_cps:g} 字/秒。请调整字幕断句或时长。", "字幕可读性", "low"))
    furthest_end = -1
    for source in sorted(subtitles, key=lambda item: item["start"]):
        if source["start"] < furthest_end - .05:
            issues.append(finding(source, "上传字幕时间重叠", source["text"],
                f"与前序字幕时间重叠，前序结束于 {furthest_end:.2f} 秒。确认是否有意分层，否则调整时间轴。", "字幕时间轴"))
        if source["end"] > furthest_end:
            furthest_end = source["end"]
    return issues


_DIGITS = {c:i for i,c in enumerate("零一二三四五六七八九")}
_DIGITS.update({"〇":0,"两":2})
_UNITS = {"十":10,"百":100,"千":1000,"万":10000,"亿":100000000}


def _number(match):
    text = match.group()
    return str(chinese_integer(text))


def chinese_integer(text):
    if all(c in _DIGITS for c in text):
        return int("".join(str(_DIGITS[c]) for c in text) or "0")
    for unit in ("亿", "万"):
        if unit in text:
            left, right = text.split(unit,1)
            return chinese_integer(left)*_UNITS[unit] + chinese_integer(right)
    section, number = 0, 0
    for char in text:
        if char in _DIGITS:
            number = _DIGITS[char]
        else:
            unit = _UNITS[char]
            section += (number or 1) * unit
            number = 0
    return section + number


def normalized(text):
    # Only normalize Chinese numerals adjacent to explicit numeric units or multi-character numbers.
    text = re.sub(r"[零〇一二两三四五六七八九十百千万亿]+(?=[年月日个座册人米元岁分秒点%％])", _number, text)
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text.casefold())


def spoken_in_window(speech, start, end):
    texts = []
    for source in speech:
        if source["end"] <= start or source["start"] >= end:
            continue
        words = source.get("words")
        if words:
            texts.extend(word["text"] for word in words if word["end"] > start and word["start"] < end)
        else:
            texts.append(source["text"])
    return "".join(texts)


def best_fragment(caption, spoken):
    if not caption or not spoken:
        return "", 0.0
    if caption in spoken:
        return caption, 1.0
    if len(spoken) <= len(caption) * 1.4:
        return spoken, SequenceMatcher(None, caption, spoken, autojunk=False).ratio()
    match = SequenceMatcher(None, caption, spoken, autojunk=False).find_longest_match()
    anchor = max(0, match.b-match.a)
    candidates = [spoken[max(0,anchor+shift):max(0,anchor+shift)+len(caption)+delta]
                  for shift in (-2,0,2) for delta in (-3,0,3)]
    best = max(candidates, key=lambda text: SequenceMatcher(None, caption, text, autojunk=False).ratio())
    return best, SequenceMatcher(None, caption, best, autojunk=False).ratio()


def alignment_checks(speech, captions, authored=False):
    rows, issues = [], []
    for caption in captions:
        if len(normalized(caption["text"])) < 4:
            continue
        # Bound unusually long captions before difflib; preserved in sources for manual review.
        if len(caption["text"]) > 500:
            rows.append({"source_id":caption["id"],"start":caption["start"],"end":caption["end"],"status":"unmatched",
                         "caption":caption["text"],"speech":"", "similarity":None, "timing_offset":None})
            continue
        nearby = [s for s in speech if s["end"] > caption["start"]-3.5 and s["start"] < caption["end"]+3.5]
        spoken = spoken_in_window(nearby, caption["start"]-.25, caption["end"]+.25)
        caption_text = normalized(caption["text"])
        fragment, similarity = best_fragment(caption_text, normalized(spoken))
        status, offset = "matched" if similarity >= .96 else "unmatched", None
        if authored and similarity < .96:
            for candidate in nearby:
                if SequenceMatcher(None, caption_text, normalized(candidate["text"]), autojunk=False).ratio() >= .96:
                    delta = candidate["start"] - caption["start"]
                    if .8 < abs(delta) <= 3.5:
                        status, offset = "timing", round(delta, 2)
                        break
        if status == "timing":
            issues.append(finding(caption, "字幕与口播时间可能偏移", caption["text"],
                f"相似口播约在字幕起点后 {offset:+.2f} 秒出现。请试听校正；这是转写时间对齐候选，不是口型同步检测。", "音字对齐"))
        elif spoken and .5 <= similarity < .96:
            status = "different"
            issues.append(finding(caption, "字幕与同期口播存在文字差异", caption["text"],
                f"同期转写：{spoken[:400]}。请对照原片核实漏字、错字或数字差异；转写及 OCR 也可能识别有误。", "音字对齐"))
        elif authored and spoken and similarity < .5:
            issues.append(finding(caption, "字幕与同期口播需重新对照", caption["text"],
                f"同期转写：{spoken[:400]}。文字匹配度较低，检查字幕文件版本、时间轴和语音识别结果。", "音字对齐"))
        rows.append({"source_id":caption["id"], "start":caption["start"],"end":caption["end"],
            "caption":caption["text"],"speech":spoken,"similarity":round(similarity,3) if spoken else None,
            "status":status, "timing_offset":offset})
    if authored:
        for source in speech:
            covered = sum(max(0,min(source["end"],c["end"])-max(source["start"],c["start"])) for c in captions)
            if source["end"]-source["start"] >= 2 and covered < .1:
                issues.append(finding(source, "口播片段未被上传字幕覆盖", source["text"],
                    "这一时间段没有配套 SRT/VTT 字幕。检查是否漏字幕、时间轴偏移或有意留空；不据此判断画面一定没有硬字幕。", "字幕覆盖"))
    return rows, issues
