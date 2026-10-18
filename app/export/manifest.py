from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any


MANIFEST_VERSION = "1.0"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    package_id: str,
    product_id: str,
    direction_id: str | None,
    files: list[Path],
    package_root: Path,
    *,
    offline: bool,
    demo_data: bool,
    app_version: str = "0.1.0",
    prompt_version: str = "0.1.0",
    brand_rules_version: str = "0.1.0",
    evidence_count: int = 0,
    pending_count: int = 0,
    conflict_count: int = 0,
    stale_count: int = 0,
) -> dict[str, Any]:
    entries = []
    for file_path in sorted(files):
        entries.append(
            {
                "path": file_path.relative_to(package_root).as_posix(),
                "size": file_path.stat().st_size,
                "sha256": file_sha256(file_path),
            }
        )

    return {
        "manifest_version": MANIFEST_VERSION,
        "package_id": package_id,
        "product_id": product_id,
        "direction_id": direction_id,
        "app_version": app_version,
        "prompt_version": prompt_version,
        "brand_rules_version": brand_rules_version,
        "offline": offline,
        "demo_data": demo_data,
        "evidence_count": evidence_count,
        "pending_count": pending_count,
        "conflict_count": conflict_count,
        "stale_count": stale_count,
        "files": entries,
    }


def write_manifest(manifest: dict[str, Any], destination: Path) -> None:
    destination.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def verify_manifest(package_root: Path, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    declared_paths = set()
    for entry in manifest.get("files", []):
        relative_path = entry["path"]
        declared_paths.add(relative_path)
        file_path = package_root / relative_path
        if not file_path.is_file():
            errors.append(f"missing:{relative_path}")
            continue
        if file_path.stat().st_size != entry["size"]:
            errors.append(f"size:{relative_path}")
        if file_sha256(file_path) != entry["sha256"]:
            errors.append(f"sha256:{relative_path}")

    actual_paths = {
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    for extra_path in sorted(actual_paths - declared_paths):
        errors.append(f"undeclared:{extra_path}")
    return errors
