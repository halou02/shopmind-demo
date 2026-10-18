from __future__ import annotations

from pathlib import Path

import pytest

from app.core.business_judgment import build_business_judgment
from app.core.content_generator import build_content_package
from app.core.creative_directions import build_creative_directions
from app.core.evidence_extractor import extract_evidence_from_index
from app.core.product_understanding import build_product_understanding
from app.core.source_scanner import scan_library
from app.export.content_package import export_content_package
from app.export.manifest import verify_manifest

from app.demo_paths import JEANS_PACK as CLOTHING_PACK


def _export(pack: Path, output: Path):
    if not pack.is_dir():
        pytest.skip(f"真实素材包不存在：{pack}")
    scan_report = scan_library(pack)
    extraction = extract_evidence_from_index(scan_report.index(), scan_report.library_root)
    understanding = build_product_understanding(extraction, scan_report.index())
    judgment = build_business_judgment(understanding, extraction)
    directions = build_creative_directions(judgment, understanding, extraction)
    content = build_content_package(
        directions,
        understanding,
        extraction,
        selected_direction_id=directions.directions[0].direction_id,
    )
    return export_content_package(
        understanding=understanding,
        judgment_report=judgment,
        directions_report=directions,
        content_report=content,
        extraction=extraction,
        index=scan_report.index(),
        output_root=output,
        library_root=scan_report.library_root,
    )


def test_real_jeans_export_package_is_verifiable(tmp_path: Path) -> None:
    export = _export(CLOTHING_PACK, tmp_path / "workspace")

    assert export.export_status in {"ready", "partial"}
    assert export.package_root.is_dir()
    assert (export.package_root / "evidence.json").is_file()
    assert (export.package_root / "business_judgment.json").is_file()
    assert (export.package_root / "content" / "douyin.md").is_file()
    assert verify_manifest(export.package_root, export.manifest) == []
    readme = (export.package_root / "README.md").read_text(encoding="utf-8")
    assert "仅基于本地资料" in readme


def test_export_leaves_material_pack_unchanged(tmp_path: Path) -> None:
    before = {
        path.relative_to(CLOTHING_PACK).as_posix(): path.stat().st_size
        for path in CLOTHING_PACK.rglob("*")
        if path.is_file()
    }
    _export(CLOTHING_PACK, tmp_path / "workspace")
    after = {
        path.relative_to(CLOTHING_PACK).as_posix(): path.stat().st_size
        for path in CLOTHING_PACK.rglob("*")
        if path.is_file()
    }
    assert before == after
