from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn


def application_home() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_HOME = application_home()
os.environ["FRAMEFLOW_HOME"] = str(APP_HOME)
ffmpeg_dir = APP_HOME / "ffmpeg-bin"
if ffmpeg_dir.is_dir():
    os.environ["PATH"] = f"{ffmpeg_dir}{os.pathsep}{os.environ.get('PATH', '')}"

from backend.app.main import app

logging.basicConfig(
    filename=APP_HOME / "FrameFlow.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def available_port() -> int:
    for port in range(8000, 8011):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("端口 8000–8010 均被占用")


def open_when_ready(port: int) -> None:
    url = f"http://127.0.0.1:{port}/"
    health = f"http://127.0.0.1:{port}/api/health"
    for _ in range(80):
        try:
            with urllib.request.urlopen(health, timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.25)
    logging.error("FrameFlow backend did not become ready")


def main() -> None:
    port = available_port()
    threading.Thread(target=open_when_ready, args=(port,), daemon=True).start()
    logging.info("Starting FrameFlow at http://127.0.0.1:%s", port)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("FrameFlow failed to start")
        raise
