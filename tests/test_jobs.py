import threading

from app.config import Settings
from app.jobs import Runner
from app.review import ReviewError
from app.store import Store


def test_title_is_also_reviewed(tmp_path):
    settings = Settings(tmp_path)
    runner = Runner(Store(tmp_path / "test.sqlite3"), settings)
    try:
        report = runner.text({"title":"2026年2月30日活动通知", "text":"今天活动开始。", "use_ai":False}, threading.Event(), lambda *args: None)
        issue = report["issues"][0]
        assert issue["source"] == "title" and issue["evidence"] == "2026年2月30日"
    finally:
        runner.close()


def test_cancelled_job_does_not_complete(tmp_path):
    settings = Settings(tmp_path)
    store = Store(tmp_path / "test.sqlite3")
    runner = Runner(store, settings)
    job_id = store.create("text", "取消测试", {"text":"内容", "use_ai":False})
    event = threading.Event(); event.set()
    try:
        runner.run(job_id, event)
        assert store.get(job_id)["status"] == "cancelled"
        assert store.get(job_id)["report"] is None
    finally:
        runner.close()


def test_failed_video_stages_preserve_results_and_coverage(tmp_path, monkeypatch):
    from app import jobs
    settings = Settings(tmp_path)
    settings.key = "test"
    store = Store(tmp_path / "test.sqlite3")
    runner = Runner(store, settings)
    monkeypatch.setattr(jobs.media, "technical_checks", lambda *args: {"issues":[],"metrics":{"integrated_lufs":-23},"scenes":[]})
    def unavailable(*args):
        raise ReviewError("测试：语音失败")
    monkeypatch.setattr(jobs.media, "transcribe", unavailable)
    frames = [{"id":"frame-1", "kind":"frame", "start":0, "end":0, "text":"frame", "file":"frame-1.jpg"}]
    monkeypatch.setattr(jobs.media, "scan_frames", lambda *args: (frames, [], 0))
    monkeypatch.setattr(jobs.media, "image_data", lambda *args: "data:image/jpeg;base64,AA")
    monkeypatch.setattr(jobs.DeepSeek, "content", unavailable)
    try:
        report = runner.video({"id":"f"*32, "payload":{"filename":"video.mp4", "metadata":{"has_audio":True,"duration":10},
            "interval":2,"use_ai":True,"vision":True,"reference":"","instructions":""}}, threading.Event(), lambda *args: None)
        assert "不完整" in report["verdict"]
        assert report["frames"][0]["vision_checked"] is False
        assert report["coverage"][0]["status"] == "complete"
        assert any(c["status"] == "failed" for c in report["coverage"])
    finally:
        runner.close()


def test_dense_video_text_has_bounded_api_calls_and_explicit_gap(tmp_path, monkeypatch):
    from app import jobs
    settings=Settings(tmp_path)
    runner=Runner(Store(tmp_path/"budget.sqlite3"),settings)
    calls=[]
    monkeypatch.setattr(jobs.media,"technical_checks",lambda *args:{"issues":[],"metrics":{},"scenes":[]})
    sources=[{"id":f"ocr-{i}","kind":"ocr","text":"文"*9000,"start":i,"end":i+1} for i in range(15)]
    monkeypatch.setattr(jobs.media,"scan_frames",lambda *args:([],sources,0))
    def review(self,sources,reference,instructions,event,profile,phase,*args):
        calls.append((phase,sources[0]["id"],sources[-1]["id"]))
        return {"issues":[],"summary":"已检查","warnings":[],"checks":[],"phase":phase}
    monkeypatch.setattr(jobs.DeepSeek,"content",review)
    try:
        report=runner.video({"id":"f"*32,"payload":{"filename":"video.mp4","metadata":{"has_audio":False,"duration":20},
            "interval":2,"use_ai":True,"vision":False,"reference":"","instructions":"","options":{"caption_check":False,"alignment_check":False}}},threading.Event(),lambda *args:None)
        assert len(calls)==13 and calls[0][1]=="ocr-0" and calls[-2][1]=="ocr-14" and calls[-1][0]=="global"
        assert "不完整" in report["verdict"] and any("12/15" in warning for warning in report["warnings"])
        assert sum(len(s["text"]) for s in report["sources"])==135000
    finally:
        runner.close()


def test_video_title_and_same_time_speech_reach_content_model(tmp_path,monkeypatch):
    from app import jobs
    settings=Settings(tmp_path)
    runner=Runner(Store(tmp_path/"content.sqlite3"),settings)
    calls=[]
    monkeypatch.setattr(jobs.media,"technical_checks",lambda *args:{"issues":[],"metrics":{},"scenes":[]})
    speech=[{"id":"speech-1","kind":"speech","text":"两县试点","start":1,"end":4}]
    frames=[{"id":"frame-1","kind":"frame","text":"全国实施","start":2,"end":2,"file":"frame-1.jpg"}]
    monkeypatch.setattr(jobs.media,"transcribe",lambda *args:speech)
    monkeypatch.setattr(jobs.media,"scan_frames",lambda *args:(frames,[],0))
    monkeypatch.setattr(jobs.media,"image_data",lambda *args:"data:image/jpeg;base64,AA")
    def content(self,sources,reference,instructions,event,profile,phase,images=None):
        calls.append((phase,{s["id"] for s in sources},images))
        return {"issues":[],"summary":"对照口播范围","warnings":[],"checks":[],"phase":phase}
    monkeypatch.setattr(jobs.DeepSeek,"content",content)
    try:
        report=runner.video({"id":"f"*32,"payload":{"filename":"video.mp4","publication_title":"补贴全国实施", "metadata":{"has_audio":True,"duration":6},
            "interval":2,"use_ai":True,"vision":True,"reference":"仅限两县","instructions":"","options":{"audio_check":False,"caption_check":False,"alignment_check":False}}},threading.Event(),lambda *args:None)
        assert len(calls)==2 and calls[0][0]=="global" and calls[1][0]=="visual"
        assert calls[1][1]=={"video-title","speech-1","frame-1"} and calls[1][2]
        assert report["content_review"]["vision_complete"] is True
        assert any(s["kind"]=="title" for s in report["sources"])
    finally:
        runner.close()
