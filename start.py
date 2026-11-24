import socket
import json
import threading
import urllib.error
import urllib.request
import webbrowser

import uvicorn

URL = "http://127.0.0.1:8765"


def open_browser():
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", 8765), timeout=0.2):
                webbrowser.open(URL)
                return
        except OSError:
            threading.Event().wait(0.2)


if __name__ == "__main__":
    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=1) as response:
            existing = json.load(response).get("app") == "qingshen"
    except (OSError, ValueError, urllib.error.URLError):
        existing = False
    if existing:
        webbrowser.open(URL)
        print("Qingshen is already running. Opened the existing workspace.")
        raise SystemExit(0)
    print(f"Qingshen AI: {URL}\nKeep this window open. Press Ctrl+C to stop.", flush=True)
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=8765, log_level="warning", access_log=False)
