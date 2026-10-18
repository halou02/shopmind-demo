from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.web.product_store import create_product, list_product_files, load_product_profile, save_product_file
from app.web.server import app


def test_create_product_and_save_detail(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    created = client.post(
        "/api/products",
        json={
            "product_name": "手动添加牛仔裤",
            "product_category": "服饰",
            "product_price": "329",
            "product_brand": "界线",
            "product_desc": "高腰直筒",
        },
    )
    assert created.status_code == 200
    product_id = created.json()["product_id"]
    assert product_id.startswith("prod_")

    detail = client.get(f"/api/products/{product_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["profile"]["product_name"] == "手动添加牛仔裤"
    assert len(payload["summary"]["sections"]) == 6

    save = client.post(
        f"/api/products/{product_id}/save",
        json={
            "profile": {
                "product_name": "手动添加牛仔裤",
                "product_category": "服饰",
                "product_price": "399",
                "product_brand": "界线",
                "product_desc": "更新描述",
                "user_notes": "人工补充说明",
            },
            "sections": payload["summary"]["sections"],
        },
    )
    assert save.status_code == 200
    assert save.json()["profile"]["product_price"] == "399"

    upload = client.post(
        f"/api/products/{product_id}/files",
        files=[("files", ("cover.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
    )
    assert upload.status_code == 200
    assert upload.json()["added_count"] == 1

    again = client.get(f"/api/products/{product_id}")
    assert len(again.json()["files"]) == 1

    deleted = client.post(
        f"/api/products/{product_id}/files/delete",
        json={"relative_path": again.json()["files"][0]["relative_path"]},
    )
    assert deleted.status_code == 200
    assert deleted.json()["files"] == []


def test_create_product_duplicate_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    create_product(workspace, {"product_name": "重复商品"})
    client = TestClient(app)
    response = client.post("/api/products", json={"product_name": "重复商品"})
    assert response.status_code == 400
