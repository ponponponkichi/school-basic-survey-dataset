from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd
import xlrd


class ExcelReadError(RuntimeError):
    """公式Excelを安全に読み取れなかったときの例外。"""


@dataclass(frozen=True)
class ParsedExcel:
    dataframe: pd.DataFrame
    header_row_number: int
    source_row_numbers: list[int]
    source_columns: list[str]
    export_columns: list[str]


COLUMN_ALIASES = {
    "年度(西暦)": "年度",
    "西暦年度": "年度",
    "大学": "学校名",
    "大学名": "学校名",
}


def _hidden_header_index(content: bytes, suffix: str) -> int | None:
    if suffix == ".xlsx":
        workbook = openpyxl.load_workbook(BytesIO(content), data_only=True, read_only=False)
        try:
            sheet = workbook.active
            return next(
                (row_index - 1 for row_index in range(1, sheet.max_row + 1) if sheet.row_dimensions[row_index].hidden),
                None,
            )
        finally:
            workbook.close()
    if suffix == ".xls":
        book = xlrd.open_workbook(file_contents=content, formatting_info=True)
        sheet = book.sheet_by_index(0)
        return next(
            (
                row_index
                for row_index in range(sheet.nrows)
                if row_index in sheet.rowinfo_map and sheet.rowinfo_map[row_index].hidden
            ),
            None,
        )
    return None


def _clean_column_name(value: Any, position: int) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return f"名称未設定_{position + 1}"
    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    return text or f"名称未設定_{position + 1}"


def _unique_names(names: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    unique: list[str] = []
    for name in names:
        count = counts.get(name, 0) + 1
        counts[name] = count
        unique.append(name if count == 1 else f"{name}_{count}")
    return unique


def _normalize_identifier(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_excel_bytes(content: bytes, filename: str) -> ParsedExcel:
    """非表示の機械判読用見出しを使って公式Excelを読み取る。"""
    suffix = Path(filename).suffix.casefold()
    if suffix not in {".xls", ".xlsx"}:
        raise ExcelReadError(f"未対応のファイル形式です: {suffix}")
    try:
        header_index = _hidden_header_index(content, suffix)
        if header_index is None:
            raise ExcelReadError("非表示の機械判読用見出し行が見つかりません。")
        engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
        dataframe = pd.read_excel(
            BytesIO(content),
            header=header_index,
            engine=engine,
            dtype=object,
        )
    except ExcelReadError:
        raise
    except Exception as exc:
        raise ExcelReadError(f"{filename} を読み取れませんでした。") from exc

    dataframe = dataframe.dropna(how="all").dropna(how="all", axis=1)
    if dataframe.empty:
        raise ExcelReadError(f"{filename} にデータ行がありません。")

    source_row_numbers = [header_index + 2 + int(index) for index in dataframe.index]
    source_columns = [_clean_column_name(value, index) for index, value in enumerate(dataframe.columns)]
    export_columns = _unique_names([COLUMN_ALIASES.get(name, name) for name in source_columns])
    dataframe.columns = export_columns
    dataframe = dataframe.reset_index(drop=True)

    for column in dataframe.columns:
        if "コード" in column or "番号" in column:
            dataframe[column] = dataframe[column].map(_normalize_identifier)

    return ParsedExcel(
        dataframe=dataframe,
        header_row_number=header_index + 1,
        source_row_numbers=source_row_numbers,
        source_columns=source_columns,
        export_columns=export_columns,
    )

