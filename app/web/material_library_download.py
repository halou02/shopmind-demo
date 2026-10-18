from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from app.web.catalog import LIBRARY_UI_HIDDEN_NAMES, scan_material_catalog


def resolve_library_download_file(library_root: Path, relative_path: str) -> Path:
    root = library_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("非法文件路径") from exc
    if not candidate.is_file():
        raise ValueError("文件不存在或已被删除")
    if candidate.name in LIBRARY_UI_HIDDEN_NAMES or candidate.name.startswith("."):
        raise ValueError("该文件不可下载")
    return candidate


def _visible_library_relative_paths(library_root: Path) -> list[str]:
    items = scan_material_catalog(library_root)
    return [
        item.relative_path
        for item in items
        if item.name not in LIBRARY_UI_HIDDEN_NAMES and not item.name.startswith(".")
    ]


def build_material_library_zip(
    library_root: Path,
    relative_paths: list[str] | None = None,
) -> Path:
    if relative_paths is None:
        relative_paths = _visible_library_relative_paths(library_root)
    else:
        validated: list[str] = []
        for relative_path in relative_paths:
            try:
                resolve_library_download_file(library_root, relative_path)
            except ValueError:
                continue
            if relative_path not in validated:
                validated.append(relative_path)
        relative_paths = validated

    if not relative_paths:
        raise ValueError("没有可下载的素材")

    temp_dir = Path(tempfile.mkdtemp(prefix="shopmind-library-"))
    staging = temp_dir / "material_library"
    staging.mkdir(parents=True, exist_ok=True)
    for relative_path in relative_paths:
        source = resolve_library_download_file(library_root, relative_path)
        destination = staging / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    archive_base = temp_dir / "material_library"
    shutil.make_archive(str(archive_base), "zip", root_dir=staging)
    zip_path = archive_base.with_suffix(".zip")
    if not zip_path.is_file():
        raise ValueError("打包失败，请重试")
    return zip_path


def zip_download_filename(folder_name: str | None = None) -> str:
    if not folder_name:
        return "素材库.zip"
    safe = re.sub(r"[^\w.\-一-龥]+", "_", folder_name.strip()) or "素材夹"
    return f"素材库_{safe[:40]}.zip"
