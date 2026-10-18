from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.core.capability_config import (
    CapabilityConfigError,
    resolve_vision_request,
)
from app.core.evidence_extractor import EvidenceExtractionReport
from app.core.llm_client import LLMClientError, build_vision_messages, chat_completion_json
from app.core.llm_config import LLMConfig, LocalVisionConfig, load_llm_config
from app.core.local_vision_client import (
    local_chat_completion_json,
    select_first_valid_image,
)
from app.core.models import Evidence, EvidenceStatus, SourceKind
from app.core.product_understanding import (
    SECTION_BRAND_MEMORY,
    SECTION_DATA_GAPS,
    SECTION_PRODUCT_FACTS,
    SECTION_REVIEW_INSIGHTS,
    ProductUnderstandingReport,
    UnderstandingFact,
    UnderstandingSection,
    fact_id_of,
)
from app.core.source_index import SourceIndex

IMAGE_FOLDER_HINTS = ("商品图片", "图片", "images", "product_images")
REFERENCE_FOLDER_HINTS = ("参考资料", "reference", "docs")
TEXT_FILE_SUFFIXES = {".txt", ".md", ".csv", ".json"}
MAX_TEXT_MATERIAL_CHARS = 12000
PLACEHOLDER_REVIEW_MARKERS = (
    "待 ai 结合图像理解商品",
    "图片素材已上传",
    "演示数据",
    "待补充",
)

SYSTEM_PROMPT = """你是电商商品创意分析师，擅长从商品图片和商家描述中提取可落地的商品理解。

任务：根据用户上传的商品图片、填写的商品信息，以及可选的本地 Excel/文本资料，输出结构化 JSON。

要求：
1. 仔细观察图片中的颜色、材质质感、造型轮廓、五金/缝线/分区等可见细节
2. 结合用户填写的类别、价格、描述、品牌，给出具体、真实的分析，避免空泛套话
3. 图片中看不清或无法确认的内容，在对应字段写「未能从图片确认」，不要编造
4. 若用户评价是占位/demo 数据，应基于图片与商品信息推断潜在卖点与用户顾虑
5. 输出必须是合法 JSON 对象，字段名与结构必须与用户消息中的 schema 完全一致
6. 所有文本使用简体中文"""

# SYSTEM_PROMPT 要求 3 里那个占位原词，逐字对应，只保护这一个字面量。
# 本批不建模糊同义词表，也不猜「无法确认/看不清」这类变体：判定面只认提示词自己写的话。
UNCONFIRMED_PLACEHOLDER = "未能从图片确认"


def _analysis_schema_hint() -> str:
    return json.dumps(
        {
            "product_name": "结合图片与表单推断的具体商品名",
            "category_positioning": "一句话品类定位，说明这是什么、卖给谁、解决什么问题",
            "product_facts": {
                "材质": "从图片/描述识别的材质",
                "颜色": "主色与辅色",
                "造型风格": "轮廓、设计语言",
                "尺寸规格": "可见或推断的尺寸信息，无法确认则说明",
                "容量/规格": "容量、尺码或规格要点",
                "工艺细节": "五金、缝线、分区、功能结构等可见细节",
            },
            "selling_points": ["核心卖点1", "核心卖点2", "核心卖点3"],
            "target_audience": "目标人群画像，要具体",
            "usage_scenarios": ["场景1", "场景2"],
            "user_likes": ["用户可能喜欢的点1", "用户可能喜欢的点2"],
            "user_concerns": ["潜在顾虑1", "潜在顾虑2"],
            "content_opportunities": "内容创作可强调的差异化表达机会",
            "brand_tone": "建议的品牌语气/调性",
            "visual_keywords": ["视觉关键词1", "视觉关键词2"],
            "core_opportunity": "经营层面的核心机会判断，一句话",
            "data_gaps": ["还缺少的资料或拍摄角度"],
        },
        ensure_ascii=False,
        indent=2,
    )


@dataclass
class AIProductAnalysis:
    product_name: str = ""
    category_positioning: str = ""
    product_facts: dict[str, str] = field(default_factory=dict)
    selling_points: tuple[str, ...] = ()
    target_audience: str = ""
    usage_scenarios: tuple[str, ...] = ()
    user_likes: tuple[str, ...] = ()
    user_concerns: tuple[str, ...] = ()
    content_opportunities: str = ""
    brand_tone: str = ""
    visual_keywords: tuple[str, ...] = ()
    core_opportunity: str = ""
    data_gaps: tuple[str, ...] = ()
    source: str = "offline"
    model: str = ""
    fallback_reason: str = ""
    # 两个最小分析元数据字段：执行位置，以及本次实际送进模型的那张图的相对文件名。
    # 位置取值 local / cloud / unknown；unknown 表示这一步证明不了自己跑在哪里。
    execution_location: str = "unknown"
    analyzed_image: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, source: str = "llm", model: str = "",
                  execution_location: str = "unknown",
                  analyzed_image: str = "") -> AIProductAnalysis:
        product_facts = payload.get("product_facts") or {}
        if not isinstance(product_facts, dict):
            product_facts = {}

        return cls(
            product_name=str(payload.get("product_name") or "").strip(),
            category_positioning=str(payload.get("category_positioning") or "").strip(),
            product_facts={
                str(key).strip(): str(value).strip()
                for key, value in product_facts.items()
                if str(key).strip() and str(value).strip()
            },
            selling_points=_as_str_tuple(payload.get("selling_points")),
            target_audience=str(payload.get("target_audience") or "").strip(),
            usage_scenarios=_as_str_tuple(payload.get("usage_scenarios")),
            user_likes=_as_str_tuple(payload.get("user_likes")),
            user_concerns=_as_str_tuple(payload.get("user_concerns")),
            content_opportunities=str(payload.get("content_opportunities") or "").strip(),
            brand_tone=str(payload.get("brand_tone") or "").strip(),
            visual_keywords=_as_str_tuple(payload.get("visual_keywords")),
            core_opportunity=str(payload.get("core_opportunity") or "").strip(),
            data_gaps=_as_str_tuple(payload.get("data_gaps")),
            source=source,
            model=model,
            execution_location=execution_location,
            analyzed_image=analyzed_image,
        )

    def has_content(self) -> bool:
        return bool(
            self.category_positioning
            or self.product_facts
            or self.selling_points
            or self.user_likes
            or self.user_concerns
            or self.content_opportunities
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_name": self.product_name,
            "category_positioning": self.category_positioning,
            "product_facts": dict(self.product_facts),
            "selling_points": list(self.selling_points),
            "target_audience": self.target_audience,
            "usage_scenarios": list(self.usage_scenarios),
            "user_likes": list(self.user_likes),
            "user_concerns": list(self.user_concerns),
            "content_opportunities": self.content_opportunities,
            "brand_tone": self.brand_tone,
            "visual_keywords": list(self.visual_keywords),
            "core_opportunity": self.core_opportunity,
            "data_gaps": list(self.data_gaps),
            "source": self.source,
            "model": self.model,
            "fallback_reason": self.fallback_reason,
            "execution_location": self.execution_location,
            "analyzed_image": self.analyzed_image,
        }


# 本机专属的降级文案：不复用下面那三句云端话术，也不冒充云端结果。
_LOCAL_NEEDS_IMAGE = "本机图片理解需要至少 1 张可用图片，本次未发送模型请求"
_LOCAL_NO_VALID_IMAGE = "本机图片理解没有找到可读且可解码的图片，本次未发送模型请求"
_LOCAL_ANALYSIS_FAILED = "本机图片理解未成功或结果不合格，已降级到本地规则"
# 未实测的本机上下文候选值：只约束本机分支，不改动云端 12000 字的资料上限。
LOCAL_PROMPT_TEXT_LIMIT = 4000
_LOCAL_TEXT_TRUNCATED = "\n（本机上下文容量有限，本地资料到此处截断，后面的内容未发送。）"


def analyze_product(
    *,
    material_root: Path,
    index: SourceIndex,
    product_form: dict[str, Any] | None = None,
    extraction: EvidenceExtractionReport | None = None,
    config: LLMConfig | LocalVisionConfig | None = None,
) -> AIProductAnalysis:
    cfg: LLMConfig | LocalVisionConfig | None = config
    declared_location = "unknown"
    stop_reason = ""
    if cfg is None:
        try:
            # 传函数本身而不是它的返回值：v2 vision 文件存在时这条链路根本不触发 v1 加载。
            cfg, declared_location = resolve_vision_request(load_llm_config)
        except CapabilityConfigError as error:
            stop_reason = str(error)
    image_paths = collect_product_image_paths(material_root, index)
    text_materials = read_text_materials(material_root)
    evidence_summary = summarize_local_evidence(extraction.evidences if extraction else ())
    if text_materials:
        evidence_summary = "\n\n".join(part for part in (evidence_summary, text_materials) if part)
    form = product_form or {}

    # isinstance 而不是鸭子类型：调用方传入的 Mock 恰好也有 base_url/model 时，不能被当成本机分支。
    is_local = isinstance(cfg, LocalVisionConfig)
    use_cloud = not is_local and cfg is not None and cfg.enabled
    local_reason = ""
    # 互斥而不是顺序：一次商品理解最多尝试一次模型请求，图片请求失败后直接走离线，不追加文字请求。
    if is_local:
        # 本机这条支永远不进云端：结构上就接不到下面两个 elif，也不存在本地失败转云端的通道。
        if not image_paths:
            local_reason = _LOCAL_NEEDS_IMAGE
        else:
            # 发送前先确认「可读且可解码」，靠过滤而不是靠发请求去试探：一张都不合格时模型请求数为 0。
            picked = select_first_valid_image(image_paths)
            if picked is None:
                local_reason = _LOCAL_NO_VALID_IMAGE
            else:
                try:
                    return _analyze_with_local_vision(
                        material_root=material_root,
                        image_path=picked[0],
                        image_base64=picked[1],
                        product_form=form,
                        evidence_summary=evidence_summary,
                        config=cfg,
                    )
                except LLMClientError:
                    local_reason = _LOCAL_ANALYSIS_FAILED

    elif use_cloud and image_paths:
        try:
            analysis = _analyze_with_vision(
                image_paths=image_paths,
                product_form=form,
                evidence_summary=evidence_summary,
                config=cfg,
            )
            analysis.execution_location = declared_location
            return analysis
        except LLMClientError:
            pass

    elif use_cloud and (text_materials or evidence_summary):
        try:
            analysis = _analyze_with_text(
                product_form=form,
                evidence_summary=evidence_summary,
                text_materials=text_materials,
                image_count=len(image_paths),
                config=cfg,
            )
            analysis.execution_location = declared_location
            return analysis
        except LLMClientError:
            pass

    analysis = build_offline_analysis(
        product_form=form,
        image_paths=image_paths,
        evidence_summary=evidence_summary,
        text_materials=text_materials,
    )
    if is_local:
        # 离线结果仍标本机：这一支压根没有云端请求，标注成 cloud 或留空都会误导页面上的位置分档。
        analysis.execution_location = "local"
    from app.core.llm_config import CLOUD_AUTHORIZED
    if stop_reason:
        analysis.fallback_reason = stop_reason
    elif local_reason:
        analysis.fallback_reason = local_reason
    else:
        analysis.fallback_reason = (
            "本次未授权云端发送，使用本地规则" if CLOUD_AUTHORIZED.get() is False
            else "缺少 API Key，使用本地规则" if not use_cloud
            else "云端分析未成功或资料不足，已降级到本地规则"
        )
    return analysis


def collect_product_image_paths(material_root: Path, index: SourceIndex) -> list[Path]:
    candidates: list[tuple[int, Path]] = []
    for source in index.sources:
        if source.source_kind is not SourceKind.IMAGE:
            continue
        path = material_root / source.relative_path
        if not path.is_file():
            continue
        priority = 0
        haystack = source.relative_path.replace("\\", "/")
        for hint in IMAGE_FOLDER_HINTS:
            if hint in haystack:
                priority += 10
        candidates.append((priority, path))

    candidates.sort(key=lambda item: (-item[0], item[1].name))
    return [path for _, path in candidates]


def read_text_materials(material_root: Path, *, max_chars: int = MAX_TEXT_MATERIAL_CHARS) -> str:
    if not material_root.is_dir():
        return ""

    candidates: list[tuple[int, Path]] = []
    for path in material_root.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in TEXT_FILE_SUFFIXES:
            continue
        priority = 0
        haystack = path.relative_to(material_root).as_posix()
        for hint in REFERENCE_FOLDER_HINTS:
            if hint in haystack:
                priority += 10
        candidates.append((priority, path))

    candidates.sort(key=lambda item: (-item[0], item[1].name))
    chunks: list[str] = []
    total = 0
    for _, path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            continue
        if not text:
            continue
        rel = path.relative_to(material_root).as_posix()
        snippet = text[: max_chars - total]
        if not snippet:
            break
        chunks.append(f"【{rel}】\n{snippet}")
        total += len(snippet)
        if total >= max_chars:
            break
    return "\n\n".join(chunks)


def summarize_local_evidence(evidences: Iterable[Evidence]) -> str:
    lines: list[str] = []
    for item in evidences:
        if item.field_id.endswith(".数据使用提示"):
            continue
        value = str(item.value).strip()
        if not value or _is_placeholder_text(value):
            continue
        label = item.field_id.split(".")[-1]
        lines.append(f"- {label}: {value}")
    return "\n".join(lines[:40])


def build_offline_analysis(
    *,
    product_form: dict[str, Any],
    image_paths: list[Path],
    evidence_summary: str,
    text_materials: str = "",
) -> AIProductAnalysis:
    name = str(product_form.get("product_name") or "").strip() or "未命名商品"
    category = str(product_form.get("product_category") or "").strip()
    price = str(product_form.get("product_price") or "").strip()
    desc = str(product_form.get("product_desc") or "").strip()
    brand = str(product_form.get("product_brand") or "").strip()

    product_facts: dict[str, str] = {}
    if category:
        product_facts["商品类别"] = category
    if brand:
        product_facts["品牌名称"] = brand
    if price:
        product_facts["建议价格"] = f"{price} 元" if price.replace(".", "", 1).isdigit() else price
    if desc:
        product_facts["主要场景"] = desc
        _infer_desc_facts(desc, product_facts)
    if text_materials:
        summary = text_materials.replace("\n", " ").strip()
        product_facts["上传资料摘要"] = summary[:280] + ("…" if len(summary) > 280 else "")
        _infer_text_material_facts(text_materials, product_facts)

    image_count = len(image_paths)
    if image_count:
        product_facts["已上传图片"] = f"{image_count} 张商品图待视觉分析"
        roles = _infer_roles_from_paths(image_paths)
        if roles:
            product_facts["图片类型"] = "、".join(roles)

    positioning_parts = [part for part in (category, desc) if part]
    category_positioning = (
        f"{'，'.join(positioning_parts)}。" if positioning_parts else "请配置 LLM API Key 以启用 AI 分析。"
    )
    if text_materials and not positioning_parts:
        category_positioning = "已读取上传的文本资料，结合商品信息可进一步生成定位。"
    if image_count:
        category_positioning += " 本次使用本地规则，未完成云端图片识别，颜色、材质与细节需人工核对。"
    elif text_materials:
        category_positioning += " 本次使用本地文本规则，非云端文档理解。"

    selling_points = []
    if desc:
        selling_points.append(desc)
    if category:
        selling_points.append(f"面向{category}品类需求")
    if text_materials and len(selling_points) < 3:
        selling_points.append("资料中提到的功能/卖点待 AI 进一步提炼")

    concerns = []
    if image_count == 0 and not text_materials:
        concerns.append("尚未上传商品图片或可读文档，分析依据有限")
    elif image_count == 0:
        concerns.append("缺少商品图片，视觉卖点需后续补充")
    if not evidence_summary:
        concerns.append("缺少用户评价与历史内容，卖点判断依据有限")

    gaps = []
    if image_count == 0:
        gaps.append("缺少商品实拍图")
    gaps.append("本次未成功完成云端 AI 理解，当前为本地规则结果")

    return AIProductAnalysis(
        product_name=name,
        category_positioning=category_positioning,
        product_facts=product_facts,
        selling_points=tuple(selling_points[:3]),
        target_audience=f"关注{category or '该品类'}、预算约 {price or '待定'} 元的消费者" if category or price else "",
        usage_scenarios=(desc,) if desc else (),
        user_likes=tuple(selling_points[:2]),
        user_concerns=tuple(concerns[:3]),
        content_opportunities=desc or "结合商品图展示真实使用场景与细节",
        brand_tone=f"{brand} 品牌调性" if brand else "",
        visual_keywords=_visual_keywords_from_form(desc, image_paths),
        core_opportunity=category_positioning,
        data_gaps=tuple(gaps),
        source="offline",
    )


def merge_ai_analysis(
    report: ProductUnderstandingReport,
    analysis: AIProductAnalysis,
) -> ProductUnderstandingReport:
    if not analysis.has_content():
        return report

    product_id = report.product_id
    product_name = report.product_name
    if analysis.product_name and (
        analysis.source == "llm" or analysis.product_name != "未命名商品"
    ):
        product_name = analysis.product_name

    product_fact_map = {
        fact.label: fact for fact in report.facts_for_section(SECTION_PRODUCT_FACTS)
    }
    if analysis.category_positioning:
        product_fact_map["品类定位"] = _ai_fact(
            product_id, SECTION_PRODUCT_FACTS, "品类定位", analysis.category_positioning
        )
    for label, summary in analysis.product_facts.items():
        product_fact_map[label] = _ai_fact(product_id, SECTION_PRODUCT_FACTS, label, summary)
    if analysis.usage_scenarios:
        product_fact_map["主要场景"] = _ai_fact(
            product_id,
            SECTION_PRODUCT_FACTS,
            "主要场景",
            "、".join(analysis.usage_scenarios),
        )
    if analysis.target_audience:
        product_fact_map["核心人群"] = _ai_fact(
            product_id, SECTION_PRODUCT_FACTS, "核心人群", analysis.target_audience
        )

    review_fact_map = {
        fact.label: fact for fact in report.facts_for_section(SECTION_REVIEW_INSIGHTS)
    }
    likes_summary = "；".join(analysis.user_likes) or "；".join(analysis.selling_points)
    if likes_summary and _should_replace_review_fact(review_fact_map.get("用户真正喜欢")):
        review_fact_map["用户真正喜欢"] = _ai_fact(
            product_id, SECTION_REVIEW_INSIGHTS, "用户真正喜欢", likes_summary
        )
    concerns_summary = "；".join(analysis.user_concerns)
    if concerns_summary and _should_replace_review_fact(review_fact_map.get("用户主要顾虑")):
        review_fact_map["用户主要顾虑"] = _ai_fact(
            product_id, SECTION_REVIEW_INSIGHTS, "用户主要顾虑", concerns_summary
        )
    if analysis.content_opportunities and _should_replace_review_fact(
        review_fact_map.get("被忽略的表达机会")
    ):
        review_fact_map["被忽略的表达机会"] = _ai_fact(
            product_id,
            SECTION_REVIEW_INSIGHTS,
            "被忽略的表达机会",
            analysis.content_opportunities,
        )

    brand_fact_map = {
        fact.label: fact for fact in report.facts_for_section(SECTION_BRAND_MEMORY)
    }
    if analysis.brand_tone:
        brand_fact_map["品牌语气"] = _ai_fact(
            product_id, SECTION_BRAND_MEMORY, "品牌语气", analysis.brand_tone
        )
    if analysis.visual_keywords:
        brand_fact_map["视觉关键词"] = _ai_fact(
            product_id,
            SECTION_BRAND_MEMORY,
            "视觉关键词",
            "、".join(analysis.visual_keywords),
        )

    updated_sections: list[UnderstandingSection] = []
    for section in report.sections:
        if section.section_id == SECTION_PRODUCT_FACTS:
            updated_sections.append(
                UnderstandingSection(
                    section_id=section.section_id,
                    title=section.title,
                    facts=tuple(product_fact_map.values()),
                )
            )
        elif section.section_id == SECTION_REVIEW_INSIGHTS:
            updated_sections.append(
                UnderstandingSection(
                    section_id=section.section_id,
                    title=section.title,
                    facts=tuple(review_fact_map.values()),
                )
            )
        elif section.section_id == SECTION_BRAND_MEMORY:
            updated_sections.append(
                UnderstandingSection(
                    section_id=section.section_id,
                    title=section.title,
                    facts=tuple(brand_fact_map.values()),
                )
            )
        elif section.section_id == SECTION_DATA_GAPS and analysis.data_gaps:
            merged_gaps = tuple(dict.fromkeys((*section.gaps, *analysis.data_gaps)))
            updated_sections.append(
                UnderstandingSection(
                    section_id=section.section_id,
                    title=section.title,
                    facts=tuple(
                        UnderstandingFact(
                            fact_id=fact_id_of(product_id, SECTION_DATA_GAPS, gap),
                            section_id=SECTION_DATA_GAPS,
                            label="资料不足",
                            summary=gap,
                            evidence_ids=(),
                            source_locators=("ai_analysis",),
                            status=EvidenceStatus.PENDING,
                            is_gap=True,
                        )
                        for gap in merged_gaps
                    ),
                    gaps=merged_gaps,
                )
            )
        else:
            updated_sections.append(section)

    boundary = "基于用户图片与商品信息的 AI 分析" if analysis.source == "llm" else report.boundary_notice
    not_connected = (
        f"AI 模型：{analysis.model}" if analysis.source == "llm" and analysis.model else report.not_connected_notice
    )
    global_gaps = report.global_gaps
    if analysis.data_gaps:
        global_gaps = tuple(dict.fromkeys((*report.global_gaps, *analysis.data_gaps)))

    from dataclasses import replace

    return replace(
        report,
        product_name=product_name,
        sections=tuple(updated_sections),
        boundary_notice=boundary,
        not_connected_notice=not_connected,
        global_gaps=global_gaps,
    )


def _analyze_with_vision(
    *,
    image_paths: list[Path],
    product_form: dict[str, Any],
    evidence_summary: str,
    config: LLMConfig,
) -> AIProductAnalysis:
    user_text = _build_user_prompt(product_form, evidence_summary, len(image_paths))
    messages = build_vision_messages(SYSTEM_PROMPT, user_text, image_paths, config=config)
    payload = chat_completion_json(messages, config=config, temperature=0.2)
    analysis = AIProductAnalysis.from_dict(payload, source="llm", model=config.model)
    if not analysis.product_name:
        analysis.product_name = str(product_form.get("product_name") or "").strip()
    return analysis


def _analyze_with_text(
    *,
    product_form: dict[str, Any],
    evidence_summary: str,
    text_materials: str,
    image_count: int,
    config: LLMConfig,
) -> AIProductAnalysis:
    user_text = _build_user_prompt(
        product_form,
        evidence_summary,
        image_count,
        text_materials=text_materials,
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]
    payload = chat_completion_json(messages, config=config, temperature=0.2)
    analysis = AIProductAnalysis.from_dict(payload, source="llm", model=config.model)
    if not analysis.product_name:
        analysis.product_name = str(product_form.get("product_name") or "").strip()
    return analysis


def _clip_local_prompt_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + _LOCAL_TEXT_TRUNCATED


# 本机结果的最低契约：字段清单与 _analysis_schema_hint 的 13 个顶层键一一对应，
# 由 test_local_vision 用 json.loads(_analysis_schema_hint()) 的键集合当场核对，防止两边漂移。
_LOCAL_STR_FIELDS = ("product_name", "category_positioning", "target_audience",
                     "content_opportunities", "brand_tone", "core_opportunity")
_LOCAL_LIST_FIELDS = ("selling_points", "usage_scenarios", "user_likes", "user_concerns",
                      "visual_keywords", "data_gaps")
_LOCAL_SHAPE_FAILED = "本机图片理解结果字段缺失或类型不合格，已降级到本地规则"


def _analyze_with_local_vision(
    *,
    material_root: Path,
    image_path: Path,
    image_base64: str,
    product_form: dict[str, Any],
    evidence_summary: str,
    config: LocalVisionConfig,
) -> AIProductAnalysis:
    """本机一支：实发 1 张图，提示词里的张数也写 1，不复用云端的多图消息构造。"""
    user_text = _build_user_prompt(product_form, evidence_summary, 1,
                                  local_text_limit=LOCAL_PROMPT_TEXT_LIMIT)
    payload = local_chat_completion_json(config, system_prompt=SYSTEM_PROMPT,
                                        user_text=user_text, image_base64=image_base64)
    return _local_analysis_from_payload(
        payload,
        product_form=product_form,
        model=config.model,
        analyzed_image=_local_relative_image_name(material_root, image_path),
    )


def _local_relative_image_name(material_root: Path, path: Path) -> str:
    """结果里只留素材根目录之下的相对文件名，绝不把用户的绝对路径带进结果、导出或下一次云端请求。"""
    try:
        return path.relative_to(material_root).as_posix()
    except ValueError:
        return path.name


def _local_analysis_from_payload(
    payload: dict[str, Any],
    *,
    product_form: dict[str, Any],
    model: str,
    analyzed_image: str,
) -> AIProductAnalysis:
    """13 个字段齐全、类型对得上才算合格：不许用 str() 把任意错误类型伪装成合格值。

    空值是合法的（不逼模型编造尺寸/材质/评价/销量）。结构合格也不等于内容正确，校验通过后
    has_content 仍要为真，否则一样落回离线。云端 from_dict 的宽容度本批一字未改。
    """
    values: dict[str, Any] = {}
    for key in _LOCAL_STR_FIELDS:
        value = payload.get(key)
        if not isinstance(value, str):
            raise LLMClientError(_LOCAL_SHAPE_FAILED)
        values[key] = value.strip()

    facts = payload.get("product_facts")
    if not isinstance(facts, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in facts.items()):
        raise LLMClientError(_LOCAL_SHAPE_FAILED)

    lists: dict[str, list[str]] = {}
    for key in _LOCAL_LIST_FIELDS:
        raw = payload.get(key)
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise LLMClientError(_LOCAL_SHAPE_FAILED)
        lists[key] = [item.strip() for item in raw]

    # 无法确认的条目不许留在 product_facts：那一栏会被合并成 CONFIRMED 事实，data_gaps 才是它的去向。
    unconfirmed = [f"「{key.strip()}」{UNCONFIRMED_PLACEHOLDER}" for key, value in facts.items()
                   if value.strip() == UNCONFIRMED_PLACEHOLDER]
    values["product_facts"] = {
        key.strip(): value.strip() for key, value in facts.items()
        if key.strip() and value.strip() and value.strip() != UNCONFIRMED_PLACEHOLDER
    }
    for key in _LOCAL_LIST_FIELDS:
        if key == "data_gaps":
            # data_gaps 本身就是「未能确认」的落点，逐字保留；其余字段里的占位值不算内容。
            values[key] = lists[key]
        else:
            values[key] = [item for item in lists[key]
                           if item and item != UNCONFIRMED_PLACEHOLDER]
    values["data_gaps"] = list(dict.fromkeys([*values["data_gaps"], *unconfirmed]))

    analysis = AIProductAnalysis.from_dict(values, source="llm", model=model,
                                           execution_location="local",
                                           analyzed_image=analyzed_image)
    if not analysis.has_content():
        raise LLMClientError("本机图片理解结果没有可用内容，已降级到本地规则")
    if not analysis.product_name:
        analysis.product_name = str(product_form.get("product_name") or "").strip()
    return analysis


def _build_user_prompt(
    product_form: dict[str, Any],
    evidence_summary: str,
    image_count: int,
    *,
    text_materials: str = "",
    local_text_limit: int | None = None,
) -> str:
    # local_text_limit 只有本机分支会传：None 时本地资料一个字符都不截，云端请求字节保持不变。
    if local_text_limit is not None:
        evidence_summary = _clip_local_prompt_text(evidence_summary, local_text_limit)
    form_lines = []
    labels = {
        "product_name": "商品名称",
        "product_category": "商品类别",
        "product_price": "价格",
        "product_desc": "商品描述",
        "product_brand": "品牌",
    }
    for key, label in labels.items():
        value = str(product_form.get(key) or "").strip()
        if value:
            form_lines.append(f"- {label}：{value}")

    parts = [
        f"已上传 {image_count} 张商品图片。" if image_count else "当前没有商品图片，请主要依据文本资料与用户填写信息分析。",
        "",
        "【用户填写的商品信息】",
        "\n".join(form_lines) if form_lines else "- （用户未填写额外信息）",
    ]
    if text_materials:
        parts.extend(["", "【用户上传的文本资料原文】", text_materials[:MAX_TEXT_MATERIAL_CHARS]])
    if evidence_summary and evidence_summary != text_materials:
        parts.extend(["", "【本地资料摘要（Excel/文本，可能含演示数据）】", evidence_summary])
    parts.extend(
        [
            "",
            "请严格按以下 JSON schema 输出，不要添加额外字段：",
            _analysis_schema_hint(),
        ]
    )
    return "\n".join(parts)


def _ai_fact(
    product_id: str,
    section_id: str,
    label: str,
    summary: str,
) -> UnderstandingFact:
    return UnderstandingFact(
        fact_id=fact_id_of(product_id, section_id, label),
        section_id=section_id,
        label=label,
        summary=summary,
        evidence_ids=(),
        source_locators=("ai_analysis",),
        status=EvidenceStatus.CONFIRMED,
        is_gap=False,
    )


def _should_replace_review_fact(fact: UnderstandingFact | None) -> bool:
    if fact is None:
        return True
    return _is_placeholder_text(fact.summary)


def _is_placeholder_text(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered or lowered in {"none", "null", "待补充", "日常使用"}:
        return True
    return any(marker in lowered for marker in PLACEHOLDER_REVIEW_MARKERS)


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),) if str(value).strip() else ()


def _infer_desc_facts(desc: str, product_facts: dict[str, str]) -> None:
    color_keywords = ("绿", "蓝", "红", "黑", "白", "粉", "黄", "灰", "棕", "米", "咖")
    for keyword in color_keywords:
        if keyword in desc:
            product_facts.setdefault("颜色", f"描述提及{keyword}色系")
            break
    if any(word in desc for word in ("大容量", "容量", "能装", "收纳")):
        product_facts.setdefault("容量/规格", "大容量/强收纳（来自描述）")


def _infer_text_material_facts(text: str, product_facts: dict[str, str]) -> None:
    lowered = text.lower()
    if any(word in text for word in ("材质", "面料", "皮革", "尼龙", "金属")):
        product_facts.setdefault("材质", "资料中提及材质信息")
    if any(word in text for word in ("尺寸", "规格", "容量", "cm", "mm")):
        product_facts.setdefault("尺寸规格", "资料中包含规格/尺寸描述")
    if "卖点" in text or "优势" in lowered:
        product_facts.setdefault("资料要点", "资料中包含卖点/优势描述")


def _infer_roles_from_paths(image_paths: list[Path]) -> list[str]:
    roles: list[str] = []
    for path in image_paths:
        name = path.name
        if "主图" in name or "平铺" in name:
            roles.append("主图")
        elif "细节" in name or "容量" in name:
            roles.append("细节图")
        elif "场景" in name or "上身" in name:
            roles.append("场景图")
        else:
            roles.append(path.stem)
    return roles[:4]


def _visual_keywords_from_form(desc: str, image_paths: list[Path]) -> tuple[str, ...]:
    keywords: list[str] = []
    if desc:
        keywords.extend(part.strip() for part in desc.replace("，", ",").split(",") if part.strip())
    if image_paths:
        keywords.append(f"{len(image_paths)}张商品图")
    return tuple(keywords[:4])

