from __future__ import annotations

from io import BytesIO
import unittest
import zipfile

import pandas as pd

from school_survey.exports import build_csv_zip, build_excel_workbook


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.frames = {
            "07go_A": pd.DataFrame(
                [{"調査年度": 2026, "学校名": "テスト大学", "計_計": 123}]
            )
        }
        self.manifest = pd.DataFrame(
            [{"調査年度": 2026, "様式コード": "07go_A", "行数": 1}]
        )
        self.column_dictionary = pd.DataFrame(
            [{"調査年度": 2026, "様式コード": "07go_A", "列順": 1, "元の列名": "年度", "出力列名": "年度"}]
        )

    def test_csv_zip_contains_data_and_manifest(self):
        content = build_csv_zip(self.frames, self.manifest, self.column_dictionary)
        with zipfile.ZipFile(BytesIO(content)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {"07go_A.csv", "取得情報.csv", "列定義.csv", "README.txt"},
            )
            csv_text = archive.read("07go_A.csv").decode("utf-8-sig")
            self.assertIn("テスト大学", csv_text)

    def test_excel_contains_expected_sheets(self):
        content = build_excel_workbook(self.frames, self.manifest, self.column_dictionary)
        workbook = pd.ExcelFile(BytesIO(content))
        self.assertEqual(workbook.sheet_names, ["07go_A", "取得情報", "列定義", "利用上の注意"])


if __name__ == "__main__":
    unittest.main()
