from __future__ import annotations

from typing import Any

PRODUCT_CATEGORY_OPTIONS: list[dict[str, str]] = [
    {"key": "bag", "label": "箱包"},
    {"key": "shoes", "label": "鞋子"},
    {"key": "pants", "label": "裤子"},
    {"key": "top", "label": "上衣"},
    {"key": "coat", "label": "外套"},
    {"key": "custom", "label": "自定义"},
]

CATEGORY_KEYS = {item["key"] for item in PRODUCT_CATEGORY_OPTIONS}
KEY_TO_LABEL = {item["key"]: item["label"] for item in PRODUCT_CATEGORY_OPTIONS}
LABEL_TO_KEY = {item["label"]: item["key"] for item in PRODUCT_CATEGORY_OPTIONS}


def category_label(key: str | None) -> str:
    if not key:
        return "商品"
    if key == "custom":
        return "自定义"
    return KEY_TO_LABEL.get(key, key)


def normalize_category_key(raw: str | None) -> str:
    if not raw:
        return "all"
    value = str(raw).strip()
    if value in CATEGORY_KEYS:
        return value
    if value in LABEL_TO_KEY:
        return LABEL_TO_KEY[value]
    lowered = value.lower()
    if value in {"bag", "clothing"} or "包" in value or "bag" in lowered or "tote" in lowered:
        return "bag"
    if "鞋" in value or "shoe" in lowered:
        return "shoes"
    if "裤" in value or "jeans" in lowered or "牛仔" in value:
        return "pants"
    if "上衣" in value or "top" in lowered or "T恤" in value or "衬衫" in value:
        return "top"
    if "外套" in value or "coat" in lowered or "夹克" in value or "大衣" in value:
        return "coat"
    if "服饰" in value or "服装" in value:
        return "top"
    return "custom"


def resolve_product_category_fields(payload: dict[str, Any] | None) -> tuple[str, str]:
    """Return (filter_key, display_label) for product library."""
    if not payload:
        return "all", "商品"
    key = payload.get("product_category_key")
    label = str(payload.get("product_category") or "").strip()
    if key:
        key = normalize_category_key(str(key))
    elif label:
        key = normalize_category_key(label)
    else:
        key = "all"
    if key == "custom":
        display = label or "自定义"
        return "custom", display
    if label:
        return key, label
    return key, category_label(key)
