from __future__ import annotations

import unittest

import pandas as pd

from school_survey.bi import build_bi_long, build_looker_studio_csv


class BiTests(unittest.TestCase):
    def setUp(self):
        self.source = pd.DataFrame(
            [
                {
                    "調査年度": 2026,
                    "学校コード": "F001",
                    "学校名": "テスト大学",
                    "学部名": "教育学部",
                    "学科": "学校教育",
                    "符号": "A001",
                    "計_男": 40,
                    "計_女": 60,
                    "志願者_男": 80,
                    "志願者_女": 120,
                    "入学者_男": 10,
                    "入学者_女": 20,
                },
                {
                    "調査年度": 2026,
                    "学校コード": "F001",
                    "学校名": "テスト大学",
                    "学部名": "教育学部",
                    "学科": "計",
                    "符号": "9999",
                    "計_男": 40,
                    "計_女": 60,
                    "志願者_男": 80,
                    "志願者_女": 120,
                    "入学者_男": 10,
                    "入学者_女": 20,
                },
            ]
        )

    def test_builds_long_metrics_and_total_without_losing_zero(self):
        result = build_bi_long(self.source)
        self.assertEqual(set(result["指標"]), {"学生数", "志願者数", "入学者数"})
        self.assertEqual(set(result["性別"]), {"男", "女", "合計"})
        total = result[
            (result["集計区分"] == "学部計")
            & (result["指標"] == "学生数")
            & (result["性別"] == "合計")
        ]
        self.assertEqual(total.iloc[0]["値"], 100)

    def test_separates_department_rows_from_faculty_totals(self):
        result = build_bi_long(self.source)
        categories = result.groupby("学科")["集計区分"].first().to_dict()
        self.assertEqual(categories["学校教育"], "学科明細")
        self.assertEqual(categories["計"], "学部計")

    def test_looker_csv_is_utf8_with_bom(self):
        result = build_bi_long(self.source)
        content = build_looker_studio_csv(result)
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        self.assertIn("集計区分", content.decode("utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
