from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any


def list_export_files(package_root: Path) -> list[dict[str, Any]]:
    if not package_root.is_dir():
        return []
    files: list[dict[str, Any]] = []
    for path in sorted(package_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(package_root).as_posix()
        files.append(
            {
                "name": path.name,
                "relative_path": relative,
                "size_bytes": path.stat().st_size,
            }
        )
    return files


def resolve_export_file(package_root: Path, relative_path: str) -> Path:
    candidate = (package_root / relative_path).resolve()
    package_resolved = package_root.resolve()
    try:
        candidate.relative_to(package_resolved)
    except ValueError:
        raise PermissionError("invalid export file path")
    if not candidate.is_file():
        raise FileNotFoundError(relative_path)
    return candidate


def build_export_zip(package_root: Path) -> Path:
    if not package_root.is_dir():
        raise FileNotFoundError(str(package_root))
    temp_dir = Path(tempfile.mkdtemp(prefix="aipc-export-"))
    archive_base = temp_dir / package_root.name
    shutil.make_archive(str(archive_base), "zip", root_dir=package_root)
    zip_path = archive_base.with_suffix(".zip")
    if not zip_path.is_file():
        raise FileNotFoundError("zip archive was not created")
    return zip_path
