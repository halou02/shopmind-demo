from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.web.server import app


def test_product_summary_get_and_save(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    product_dir = workspace / "prod_summary_test"
    product_dir.mkdir(parents=True)
    (product_dir / "product_understanding.json").write_text(
        json.dumps({"product_name": "总结测试商品", "sections": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    client = TestClient(app)
    detail = client.get("/api/products/prod_summary_test")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["name"] == "总结测试商品"
    assert len(payload["summary"]["sections"]) == 6

    sections = payload["summary"]["sections"]
    sections[0]["body"] = "用户手动修改后的商品理解"
    save = client.post(
        "/api/products/prod_summary_test/summary",
        json={"sections": sections},
    )
    assert save.status_code == 200
    assert save.json()["ok"] is True

    again = client.get("/api/products/prod_summary_test")
    assert again.status_code == 200
    saved_body = again.json()["summary"]["sections"][0]["body"]
    assert saved_body == "用户手动修改后的商品理解"
    assert (product_dir / "product_summary.json").is_file()

    demo_save = client.post(
        "/api/products/demo:bag/summary",
        json={"sections": sections},
    )
    assert demo_save.status_code == 400
