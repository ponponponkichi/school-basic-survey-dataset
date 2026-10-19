from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st


ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from school_survey.catalog import (
    CatalogError,
    FormFile,
    download_form_file,
    fetch_catalog,
    fetch_year_forms,
)
from school_survey.excel_reader import ExcelReadError, read_excel_bytes
from school_survey.exports import build_csv_zip, build_excel_workbook


MAX_FILES_PER_REQUEST = 20


@st.cache_data(ttl=3600, show_spinner=False)
def load_catalog() -> dict[int, str]:
    return fetch_catalog()


@st.cache_data(ttl=3600, show_spinner=False)
def load_year_forms(url: str) -> list[FormFile]:
    return fetch_year_forms(url)


st.set_page_config(page_title="学校基本調査データセット", layout="wide")
st.title("学校基本調査データセット")
st.caption("大学改革支援・学位授与機構の公開データから、必要な年度・様式・項目を取得します。")
st.info(
    "この公開用画面は、利用時点の公式Excelを直接読み取ります。"
    "履歴データや学内限定データは保存・使用しません。"
)

try:
    catalog = load_catalog()
except CatalogError as exc:
    catalog = {}
    st.error(f"公式サイトから年度一覧を取得できませんでした: {exc}")

years = sorted(catalog, reverse=True)
selected_years = st.multiselect("年度", years, default=years[:1])
forms_by_year: dict[int, list[FormFile]] = {}
form_labels: dict[str, str] = {}
for year in selected_years:
    try:
        forms_by_year[year] = load_year_forms(catalog[year])
        for form in forms_by_year[year]:
            form_labels.setdefault(form.form_code, form.form_label)
    except CatalogError as exc:
        st.error(f"{year}年度の様式一覧を取得できませんでした: {exc}")

form_codes = sorted(form_labels)
recommended = [code for code in ("07go_A", "08go_D") if code in form_codes]
selected_forms = st.multiselect(
    "様式",
    form_codes,
    default=recommended or form_codes[:1],
    format_func=lambda code: f"{code}  {form_labels.get(code, code)}",
)

tasks = [
    (year, form)
    for year in sorted(selected_years)
    for form in forms_by_year.get(year, [])
    if form.form_code in set(selected_forms)
]
st.caption(f"取得対象: {len(tasks):,}ファイル（1回につき最大{MAX_FILES_PER_REQUEST}ファイル）")

if st.button("公式データを取得", type="primary", disabled=not catalog):
    if not tasks:
        st.warning("年度と様式を1つ以上選択してください。")
    elif len(tasks) > MAX_FILES_PER_REQUEST:
        st.warning(
            f"クラウド版では1回{MAX_FILES_PER_REQUEST}ファイルまでです。"
            "年度または様式を減らしてください。全件更新と履歴保存はローカル版を使います。"
        )
    else:
        progress = st.progress(0)
        frames_by_form: dict[str, list[pd.DataFrame]] = {}
        manifest_rows = []
        dictionary_rows = []
        errors = []
        for index, (year, form) in enumerate(tasks, start=1):
            try:
                downloaded = download_form_file(form)
                parsed = read_excel_bytes(downloaded.content, downloaded.filename)
                frame = parsed.dataframe.copy()
                frame.insert(0, "元データ行番号", parsed.source_row_numbers)
                frame.insert(0, "元ファイル名", downloaded.filename)
                frame.insert(0, "様式コード", form.form_code)
                frame.insert(0, "調査年度", year)
                frames_by_form.setdefault(form.form_code, []).append(frame)
                manifest_rows.append(
                    {
                        "調査年度": year,
                        "様式コード": form.form_code,
                        "様式名": form.form_label,
                        "元ファイル名": downloaded.filename,
                        "取得元URL": downloaded.url,
                        "見出し行": parsed.header_row_number,
                        "行数": len(parsed.dataframe),
                    }
                )
                dictionary_rows.extend(
                    {
                        "調査年度": year,
                        "様式コード": form.form_code,
                        "列順": order,
                        "元の列名": source,
                        "出力列名": export,
                    }
                    for order, (source, export) in enumerate(
                        zip(parsed.source_columns, parsed.export_columns), start=1
                    )
                )
            except (CatalogError, ExcelReadError, OSError, ValueError) as exc:
                errors.append(f"{year}年度 {form.form_code}: {exc}")
            progress.progress(index / len(tasks))

        st.session_state["cloud_frames"] = {
            code: pd.concat(parts, ignore_index=True, sort=False)
            for code, parts in frames_by_form.items()
        }
        st.session_state["cloud_manifest"] = pd.DataFrame(manifest_rows)
        st.session_state["cloud_dictionary"] = pd.DataFrame(dictionary_rows)
        st.session_state["cloud_errors"] = errors

if st.session_state.get("cloud_errors"):
    with st.expander("取得できなかったファイル", expanded=True):
        for message in st.session_state["cloud_errors"]:
            st.error(message)

frames = st.session_state.get("cloud_frames", {})
if frames:
    st.subheader("出力内容")
    school_query = st.text_input("学校名で絞り込み（部分一致）", placeholder="例：神戸大学")
    metadata_columns = ["調査年度", "様式コード", "元ファイル名", "元データ行番号"]
    output_frames = {}
    selected_columns_by_form = {}
    for form_code, frame in frames.items():
        data_columns = [column for column in frame.columns if column not in metadata_columns]
        with st.expander(f"{form_code} の出力項目", expanded=len(frames) <= 3):
            selected_columns = st.multiselect(
                "項目",
                data_columns,
                default=data_columns,
                key=f"cloud_columns_{form_code}",
            )
        selected_columns_by_form[form_code] = selected_columns
        filtered = frame
        if school_query.strip() and "学校名" in filtered.columns:
            mask = (
                filtered["学校名"]
                .fillna("")
                .astype(str)
                .str.contains(school_query.strip(), case=False, regex=False)
            )
            filtered = filtered[mask]
        output_frames[form_code] = filtered[
            [column for column in metadata_columns if column in filtered.columns]
            + selected_columns
        ].reset_index(drop=True)

    manifest = st.session_state["cloud_manifest"]
    dictionary = st.session_state["cloud_dictionary"]
    if not dictionary.empty:
        dictionary = dictionary[
            dictionary.apply(
                lambda row: row["出力列名"]
                in selected_columns_by_form.get(row["様式コード"], []),
                axis=1,
            )
        ].reset_index(drop=True)

    total_rows = sum(len(frame) for frame in output_frames.values())
    st.write(f"出力行数: {total_rows:,}行")
    excel_bytes = build_excel_workbook(output_frames, manifest, dictionary)
    csv_bytes = build_csv_zip(output_frames, manifest, dictionary)
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Excelデータセット",
            excel_bytes,
            file_name="school_basic_survey_dataset.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
    with col2:
        st.download_button(
            "CSVデータセット（ZIP）",
            csv_bytes,
            file_name="school_basic_survey_dataset_csv.zip",
            mime="application/zip",
            width="stretch",
        )

st.divider()
st.caption(
    "出典：大学改革支援・学位授与機構「大学基本情報」 "
    "https://portal.niad.ac.jp/ptrt/table.html を加工して作成"
)
