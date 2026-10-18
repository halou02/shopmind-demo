from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SUMMARY_FILE_NAME = "product_summary.json"

SECTION_SPECS: tuple[tuple[str, str], ...] = (
    ("understanding", "AI 商品理解"),
    ("judgment", "经营判断"),
    ("direction", "创意方向"),
    ("platform_copy", "多平台文案"),
    ("visual", "视觉概念"),
    ("video", "视频脚本"),
)

CHANNEL_LABELS = {
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "kuaishou": "快手",
    "shopping": "购物平台",
}


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _latest_content_package(product_dir: Path) -> dict[str, Any] | None:
    packages_dir = product_dir / "packages"
    if not packages_dir.is_dir():
        return None
    candidates: list[tuple[float, Path]] = []
    for pkg in packages_dir.glob("pkg_*"):
        candidate = pkg / "content_package.json"
        if candidate.is_file():
            candidates.append((candidate.stat().st_mtime, candidate))
    if not candidates:
        root_candidate = product_dir / "content_package.json"
        if root_candidate.is_file():
            return _read_json(root_candidate)
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return _read_json(candidates[0][1])


def _section_facts(understanding: dict[str, Any] | None, section_id: str) -> str:
    if not understanding:
        return ""
    lines: list[str] = []
    for section in understanding.get("sections") or []:
        if section.get("section_id") != section_id:
            continue
        for fact in section.get("facts") or []:
            label = str(fact.get("label") or "").strip()
            summary = str(fact.get("summary") or "").strip()
            if label and summary:
                lines.append(f"{label}：{summary}")
    return "\n".join(lines)


def _understanding_summary(understanding: dict[str, Any] | None) -> str:
    if not understanding:
        return "尚未完成创意任务分析，请点击「创作」生成商品理解。"
    parts: list[str] = []
    name = str(understanding.get("product_name") or "").strip()
    if name:
        parts.append(f"商品名称：{name}")
    facts = _section_facts(understanding, "product_facts")
    if facts:
        parts.append(facts)
    reviews = _section_facts(understanding, "review_insights")
    if reviews:
        parts.append("\n用户评价洞察：\n" + reviews)
    gaps = understanding.get("global_gaps") or []
    if gaps:
        parts.append("\n资料缺口：\n" + "\n".join(f"- {gap}" for gap in gaps))
    return "\n".join(parts).strip()


def _judgment_summary(judgment: dict[str, Any] | None) -> str:
    if not judgment:
        return "暂无经营判断，完成创意任务后将自动填充。"
    lines = [
        f"核心机会：{judgment.get('core_opportunity') or '—'}",
        f"目标人群：{judgment.get('target_audience') or '—'}",
        f"内容机会：{judgment.get('content_opportunity') or '—'}",
    ]
    risks = judgment.get("key_risks") or []
    if risks:
        lines.append("主要风险：\n" + "\n".join(f"- {item}" for item in risks))
    return "\n".join(lines)


def _direction_summary(directions: dict[str, Any] | None, content: dict[str, Any] | None) -> str:
    direction_id = (content or {}).get("direction_id")
    direction_name = (content or {}).get("direction_name")
    items = (directions or {}).get("directions") or []
    selected = None
    for item in items:
        if direction_id and item.get("direction_id") == direction_id:
            selected = item
            break
    if selected is None and items:
        selected = items[0]
    if not selected:
        return "暂无创意方向，请先完成分析并选择方向。"
    lines = [
        f"方向名称：{selected.get('name') or direction_name or '—'}",
        f"核心卖点：{selected.get('main_selling_point') or '—'}",
        f"目标人群：{selected.get('audience') or '—'}",
        f"适用场景：{' / '.join(selected.get('scenes') or [])}",
        f"平台表达：{selected.get('platform_expression') or '—'}",
    ]
    if selected.get("avoid_expression"):
        lines.append(f"避免表达：{selected['avoid_expression']}")
    return "\n".join(lines)


def _content_by_channel(content: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in (content or {}).get("items") or []:
        channel = str(item.get("channel") or "")
        if channel:
            mapping[channel] = item
    return mapping


def _platform_copy_summary(content: dict[str, Any] | None) -> str:
    by_channel = _content_by_channel(content)
    if not by_channel:
        return "暂无平台文案，请在创意任务中生成内容包。"
    blocks: list[str] = []
    for channel in ("xiaohongshu", "douyin", "kuaishou", "shopping"):
        item = by_channel.get(channel)
        if not item:
            continue
        label = CHANNEL_LABELS.get(channel, channel)
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        preview = body[:500] + ("…" if len(body) > 500 else "")
        blocks.append(f"【{label}】{title}\n{preview}")
    return "\n\n".join(blocks) if blocks else "暂无平台文案，请在创意任务中生成内容包。"


def _channel_body(content: dict[str, Any] | None, channel: str, empty: str) -> str:
    item = _content_by_channel(content).get(channel)
    if not item:
        return empty
    title = str(item.get("title") or "").strip()
    body = str(item.get("body") or "").strip()
    return f"{title}\n\n{body}".strip()


def build_product_summary(
    *,
    product_id: str,
    product_dir: Path | None,
    understanding: dict[str, Any] | None,
    judgment: dict[str, Any] | None,
    directions: dict[str, Any] | None,
    demo_hint: str = "",
) -> dict[str, Any]:
    content = _latest_content_package(product_dir) if product_dir else None
    generated: dict[str, str] = {
        "understanding": _understanding_summary(understanding) or demo_hint,
        "judgment": _judgment_summary(judgment),
        "direction": _direction_summary(directions, content),
        "platform_copy": _platform_copy_summary(content),
        "visual": _channel_body(content, "visual", "暂无视觉概念方案。"),
        "video": _channel_body(content, "video", "暂无视频脚本。"),
    }
    saved = load_product_summary(product_dir) if product_dir else None
    sections: list[dict[str, str]] = []
    for section_id, title in SECTION_SPECS:
        body = generated.get(section_id, "")
        if saved and saved.get("sections"):
            for block in saved["sections"]:
                if block.get("id") == section_id and str(block.get("body") or "").strip():
                    body = str(block["body"])
                    break
        sections.append({"id": section_id, "title": title, "body": body})
    return {
        "product_id": product_id,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "sections": sections,
        "has_generated_content": content is not None,
    }


def load_product_summary(product_dir: Path) -> dict[str, Any] | None:
    return _read_json(product_dir / SUMMARY_FILE_NAME)


def save_product_summary(product_dir: Path, sections: list[dict[str, str]]) -> dict[str, Any]:
    normalized: list[dict[str, str]] = []
    by_id = {str(item.get("id") or ""): item for item in sections}
    for section_id, title in SECTION_SPECS:
        block = by_id.get(section_id) or {}
        normalized.append(
            {
                "id": section_id,
                "title": title,
                "body": str(block.get("body") or "").strip(),
            }
        )
    payload = {
        "sections": normalized,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    _write_json(product_dir / SUMMARY_FILE_NAME, payload)
    return payload
