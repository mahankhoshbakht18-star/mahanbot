"""Small OTP relay used by the Android SMS app and Mahan bot.

Run locally:
    python otp_server.py

Production:
    uvicorn otp_server:app --host 127.0.0.1 --port 8010
"""

import asyncio
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Dict

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("MAHAN_OTP_DB", os.path.join(BASE_DIR, "otp_codes.db"))
OTP_TTL_SECONDS = max(30, int(os.getenv("MAHAN_OTP_TTL", "180")))

app = FastAPI(title="Mahan OTP", version="1.1.0")
_events: Dict[str, asyncio.Event] = {}


class SMSRequest(BaseModel):
    nid: str
    code: str


def require_otp_secret(x_otp_secret: str = Header(default="", alias="X-OTP-Secret")):
    """Shared-secret guard for SMS ingestion (set MAHANBOT_OTP_SECRET)."""
    import hmac as _hmac

    expected = os.getenv("MAHAN_OTP_SECRET") or os.getenv("MAHANBOT_OTP_SECRET")
    if not expected:
        return  # not configured; deployment must keep this service local-only
    if not _hmac.compare_digest(str(x_otp_secret), expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OTP secret")


def normalize_digits(value: str) -> str:
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return str(value or "").translate(table).strip()


def normalize_nid(value: str) -> str:
    return normalize_digits(value).replace("-", "").replace(" ", "")


def normalize_code(value: str) -> str:
    return normalize_digits(value).replace(" ", "")


def validate(nid: str, code: str) -> None:
    if not re.fullmatch(r"\d{10}", nid):
        raise HTTPException(status_code=400, detail="Invalid national ID")
    if not re.fullmatch(r"\d{4,8}", code):
        raise HTTPException(status_code=400, detail="Invalid OTP")


@contextmanager
def db():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS otp_codes ("
            "nid TEXT PRIMARY KEY, code TEXT NOT NULL, received_at REAL NOT NULL)"
        )
        yield connection
        connection.commit()
    finally:
        connection.close()


def read_fresh(nid: str, min_ts: float = 0.0):
    with db() as connection:
        row = connection.execute(
            "SELECT code, received_at FROM otp_codes WHERE nid=?", (nid,)
        ).fetchone()
    if not row:
        return None
    code, received_at = row
    if received_at <= min_ts or time.time() - received_at > OTP_TTL_SECONDS:
        return None
    return {"nid": nid, "otp": code, "ts": received_at, "source": "fresh"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/receive_sms")
async def receive_sms(req: SMSRequest, _: None = Depends(require_otp_secret)):
    nid = normalize_nid(req.nid)
    code = normalize_code(req.code)
    validate(nid, code)
    received_at = time.time()
    with db() as connection:
        connection.execute(
            "INSERT INTO otp_codes(nid, code, received_at) VALUES(?,?,?) "
            "ON CONFLICT(nid) DO UPDATE SET code=excluded.code, received_at=excluded.received_at",
            (nid, code, received_at),
        )
    event = _events.setdefault(nid, asyncio.Event())
    event.set()
    return {"status": "ok", "ts": received_at}


@app.get("/get_otp/{nid}")
def get_otp(nid: str):
    normalized = normalize_nid(nid)
    if not re.fullmatch(r"\d{10}", normalized):
        raise HTTPException(status_code=400, detail="Invalid national ID")
    record = read_fresh(normalized)
    if not record:
        return Response(status_code=204)
    return record


@app.get("/wait_otp/{nid}")
async def wait_otp(nid: str, timeout: int = 120, min_ts: float = 0.0):
    normalized = normalize_nid(nid)
    if not re.fullmatch(r"\d{10}", normalized):
        raise HTTPException(status_code=400, detail="Invalid national ID")
    timeout = max(1, min(int(timeout), 300))
    deadline = time.monotonic() + timeout
    event = _events.setdefault(normalized, asyncio.Event())
    while True:
        record = read_fresh(normalized, float(min_ts))
        if record:
            return record
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return Response(status_code=204)
        event.clear()
        try:
            await asyncio.wait_for(event.wait(), remaining)
        except asyncio.TimeoutError:
            return Response(status_code=204)


@app.post("/otp/clear/{nid}")
def clear_otp(nid: str):
    normalized = normalize_nid(nid)
    with db() as connection:
        connection.execute("DELETE FROM otp_codes WHERE nid=?", (normalized,))
    return {"status": "cleared", "nid": normalized}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("otp_server:app", host="127.0.0.1", port=8010, reload=False)
