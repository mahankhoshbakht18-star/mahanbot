# -*- coding: utf-8 -*-
"""Sadaf-only settings. Defaults come from the existing fuel project, not from guesses."""
from .db import connect, ensure_schema

DEFAULT_BASE = "https://newtejaratasan.niopdc.ir"
DEFAULTS = {
    "base_url": DEFAULT_BASE,
    "browser": "chrome",
}


def get_settings() -> dict:
    ensure_schema()
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    found = {row["key"]: row["value"] for row in rows}
    merged = dict(DEFAULTS)
    merged.update({key: value for key, value in found.items() if key in DEFAULTS})
    return merged


def save_settings(payload: dict) -> dict:
    browser = str(payload.get("browser") or "chrome").lower()
    if browser not in {"chrome", "edge"}:
        raise ValueError("مرورگر باید Chrome یا Edge باشد.")
    base = str(payload.get("base_url") or DEFAULT_BASE).strip().rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("آدرس سامانه باید با https:// شروع شود.")
    ensure_schema()
    with connect() as conn:
        conn.execute("INSERT INTO settings(key, value) VALUES('browser', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (browser,))
        conn.execute("INSERT INTO settings(key, value) VALUES('base_url', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (base,))
    return get_settings()


def urls() -> dict:
    base = get_settings()["base_url"]
    return {
        "base": base,
        "login": base + "/login.aspx?ReturnUrl=%2fprotected%2ffrmrequest.aspx",
        "form": base + "/protected/frmrequest.aspx",
        "browser": get_settings()["browser"],
    }
