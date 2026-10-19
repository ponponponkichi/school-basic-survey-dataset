# 学校基本調査データストック

大学改革支援・学位授与機構の「大学基本情報」から学校基本調査データを取得し、
年度・様式ごとの公式スナップショットとして蓄積するStreamlitアプリです。

公式年度ページに掲載されているExcelを自動認識します。現在の公式サイトでは
2012～2026年度、年度ごとに22～30様式が掲載されています。

## この版の考え方

- 元Excelは版番号とSHA-256（内容から計算する識別値）を付け、
  `data/raw_history/<年度>/<様式>/` に上書きせず保存します。
- 同じ年度・様式・同じ内容を再取得しても、レコードを重複登録しません。
- 公式ファイルの内容が更新された場合は、新しい版を追加して旧版を履歴として残します。
- 「全年度・全様式を更新確認」ボタンで、現在掲載されている全ファイルを確認できます。
- 新しい版や別年度を取り込むとき、直前の同じ様式と列構成を比較し、
  追加・削除・並び替えを記録します。列名変更は推測せず、旧列の削除と新列の追加として表示します。
- 通常の検索・出力には最新版だけを使います。
- 年度による列追加・列名変更に耐えられるよう、行データをParquetとJSON形式で保存します。
- SQLiteは検索用の完成済みスナップショットです。更新処理はWindowsの一時領域で行い、
  完成したファイルだけをGドライブへ置き換えます。
- DBと管理台帳に記録するファイル位置は `data` からの相対パスなので、
  フォルダ全体を新しいPCへ移しても利用しやすい設計です。
- 利用時には年度・様式・項目を選び、ExcelまたはCSVへ展開します。
- 更新結果、エラー、列構成の変更は画面の更新ログから確認できます。

2012～2014年度は `.xls`、2015年度以降は `.xlsx` のため、`xlrd` と
`openpyxl` の両方を使用します。

## 起動方法

コマンドプロンプトまたはPowerShellでこのフォルダへ移動し、次の順に実行します。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

`app.py` はGドライブに履歴を保存するローカル版です。公開用クラウド版は、
履歴を保存せず利用時点の公式Excelを直接読み取ります。

```powershell
.\.venv\Scripts\python.exe -m streamlit run cloud_app.py
```

## 月1回の自動更新

画面を開かずに全年度・全様式を更新する場合は、次を実行します。

```powershell
.\.venv\Scripts\python.exe sync_data.py
```

Windowsのタスクスケジューラへ登録するためのスクリプトも用意しています。
標準では毎日9時に確認し、その月の更新が成功済みなら何もせず終了します。
PCが停止していた場合は、次回起動・サインイン後に繰り越して実行します。

```powershell
powershell -ExecutionPolicy Bypass -File .\install_monthly_task.ps1
```

時刻を変える例（毎日12時30分に確認）:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_monthly_task.ps1 -Hour 12 -Minute 30
```

タスク設定自体はWindowsに保存されますが、原本・DB・ログなどのデータはGドライブだけに
保存します。PC買い替え時は、新しいPCで上記の登録コマンドを1回実行します。

## テスト

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Gドライブ内の保存場所

- 検索用DB: `data/database_snapshot/school_basic_survey.db`
- 公式原本の全履歴: `data/raw_history/<年度>/<様式>/`
- 加工済みデータの全履歴: `data/datasets/<年度>/<様式>/`
- 原本・加工データ・版の対応表: `data/manifests/source_manifest.json`
- 更新ログ: `data/logs/`
- 取得元: <https://portal.niad.ac.jp/ptrt/table.html>

永続保存するデータはすべて、このプロジェクトの `data` フォルダに集約します。
Cドライブ等のWindows一時領域は更新途中の作業にだけ使い、処理後に残しません。
PC買い替え時は、プロジェクトフォルダ全体（特に `data`）を同期・移行対象にします。

このデータは公開情報ですが、将来、学内限定データを結合する場合は、公開用データと
同じデータベースへ混在させないでください。ローカル版は全履歴と更新作業をGドライブ側で
管理します。クラウド版は公開情報だけを
公式サイトから直接取得し、Gドライブや学内限定データへ接続しません。

## クラウド公開

公開アプリ: <https://school-basic-survey-dataset.streamlit.app/>

Streamlit Community Cloudでは、GitHubリポジトリを接続し、起動ファイルとして
`cloud_app.py` を指定します。`data/`、ローカルDB、更新ログ、秘密設定は
`.gitignore` によりGitHubへ登録しません。クラウド版は公式サイトの公開Excelだけを
利用時に取得します。

