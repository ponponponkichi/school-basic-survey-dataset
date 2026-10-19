from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterable

import pandas as pd

from .excel_reader import ParsedExcel


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS source_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_year INTEGER NOT NULL,
    form_code TEXT NOT NULL,
    form_label TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    raw_file_path TEXT NOT NULL DEFAULT '',
    dataset_path TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    last_checked_at TEXT NOT NULL,
    header_row_number INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    version_number INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    UNIQUE (survey_year, form_code, sha256)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_source_versions_one_active
ON source_versions(survey_year, form_code)
WHERE is_active = 1;

CREATE INDEX IF NOT EXISTS idx_source_versions_lookup
ON source_versions(form_code, survey_year, is_active);

CREATE TABLE IF NOT EXISTS column_definitions (
    source_version_id INTEGER NOT NULL,
    column_order INTEGER NOT NULL,
    source_column_name TEXT NOT NULL,
    export_column_name TEXT NOT NULL,
    PRIMARY KEY (source_version_id, column_order),
    FOREIGN KEY (source_version_id) REFERENCES source_versions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_version_id INTEGER NOT NULL,
    source_row_number INTEGER NOT NULL,
    row_hash TEXT NOT NULL,
    data_json TEXT NOT NULL,
    UNIQUE (source_version_id, source_row_number),
    FOREIGN KEY (source_version_id) REFERENCES source_versions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_records_source_version
ON records(source_version_id);

CREATE TABLE IF NOT EXISTS schema_comparisons (
    source_version_id INTEGER PRIMARY KEY,
    reference_source_version_id INTEGER NOT NULL,
    reference_year INTEGER NOT NULL,
    reference_version INTEGER NOT NULL,
    added_columns_json TEXT NOT NULL,
    removed_columns_json TEXT NOT NULL,
    reordered_columns_json TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    FOREIGN KEY (source_version_id) REFERENCES source_versions(id) ON DELETE CASCADE,
    FOREIGN KEY (reference_source_version_id) REFERENCES source_versions(id)
);
"""


@dataclass(frozen=True)
class SchemaChange:
    reference_year: int
    reference_version: int
    added_columns: tuple[str, ...]
    removed_columns: tuple[str, ...]
    reordered_columns: tuple[str, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.added_columns or self.removed_columns or self.reordered_columns)


@dataclass(frozen=True)
class ImportResult:
    status: str
    row_count: int
    version_number: int
    source_version_id: int
    schema_change: SchemaChange | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class Database:
    def __init__(self, path: Path, *, readonly: bool = False):
        self.path = Path(path)
        self.readonly = readonly

    def connect(self) -> sqlite3.Connection:
        if self.readonly:
            connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if self.readonly:
            connection.execute("PRAGMA query_only = ON")
        else:
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def session(self):
        """トランザクション終了後に、Windowsでも確実に接続を閉じる。"""
        connection = self.connect()
        try:
            yield connection
            if not self.readonly:
                connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        if self.readonly:
            raise RuntimeError("読み取り専用データベースは初期化できません。")
        with self.session() as connection:
            connection.executescript(SCHEMA)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(source_versions)").fetchall()
            }
            if "dataset_path" not in columns:
                connection.execute(
                    "ALTER TABLE source_versions ADD COLUMN dataset_path TEXT NOT NULL DEFAULT ''"
                )

    def import_dataframe(
        self,
        *,
        survey_year: int,
        form_code: str,
        form_label: str,
        source_url: str,
        source_filename: str,
        raw_file_path: Path,
        content: bytes,
        parsed: ParsedExcel,
    ) -> ImportResult:
        checksum = hashlib.sha256(content).hexdigest()
        now = _utc_now()
        with self.session() as connection:
            existing = connection.execute(
                """
                SELECT id, row_count, version_number, is_active
                FROM source_versions
                WHERE survey_year = ? AND form_code = ? AND sha256 = ?
                """,
                (survey_year, form_code, checksum),
            ).fetchone()
            if existing:
                status = "unchanged"
                if not existing["is_active"]:
                    connection.execute(
                        """
                        UPDATE source_versions SET is_active = 0
                        WHERE survey_year = ? AND form_code = ? AND is_active = 1
                        """,
                        (survey_year, form_code),
                    )
                    connection.execute(
                        "UPDATE source_versions SET is_active = 1 WHERE id = ?",
                        (existing["id"],),
                    )
                    status = "reactivated"
                connection.execute(
                    """
                    UPDATE source_versions
                    SET last_checked_at = ?, raw_file_path = ?,
                        form_label = ?, source_url = ?, source_filename = ?
                    WHERE id = ?
                    """,
                    (
                        now,
                        str(raw_file_path),
                        form_label,
                        source_url,
                        source_filename,
                        existing["id"],
                    ),
                )
                return ImportResult(
                    status=status,
                    row_count=existing["row_count"],
                    version_number=existing["version_number"],
                    source_version_id=existing["id"],
                )

            latest_version = connection.execute(
                """
                SELECT COALESCE(MAX(version_number), 0)
                FROM source_versions
                WHERE survey_year = ? AND form_code = ?
                """,
                (survey_year, form_code),
            ).fetchone()[0]

            reference = connection.execute(
                """
                SELECT id, survey_year, version_number
                FROM source_versions
                WHERE form_code = ? AND is_active = 1
                ORDER BY CASE WHEN survey_year = ? THEN 0 ELSE 1 END,
                         ABS(survey_year - ?), survey_year DESC
                LIMIT 1
                """,
                (form_code, survey_year, survey_year),
            ).fetchone()
            reference_columns: list[str] = []
            if reference:
                reference_columns = [
                    row["source_column_name"]
                    for row in connection.execute(
                        """
                        SELECT source_column_name
                        FROM column_definitions
                        WHERE source_version_id = ?
                        ORDER BY column_order
                        """,
                        (reference["id"],),
                    ).fetchall()
                ]

            connection.execute(
                """
                UPDATE source_versions SET is_active = 0
                WHERE survey_year = ? AND form_code = ? AND is_active = 1
                """,
                (survey_year, form_code),
            )
            cursor = connection.execute(
                """
                INSERT INTO source_versions (
                    survey_year, form_code, form_label, source_url, source_filename,
                    raw_file_path, sha256, fetched_at, imported_at, last_checked_at,
                    header_row_number, row_count, version_number, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    survey_year,
                    form_code,
                    form_label,
                    source_url,
                    source_filename,
                    str(raw_file_path),
                    checksum,
                    now,
                    now,
                    now,
                    parsed.header_row_number,
                    len(parsed.dataframe),
                    latest_version + 1,
                ),
            )
            source_version_id = int(cursor.lastrowid)

            connection.executemany(
                """
                INSERT INTO column_definitions (
                    source_version_id, column_order, source_column_name, export_column_name
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (source_version_id, index, source_name, export_name)
                    for index, (source_name, export_name) in enumerate(
                        zip(parsed.source_columns, parsed.export_columns)
                    )
                ],
            )

            schema_change = None
            if reference:
                new_columns = parsed.source_columns
                old_set = set(reference_columns)
                new_set = set(new_columns)
                added = tuple(column for column in new_columns if column not in old_set)
                removed = tuple(column for column in reference_columns if column not in new_set)
                old_common = [column for column in reference_columns if column in new_set]
                new_common = [column for column in new_columns if column in old_set]
                reordered = tuple(
                    column
                    for index, column in enumerate(new_common)
                    if index >= len(old_common) or old_common[index] != column
                )
                schema_change = SchemaChange(
                    reference_year=int(reference["survey_year"]),
                    reference_version=int(reference["version_number"]),
                    added_columns=added,
                    removed_columns=removed,
                    reordered_columns=reordered,
                )
                connection.execute(
                    """
                    INSERT INTO schema_comparisons (
                        source_version_id, reference_source_version_id,
                        reference_year, reference_version,
                        added_columns_json, removed_columns_json,
                        reordered_columns_json, detected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_version_id,
                        reference["id"],
                        schema_change.reference_year,
                        schema_change.reference_version,
                        json.dumps(added, ensure_ascii=False),
                        json.dumps(removed, ensure_ascii=False),
                        json.dumps(reordered, ensure_ascii=False),
                        now,
                    ),
                )

            record_rows = []
            for source_row_number, (_, row) in zip(
                parsed.source_row_numbers,
                parsed.dataframe.iterrows(),
            ):
                data = {column: _json_value(row[column]) for column in parsed.dataframe.columns}
                data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                row_hash = hashlib.sha256(data_json.encode("utf-8")).hexdigest()
                record_rows.append((source_version_id, source_row_number, row_hash, data_json))
            connection.executemany(
                """
                INSERT INTO records (source_version_id, source_row_number, row_hash, data_json)
                VALUES (?, ?, ?, ?)
                """,
                record_rows,
            )

            return ImportResult(
                status="imported",
                row_count=len(parsed.dataframe),
                version_number=latest_version + 1,
                source_version_id=source_version_id,
                schema_change=schema_change,
            )

    def get_recent_schema_changes(self, limit: int = 100) -> list[dict[str, Any]]:
        sql = """
            SELECT sv.survey_year, sv.form_code, sv.form_label, sv.version_number,
                   sc.reference_year, sc.reference_version,
                   sc.added_columns_json, sc.removed_columns_json,
                   sc.reordered_columns_json, sc.detected_at
            FROM schema_comparisons AS sc
            JOIN source_versions AS sv ON sv.id = sc.source_version_id
            ORDER BY sc.detected_at DESC, sv.form_code
            LIMIT ?
        """
        try:
            with self.session() as connection:
                rows = connection.execute(sql, (limit,)).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise
        output = []
        for row in rows:
            item = dict(row)
            item["added_columns"] = json.loads(item.pop("added_columns_json"))
            item["removed_columns"] = json.loads(item.pop("removed_columns_json"))
            item["reordered_columns"] = json.loads(item.pop("reordered_columns_json"))
            if item["added_columns"] or item["removed_columns"] or item["reordered_columns"]:
                output.append(item)
        return output

    def update_storage_paths(
        self,
        source_version_id: int,
        *,
        raw_file_path: Path,
        dataset_path: Path,
    ) -> None:
        with self.session() as connection:
            connection.execute(
                """
                UPDATE source_versions
                SET raw_file_path = ?, dataset_path = ?
                WHERE id = ?
                """,
                (str(raw_file_path), str(dataset_path), source_version_id),
            )

    def get_all_source_versions(self) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT survey_year, form_code, form_label, source_url,
                       source_filename, raw_file_path, dataset_path, sha256,
                       fetched_at, imported_at, last_checked_at,
                       header_row_number, row_count, version_number, is_active
                FROM source_versions
                ORDER BY survey_year, form_code, version_number
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_active_summary(self) -> pd.DataFrame:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT survey_year, form_code, form_label, row_count,
                       version_number, imported_at
                FROM source_versions
                WHERE is_active = 1
                ORDER BY survey_year DESC, form_code
                """
            ).fetchall()
        return pd.DataFrame([dict(row) for row in rows])

    @staticmethod
    def _placeholders(values: Iterable[Any]) -> str:
        return ",".join("?" for _ in values)

    def _active_record_rows(self, years: list[int], form_codes: list[str]) -> list[sqlite3.Row]:
        if not years or not form_codes:
            return []
        years = [int(year) for year in years]
        form_codes = [str(form_code) for form_code in form_codes]
        year_marks = self._placeholders(years)
        form_marks = self._placeholders(form_codes)
        sql = f"""
            SELECT sv.survey_year, sv.form_code, sv.source_filename,
                   sv.source_url, sv.imported_at, r.source_row_number, r.data_json
            FROM source_versions AS sv
            JOIN records AS r ON r.source_version_id = sv.id
            WHERE sv.is_active = 1
              AND sv.survey_year IN ({year_marks})
              AND sv.form_code IN ({form_marks})
            ORDER BY sv.form_code, sv.survey_year, r.source_row_number
        """
        with self.session() as connection:
            return connection.execute(sql, [*years, *form_codes]).fetchall()

    def load_active_dataframe(
        self,
        years: list[int],
        form_codes: list[str],
        school_name_query: str = "",
    ) -> pd.DataFrame:
        rows = []
        for row in self._active_record_rows(years, form_codes):
            data = json.loads(row["data_json"])
            output = {
                "調査年度": row["survey_year"],
                "様式コード": row["form_code"],
                "元ファイル名": row["source_filename"],
                "元データ行番号": row["source_row_number"],
                **data,
            }
            rows.append(output)
        dataframe = pd.DataFrame(rows)
        query = school_name_query.strip().casefold()
        if query and "学校名" in dataframe.columns:
            mask = dataframe["学校名"].fillna("").astype(str).str.casefold().str.contains(query, regex=False)
            dataframe = dataframe[mask].reset_index(drop=True)
        return dataframe

    def load_active_frames(self, years: list[int], form_codes: list[str]) -> dict[str, pd.DataFrame]:
        return {
            form_code: self.load_active_dataframe(years, [form_code])
            for form_code in form_codes
        }

    def get_source_manifest(self, years: list[int], form_codes: list[str]) -> pd.DataFrame:
        if not years or not form_codes:
            return pd.DataFrame()
        years = [int(year) for year in years]
        form_codes = [str(form_code) for form_code in form_codes]
        year_marks = self._placeholders(years)
        form_marks = self._placeholders(form_codes)
        sql = f"""
            SELECT survey_year AS 調査年度,
                   form_code AS 様式コード,
                   form_label AS 様式名,
                   source_filename AS 元ファイル名,
                   source_url AS 取得元URL,
                   sha256 AS SHA256,
                   header_row_number AS 見出し行,
                   row_count AS 行数,
                   version_number AS 版,
                   imported_at AS 取込日時
            FROM source_versions
            WHERE is_active = 1
              AND survey_year IN ({year_marks})
              AND form_code IN ({form_marks})
            ORDER BY survey_year DESC, form_code
        """
        with self.session() as connection:
            rows = connection.execute(sql, [*years, *form_codes]).fetchall()
        return pd.DataFrame([dict(row) for row in rows])

    def get_column_dictionary(self, years: list[int], form_codes: list[str]) -> pd.DataFrame:
        if not years or not form_codes:
            return pd.DataFrame()
        years = [int(year) for year in years]
        form_codes = [str(form_code) for form_code in form_codes]
        year_marks = self._placeholders(years)
        form_marks = self._placeholders(form_codes)
        sql = f"""
            SELECT sv.survey_year AS 調査年度,
                   sv.form_code AS 様式コード,
                   cd.column_order + 1 AS 列順,
                   cd.source_column_name AS 元の列名,
                   cd.export_column_name AS 出力列名
            FROM source_versions AS sv
            JOIN column_definitions AS cd ON cd.source_version_id = sv.id
            WHERE sv.is_active = 1
              AND sv.survey_year IN ({year_marks})
              AND sv.form_code IN ({form_marks})
            ORDER BY sv.form_code, sv.survey_year, cd.column_order
        """
        with self.session() as connection:
            rows = connection.execute(sql, [*years, *form_codes]).fetchall()
        return pd.DataFrame([dict(row) for row in rows])

    def create_backup_bytes(self) -> bytes:
        """WALの未反映を避け、ダウンロード用の一貫したDBコピーを返す。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            backup_path = Path(temp_dir) / self.path.name
            source = self.connect()
            destination = sqlite3.connect(backup_path)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
            return backup_path.read_bytes()

