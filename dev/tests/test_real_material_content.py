from __future__ import annotations

from pathlib import Path

import pytest

from app.core.business_judgment import build_business_judgment
from app.core.content_generator import CHANNELS, build_content_package
from app.core.creative_directions import build_creative_directions
from app.core.evidence_extractor import extract_evidence_from_index
from app.core.models import ValidationLevel
from app.core.product_understanding import build_product_understanding
from app.core.source_scanner import scan_library

from app.demo_paths import BAG_PACK, JEANS_PACK as CLOTHING_PACK


def _content(pack: Path, *, direction_index: int = 0):
    if not pack.is_dir():
        pytest.skip(f"真实素材包不存在：{pack}")
    scan_report = scan_library(pack)
    extraction = extract_evidence_from_index(scan_report.index(), scan_report.library_root)
    understanding = build_product_understanding(extraction, scan_report.index())
    judgment = build_business_judgment(understanding, extraction)
    directions = build_creative_directions(judgment, understanding, extraction)
    direction = directions.directions[direction_index]
    report = build_content_package(
        directions,
        understanding,
        extraction,
        selected_direction_id=direction.direction_id,
    )
    return report, direction


def test_real_jeans_waist_direction_content_is_grounded() -> None:
    report, direction = _content(CLOTHING_PACK, direction_index=0)

    assert direction.name.startswith("坐一整天")
    assert len(report.items) == len(CHANNELS)
    bodies = "\n".join(item.body for item in report.items)
    assert "329 元" in bodies
    assert "约 2 cm" in bodies or "余量" in bodies
    assert all(item.validation_level is not ValidationLevel.FAIL for item in report.items)
    assert all(item.direction_id == direction.direction_id for item in report.items)
    assert all(
        set(item.evidence_ids).issubset(set(direction.evidence_ids))
        for item in report.items
    )


def test_real_bag_content_changes_with_direction() -> None:
    first, direction_a = _content(BAG_PACK, direction_index=0)
    second, direction_b = _content(BAG_PACK, direction_index=2)

    assert direction_a.name != direction_b.name
    first_xhs = next(item for item in first.items if item.channel == "xiaohongshu")
    second_xhs = next(item for item in second.items if item.channel == "xiaohongshu")
    assert first_xhs.body != second_xhs.body
    assert all(
        item.validation_level in {ValidationLevel.PASS, ValidationLevel.WARN}
        for item in first.items
    )


def test_content_generation_leaves_material_packs_unchanged() -> None:
    before = {
        path.relative_to(CLOTHING_PACK).as_posix(): path.stat().st_size
        for path in CLOTHING_PACK.rglob("*")
        if path.is_file()
    }
    _content(CLOTHING_PACK)
    after = {
        path.relative_to(CLOTHING_PACK).as_posix(): path.stat().st_size
        for path in CLOTHING_PACK.rglob("*")
        if path.is_file()
    }
    assert before == after
