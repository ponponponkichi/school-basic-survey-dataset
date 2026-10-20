from __future__ import annotations

import pandas as pd


BI_FORM_CODE = "08go_D"

BI_DIMENSION_COLUMNS = [
    "調査年度",
    "学校コード",
    "学校名",
    "所在地",
    "学部番号",
    "学部名",
    "学科",
    "符号",
    "昼夜別",
    "課程別",
    "元ファイル名",
    "元データ行番号",
    "取得元URL",
]

BI_METRIC_COLUMNS = {
    "学生数": {"男": "計_男", "女": "計_女"},
    "志願者数": {"男": "志願者_男", "女": "志願者_女"},
    "入学者数": {"男": "入学者_男", "女": "入学者_女"},
}


def _is_total_row(dataframe: pd.DataFrame) -> pd.Series:
    department = dataframe.get("学科", pd.Series("", index=dataframe.index, dtype="object"))
    symbol = dataframe.get("符号", pd.Series("", index=dataframe.index, dtype="object"))
    return department.fillna("").astype(str).str.strip().eq("計") | symbol.fillna("").astype(str).str.strip().eq("9999")


def build_bi_long(dataframe: pd.DataFrame) -> pd.DataFrame:
    """08go_DをBIツール向けの縦長データへ変換する。

    学科明細と学部計は同じ元ファイルに共存するため、集計区分を必ず付与する。
    """
    if dataframe.empty:
        return pd.DataFrame(
            columns=BI_DIMENSION_COLUMNS + ["集計区分", "指標", "性別", "値"]
        )
    if "調査年度" not in dataframe.columns:
        raise ValueError("BIデータには調査年度が必要です。")

    dimensions = [column for column in BI_DIMENSION_COLUMNS if column in dataframe.columns]
    base = dataframe[dimensions].copy()
    base["調査年度"] = pd.to_numeric(base["調査年度"], errors="coerce").astype("Int64")
    base["集計区分"] = _is_total_row(dataframe).map({True: "学部計", False: "学科明細"})

    parts: list[pd.DataFrame] = []
    for metric, sex_columns in BI_METRIC_COLUMNS.items():
        numeric_by_sex: dict[str, pd.Series] = {}
        for sex, source_column in sex_columns.items():
            if source_column in dataframe.columns:
                numeric_by_sex[sex] = pd.to_numeric(dataframe[source_column], errors="coerce")
            else:
                numeric_by_sex[sex] = pd.Series(pd.NA, index=dataframe.index, dtype="Float64")

        total = pd.concat(numeric_by_sex.values(), axis=1).sum(axis=1, min_count=1)
        for sex, values in {**numeric_by_sex, "合計": total}.items():
            part = base.copy()
            part["指標"] = metric
            part["性別"] = sex
            part["値"] = values
            parts.append(part)

    result = pd.concat(parts, ignore_index=True)
    result = result.dropna(subset=["調査年度", "値"]).reset_index(drop=True)
    result["値"] = pd.to_numeric(result["値"], errors="coerce")
    integer_values = result["値"].dropna().mod(1).eq(0).all()
    if integer_values:
        result["値"] = result["値"].astype("Int64")
    return result


def build_looker_studio_csv(dataframe: pd.DataFrame) -> bytes:
    """Looker StudioやGoogleスプレッドシートで扱いやすいCSVを返す。"""
    return dataframe.to_csv(index=False).encode("utf-8-sig")
