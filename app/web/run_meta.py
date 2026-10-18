from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_run_meta(
    output_root: Path,
    product_id: str,
    *,
    material_path: str,
    product_name: str,
    category: str | None = None,
    brand: str | None = None,
    price: str | int | float | None = None,
    product_form: dict[str, Any] | None = None,
) -> None:
    destination = output_root / product_id / "run_meta.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "product_id": product_id,
        "material_path": material_path,
        "product_name": product_name,
        "category": category,
        "brand": brand,
        "price": price,
        "product_form": product_form or {},
    }
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
