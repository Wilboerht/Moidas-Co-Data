from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codata import config
from codata.storage import as_cursor, as_items, item_author, item_id, item_title


class ExtractionTests(unittest.TestCase):
    def test_as_items_handles_common_shapes(self) -> None:
        self.assertEqual(len(as_items({"code": 0, "data": [{"a": 1}, "skip"]})), 1)
        self.assertEqual(len(as_items({"code": 0, "data": {"items": [{"a": 1}]}})), 1)
        self.assertEqual(len(as_items({"code": 0, "data": {"notes": [{"a": 1}]}})), 1)
        self.assertEqual(as_items({"code": 0, "data": {"cursor": "x"}}), [])

    def test_as_cursor(self) -> None:
        self.assertEqual(as_cursor({"data": {"cursor": "abc"}}), "abc")
        self.assertIsNone(as_cursor({"data": {"cursor": ""}}))
        self.assertIsNone(as_cursor([]))

    def test_item_fields(self) -> None:
        item = {"noteId": "123", "displayTitle": "标题", "user": {"nickname": "作者"}}
        self.assertEqual(item_id(item), "123")
        self.assertEqual(item_title(item), "标题")
        self.assertEqual(item_author(item), "作者")

    def test_item_id_nested_note(self) -> None:
        self.assertEqual(item_id({"note_card": {"note_id": "n1"}}), "n1")

    def test_nested_note_title_and_author(self) -> None:
        item = {
            "model_type": "note",
            "note": {
                "id": "n1",
                "title": "咖啡笔记",
                "user": {"userid": "u1", "nickname": "咖啡师"},
            },
        }
        self.assertEqual(item_id(item), "n1")
        self.assertEqual(item_title(item), "咖啡笔记")
        self.assertEqual(item_author(item), "咖啡师")

    def test_item_id_handles_userid(self) -> None:
        self.assertEqual(item_id({"userid": "u1", "nickname": "咖啡师"}), "u1")


class ConfigTests(unittest.TestCase):
    def test_runtime_env_overrides_base_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("DATAFLOW_TOKEN=base\n", encoding="utf-8")
            runtime = root / "data" / "runtime.env"
            runtime.parent.mkdir()
            runtime.write_text("DATAFLOW_TOKEN=runtime\n", encoding="utf-8")
            with (
                patch.object(config, "PROJECT_ROOT", root),
                patch.dict(os.environ, {"CODATA_ENV_FILE": str(runtime)}),
            ):
                loaded = config.load_settings(require_credentials=False)
                self.assertEqual(loaded.token, "runtime")
                self.assertEqual(config.runtime_env_path(), runtime)


if __name__ == "__main__":
    unittest.main()
