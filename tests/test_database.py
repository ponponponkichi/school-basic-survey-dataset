from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import pandas as pd
import numpy as np

from school_survey.database import Database
from school_survey.excel_reader import ParsedExcel


def parsed(total: int) -> ParsedExcel:
    dataframe = pd.DataFrame(
        [{"年度": 2026, "学校コード": "F0001", "学校名": "テスト大学", "計_計": total}]
    )
    return ParsedExcel(
        dataframe=dataframe,
        header_row_number=5,
        source_row_numbers=[6],
        source_columns=list(dataframe.columns),
        export_columns=list(dataframe.columns),
    )


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "test.db")
        self.database.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def import_one(self, content: bytes, total: int):
        return self.database.import_dataframe(
            survey_year=2026,
            form_code="07go_A",
            form_label="学生数",
            source_url="https://portal.niad.ac.jp/example.xlsx",
            source_filename="2026_07go_A.xlsx",
            raw_file_path=Path(self.temp_dir.name) / "2026_07go_A.xlsx",
            content=content,
            parsed=parsed(total),
        )

    def test_same_file_is_not_duplicated(self):
        first = self.import_one(b"first", 100)
        second = self.import_one(b"first", 100)

        self.assertEqual(first.status, "imported")
        self.assertEqual(second.status, "unchanged")
        self.assertEqual(second.version_number, 1)
        frame = self.database.load_active_dataframe([2026], ["07go_A"])
        self.assertEqual(len(frame), 1)

    def test_changed_file_creates_new_active_version(self):
        self.import_one(b"first", 100)
        result = self.import_one(b"second", 101)

        self.assertEqual(result.status, "imported")
        self.assertEqual(result.version_number, 2)
        summary = self.database.get_active_summary()
        self.assertEqual(len(summary), 1)
        self.assertEqual(int(summary.loc[0, "version_number"]), 2)
        frame = self.database.load_active_dataframe([2026], ["07go_A"])
        self.assertEqual(int(frame.loc[0, "計_計"]), 101)

    def test_backup_contains_latest_data(self):
        self.import_one(b"first", 100)
        backup = self.database.create_backup_bytes()
        backup_path = Path(self.temp_dir.name) / "backup.db"
        backup_path.write_bytes(backup)
        backup_database = Database(backup_path)

        frame = backup_database.load_active_dataframe([2026], ["07go_A"])

        self.assertEqual(len(frame), 1)
        self.assertEqual(int(frame.loc[0, "計_計"]), 100)

    def test_accepts_numpy_year_values_from_streamlit_options(self):
        self.import_one(b"first", 100)

        frame = self.database.load_active_dataframe([np.int64(2026)], ["07go_A"])

        self.assertEqual(len(frame), 1)

    def test_records_added_removed_and_reordered_columns(self):
        first = ParsedExcel(
            dataframe=pd.DataFrame([{"学校名": "A大学", "学生数": 100, "備考": "旧"}]),
            header_row_number=4,
            source_row_numbers=[5],
            source_columns=["学校名", "学生数", "備考"],
            export_columns=["学校名", "学生数", "備考"],
        )
        second = ParsedExcel(
            dataframe=pd.DataFrame([{"学生数": 101, "学校名": "A大学", "新項目": "新"}]),
            header_row_number=4,
            source_row_numbers=[5],
            source_columns=["学生数", "学校名", "新項目"],
            export_columns=["学生数", "学校名", "新項目"],
        )
        common = {
            "survey_year": 2026,
            "form_code": "07go_A",
            "form_label": "学生数",
            "source_url": "https://portal.niad.ac.jp/example.xlsx",
            "source_filename": "2026_07go_A.xlsx",
            "raw_file_path": Path(self.temp_dir.name) / "2026_07go_A.xlsx",
        }
        self.database.import_dataframe(content=b"first", parsed=first, **common)
        result = self.database.import_dataframe(content=b"second", parsed=second, **common)

        self.assertIsNotNone(result.schema_change)
        self.assertEqual(result.schema_change.added_columns, ("新項目",))
        self.assertEqual(result.schema_change.removed_columns, ("備考",))
        self.assertEqual(set(result.schema_change.reordered_columns), {"学生数", "学校名"})
        changes = self.database.get_recent_schema_changes()
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["added_columns"], ["新項目"])


if __name__ == "__main__":
    unittest.main()

