from __future__ import annotations

from pathlib import Path

import pytest

from app.core.business_judgment import build_business_judgment
from app.core.creative_directions import build_creative_directions, evidence_kinds_for_direction
from app.core.evidence_extractor import extract_evidence_from_index
from app.core.product_understanding import build_product_understanding
from app.core.source_scanner import scan_library

from app.demo_paths import BAG_PACK, JEANS_PACK as CLOTHING_PACK

JEANS_DIRECTION_NAMES = (
    "坐一整天 腰里也有余量",
    "从会议室到咖啡馆 不用换掉自己",
    "三档裤长减少改裤脚负担",
)
BAG_DIRECTION_NAMES = (
    "早八不慌的一包秩序",
    "轻一点上班 留一点余裕",
    "下班不换包的城市续航",
)


def _directions(pack: Path):
    if not pack.is_dir():
        pytest.skip(f"真实素材包不存在：{pack}")
    scan_report = scan_library(pack)
    extraction = extract_evidence_from_index(scan_report.index(), scan_report.library_root)
    understanding = build_product_understanding(extraction, scan_report.index())
    judgment = build_business_judgment(understanding, extraction)
    return build_creative_directions(judgment, understanding, extraction), extraction


def test_real_jeans_directions_match_demo_strategy() -> None:
    report, extraction = _directions(CLOTHING_PACK)

    assert tuple(item.name for item in report.directions) == JEANS_DIRECTION_NAMES
    inseam = report.directions[2]
    assert "98" in inseam.main_selling_point or "裤长" in inseam.main_selling_point
    assert any("身高" in question or "尺码" in question for question in inseam.open_questions)

    evidence_sets = [set(direction.evidence_ids) for direction in report.directions]
    assert evidence_sets[0] != evidence_sets[1] != evidence_sets[2]

    for direction in report.directions:
        assert len(evidence_kinds_for_direction(direction, extraction.evidences)) >= 2
        assert direction.risks
        assert direction.open_questions
        assert direction.asset_ids
        assert "显瘦" in direction.avoid_expression or "百搭" in direction.avoid_expression or "小个子" in direction.avoid_expression


def test_real_bag_directions_are_grounded_and_distinct() -> None:
    report, extraction = _directions(BAG_PACK)

    assert tuple(item.name for item in report.directions) == BAG_DIRECTION_NAMES
    light = report.directions[1]
    assert "380" in light.main_selling_point or "负担" in light.main_selling_point

    names = {direction.name for direction in report.directions}
    audiences = {direction.audience for direction in report.directions}
    assert len(names) == 3
    assert len(audiences) == 3

    for direction in report.directions:
        assert len(evidence_kinds_for_direction(direction, extraction.evidences)) >= 2
        assert direction.asset_ids


def test_creative_directions_leave_material_packs_unchanged() -> None:
    before = {
        path.relative_to(CLOTHING_PACK).as_posix(): path.stat().st_size
        for path in CLOTHING_PACK.rglob("*")
        if path.is_file()
    }
    _directions(CLOTHING_PACK)
    after = {
        path.relative_to(CLOTHING_PACK).as_posix(): path.stat().st_size
        for path in CLOTHING_PACK.rglob("*")
        if path.is_file()
    }
    assert before == after
