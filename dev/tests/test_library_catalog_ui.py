import tempfile
import unittest
from pathlib import Path

from app.web.catalog import filter_library_catalog_for_ui
from app.web.materials_flow import build_material_catalog


class LibraryCatalogUiFilterTests(unittest.TestCase):
    def test_hides_index_and_product_sheet_from_ui_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "library_index.json").write_text("{}", encoding="utf-8")
            ref = root / "参考资料"
            ref.mkdir()
            (ref / "商品资料.xlsx").write_bytes(b"fake")
            (root / "商品图片").mkdir()
            (root / "商品图片" / "demo.png").write_bytes(b"png")

            raw = build_material_catalog(root, [])
            filtered = filter_library_catalog_for_ui(raw)
            names = {item["name"] for item in filtered["items"]}
            self.assertNotIn("library_index.json", names)
            self.assertNotIn("商品资料.xlsx", names)
            self.assertIn("demo.png", names)
            self.assertEqual(filtered["stats"]["total_count"], 1)


if __name__ == "__main__":
    unittest.main()
