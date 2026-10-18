from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_folder_in_explorer(path: Path) -> None:
    target = path.resolve()
    if not target.exists():
        raise FileNotFoundError(str(target))
    if sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
        return
    subprocess.Popen(["xdg-open", str(target)])
