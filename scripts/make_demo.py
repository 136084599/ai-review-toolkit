import os
"""Create a clearly labelled synthetic video for repeatable end-to-end QA."""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image, ImageDraw, ImageFont
from app.config import ROOT
from app.media import run_ffmpeg

directory = ROOT / "test-results"
directory.mkdir(exist_ok=True)
image = Image.new("RGB", (1280, 720), "#eaf1e7")
draw = ImageDraw.Draw(image)
font = ImageFont.truetype(str(Path(os.environ.get("WINDIR", "")) / "Fonts" / "msyh.ttc"), 52)
small = ImageFont.truetype(str(Path(os.environ.get("WINDIR", "")) / "Fonts" / "msyh.ttc"), 32)
draw.rounded_rectangle((80, 80, 1200, 630), radius=26, fill="white")
draw.text((140, 140), "清审 · 测试素材（非真实新闻）", fill="#285c49", font=small)
draw.text((140, 260), "城市书房正式开放", fill="#263c2f", font=font)
draw.text((140, 390), "开放日期：2026年2月30日", fill="#263c2f", font=small)
draw.text((140, 450), "共有80个阅读座位", fill="#263c2f", font=small)
image.save(directory / "demo-title.png")
audio = directory / "demo-speech.wav"
if not audio.exists():
    raise SystemExit("Generate demo-speech.wav with Windows SAPI first")
run_ffmpeg(["-loop","1","-framerate","10","-i",str(directory/"demo-title.png"),
    "-i",str(audio),"-c:v","libx264","-preset","fast","-pix_fmt","yuv420p","-c:a","aac","-shortest",str(directory/"demo.mp4")], threading.Event())
print("Created test-results/demo.mp4", flush=True)
