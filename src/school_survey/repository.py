from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any
import uuid

from .database import Database, ImportResult, SchemaChange
from .excel_reader import ParsedExcel


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class StoredImport:
    status: str
    row_count: int
    version_number: int
    source_version_id: int
    sha256: str
    raw_file_path: Path
    dataset_path: Path
    schema_change: SchemaChange | None = None


class RepositoryUpdateSession:
    """複数ファイルを1つの一時DBへ取り込み、最後にまとめて公開する。"""

    def __init__(self, repository: "DataRepository", database: Database, temp_dir: Path):
        self.repository = repository
        self.database = database
        self.temp_dir = temp_dir

    def import_parsed(self, **kwargs: Any) -> StoredImport:
        return self.repository._import_into_database(
            temporary_database=self.database,
            temp_dir=self.temp_dir,
            **kwargs,
        )


class DataRepository:
    """Gドライブには完成物だけを保存し、更新処理はローカル一時領域で行う。"""

    def __init__(
        self,
        *,
        data_dir: Path,
        database_path: Path,
        raw_history_dir: Path,
        datasets_dir: Path,
        manifests_dir: Path,
        logs_dir: Path,
        legacy_database_path: Path | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.database_path = Path(database_path)
        self.raw_history_dir = Path(raw_history_dir)
        self.datasets_dir = Path(datasets_dir)
        self.manifests_dir = Path(manifests_dir)
        self.logs_dir = Path(logs_dir)
        self.legacy_database_path = Path(legacy_database_path) if legacy_database_path else None

    @staticmethod
    def _atomic_write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.{uuid.uuid4().hex}.partial")
        try:
            partial.write_bytes(content)
            if hashlib.sha256(partial.read_bytes()).digest() != hashlib.sha256(content).digest():
                raise OSError(f"保存後の検証に失敗しました: {path}")
            os.replace(partial, path)
        finally:
            if partial.exists():
                partial.unlink()

    @classmethod
    def _atomic_copy(cls, source: Path, destination: Path) -> None:
        cls._atomic_write_bytes(destination, source.read_bytes())

    def ensure_layout(self) -> None:
        for directory in (
            self.raw_history_dir,
            self.datasets_dir,
            self.manifests_dir,
            self.logs_dir,
            self.database_path.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        if self.database_path.exists():
            return
        if self.legacy_database_path and self.legacy_database_path.exists():
            self._atomic_copy(self.legacy_database_path, self.database_path)
            return

        with tempfile.TemporaryDirectory(prefix="school-survey-init-") as temp_dir:
            temporary_database = Path(temp_dir) / self.database_path.name
            database = Database(temporary_database)
            database.initialize()
            self._atomic_write_bytes(self.database_path, database.create_backup_bytes())

    def read_database(self) -> Database:
        self.ensure_layout()
        return Database(self.database_path, readonly=True)

    def _archive_paths(
        self,
        *,
        survey_year: int,
        form_code: str,
        version_number: int,
        checksum: str,
        source_filename: str,
    ) -> tuple[Path, Path]:
        stem = f"v{version_number:03d}_sha256-{checksum[:12]}_{source_filename}"
        raw_path = self.raw_history_dir / str(survey_year) / form_code / stem
        dataset_name = f"v{version_number:03d}_sha256-{checksum[:12]}.parquet"
        dataset_path = self.datasets_dir / str(survey_year) / form_code / dataset_name
        return raw_path, dataset_path

    def import_parsed(
        self,
        *,
        survey_year: int,
        form_code: str,
        form_label: str,
        source_url: str,
        source_filename: str,
        content: bytes,
        parsed: ParsedExcel,
    ) -> StoredImport:
        with self.update_session() as session:
            return session.import_parsed(
                survey_year=survey_year,
                form_code=form_code,
                form_label=form_label,
                source_url=source_url,
                source_filename=source_filename,
                content=content,
                parsed=parsed,
            )

    @contextmanager
    def update_session(self):
        """更新単位ごとにDBを1回だけGドライブへ公開する。"""
        self.ensure_layout()
        with tempfile.TemporaryDirectory(prefix="school-survey-update-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            temporary_database_path = temp_dir / self.database_path.name
            shutil.copy2(self.database_path, temporary_database_path)
            temporary_database = Database(temporary_database_path)
            temporary_database.initialize()
            yield RepositoryUpdateSession(self, temporary_database, temp_dir)
            database_bytes = temporary_database.create_backup_bytes()
            versions = temporary_database.get_all_source_versions()
            self._atomic_write_bytes(self.database_path, database_bytes)
            self._write_manifest(versions)

    def _import_into_database(
        self,
        *,
        temporary_database: Database,
        temp_dir: Path,
        survey_year: int,
        form_code: str,
        form_label: str,
        source_url: str,
        source_filename: str,
        content: bytes,
        parsed: ParsedExcel,
    ) -> StoredImport:
        checksum = hashlib.sha256(content).hexdigest()
        result: ImportResult = temporary_database.import_dataframe(
            survey_year=survey_year,
            form_code=form_code,
            form_label=form_label,
            source_url=source_url,
            source_filename=source_filename,
            raw_file_path=Path(""),
            content=content,
            parsed=parsed,
        )
        raw_path, dataset_path = self._archive_paths(
            survey_year=survey_year,
            form_code=form_code,
            version_number=result.version_number,
            checksum=checksum,
            source_filename=source_filename,
        )

        if not raw_path.exists():
            self._atomic_write_bytes(raw_path, content)
        if not dataset_path.exists():
            parquet_temp = temp_dir / f"{uuid.uuid4().hex}.parquet"
            parsed.dataframe.convert_dtypes().to_parquet(parquet_temp, index=False)
            self._atomic_copy(parquet_temp, dataset_path)

        temporary_database.update_storage_paths(
            result.source_version_id,
            raw_file_path=raw_path.relative_to(self.data_dir),
            dataset_path=dataset_path.relative_to(self.data_dir),
        )
        return StoredImport(
            status=result.status,
            row_count=result.row_count,
            version_number=result.version_number,
            source_version_id=result.source_version_id,
            sha256=checksum,
            raw_file_path=raw_path,
            dataset_path=dataset_path,
            schema_change=result.schema_change,
        )

    def _write_manifest(self, versions: list[dict[str, Any]]) -> None:
        payload = {
            "format_version": 1,
            "generated_at": _utc_now(),
            "database_snapshot": str(self.database_path.relative_to(self.data_dir)),
            "source_versions": versions,
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self._atomic_write_bytes(self.manifests_dir / "source_manifest.json", content)

    def write_sync_log(
        self,
        *,
        started_at: str,
        trigger: str,
        items: list[dict[str, Any]],
    ) -> Path:
        finished_at = _utc_now()
        run_id = started_at.replace(":", "").replace("-", "").replace("+00:00", "Z")
        payload = {
            "format_version": 1,
            "run_id": run_id,
            "trigger": trigger,
            "started_at": started_at,
            "finished_at": finished_at,
            "counts": {
                status: sum(1 for item in items if item.get("status") == status)
                for status in ("imported", "unchanged", "reactivated", "error")
            },
            "items": items,
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        run_path = self.logs_dir / f"sync_{run_id}.json"
        self._atomic_write_bytes(run_path, content)
        self._atomic_write_bytes(self.logs_dir / "latest.json", content)
        return run_path

    def read_latest_log(self) -> dict[str, Any] | None:
        path = self.logs_dir / "latest.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def list_sync_logs(self, limit: int = 20) -> list[dict[str, Any]]:
        logs: list[dict[str, Any]] = []
        for path in sorted(self.logs_dir.glob("sync_*.json"), reverse=True):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            item["log_filename"] = path.name
            logs.append(item)
            if len(logs) >= limit:
                break
        return logs
