# -*- coding: utf-8 -*-
"""Sadaf-only settings. Defaults come from the existing fuel project, not from guesses.

The shared request fields live here too: every applicant uses them unless the
applicant has a value of its own for the same field.
"""
import json
from typing import Any, Dict, List

from .db import connect, ensure_schema

DEFAULT_BASE = "https://newtejaratasan.niopdc.ir"
DEFAULTS = {
    "base_url": DEFAULT_BASE,
    "browser": "chrome",
}


# Used only when nothing is saved yet and no applicant has request fields to copy.
FALLBACK_REQUEST = [
    ("بخش مصرف", "کشاورزی"),
    ("گروه کاربرد", "گلخانه"),
    ("کاربرد", "گلخانه"),
    ("کاربرد مورد نظر", "تولید محصولات سبزی و صیفی در گلخانه / بهره برداری گلخانه کوچک مقیاس (1027)"),
    ("آمار تولید دوره کشت قبل", "299"),
    ("نوع کشت", "خیار"),
    ("سطح زیر کشت فعال", "299"),
    ("شروع دوره کشت", "1405/07/01"),
    ("مدت زمان کشت", "8ماه"),
    ("نوع فروشندگی", "فروشندگی"),
    ("نام فروشندگی", "تعاوني کهن شهر-زابلي"),
    ("نوع مصرف سوخت", "مصرف سوخت جهت گرمایش"),
    ("از تاریخ", "1405/08/01"),
    ("تا تاریخ", "1405/08/30"),
    ("نفتگاز|مقدار درخواستی در بخش یارانه", "2000"),
]


def _put(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def _seed_request(conn) -> List[Dict[str, Any]]:
    """First use: copy the request fields of the applicant with the most fields, so nothing is retyped."""
    row = conn.execute(
        "SELECT customer_id, COUNT(*) AS n FROM request_fields GROUP BY customer_id ORDER BY n DESC, customer_id DESC LIMIT 1"
    ).fetchone()
    if row is not None:
        rows = conn.execute(
            "SELECT field_name, field_value, field_kind, field_options FROM request_fields WHERE customer_id = ? ORDER BY id",
            (row["customer_id"],),
        ).fetchall()
        fields = [{"name": r["field_name"], "value": r["field_value"], "kind": r["field_kind"] or "text", "options": r["field_options"] or ""} for r in rows]
    else:
        fields = [{"name": name, "value": value, "kind": "text", "options": ""} for name, value in FALLBACK_REQUEST]
    _put(conn, "request_fields", json.dumps(fields, ensure_ascii=False))
    # Applicants keep only what differs from the shared fields; identical copies would hide later setting changes.
    for field in fields:
        conn.execute(
            "DELETE FROM request_fields WHERE field_name = ? AND field_value = ?",
            (field["name"], field["value"]),
        )
    return fields


def request_defaults() -> List[Dict[str, Any]]:
    ensure_schema()
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'request_fields'").fetchone()
        if row is None:
            return _seed_request(conn)
    try:
        fields = json.loads(row["value"])
    except ValueError:
        return []
    return fields if isinstance(fields, list) else []


def get_settings() -> dict:
    ensure_schema()
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    found = {row["key"]: row["value"] for row in rows}
    merged = dict(DEFAULTS)
    merged.update({key: value for key, value in found.items() if key in DEFAULTS})
    merged["request_fields"] = request_defaults()
    return merged


def save_settings(payload: dict) -> dict:
    browser = str(payload.get("browser") or "chrome").lower()
    if browser not in {"chrome", "edge"}:
        raise ValueError("مرورگر باید Chrome یا Edge باشد.")
    base = str(payload.get("base_url") or DEFAULT_BASE).strip().rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("آدرس سامانه باید با https:// شروع شود.")
    fields = None
    if "request_fields" in payload:
        from .customers import prepare_fields  # customers imports settings lazily too; avoid a cycle
        fields = prepare_fields(payload.get("request_fields"))
        if not fields:
            raise ValueError("اطلاعات درخواست در تنظیمات خالی است.")
    ensure_schema()
    with connect() as conn:
        _put(conn, "browser", browser)
        _put(conn, "base_url", base)
        if fields is not None:
            _put(conn, "request_fields", json.dumps(fields, ensure_ascii=False))
    return get_settings()


def urls() -> dict:
    current = get_settings()
    base = current["base_url"]
    return {
        "base": base,
        "login": base + "/login.aspx?ReturnUrl=%2fprotected%2ffrmrequest.aspx",
        "form": base + "/protected/frmrequest.aspx",
        "browser": current["browser"],
    }
