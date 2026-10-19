from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .catalog import CatalogError, FormFile, download_form_file
from .excel_reader import ExcelReadError, read_excel_bytes
from .repository import DataRepository


ProgressCallback = Callable[[int, int, int, FormFile], None]


@dataclass(frozen=True)
class SyncRunResult:
    items: list[dict]
    log_path: Path

    @property
    def error_count(self) -> int:
        return sum(item.get("status") == "error" for item in self.items)


def sync_tasks(
    repository: DataRepository,
    tasks: list[tuple[int, FormFile]],
    *,
    trigger: str,
    progress_callback: ProgressCallback | None = None,
) -> SyncRunResult:
    """公式ファイル群を確認し、1つの完成済みDBとしてまとめて公開する。"""
    if not tasks:
        raise ValueError("更新対象のファイルがありません。")

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    items: list[dict] = []
    with repository.update_session() as update_session:
        for index, (year, form) in enumerate(tasks, start=1):
            if progress_callback:
                progress_callback(index, len(tasks), year, form)
            try:
                downloaded = download_form_file(form)
                parsed = read_excel_bytes(downloaded.content, downloaded.filename)
                result = update_session.import_parsed(
                    survey_year=year,
                    form_code=form.form_code,
                    form_label=form.form_label,
                    source_url=downloaded.url,
                    source_filename=downloaded.filename,
                    content=downloaded.content,
                    parsed=parsed,
                )
                item = {
                    "survey_year": year,
                    "form_code": form.form_code,
                    "form_label": form.form_label,
                    "status": result.status,
                    "row_count": result.row_count,
                    "version_number": result.version_number,
                    "sha256": result.sha256,
                    "raw_file_path": str(result.raw_file_path.relative_to(repository.data_dir)),
                    "dataset_path": str(result.dataset_path.relative_to(repository.data_dir)),
                }
                if result.schema_change and result.schema_change.has_changes:
                    item["schema_change"] = {
                        "reference_year": result.schema_change.reference_year,
                        "reference_version": result.schema_change.reference_version,
                        "added_columns": list(result.schema_change.added_columns),
                        "removed_columns": list(result.schema_change.removed_columns),
                        "reordered_columns": list(result.schema_change.reordered_columns),
                    }
                items.append(item)
            except (CatalogError, ExcelReadError, OSError, ValueError) as exc:
                items.append(
                    {
                        "survey_year": year,
                        "form_code": form.form_code,
                        "form_label": form.form_label,
                        "status": "error",
                        "message": str(exc),
                    }
                )

    log_path = repository.write_sync_log(
        started_at=started_at,
        trigger=trigger,
        items=items,
    )
    return SyncRunResult(items=items, log_path=log_path)


def successful_monthly_run_exists(
    logs: list[dict],
    *,
    year: int,
    month: int,
) -> bool:
    local_timezone = datetime.now().astimezone().tzinfo
    for log in logs:
        if log.get("trigger") != "scheduled_monthly":
            continue
        if int(log.get("counts", {}).get("error", 0)) != 0:
            continue
        try:
            started = datetime.fromisoformat(str(log.get("started_at", "")))
            if started.tzinfo is None:
                continue
            local_started = started.astimezone(local_timezone)
        except ValueError:
            continue
        if local_started.year == year and local_started.month == month:
            return True
    return False
