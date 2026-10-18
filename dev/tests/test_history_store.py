import tempfile
import unittest
from pathlib import Path

from app.web.history_store import archive_task, delete_history_item, list_history


class HistoryStoreTests(unittest.TestCase):
    def test_delete_history_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archived = archive_task(root, {"id": "task_test001", "product_name": "测试"})
            self.assertEqual(archived["id"], "task_test001")
            self.assertEqual(len(list_history(root)), 1)
            self.assertTrue(delete_history_item(root, "task_test001"))
            self.assertEqual(list_history(root), [])
            self.assertFalse(delete_history_item(root, "task_test001"))

    def test_delete_history_item_rejects_invalid_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                delete_history_item(Path(tmp), "../evil")


if __name__ == "__main__":
    unittest.main()
