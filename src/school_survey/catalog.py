from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import ALLOWED_HOST, REQUEST_TIMEOUT_SECONDS, TOP_URL


class CatalogError(RuntimeError):
    """公式サイトの一覧・ファイル取得に失敗したときの例外。"""


@dataclass(frozen=True)
class DownloadedFile:
    url: str
    filename: str
    content: bytes


@dataclass(frozen=True)
class FormFile:
    """年度ページに掲載された1つの公式Excel。"""

    form_code: str
    form_label: str
    url: str
    filename: str


def _session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session = requests.Session()
    session.headers.update({"User-Agent": "SchoolSurveyDatasetTool/0.1"})
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _get(url: str) -> requests.Response:
    try:
        response = _session().get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        raise CatalogError(f"公式サイトへの接続に失敗しました: {url}") from exc


def _validate_official_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
        raise CatalogError(f"公式サイト以外のURLは取得しません: {url}")


def fetch_catalog() -> dict[int, str]:
    """掲載年度と年度ページURLを取得する。"""
    response = _get(TOP_URL)
    soup = BeautifulSoup(response.content, "html.parser")
    catalog: dict[int, str] = {}
    for link in soup.find_all("a", href=True):
        label = link.get_text(" ", strip=True)
        match = re.match(r"^(20\d{2})", label)
        if not match:
            continue
        url = urljoin(TOP_URL, link["href"])
        _validate_official_url(url)
        catalog[int(match.group(1))] = url
    if not catalog:
        raise CatalogError("公式サイトから掲載年度を読み取れませんでした。")
    return catalog


def _form_token(value: str) -> str:
    return re.sub(r"[^0-9a-z]", "", value.casefold())


def _form_code_from_filename(filename: str) -> str | None:
    match = re.match(r"^20\d{2}_(.+?)\.xlsx?$", filename, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).replace("-", "_")


def _parse_form_files(content: bytes, year_page_url: str) -> list[FormFile]:
    """年度ページの表から、様式コード・名称・Excel URLを読み取る。"""
    soup = BeautifulSoup(content.decode("utf-8-sig"), "html.parser")
    forms: list[FormFile] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        url = urljoin(year_page_url, link["href"])
        filename = Path(urlparse(url).path).name
        if Path(filename).suffix.casefold() not in {".xls", ".xlsx"}:
            continue
        form_code = _form_code_from_filename(filename)
        if not form_code or form_code in seen:
            continue
        _validate_official_url(url)
        download_cell = link.find_parent(["td", "th"])
        label_cell = download_cell.find_previous_sibling(["td", "th"]) if download_cell else None
        label = label_cell.get_text(" ", strip=True) if label_cell else ""
        forms.append(
            FormFile(
                form_code=form_code,
                form_label=label or form_code,
                url=url,
                filename=filename,
            )
        )
        seen.add(form_code)
    return forms


def fetch_year_forms(year_page_url: str) -> list[FormFile]:
    """指定年度に掲載されている全様式を取得する。"""
    _validate_official_url(year_page_url)
    forms = _parse_form_files(_get(year_page_url).content, year_page_url)
    if not forms:
        raise CatalogError("年度ページからExcel様式を読み取れませんでした。")
    return forms


def download_form_file(form: FormFile) -> DownloadedFile:
    """一覧で確認済みの公式Excelをダウンロードする。"""
    _validate_official_url(form.url)
    response = _get(form.url)
    if not response.content:
        raise CatalogError(f"取得したファイルが空です: {form.filename}")
    return DownloadedFile(url=form.url, filename=form.filename, content=response.content)


def fetch_form_file(year_page_url: str, form_code: str) -> DownloadedFile:
    """年度ページから指定様式のExcelを取得する。"""
    expected = _form_token(form_code)
    form = next(
        (item for item in fetch_year_forms(year_page_url) if _form_token(item.form_code) == expected),
        None,
    )
    if form is None:
        raise CatalogError(f"{form_code} のExcelファイルが年度ページに見つかりません。")
    return download_form_file(form)

