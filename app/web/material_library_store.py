from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.web.catalog import categorize_file, scan_material_catalog
from app.web.reference_files import is_reference_file

INDEX_FILE_NAME = "library_index.json"


def _index_path(library_root: Path) -> Path:
    return library_root / INDEX_FILE_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_index(library_root: Path) -> dict[str, Any]:
    path = _index_path(library_root)
    if not path.is_file():
        return {"version": 1, "updated_at": None, "items": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "updated_at": None, "items": []}
    if not isinstance(payload, dict):
        return {"version": 1, "updated_at": None, "items": []}
    payload.setdefault("version", 1)
    payload.setdefault("items", [])
    return payload


def _write_index(library_root: Path, payload: dict[str, Any]) -> None:
    path = _index_path(library_root)
    payload["updated_at"] = _now_iso()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def rebuild_library_index(library_root: Path) -> dict[str, Any]:
    items = scan_material_catalog(library_root)
    entries: list[dict[str, Any]] = []
    for item in items:
        if not is_reference_file(item.name):
            continue
        entries.append(
            {
                "name": item.name,
                "relative_path": item.relative_path,
                "category": item.category,
                "size_bytes": item.size_bytes,
                "source": "scan",
                "uploaded_at": _now_iso(),
            }
        )
    payload = {"version": 1, "items": entries}
    _write_index(library_root, payload)
    return payload


def record_library_files(
    library_root: Path,
    saved_paths: list[Path],
    *,
    source: str = "upload",
) -> list[dict[str, Any]]:
    if not saved_paths:
        return []

    library_resolved = library_root.resolve()
    index = _read_index(library_root)
    by_path = {
        str(item.get("relative_path") or ""): item
        for item in index.get("items", [])
        if isinstance(item, dict) and item.get("relative_path")
    }

    recorded: list[dict[str, Any]] = []
    for path in saved_paths:
        resolved = path.resolve()
        try:
            relative_path = resolved.relative_to(library_resolved).as_posix()
        except ValueError:
            continue
        if not resolved.is_file() or not is_reference_file(resolved.name):
            continue
        entry = {
            "name": resolved.name,
            "relative_path": relative_path,
            "category": categorize_file(resolved, library_resolved),
            "size_bytes": resolved.stat().st_size,
            "source": source,
            "uploaded_at": _now_iso(),
        }
        by_path[relative_path] = entry
        recorded.append(entry)

    index["items"] = list(by_path.values())
    _write_index(library_root, index)
    return recorded


def _safe_filename(name: str) -> str:
    cleaned = (name or "").strip().replace("\\", "").replace("/", "")
    if not cleaned or cleaned.startswith("."):
        raise ValueError("文件名无效")
    return cleaned


def rename_library_file(library_root: Path, relative_path: str, new_name: str) -> str:
    from app.web.material_delete import PROTECTED_FILENAMES

    filename = _safe_filename(new_name)
    if filename in PROTECTED_FILENAMES:
        raise ValueError("系统文件不可重命名")
    source = (library_root / relative_path).resolve()
    library_resolved = library_root.resolve()
    try:
        source.relative_to(library_resolved)
    except ValueError as exc:
        raise ValueError("非法文件路径") from exc
    if not source.is_file():
        raise ValueError("文件不存在或已被删除")
    if source.name in PROTECTED_FILENAMES:
        raise ValueError("该文件不可重命名")
    target = source.with_name(filename)
    if target == source:
        return relative_path
    if target.exists():
        raise ValueError(f"已存在同名文件「{filename}」")
    source.rename(target)
    new_relative_path = target.relative_to(library_resolved).as_posix()

    index = _read_index(library_root)
    items = index.get("items", [])
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and str(item.get("relative_path") or "") == relative_path:
                item["relative_path"] = new_relative_path
                item["name"] = filename
                if "category" in item:
                    item["category"] = categorize_file(target, library_resolved)
                break
        index["items"] = items
        _write_index(library_root, index)
    return new_relative_path


def remove_library_file(library_root: Path, relative_path: str) -> None:
    index = _read_index(library_root)
    items = index.get("items", [])
    if not isinstance(items, list):
        items = []
    index["items"] = [
        item
        for item in items
        if isinstance(item, dict) and str(item.get("relative_path") or "") != relative_path
    ]
    _write_index(library_root, index)


def library_index_entries(library_root: Path) -> list[dict[str, Any]]:
    index = _read_index(library_root)
    items = index.get("items", [])
    if not isinstance(items, list) or not items:
        return rebuild_library_index(library_root).get("items", [])
    valid: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        relative_path = str(item.get("relative_path") or "")
        candidate = library_root / relative_path
        if candidate.is_file():
            valid.append(item)
    if len(valid) != len(items):
        index["items"] = valid
        _write_index(library_root, index)
    return valid
