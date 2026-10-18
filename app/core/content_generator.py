from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.core.creative_directions import CreativeDirectionsReport
from app.core.evidence_extractor import EvidenceExtractionReport
from app.core.hashing import sha256_bytes
from app.core.models import (
    Asset,
    ContentItem,
    Direction,
    Evidence,
    EvidenceStatus,
    ValidationLevel,
    ValidationResult,
)
from app.core.product_understanding import (
    ProductUnderstandingReport,
    SECTION_BRAND_MEMORY,
    SECTION_PRODUCT_FACTS,
)
from app.core.source_index import ID_SEPARATOR, output_is_outside_source

CONTENT_FILE_NAME = "content_package.json"
BLOCKED_OUTPUT_INSIDE_SOURCE = "blocked:output_inside_source_root"
CONTENT_ID_LENGTH = 16

CHANNEL_XIAOHONGSHU = "xiaohongshu"
CHANNEL_DOUYIN = "douyin"
CHANNEL_KUAISHOU = "kuaishou"
CHANNEL_SHOPPING = "shopping"
CHANNEL_VISUAL = "visual"
CHANNEL_VIDEO = "video"
COPY_CHANNELS = (
    CHANNEL_XIAOHONGSHU,
    CHANNEL_DOUYIN,
    CHANNEL_KUAISHOU,
    CHANNEL_SHOPPING,
)
CHANNELS = (*COPY_CHANNELS, CHANNEL_VISUAL, CHANNEL_VIDEO)


def content_id_of(product_id: str, direction_id: str, channel: str) -> str:
    joined = f"{product_id}{ID_SEPARATOR}{direction_id}{ID_SEPARATOR}{channel}"
    return "cnt_" + sha256_bytes(joined.encode("utf-8"))[:CONTENT_ID_LENGTH]


@dataclass(frozen=True)
class ContentGenerationReport:
    product_id: str
    product_name: str
    direction_id: str
    direction_name: str
    items: tuple[ContentItem, ...]
    validations: tuple[ValidationResult, ...] = field(default_factory=tuple)
    boundary_notice: str = "仅基于本地资料"
    generation_sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "direction_id": self.direction_id,
            "direction_name": self.direction_name,
            "boundary_notice": self.boundary_notice,
            "item_count": len(self.items),
            "items": [content_item_to_dict(item) for item in self.items],
            "validations": [validation_to_dict(item) for item in self.validations],
            "generation_sources": dict(self.generation_sources),
            "source": "mixed" if len(set(self.generation_sources.values())) > 1 else next(iter(self.generation_sources.values()), "template"),
        }

    def write_json(self, output_root: Path, library_root: Path) -> list[str]:
        if not output_is_outside_source(output_root, library_root):
            return [BLOCKED_OUTPUT_INSIDE_SOURCE]
        destination = output_root / self.product_id / CONTENT_FILE_NAME
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


def build_content_package(
    directions_report: CreativeDirectionsReport,
    understanding: ProductUnderstandingReport,
    extraction: EvidenceExtractionReport,
    *,
    selected_direction_id: str | None = None,
    ai_analysis: dict[str, Any] | None = None,
) -> ContentGenerationReport:
    direction = _select_direction(directions_report, selected_direction_id)
    assets_by_id = {asset.asset_id: asset for asset in understanding.assets}
    banned_words = _collect_banned_words(extraction.evidences)
    product_name = _display_product_name(understanding)
    brand = _fact_text(understanding, SECTION_PRODUCT_FACTS, "品牌") or product_name
    price = _fact_text(understanding, SECTION_PRODUCT_FACTS, "建议价格") or ""
    asset_paths = _asset_paths(assets_by_id, direction.asset_ids)
    product_evidence = _product_evidence_summary(understanding, direction)

    visual_hints = _visual_hints_from_analysis(ai_analysis)
    items = (
        _build_xiaohongshu(direction, product_name, brand, price, asset_paths, product_evidence),
        _build_douyin(direction, product_name, brand, asset_paths),
        _build_kuaishou(direction, product_name, brand, price, product_evidence),
        _build_shopping(direction, product_name, brand, price, product_evidence, asset_paths),
        _build_visual(direction, product_name, asset_paths, visual_hints, product_evidence),
        _build_video(direction, product_name, brand, asset_paths),
    )
    generation_sources = {item.channel: "template" for item in items}
    try:
        from app.core.llm_content_generator import enhance_content_with_llm

        items = enhance_content_with_llm(
            items,
            direction=direction,
            understanding=understanding,
            product_evidence=product_evidence,
            ai_analysis=ai_analysis,
            generation_sources=generation_sources,
        )
    except Exception:
        generation_sources = {item.channel: "template" for item in items}
    validated_items: list[ContentItem] = []
    validations: list[ValidationResult] = []
    evidence_by_id = {item.evidence_id: item for item in extraction.evidences}

    for item in items:
        level, results = validate_content_item(
            item,
            direction=direction,
            banned_words=banned_words,
            evidence_by_id=evidence_by_id,
        )
        validated_items.append(
            ContentItem(
                content_id=item.content_id,
                product_id=item.product_id,
                direction_id=item.direction_id,
                channel=item.channel,
                title=item.title,
                body=item.body,
                evidence_ids=item.evidence_ids,
                asset_ids=item.asset_ids,
                validation_level=level,
                version=item.version,
            )
        )
        validations.extend(results)

    return ContentGenerationReport(
        product_id=directions_report.product_id,
        product_name=product_name,
        direction_id=direction.direction_id,
        direction_name=direction.name,
        items=tuple(validated_items),
        validations=tuple(validations),
        generation_sources=generation_sources,
    )


def validate_content_item(
    item: ContentItem,
    *,
    direction: Direction,
    banned_words: frozenset[str],
    evidence_by_id: dict[str, Evidence],
) -> tuple[ValidationLevel, tuple[ValidationResult, ...]]:
    results: list[ValidationResult] = []
    corpus = _promotional_corpus(item)
    level = ValidationLevel.PASS

    for word in sorted(banned_words):
        if word and word in corpus:
            results.append(
                ValidationResult(
                    level=ValidationLevel.FAIL,
                    code="forbidden_word",
                    message=f"内容命中品牌禁用词：{word}",
                    details={"word": word, "channel": item.channel},
                )
            )
            level = ValidationLevel.FAIL

    outside_evidence = [
        evidence_id
        for evidence_id in item.evidence_ids
        if evidence_id not in direction.evidence_ids
    ]
    if outside_evidence:
        results.append(
            ValidationResult(
                level=ValidationLevel.FAIL,
                code="evidence_outside_direction",
                message="内容引用了当前方向白名单之外的证据。",
                details={"evidence_ids": outside_evidence, "channel": item.channel},
            )
        )
        level = ValidationLevel.FAIL

    outside_assets = [
        asset_id for asset_id in item.asset_ids if asset_id not in direction.asset_ids
    ]
    if outside_assets:
        results.append(
            ValidationResult(
                level=ValidationLevel.FAIL,
                code="asset_outside_direction",
                message="内容引用了当前方向白名单之外的素材。",
                details={"asset_ids": outside_assets, "channel": item.channel},
            )
        )
        level = ValidationLevel.FAIL

    pending = [
        evidence_id
        for evidence_id in item.evidence_ids
        if evidence_by_id.get(evidence_id) is not None
        and evidence_by_id[evidence_id].status is EvidenceStatus.PENDING
    ]
    if pending and level is not ValidationLevel.FAIL:
        results.append(
            ValidationResult(
                level=ValidationLevel.WARN,
                code="pending_evidence",
                message="部分内容依据仍为待确认状态。",
                details={"evidence_ids": pending, "channel": item.channel},
            )
        )
        level = ValidationLevel.WARN

    if direction.open_questions and level is not ValidationLevel.FAIL:
        results.append(
            ValidationResult(
                level=ValidationLevel.WARN,
                code="open_questions",
                message="方向仍有待验证资料，内容中已标注资料不足项。",
                details={
                    "open_questions": list(direction.open_questions),
                    "channel": item.channel,
                },
            )
        )
        if level is ValidationLevel.PASS:
            level = ValidationLevel.WARN

    if level is ValidationLevel.PASS and not results:
        results.append(
            ValidationResult(
                level=ValidationLevel.PASS,
                code="checks_passed",
                message="未发现客观阻断问题。",
                details={"channel": item.channel},
            )
        )
    return level, tuple(results)


def _build_xiaohongshu(
    direction: Direction,
    product_name: str,
    brand: str,
    price: str,
    asset_paths: tuple[str, ...],
    product_evidence: str,
) -> ContentItem:
    title = f"{direction.main_selling_point}｜{direction.name}"
    body_parts = [
        f"【仅基于本地资料】以下文案仅使用当前选定方向「{direction.name}」的证据与素材。",
        "",
        f"目标人群：{direction.audience}",
        f"核心表达：{direction.main_selling_point}",
        f"商品依据：{product_evidence}",
        "",
        "正文：",
        f"如果你也在「{' / '.join(direction.scenes)}」这些场景里切换，这条 {product_name} 可能更适合从“穿着感受”而不是“看起来如何”去判断。",
        f"{direction.platform_expression}",
        "",
        f"价格依据：{price}" if price else "价格：资料中未给出明确数字（资料不足）。",
        f"品牌：{brand}",
        "",
        "不建议重复：" + direction.avoid_expression,
    ]
    if direction.risks:
        body_parts.extend(["", "风险提示：", *[f"- {risk}" for risk in direction.risks]])
    if direction.open_questions:
        body_parts.extend(
            ["", "待验证项：", *[f"- {question}" for question in direction.open_questions]]
        )
    if asset_paths:
        body_parts.extend(["", "推荐本地素材顺序：", *[f"- {path}" for path in asset_paths]])
    body_parts.extend(["", "标签：", f"#{' #'.join(_hashtags(direction))}"])
    return ContentItem(
        content_id=content_id_of(direction.product_id, direction.direction_id, CHANNEL_XIAOHONGSHU),
        product_id=direction.product_id,
        direction_id=direction.direction_id,
        channel=CHANNEL_XIAOHONGSHU,
        title=title,
        body="\n".join(body_parts),
        evidence_ids=direction.evidence_ids,
        asset_ids=direction.asset_ids,
    )


def _build_douyin(
    direction: Direction,
    product_name: str,
    brand: str,
    asset_paths: tuple[str, ...],
) -> ContentItem:
    assets = asset_paths or ("待补拍素材",)
    shots = [
        ("0-3 秒", assets[0], "先抛出场景问题，不要上来就讲卖点。"),
        ("3-8 秒", assets[min(1, len(assets) - 1)], direction.main_selling_point),
        ("8-15 秒", assets[min(2, len(assets) - 1)], direction.platform_expression),
        ("15-20 秒", assets[-1], f"{brand} {product_name}。口播收束到穿着感受。"),
    ]
    lines = [
        "【仅基于本地资料】抖音短视频脚本",
        "时长：20 秒。画幅：9:16。",
        "",
    ]
    for slot, asset, subtitle in shots:
        lines.append(f"{slot}｜画面：{asset}；字幕/口播：{subtitle}")
    if direction.open_questions:
        lines.extend(["", "待补拍：", *[f"- {question}" for question in direction.open_questions]])
    lines.extend(["", "避免表达：" + direction.avoid_expression])
    return ContentItem(
        content_id=content_id_of(direction.product_id, direction.direction_id, CHANNEL_DOUYIN),
        product_id=direction.product_id,
        direction_id=direction.direction_id,
        channel=CHANNEL_DOUYIN,
        title=f"{direction.name} · 20 秒脚本",
        body="\n".join(lines),
        evidence_ids=direction.evidence_ids,
        asset_ids=direction.asset_ids,
    )


def _build_kuaishou(
    direction: Direction,
    product_name: str,
    brand: str,
    price: str,
    product_evidence: str,
) -> ContentItem:
    body = "\n".join(
        [
            "【仅基于本地资料】快手短视频/直播话术",
            "",
            f"老铁们，今天给你们看这款 {product_name}，实在话不忽悠。",
            f"核心卖点：{direction.main_selling_point}",
            f"适合：{direction.audience}",
            f"商品依据：{product_evidence}",
            f"价格：{price}" if price else "价格：资料未标注（资料不足）",
            "",
            f"场景：{' / '.join(direction.scenes)}",
            "互动：觉得实用的点个关注，评论区说说你最关心啥。",
            "",
            "避免表达：" + direction.avoid_expression,
            f"品牌：{brand}",
        ]
    )
    return ContentItem(
        content_id=content_id_of(direction.product_id, direction.direction_id, CHANNEL_KUAISHOU),
        product_id=direction.product_id,
        direction_id=direction.direction_id,
        channel=CHANNEL_KUAISHOU,
        title=f"{direction.name} · 快手话术",
        body=body,
        evidence_ids=direction.evidence_ids,
        asset_ids=direction.asset_ids,
    )


def _build_shopping(
    direction: Direction,
    product_name: str,
    brand: str,
    price: str,
    product_evidence: str,
    asset_paths: tuple[str, ...],
) -> ContentItem:
    lines = [
        "【仅基于本地资料】购物平台详情文案",
        "",
        f"标题：{product_name} | {direction.main_selling_point}",
        "",
        "核心卖点：",
        f"1. {direction.main_selling_point}",
        f"2. 适用人群：{direction.audience}",
        f"3. 商品依据：{product_evidence}",
        "",
        "规格与参数：",
        f"- 品牌：{brand}",
        f"- 价格：{price}" if price else "- 价格：待补充（资料不足）",
        "",
        "适用场景：",
        *[f"- {scene}" for scene in direction.scenes],
        "",
        "详情页建议配图顺序：",
    ]
    if asset_paths:
        lines.extend(f"- {path}" for path in asset_paths[:5])
    else:
        lines.append("- 待补拍主图与细节图（资料不足）")
    lines.extend(["", "避免表达：" + direction.avoid_expression])
    return ContentItem(
        content_id=content_id_of(direction.product_id, direction.direction_id, CHANNEL_SHOPPING),
        product_id=direction.product_id,
        direction_id=direction.direction_id,
        channel=CHANNEL_SHOPPING,
        title=f"{product_name} · 购物平台详情",
        body="\n".join(lines),
        evidence_ids=direction.evidence_ids,
        asset_ids=direction.asset_ids,
    )


def _build_video(
    direction: Direction,
    product_name: str,
    brand: str,
    asset_paths: tuple[str, ...],
) -> ContentItem:
    assets = asset_paths or ("待补拍素材",)
    shots = [
        ("0-3 秒", assets[0], "钩子：抛出用户痛点或场景问题"),
        ("3-8 秒", assets[min(1, len(assets) - 1)], direction.main_selling_point),
        ("8-15 秒", assets[min(2, len(assets) - 1)], direction.platform_expression),
        ("15-20 秒", assets[-1], f"{brand} {product_name}，引导点击/下单"),
    ]
    lines = [
        "【仅基于本地资料】20 秒竖屏视频脚本",
        "画幅：9:16",
        "",
    ]
    for slot, asset, subtitle in shots:
        lines.append(f"{slot}｜画面：{asset}；口播：{subtitle}")
    lines.extend(["", "避免表达：" + direction.avoid_expression])
    return ContentItem(
        content_id=content_id_of(direction.product_id, direction.direction_id, CHANNEL_VIDEO),
        product_id=direction.product_id,
        direction_id=direction.direction_id,
        channel=CHANNEL_VIDEO,
        title=f"{direction.name} · 视频脚本",
        body="\n".join(lines),
        evidence_ids=direction.evidence_ids,
        asset_ids=direction.asset_ids,
    )


def _build_visual(
    direction: Direction,
    product_name: str,
    asset_paths: tuple[str, ...],
    visual_hints: str,
    product_evidence: str,
) -> ContentItem:
    lines = [
        "【仅基于本地资料 · 本地模板】视觉概念方案",
        f"商品：{product_name}",
        f"方向：{direction.name}",
        f"当前分析视觉要点（请核对来源）：{visual_hints}",
        f"商品依据：{product_evidence}",
        "画幅：9:16 竖屏优先，辅 3:4 图文。",
        "",
        "镜头顺序：",
    ]
    for index, path in enumerate(asset_paths, start=1):
        lines.append(f"{index}. {path} —— 对应 {direction.scenes[min(index - 1, len(direction.scenes) - 1)]}")
    if not asset_paths:
        lines.append("1. 待补拍 —— 当前方向缺少可用本地素材（资料不足）。")
    lines.extend(
        [
            "",
            "字幕主线：" + direction.main_selling_point,
            "口播主线：" + direction.platform_expression,
        ]
    )
    lines.extend(["", "需要补拍/待验证："])
    if direction.open_questions:
        lines.extend(f"- {question}" for question in direction.open_questions)
    else:
        lines.append("- 无")
    lines.extend(["", "避免表达：" + direction.avoid_expression])
    return ContentItem(
        content_id=content_id_of(direction.product_id, direction.direction_id, CHANNEL_VISUAL),
        product_id=direction.product_id,
        direction_id=direction.direction_id,
        channel=CHANNEL_VISUAL,
        title=f"{direction.name} · 视觉方案",
        body="\n".join(lines),
        evidence_ids=direction.evidence_ids,
        asset_ids=direction.asset_ids,
    )


def _promotional_corpus(item: ContentItem) -> str:
    parts = [item.title]
    for line in item.body.splitlines():
        if line.startswith(("避免表达：", "不建议重复：")):
            continue
        if line.startswith("- ") and any(
            marker in line for marker in ("不讲", "不要", "不用", "避免", "风险", "禁用")
        ):
            continue
        parts.append(line)
    return "\n".join(parts)


def _select_direction(
    report: CreativeDirectionsReport,
    selected_direction_id: str | None,
) -> Direction:
    if selected_direction_id is not None:
        for direction in report.directions:
            if direction.direction_id == selected_direction_id:
                return direction
        raise ValueError(f"unknown_direction_id:{selected_direction_id}")
    if not report.directions:
        raise ValueError("no_directions_available")
    return report.directions[0]


def _collect_banned_words(evidences: Iterable[Evidence]) -> frozenset[str]:
    words: set[str] = set()
    for item in evidences:
        if item.field_id in {
            "excel.商品参数.品牌禁用词",
            "text.禁用词",
        } or item.field_id.endswith(".禁用词.内容"):
            words.update(_split_terms(str(item.value)))
    return frozenset(word for word in words if len(word) >= 2)


def _split_terms(value: str) -> set[str]:
    parts = re.split(r"[\s、，,/|；;]+", value.strip())
    return {part.strip() for part in parts if part.strip()}


def _fact_text(
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


def _display_product_name(understanding: ProductUnderstandingReport) -> str:
    brand = _fact_text(understanding, SECTION_PRODUCT_FACTS, "品牌")
    name = understanding.product_name
    if brand and brand not in name:
        return f"{brand} {name}"
    return name


def _product_evidence_summary(
    understanding: ProductUnderstandingReport,
    direction: Direction,
) -> str:
    if "腰" in direction.name:
        labels = ("腰部活动余量", "版型")
    elif "裤长" in direction.name or "改裤脚" in direction.name:
        labels = ("裤长", "版型")
    elif "咖啡馆" in direction.name or "会议" in direction.name:
        labels = ("主要场景", "材质")
    elif "秩序" in direction.name or "早八" in direction.name:
        labels = ("收纳结构", "尺寸")
    elif "轻一点" in direction.name or "余裕" in direction.name:
        labels = ("重量", "材质")
    elif "下班" in direction.name or "续航" in direction.name:
        labels = ("主要场景", "尺寸")
    else:
        labels = ("材质", "主要场景")
    parts = [
        _fact_text(understanding, SECTION_PRODUCT_FACTS, label)
        for label in labels
    ]
    summary = "；".join(part for part in parts if part)
    return summary or direction.main_selling_point


def _asset_paths(assets_by_id: dict[str, Asset], asset_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        assets_by_id[asset_id].relative_path
        for asset_id in asset_ids
        if asset_id in assets_by_id
    )


def _visual_hints_from_analysis(ai_analysis: dict[str, Any] | None) -> str:
    if not ai_analysis:
        return "待 AI 分析补充颜色、材质与风格关键词"
    parts: list[str] = []
    facts = ai_analysis.get("product_facts") or {}
    if isinstance(facts, dict):
        for key in ("颜色", "材质", "造型风格", "工艺细节"):
            value = str(facts.get(key) or "").strip()
            if value:
                parts.append(f"{key}：{value}")
    keywords = ai_analysis.get("visual_keywords") or []
    if isinstance(keywords, list) and keywords:
        parts.append("视觉关键词：" + "、".join(str(k) for k in keywords[:6]))
    tone = str(ai_analysis.get("brand_tone") or "").strip()
    if tone:
        parts.append(f"品牌调性：{tone}")
    positioning = str(ai_analysis.get("category_positioning") or "").strip()
    if positioning:
        parts.append(f"品类定位：{positioning}")
    return "；".join(parts) if parts else "待 AI 分析补充视觉要点"


def _hashtags(direction: Direction) -> tuple[str, ...]:
    tags = [direction.name.replace(" ", "")]
    tags.extend(scene.replace(" ", "") for scene in direction.scenes[:3])
    return tuple(dict.fromkeys(tag for tag in tags if tag))


def content_item_to_dict(item: ContentItem) -> dict[str, Any]:
    return {
        "content_id": item.content_id,
        "product_id": item.product_id,
        "direction_id": item.direction_id,
        "channel": item.channel,
        "title": item.title,
        "body": item.body,
        "evidence_ids": list(item.evidence_ids),
        "asset_ids": list(item.asset_ids),
        "validation_level": item.validation_level.value,
        "version": item.version,
    }


def validation_to_dict(result: ValidationResult) -> dict[str, Any]:
    return {
        "level": result.level.value,
        "code": result.code,
        "message": result.message,
        "details": result.details,
    }
