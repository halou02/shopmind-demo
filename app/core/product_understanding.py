from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from app.core.evidence_extractor import EvidenceExtractionReport
from app.core.hashing import sha256_bytes
from app.core.models import Asset, Evidence, EvidenceStatus, Source, SourceKind, Status
from app.core.source_index import ID_SEPARATOR, SourceIndex, output_is_outside_source

UNDERSTANDING_FILE_NAME = "product_understanding.json"
BLOCKED_OUTPUT_INSIDE_SOURCE = "blocked:output_inside_source_root"
FACT_ID_LENGTH = 12
ASSET_ID_LENGTH = 16

SECTION_PRODUCT_FACTS = "product_facts"
SECTION_REVIEW_INSIGHTS = "review_insights"
SECTION_BRAND_MEMORY = "brand_memory"
SECTION_HISTORY_PATTERNS = "history_patterns"
SECTION_AVAILABLE_ASSETS = "available_assets"
SECTION_DATA_GAPS = "data_gaps"

POSITIVE_SENTIMENTS = frozenset({"正向", "正面", "好评"})
NEGATIVE_SENTIMENTS = frozenset({"负向", "负面", "顾虑", "差评"})
NEUTRAL_SENTIMENTS = frozenset({"中性", "中评"})

PRODUCT_PARAM_LABELS = (
    "品牌",
    "商品名称",
    "SKU",
    "建议价格",
    "材质",
    "克重",
    "重量",
    "版型",
    "裤长",
    "尺寸",
    "腰部活动余量",
    "收纳结构",
    "主要场景",
    "核心人群",
    "品牌禁用词",
)

BRAND_MEMORY_LABELS = (
    "品牌定位",
    "品牌语气",
    "常用表达",
    "禁用词",
    "历史高频词",
    "视觉关键词",
)

TEXT_BRAND_FIELD_MAP = {
    "品牌定位": "text.品牌定位",
    "品牌语气": "text.语气",
    "常用表达": "text.常用表达",
    "禁用词": "text.禁用词",
    "视觉关键词": "text.视觉关键词",
}

IMAGE_ROLE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("平铺", "main_image"),
    ("主图", "main_image"),
    ("细节", "detail"),
    ("腰部", "detail"),
    ("容量", "detail"),
    ("场景", "scene"),
    ("咖啡馆", "scene"),
    ("通勤", "scene"),
    ("上身", "on_body"),
)

JEANS_GAP_CHECKS: tuple[tuple[str, str], ...] = (
    ("腰围", "缺少腰围/臀围尺码对照表（资料不足）"),
    ("臀围", "缺少腰围/臀围尺码对照表（资料不足）"),
    ("身高", "缺少不同身高与三档裤长的上身参考（资料不足）"),
    ("尺码表", "缺少完整尺码选择表（资料不足）"),
)

BAG_GAP_CHECKS: tuple[tuple[str, str], ...] = (
    ("耐脏", "缺少浅色面料耐脏测试记录（资料不足）"),
    ("承重", "缺少厚外套装入后的肩带稳定性实拍（资料不足）"),
)


def fact_id_of(product_id: str, section_id: str, label: str) -> str:
    joined = f"{product_id}{ID_SEPARATOR}{section_id}{ID_SEPARATOR}{label}"
    return "fact_" + sha256_bytes(joined.encode("utf-8"))[:FACT_ID_LENGTH]


def asset_id_of(product_id: str, source_id: str) -> str:
    joined = f"{product_id}{ID_SEPARATOR}{source_id}"
    return "ast_" + sha256_bytes(joined.encode("utf-8"))[:ASSET_ID_LENGTH]


@dataclass(frozen=True)
class UnderstandingFact:
    fact_id: str
    section_id: str
    label: str
    summary: str
    evidence_ids: tuple[str, ...]
    source_locators: tuple[str, ...]
    status: EvidenceStatus
    is_gap: bool = False


@dataclass(frozen=True)
class UnderstandingSection:
    section_id: str
    title: str
    facts: tuple[UnderstandingFact, ...]
    gaps: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProductUnderstandingReport:
    product_id: str
    product_name: str
    boundary_notice: str
    not_connected_notice: str
    sections: tuple[UnderstandingSection, ...]
    assets: tuple[Asset, ...]
    evidence_count: int
    global_gaps: tuple[str, ...]

    def section(self, section_id: str) -> UnderstandingSection | None:
        for item in self.sections:
            if item.section_id == section_id:
                return item
        return None

    def facts_for_section(self, section_id: str) -> tuple[UnderstandingFact, ...]:
        section = self.section(section_id)
        return section.facts if section is not None else ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "boundary_notice": self.boundary_notice,
            "not_connected_notice": self.not_connected_notice,
            "evidence_count": self.evidence_count,
            "global_gaps": list(self.global_gaps),
            "assets": [asset_to_dict(item) for item in self.assets],
            "sections": [section_to_dict(item) for item in self.sections],
        }

    def write_json(self, output_root: Path, library_root: Path) -> list[str]:
        if not output_is_outside_source(output_root, library_root):
            return [BLOCKED_OUTPUT_INSIDE_SOURCE]
        destination = output_root / self.product_id / UNDERSTANDING_FILE_NAME
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


def build_product_understanding(
    extraction: EvidenceExtractionReport,
    index: SourceIndex,
) -> ProductUnderstandingReport:
    evidences = extraction.evidences
    product_name = _product_name(evidences)
    sections = (
        _build_product_facts(extraction.product_id, evidences),
        _build_review_insights(extraction.product_id, evidences),
        _build_brand_memory(extraction.product_id, evidences),
        _build_history_patterns(extraction.product_id, evidences),
        _build_available_assets(extraction.product_id, index.sources),
    )
    global_gaps = _detect_global_gaps(evidences, product_name)
    gap_section = UnderstandingSection(
        section_id=SECTION_DATA_GAPS,
        title="资料缺口",
        facts=tuple(
            UnderstandingFact(
                fact_id=fact_id_of(extraction.product_id, SECTION_DATA_GAPS, gap),
                section_id=SECTION_DATA_GAPS,
                label="资料不足",
                summary=gap,
                evidence_ids=(),
                source_locators=(),
                status=EvidenceStatus.PENDING,
                is_gap=True,
            )
            for gap in global_gaps
        ),
        gaps=global_gaps,
    )
    return ProductUnderstandingReport(
        product_id=extraction.product_id,
        product_name=product_name,
        boundary_notice="仅基于本地资料",
        not_connected_notice="未接入，不作判断",
        sections=sections + (gap_section,),
        assets=_assets_from_sources(extraction.product_id, index.sources),
        evidence_count=len(evidences),
        global_gaps=global_gaps,
    )


PRODUCT_FORM_FIELDS: tuple[tuple[str, str], ...] = (
    ("product_name", "商品名称"),
    ("product_category", "商品类别"),
    ("product_price", "建议价格"),
    ("product_desc", "主要场景"),
    ("product_brand", "品牌名称"),
)


def apply_product_form(
    report: ProductUnderstandingReport,
    product_form: dict[str, Any] | None,
) -> ProductUnderstandingReport:
    if not product_form:
        return report

    product_name = str(product_form.get("product_name") or "").strip() or report.product_name
    section = report.section(SECTION_PRODUCT_FACTS)
    fact_map = {fact.label: fact for fact in (section.facts if section else ())}

    for field_key, label in PRODUCT_FORM_FIELDS:
        raw_value = product_form.get(field_key)
        if raw_value is None or str(raw_value).strip() == "":
            continue
        if field_key == "product_price":
            text = str(raw_value).strip()
            if text.replace(".", "", 1).isdigit():
                summary = _display_value(float(text) if "." in text else int(text), label)
            else:
                summary = text
        else:
            summary = str(raw_value).strip()
        fact_map[label] = UnderstandingFact(
            fact_id=fact_id_of(report.product_id, SECTION_PRODUCT_FACTS, label),
            section_id=SECTION_PRODUCT_FACTS,
            label=label,
            summary=summary,
            evidence_ids=(),
            source_locators=("user_form",),
            status=EvidenceStatus.CONFIRMED,
            is_gap=False,
        )

    updated_product_facts = UnderstandingSection(
        section_id=SECTION_PRODUCT_FACTS,
        title=section.title if section else "商品参数",
        facts=tuple(fact_map.values()),
    )
    updated_sections = tuple(
        updated_product_facts if item.section_id == SECTION_PRODUCT_FACTS else item for item in report.sections
    )
    return replace(report, product_name=product_name, sections=updated_sections)


def _build_product_facts(
    product_id: str,
    evidences: tuple[Evidence, ...],
) -> UnderstandingSection:
    facts: list[UnderstandingFact] = []
    for label in PRODUCT_PARAM_LABELS:
        field_id = f"excel.商品参数.{label}"
        matches = [item for item in evidences if item.field_id == field_id]
        if not matches:
            continue
        evidence = matches[0]
        summary = _display_value(evidence.value, label)
        facts.append(
            _make_fact(
                product_id=product_id,
                section_id=SECTION_PRODUCT_FACTS,
                label=label,
                summary=summary,
                evidence_items=matches,
            )
        )
    return UnderstandingSection(
        section_id=SECTION_PRODUCT_FACTS,
        title="商品参数",
        facts=tuple(facts),
    )


def _build_review_insights(
    product_id: str,
    evidences: tuple[Evidence, ...],
) -> UnderstandingSection:
    reviews = _group_reviews(evidences)
    facts: list[UnderstandingFact] = []

    positive_ids = [
        review_id
        for review_id, fields in sorted(reviews.items())
        if _review_sentiment(fields) in POSITIVE_SENTIMENTS
    ]
    if positive_ids:
        facts.append(
            _make_fact(
                product_id=product_id,
                section_id=SECTION_REVIEW_INSIGHTS,
                label="用户真正喜欢",
                summary=_join_review_summaries(reviews, positive_ids),
                evidence_items=_review_evidence_items(evidences, positive_ids),
            )
        )

    concern_ids = [
        review_id
        for review_id, fields in sorted(reviews.items())
        if _review_sentiment(fields) in NEGATIVE_SENTIMENTS | NEUTRAL_SENTIMENTS
    ]
    if concern_ids:
        facts.append(
            _make_fact(
                product_id=product_id,
                section_id=SECTION_REVIEW_INSIGHTS,
                label="用户主要顾虑",
                summary=_join_review_summaries(reviews, concern_ids),
                evidence_items=_review_evidence_items(evidences, concern_ids),
            )
        )

    review_count = len(reviews)
    if review_count:
        facts.append(
            _make_fact(
                product_id=product_id,
                section_id=SECTION_REVIEW_INSIGHTS,
                label="评价样本量",
                summary=f"共 {review_count} 条本地评价",
                evidence_items=[
                    item
                    for item in evidences
                    if item.field_id.startswith("excel.用户评价.")
                    and item.field_id.endswith(".编号")
                ],
            )
        )

    md_opportunity = [
        item
        for item in evidences
        if item.field_id.startswith("md.")
        and item.scope
        and "被忽略的表达机会" in str(item.scope)
    ]
    if md_opportunity:
        facts.append(
            _make_fact(
                product_id=product_id,
                section_id=SECTION_REVIEW_INSIGHTS,
                label="被忽略的表达机会",
                summary=str(md_opportunity[0].value),
                evidence_items=(md_opportunity[0],),
            )
        )
    else:
        summary_evidence = next(
            (
                item
                for item in evidences
                if item.field_id in {"text.history_summary", "text.结论"}
            ),
            None,
        )
        if summary_evidence is not None:
            facts.append(
                _make_fact(
                    product_id=product_id,
                    section_id=SECTION_REVIEW_INSIGHTS,
                    label="被忽略的表达机会",
                    summary=str(summary_evidence.value),
                    evidence_items=(summary_evidence,),
                )
            )

    return UnderstandingSection(
        section_id=SECTION_REVIEW_INSIGHTS,
        title="评价洞察",
        facts=tuple(facts),
    )


def _build_brand_memory(
    product_id: str,
    evidences: tuple[Evidence, ...],
) -> UnderstandingSection:
    facts: list[UnderstandingFact] = []

    for label in BRAND_MEMORY_LABELS:
        sheet_field_id = f"excel.品牌记忆.{label}.内容"
        sheet_matches = [item for item in evidences if item.field_id == sheet_field_id]
        if sheet_matches:
            facts.append(
                _make_fact(
                    product_id=product_id,
                    section_id=SECTION_BRAND_MEMORY,
                    label=label,
                    summary=str(sheet_matches[0].value),
                    evidence_items=sheet_matches,
                )
            )
            continue
        text_field_id = TEXT_BRAND_FIELD_MAP.get(label, f"text.{_slug_label(label)}")
        text_matches = [item for item in evidences if item.field_id == text_field_id]
        if text_matches:
            facts.append(
                _make_fact(
                    product_id=product_id,
                    section_id=SECTION_BRAND_MEMORY,
                    label=label,
                    summary=str(text_matches[0].value),
                    evidence_items=text_matches,
                )
            )

    reminder = [item for item in evidences if item.field_id == "text.历史表达提醒"]
    if reminder:
        facts.append(
            _make_fact(
                product_id=product_id,
                section_id=SECTION_BRAND_MEMORY,
                label="历史表达提醒",
                summary=str(reminder[0].value),
                evidence_items=reminder,
            )
        )

    banned = [item for item in evidences if item.field_id == "excel.商品参数.品牌禁用词"]
    if banned:
        facts.append(
            _make_fact(
                product_id=product_id,
                section_id=SECTION_BRAND_MEMORY,
                label="商品参数禁用词",
                summary=str(banned[0].value),
                evidence_items=banned,
            )
        )

    return UnderstandingSection(
        section_id=SECTION_BRAND_MEMORY,
        title="品牌记忆",
        facts=tuple(facts),
    )


def _build_history_patterns(
    product_id: str,
    evidences: tuple[Evidence, ...],
) -> UnderstandingSection:
    facts: list[UnderstandingFact] = []

    risk_items = [
        item
        for item in evidences
        if item.field_id.startswith("excel.历史内容.")
        and item.field_id.endswith(".重复表达风险")
        and not _is_empty(item.value)
    ]
    if risk_items:
        merged = "；".join(
            f"{item.field_id.split('.')[2]}：{item.value}" for item in risk_items
        )
        facts.append(
            _make_fact(
                product_id=product_id,
                section_id=SECTION_HISTORY_PATTERNS,
                label="历史重复表达风险",
                summary=merged,
                evidence_items=risk_items,
            )
        )

    summary = [
        item
        for item in evidences
        if item.field_id in {"text.history_summary", "text.结论"}
    ]
    if summary:
        facts.append(
            _make_fact(
                product_id=product_id,
                section_id=SECTION_HISTORY_PATTERNS,
                label="历史内容结论",
                summary=str(summary[0].value),
                evidence_items=summary,
            )
        )

    high_frequency = [
        item
        for item in evidences
        if item.field_id.startswith("excel.品牌记忆.")
        and item.field_id.endswith(".历史高频词")
    ]
    if high_frequency:
        facts.append(
            _make_fact(
                product_id=product_id,
                section_id=SECTION_HISTORY_PATTERNS,
                label="品牌记忆中的历史高频词",
                summary=str(high_frequency[0].value),
                evidence_items=high_frequency,
            )
        )

    return UnderstandingSection(
        section_id=SECTION_HISTORY_PATTERNS,
        title="历史重复表达",
        facts=tuple(facts),
    )


def _build_available_assets(
    product_id: str,
    sources: tuple[Source, ...],
) -> UnderstandingSection:
    assets = _assets_from_sources(product_id, sources)
    facts = tuple(
        UnderstandingFact(
            fact_id=fact_id_of(product_id, SECTION_AVAILABLE_ASSETS, asset.relative_path),
            section_id=SECTION_AVAILABLE_ASSETS,
            label=asset.role,
            summary=asset.relative_path,
            evidence_ids=(),
            source_locators=(asset.relative_path,),
            status=EvidenceStatus.PENDING,
        )
        for asset in assets
    )
    return UnderstandingSection(
        section_id=SECTION_AVAILABLE_ASSETS,
        title="可用本地素材",
        facts=facts,
    )


def _assets_from_sources(
    product_id: str,
    sources: Iterable[Source],
) -> tuple[Asset, ...]:
    assets: list[Asset] = []
    for source in sources:
        if source.source_kind not in {SourceKind.IMAGE, SourceKind.VIDEO}:
            continue
        assets.append(
            Asset(
                asset_id=asset_id_of(product_id, source.source_id),
                product_id=product_id,
                source_id=source.source_id,
                relative_path=source.relative_path,
                asset_type=source.file_type,
                role=_infer_asset_role(source),
                file_hash=source.file_hash,
                processing_status=Status.READY,
            )
        )
    return tuple(assets)


def _detect_global_gaps(
    evidences: tuple[Evidence, ...],
    product_name: str,
) -> tuple[str, ...]:
    corpus = " ".join(
        str(item.value)
        for item in evidences
        if not item.field_id.endswith(".数据使用提示")
    )
    checks = JEANS_GAP_CHECKS if "牛仔裤" in product_name or "裤" in product_name else BAG_GAP_CHECKS
    gaps: list[str] = []
    for keyword, message in checks:
        if keyword not in corpus:
            if message not in gaps:
                gaps.append(message)
    return tuple(gaps)


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


def _review_sentiment(fields: dict[str, Evidence]) -> str:
    sentiment = fields.get("情绪")
    if sentiment is None:
        return ""
    return str(sentiment.value)


def _review_evidence_items(
    evidences: Iterable[Evidence],
    review_ids: Iterable[str],
) -> tuple[Evidence, ...]:
    wanted = set(review_ids)
    return tuple(
        item
        for item in evidences
        if item.field_id.startswith("excel.用户评价.")
        and item.field_id.split(".")[2] in wanted
    )


def _join_review_summaries(
    reviews: dict[str, dict[str, Evidence]],
    review_ids: Iterable[str],
) -> str:
    snippets: list[str] = []
    for review_id in review_ids:
        fields = reviews.get(review_id, {})
        text = fields.get("评价文本")
        if text is None:
            continue
        snippets.append(f"{review_id}：{text.value}")
    return "；".join(snippets)


def _product_name(evidences: Iterable[Evidence]) -> str:
    for item in evidences:
        if item.field_id == "excel.商品参数.商品名称":
            return str(item.value)
    for item in evidences:
        if item.field_id == "text.商品名称":
            return str(item.value)
    for item in evidences:
        if item.field_id.startswith("md.") and item.field_id.endswith(".商品名称"):
            return str(item.value)
    return "未命名商品"


def _display_value(value: Any, label: str) -> str:
    if label == "建议价格" and isinstance(value, (int, float)):
        return f"{int(value)} 元"
    if label in {"克重", "重量"} and isinstance(value, (int, float)):
        return f"{int(value)} g"
    return str(value)


def _infer_asset_role(source: Source) -> str:
    haystack = f"{source.file_name} {source.relative_path}"
    for keyword, role in IMAGE_ROLE_KEYWORDS:
        if keyword in haystack:
            return role
    if source.source_kind is SourceKind.VIDEO:
        return "video"
    return "image"


def _make_fact(
    *,
    product_id: str,
    section_id: str,
    label: str,
    summary: str,
    evidence_items: Iterable[Evidence],
) -> UnderstandingFact:
    items = tuple(evidence_items)
    return UnderstandingFact(
        fact_id=fact_id_of(product_id, section_id, label),
        section_id=section_id,
        label=label,
        summary=summary,
        evidence_ids=tuple(item.evidence_id for item in items),
        source_locators=tuple(
            locator
            for item in items
            if (locator := item.source_locator) is not None
        ),
        status=_aggregate_status(items),
    )


def _aggregate_status(items: Iterable[Evidence]) -> EvidenceStatus:
    statuses = {item.status for item in items}
    if EvidenceStatus.CONFLICT in statuses:
        return EvidenceStatus.CONFLICT
    if EvidenceStatus.STALE in statuses:
        return EvidenceStatus.STALE
    if statuses == {EvidenceStatus.CONFIRMED}:
        return EvidenceStatus.CONFIRMED
    return EvidenceStatus.PENDING


def _first_evidence_value(
    evidences: Iterable[Evidence],
    *,
    field_id: str,
) -> str | None:
    for item in evidences:
        if item.field_id == field_id:
            return str(item.value)
    return None


def _slug_label(label: str) -> str:
    return re.sub(r"\s+", "_", label.strip())


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def section_to_dict(section: UnderstandingSection) -> dict[str, Any]:
    return {
        "section_id": section.section_id,
        "title": section.title,
        "gaps": list(section.gaps),
        "facts": [fact_to_dict(item) for item in section.facts],
    }


def fact_to_dict(fact: UnderstandingFact) -> dict[str, Any]:
    return {
        "fact_id": fact.fact_id,
        "section_id": fact.section_id,
        "label": fact.label,
        "summary": fact.summary,
        "evidence_ids": list(fact.evidence_ids),
        "source_locators": list(fact.source_locators),
        "status": fact.status.value,
        "is_gap": fact.is_gap,
    }


def asset_to_dict(asset: Asset) -> dict[str, Any]:
    return {
        "asset_id": asset.asset_id,
        "product_id": asset.product_id,
        "source_id": asset.source_id,
        "relative_path": asset.relative_path,
        "asset_type": asset.asset_type,
        "role": asset.role,
        "file_hash": asset.file_hash,
        "processing_status": asset.processing_status.value,
    }
