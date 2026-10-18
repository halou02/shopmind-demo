from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "ShopMind-交互原型-素材库(1).html"
OUT = ROOT / "app" / "web" / "static" / "index.html"


def main() -> None:
    src = SRC.read_text(encoding="utf-8")
    body_start = src.index("<body>") + len("<body>")
    body_end = src.index("<script>window.IMG_MAP")
    body = src[body_start:body_end].strip()

    body = re.sub(
        r'<div class="projects-grid">.*?</div>\s*</div>\s*<!-- 创意流程',
        '<div class="projects-grid" id="homeProjects"></div>\n    </div>\n\n    <!-- 创意流程',
        body,
        count=1,
        flags=re.DOTALL,
    )
    body = body.replace('onclick="simulateUpload()"', 'data-action="pick-folder"')
    body = body.replace('onclick="pickDemo(\'bag\')"', 'data-demo="bag" data-action="pick-demo"')
    body = body.replace('onclick="pickDemo(\'jeans\')"', 'data-demo="jeans" data-action="pick-demo"')
    body = body.replace('onclick="goToStep2()"', 'data-action="go-step-2"')
    body = body.replace('onclick="goToStep3()"', 'data-action="go-step-3"')
    body = body.replace('onclick="goToStep4()"', 'data-action="go-step-4"')
    body = body.replace('onclick="goToStep5()"', 'data-action="go-step-5"')
    body = body.replace('onclick="goToStep1()"', 'data-action="go-step-1"')
    body = body.replace('onclick="startCreativeFlow()"', 'data-action="start-flow"')
    body = body.replace('onclick="startCreativeFlowNav(this)"', 'data-action="start-flow-nav"')
    body = re.sub(
        r'<div class="product-grid" id="productGrid">.*?</div>\s*</div>\s*</div>\s*<div class="toast"',
        '<div class="product-grid" id="productGrid"></div>\n        </div>\n    </div>\n\n    <div class="toast"',
        body,
        count=1,
        flags=re.DOTALL,
    )
    body = body.replace(
        'onclick="showToast(\'批量上传功能演示\')"',
        'data-action="pick-folder"',
    )
    body = re.sub(
        r'<div class="material-grid" id="materialGrid">.*?</div>\s*</div>\s*</div>\s*<!-- 商品库 -->',
        '<div class="material-grid" id="materialGrid"></div>\n            </div>\n        </div>\n    </div>\n\n    <!-- 商品库 -->',
        body,
        count=1,
        flags=re.DOTALL,
    )

    head = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ShopMind - AI商品创意导演</title>
  <link rel="stylesheet" href="/prototype.css">
  <link rel="stylesheet" href="/app-overrides.css">
</head>
<body>
"""
    tail = """
<script src="/app.js"></script>
</body>
</html>
"""
    OUT.write_text(head + body + tail, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
