from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from school_survey.catalog import DownloadedFile, FormFile
from school_survey.excel_reader import ParsedExcel
from school_survey.repository import DataRepository
from school_survey.sync_service import successful_monthly_run_exists, sync_tasks


class SyncServiceTests(unittest.TestCase):
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

    def test_sync_tasks_imports_and_writes_log(self):
        form = FormFile(
            form_code="07go_A",
            form_label="学生数",
            url="https://portal.niad.ac.jp/example.xlsx",
            filename="2026_07go_A.xlsx",
        )
        parsed = ParsedExcel(
            dataframe=pd.DataFrame([{"学校名": "テスト大学", "学生数": 100}]),
            header_row_number=4,
            source_row_numbers=[5],
            source_columns=["学校名", "学生数"],
            export_columns=["学校名", "学生数"],
        )
        downloaded = DownloadedFile(form.url, form.filename, b"content")
        with (
            patch("school_survey.sync_service.download_form_file", return_value=downloaded),
            patch("school_survey.sync_service.read_excel_bytes", return_value=parsed),
        ):
            result = sync_tasks(
                self.repository,
                [(2026, form)],
                trigger="scheduled_monthly",
            )

        self.assertEqual(result.error_count, 0)
        self.assertEqual(result.items[0]["status"], "imported")
        self.assertTrue(result.log_path.exists())

    def test_monthly_run_requires_success_in_same_month(self):
        logs = [
            {
                "trigger": "scheduled_monthly",
                "started_at": "2026-10-02T00:00:00+00:00",
                "counts": {"error": 0},
            }
        ]
        self.assertTrue(successful_monthly_run_exists(logs, year=2026, month=10))
        self.assertFalse(successful_monthly_run_exists(logs, year=2026, month=11))
        logs[0]["counts"]["error"] = 1
        self.assertFalse(successful_monthly_run_exists(logs, year=2026, month=10))

    def test_monthly_run_uses_local_month_for_utc_log(self):
        local_timezone = datetime.now().astimezone().tzinfo
        local_start = datetime(2026, 10, 1, 0, 30, tzinfo=local_timezone)
        logs = [
            {
                "trigger": "scheduled_monthly",
                "started_at": local_start.astimezone(timezone.utc).isoformat(),
                "counts": {"error": 0},
            }
        ]

        self.assertTrue(successful_monthly_run_exists(logs, year=2026, month=10))


if __name__ == "__main__":
    unittest.main()
