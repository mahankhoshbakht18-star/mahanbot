# -*- coding: utf-8 -*-
"""Excel and CSV import. Preview never writes secrets to disk."""
import csv
import io
import secrets
import threading
import time
from typing import Any, Dict, List

from .customers import get_customer, identity_key, is_pass_col, is_user_col, save_customer
from .db import connect, ensure_schema

_LOCK = threading.Lock()
_PREVIEWS: Dict[str, Dict[str, Any]] = {}
_TTL = 600


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_table(filename: str, raw: bytes) -> List[Dict[str, str]]:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        try:
            grid = [[_cell(cell) for cell in row] for row in book.worksheets[0].iter_rows(values_only=True)]
        finally:
            book.close()
    elif name.endswith(".csv"):
        text = raw.decode("utf-8-sig")
        grid = [[_cell(cell) for cell in row] for row in csv.reader(io.StringIO(text))]
    else:
        raise ValueError("فقط فایل xlsx یا csv پذیرفته می‌شود.")
    grid = [row for row in grid if any(row)]
    if len(grid) < 2:
        raise ValueError("فایل باید یک سطر عنوان و حداقل یک مشتری داشته باشد.")
    head = grid[0]
    if not any(is_user_col(item) for item in head) or not any(is_pass_col(item) for item in head):
        raise ValueError("ستون «نام کاربری» یا «رمز عبور» در سطر اول پیدا نشد.")
    rows = []
    for offset, row in enumerate(grid[1:], start=2):
        item = {head[index]: (row[index] if index < len(row) else "") for index in range(len(head)) if head[index]}
        item["__row__"] = str(offset)
        rows.append(item)
    return rows


def _known(username: str):
    ensure_schema()
    with connect() as conn:
        return conn.execute("SELECT id FROM customers WHERE username = ?", (username,)).fetchone()


def _split(row: Dict[str, str]) -> Dict[str, Any]:
    username = next((value for key, value in row.items() if is_user_col(key)), "").strip()
    password = next((value for key, value in row.items() if is_pass_col(key)), "").strip()
    identity = {"name": "", "phone": "", "kind": "", "notes": ""}
    fields = []
    for key, value in row.items():
        if key == "__row__" or not value or is_user_col(key) or is_pass_col(key):
            continue
        slot = identity_key(key)
        if slot:
            identity[slot] = value
        else:
            fields.append({"name": key, "value": value})
    return {
        "sheet_row": int(row.get("__row__") or 0),
        "username": username,
        "password": password,
        "full_name": identity["name"],
        "phone": identity["phone"],
        "customer_type": identity["kind"],
        "notes": identity["notes"],
        "fields": fields,
    }


def build_preview(filename: str, raw: bytes) -> Dict[str, Any]:
    parsed = [_split(row) for row in read_table(filename, raw)]
    errors = []
    clean = []
    seen = set()
    for item in parsed:
        if not item["username"] or not item["password"]:
            errors.append({"row": item["sheet_row"], "message": "نام کاربری یا رمز خالی است."})
            continue
        if item["username"] in seen:
            errors.append({"row": item["sheet_row"], "message": "نام کاربری در همین فایل تکراری است."})
            continue
        seen.add(item["username"])
        item["duplicate"] = _known(item["username"]) is not None
        clean.append(item)
    token = secrets.token_urlsafe(18)
    with _LOCK:
        _purge()
        _PREVIEWS[token] = {"at": time.time(), "rows": clean}
    return {
        "token": token,
        "accepted": len(clean),
        "duplicates": sum(1 for item in clean if item["duplicate"]),
        "new": sum(1 for item in clean if not item["duplicate"]),
        "errors": errors,
        "sample": [
            {
                "row": item["sheet_row"],
                "username": item["username"],
                "full_name": item["full_name"],
                "fields": len(item["fields"]),
                "duplicate": item["duplicate"],
            }
            for item in clean[:12]
        ],
    }


def commit_preview(token: str, policy: str) -> Dict[str, int]:
    if policy not in {"skip", "update"}:
        raise ValueError("سیاست تکراری باید skip یا update باشد.")
    with _LOCK:
        _purge()
        bag = _PREVIEWS.pop(token, None)
    if not bag:
        raise ValueError("پیش‌نمایش منقضی شده. فایل را دوباره انتخاب کنید.")
    created = updated = skipped = 0
    for item in bag["rows"]:
        if item["duplicate"] and policy == "skip":
            skipped += 1
            continue
        payload = {
            "username": item["username"],
            "password": item["password"],
            "full_name": item["full_name"],
            "phone": item["phone"],
            "customer_type": item["customer_type"],
            "notes": item["notes"],
            "fields": item["fields"],
            "active": True,
        }
        if item["duplicate"]:
            existing = _known(item["username"])
            current = get_customer(int(existing["id"]))
            if current and not payload["full_name"]:
                payload["full_name"] = current["full_name"]
            if current and not payload["phone"]:
                payload["phone"] = current["phone"]
            save_customer(payload, int(existing["id"]))
            updated += 1
        else:
            save_customer(payload)
            created += 1
    return {"created": created, "updated": updated, "skipped": skipped}


def _purge() -> None:
    dead = [key for key, value in _PREVIEWS.items() if time.time() - value["at"] > _TTL]
    for key in dead:
        _PREVIEWS.pop(key, None)
