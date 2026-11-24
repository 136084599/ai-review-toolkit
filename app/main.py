import json
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import ROOT, Settings
from app.exports import DECISION_LABELS, content_markdown, issues_csv, transcript_srt
from app.content import BASIS_LABELS
from app.jobs import Runner
from app.media import probe, read_subtitles
from app.review import DeepSeek, ReviewError
from app.schemas import ConfigRequest, DecisionRequest, ReviewRequest, VideoOptions
from app.store import Store


def create_app(root=ROOT):
    settings = Settings(Path(root))
    store = Store(settings.data / "reviews.sqlite3")
    runner = Runner(store, settings)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        yield
        await run_in_threadpool(runner.close)

    app = FastAPI(title="清审 · 内容预审工作台", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings, app.state.store, app.state.runner = settings, store, runner
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def guard(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "不接受跨站请求，请从本机工作台操作"}, status_code=403)
        if request.method in ("POST", "PUT", "PATCH"):
            length = request.headers.get("content-length")
            if length is None:
                return JSONResponse({"detail": "请求必须包含文件大小"}, status_code=411)
            try:
                too_large = int(length) > settings.max_upload + 1024 * 1024 or int(length) < 0
            except ValueError:
                too_large = True
            if too_large:
                return JSONResponse({"detail": "上传超过 512 MB 限制"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ReviewError)
    async def review_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def get_job(job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise HTTPException(404, "找不到审核任务")
        job = store.get(job_id)
        if not job:
            raise HTTPException(404, "找不到审核任务")
        return job

    def ensure_capacity():
        if sum(job["status"] in ("queued", "running") for job in store.list()) >= 6:
            raise HTTPException(429, "待处理任务较多，请等待已有任务完成")

    @app.get("/api/settings")
    def get_settings():
        return settings.public()

    @app.get("/api/health")
    def health():
        return {"app": "qingshen", "version": "1.2"}

    @app.put("/api/settings")
    def update_settings(body: ConfigRequest):
        settings.save(body.key, body.model, body.vision_model)
        return settings.public()

    @app.post("/api/settings/test")
    def test_settings():
        models = DeepSeek(settings.key, settings.model).models()
        return {"ok": True, "models": [model["id"] for model in models]}

    @app.get("/api/jobs")
    def list_jobs():
        return store.list()

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str):
        job = get_job(job_id)
        job["payload"] = {k: v for k, v in job["payload"].items() if k not in ("filename", "subtitles")}
        return job

    @app.post("/api/reviews/text", status_code=202)
    async def review_text(body: ReviewRequest):
        ensure_capacity()
        job_id = store.create("text", body.title.strip() or "未命名稿件", body.model_dump())
        runner.submit(job_id)
        return {"id": job_id}

    @app.post("/api/reviews/video", status_code=202)
    async def review_video(file: UploadFile = File(...), title: str = Form(""),
                           reference: str = Form(""), instructions: str = Form(""),
                           subtitles: str = Form(""), interval: int = Form(2),
                           use_ai: bool = Form(True), vision: bool = Form(True), options: str = Form("{}")):
        ensure_capacity()
        if len(options) > 8000:
            raise HTTPException(422, "视频检查参数过长")
        try:
            checked_options = VideoOptions.model_validate_json(options).model_dump()
        except ValueError:
            raise HTTPException(422, "视频检查参数无效。检查数值范围；易错词每行格式为：错误写法=>正确写法") from None
        if len(title) > 120 or len(reference) > 20000 or len(instructions) > 3000 or len(subtitles) > 200000:
            raise HTTPException(422, "标题、参考资料、要求或字幕过长")
        if interval not in (1, 2, 3, 5):
            raise HTTPException(422, "采样间隔必须为 1、2、3 或 5 秒")
        extension = Path(file.filename or "").suffix.lower()
        if extension not in (".mp4", ".mov", ".mkv", ".webm", ".m4v"):
            raise HTTPException(415, "请上传 MP4、MOV、MKV、WebM 或 M4V 视频")
        job_id = uuid4().hex
        directory = settings.data / job_id
        directory.mkdir()
        path = directory / f"video{extension}"
        try:
            size = 0
            with path.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload:
                        raise HTTPException(413, "视频超过 512 MB")
                    await run_in_threadpool(output.write, chunk)
            if not size:
                raise HTTPException(422, "视频为空")
            metadata = await run_in_threadpool(probe, path)
            if metadata["duration"] > settings.max_duration:
                raise HTTPException(422, "视频超过 30 分钟，请按内容分段上传")
            if max(metadata["width"], metadata["height"]) > 8192:
                raise HTTPException(422, "视频分辨率过大，请转为 4K 或以下")
            if subtitles:
                read_subtitles(subtitles, metadata["duration"])
            ensure_capacity()
            payload = dict(filename=path.name, metadata=metadata, reference=reference, instructions=instructions, publication_title=title.strip(),
                           subtitles=subtitles, interval=interval, use_ai=use_ai, vision=vision, options=checked_options)
            store.create("video", title.strip() or Path(file.filename).name[:120], payload, job_id)
            runner.submit(job_id)
            return {"id": job_id}
        except Exception:
            shutil.rmtree(directory)
            raise
        finally:
            await file.close()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        job = get_job(job_id)
        if job["status"] in ("queued", "running"):
            runner.cancel(job_id)
        return {"ok": True}

    @app.patch("/api/jobs/{job_id}/issues/{issue_id}")
    def decide_issue(job_id: str, issue_id: str, body: DecisionRequest):
        job = get_job(job_id)
        if not job["report"] or not any(issue["id"] == issue_id for issue in job["report"]["issues"]):
            raise HTTPException(404, "找不到这条审核问题")
        store.decide(job_id, issue_id, body.status, body.note)
        return store.get(job_id)["decisions"][issue_id]

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str):
        job = get_job(job_id)
        if job["status"] in ("queued", "running"):
            raise HTTPException(409, "请先取消任务，等待处理停止后再删除")
        directory = settings.data / job_id
        if directory.exists():
            try:
                shutil.rmtree(directory)
            except OSError:
                raise HTTPException(409, "文件正在播放或使用中，请关闭预览后再删除") from None
        store.delete(job_id)
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/media")
    def video_file(job_id: str):
        job = get_job(job_id)
        if job["kind"] != "video":
            raise HTTPException(404)
        return FileResponse(settings.data / job_id / job["payload"]["filename"])

    @app.get("/api/jobs/{job_id}/frames/{frame_id}")
    def frame_file(job_id: str, frame_id: str):
        job = get_job(job_id)
        if not re.fullmatch(r"frame-\d+", frame_id):
            raise HTTPException(404)
        path = settings.data / job_id / f"{frame_id}.jpg"
        if job["kind"] != "video" or not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/jobs/{job_id}/export")
    def export(job_id: str, format: str = "md"):
        job = get_job(job_id)
        report = job["report"]
        if not report:
            raise HTTPException(409, "报告尚未生成")
        if format == "json":
            return Response(json.dumps({"title": job["title"], "created": job["created"], **report, "decisions":job["decisions"]}, ensure_ascii=False, indent=2),
                media_type="application/json", headers={"Content-Disposition": f'attachment; filename="review-{job_id[:8]}.json"'})
        if format == "csv":
            return Response(issues_csv(job), media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="issues-{job_id[:8]}.csv"'})
        if format == "srt":
            if not any(source["kind"] == "speech" for source in report["sources"]):
                raise HTTPException(409, "没有实际口播转写，无法导出 SRT")
            return Response(transcript_srt(report), media_type="application/x-subrip; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="asr-unverified-{job_id[:8]}.srt"'})
        if format != "md":
            raise HTTPException(422, "报告格式不支持")
        lines = [f"# {job['title']} · AI 预审报告", "", f"结论：{report['verdict']}", f"创建时间：{job['created']}", "", report["notice"], "", "## 审核覆盖", ""]
        lines.extend(f"- {c['name']} [{c['status']}]：{c['detail']}" for c in report["coverage"])
        lines.extend(content_markdown(report))
        if report["warnings"]:
            lines += ["", "## 未完成项与提醒", "", *[f"- {w}" for w in report["warnings"]]]
        for issue in report["issues"]:
            timestamp = f" · {issue['start']:.2f} 秒" if issue.get("start") is not None else ""
            lines += ["", f"## [{issue['severity']}] {issue['title']}{timestamp}", "", f"证据：{issue['evidence']}", "", f"建议：{issue['suggestion']}"]
            if issue.get("basis"):
                lines.append(f"内容判断依据：{BASIS_LABELS[issue['basis']]}")
            for evidence in issue.get("related", []):
                lines.append(f"对照证据 [{evidence['source_id']}, {evidence.get('start')}秒]：{evidence['evidence']}")
            if issue.get("reference_quote"):
                lines.append(f"所附资料原文（未独立核实）：{issue['reference_quote']}")
            decision = job["decisions"].get(issue["id"], {})
            lines += [f"人工状态：{DECISION_LABELS[decision.get('status','pending')]}", f"人工备注：{decision.get('note','')}"]
        if report.get("metrics"):
            lines += ["", "## 全片音频测量", "", "本次参数为自设阈值，不代表央媒统一验收标准。", "", *[f"- {key}: {value}" for key,value in report["metrics"].items()]]
        if report.get("options"):
            lines += ["", "## 本次自查参数", "", *[f"- {key}: {value}" for key,value in report["options"].items()]]
        if report.get("scenes"):
            scenes = report["scenes"]
            lines += ["", f"镜头检测：{len(scenes)} 个；已送 AI 视觉抽查 {sum(bool(s.get('vision_checked')) for s in scenes)} 个镜头。抽查不等于逐帧审核。"]
        lines += ["", "## 人工送审前复核", "", "- [ ] 人名、职务、机构名称已对照原始资料", "- [ ] 数字、单位、时间、地点及引述来源已核实", "- [ ] 视频口播、字幕和参考稿已复听复看", "- [ ] 地图、版权、肖像授权及隐私信息已确认", "- [ ] AI 未覆盖部分已人工复核"]
        return Response("\n".join(lines), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="review-{job_id[:8]}.md"'})

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/")
    def home():
        return FileResponse(ROOT / "static" / "index.html")

    return app


app = create_app()
