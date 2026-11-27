# -*- coding: utf-8 -*-
"""Customer records. Passwords are sealed and are not returned to the UI."""
import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from .catalog import NUMBER_FIELDS, SECTORS, SELLERS, field_names
from .crypto import open_secret, seal
from .db import connect, ensure_schema
from .settings import request_defaults

NUMBER_RE = re.compile(r"^[0-9۰-۹٠-٩]+(?:[./][0-9۰-۹٠-٩]+)?$")
SELECT_FIELDS = {"بخش مصرف": SECTORS, "نام فروشندگی": SELLERS}
BUILTIN_FIELDS = set(field_names())

IDENTITY = {
    "name": ("نام", "نام و نام خانوادگی", "نام مشتری"),
    "phone": ("شماره تماس", "تلفن", "موبایل", "شماره موبایل"),
    "kind": ("نوع مشتری", "نوع"),
    "notes": ("توضیحات", "توضیح"),
}


def now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _norm(value: str) -> str:
    return "".join(str(value).replace("ي", "ی").replace("ك", "ک").split()).replace("‌", "")


def is_user_col(name: str) -> bool:
    return _norm(name) in {_norm(item) for item in ("نام کاربری", "کلمه کاربری")}


def is_pass_col(name: str) -> bool:
    return _norm(name) in {_norm(item) for item in ("رمز عبور", "کلمه عبور", "رمز")}


def identity_key(name: str) -> Optional[str]:
    token = _norm(name)
    for key, names in IDENTITY.items():
        if token in {_norm(item) for item in names}:
            return key
    return None


def _row_value(row, key: str, default: Any = "") -> Any:
    return row[key] if key in row.keys() and row[key] is not None else default


def public_customer(row, fields: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    stored = fields or []
    if "field_count" in row.keys() and row["field_count"] is not None:
        count = int(row["field_count"])
    else:
        count = len(stored)
    return {
        "id": row["id"],
        "full_name": row["full_name"],
        "username": row["username"],
        "phone": row["phone"],
        "customer_type": row["customer_type"],
        "active": bool(row["active"]),
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_status": _row_value(row, "last_status", ""),
        "last_finished_at": _row_value(row, "last_finished_at", ""),
        "field_count": count,
        "info_status": f"تنظیمات + {count} فیلد اختصاصی" if count else "از تنظیمات",
        "fields": stored,
    }


def _option_text(raw: Any) -> str:
    if isinstance(raw, list):
        parts = [str(item) for item in raw]
    else:
        parts = str(raw or "").replace("،", "\n").replace(",", "\n").splitlines()
    cleaned = []
    for part in parts:
        item = part.strip()
        if item and item not in cleaned:
            cleaned.append(item)
    return "\n".join(cleaned)


def _number_ok(value: str) -> bool:
    return bool(NUMBER_RE.fullmatch(value.replace(",", "").replace("٬", "")))


def prepare_fields(fields: Any) -> List[Dict[str, str]]:
    if fields is None:
        fields = []
    if not isinstance(fields, list):
        raise ValueError("اطلاعات درخواست باید فهرست باشد.")
    prepared = []
    seen = set()
    for field in fields:
        name = str(field.get("name") or "").strip()
        value = str(field.get("value") or "").strip()
        if not name or not value or is_user_col(name) or is_pass_col(name):
            continue
        key = _norm(name)
        if key in seen:
            raise ValueError(f"فیلد «{name}» دو بار آمده است.")
        seen.add(key)
        if name in SELECT_FIELDS:
            if value not in SELECT_FIELDS[name]:
                raise ValueError(f"مقدار «{value}» در فهرست «{name}» نیست.")
            prepared.append({"name": name, "value": value, "kind": "select", "options": ""})
            continue
        if name in NUMBER_FIELDS:
            if not _number_ok(value):
                raise ValueError(f"فیلد «{name}» فقط عدد می‌پذیرد.")
            prepared.append({"name": name, "value": value, "kind": "number", "options": ""})
            continue
        if name in BUILTIN_FIELDS:
            prepared.append({"name": name, "value": value, "kind": "text", "options": ""})
            continue
        kind = str(field.get("kind") or "text")
        if kind not in {"text", "number", "select"}:
            raise ValueError(f"نوع فیلد «{name}» شناخته نشد.")
        options = _option_text(field.get("options"))
        if kind == "number" and not _number_ok(value):
            raise ValueError(f"فیلد «{name}» فقط عدد می‌پذیرد.")
        if kind == "select":
            allowed = [item for item in options.split("\n") if item]
            if not allowed:
                raise ValueError(f"برای فیلد انتخابی «{name}» حداقل یک گزینه بنویسید.")
            if value not in allowed:
                raise ValueError(f"مقدار «{value}» بین گزینه‌های «{name}» نیست.")
        prepared.append({"name": name, "value": value, "kind": kind, "options": options if kind == "select" else ""})
    return prepared


def request_pairs(person: Dict[str, Any]) -> List[List[str]]:
    """Shared request fields from settings, with this applicant's own values on top."""
    merged: Dict[str, List[str]] = {}
    for source in (request_defaults(), person.get("fields") or []):
        for field in source:
            name = str(field.get("name") or "").strip()
            value = str(field.get("value") or "").strip()
            if name and value:
                merged[_norm(name)] = [name, value]
    return list(merged.values())


def set_name_if_empty(customer_id: int, full_name: str) -> bool:
    """Keep a typed name; fill an empty one from the Sadaf panel."""
    name = " ".join(str(full_name or "").split())
    if not name:
        return False
    ensure_schema()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE customers SET full_name = ?, updated_at = ? WHERE id = ? AND TRIM(full_name) = ''",
            (name, now(), customer_id),
        )
    return cur.rowcount > 0


def list_customers(query: str = "") -> List[Dict[str, Any]]:
    ensure_schema()
    like = f"%{query.strip()}%"
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.*,
                   (SELECT status FROM run_items ri
                    WHERE ri.customer_id = c.id
                    ORDER BY ri.id DESC LIMIT 1) AS last_status,
                   (SELECT finished_at FROM run_items ri
                    WHERE ri.customer_id = c.id
                    ORDER BY ri.id DESC LIMIT 1) AS last_finished_at,
                   (SELECT COUNT(*) FROM request_fields rf WHERE rf.customer_id = c.id) AS field_count
            FROM customers c
            WHERE ? = '' OR c.username LIKE ? OR c.full_name LIKE ? OR c.phone LIKE ?
            ORDER BY c.id
            """,
            (query.strip(), like, like, like),
        ).fetchall()
    return [public_customer(row) for row in rows]


def get_customer(customer_id: int, with_secret: bool = False) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT c.*,
                   (SELECT status FROM run_items ri
                    WHERE ri.customer_id = c.id
                    ORDER BY ri.id DESC LIMIT 1) AS last_status,
                   (SELECT finished_at FROM run_items ri
                    WHERE ri.customer_id = c.id
                    ORDER BY ri.id DESC LIMIT 1) AS last_finished_at
            FROM customers c WHERE c.id = ?
            """,
            (customer_id,),
        ).fetchone()
        if row is None:
            return None
        fields = conn.execute(
            "SELECT field_name, field_value, field_kind, field_options FROM request_fields WHERE customer_id = ? ORDER BY id",
            (customer_id,),
        ).fetchall()
    item = public_customer(row, [
        {"name": f["field_name"], "value": f["field_value"], "kind": f["field_kind"] or "text", "options": f["field_options"] or ""}
        for f in fields
    ])
    if with_secret:
        item["password"] = open_secret(row["password_blob"])
    return item


def save_customer(payload: Dict[str, Any], customer_id: Optional[int] = None) -> int:
    ensure_schema()
    username = str(payload.get("username") or "").strip()
    if not username:
        raise ValueError("نام کاربری لازم است.")
    password = str(payload.get("password") or "")
    fields = prepare_fields(payload.get("fields")) if "fields" in payload else []
    replace_fields = "fields" in payload
    stamp = now()
    try:
        with connect() as conn:
            if customer_id is None:
                if not password:
                    raise ValueError("رمز عبور لازم است.")
                cur = conn.execute(
                    """
                    INSERT INTO customers
                        (full_name, username, password_blob, phone, customer_type, active, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(payload.get("full_name") or "").strip(),
                        username,
                        seal(password),
                        str(payload.get("phone") or "").strip(),
                        str(payload.get("customer_type") or "").strip(),
                        1 if payload.get("active", True) else 0,
                        str(payload.get("notes") or "").strip(),
                        stamp,
                        stamp,
                    ),
                )
                customer_id = int(cur.lastrowid)
            else:
                current = conn.execute("SELECT id FROM customers WHERE id = ?", (customer_id,)).fetchone()
                if current is None:
                    raise ValueError("مشتری پیدا نشد.")
                blob = seal(password) if password else None
                conn.execute(
                    """
                    UPDATE customers
                    SET full_name = ?, username = ?, phone = ?, customer_type = ?, active = ?, notes = ?, updated_at = ?,
                        password_blob = COALESCE(?, password_blob)
                    WHERE id = ?
                    """,
                    (
                        str(payload.get("full_name") or "").strip(),
                        username,
                        str(payload.get("phone") or "").strip(),
                        str(payload.get("customer_type") or "").strip(),
                        1 if payload.get("active", True) else 0,
                        str(payload.get("notes") or "").strip(),
                    stamp,
                    blob,
                    customer_id,
                ),
            )
            target_id = int(customer_id)
            if replace_fields:
                conn.execute("DELETE FROM request_fields WHERE customer_id = ?", (target_id,))
                for field in fields:
                    conn.execute(
                        """
                        INSERT INTO request_fields
                            (customer_id, field_name, field_value, field_kind, field_options)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (target_id, field["name"], field["value"], field["kind"], field["options"]),
                    )
            return target_id
    except sqlite3.IntegrityError as exc:
        text = str(exc).lower()
        if "field" in text:
            raise ValueError("یک فیلد درخواست تکراری است.") from exc
        raise ValueError("این نام کاربری قبلاً ثبت شده است.") from exc


def delete_customer(customer_id: int) -> None:
    ensure_schema()
    with connect() as conn:
        conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))


def summary_counts() -> Dict[str, int]:
    ensure_schema()
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM customers").fetchone()["n"]
        active = conn.execute("SELECT COUNT(*) AS n FROM customers WHERE active = 1").fetchone()["n"]
        rows = conn.execute(
            """
            SELECT c.active,
                   (SELECT status FROM run_items ri
                    WHERE ri.customer_id = c.id
                    ORDER BY ri.id DESC LIMIT 1) AS status
            FROM customers c
            """
        ).fetchall()
    counts = {"success": 0, "failed": 0, "skipped": 0, "needs_review": 0, "waiting": 0}
    for row in rows:
        if not row["active"]:
            continue
        status = row["status"] or ""
        if status == "success":
            counts["success"] += 1
        elif status == "failed":
            counts["failed"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
        elif status == "needs_review":
            counts["needs_review"] += 1
        else:
            counts["waiting"] += 1
    counts["total"] = total
    counts["active"] = active
    counts["remaining"] = counts["waiting"] + counts["failed"] + counts["needs_review"]
    return counts


def customer_for_run(customer_id: int) -> Dict[str, Any]:
    item = get_customer(customer_id, with_secret=True)
    if not item:
        raise ValueError("مشتری پیدا نشد.")
    if not item.get("password"):
        raise ValueError("رمز این مشتری روی این ویندوز باز نمی‌شود.")
    return item


def runnable_customers(force: bool) -> List[Dict[str, Any]]:
    ensure_schema()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id,
                   (SELECT status FROM run_items ri
                    WHERE ri.customer_id = c.id
                    ORDER BY ri.id DESC LIMIT 1) AS status
            FROM customers c
            WHERE c.active = 1
            ORDER BY c.id
            """
        ).fetchall()
    chosen = []
    for row in rows:
        if row["status"] == "success" and not force:
            continue
        item = get_customer(int(row["id"]), with_secret=True)
        if item:
            chosen.append(item)
    return chosen
