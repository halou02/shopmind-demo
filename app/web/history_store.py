from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _history_root(workspace: Path) -> Path:
    root = workspace / "history"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def archive_task(workspace: Path, record: dict[str, Any]) -> dict[str, Any]:
    root = _history_root(workspace)
    task_id = record.get("id") or f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    destination = root / f"{task_id}.json"
    from app.web.history_media import attach_history_media_urls, persist_history_media_assets

    payload = {
        **record,
        "id": task_id,
        "archived_at": datetime.now(timezone.utc).isoformat(),
    }
    payload = persist_history_media_assets(workspace, payload)
    payload = attach_history_media_urls(payload)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    return payload


def list_history(workspace: Path, limit: int = 50) -> list[dict[str, Any]]:
    from app.web.history_media import attach_history_media_urls

    root = _history_root(workspace)
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob("task_*.json"), reverse=True):
        data = _read_json(path)
        if data is None:
            continue
        records.append(attach_history_media_urls(data))
        if len(records) >= limit:
            break
    return records


def get_history_item(workspace: Path, task_id: str) -> dict[str, Any] | None:
    from app.web.history_media import attach_history_media_urls

    data = _read_json(_history_root(workspace) / f"{task_id}.json")
    if data is None:
        return None
    return attach_history_media_urls(data)


def _validate_task_id(task_id: str) -> str:
    cleaned = (task_id or "").strip()
    if not cleaned or cleaned != Path(cleaned).name:
        raise ValueError("无效的任务 ID")
    base = cleaned.removesuffix(".json")
    if ".." in base or not base.startswith("task_"):
        raise ValueError("无效的任务 ID")
    return base


def delete_history_item(workspace: Path, task_id: str) -> bool:
    task_key = _validate_task_id(task_id)
    path = _history_root(workspace) / f"{task_key}.json"
    if not path.is_file():
        return False
    path.unlink()
    return True
