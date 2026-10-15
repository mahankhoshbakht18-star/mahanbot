"""
core/helpers.py — Shared helper functions used across routes.
"""
import os
import json
import asyncio
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

try:
    import aiosqlite
    AIOSQLITE_AVAILABLE = True
except ImportError:  # pragma: no cover - sync wrapper when aiosqlite is unavailable
    aiosqlite = None  # type: ignore[assignment]
    AIOSQLITE_AVAILABLE = False

from database import DBHandler, DB_PATH


# ── NID & Postal Code normalization ──────────────────────────────────
def _normalize_nid(value: Optional[str]) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    s = s.translate(trans)
    s = s.replace("-", "").replace(" ", "")
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    if s.isdigit() and 0 < len(s) < 10:
        s = s.zfill(10)
    return s


def normalize_postal_code(value: Optional[Any]) -> str:
    """Normalize postal code and preserve leading zeros (10 digits)."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    s = s.translate(trans).replace("-", "").replace(" ", "")
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    if s.isdigit() and 0 < len(s) < 10:
        s = s.zfill(10)
    return s


def normalize_mobile(value: Optional[Any]) -> str:
    """Normalize Iranian mobile number and preserve leading zero (11 digits)."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    s = s.translate(trans).replace("-", "").replace(" ", "")
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    if s.startswith("+98"):
        s = "0" + s[3:]
    elif s.startswith("98") and len(s) == 12:
        s = "0" + s[2:]
    elif s.isdigit() and len(s) == 10 and s.startswith("9"):
        s = "0" + s
    return s


# ── Identifier masking ───────────────────────────────────────────────
def _mask_identifier(value: Optional[str]) -> str:
    text = str(value or "")
    if len(text) <= 4:
        return "*" * len(text)
    return text[:2] + "*" * (len(text) - 4) + text[-2:]


class _SyncCursorAdapter:
    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor

    async def fetchone(self):
        return self._cursor.fetchone()

    async def fetchall(self):
        return self._cursor.fetchall()


class _SyncConnAdapter:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    async def execute(self, sql: str, parameters=()):
        cur = self._conn.execute(sql, parameters)
        return _SyncCursorAdapter(cur)

    async def commit(self):
        self._conn.commit()

    async def close(self):
        self._conn.close()


# ── Async SQLite connection helper ───────────────────────────────────
@asynccontextmanager
async def _open_aiosqlite():
    """
    برای جلوگیری از 'database is locked' و هماهنگی با DBHandler (sqlite3 sync)
    """
    if AIOSQLITE_AVAILABLE and aiosqlite is not None:
        conn = await aiosqlite.connect(DB_PATH, timeout=30)
        try:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute("PRAGMA busy_timeout=5000;")
            await conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
        finally:
            await conn.close()
    else:
        raw_conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        adapter = _SyncConnAdapter(raw_conn)
        try:
            await adapter.execute("PRAGMA journal_mode=WAL;")
            await adapter.execute("PRAGMA synchronous=NORMAL;")
            await adapter.execute("PRAGMA busy_timeout=5000;")
            await adapter.execute("PRAGMA foreign_keys=ON;")
            yield adapter
        finally:
            await adapter.close()


# ── Browser profile helpers ──────────────────────────────────────────
def load_browser_profile_settings() -> Dict[str, Any]:
    from browser_launcher import get_default_browser_profile, merge_browser_profiles
    stored = DBHandler.get_setting("browser_profile") or {}
    try:
        return merge_browser_profiles(get_default_browser_profile(), stored)
    except Exception:
        return get_default_browser_profile()


def save_browser_profile_settings(profile: Dict[str, Any]) -> Dict[str, Any]:
    from browser_launcher import normalize_browser_profile
    normalized = normalize_browser_profile(profile)
    DBHandler.update_setting("browser_profile", normalized)
    return normalized


# ── Applicant serialization ──────────────────────────────────────────
def _serialize_applicants() -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    try:
        with DBHandler._connect(row_factory=True) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM applicants ORDER BY id DESC")
            rows = cursor.fetchall()
            runtime_rows = conn.execute("SELECT nid,payload FROM automation_runtime").fetchall()
            runtime_by_nid = {}
            for runtime_row in runtime_rows:
                try:
                    runtime_by_nid[runtime_row[0]] = json.loads(runtime_row[1] or "{}")
                except Exception:
                    runtime_by_nid[runtime_row[0]] = {}
            checkpoint_by_nid = {}
            try:
                cp_rows = conn.execute("SELECT nid,payload FROM checkpoints").fetchall()
                for cp_row in cp_rows:
                    try:
                        checkpoint_by_nid[cp_row[0]] = json.loads(cp_row[1] or "{}")
                    except Exception:
                        checkpoint_by_nid[cp_row[0]] = {}
            except Exception:
                pass
            for row in rows:
                record = dict(row)
                try:
                    data = json.loads(record["data"]) if record.get("data") else {}
                except Exception:
                    data = {}
                if isinstance(data, dict):
                    data = dict(data)
                    data.pop("otp_code", None)
                record["data"] = data
                nid = record.get("national_id")
                record["runtime"] = runtime_by_nid.get(nid, {})
                record["checkpoint"] = checkpoint_by_nid.get(nid, {})
                results.append(record)
    except Exception:
        return []
    return results
