from __future__ import annotations

from pathlib import Path

import pytest

from app.core.business_judgment import build_business_judgment
from app.core.evidence_extractor import extract_evidence_from_index
from app.core.product_understanding import build_product_understanding
from app.core.source_scanner import scan_library

from app.demo_paths import BAG_PACK, JEANS_PACK as CLOTHING_PACK


def _judge(pack: Path):
    if not pack.is_dir():
        pytest.skip(f"真实素材包不存在：{pack}")
    scan_report = scan_library(pack)
    extraction = extract_evidence_from_index(scan_report.index(), scan_report.library_root)
    understanding = build_product_understanding(extraction, scan_report.index())
    return build_business_judgment(understanding, extraction)


def test_real_jeans_business_judgment_matches_demo_card() -> None:
    report = _judge(CLOTHING_PACK)

    assert "15 条演示评价" in report.judgment_scope
    assert "5 条历史同类内容" in report.judgment_scope
    assert report.judgment.recommendation == "建议进入新品内容制作与小范围验证。"
    assert "显瘦神裤" in report.judgment.opportunity
    assert "久坐" in report.judgment.opportunity or "活动余量" in report.judgment.opportunity
    assert "99% 棉 1% 氨纶" in report.product_evidence
    assert "98" in report.product_evidence and "2 cm" in report.product_evidence
    assert "J01" in report.user_evidence
    assert "显瘦" in report.history_risk
    assert "百搭" in report.history_risk
    assert any(review_id in report.product_risk for review_id in ("J11", "J12", "J13"))
    assert any("尺码" in action or "裤长" in action for action in report.judgment.next_actions)
    assert report.judgment.boundary_notice == "仅基于本地资料"
    assert "未接入，不作判断" in report.judgment.not_connected_notice
    assert not any(
        term in report.judgment.opportunity
        for term in ("蓝海", "高利润", "行业趋势", "全网爆款")
    )


def test_real_bag_business_judgment_is_grounded_in_local_facts() -> None:
    report = _judge(BAG_PACK)

    assert "15 条演示评价" in report.judgment_scope
    assert "380" in report.product_evidence or "34" in report.product_evidence
    assert "百搭" in report.judgment.opportunity or "大容量" in report.judgment.opportunity
    assert report.judgment.evidence_ids
    assert report.judgment.risks
    assert any("耐脏" in question or "肩带" in question for question in report.judgment.open_questions)


def test_business_judgment_leaves_material_packs_unchanged() -> None:
    before = {
        path.relative_to(CLOTHING_PACK).as_posix(): path.stat().st_size
        for path in CLOTHING_PACK.rglob("*")
        if path.is_file()
    }
    _judge(CLOTHING_PACK)
    after = {
        path.relative_to(CLOTHING_PACK).as_posix(): path.stat().st_size
        for path in CLOTHING_PACK.rglob("*")
        if path.is_file()
    }
    assert before == after
