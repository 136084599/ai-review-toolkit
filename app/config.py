import os
import threading
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
CONFIG_LOCK = threading.Lock()


class Settings:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.data = root / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self.reload()

    def reload(self):
        values = {**dotenv_values(self.root / ".env"), **os.environ}
        self.key = values.get("DEEPSEEK_API_KEY", "") or ""
        self.model = values.get("DEEPSEEK_MODEL", "deepseek-flash")
        self.vision_model = values.get("DEEPSEEK_VISION_MODEL", "deepseek-flash")
        self.asr_model = values.get("ASR_MODEL", "small")
        self.model_dir = self.data / "models" / self.asr_model
        self.max_upload = 512 * 1024 * 1024
        self.max_duration = 1800

    def save(self, key: str, model: str, vision_model: str):
        # Only supported fields, no arbitrary endpoint to accidentally forward credentials to.
        with CONFIG_LOCK:
            existing = dict(dotenv_values(self.root / ".env"))
            if key:
                existing["DEEPSEEK_API_KEY"] = key
            existing.update(DEEPSEEK_MODEL=model, DEEPSEEK_VISION_MODEL=vision_model)
            temp = self.root / ".env.tmp"
            temp.write_text("".join(f'{k}={v}\n' for k, v in existing.items() if v is not None), encoding="utf-8")
            temp.replace(self.root / ".env")
            self.reload()

    def public(self):
        return {"configured": bool(self.key), "model": self.model,
                "vision_model": self.vision_model, "asr_ready": (self.model_dir / "model.bin").exists(),
                "asr_model": self.asr_model, "max_upload_mb": 512, "max_duration_minutes": 30}
