from __future__ import annotations

from pathlib import Path

from app.web.material_library_store import (
    INDEX_FILE_NAME,
    library_index_entries,
    record_library_files,
)
from app.web.reference_files import ensure_material_library_root, save_reference_file


def test_record_library_files_persists_index(tmp_path: Path) -> None:
    library = ensure_material_library_root(tmp_path)
    saved = save_reference_file(library, "demo.png", b"\x89PNG\r\n\x1a\n")

    recorded = record_library_files(library, [saved], source="upload")
    entries = library_index_entries(library)

    assert len(recorded) == 1
    assert len(entries) == 1
    assert entries[0]["relative_path"].endswith("demo.png")
    assert (library / INDEX_FILE_NAME).is_file()


def test_library_upload_does_not_replace_task_material(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.web.server import SESSION, app

    monkeypatch.chdir(tmp_path)
    task_dir = tmp_path / "task_pack"
    task_dir.mkdir()
    SESSION.material_path = task_dir

    client = TestClient(app)
    files = [
        ("files", ("library-1.png", b"\x89PNG\r\n\x1a\n", "image/png")),
    ]
    response = client.post("/api/material-library/upload", files=files)
    assert response.status_code == 200
    payload = response.json()
    assert payload["target"] == "library"
    assert payload["added_count"] == 1
    assert payload["index_count"] >= 1
    assert SESSION.material_path == task_dir.resolve()
    library_root = Path(payload["library_path"])
    assert (library_root / INDEX_FILE_NAME).is_file()
