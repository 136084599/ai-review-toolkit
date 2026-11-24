import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import Store


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


def wait_job(client, job_id):
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(.02)
    pytest.fail("Job did not complete")


def test_text_full_lifecycle_export_delete(client):
    response = client.post("/api/reviews/text", json={"title":"测试稿件", "text":"2026年2月30日，，", "use_ai":False})
    assert response.status_code == 202
    job_id = response.json()["id"]
    job = wait_job(client, job_id)
    assert job["status"] == "completed"
    assert job["report"]["counts"]["high"] == 1
    assert "不完整" in job["report"]["verdict"]
    report = client.get(f"/api/jobs/{job_id}/export")
    assert "日期不存在" in report.text
    assert "attachment" in report.headers["content-disposition"]
    assert client.get(f"/api/jobs/{job_id}/export?format=json").json()["title"] == "测试稿件"
    assert len(client.get("/api/jobs").json()) == 1
    assert client.delete(f"/api/jobs/{job_id}").status_code == 200
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_missing_api_key_marked_incomplete(client):
    job_id = client.post("/api/reviews/text", json={"text":"今天的活动正式开始。"}).json()["id"]
    job = wait_job(client, job_id)
    assert job["status"] == "completed"
    assert job["report"]["coverage"][-1]["status"] == "failed"
    assert "不完整" in job["report"]["verdict"]


@pytest.mark.parametrize("text", ["", "   ", "文" * 30001], ids=["empty", "blank", "too-long"])
def test_invalid_text(client, text):
    assert client.post("/api/reviews/text", json={"text":text}).status_code == 422


def test_config_secret_not_readable_and_input_guard(client):
    result = client.put("/api/settings", json={"key":"sk-test-secret", "model":"deepseek-flash", "vision_model":"deepseek-flash"})
    assert result.status_code == 200
    assert result.json()["configured"] is True
    assert "sk-test-secret" not in client.get("/api/settings").text
    assert client.get("/.env").status_code == 404
    assert client.put("/api/settings", json={"key":"bad\nINJECTION=yes"}).status_code == 422
    assert client.get("/static/../.env").status_code == 404


def test_cross_site_and_rebinding_rejected(client):
    assert client.post("/api/reviews/text", json={"text":"测试"}, headers={"Origin":"https://evil.example"}).status_code == 403
    assert client.get("/api/settings", headers={"Host":"evil.example"}).status_code == 400


def test_bad_video_and_size_limits_clean_up(client):
    assert client.post("/api/reviews/video", files={"file":("bad.mp4", b"garbage", "video/mp4")}).status_code == 400
    assert client.post("/api/reviews/video", files={"file":("bad.exe", b"garbage")}).status_code == 415
    response = client.post("/api/reviews/video", content=b"a", headers={"content-length":str(600*1024*1024)})
    assert response.status_code == 413
    assert client.get("/api/jobs").json() == []
    assert not any(p.is_dir() for p in client.app.state.settings.data.iterdir())


def test_recover_interrupted_jobs(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    job_id = store.create("text", "pending", {})
    store.recover()
    assert store.get(job_id)["status"] == "failed"
    assert "重启" in store.get(job_id)["error"]


def test_html_and_static_security_headers(client):
    assert client.get("/api/health").json()["app"] == "qingshen"
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]
    assert client.get("/api/jobs/not-an-id").status_code == 404


def test_review_decision_persists_and_exports(client):
    job_id=client.post("/api/reviews/text",json={"text":"2026年2月30日。", "use_ai":False}).json()["id"]
    issue=wait_job(client,job_id)["report"]["issues"][0]
    endpoint=f"/api/jobs/{job_id}/issues/{issue['id']}"
    response=client.patch(endpoint,json={"status":"resolved","note":"已对照原始采访记录修正"})
    assert response.status_code==200
    assert client.get(f"/api/jobs/{job_id}").json()["decisions"][issue["id"]]["status"]=="resolved"
    assert "已对照原始采访记录修正" in client.get(f"/api/jobs/{job_id}/export").text
    exported=client.get(f"/api/jobs/{job_id}/export?format=json").json()
    assert exported["decisions"][issue["id"]]["status"]=="resolved"
    assert "已处理" in client.get(f"/api/jobs/{job_id}/export?format=csv").text
    assert client.patch(endpoint,json={"status":"invalid"}).status_code==422
    assert client.patch(f"/api/jobs/{job_id}/issues/unknown",json={"status":"resolved"}).status_code==404
    client.delete(f"/api/jobs/{job_id}")
    with client.app.state.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM decisions").fetchone()[0]==0


def test_video_options_rejected_before_processing(client):
    for options in ['{"vision_limit":999999}','{"target_lufs":"NaN"}','{"glossary":"格式错误"}','[]']:
        response=client.post("/api/reviews/video",files={"file":("bad.mp4",b"invalid")},data={"options":options})
        assert response.status_code==422
    assert client.get("/api/jobs").json()==[]


def test_srt_export_requires_actual_speech(client):
    job_id=client.app.state.store.create("video","测试",{})
    client.app.state.store.update(job_id,status="completed",report={"sources":[{"id":"speech-1","kind":"speech","start":1.234,"end":4.567,"text":"自动转写，需校对"}],"issues":[]})
    result=client.get(f"/api/jobs/{job_id}/export?format=srt")
    assert "00:00:01,234 --> 00:00:04,567" in result.text
    assert "unverified" in result.headers["content-disposition"]
    client.app.state.store.update(job_id,report={"sources":[{"id":"sub-1","kind":"subtitle","start":0,"end":1,"text":"上传字幕"}],"issues":[]})
    assert client.get(f"/api/jobs/{job_id}/export?format=srt").status_code==409


def test_video_publication_title_and_content_profile_persist(client,monkeypatch):
    from app import main
    monkeypatch.setattr(main,"probe",lambda path:{"duration":5,"width":1280,"height":720,"has_audio":False})
    monkeypatch.setattr(client.app.state.runner,"submit",lambda job_id:None)
    response=client.post("/api/reviews/video",files={"file":("fixture.mp4",b"mocked probe")},data={"title":"  两县开展试点  ","options":'{"content_profile":"government"}'})
    assert response.status_code==202
    job=client.get(f"/api/jobs/{response.json()['id']}").json()
    assert job["payload"]["publication_title"]=="两县开展试点"
    assert job["payload"]["options"]["content_profile"]=="government"
    response=client.post("/api/reviews/video",files={"file":("fixture.mp4",b"mocked probe")},data={"options":'{"content_profile":"invented"}'})
    assert response.status_code==422
