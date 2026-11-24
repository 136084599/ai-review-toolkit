"""Download once; review jobs never download models implicitly."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from app.config import Settings
from faster_whisper.utils import download_model

settings = Settings()
print(f"Downloading Whisper {settings.asr_model} to {settings.model_dir}", flush=True)
download_model(settings.asr_model, output_dir=str(settings.model_dir))
print("Speech model ready. Restart any failed review from the browser.", flush=True)
