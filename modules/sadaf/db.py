# -*- coding: utf-8 -*-
"""SQLite store for Sadaf. This file never opens the banking database."""
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .paths import DB_FILE, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL UNIQUE,
    password_blob BLOB NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    customer_type TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS request_fields (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    field_value TEXT NOT NULL,
    UNIQUE(customer_id, field_name)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    report_path TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS run_items (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    customer_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    customer_id INTEGER,
    run_id INTEGER
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def ensure_schema() -> None:
    ensure_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(request_fields)")}
        if "field_kind" not in cols:
            conn.execute("ALTER TABLE request_fields ADD COLUMN field_kind TEXT NOT NULL DEFAULT 'text'")
        if "field_options" not in cols:
            conn.execute("ALTER TABLE request_fields ADD COLUMN field_options TEXT NOT NULL DEFAULT ''")


def mark_open_runs_interrupted(stamp: str) -> None:
    ensure_schema()
    with connect() as conn:
        conn.execute(
            "UPDATE runs SET status = 'interrupted', finished_at = COALESCE(finished_at, ?) WHERE status = 'running'",
            (stamp,),
        )


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
