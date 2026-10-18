from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.core.evidence_extractor import EvidenceExtractionReport
from app.core.hashing import sha256_bytes
from app.core.models import BusinessJudgment, Evidence
from app.core.product_understanding import (
    ProductUnderstandingReport,
    SECTION_BRAND_MEMORY,
    SECTION_DATA_GAPS,
    SECTION_HISTORY_PATTERNS,
    SECTION_PRODUCT_FACTS,
    SECTION_REVIEW_INSIGHTS,
)
from app.core.source_index import output_is_outside_source

JUDGMENT_FILE_NAME = "business_judgment.json"
BLOCKED_OUTPUT_INSIDE_SOURCE = "blocked:output_inside_source_root"
JUDGMENT_ID_LENGTH = 12

NOT_CONNECTED_NOTICE = "市场体量、竞品价格、真实利润、广告投放数据。未接入，不作判断。"
DEFAULT_RECOMMENDATION = "建议进入新品内容制作与小范围验证。"

JEANS_OPPORTUNITY = (
    "不主打“显瘦神裤”，而主打“通勤久坐也保有活动余量的高腰直筒裤”。"
)
BAG_OPPORTUNITY = (
    "不主打“百搭大容量”，而主打“轻量通勤中保持收纳秩序、下班不必换包”的托特包。"
)

JEANS_REVIEW_THEMES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("腰", "勒", "坐", "起身"), "久坐腰部不勒"),
    (("直筒", "贴", "走路", "自在"), "腿部不贴、走路自在"),
    (("中靛蓝", "通勤", "上班", "下班", "正式"), "中靛蓝适合通勤与下班切换"),
)

BAG_REVIEW_THEMES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("轻", "压肩", "不重"), "背负轻便、不压肩"),
    (("分区", "拉链", "收纳", "整齐"), "分区与拉链带来通勤安全感"),
    (("咖啡馆", "下班", "切换", "不必换包"), "下班场景仍可自然使用"),
)

FORBIDDEN_MARKET_TERMS = (
    "蓝海",
    "全网爆款",
    "高利润",
    "行业趋势",
    "爆款",
    "秒杀",
)


def judgment_id_of(product_id: str) -> str:
    return "jud_" + sha256_bytes(product_id.encode("utf-8"))[:JUDGMENT_ID_LENGTH]


@dataclass(frozen=True)
class BusinessJudgmentReport:
    product_id: str
    product_name: str
    judgment: BusinessJudgment
    judgment_scope: str
    target_audience: str
    product_evidence: str
    user_evidence: str
    history_risk: str
    product_risk: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "judgment_id": self.judgment.judgment_id,
            "judgment_scope": self.judgment_scope,
            "boundary_notice": self.judgment.boundary_notice,
            "not_connected_notice": self.judgment.not_connected_notice,
            "recommendation": self.judgment.recommendation,
            "opportunity": self.judgment.opportunity,
            "target_audience": self.target_audience,
            "product_evidence": self.product_evidence,
            "user_evidence": self.user_evidence,
            "history_risk": self.history_risk,
            "product_risk": self.product_risk,
            "risks": list(self.judgment.risks),
            "open_questions": list(self.judgment.open_questions),
            "next_actions": list(self.judgment.next_actions),
            "evidence_ids": list(self.judgment.evidence_ids),
        }

    def write_json(self, output_root: Path, library_root: Path) -> list[str]:
        if not output_is_outside_source(output_root, library_root):
            return [BLOCKED_OUTPUT_INSIDE_SOURCE]
        destination = output_root / self.product_id / JUDGMENT_FILE_NAME
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".tmp")
            temporary.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, destination)
        except OSError as error:
            return [f"write_failed:{destination.as_posix()}:{type(error).__name__}"]
        return []


def build_business_judgment(
    understanding: ProductUnderstandingReport,
    extraction: EvidenceExtractionReport,
    ai_analysis: dict[str, Any] | None = None,
) -> BusinessJudgmentReport:
    is_jeans = _is_jeans_product(understanding)
    review_count = _review_count(extraction.evidences)
    history_count = _history_count(extraction.evidences)

    product_evidence = _product_evidence_summary(understanding, is_jeans)
    user_evidence = _user_evidence_summary(
        extraction.evidences,
        JEANS_REVIEW_THEMES if is_jeans else BAG_REVIEW_THEMES,
    )
    history_risk = _history_risk_summary(understanding)
    product_risk = _product_risk_summary(understanding, extraction.evidences, is_jeans)
    target_audience = _target_audience_summary(understanding, is_jeans)
    opportunity = JEANS_OPPORTUNITY if is_jeans else BAG_OPPORTUNITY

    if ai_analysis and str(ai_analysis.get("source") or "") == "llm":
        ai_opportunity = str(ai_analysis.get("core_opportunity") or "").strip()
        ai_positioning = str(ai_analysis.get("category_positioning") or "").strip()
        ai_audience = str(ai_analysis.get("target_audience") or "").strip()
        ai_likes = ai_analysis.get("user_likes") or ai_analysis.get("selling_points") or []
        ai_concerns = ai_analysis.get("user_concerns") or []
        if ai_opportunity:
            opportunity = ai_opportunity
        elif ai_positioning:
            opportunity = ai_positioning
        if ai_audience:
            target_audience = ai_audience
        if isinstance(ai_likes, list) and ai_likes:
            likes_text = "；".join(str(item).strip() for item in ai_likes if str(item).strip())
            if likes_text and _is_placeholder_user_evidence(user_evidence):
                user_evidence = likes_text
        if isinstance(ai_concerns, list) and ai_concerns:
            concerns_text = "；".join(str(item).strip() for item in ai_concerns if str(item).strip())
            if concerns_text:
                product_risk = concerns_text if not product_risk else f"{product_risk}；{concerns_text}"

    open_questions = tuple(understanding.global_gaps)
    next_actions = _next_actions(understanding, is_jeans)
    evidence_ids = _collect_evidence_ids(
        understanding,
        extraction.evidences,
        is_jeans,
    )

    judgment = BusinessJudgment(
        judgment_id=judgment_id_of(understanding.product_id),
        product_id=understanding.product_id,
        recommendation=DEFAULT_RECOMMENDATION,
        opportunity=opportunity,
        risks=_merge_risks(history_risk, product_risk),
        open_questions=open_questions,
        next_actions=next_actions,
        evidence_ids=evidence_ids,
        not_connected_notice=NOT_CONNECTED_NOTICE,
    )
    _assert_no_forbidden_market_terms(judgment, product_evidence, user_evidence)

    judgment_scope = (
        f"基于本地商品参数、{review_count} 条演示评价、"
        f"{history_count} 条历史同类内容与品牌规则。"
    )
    if ai_analysis and str(ai_analysis.get("source") or "") == "llm":
        judgment_scope = "基于用户上传图片、商品信息与 AI 视觉理解的综合判断。"

    return BusinessJudgmentReport(
        product_id=understanding.product_id,
        product_name=understanding.product_name,
        judgment=judgment,
        judgment_scope=judgment_scope,
        target_audience=target_audience,
        product_evidence=product_evidence,
        user_evidence=user_evidence,
        history_risk=history_risk,
        product_risk=product_risk,
    )


def _is_jeans_product(understanding: ProductUnderstandingReport) -> bool:
    name = understanding.product_name
    if "牛仔裤" in name or "裤" in name:
        return True
    facts = understanding.facts_for_section(SECTION_PRODUCT_FACTS)
    return any(fact.label in {"裤长", "腰部活动余量", "版型"} for fact in facts)


def _review_count(evidences: Iterable[Evidence]) -> int:
    return len(
        {
            item.field_id.split(".")[2]
            for item in evidences
            if item.field_id.startswith("excel.用户评价.")
            and item.field_id.endswith(".编号")
        }
    )


def _history_count(evidences: Iterable[Evidence]) -> int:
    return len(
        {
            item.field_id.split(".")[2]
            for item in evidences
            if item.field_id.startswith("excel.历史内容.")
            and item.field_id.endswith(".日期")
        }
    )


def _fact_summary(
    understanding: ProductUnderstandingReport,
    section_id: str,
    label: str,
) -> str | None:
    section = understanding.section(section_id)
    if section is None:
        return None
    for fact in section.facts:
        if fact.label == label:
            return fact.summary
    return None


def _product_evidence_summary(
    understanding: ProductUnderstandingReport,
    is_jeans: bool,
) -> str:
    if is_jeans:
        parts = [
            _fact_summary(understanding, SECTION_PRODUCT_FACTS, "材质"),
            _fact_summary(understanding, SECTION_PRODUCT_FACTS, "版型"),
            _fact_summary(understanding, SECTION_PRODUCT_FACTS, "裤长"),
            _fact_summary(understanding, SECTION_PRODUCT_FACTS, "腰部活动余量"),
        ]
        color = understanding.product_name
        if "中靛蓝" in color:
            parts.append("中靛蓝")
        return "；".join(part for part in parts if part)
    parts = [
        _fact_summary(understanding, SECTION_PRODUCT_FACTS, "重量"),
        _fact_summary(understanding, SECTION_PRODUCT_FACTS, "尺寸"),
        _fact_summary(understanding, SECTION_PRODUCT_FACTS, "收纳结构"),
        _fact_summary(understanding, SECTION_PRODUCT_FACTS, "材质"),
    ]
    return "；".join(part for part in parts if part)


def _target_audience_summary(
    understanding: ProductUnderstandingReport,
    is_jeans: bool,
) -> str:
    scene = _fact_summary(understanding, SECTION_PRODUCT_FACTS, "主要场景")
    audience = _fact_summary(understanding, SECTION_PRODUCT_FACTS, "核心人群")
    brand_tone = _fact_summary(understanding, SECTION_BRAND_MEMORY, "品牌定位")
    if is_jeans:
        base = (
            "需在办公室、通勤、咖啡馆与周末步行之间切换的城市女性；"
            "重视穿着感受，不希望被身材焦虑营销。"
        )
        if scene:
            return f"{base} 主要场景依据：{scene}。"
        return base
    base = "25 至 35 岁、通勤距离较长的城市女性，希望包轻、能装且外观不笨重。"
    if audience:
        return f"{audience}。{base}"
    if brand_tone:
        return f"{brand_tone}。{base}"
    return base


def _is_placeholder_user_evidence(text: str) -> bool:
    lowered = text.strip().lower()
    markers = (
        "待 ai 结合图像理解商品",
        "图片素材已上传",
        "尚未形成稳定主题分组",
        "本地评价已读取，但尚未形成稳定主题分组",
    )
    return any(marker in lowered for marker in markers)


def _user_evidence_summary(
    evidences: Iterable[Evidence],
    themes: tuple[tuple[tuple[str, ...], str], ...],
) -> str:
    reviews = _group_reviews(evidences)
    lines: list[str] = []
    for keywords, label in themes:
        matched_ids = []
        for review_id, fields in sorted(reviews.items()):
            haystack = (
                f"{_review_field_value(fields, '评价文本')} "
                f"{_review_field_value(fields, '标签')}"
            )
            if any(keyword in haystack for keyword in keywords):
                matched_ids.append(review_id)
        if matched_ids:
            lines.append(f"{'/'.join(matched_ids)}：{label}")
    if lines:
        return "；".join(lines)
    return "本地评价已读取，但尚未形成稳定主题分组（资料不足）。"


def _group_reviews(evidences: Iterable[Evidence]) -> dict[str, dict[str, Evidence]]:
    grouped: dict[str, dict[str, Evidence]] = {}
    for item in evidences:
        if not item.field_id.startswith("excel.用户评价."):
            continue
        parts = item.field_id.split(".")
        if len(parts) != 4:
            continue
        review_id, column = parts[2], parts[3]
        grouped.setdefault(review_id, {})[column] = item
    return grouped


def _history_risk_summary(understanding: ProductUnderstandingReport) -> str:
    history = _fact_summary(understanding, SECTION_HISTORY_PATTERNS, "历史内容结论")
    if history:
        return f"历史内容显示：{history}"
    repeat = _fact_summary(understanding, SECTION_HISTORY_PATTERNS, "历史重复表达风险")
    if repeat:
        return f"历史重复表达风险：{repeat}"
    high_frequency = _fact_summary(understanding, SECTION_BRAND_MEMORY, "历史高频词")
    if high_frequency:
        return f"品牌记忆中的历史高频词：{high_frequency}"
    return "历史内容不足，暂无法判断重复表达风险（资料不足）。"


def _product_risk_summary(
    understanding: ProductUnderstandingReport,
    evidences: Iterable[Evidence],
    is_jeans: bool,
) -> str:
    concerns = _fact_summary(understanding, SECTION_REVIEW_INSIGHTS, "用户主要顾虑")
    if concerns:
        negative_ids = _negative_review_ids(evidences)
        if negative_ids:
            prefix = "/".join(negative_ids)
            return f"{prefix}：{concerns}"
        return concerns
    if is_jeans:
        return "缺少足够负向评价，尺码与裤长参考风险待补资料确认（资料不足）。"
    return "缺少足够负向评价，肩带稳定性与耐脏风险待补资料确认（资料不足）。"


def _negative_review_ids(evidences: Iterable[Evidence]) -> tuple[str, ...]:
    reviews = _group_reviews(evidences)
    negative = []
    for review_id, fields in sorted(reviews.items()):
        sentiment = _review_field_value(fields, "情绪")
        if sentiment in {"负向", "负面", "顾虑", "差评", "中性", "中评"}:
            negative.append(review_id)
    return tuple(negative)


def _next_actions(
    understanding: ProductUnderstandingReport,
    is_jeans: bool,
) -> tuple[str, ...]:
    actions: list[str] = []
    if is_jeans:
        actions.extend(
            [
                "补拍坐姿侧面、办公室走动、三种身高与三档裤长上身参考。",
                "补充腰围/臀围/身高选择表。",
            ]
        )
    else:
        actions.extend(
            [
                "补拍厚外套肩背与地铁场景，验证肩带稳定性。",
                "补充浅色面料日常打理说明与装填后的上身比例图。",
            ]
        )
    gap_section = understanding.section(SECTION_DATA_GAPS)
    if gap_section is not None:
        for gap in gap_section.gaps:
            if gap not in actions:
                actions.append(gap.replace("（资料不足）", "。"))
    return tuple(actions)


def _merge_risks(history_risk: str, product_risk: str) -> tuple[str, ...]:
    risks = []
    if history_risk:
        risks.append(history_risk)
    if product_risk and product_risk not in risks:
        risks.append(product_risk)
    return tuple(risks)


def _collect_evidence_ids(
    understanding: ProductUnderstandingReport,
    evidences: Iterable[Evidence],
    is_jeans: bool,
) -> tuple[str, ...]:
    wanted_sections = {
        SECTION_PRODUCT_FACTS,
        SECTION_REVIEW_INSIGHTS,
        SECTION_BRAND_MEMORY,
        SECTION_HISTORY_PATTERNS,
    }
    collected: list[str] = []
    for section_id in wanted_sections:
        section = understanding.section(section_id)
        if section is None:
            continue
        for fact in section.facts:
            collected.extend(fact.evidence_ids)
    if is_jeans:
        theme_ids = _review_ids_for_themes(evidences, JEANS_REVIEW_THEMES)
    else:
        theme_ids = _review_ids_for_themes(evidences, BAG_REVIEW_THEMES)
    for review_id in theme_ids:
        collected.extend(
            item.evidence_id
            for item in evidences
            if item.field_id.startswith(f"excel.用户评价.{review_id}.")
        )
    return tuple(dict.fromkeys(item for item in collected if item))


def _review_ids_for_themes(
    evidences: Iterable[Evidence],
    themes: tuple[tuple[tuple[str, ...], str], ...],
) -> tuple[str, ...]:
    reviews = _group_reviews(evidences)
    matched: list[str] = []
    for keywords, _label in themes:
        for review_id, fields in reviews.items():
            haystack = (
                f"{_review_field_value(fields, '评价文本')} "
                f"{_review_field_value(fields, '标签')}"
            )
            if any(keyword in haystack for keyword in keywords):
                matched.append(review_id)
    return tuple(dict.fromkeys(matched))


def _review_field_value(fields: dict[str, Evidence], column: str) -> str:
    item = fields.get(column)
    if item is None:
        return ""
    return str(item.value)


def _assert_no_forbidden_market_terms(*texts: str | BusinessJudgment) -> None:
    corpus = []
    for item in texts:
        if isinstance(item, BusinessJudgment):
            corpus.extend(
                [
                    item.recommendation,
                    item.opportunity,
                    *item.risks,
                    *item.open_questions,
                    *item.next_actions,
                ]
            )
        else:
            corpus.append(item)
    joined = " ".join(corpus)
    for term in FORBIDDEN_MARKET_TERMS:
        if term in joined:
            raise ValueError(f"forbidden_market_term:{term}")
