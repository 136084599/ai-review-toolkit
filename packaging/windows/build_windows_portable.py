"""Build a clean Windows x64 Qingshen Windows x64 ZIP with a verified offline speech model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import socket
import time
import sys
from tempfile import TemporaryDirectory
import urllib.request
import zipfile

PYTHON = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/20261124/cpython-3.12.16%2B20261124-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
    "96694b50dcfde13d56e40346ddaa552a9cd650290e9b933cecf83c1d82226960",
    "cpython-3.12.16-windows-x64-standalone.tar.gz",
)
MODEL_REV = "536b0662742c02347bc0e980a01041f333bce120"
MODEL_BASE = "https://huggingface.co/Systran/faster-whisper-small/resolve/" + MODEL_REV
MODELS = {
    "config.json": "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
    "model.bin": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
    "README.md": "329373481008c7c38654aff8ecdcf0163c211557cc7ba8e2ef6f2f84b4f75ec8",
    "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
    "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
    "LICENSE": "b5d65a59060e68c4ff940e1eddfa6f94b2d68fdf58ed7f4dd57721c997e35e9d",
}
FILES = ("README.md", "start.py", "requirements-lock.txt")
TEST_TOOLS = {"pytest", "pluggy", "iniconfig", "pygments"}
NAME = "Qingshen-Windows-x64-Portable.zip"

def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(item, cache):
    url, expected, name = item
    path = cache / name
    if not path.exists():
        incoming = path.with_suffix(".download")
        with urllib.request.urlopen(url, timeout=120) as response, incoming.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        if sha256(incoming) != expected:
            raise ValueError(f"SHA-256 mismatch for {name}")
        incoming.replace(path)
    if sha256(path) != expected:
        raise ValueError(f"SHA-256 mismatch for {name}")
    return path


def verify_app(unpacked, directory):
    runtime = unpacked / "runtime" / "python.exe"
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(("DEEPSEEK_", "ASR_", "HF_"))
           and name not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}}
    env.update(HF_HUB_OFFLINE="1", PYTHONIOENCODING="utf-8")
    code = (
        "from faster_whisper import WhisperModel; from rapidocr_onnxruntime import RapidOCR; "
        "from imageio_ffmpeg import get_ffmpeg_exe; from pathlib import Path; "
        "WhisperModel('data/models/small', device='cpu', compute_type='int8', local_files_only=True); "
        "RapidOCR(); assert Path(get_ffmpeg_exe()).is_file(); print('Offline Whisper, OCR and FFmpeg available')"
    )
    subprocess.run([str(runtime), "-c", code], cwd=unpacked, env=env, check=True, timeout=100)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    logfile = directory / "startup.log"
    with logfile.open("wb") as log:
        child = subprocess.Popen(
            [str(runtime), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=unpacked, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(120):
                if child.poll() is not None:
                    raise RuntimeError("The bundled service stopped")
                try:
                    with opener.open(f"http://127.0.0.1:{port}/api/settings", timeout=2) as response:
                        settings = json.load(response)
                except OSError:
                    time.sleep(0.25)
                    continue
                assert settings["asr_ready"] and not settings["configured"]
                with opener.open(f"http://127.0.0.1:{port}/", timeout=2) as response:
                    assert response.status == 200
                return
            raise RuntimeError("Qingshen startup timed out")
        except Exception:
            print(logfile.read_text(encoding="utf-8", errors="replace")[-3500:])
            raise
        finally:
            child.terminate()
            child.wait(timeout=10)


def build(source, output, cache):
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="qingshen-portable-") as tmp:
        work = Path(tmp)
        root = work / "Qingshen"
        runtime = root / "runtime"
        root.mkdir()
        with tarfile.open(download(PYTHON, cache), "r:gz") as archive:
            archive.extractall(work / "python-input", filter="data")
        (work / "python-input" / "python").rename(runtime)
        if not (runtime / "python.exe").is_file():
            raise ValueError("Standalone Python executable is missing")
        args = [sys.executable, "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                "--no-input", "--no-deps", "--only-binary=:all:", "--no-compile",
                "--platform", "win_amd64", "--implementation", "cp", "--python-version", "3.12",
                "--index-url", "https://pypi.org/simple", "--target", str(runtime / "Lib/site-packages")]
        pins = [line.strip() for line in (source / "requirements-lock.txt").read_text(encoding="utf-8-sig").splitlines()
                if "==" in line and line.split("==", 1)[0].strip().lower() not in TEST_TOOLS]
        subprocess.run([*args, *pins], check=True)
        model_cache = cache / "whisper-small"
        model_cache.mkdir(exist_ok=True)
        model_dest = root / "data" / "models" / "small"
        model_dest.mkdir(parents=True)
        for name, digest in MODELS.items():
            url = ("https://raw.githubusercontent.com/openai/whisper/main/LICENSE" if name == "LICENSE"
                   else MODEL_BASE + "/" + name)
            shutil.copyfile(download((url, digest, name), model_cache), model_dest / name)
        (root / "upstream-components.json").write_text(json.dumps(
            {"python": {"url": PYTHON[0], "sha256": PYTHON[1]}, "speech_model": {
                "source": MODEL_BASE, "files_sha256": MODELS}}, indent=2) + "\n", encoding="utf-8")
        for package in ("app", "static"):
            shutil.copytree(source / package, root / package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in FILES:
            shutil.copyfile(source / name, root / name)
        for name in ("Start.cmd", "PORTABLE-README.md"):
            shutil.copyfile(Path(__file__).resolve().parent / name, root / name)
        artifact = output / NAME
        with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
            for file in sorted(root.rglob("*")):
                if file.is_file() and file.suffix != ".pyc" and file.relative_to(root).parts[:4] != ("runtime", "Lib", "site-packages", "bin"):
                    archive.write(file, file.relative_to(work).as_posix())
        (output / "SHA256SUMS.txt").write_text(f"{sha256(artifact)}  {artifact.name}\n", encoding="ascii")
        with TemporaryDirectory(prefix="unpacked-", dir=work) as unpacked:
            with zipfile.ZipFile(artifact) as archive:
                assert archive.testzip() is None
                archive.extractall(unpacked)
            unpacked = Path(unpacked) / root.name
            verify_app(unpacked, work)
    print(f"Verified {NAME}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    build(args.source.resolve(), args.output.resolve(), args.cache.resolve())
