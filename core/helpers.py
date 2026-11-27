"""
core/helpers.py — Shared helper functions used across routes.
"""
import os
import json
import asyncio
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    import aiosqlite
    AIOSQLITE_AVAILABLE = True
except ImportError:  # pragma: no cover - sync wrapper when aiosqlite is unavailable
    aiosqlite = None  # type: ignore[assignment]
    AIOSQLITE_AVAILABLE = False

from database import DBHandler, DB_PATH
from core.persian_normalizer import to_ascii_digits


# ── NID & Postal Code normalization ──────────────────────────────────
def _normalize_nid(value: Optional[str]) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    s = to_ascii_digits(s)
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
    s = to_ascii_digits(s).replace("-", "").replace(" ", "")
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
    s = to_ascii_digits(s).replace("-", "").replace(" ", "")
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    if s.startswith("+98"):
        s = "0" + s[3:]
    elif s.startswith("98") and len(s) == 12:
        s = "0" + s[2:]
    elif s.isdigit() and len(s) == 10 and s.startswith("9"):
        s = "0" + s
    return s


def _clean_text(value: Any) -> str:
    """Collapse internal whitespace cleanly without mutating legitimate text."""
    return " ".join(str(value or "").split())


def _normalize_date_digit_part(value: Any) -> str:
    """Convert Persian/Arabic digits in a date component to trimmed ASCII text."""
    s = to_ascii_digits(str(value or "")).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _extract_date_triple(
    data: Mapping[str, Any],
    y_keys: Tuple[str, ...],
    m_keys: Tuple[str, ...],
    d_keys: Tuple[str, ...],
    composite_key: str,
) -> Tuple[str, str, str]:
    """Read year/month/day parts with ASCII digit normalization and composite YYYY/MM/DD fallback."""
    def _first(keys: Tuple[str, ...]) -> str:
        for k in keys:
            val = _normalize_date_digit_part(data.get(k))
            if val:
                return val
        return ""

    year = _first(y_keys)
    month = _first(m_keys)
    day = _first(d_keys)
    if not (year and month and day):
        comp = _normalize_date_digit_part(data.get(composite_key))
        if "/" in comp:
            parts = [p.strip() for p in comp.split("/")]
            if len(parts) == 3:
                year = year or parts[0]
                month = month or parts[1]
                day = day or parts[2]
    return year, month, day


def _validate_date_triple(label: str, year: str, month: str, day: str, *, required: bool) -> Optional[str]:
    """Validate normalized Jalali year/month/day triple and return a Persian error message if invalid."""
    if not (year or month or day):
        return f"{label} وارد نشده است" if required else None
    if not (year and month and day):
        return f"{label} ناقص است (روز، ماه و سال باید کامل باشند)"
    if not (year.isdigit() and len(year) == 4 and 1300 <= int(year) <= 1499):
        return f"سال {label} نامعتبر است (باید ۴ رقم بین ۱۳۰۰ تا ۱۴۹۹ باشد)"
    if not (month.isdigit() and 1 <= int(month) <= 12):
        return f"ماه {label} نامعتبر است (باید بین ۱ تا ۱۲ باشد)"
    if not (day.isdigit() and 1 <= int(day) <= 31):
        return f"روز {label} نامعتبر است (باید بین ۱ تا ۳۱ باشد)"
    return None


def _split_applicant_name(data: Mapping[str, Any], full_name: str) -> Tuple[str, str]:
    """Extract first and last name deterministically from structured keys or full_name."""
    first = _clean_text(data.get("first_name") or data.get("fname"))
    last = _clean_text(data.get("last_name") or data.get("lname"))
    if first or last:
        return first, last
    parts = _clean_text(full_name).split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def normalize_applicant_payload(
    full_name: Optional[str],
    national_id: Optional[str],
    data: Optional[Mapping[str, Any]],
) -> Tuple[str, str, Dict[str, Any]]:
    """Normalize applicant top-level identity and JSON data without dropping optional fields."""
    clean_name = _clean_text(full_name)
    clean_nid = _normalize_nid(national_id)
    cleaned: Dict[str, Any] = dict(data or {})

    raw_loan = str(cleaned.get("loan_type") or "marriage").strip().lower()
    cleaned["loan_type"] = "childbearing" if raw_loan in {"child", "childbearing", "farzand", "فرزند", "فرزندآوری"} else "marriage"

    cleaned["spouse_national_id"] = _normalize_nid(cleaned.get("spouse_national_id"))
    cleaned["child_national_id"] = _normalize_nid(cleaned.get("child_national_id"))

    raw_zip = cleaned.get("postal_code") if cleaned.get("postal_code") not in (None, "") else cleaned.get("zip_code")
    postal_code = normalize_postal_code(raw_zip)
    cleaned["postal_code"] = postal_code
    cleaned["zip_code"] = postal_code

    cleaned["mobile"] = normalize_mobile(cleaned.get("mobile"))
    cleaned["phone"] = to_ascii_digits(str(cleaned.get("phone") or "")).replace(" ", "").strip()
    cleaned["tracking_code"] = to_ascii_digits(str(cleaned.get("tracking_code") or "")).strip()
    cleaned["state"] = _clean_text(cleaned.get("state"))
    cleaned["city"] = _clean_text(cleaned.get("city"))
    if "military_status" in cleaned or cleaned.get("military_status"):
        mil = to_ascii_digits(str(cleaned.get("military_status") or "")).strip()
        cleaned["military_status"] = mil

    by, bm, bd = _extract_date_triple(
        cleaned, ("birth_y", "birth_year"), ("birth_m", "birth_month"), ("birth_d", "birth_day"), "birth_date"
    )
    cleaned["birth_y"], cleaned["birth_m"], cleaned["birth_d"] = by, bm, bd

    my, mm, md = _extract_date_triple(
        cleaned, ("marriage_y", "marriage_year"), ("marriage_m", "marriage_month"), ("marriage_d", "marriage_day"), "marriage_date"
    )
    cleaned["marriage_y"], cleaned["marriage_m"], cleaned["marriage_d"] = my, mm, md

    cby, cbm, cbd = _extract_date_triple(
        cleaned,
        ("child_birth_y", "child_birth_year"),
        ("child_birth_m", "child_birth_month"),
        ("child_birth_d", "child_birth_day"),
        "child_birth_date",
    )
    cleaned["child_birth_y"], cleaned["child_birth_m"], cleaned["child_birth_d"] = cby, cbm, cbd

    # Keep canonical SQL identity authoritative inside data as well.
    if clean_name:
        cleaned["full_name"] = clean_name
    if clean_nid:
        cleaned["national_id"] = clean_nid
    return clean_name, clean_nid, cleaned


def validate_applicant_save(full_name: str, national_id: str, data: Mapping[str, Any]) -> List[str]:
    """Validate format of provided fields when saving an applicant (allows optional fields empty)."""
    errors: List[str] = []
    if not _clean_text(full_name):
        errors.append("نام و نام خانوادگی الزامی است")
    if not (national_id.isdigit() and len(national_id) == 10):
        errors.append("کد ملی باید دقیقاً ۱۰ رقم باشد")

    spouse = str(data.get("spouse_national_id") or "")
    if spouse and (not spouse.isdigit() or len(spouse) != 10):
        errors.append("کد ملی همسر باید دقیقاً ۱۰ رقم باشد")

    child = str(data.get("child_national_id") or "")
    if child and (not child.isdigit() or len(child) != 10):
        errors.append("کد ملی فرزند باید دقیقاً ۱۰ رقم باشد")

    postal = str(data.get("postal_code") or "")
    if postal and (not postal.isdigit() or len(postal) != 10):
        errors.append("کد پستی باید دقیقاً ۱۰ رقم باشد")

    mobile = str(data.get("mobile") or "")
    if mobile and (not mobile.isdigit() or len(mobile) != 11 or not mobile.startswith("09")):
        errors.append("شماره موبایل نامعتبر است (باید ۱۱ رقم و با ۰۹ شروع شود)")

    for label, yk, mk, dk in (
        ("تاریخ تولد متقاضی", "birth_y", "birth_m", "birth_d"),
        ("تاریخ عقد ازدواج", "marriage_y", "marriage_m", "marriage_d"),
        ("تاریخ تولد فرزند", "child_birth_y", "child_birth_m", "child_birth_d"),
    ):
        err = _validate_date_triple(label, str(data.get(yk) or ""), str(data.get(mk) or ""), str(data.get(dk) or ""), required=False)
        if err:
            errors.append(err)
    return errors


def validate_registration_readiness(
    applicant: Optional[Mapping[str, Any]],
    *,
    expected_nid: Optional[str] = None,
    loan_type_override: Optional[str] = None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Verify DB applicant identity and completeness of all required initial registration fields."""
    if not applicant:
        return False, ["پرونده متقاضی در پایگاه‌داده یافت نشد؛ ابتدا اطلاعات فرم را ذخیره کنید"], {}

    raw_data = applicant.get("data")
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data) if raw_data else {}
        except Exception:
            raw_data = {}
    if not isinstance(raw_data, dict):
        raw_data = {}

    if loan_type_override:
        raw_data = {**raw_data, "loan_type": loan_type_override}

    clean_name, clean_nid, norm = normalize_applicant_payload(
        applicant.get("full_name"), applicant.get("national_id"), raw_data
    )

    errors: List[str] = []
    if expected_nid is not None and _normalize_nid(expected_nid) != clean_nid:
        errors.append("عدم تطابق کد ملی متقاضی با پرونده انتخاب‌شده")

    first_name, last_name = _split_applicant_name(norm, clean_name)
    if not clean_name:
        errors.append("نام و نام خانوادگی وارد نشده است")
    elif not first_name or not last_name:
        errors.append("نام و نام خانوادگی باید شامل نام و نام خانوادگی (با فاصله) باشد")

    if not (clean_nid.isdigit() and len(clean_nid) == 10):
        errors.append("کد ملی متقاضی باید دقیقاً ۱۰ رقم باشد")

    mobile = norm.get("mobile", "")
    if not mobile:
        errors.append("شماره تلفن همراه وارد نشده است")
    elif not (mobile.isdigit() and len(mobile) == 11 and mobile.startswith("09")):
        errors.append("شماره تلفن همراه نامعتبر است (۱۱ رقم با ۰۹)")

    b_err = _validate_date_triple("تاریخ تولد متقاضی", norm["birth_y"], norm["birth_m"], norm["birth_d"], required=True)
    if b_err:
        errors.append(b_err)

    if norm["loan_type"] == "marriage":
        spouse = norm.get("spouse_national_id", "")
        if not spouse:
            errors.append("کد ملی همسر وارد نشده است")
        elif not (spouse.isdigit() and len(spouse) == 10):
            errors.append("کد ملی همسر باید دقیقاً ۱۰ رقم باشد")
        m_err = _validate_date_triple("تاریخ عقد ازدواج", norm["marriage_y"], norm["marriage_m"], norm["marriage_d"], required=True)
        if m_err:
            errors.append(m_err)
    elif norm["loan_type"] == "childbearing":
        child = norm.get("child_national_id", "")
        if not child:
            errors.append("کد ملی فرزند وارد نشده است")
        elif not (child.isdigit() and len(child) == 10):
            errors.append("کد ملی فرزند باید دقیقاً ۱۰ رقم باشد")
        c_err = _validate_date_triple("تاریخ تولد فرزند", norm["child_birth_y"], norm["child_birth_m"], norm["child_birth_d"], required=True)
        if c_err:
            errors.append(c_err)

    if not norm.get("state"):
        errors.append("استان محل سکونت انتخاب نشده است")
    if not norm.get("city"):
        errors.append("شهر محل سکونت انتخاب نشده است")
    postal = norm.get("postal_code", "")
    if not postal:
        errors.append("کد پستی ۱۰ رقمی وارد نشده است")
    elif not (postal.isdigit() and len(postal) == 10):
        errors.append("کد پستی باید دقیقاً ۱۰ رقم باشد")

    return len(errors) == 0, errors, norm


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
                record['runtime'].pop('last_otp_code', None)
                record["checkpoint"] = checkpoint_by_nid.get(nid, {})
                results.append(record)
    except Exception:
        return []
    return results
