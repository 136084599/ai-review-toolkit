"""Run real OCR, ASR, text and vision API checks against synthetic media only."""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import ROOT, Settings
from app.jobs import Runner
from app.media import probe
from app.store import Store

settings = Settings()
store = Store(settings.data / "reviews.sqlite3")
runner = Runner(store, settings)
video = ROOT / "test-results" / "demo.mp4"
metadata = probe(video)
payload = {"filename":"video.mp4", "publication_title":"城市书房向公众开放", "metadata":metadata, "reference":"测试资料：城市书房2026年3月1日开放，共有60个阅读座位。",
    "instructions":"测试素材，检查画面日期与口播、座位数是否冲突。", "subtitles":"", "interval":2, "use_ai":True, "vision":True}
job_id = store.create("video", "端到端测试｜中文口播与字幕比对", payload)
directory = settings.data / job_id
directory.mkdir()
import shutil
shutil.copyfile(video, directory / "video.mp4")
start = time.monotonic()
runner.submit(job_id)
last_stage = ""
try:
    while True:
        job = store.get(job_id)
        if job["stage"] != last_stage:
            print(json.dumps({"stage":job["stage"],"progress":job["progress"]}, ensure_ascii=True), flush=True)
            last_stage = job["stage"]
        if job["status"] not in ("queued","running"):
            break
        if time.monotonic() - start > 600:
            runner.cancel(job_id)
            raise RuntimeError("End-to-end test timed out")
        time.sleep(.5)
    if job["status"] != "completed":
        raise RuntimeError(job["error"] or job["status"])
    report = job["report"]
    summary = {"job_id":job_id,"elapsed_seconds":round(time.monotonic()-start,2), "coverage":report["coverage"],
               "issues":len(report["issues"]), "sources":len(report["sources"]), "frames":len(report["frames"]),
               "warnings":report["warnings"], "content_review":report.get("content_review")}
    (ROOT/"test-results"/"full-smoke.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=True),flush=True)
    assert all(c["status"] not in ("failed","skipped") for c in report["coverage"]), "One or more stages failed"
    assert any(s["kind"]=="speech" for s in report["sources"]), "No speech recognized"
    assert any(s["kind"]=="ocr" for s in report["sources"]), "No OCR text recognized"
    assert any(i["engine"]=="ai" for i in report["issues"]), "No AI findings returned"
finally:
    runner.close()
