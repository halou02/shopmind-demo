from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.web.reference_files import save_reference_file
from app.web.server import SESSION, app


def test_delete_task_material(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)
    upload = client.post(
        "/api/upload-files",
        files=[("files", ("task.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
    )
    assert upload.status_code == 200
    relative_path = upload.json()["images"][0]["relative_path"]

    deleted = client.post("/api/task-materials/delete", json={"relative_path": relative_path})
    assert deleted.status_code == 200
    payload = deleted.json()
    assert payload["ok"] is True
    assert not payload["images"]


def test_delete_library_material(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)
    upload = client.post(
        "/api/material-library/upload",
        files=[("files", ("lib.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
    )
    assert upload.status_code == 200
    relative_path = upload.json()["images"][0]["relative_path"]

    deleted = client.post("/api/material-library/delete", json={"relative_path": relative_path})
    assert deleted.status_code == 200
    assert deleted.json()["images"] == []


def test_delete_workspace_product(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    product_dir = workspace / "prod_test123"
    product_dir.mkdir(parents=True)
    (product_dir / "product_understanding.json").write_text("{}", encoding="utf-8")

    client = TestClient(app)
    response = client.delete("/api/products/prod_test123")
    assert response.status_code == 200
    assert not product_dir.exists()

    demo = client.delete("/api/products/demo:bag")
    assert demo.status_code == 400
