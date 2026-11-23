from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codata.panel.queries import filter_rows, format_display, sort_rows, to_csv_bytes


def frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "昵称": "咖啡师",
                "粉丝数": 19360.0,
                "粉丝层级": "尾部",
                "采用状态": "待审核",
                "匹配方向": "咖啡",
                "命中关键词": "咖啡、手冲",
                "来源标签": "小红书",
                "账号ID": "u1",
            },
            {
                "昵称": "新人",
                "粉丝数": None,
                "粉丝层级": "",
                "采用状态": "候选",
                "匹配方向": "未分类",
                "命中关键词": "咖啡",
                "来源标签": "小红书",
                "账号ID": "u2",
            },
        ]
    )


class FilterTests(unittest.TestCase):
    def test_only_unenriched(self) -> None:
        result = filter_rows(
            frame(),
            tiers=[],
            statuses=[],
            directions=[],
            keywords=[],
            search="",
            min_fans=None,
            max_fans=None,
            only_unenriched=True,
        )
        self.assertEqual(result["账号ID"].tolist(), ["u2"])

    def test_keyword_and_status_filters(self) -> None:
        result = filter_rows(
            frame(),
            tiers=["尾部"],
            statuses=["待审核"],
            directions=["咖啡"],
            keywords=["手冲"],
            search="咖啡",
            min_fans=1000,
            max_fans=50000,
        )
        self.assertEqual(result["账号ID"].tolist(), ["u1"])

    def test_no_match(self) -> None:
        result = filter_rows(
            frame(),
            tiers=["顶流"],
            statuses=[],
            directions=[],
            keywords=[],
            search="",
            min_fans=None,
            max_fans=None,
        )
        self.assertTrue(result.empty)

    def test_format_display(self) -> None:
        display = format_display(frame())
        self.assertEqual(display.loc[0, "粉丝数"], "19,360")
        self.assertEqual(display.loc[1, "粉丝数"], "—")
        self.assertEqual(display.loc[1, "粉丝层级"], "—")

    def test_sort_rows(self) -> None:
        result = sort_rows(frame(), "粉丝数 ↓")
        self.assertEqual(result["账号ID"].tolist()[0], "u1")
        self.assertEqual(result["账号ID"].tolist()[1], "u2")

    def test_csv_export_escapes_formulas(self) -> None:
        data = frame()
        data.loc[0, "昵称"] = "=HYPERLINK(\"https://evil.example\")"
        encoded = to_csv_bytes(data, list(data.columns))
        text = encoded.decode("utf-8-sig")
        self.assertIn("'=HYPERLINK", text)


if __name__ == "__main__":
    unittest.main()
