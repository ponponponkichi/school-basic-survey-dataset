from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_HISTORY_DIR = DATA_DIR / "raw_history"
DATASETS_DIR = DATA_DIR / "datasets"
MANIFESTS_DIR = DATA_DIR / "manifests"
LOGS_DIR = DATA_DIR / "logs"
DATABASE_SNAPSHOT_DIR = DATA_DIR / "database_snapshot"
DATABASE_PATH = DATABASE_SNAPSHOT_DIR / "school_basic_survey.db"
LEGACY_DATABASE_PATH = DATA_DIR / "school_basic_survey.db"
LEGACY_RAW_DIR = DATA_DIR / "raw"

TOP_URL = "https://portal.niad.ac.jp/ptrt/table.html"
ALLOWED_HOST = "portal.niad.ac.jp"
REQUEST_TIMEOUT_SECONDS = 45

FORM_LABELS = {
    "07go_A": "学生数",
    "08go_D": "学部・学科別学生数、志願者数、入学者数",
}

