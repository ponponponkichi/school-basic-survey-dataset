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
from school_survey.bi import BI_FORM_CODE, build_bi_long, build_looker_studio_csv


MAX_FILES_PER_REQUEST = 20


@st.cache_data(ttl=3600, show_spinner=False)
def load_catalog() -> dict[int, str]:
    return fetch_catalog()


@st.cache_data(ttl=3600, show_spinner=False)
def load_year_forms(url: str) -> list[FormFile]:
    return fetch_year_forms(url)


@st.cache_data(ttl=3600, show_spinner=False)
def load_form_dataset(year: int, form: FormFile) -> pd.DataFrame:
    """公式Excelを取得し、出典を追跡できる列を付けて返す。"""
    downloaded = download_form_file(form)
    parsed = read_excel_bytes(downloaded.content, downloaded.filename)
    frame = parsed.dataframe.copy()
    frame.insert(0, "取得元URL", downloaded.url)
    frame.insert(0, "元データ行番号", parsed.source_row_numbers)
    frame.insert(0, "元ファイル名", downloaded.filename)
    frame.insert(0, "様式コード", form.form_code)
    frame.insert(0, "調査年度", year)
    return frame


def _format_people(value: int | float) -> str:
    if pd.isna(value):
        return "－"
    return f"{int(value):,}人"


def render_bi_dashboard(catalog: dict[int, str]) -> None:
    st.header("BIダッシュボード")
    st.write(
        "大学基本情報の「在籍者数等（08go_D）」を使い、年度推移・大学比較・学部構成を確認できます。"
    )
    st.info(
        "初期設定は「学部計」です。学部計と学科明細を同時に足すと二重集計になるため、"
        "画面では必ずどちらか一方を選びます。"
    )

    years = sorted(catalog, reverse=True)
    default_years = years[: min(5, len(years))]
    selected_years = st.multiselect(
        "読み込む年度",
        years,
        default=default_years,
        key="bi_source_years",
        help="複数年度を選ぶと推移を比較できます。最初は直近5年度です。",
    )

    if st.button("BIデータを読み込む", type="primary", disabled=not selected_years):
        progress = st.progress(0)
        frames: list[pd.DataFrame] = []
        errors: list[str] = []
        ordered_years = sorted(selected_years)
        for index, year in enumerate(ordered_years, start=1):
            try:
                forms = load_year_forms(catalog[year])
                form = next((item for item in forms if item.form_code == BI_FORM_CODE), None)
                if form is None:
                    raise CatalogError(f"{BI_FORM_CODE} が掲載されていません。")
                frames.append(load_form_dataset(year, form))
            except (CatalogError, ExcelReadError, OSError, ValueError) as exc:
                errors.append(f"{year}年度: {exc}")
            progress.progress(index / len(ordered_years))

        if frames:
            source = pd.concat(frames, ignore_index=True, sort=False)
            st.session_state["bi_data"] = build_bi_long(source)
            st.session_state["bi_loaded_years"] = ordered_years
        else:
            st.session_state.pop("bi_data", None)
            st.session_state.pop("bi_loaded_years", None)
        st.session_state["bi_errors"] = errors

    if st.session_state.get("bi_errors"):
        with st.expander("読み込めなかった年度", expanded=True):
            for message in st.session_state["bi_errors"]:
                st.error(message)

    bi_data = st.session_state.get("bi_data")
    if bi_data is None or bi_data.empty:
        st.caption("年度を選び、「BIデータを読み込む」を押してください。")
        return

    loaded_years = st.session_state.get("bi_loaded_years", [])
    st.success(
        f"{min(loaded_years)}～{max(loaded_years)}年度のデータを読み込みました。"
        if loaded_years
        else "BIデータを読み込みました。"
    )

    available_years = sorted(bi_data["調査年度"].dropna().astype(int).unique(), reverse=True)
    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
    with filter_col1:
        display_years = st.multiselect(
            "表示年度", available_years, default=available_years, key="bi_display_years"
        )
    with filter_col2:
        metric = st.selectbox("指標", ["学生数", "志願者数", "入学者数"])
    with filter_col3:
        sex = st.selectbox("性別", ["合計", "男", "女"])
    with filter_col4:
        aggregate_level = st.selectbox("集計区分", ["学部計", "学科明細"])

    base = bi_data[
        bi_data["調査年度"].astype(int).isin(display_years)
        & bi_data["指標"].eq(metric)
        & bi_data["集計区分"].eq(aggregate_level)
    ].copy()

    schools = sorted(base["学校名"].dropna().astype(str).unique()) if "学校名" in base else []
    selected_schools = st.multiselect(
        "大学",
        schools,
        placeholder="未選択の場合は全大学",
        key="bi_schools",
    )
    if selected_schools:
        base = base[base["学校名"].isin(selected_schools)]

    faculties = sorted(base["学部名"].dropna().astype(str).unique()) if "学部名" in base else []
    selected_faculties = st.multiselect(
        "学部",
        faculties,
        placeholder="未選択の場合は全学部",
        key="bi_faculties",
    )
    if selected_faculties:
        base = base[base["学部名"].isin(selected_faculties)]

    filtered = base[base["性別"].eq(sex)].copy()
    if filtered.empty:
        st.warning("現在の条件に該当するデータがありません。")
    else:
        latest_year = int(filtered["調査年度"].max())
        latest = filtered[filtered["調査年度"].astype(int).eq(latest_year)]
        latest_total = pd.to_numeric(latest["値"], errors="coerce").sum()
        university_count = latest["学校コード"].nunique() if "学校コード" in latest else 0

        women = base[
            base["調査年度"].astype(int).eq(latest_year) & base["性別"].eq("女")
        ]["値"].sum()
        all_people = base[
            base["調査年度"].astype(int).eq(latest_year) & base["性別"].eq("合計")
        ]["値"].sum()
        women_share = (women / all_people * 100) if all_people else None

        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        kpi1.metric(f"{latest_year}年度 {metric}", _format_people(latest_total))
        kpi2.metric("大学数", f"{university_count:,}校")
        kpi3.metric(
            "女性割合",
            f"{women_share:.1f}%" if women_share is not None else "－",
        )
        kpi4.metric("集計区分", aggregate_level)

        trend = (
            filtered.groupby("調査年度", as_index=False)["値"]
            .sum()
            .sort_values("調査年度")
        )
        st.subheader("年度推移")
        st.line_chart(trend, x="調査年度", y="値")

        chart_col1, chart_col2 = st.columns(2)
        with chart_col1:
            st.subheader(f"大学別比較（{latest_year}年度）")
            by_school = (
                latest.groupby("学校名", as_index=False)["値"]
                .sum()
                .sort_values("値", ascending=False)
                .head(30)
            )
            st.bar_chart(by_school, x="学校名", y="値")
            if len(latest["学校名"].dropna().unique()) > 30:
                st.caption("値の大きい30大学を表示しています。大学フィルターで対象を絞れます。")

        with chart_col2:
            st.subheader(f"学部別構成（{latest_year}年度）")
            by_faculty = (
                latest.groupby("学部名", as_index=False)["値"]
                .sum()
                .sort_values("値", ascending=False)
                .head(30)
            )
            st.bar_chart(by_faculty, x="学部名", y="値")
            if len(latest["学部名"].dropna().unique()) > 30:
                st.caption("値の大きい30学部を表示しています。学部フィルターで対象を絞れます。")

        st.subheader("集計表")
        summary = (
            filtered.groupby(["調査年度", "学校名"], as_index=False)["値"]
            .sum()
            .sort_values(["調査年度", "値"], ascending=[False, False])
        )
        st.dataframe(summary, width="stretch", hide_index=True)

    st.divider()
    st.subheader("Looker Studio用データ")
    st.write(
        "読み込んだ全年度を、Looker Studioで扱いやすい縦長CSVとして出力します。"
        "Googleスプレッドシートへ読み込み、Looker Studioのデータソースとして接続できます。"
    )
    st.warning(
        "Looker Studio側でも「集計区分」を必ず「学部計」または「学科明細」の"
        "どちらか一方に絞ってください。"
    )
    st.download_button(
        "Looker Studio用CSVをダウンロード",
        build_looker_studio_csv(bi_data),
        file_name="school_basic_survey_looker_studio.csv",
        mime="text/csv",
        width="stretch",
    )


st.set_page_config(page_title="学校基本調査データポータル", layout="wide")
st.title("学校基本調査データポータル")
st.caption("大学改革支援・学位授与機構の公開データから、必要な年度・様式・項目を取得します。")
st.info(
    "この公開用画面は、利用時点の公式Excelを直接読み取ります。"
    "履歴データや学内限定データは保存・使用しません。"
)
page = st.sidebar.radio(
    "表示する機能",
    ["データセット作成", "BIダッシュボード"],
)

try:
    catalog = load_catalog()
except CatalogError as exc:
    catalog = {}
    st.error(f"公式サイトから年度一覧を取得できませんでした: {exc}")

if page == "BIダッシュボード":
    render_bi_dashboard(catalog)
    st.divider()
    st.caption(
        "出典：大学改革支援・学位授与機構「大学基本情報」 "
        "https://portal.niad.ac.jp/ptrt/table.html を加工して作成"
    )
    st.stop()

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
