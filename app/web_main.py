from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env", override=False)

_HOST = os.environ.get("SHOPMIND_HOST", "127.0.0.1")
_PORT = int(os.environ.get("SHOPMIND_PORT", "8080"))
_APP_URL = f"http://{_HOST}:{_PORT}" if _HOST not in {"0.0.0.0", "::"} else f"http://127.0.0.1:{_PORT}"


def _open_browser_when_ready() -> None:
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"{_APP_URL}/api/health", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(_APP_URL)
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.5)


def main() -> None:
    if os.environ.get("SHOPMIND_OPEN_BROWSER", "").strip() in {"1", "true", "yes"}:
        threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    uvicorn.run(
        "app.web.server:app",
        host=_HOST,
        port=_PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
