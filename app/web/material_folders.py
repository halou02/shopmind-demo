from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FOLDERS_FILE_NAME = "material_folders.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _folders_path(library_root: Path) -> Path:
    return library_root / FOLDERS_FILE_NAME


def _read_payload(library_root: Path) -> dict[str, Any]:
    path = _folders_path(library_root)
    if not path.is_file():
        return {"version": 1, "folders": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "folders": []}
    if not isinstance(data, dict):
        return {"version": 1, "folders": []}
    data.setdefault("folders", [])
    return data


def _write_payload(library_root: Path, payload: dict[str, Any]) -> None:
    path = _folders_path(library_root)
    payload["version"] = 1
    payload["updated_at"] = _now_iso()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_folder_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", (name or "").strip())
    return cleaned[:40]


def _valid_relative_path(library_root: Path, relative_path: str) -> bool:
    candidate = (library_root / relative_path).resolve()
    try:
        candidate.relative_to(library_root.resolve())
    except ValueError:
        return False
    return candidate.is_file()


def list_material_folders(library_root: Path) -> list[dict[str, Any]]:
    payload = _read_payload(library_root)
    folders: list[dict[str, Any]] = []
    for item in payload.get("folders", []):
        if not isinstance(item, dict):
            continue
        folder_id = str(item.get("id") or "")
        name = str(item.get("name") or "")
        if not folder_id or not name:
            continue
        paths = [
            path
            for path in (item.get("paths") or [])
            if isinstance(path, str) and _valid_relative_path(library_root, path)
        ]
        folders.append(
            {
                "id": folder_id,
                "name": name,
                "paths": paths,
                "count": len(paths),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
            }
        )
    return folders


def create_material_folder(library_root: Path, name: str) -> dict[str, Any]:
    label = _safe_folder_name(name)
    if not label:
        raise ValueError("请填写文件夹名称")
    payload = _read_payload(library_root)
    folders = payload.get("folders", [])
    if not isinstance(folders, list):
        folders = []
    for item in folders:
        if isinstance(item, dict) and str(item.get("name") or "") == label:
            raise ValueError(f"文件夹「{label}」已存在")
    folder = {
        "id": f"fld_{uuid.uuid4().hex[:10]}",
        "name": label,
        "paths": [],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    folders.append(folder)
    payload["folders"] = folders
    _write_payload(library_root, payload)
    return {**folder, "count": 0}


def delete_material_folder(library_root: Path, folder_id: str) -> bool:
    payload = _read_payload(library_root)
    folders = payload.get("folders", [])
    if not isinstance(folders, list):
        return False
    kept = [item for item in folders if isinstance(item, dict) and str(item.get("id")) != folder_id]
    if len(kept) == len(folders):
        return False
    payload["folders"] = kept
    _write_payload(library_root, payload)
    return True


def sync_folder_paths(library_root: Path, folder_id: str, relative_paths: list[str]) -> dict[str, Any]:
    payload = _read_payload(library_root)
    folders = payload.get("folders", [])
    target = None
    for item in folders:
        if isinstance(item, dict) and str(item.get("id")) == folder_id:
            target = item
            break
    if target is None:
        raise ValueError("文件夹不存在")
    paths: list[str] = []
    for relative_path in relative_paths:
        if not relative_path or not _valid_relative_path(library_root, relative_path):
            continue
        if relative_path not in paths:
            paths.append(relative_path)
    target["paths"] = paths
    target["updated_at"] = _now_iso()
    _write_payload(library_root, payload)
    return {
        "id": folder_id,
        "name": target.get("name"),
        "paths": paths,
        "count": len(paths),
    }


def add_paths_to_folder(library_root: Path, folder_id: str, relative_paths: list[str]) -> dict[str, Any]:
    payload = _read_payload(library_root)
    folders = payload.get("folders", [])
    target = None
    for item in folders:
        if isinstance(item, dict) and str(item.get("id")) == folder_id:
            target = item
            break
    if target is None:
        raise ValueError("文件夹不存在")
    paths = list(dict.fromkeys(str(p) for p in (target.get("paths") or []) if p))
    for relative_path in relative_paths:
        if not relative_path or not _valid_relative_path(library_root, relative_path):
            continue
        if relative_path not in paths:
            paths.append(relative_path)
    target["paths"] = paths
    target["updated_at"] = _now_iso()
    _write_payload(library_root, payload)
    return {
        "id": folder_id,
        "name": target.get("name"),
        "paths": paths,
        "count": len(paths),
    }


def remove_path_from_folder(library_root: Path, folder_id: str, relative_path: str) -> dict[str, Any]:
    payload = _read_payload(library_root)
    folders = payload.get("folders", [])
    target = None
    for item in folders:
        if isinstance(item, dict) and str(item.get("id")) == folder_id:
            target = item
            break
    if target is None:
        raise ValueError("文件夹不存在")
    paths = [p for p in (target.get("paths") or []) if p != relative_path]
    target["paths"] = paths
    target["updated_at"] = _now_iso()
    _write_payload(library_root, payload)
    return {
        "id": folder_id,
        "name": target.get("name"),
        "paths": paths,
        "count": len(paths),
    }


def rename_path_in_all_folders(library_root: Path, old_path: str, new_path: str) -> None:
    if old_path == new_path:
        return
    payload = _read_payload(library_root)
    folders = payload.get("folders", [])
    changed = False
    for item in folders:
        if not isinstance(item, dict):
            continue
        paths = item.get("paths") or []
        if old_path not in paths:
            continue
        item["paths"] = [new_path if p == old_path else p for p in paths]
        item["updated_at"] = _now_iso()
        changed = True
    if changed:
        _write_payload(library_root, payload)


def remove_path_from_all_folders(library_root: Path, relative_path: str) -> None:
    payload = _read_payload(library_root)
    folders = payload.get("folders", [])
    changed = False
    for item in folders:
        if not isinstance(item, dict):
            continue
        paths = item.get("paths") or []
        if relative_path in paths:
            item["paths"] = [p for p in paths if p != relative_path]
            item["updated_at"] = _now_iso()
            changed = True
    if changed:
        _write_payload(library_root, payload)
