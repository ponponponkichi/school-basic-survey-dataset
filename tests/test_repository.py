from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

import pandas as pd

from school_survey.excel_reader import ParsedExcel
from school_survey.repository import DataRepository


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


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.repository = DataRepository(
            data_dir=root,
            database_path=root / "database_snapshot" / "school_basic_survey.db",
            raw_history_dir=root / "raw_history",
            datasets_dir=root / "datasets",
            manifests_dir=root / "manifests",
            logs_dir=root / "logs",
        )
        self.repository.ensure_layout()

    def tearDown(self):
        self.temp_dir.cleanup()

    def import_one(self, content: bytes, total: int):
        return self.repository.import_parsed(
            survey_year=2026,
            form_code="07go_A",
            form_label="学生数",
            source_url="https://portal.niad.ac.jp/example.xlsx",
            source_filename="2026_07go_A.xlsx",
            content=content,
            parsed=parsed(total),
        )

    def test_publishes_immutable_raw_parquet_manifest_and_snapshot(self):
        result = self.import_one(b"first", 100)

        self.assertEqual(result.status, "imported")
        self.assertTrue(result.raw_file_path.exists())
        self.assertTrue(result.dataset_path.exists())
        self.assertTrue((Path(self.temp_dir.name) / "manifests" / "source_manifest.json").exists())
        frame = self.repository.read_database().load_active_dataframe([2026], ["07go_A"])
        self.assertEqual(int(frame.loc[0, "計_計"]), 100)
        manifest = json.loads(
            (Path(self.temp_dir.name) / "manifests" / "source_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        version = manifest["source_versions"][0]
        self.assertEqual(
            version["raw_file_path"],
            str(Path("raw_history") / "2026" / "07go_A" / result.raw_file_path.name),
        )
        self.assertEqual(
            version["dataset_path"],
            str(Path("datasets") / "2026" / "07go_A" / result.dataset_path.name),
        )
        self.assertNotIn(str(Path(self.temp_dir.name)), version["raw_file_path"])

    def test_same_content_reuses_versioned_files(self):
        first = self.import_one(b"first", 100)
        second = self.import_one(b"first", 100)

        self.assertEqual(second.status, "unchanged")
        self.assertEqual(first.raw_file_path, second.raw_file_path)
        self.assertEqual(first.dataset_path, second.dataset_path)
        self.assertEqual(len(list(first.raw_file_path.parent.glob("*.xlsx"))), 1)

    def test_batch_session_publishes_multiple_files_together(self):
        with self.repository.update_session() as session:
            for form_code, content, total in (
                ("07go_A", b"first", 100),
                ("08go_D", b"second", 200),
            ):
                session.import_parsed(
                    survey_year=2026,
                    form_code=form_code,
                    form_label=form_code,
                    source_url=f"https://portal.niad.ac.jp/{form_code}.xlsx",
                    source_filename=f"2026_{form_code}.xlsx",
                    content=content,
                    parsed=parsed(total),
                )

        summary = self.repository.read_database().get_active_summary()
        self.assertEqual(set(summary["form_code"]), {"07go_A", "08go_D"})
        self.assertEqual(len(list((Path(self.temp_dir.name) / "datasets").rglob("*.parquet"))), 2)


if __name__ == "__main__":
    unittest.main()
