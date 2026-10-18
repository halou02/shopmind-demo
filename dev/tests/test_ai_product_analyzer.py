from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.core.ai_product_analyzer import (
    AIProductAnalysis,
    analyze_product,
    build_offline_analysis,
    merge_ai_analysis,
    read_text_materials,
)
from app.core.evidence_extractor import extract_evidence_from_index
from app.core.product_understanding import SECTION_PRODUCT_FACTS, build_product_understanding
from app.core.source_scanner import scan_library


def _write_pack(tmp_path: Path) -> Path:
    pack = tmp_path / "pack"
    images = pack / "商品图片"
    images.mkdir(parents=True)
    (images / "01_主图.png").write_bytes(b"fakepng")
    (pack / "商品资料.xlsx").write_bytes(b"")  # scaffold may be created elsewhere
    return pack


def test_offline_analysis_uses_product_form() -> None:
    analysis = build_offline_analysis(
        product_form={
            "product_name": "轻序托特包",
            "product_category": "箱包",
            "product_price": "120",
            "product_desc": "绿色，容量大",
            "product_brand": "TMM",
        },
        image_paths=[Path("01_主图.png")],
        evidence_summary="",
    )

    assert analysis.product_name == "轻序托特包"
    assert "箱包" in analysis.category_positioning
    assert analysis.product_facts["商品类别"] == "箱包"
    assert analysis.product_facts["建议价格"] == "120 元"
    assert analysis.source == "offline"


def test_merge_ai_analysis_enriches_product_facts(tmp_path: Path) -> None:
    from tests.test_product_understanding import _build_pack

    pack = _build_pack(tmp_path)
    scan_report = scan_library(pack)
    extraction = extract_evidence_from_index(scan_report.index(), scan_report.library_root)
    report = build_product_understanding(extraction, scan_report.index())

    analysis = AIProductAnalysis(
        product_name="云苔绿通勤托特包",
        category_positioning="轻量通勤托特包，强调大容量与秩序收纳",
        product_facts={"颜色": "云苔绿", "材质": "尼龙面料"},
        user_likes=("分区收纳清晰", "颜色清新"),
        target_audience="25-35岁通勤女性",
        core_opportunity="主打轻量通勤中的收纳秩序感",
        source="llm",
        model="qwen-vl-max",
    )
    merged = merge_ai_analysis(report, analysis)

    facts = {fact.label: fact.summary for fact in merged.facts_for_section(SECTION_PRODUCT_FACTS)}
    assert facts["颜色"] == "云苔绿"
    assert facts["品类定位"] == "轻量通勤托特包，强调大容量与秩序收纳"
    assert merged.product_name == "云苔绿通勤托特包"
    assert merged.boundary_notice == "基于用户图片与商品信息的 AI 分析"


def test_analyze_product_uses_mocked_vision_llm(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    images = pack / "商品图片"
    images.mkdir(parents=True)
    (images / "01_主图.png").write_bytes(b"png")

    scan_report = scan_library(pack)
    extraction = extract_evidence_from_index(scan_report.index(), scan_report.library_root)

    mocked_payload = {
        "product_name": "云苔绿托特包",
        "category_positioning": "面向通勤的大容量托特包",
        "product_facts": {"颜色": "云苔绿", "材质": "尼龙"},
        "selling_points": ["大容量", "轻便"],
        "target_audience": "通勤女性",
        "usage_scenarios": ["通勤", "咖啡馆"],
        "user_likes": ["颜色好看"],
        "user_concerns": ["耐脏性未知"],
        "content_opportunities": "展示内部分区",
        "brand_tone": "清新实用",
        "visual_keywords": ["云苔绿", "托特"],
        "core_opportunity": "强调通勤收纳秩序",
        "data_gaps": [],
    }

    with patch("app.core.ai_product_analyzer.load_llm_config") as mock_cfg, patch(
        "app.core.ai_product_analyzer.chat_completion_json",
        return_value=mocked_payload,
    ):
        mock_cfg.return_value.enabled = True
        mock_cfg.return_value.model = "qwen-vl-max"
        mock_cfg.return_value.max_images = 6
        mock_cfg.return_value.timeout_seconds = 30
        mock_cfg.return_value.base_url = "https://example.com/v1"
        mock_cfg.return_value.api_key = "test-key"

        analysis = analyze_product(
            material_root=pack,
            index=scan_report.index(),
            product_form={"product_category": "箱包", "product_desc": "绿色，容量大"},
            extraction=extraction,
        )

    assert analysis.source == "llm"
    assert analysis.product_facts["颜色"] == "云苔绿"
    assert analysis.core_opportunity == "强调通勤收纳秩序"


def test_read_text_materials_reads_reference_docs(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    docs = pack / "参考资料"
    docs.mkdir(parents=True)
    (docs / "brief.txt").write_text("材质：尼龙面料\n卖点：大容量通勤", encoding="utf-8")

    text = read_text_materials(pack)

    assert "尼龙面料" in text
    assert "brief.txt" in text


def test_offline_analysis_uses_text_materials() -> None:
    analysis = build_offline_analysis(
        product_form={"product_name": "测试包", "product_category": "箱包"},
        image_paths=[],
        evidence_summary="",
        text_materials="材质：牛皮\n卖点：商务通勤",
    )

    assert "上传资料摘要" in analysis.product_facts
    assert analysis.product_facts["材质"] == "资料中提及材质信息"
