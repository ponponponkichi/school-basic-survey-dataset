from __future__ import annotations

from io import BytesIO
import unittest

from openpyxl import Workbook

from school_survey.excel_reader import read_excel_bytes


class ExcelReaderTests(unittest.TestCase):
    def test_reads_hidden_header_and_normalizes_known_aliases(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["表示用タイトル"])
        sheet.append(["年度(西暦)", "学校調査番号", "大学", "計_計"])
        sheet.row_dimensions[2].hidden = True
        sheet.append([2012, 100, "テスト大学", 123])
        buffer = BytesIO()
        workbook.save(buffer)

        parsed = read_excel_bytes(buffer.getvalue(), "sample.xlsx")

        self.assertEqual(parsed.header_row_number, 2)
        self.assertEqual(parsed.source_row_numbers, [3])
        self.assertEqual(list(parsed.dataframe.columns), ["年度", "学校調査番号", "学校名", "計_計"])
        self.assertEqual(parsed.dataframe.loc[0, "学校調査番号"], "100")
        self.assertEqual(parsed.dataframe.loc[0, "学校名"], "テスト大学")


if __name__ == "__main__":
    unittest.main()

