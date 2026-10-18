from __future__ import annotations

import json
from typing import Any

from app.core.creative_directions import Direction
from app.core.capability_config import CapabilityConfigError, text_request_config
from app.core.llm_client import LLMClientError, chat_completion_json
from app.core.llm_config import LLMConfig, LocalTextConfig, load_llm_config
from app.core.models import ContentItem
from app.core.product_understanding import ProductUnderstandingReport

PLATFORM_GUIDES = {
    "xiaohongshu": (
        "小红书种草笔记：真实体验口吻、分段清晰、适度 emoji、标题有记忆点、"
        "结尾带 3-5 个话题标签（#开头）。强调生活方式与使用感受，避免硬广。"
    ),
    "douyin": (
        "抖音短视频文案/口播：前 3 秒强钩子，短句快节奏，适合 15-30 秒口播，"
        "标注分镜节奏（如 0-3s/3-10s），口语化、有互动引导（点赞/评论）。"
    ),
    "kuaishou": (
        "快手内容：接地气、实在、强调性价比与真实使用，可用「老铁」式亲切语气，"
        "突出耐用/好穿/好搭等朴素卖点，适合下沉市场信任感表达。"
    ),
    "shopping": (
        "购物平台（天猫/淘宝详情）：结构化卖点、参数规格、适用人群、场景、"
        "售后与品质承诺，转化导向，标题含核心关键词，正文分点列举卖点。"
    ),
}


def enhance_content_with_llm(
    items: tuple[ContentItem, ...],
    *,
    direction: Direction,
    understanding: ProductUnderstandingReport,
    product_evidence: str,
    ai_analysis: dict[str, Any] | None = None,
    config: LLMConfig | LocalTextConfig | None = None,
    generation_sources: dict[str, str] | None = None,
) -> tuple[ContentItem, ...]:
    if generation_sources is not None:
        generation_sources.update({item.channel: "template" for item in items})
    try:
        # 传入 v1 取配置的方式本身：v2 存在时不会被调用，禁用/待确认直接落回模板。
        cfg = config or text_request_config(load_llm_config)
    except CapabilityConfigError:
        return items
    # isinstance 而不是鸭子类型，也不看 .enabled：本机文字这一格结构上没有密钥，
    # 拿 LLMConfig 那条"启用＝必须有非空密钥"去判它，只会把真请求变成一次静默降级。
    is_local = isinstance(cfg, LocalTextConfig)
    if not is_local and not cfg.enabled:
        return items

    by_channel = {item.channel: item for item in items}
    schema = {
        channel: {"title": "标题", "body": "正文（可多段，保留换行）"}
        for channel in ("xiaohongshu", "douyin", "kuaishou", "shopping", "visual", "video")
    }
    user_payload = _build_context_payload(
        direction=direction,
        understanding=understanding,
        product_evidence=product_evidence,
        ai_analysis=ai_analysis,
        template_items=by_channel,
    )
    system = """你是资深电商全渠道内容策划，根据商品 AI 分析结果与创意方向，生成各平台差异化文案。

要求：
1. 严格基于提供的商品理解、AI 分析、证据，不编造未给出的参数
2. 四个文案平台风格必须明显不同，符合各平台用户习惯
3. visual 为视觉概念方案，必须引用 AI 分析中的颜色/材质/风格/视觉关键词
4. video 为 20 秒竖屏短视频脚本（分镜+口播）
5. 禁止使用夸大违禁词，保留「仅基于本地资料」的克制表达
6. 输出合法 JSON，字段与 schema 完全一致"""

    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"平台写作要求：\n{json.dumps(PLATFORM_GUIDES, ensure_ascii=False, indent=2)}\n\n"
                f"商品与方向上下文：\n{json.dumps(user_payload, ensure_ascii=False, indent=2)}\n\n"
                f"请按以下 schema 输出 JSON：\n{json.dumps(schema, ensure_ascii=False, indent=2)}"
            ),
        },
    ]
    meta: dict[str, bool] = {}
    # 上限判据只管本机这一支：云端返回 length 属于既有行为，本批不改它，更不许把它标成本机。
    try:
        payload = chat_completion_json(messages, config=cfg, temperature=0.45, meta=meta)
    except LLMClientError:
        if is_local and meta.get("truncated") and generation_sources is not None:
            generation_sources.update({item.channel: "local_truncated" for item in items})
        return items
    if is_local and meta.get("truncated"):
        # 撞到输出上限就整批不采纳：截点通常正好切在某一段正文中间，收住的那几段也不可信。
        if generation_sources is not None:
            generation_sources.update({item.channel: "local_truncated" for item in items})
        return items

    updated: list[ContentItem] = []
    produced_by = "local" if is_local else "cloud"
    for item in items:
        block = payload.get(item.channel)
        if not isinstance(block, dict):
            updated.append(item)
            continue
        title = str(block.get("title") or item.title).strip()
        body = str(block.get("body") or "").strip()
        if not body:
            updated.append(item)
            continue
        if generation_sources is not None:
            generation_sources[item.channel] = produced_by
        if not body.startswith("【"):
            body = f"【AI 生成 · 基于商品理解】\n\n{body}"
        updated.append(
            ContentItem(
                content_id=item.content_id,
                product_id=item.product_id,
                direction_id=item.direction_id,
                channel=item.channel,
                title=title or item.title,
                body=body,
                evidence_ids=item.evidence_ids,
                asset_ids=item.asset_ids,
                validation_level=item.validation_level,
                version=item.version,
            )
        )
    return tuple(updated)


def _build_context_payload(
    *,
    direction: Direction,
    understanding: ProductUnderstandingReport,
    product_evidence: str,
    ai_analysis: dict[str, Any] | None,
    template_items: dict[str, ContentItem],
) -> dict[str, Any]:
    facts: dict[str, str] = {}
    for section in understanding.sections:
        for fact in section.facts:
            if fact.summary and not fact.is_gap:
                facts[fact.label] = fact.summary

    templates = {
        channel: {"title": item.title, "body_preview": item.body[:400]}
        for channel, item in template_items.items()
    }
    return {
        "product_name": understanding.product_name,
        "direction": {
            "name": direction.name,
            "audience": direction.audience,
            "main_selling_point": direction.main_selling_point,
            "scenes": list(direction.scenes),
            "platform_expression": direction.platform_expression,
            "avoid_expression": direction.avoid_expression,
            "risks": list(direction.risks),
        },
        "product_facts": facts,
        "product_evidence": product_evidence,
        "ai_analysis": ai_analysis or {},
        "template_reference": templates,
    }
