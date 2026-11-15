"""pywebview 진입점: python -m hub (설치판은 SoneFMPlatform.exe)."""
from __future__ import annotations

import ctypes
import logging
import sys
import time
from logging.handlers import RotatingFileHandler

from hub import APP_NAME
from hub.paths import platform_dir, resource_dir


def setup_logging() -> logging.Logger:
    logs = platform_dir() / "logs"
    logs.mkdir(exist_ok=True)
    h = RotatingFileHandler(logs / "platform.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)
    return logging.getLogger("sonefm.platform")


def show_error(message: str) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)


def main() -> int:
    log = setup_logging()
    index = resource_dir() / "ui" / "index.html"
    if not index.exists():
        show_error("화면 파일을 찾지 못했습니다. 프로그램을 다시 설치해 주세요.")
        return 1
    import webview
    from hub.api import Api

    api = Api()
    window = webview.create_window(APP_NAME, index.as_uri(), js_api=api, width=1280, height=860, min_size=(960, 640))
    api._window = window  # noqa: SLF001
    log.info("시작: 데이터 %s", platform_dir())

    def on_loaded() -> None:
        # 문제 해결용: 화면이 열렸는지, 내부 연결(pywebview)이 준비됐는지 로그에 남긴다 (별도 스레드에서 돈다)
        try:
            time.sleep(3)
            state = window.evaluate_js("JSON.stringify({bridge: !!(window.pywebview && window.pywebview.api),"
                                       " tasks: document.querySelectorAll('#tasks tr').length,"
                                       " banners: document.querySelector('#banners').innerText})")
            log.info("화면 열림: %s", state)
        except Exception:
            log.exception("화면 상태 확인 실패")

    window.events.loaded += on_loaded
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
