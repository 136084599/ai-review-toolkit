"""Explicit opt-in live API test; never prints credentials or provider raw responses."""
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import Settings
from app.review import DeepSeek, ReviewError

settings = Settings()
client = DeepSeek(settings.key, settings.model)
try:
    models = client.models()
    print(json.dumps({"connection": "ok", "models": [m["id"] for m in models]}, ensure_ascii=True), flush=True)
    if "--review" in sys.argv:
        issues, summary, warnings = client.review([{"id":"text-1","kind":"text","text":"2026年2月30日，活动正式开始。。"}], "", "检查日期与标点。", threading.Event())
        print(json.dumps({"review": "ok", "issue_count": len(issues), "warnings": warnings}, ensure_ascii=True), flush=True)
except ReviewError as exc:
    print(json.dumps({"connection": "failed", "message": str(exc)}, ensure_ascii=True), flush=True)
    sys.exit(1)
