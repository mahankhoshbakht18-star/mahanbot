# -*- coding: utf-8 -*-
"""NIOPDC SMS inbox for Sadaf.

Only three message shapes are kept:
  * «کد تایید 8510273»                      -> a 7-digit form code for the open run
  * «ثبت درخواست:36858316: 2000 لیتر نفتگاز» -> tracking code for the Jahad file
  * «تایید پخش:36858316: 60 لیتر نفتگاز»     -> delivery status of that tracking code
Password and bank-card messages are dropped before anything is stored.
The loan inbox (otp_codes.db) is never read or written here.
"""
import csv
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from .db import connect, ensure_schema
from .paths import REPORTS

SENDER = "98404010066600"
HEADER = "*NIOPDC*"
JAHAD_FILE = REPORTS / "jahad-requests.csv"
JAHAD_COLUMNS = ["نام کاربر", "نام کاربری", "کد رهگیری", "فرآورده", "مقدار", "تاریخ پیامک", "وضعیت پخش"]
CODE_TTL = 600
SUBMIT_WINDOW = 3600

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_SECRET = re.compile(r"کلمه\s*عبور|رمز\s*عبور|کارت\s*بانکی|رمز\s*کارت|cvv2?", re.IGNORECASE)
_CODE = re.compile(r"کد\s*تایید\s*[:：]?\s*(\d{7})(?!\d)")
_REQUEST = re.compile(r"ثبت\s*درخواست\s*[:：]\s*(\d{5,12})\s*[:：]\s*(\d+(?:[.,]\d+)?)\s*لیتر\s*([^\s\d:：،,]+)")
_DELIVERY = re.compile(r"تایید\s*پخش\s*[:：]\s*(\d{5,12})\s*[:：]\s*(\d+(?:[.,]\d+)?)\s*لیتر\s*([^\s\d:：،,]+)")
_DATE = re.compile(r"(1[34]\d{2})/(\d{1,2})/(\d{1,2})")
_TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?::\d{2})?(?!\d)")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sms_codes (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    received_at REAL NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    used_at REAL
);
CREATE TABLE IF NOT EXISTS jahad_requests (
    tracking TEXT PRIMARY KEY,
    customer_id INTEGER,
    full_name TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    product TEXT NOT NULL DEFAULT '',
    amount TEXT NOT NULL DEFAULT '',
    sms_at TEXT NOT NULL DEFAULT '',
    delivery TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sms_seen (
    message_id TEXT PRIMARY KEY,
    seen_at REAL NOT NULL
);
"""

_LOCK = threading.Lock()
_SUBMIT: Dict[str, Any] = {}


def _ready() -> None:
    ensure_schema()
    with connect() as conn:
        conn.executescript(_SCHEMA)


def normalize(text: str) -> str:
    value = str(text or "").translate(_DIGITS)
    value = value.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک").replace("ئ", "ی")
    value = value.replace("أ", "ا").replace("‌", " ").replace("‏", "").replace("‎", "")
    return re.sub(r"[ \t]+", " ", value).strip()


def sender_ok(sender: str) -> bool:
    digits = "".join(ch for ch in normalize(sender) if ch.isdigit())
    return digits == SENDER


def parse(body: str, sender: str = "") -> Optional[Dict[str, str]]:
    """Return the useful part of one NIOPDC SMS, or None.

    sender is checked only when it is given; a pasted message has no sender
    and must then carry the *NIOPDC* header.
    """
    text = normalize(body)
    if not text or len(text) > 1200:
        return None
    if sender and not sender_ok(sender):
        return None
    if HEADER.lower() not in text.lower() and not sender:
        if not _CODE.fullmatch(text.strip()):
            return None
    if _SECRET.search(text):
        return {"kind": "secret"}
    request = _REQUEST.search(text)
    if request:
        return {"kind": "request", "tracking": request.group(1), "amount": request.group(2),
                "product": request.group(3), "sms_at": _stamp(text)}
    delivery = _DELIVERY.search(text)
    if delivery:
        return {"kind": "delivery", "tracking": delivery.group(1), "amount": delivery.group(2),
                "product": delivery.group(3), "sms_at": _stamp(text)}
    codes = _CODE.findall(text)
    if len(codes) == 1:
        rest = _TIME.sub(" ", _DATE.sub(" ", text))
        others = [run for run in re.findall(r"\d+", rest) if run != codes[0]]
        if not others:
            return {"kind": "code", "code": codes[0]}
    return None


def _stamp(text: str) -> str:
    date = _DATE.search(text)
    clock = _TIME.search(text)
    parts = []
    if date:
        parts.append(f"{date.group(1)}/{int(date.group(2)):02d}/{int(date.group(3)):02d}")
    if clock:
        parts.append(f"{int(clock.group(1)):02d}:{clock.group(2)}")
    return " ".join(parts)


def note_submit(customer: Dict[str, Any]) -> None:
    """The runner calls this right before the final confirm click."""
    with _LOCK:
        _SUBMIT.clear()
        _SUBMIT.update({
            "id": customer.get("id"),
            "full_name": customer.get("full_name") or "",
            "username": customer.get("username") or "",
            "at": time.time(),
        })


def _submitter() -> Dict[str, Any]:
    with _LOCK:
        if _SUBMIT and time.time() - float(_SUBMIT.get("at") or 0) < SUBMIT_WINDOW:
            return dict(_SUBMIT)
    return {}


def ingest(parsed: Optional[Dict[str, str]], source: str, message_id: str = "",
           received_at: Optional[float] = None) -> str:
    """Store one parsed message. Returns its kind, or '' when nothing was kept."""
    if not parsed or parsed.get("kind") in {None, "secret"}:
        return ""
    _ready()
    received = float(received_at or time.time())
    message_id = str(message_id or "")[:160]
    with connect() as conn:
        if message_id:
            hit = conn.execute("SELECT 1 FROM sms_seen WHERE message_id = ?", (message_id,)).fetchone()
            if hit:
                return "duplicate"
            conn.execute("INSERT INTO sms_seen(message_id, seen_at) VALUES(?, ?)", (message_id, time.time()))
        kind = parsed["kind"]
        if kind == "code":
            if time.time() - received > CODE_TTL:
                return "stale"
            conn.execute(
                "INSERT INTO sms_codes(code, received_at, source, message_id) VALUES(?, ?, ?, ?)",
                (parsed["code"], received, source[:32], message_id),
            )
        elif kind == "request":
            _upsert_request(conn, parsed)
        elif kind == "delivery":
            _upsert_delivery(conn, parsed)
    if kind in {"request", "delivery"}:
        export_jahad()
    _event(kind, parsed)
    return kind


def _event(kind: str, parsed: Dict[str, str]) -> None:
    if kind == "code":
        message = "پیامک کد تایید سدف رسید."
    elif kind == "request":
        message = f"پیامک ثبت درخواست رسید. کد رهگیری {parsed['tracking']} در فایل جهاد ذخیره شد."
    else:
        message = f"پیامک تایید پخش برای کد رهگیری {parsed['tracking']} رسید."
    with connect() as conn:
        conn.execute(
            "INSERT INTO events(created_at, level, message, customer_id, run_id) VALUES(?, 'info', ?, NULL, NULL)",
            (datetime.now().replace(microsecond=0).isoformat(sep=" "), message),
        )


def _upsert_request(conn, parsed: Dict[str, str]) -> None:
    who = _submitter()
    stamp = datetime.now().replace(microsecond=0).isoformat(sep=" ")
    row = conn.execute("SELECT customer_id FROM jahad_requests WHERE tracking = ?", (parsed["tracking"],)).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO jahad_requests(tracking, customer_id, full_name, username, product, amount, sms_at, delivery, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, '', ?)""",
            (parsed["tracking"], who.get("id"), who.get("full_name", ""), who.get("username", ""),
             parsed["product"], parsed["amount"], parsed["sms_at"], stamp),
        )
        return
    conn.execute(
        """UPDATE jahad_requests SET product = ?, amount = ?, sms_at = ?, updated_at = ?,
               customer_id = COALESCE(customer_id, ?),
               full_name = CASE WHEN full_name = '' THEN ? ELSE full_name END,
               username = CASE WHEN username = '' THEN ? ELSE username END
           WHERE tracking = ?""",
        (parsed["product"], parsed["amount"], parsed["sms_at"], stamp,
         who.get("id"), who.get("full_name", ""), who.get("username", ""), parsed["tracking"]),
    )


def _upsert_delivery(conn, parsed: Dict[str, str]) -> None:
    stamp = datetime.now().replace(microsecond=0).isoformat(sep=" ")
    status = f"تایید پخش {parsed['amount']} لیتر {parsed['product']}"
    if parsed.get("sms_at"):
        status += f" ({parsed['sms_at']})"
    row = conn.execute("SELECT 1 FROM jahad_requests WHERE tracking = ?", (parsed["tracking"],)).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO jahad_requests(tracking, product, sms_at, delivery, updated_at)
               VALUES(?, ?, ?, ?, ?)""",
            (parsed["tracking"], parsed["product"], parsed["sms_at"], status, stamp),
        )
        return
    conn.execute("UPDATE jahad_requests SET delivery = ?, updated_at = ? WHERE tracking = ?",
                 (status, stamp, parsed["tracking"]))


def record_page_tracking(tracking: str, customer: Dict[str, Any], product: str = "", amount: str = "") -> None:
    """Keep a tracking code that the site itself printed after the final confirm."""
    tracking = "".join(ch for ch in normalize(tracking) if ch.isdigit())
    if not tracking:
        return
    _ready()
    stamp = datetime.now().replace(microsecond=0).isoformat(sep=" ")
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM jahad_requests WHERE tracking = ?", (tracking,)).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO jahad_requests(tracking, customer_id, full_name, username, product, amount, sms_at, delivery, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, '', '', ?)""",
                (tracking, customer.get("id"), customer.get("full_name") or "", customer.get("username") or "",
                 product, amount, stamp),
            )
        else:
            conn.execute(
                """UPDATE jahad_requests SET customer_id = COALESCE(customer_id, ?),
                       full_name = CASE WHEN full_name = '' THEN ? ELSE full_name END,
                       username = CASE WHEN username = '' THEN ? ELSE username END, updated_at = ?
                   WHERE tracking = ?""",
                (customer.get("id"), customer.get("full_name") or "", customer.get("username") or "", stamp, tracking),
            )
    export_jahad()


def tracking_for(customer_id: int, since_text: str) -> List[str]:
    _ready()
    with connect() as conn:
        rows = conn.execute(
            "SELECT tracking FROM jahad_requests WHERE customer_id = ? AND updated_at >= ? ORDER BY updated_at DESC",
            (customer_id, since_text),
        ).fetchall()
    return [row["tracking"] for row in rows]


def jahad_rows() -> List[Dict[str, str]]:
    _ready()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM jahad_requests ORDER BY updated_at DESC").fetchall()
    return [{
        "full_name": row["full_name"], "username": row["username"], "tracking": row["tracking"],
        "product": row["product"], "amount": row["amount"], "sms_at": row["sms_at"], "delivery": row["delivery"],
    } for row in rows]


def export_jahad() -> str:
    rows = jahad_rows()
    REPORTS.mkdir(parents=True, exist_ok=True)
    temp = JAHAD_FILE.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(JAHAD_COLUMNS)
        for row in rows:
            writer.writerow([row["full_name"], row["username"], row["tracking"], row["product"],
                             row["amount"], row["sms_at"], row["delivery"]])
    temp.replace(JAHAD_FILE)
    return str(JAHAD_FILE)


def take_code(since: float) -> Optional[Dict[str, Any]]:
    """Newest unused 7-digit code that arrived after since. It is marked used."""
    _ready()
    floor = max(float(since) - 5, time.time() - CODE_TTL)
    with connect() as conn:
        row = conn.execute(
            "SELECT id, code FROM sms_codes WHERE used_at IS NULL AND received_at > ? ORDER BY received_at DESC, id DESC LIMIT 1",
            (floor,),
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE sms_codes SET used_at = ? WHERE id = ?", (time.time(), row["id"]))
    return {"id": row["id"], "code": row["code"]}


def manual(text: str) -> str:
    """Operator paste: either the bare 7-digit code or the whole SMS text."""
    raw = normalize(text)
    if re.fullmatch(r"\d{7}", raw):
        return ingest({"kind": "code", "code": raw}, "manual")
    parsed = parse(raw)
    if parsed and parsed.get("kind") == "secret":
        return "secret"
    return ingest(parsed, "manual")
