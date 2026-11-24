import os
import threading
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.media import pick_vision_frames, probe, read_subtitles, run_ffmpeg, scan_frames, technical_checks
from app.review import Cancelled, ReviewError


@pytest.fixture
def video(tmp_path):
    # Real encoded media: 3 s black, followed by a legible title; all-silent audio.
    image = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(str(Path(os.environ.get("WINDIR", "")) / "Fonts" / "msyh.ttc"), 36)
    except OSError:
        font = ImageFont.load_default(size=32)
    draw.text((50, 130), "2026 NEWS TEST", fill="black", font=font)
    image.save(tmp_path / "title.png")
    target = tmp_path / "fixture.mp4"
    run_ffmpeg(["-f","lavfi","-i","color=c=black:s=640x360:r=10:d=3",
        "-loop","1","-framerate","10","-i",str(tmp_path / "title.png"),
        "-f","lavfi","-i","anullsrc=r=16000:cl=mono",
        "-filter_complex","[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map","[v]","-map","2:a",
        "-t","6","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(target)], threading.Event(), timeout=30)
    return target


def test_real_video_probe_quality_and_ocr(video, tmp_path):
    metadata = probe(video)
    assert metadata["duration"] == pytest.approx(6, abs=.2)
    assert metadata["has_audio"] and metadata["width"] == 640
    findings = technical_checks(video, metadata, threading.Event())
    assert any("黑场" in i["title"] and i["start"] < .1 for i in findings)
    assert any("静音" in i["title"] for i in findings)
    frames, sources, _ = scan_frames(video, tmp_path, metadata, threading.Event(), lambda *args: None, interval=1)
    assert len(frames) >= 5
    assert any("2026" in s["text"] for s in sources)
    assert all((tmp_path / f["file"]).exists() for f in frames)


def test_video_sampling_includes_first_and_last():
    frames = [{"start": i} for i in range(201)]
    selected = pick_vision_frames(frames)
    assert len(selected) == 24
    assert selected[0] == frames[0] and selected[-1] == frames[-1]


def test_corrupt_video(tmp_path):
    target = tmp_path / "bad.mp4"; target.write_bytes(b"not a video")
    with pytest.raises(ReviewError, match="无法读取"):
        probe(target)


def test_vision_deduplicates_static_frames():
    frames = [{"id":str(i), "fingerprint":(bytes([v]) * 576).hex()} for i, v in enumerate([0, 1, 2, 50, 51, 80])]
    assert [frame["id"] for frame in pick_vision_frames(frames)] == ["0", "3", "5"]


def test_subtitle_invalid_block_is_not_silently_ignored():
    with pytest.raises(ReviewError):
        read_subtitles("1\n00:00:01,000 --> 00:00:02,000\n有效\n\n2\ninvalid --> invalid\n无效", 6)


def test_subtitles_keep_timestamps_and_do_not_claim_speech():
    srt = "1\n00:00:01,000 --> 00:00:03,500\n你好，世界\n\n2\n00:00:04,000 --> 00:00:05,000\n<b>新闻</b>\n"
    sources = read_subtitles(srt, 6)
    assert len(sources) == 2
    assert sources[0]["start"] == 1 and sources[0]["end"] == 3.5
    assert sources[0]["kind"] == "subtitle" and sources[1]["text"] == "新闻"


@pytest.mark.parametrize("text", ["invalid", "1\n00:00:08,000 --> 00:00:09,000\n超范围", "1\n00:00:03,000 --> 00:00:01,000\n倒序"])
def test_invalid_subtitles(text):
    with pytest.raises(ReviewError):
        read_subtitles(text, 6)
