import csv
import io

from app.content import BASIS_LABELS

DECISION_LABELS = {"pending":"待复核", "resolved":"已处理", "dismissed":"无需处理"}


def srt_time(seconds):
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def transcript_srt(report):
    sources = [s for s in report["sources"] if s["kind"] == "speech"]
    return "\n\n".join(f"{index}\n{srt_time(source['start'])} --> {srt_time(source['end'])}\n{source['text']}"
                         for index, source in enumerate(sources, 1)) + "\n"


def safe_cell(value):
    text = str(value if value is not None else "")
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def issues_csv(job):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["开始秒", "结束秒", "级别", "分类", "问题", "证据", "建议", "人工状态", "人工备注", "审核类型", "判断依据", "对照证据", "参考资料原文"])
    for issue in job["report"]["issues"]:
        decision = job.get("decisions", {}).get(issue["id"], {})
        writer.writerow([safe_cell(value) for value in [issue.get("start"), issue.get("end"), issue["severity"], issue["category"],
            issue["title"], issue["evidence"], issue["suggestion"], DECISION_LABELS[decision.get("status","pending")], decision.get("note", ""),
            {"content":"内容审核", "technical":"技术检查"}.get(issue.get("scope"),""), BASIS_LABELS.get(issue.get("basis"),""),
            "；".join(f"{e['source_id']} ({e.get('start')}秒): {e['evidence']}" for e in issue.get("related",[])), issue.get("reference_quote", "")]])
    return "\ufeff" + output.getvalue()


def content_markdown(report):
    content = report.get("content_review")
    if not content:
        return []
    lines = ["", "## 视频内容审核", "", f"场景：{content['profile']}", "", content["summary"], "", content["notice"], ""]
    for dimension in content["dimensions"]:
        lines.append(f"- {dimension['name']} [{dimension['label']}]：{'；'.join(dimension['notes']) or '未获得有效依据'}")
    lines += ["", "### 内容审核未覆盖事项", "", *[f"- {gap}" for gap in content["limitations"]]]
    return lines
