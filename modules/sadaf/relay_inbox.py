# -*- coding: utf-8 -*-
"""Pull NIOPDC messages that the phone app sent to the relay.

The phone posts parsed NIOPDC messages to /api/v1/sms/niopdc on the same
relay that carries loan codes. This thread reads them back with the bot key
from /api/v1/bot/niopdc and stores them in the Sadaf inbox. The loan routes
(/api/v1/sms/otp, /api/v1/bot/pull) are not touched.
"""
import threading
import time

import requests

from . import sms
from .db import connect, ensure_schema

_STARTED = False
_LOCK = threading.Lock()
STATE = {"connected": False, "last_error": "", "last_ok": 0.0}


def _config():
    try:
        from relay_transport import config
    except Exception:
        return None
    return config()


def _cursor() -> int:
    ensure_schema()
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'niopdc_cursor'").fetchone()
    try:
        return int(row["value"]) if row else 0
    except (TypeError, ValueError):
        return 0


def _save_cursor(value: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('niopdc_cursor', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(int(value)),),
        )


def pull_once(timeout: int = 20) -> int:
    configured = _config()
    if not configured:
        STATE.update(connected=False, last_error="relay_not_configured")
        return 0
    base, headers = configured
    after = _cursor()
    response = requests.get(base + "/api/v1/bot/niopdc", headers=headers,
                            params={"after_id": after, "timeout": timeout}, timeout=(3, timeout + 5))
    if response.status_code == 204:
        STATE.update(connected=True, last_error="", last_ok=time.time())
        return 0
    if response.status_code != 200:
        STATE.update(connected=False, last_error=f"relay_http_{response.status_code}")
        return 0
    data = response.json()
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        STATE.update(connected=False, last_error="relay_invalid_response")
        return 0
    count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        parsed = _from_relay(item)
        sms.ingest(parsed, "relay", "relay:" + str(item.get("message_id") or item.get("id")),
                   float(item.get("received_at") or time.time()))
        after = max(after, int(item.get("id") or 0))
        count += 1
    _save_cursor(after)
    STATE.update(connected=True, last_error="", last_ok=time.time())
    return count


def _from_relay(item):
    kind = str(item.get("kind") or "")
    digits = lambda value: "".join(ch for ch in sms.normalize(value) if ch.isdigit() or ch in ".,")
    if kind == "code":
        code = digits(item.get("code"))
        return {"kind": "code", "code": code} if len(code) == 7 and code.isdigit() else None
    if kind in {"request", "delivery"}:
        tracking = digits(item.get("tracking"))
        if not tracking.isdigit():
            return None
        return {"kind": kind, "tracking": tracking, "amount": digits(item.get("amount")),
                "product": sms.normalize(item.get("product"))[:40], "sms_at": sms.normalize(item.get("sms_at"))[:40]}
    return None


def _loop():
    delay = 2
    while True:
        try:
            if _config():
                pull_once()
                if STATE["connected"]:
                    delay = 2
                else:
                    # 404 means the relay has not been updated with the NIOPDC route yet.
                    time.sleep(300 if STATE["last_error"] == "relay_http_404" else delay)
                    delay = min(60, delay * 2)
            else:
                time.sleep(30)
        except requests.RequestException:
            STATE.update(connected=False, last_error="relay_connection_failed")
            time.sleep(delay)
            delay = min(60, delay * 2)
        except Exception:
            STATE.update(connected=False, last_error="relay_invalid_response")
            time.sleep(delay)
            delay = min(60, delay * 2)


def start() -> None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
    threading.Thread(target=_loop, name="sadaf-niopdc-inbox", daemon=True).start()
