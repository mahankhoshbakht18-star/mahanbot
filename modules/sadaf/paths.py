# -*- coding: utf-8 -*-
"""Filesystem locations owned only by the Sadaf module."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "sadaf"
DB_FILE = DATA / "sadaf.db"
BACKUPS = DATA / "backups"
REPORTS = DATA / "reports"
BROWSER = DATA / "browser"
CAPTCHA_TRAIN = DATA / "captcha_train"


def ensure_dirs() -> None:
    for folder in (DATA, BACKUPS, REPORTS, BROWSER, CAPTCHA_TRAIN):
        folder.mkdir(parents=True, exist_ok=True)
