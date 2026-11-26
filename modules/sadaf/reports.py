# -*- coding: utf-8 -*-
"""Backup and report lookup for the Sadaf database only."""
import sqlite3
from datetime import datetime

from .db import DB_FILE, connect, ensure_schema
from .paths import BACKUPS


def latest_report() -> str:
    ensure_schema()
    with connect() as conn:
        row = conn.execute(
            "SELECT report_path FROM runs WHERE report_path != '' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return row["report_path"] if row else ""


def recent_events(limit: int = 30):
    ensure_schema()
    with connect() as conn:
        rows = conn.execute(
            "SELECT created_at, level, message FROM events ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 100)),),
        ).fetchall()
    return [{"at": row["created_at"], "level": row["level"], "message": row["message"]} for row in rows]


def backup_database() -> str:
    ensure_schema()
    dest = BACKUPS / f"sadaf-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    source = sqlite3.connect(DB_FILE)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return str(dest)
