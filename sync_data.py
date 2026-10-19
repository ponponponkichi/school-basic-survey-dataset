from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from school_survey.catalog import fetch_catalog, fetch_year_forms
from school_survey.config import (
    DATA_DIR,
    DATABASE_PATH,
    DATASETS_DIR,
    LEGACY_DATABASE_PATH,
    LOGS_DIR,
    MANIFESTS_DIR,
    RAW_HISTORY_DIR,
)
from school_survey.repository import DataRepository
from school_survey.sync_service import successful_monthly_run_exists, sync_tasks


def repository() -> DataRepository:
    return DataRepository(
        data_dir=DATA_DIR,
        database_path=DATABASE_PATH,
        raw_history_dir=RAW_HISTORY_DIR,
        datasets_dir=DATASETS_DIR,
        manifests_dir=MANIFESTS_DIR,
        logs_dir=LOGS_DIR,
        legacy_database_path=LEGACY_DATABASE_PATH,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="大学基本情報の公式Excelを画面なしで更新します。"
    )
    parser.add_argument(
        "--monthly",
        action="store_true",
        help="今月の月次更新が成功済みなら何もせず終了します。",
    )
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        help="指定年度だけを更新します。省略時は掲載中の全年度です。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_repository = repository()
    data_repository.ensure_layout()

    now = datetime.now().astimezone()
    if args.monthly and successful_monthly_run_exists(
        data_repository.list_sync_logs(limit=40), year=now.year, month=now.month
    ):
        print(f"{now:%Y-%m}: 月次更新は成功済みです。")
        return 0

    catalog = fetch_catalog()
    target_years = sorted(args.years if args.years else catalog)
    unknown_years = [year for year in target_years if year not in catalog]
    if unknown_years:
        print(f"公式サイトにない年度です: {unknown_years}", file=sys.stderr)
        return 2

    tasks = []
    for year in target_years:
        forms = fetch_year_forms(catalog[year])
        tasks.extend((year, form) for form in forms)

    print(f"更新対象: {len(target_years)}年度、{len(tasks)}ファイル")

    def show_progress(index, total, year, form):
        print(f"[{index}/{total}] {year}年度 {form.form_code}", flush=True)

    run = sync_tasks(
        data_repository,
        tasks,
        trigger="scheduled_monthly" if args.monthly else "command_line",
        progress_callback=show_progress,
    )
    counts = {
        status: sum(item.get("status") == status for item in run.items)
        for status in ("imported", "unchanged", "reactivated", "error")
    }
    print(
        "完了: "
        f"更新 {counts['imported']} / 変更なし {counts['unchanged']} / "
        f"再有効化 {counts['reactivated']} / エラー {counts['error']}"
    )
    print(f"ログ: {run.log_path}")
    return 1 if run.error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
