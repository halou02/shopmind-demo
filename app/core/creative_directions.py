from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.core.business_judgment import BusinessJudgmentReport
from app.core.evidence_extractor import EvidenceExtractionReport
from app.core.hashing import sha256_bytes
from app.core.models import Asset, Direction, Evidence
from app.core.product_understanding import (
    ProductUnderstandingReport,
    SECTION_PRODUCT_FACTS,
)
from app.core.source_index import ID_SEPARATOR, output_is_outside_source

DIRECTIONS_FILE_NAME = "creative_directions.json"
BLOCKED_OUTPUT_INSIDE_SOURCE = "blocked:output_inside_source_root"
DIRECTION_ID_LENGTH = 12


@dataclass(frozen=True)
class DirectionBlueprint:
    name: str
    audience: str
    scenes: tuple[str, ...]
    main_selling_point: str
    platform_expression: str
    product_field_ids: tuple[str, ...]
    review_keywords: tuple[str, ...]
    extra_field_prefixes: tuple[str, ...]
    asset_roles: tuple[str, ...]
    asset_path_keywords: tuple[str, ...]
    risks: tuple[str, ...]
    open_questions: tuple[str, ...]
    avoid_expression: str


JEANS_DIRECTIONS: tuple[DirectionBlueprint, ...] = (
    DirectionBlueprint(
        name="坐一整天 腰里也有余量",
        audience="工作日需要久坐、经常从工位直接去见朋友的女性。",
        scenes=("办公室久坐", "起身走动", "工位到会面"),
        main_selling_point="高腰有边界，坐下有余量。",
        platform_expression="小红书用前后对比展示坐下后的腰头状态；抖音前 3 秒先拍“坐下后”的真实感受。",
        product_field_ids=("excel.商品参数.腰部活动余量", "excel.商品参数.版型"),
        review_keywords=("腰", "勒", "坐", "起身", "支撑"),
        extra_field_prefixes=("excel.品牌记忆.禁用词.", "text.历史表达提醒"),
        asset_roles=("detail", "scene"),
        asset_path_keywords=("腰部", "咖啡馆", "久坐"),
        risks=("历史内容高频使用“显瘦”，本方向不应回到身材焦虑叙事。", "J12 提示初穿面料偏挺，需要坐下实拍支撑。"),
        open_questions=("缺少不同身高与三档裤长的上身对照（资料不足）。",),
        avoid_expression="不讲“显瘦神裤”，不把穿衣体验变成身材焦虑。",
    ),
    DirectionBlueprint(
        name="从会议室到咖啡馆 不用换掉自己",
        audience="希望通勤着装有分寸、下班后仍想保留个人状态的城市女性。",
        scenes=("通勤", "会议", "咖啡馆", "下班切换"),
        main_selling_point="一条裤，接住工作和自己的时间。",
        platform_expression="用中靛蓝在同一天内的两个场景切换做叙事，不用“百搭”一笔带过。",
        product_field_ids=("excel.商品参数.主要场景", "excel.商品参数.材质"),
        review_keywords=("中靛蓝", "通勤", "上班", "下班", "咖啡馆", "正式"),
        extra_field_prefixes=("excel.历史内容.", "text.结论"),
        asset_roles=("scene", "on_body"),
        asset_path_keywords=("咖啡馆", "上身", "通勤"),
        risks=("“百搭”在历史内容中出现 3 次，本方向需展示具体场景而非空泛万能。",),
        open_questions=("缺少从办公室到咖啡馆的连续动线视频（资料不足）。",),
        avoid_expression="不要只说“百搭”，要展示从工作到生活的具体变化。",
    ),
    DirectionBlueprint(
        name="三档裤长减少改裤脚负担",
        audience="不同身高、担心裤脚堆积或频繁改裤脚的用户。",
        scenes=("选裤长", "试穿上身", "城市步行"),
        main_selling_point="98 / 102 / 106 cm 三档裤长，减少改裤脚麻烦。",
        platform_expression="先讲三档裤长差异，再展示不同身高上身效果；没有完整身高表时标注待验证。",
        product_field_ids=("excel.商品参数.裤长", "excel.商品参数.版型"),
        review_keywords=("裤长", "裤脚", "身高", "改裤脚", "堆积"),
        extra_field_prefixes=("excel.用户评价.",),
        asset_roles=("on_body", "main_image"),
        asset_path_keywords=("上身", "平铺", "正面上身"),
        risks=("J11/J13/J14 提示尺码、身高与裤长参考不足。", "历史内容高频“小个子必入”，避免过度承诺。"),
        open_questions=(
            "缺少完整腰围/臀围/身高选择表（资料不足）。",
            "缺少不同身高与三档裤长的上身参考（资料不足）。",
        ),
        avoid_expression="不用“小个子必入”和“腿长滤镜”等过度承诺。",
    ),
)

BAG_DIRECTIONS: tuple[DirectionBlueprint, ...] = (
    DirectionBlueprint(
        name="早八不慌的一包秩序",
        audience="每天带电脑、通勤距离长的办公室新人和白领。",
        scenes=("早八出门", "地铁通勤", "装包整理"),
        main_selling_point="把今天要用的东西，装得一目了然。",
        platform_expression="抖音前 5 秒展示电脑、水杯、伞各归其位；小红书强调分区与拉链。",
        product_field_ids=("excel.商品参数.收纳结构", "excel.商品参数.尺寸"),
        review_keywords=("分区", "拉链", "收纳", "电脑", "水杯", "整齐"),
        extra_field_prefixes=("excel.品牌记忆.禁用词.",),
        asset_roles=("detail",),
        asset_path_keywords=("容量", "细节"),
        risks=("历史内容高频“大容量”“通勤必备”，不宜再做空泛标题。",),
        open_questions=("缺少装包过程与地铁场景视频（资料不足）。",),
        avoid_expression="不要用“通勤必备”“大容量”做标题。",
    ),
    DirectionBlueprint(
        name="轻一点上班 留一点余裕",
        audience="讨厌负担感、重视穿搭比例和轻松状态的城市女性。",
        scenes=("通勤肩背", "步行换乘", "轻量出门"),
        main_selling_point="装下工作，也不把肩膀交给负担。",
        platform_expression="先展示 380 g 重量感受，再展示装电脑后的上身比例。",
        product_field_ids=("excel.商品参数.重量", "excel.商品参数.材质"),
        review_keywords=("轻", "压肩", "不重", "负担"),
        extra_field_prefixes=("excel.商品参数.尺寸.",),
        asset_roles=("scene", "main_image"),
        asset_path_keywords=("通勤", "主图", "场景"),
        risks=("装物过多时包型略鼓的风险需在内容中如实提示。",),
        open_questions=("缺少厚外套装入后的肩带稳定性实拍（资料不足）。",),
        avoid_expression="不使用“显瘦神器”“天花板”等夸张说法。",
    ),
    DirectionBlueprint(
        name="下班不换包的城市续航",
        audience="下班后会去咖啡馆、上课或见朋友的通勤女性。",
        scenes=("下班离开办公室", "咖啡馆", "城市晚间步行"),
        main_selling_point="从工位到咖啡馆，包里还是今天的节奏。",
        platform_expression="用“下班不换包”替代“百搭”，展示同一包跨场景使用。",
        product_field_ids=("excel.商品参数.主要场景", "excel.商品参数.尺寸"),
        review_keywords=("咖啡馆", "下班", "切换", "不必换包", "约会"),
        extra_field_prefixes=("text.结论", "excel.历史内容."),
        asset_roles=("scene",),
        asset_path_keywords=("咖啡馆", "场景"),
        risks=("“百搭”在历史内容中出现多次，本方向需避免回到旧表达。", "浅色面料耐脏顾虑需在内容中保守表述。"),
        open_questions=("缺少浅色面料耐脏测试记录（资料不足）。",),
        avoid_expression="不用“百搭”概括，改为具体展示“下班不换包”的过程。",
    ),
)


def direction_id_of(product_id: str, name: str) -> str:
    joined = f"{product_id}{ID_SEPARATOR}{name}"
    return "dir_" + sha256_bytes(joined.encode("utf-8"))[:DIRECTION_ID_LENGTH]


@dataclass(frozen=True)
class CreativeDirectionsReport:
    product_id: str
    product_name: str
    judgment_id: str
    directions: tuple[Direction, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "judgment_id": self.judgment_id,
            "direction_count": len(self.directions),
            "directions": [direction_to_dict(item) for item in self.directions],
        }

    def write_json(self, output_root: Path, library_root: Path) -> list[str]:
        if not output_is_outside_source(output_root, library_root):
            return [BLOCKED_OUTPUT_INSIDE_SOURCE]
        destination = output_root / self.product_id / DIRECTIONS_FILE_NAME
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


def build_creative_directions(
    judgment_report: BusinessJudgmentReport,
    understanding: ProductUnderstandingReport,
    extraction: EvidenceExtractionReport,
) -> CreativeDirectionsReport:
    blueprints = (
        JEANS_DIRECTIONS
        if _is_jeans_product(understanding)
        else BAG_DIRECTIONS
    )
    directions = tuple(
        _build_direction(
            blueprint,
            product_id=understanding.product_id,
            assets=understanding.assets,
            evidences=extraction.evidences,
            judgment_report=judgment_report,
        )
        for blueprint in blueprints
    )
    return CreativeDirectionsReport(
        product_id=understanding.product_id,
        product_name=understanding.product_name,
        judgment_id=judgment_report.judgment.judgment_id,
        directions=directions,
    )


def evidence_kinds_for_direction(
    direction: Direction,
    evidences: Iterable[Evidence],
) -> set[str]:
    evidence_by_id = {item.evidence_id: item for item in evidences}
    kinds: set[str] = set()
    for evidence_id in direction.evidence_ids:
        item = evidence_by_id.get(evidence_id)
        if item is None:
            continue
        kinds.add(_evidence_kind(item))
    return kinds


def _build_direction(
    blueprint: DirectionBlueprint,
    *,
    product_id: str,
    assets: tuple[Asset, ...],
    evidences: tuple[Evidence, ...],
    judgment_report: BusinessJudgmentReport,
) -> Direction:
    evidence_ids = _collect_evidence_ids(blueprint, evidences)
    asset_ids = _collect_asset_ids(blueprint, assets)
    open_questions = _merge_open_questions(
        blueprint.open_questions,
        judgment_report.judgment.open_questions,
    )
    return Direction(
        direction_id=direction_id_of(product_id, blueprint.name),
        product_id=product_id,
        name=blueprint.name,
        audience=blueprint.audience,
        scenes=blueprint.scenes,
        main_selling_point=blueprint.main_selling_point,
        platform_expression=blueprint.platform_expression,
        evidence_ids=evidence_ids,
        asset_ids=asset_ids,
        risks=blueprint.risks,
        open_questions=open_questions,
        avoid_expression=blueprint.avoid_expression,
    )


def _collect_evidence_ids(
    blueprint: DirectionBlueprint,
    evidences: Iterable[Evidence],
) -> tuple[str, ...]:
    collected: list[str] = []
    for field_id in blueprint.product_field_ids:
        collected.extend(item.evidence_id for item in evidences if item.field_id == field_id)
    for prefix in blueprint.extra_field_prefixes:
        collected.extend(
            item.evidence_id
            for item in evidences
            if item.field_id.startswith(prefix)
        )
    for item in evidences:
        if not item.field_id.startswith("excel.用户评价."):
            continue
        if not item.field_id.endswith(".评价文本"):
            continue
        haystack = f"{item.value} {_review_tags(evidences, item.field_id)}"
        if any(keyword in haystack for keyword in blueprint.review_keywords):
            collected.append(item.evidence_id)
            review_id = item.field_id.split(".")[2]
            collected.extend(
                other.evidence_id
                for other in evidences
                if other.field_id.startswith(f"excel.用户评价.{review_id}.")
            )
    return tuple(dict.fromkeys(item for item in collected if item))


def _review_tags(evidences: Iterable[Evidence], review_text_field_id: str) -> str:
    review_id = review_text_field_id.split(".")[2]
    tags = [
        str(item.value)
        for item in evidences
        if item.field_id == f"excel.用户评价.{review_id}.标签"
    ]
    return " ".join(tags)


def _collect_asset_ids(
    blueprint: DirectionBlueprint,
    assets: Iterable[Asset],
) -> tuple[str, ...]:
    selected: list[str] = []
    for asset in assets:
        role_match = not blueprint.asset_roles or asset.role in blueprint.asset_roles
        path_match = not blueprint.asset_path_keywords or any(
            keyword in asset.relative_path for keyword in blueprint.asset_path_keywords
        )
        if role_match or path_match:
            selected.append(asset.asset_id)
    return tuple(dict.fromkeys(selected))


def _merge_open_questions(
    blueprint_questions: tuple[str, ...],
    judgment_questions: tuple[str, ...],
) -> tuple[str, ...]:
    merged = list(blueprint_questions)
    for question in judgment_questions:
        if question not in merged:
            merged.append(question)
    return tuple(merged)


def _is_jeans_product(understanding: ProductUnderstandingReport) -> bool:
    if "牛仔裤" in understanding.product_name or "裤" in understanding.product_name:
        return True
    return any(
        fact.label in {"裤长", "腰部活动余量"}
        for fact in understanding.facts_for_section(SECTION_PRODUCT_FACTS)
    )


def _evidence_kind(evidence: Evidence) -> str:
    if evidence.field_id.startswith("excel.商品参数."):
        return "product_data"
    if evidence.field_id.startswith("excel.用户评价."):
        return "review"
    if evidence.field_id.startswith("excel.历史内容."):
        return "history"
    if evidence.field_id.startswith("excel.品牌记忆."):
        return "brand_rule"
    if evidence.field_id.startswith("text."):
        return "text"
    if evidence.field_id.startswith("md."):
        return "markdown"
    return "other"


def direction_to_dict(direction: Direction) -> dict[str, Any]:
    return {
        "direction_id": direction.direction_id,
        "product_id": direction.product_id,
        "name": direction.name,
        "audience": direction.audience,
        "scenes": list(direction.scenes),
        "main_selling_point": direction.main_selling_point,
        "platform_expression": direction.platform_expression,
        "evidence_ids": list(direction.evidence_ids),
        "asset_ids": list(direction.asset_ids),
        "risks": list(direction.risks),
        "open_questions": list(direction.open_questions),
        "avoid_expression": direction.avoid_expression,
    }
