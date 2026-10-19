from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st


ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from school_survey.catalog import (
    CatalogError,
    FormFile,
    fetch_catalog,
    fetch_year_forms,
)
from school_survey.config import (
    DATA_DIR,
    DATABASE_PATH,
    DATASETS_DIR,
    LEGACY_DATABASE_PATH,
    LOGS_DIR,
    MANIFESTS_DIR,
    RAW_HISTORY_DIR,
)
from school_survey.exports import build_csv_zip, build_excel_workbook
from school_survey.repository import DataRepository
from school_survey.sync_service import sync_tasks


st.set_page_config(page_title="学校基本調査データストック", layout="wide")
st.title("学校基本調査データストック")
st.caption("公式Excelを年度・様式ごとに蓄積し、分析しやすいデータセットへ変換します。")

repository = DataRepository(
    data_dir=DATA_DIR,
    database_path=DATABASE_PATH,
    raw_history_dir=RAW_HISTORY_DIR,
    datasets_dir=DATASETS_DIR,
    manifests_dir=MANIFESTS_DIR,
    logs_dir=LOGS_DIR,
    legacy_database_path=LEGACY_DATABASE_PATH,
)
repository.ensure_layout()
database = repository.read_database()


@st.cache_data(ttl=3600, show_spinner=False)
def load_catalog() -> dict[int, str]:
    return fetch_catalog()


@st.cache_data(ttl=3600, show_spinner=False)
def load_year_forms(year_page_url: str) -> list[FormFile]:
    return fetch_year_forms(year_page_url)


def update_tasks(tasks: list[tuple[int, FormFile]], trigger: str) -> None:
    if not tasks:
        st.warning("更新対象のファイルがありません。年度と様式を確認してください。")
        return
    progress = st.progress(0)
    status = st.empty()

    def show_progress(index: int, total: int, year: int, form: FormFile) -> None:
        status.write(f"{year}年度 {form.form_code} を確認しています（{index}/{total}）。")
        progress.progress(index / total)

    run = sync_tasks(
        repository,
        tasks,
        trigger=trigger,
        progress_callback=show_progress,
    )

    status.empty()
    success_count = sum(item.get("status") != "error" for item in run.items)
    changed_count = sum(
        1
        for item in run.items
        if item.get("schema_change")
    )
    if success_count == len(run.items):
        st.success(
            f"{len(run.items):,}ファイルの確認が完了しました。"
            f"列構成の変更は{changed_count:,}件です。"
        )
    else:
        st.warning(
            f"{len(run.items):,}件中{success_count:,}件を処理しました。"
            "処理できなかった項目は更新ログで確認できます。"
        )

    for item in run.items:
        if item["status"] == "error":
            st.error(
                f"{item['survey_year']}年度 {item['form_code']}: {item.get('message', '')}"
            )
        elif item["status"] != "unchanged":
            message = (
                f"{item['survey_year']}年度 {item['form_code']}: 新しい版を保存 "
                f"（{item['row_count']:,}行、版{item['version_number']}）"
            )
            if item.get("schema_change"):
                message += "／列構成の変更あり"
            st.write(message)

with st.expander("このアプリの保存方法", expanded=False):
    st.write(
        "同じ年度・様式のファイルを再取得したとき、内容が同じなら重複登録しません。"
        "公式ファイルが更新されていた場合は新しい版を追加し、旧版も履歴として残します。"
    )
    st.write(
        "年度によって学校コード体系や列名が変わるため、元の意味を保持して保存し、"
        "Excel・CSVを作るときに年度横断の表へ展開します。"
    )
    st.info(
        "永続データはこのアプリのdataフォルダに保存します。更新中だけWindowsの一時領域を使い、"
        "完成した原本・データセット・SQLite・ログをGドライブ側へ保存します。"
    )

st.subheader("1. 公式データを追加・更新")

try:
    catalog = load_catalog()
except CatalogError as exc:
    catalog = {}
    st.error(f"年度一覧を取得できませんでした。時間をおいて再実行してください。\n\n{exc}")

available_years = sorted(catalog, reverse=True)
default_years = available_years[:1]
selected_years = st.multiselect(
    "取得する年度",
    available_years,
    default=default_years,
    help="初回は最新年度だけで動作を確認し、問題がなければ過年度を追加するのがおすすめです。",
)
forms_by_year: dict[int, list[FormFile]] = {}
form_labels: dict[str, str] = {}
for year in selected_years:
    try:
        forms_by_year[year] = load_year_forms(catalog[year])
        for form in forms_by_year[year]:
            form_labels.setdefault(form.form_code, form.form_label)
    except CatalogError as exc:
        st.error(f"{year}年度の様式一覧を取得できませんでした: {exc}")

available_forms = sorted(form_labels)
selected_forms = st.multiselect(
    "取得する様式",
    available_forms,
    default=available_forms,
    format_func=lambda code: f"{code}  {form_labels.get(code, code)}",
)

selected_button, all_button = st.columns(2)
with selected_button:
    update_selected = st.button(
        "選択したデータを追加・更新",
        type="primary",
        disabled=not catalog,
        width="stretch",
    )
with all_button:
    update_all = st.button(
        "全年度・全様式を更新確認",
        disabled=not catalog,
        width="stretch",
        help="公式サイトに現在掲載されている全年度・全様式を確認します。初回は時間がかかります。",
    )

if update_selected:
    if not selected_years or not selected_forms:
        st.warning("年度と様式を1つ以上選択してください。")
    else:
        selected_set = set(selected_forms)
        tasks = [
            (year, form)
            for year in selected_years
            for form in forms_by_year.get(year, [])
            if form.form_code in selected_set
        ]
        update_tasks(tasks, "selected_button")

if update_all:
    all_tasks: list[tuple[int, FormFile]] = []
    with st.spinner("全年度の様式一覧を確認しています。"):
        for year in sorted(catalog, reverse=True):
            try:
                all_tasks.extend((year, form) for form in load_year_forms(catalog[year]))
            except CatalogError as exc:
                st.error(f"{year}年度の様式一覧を取得できませんでした: {exc}")
    if all_tasks:
        update_tasks(all_tasks, "all_button")

database = repository.read_database()

st.subheader("2. 更新ログ")
sync_logs = repository.list_sync_logs()
if not sync_logs:
    st.info("更新ログはまだありません。")
else:
    selected_log_index = st.selectbox(
        "表示する実行履歴",
        range(len(sync_logs)),
        format_func=lambda index: (
            f"{sync_logs[index].get('started_at', '')} "
            f"（{sync_logs[index].get('trigger', '')}）"
        ),
    )
    selected_log = sync_logs[selected_log_index]
    counts = selected_log.get("counts", {})
    st.write(
        f"更新: {counts.get('imported', 0)} / "
        f"変更なし: {counts.get('unchanged', 0)} / "
        f"再有効化: {counts.get('reactivated', 0)} / "
        f"エラー: {counts.get('error', 0)}"
    )
    st.dataframe(selected_log.get("items", []), hide_index=True, width="stretch")

schema_changes = database.get_recent_schema_changes()
with st.expander(f"列構成の変更履歴（{len(schema_changes):,}件）", expanded=False):
    if not schema_changes:
        st.write("記録された列構成の変更はありません。")
    else:
        for change in schema_changes:
            st.markdown(
                f"**{change['survey_year']}年度 {change['form_code']} "
                f"版{change['version_number']}** "
                f"（比較対象: {change['reference_year']}年度 "
                f"版{change['reference_version']}）"
            )
            if change["added_columns"]:
                st.write("追加された列: " + "、".join(change["added_columns"]))
            if change["removed_columns"]:
                st.write("なくなった列: " + "、".join(change["removed_columns"]))
            if change["reordered_columns"]:
                st.write("並び順が変わった列: " + "、".join(change["reordered_columns"]))

st.subheader("3. 蓄積状況")
summary = database.get_active_summary()
if summary.empty:
    st.info("まだデータがありません。上で年度と様式を選び、追加・更新してください。")
else:
    display_summary = summary.rename(
        columns={
            "survey_year": "調査年度",
            "form_code": "様式コード",
            "form_label": "様式名",
            "row_count": "行数",
            "version_number": "版",
            "imported_at": "取込日時",
        }
    )
    st.dataframe(display_summary, hide_index=True, width="stretch")

    stored_years = sorted(summary["survey_year"].astype(int).unique(), reverse=True)
    stored_forms = sorted(summary["form_code"].astype(str).unique())
    stored_form_labels = (
        summary.drop_duplicates("form_code").set_index("form_code")["form_label"].to_dict()
    )

    st.subheader("4. 検索・確認")
    preview_form = st.selectbox(
        "確認する様式",
        stored_forms,
        format_func=lambda code: f"{code}  {stored_form_labels.get(code, code)}",
    )
    preview_years = st.multiselect("確認する年度", stored_years, default=stored_years[:1])
    school_query = st.text_input("学校名で絞り込み（部分一致）", placeholder="例：神戸大学")

    preview = database.load_active_dataframe(
        years=preview_years,
        form_codes=[preview_form],
        school_name_query=school_query,
    )
    if preview.empty:
        st.info("条件に一致するデータがありません。")
    else:
        st.caption(f"{len(preview):,}行（画面表示は先頭1,000行まで）")
        st.dataframe(preview.head(1000), hide_index=True, width="stretch")

    st.subheader("5. データセットを出力")
    export_years = st.multiselect(
        "出力する年度",
        stored_years,
        default=stored_years,
        key="export_years",
    )
    export_forms = st.multiselect(
        "出力する様式",
        stored_forms,
        default=stored_forms,
        key="export_forms",
        format_func=lambda code: f"{code}  {stored_form_labels.get(code, code)}",
    )

    if export_years and export_forms:
        export_frames = database.load_active_frames(export_years, export_forms)
        st.write("出力する項目を選択してください。年度・様式・出典情報は自動で付けます。")
        metadata_columns = ["調査年度", "様式コード", "元ファイル名", "元データ行番号"]
        selected_export_frames = {}
        selected_columns_by_form: dict[str, list[str]] = {}
        for form_code, frame in export_frames.items():
            selectable_columns = [
                column for column in frame.columns if column not in metadata_columns
            ]
            with st.expander(
                f"{form_code}  {stored_form_labels.get(form_code, form_code)}の項目",
                expanded=len(export_forms) <= 3,
            ):
                selected_columns = st.multiselect(
                    "出力項目",
                    selectable_columns,
                    default=selectable_columns,
                    key=f"export_columns_{form_code}",
                )
            selected_columns_by_form[form_code] = selected_columns
            kept_metadata = [column for column in metadata_columns if column in frame.columns]
            selected_export_frames[form_code] = frame[kept_metadata + selected_columns]

        manifest = database.get_source_manifest(export_years, export_forms)
        column_dictionary = database.get_column_dictionary(export_years, export_forms)
        if not column_dictionary.empty:
            column_dictionary = column_dictionary[
                column_dictionary.apply(
                    lambda row: row["出力列名"]
                    in selected_columns_by_form.get(row["様式コード"], []),
                    axis=1,
                )
            ].reset_index(drop=True)
        excel_bytes = build_excel_workbook(
            selected_export_frames, manifest, column_dictionary
        )
        csv_zip_bytes = build_csv_zip(
            selected_export_frames, manifest, column_dictionary
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            st.download_button(
                "Excelデータセット",
                data=excel_bytes,
                file_name="school_basic_survey_dataset.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )
        with col2:
            st.download_button(
                "CSVデータセット（ZIP）",
                data=csv_zip_bytes,
                file_name="school_basic_survey_dataset_csv.zip",
                mime="application/zip",
                width="stretch",
            )
        with col3:
            st.download_button(
                "SQLiteデータベース",
                data=database.create_backup_bytes(),
                file_name="school_basic_survey.db",
                mime="application/vnd.sqlite3",
                width="stretch",
            )

st.divider()
st.caption(
    "出典：大学改革支援・学位授与機構「大学基本情報」 "
    "https://portal.niad.ac.jp/ptrt/table.html を加工して作成"
)

