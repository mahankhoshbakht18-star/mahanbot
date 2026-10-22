"""Bounded OTP polling with local fallback, freshness checks and redacted diagnostics."""
import hashlib
import os
import time
from email.utils import parsedate_to_datetime

import requests
from database import DBHandler


def normalized_code(value):
    return str(value or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")).strip()


def fresh_record(bot, record, now=None):
    if not isinstance(record, dict):
        return None
    now = time.time() if now is None else now
    code = normalized_code(record.get("otp") or record.get("code"))
    try:
        ts = float(record.get("ts") or 0)
    except (TypeError, ValueError):
        return None
    if record.get("nid") and str(record["nid"]) != str(bot.nid):
        return None
    request_id = record.get("request_id")
    if request_id and request_id != getattr(bot, "_otp_request_id", None):
        return None
    if record.get("status") == "consumed":
        return None
    is_manual = record.get("status") == "manual" or record.get("source") == "manual"
    digest = hashlib.sha256(code.encode()).hexdigest()
    if not is_manual and digest in getattr(bot, "_consumed_otp_digests", set()):
        return None
    issued = float(getattr(bot, "_otp_entry_time", 0) or 0)
    ttl = float(bot.settings.get("otp_validity_seconds", 120))
    if not code.isascii() or not code.isdigit() or not 4 <= len(code) <= 8:
        return None
    if not ts:
        return None
    if is_manual:
        # User manually entered the OTP: accept immediately if within 120s validity window
        if now - ts > ttl or now - ts < -5:
            return None
        return code
    # Automated SMS: allow 15s grace window before issued to accommodate SMS arriving during page transition
    if ts < (issued - 15.0) or not -2 <= now - ts <= ttl or (issued and now - issued >= ttl):
        return None
    return code


def poll_once(bot, stop_event=None, timeout=1):
    if stop_event is not None and stop_event.is_set():
        return None
    local = DBHandler.get_otp_record(bot.nid)
    code = fresh_record(bot, local)
    if code:
        return code
    from relay_transport import config as relay_config, poll as relay_poll
    if relay_config():
        remote = relay_poll(bot, timeout)
        if stop_event is not None and stop_event.is_set():
            return None
        code = fresh_record(bot, remote)
        if code:
            DBHandler.set_otp(bot.nid, code, ts=float(remote["ts"]), request_id=getattr(bot, "_otp_request_id", None))
            DBHandler.update_runtime(bot.nid, otp_transport_error="", otp_state="SMS_RECEIVED")
            return code
    headers = {}
    api_key = os.getenv("MAHANBOT_API_KEY")
    secret = os.getenv("MAHANBOT_OTP_SECRET") or os.getenv("MAHAN_OTP_SECRET")
    if api_key:
        headers["X-API-KEY"] = api_key
    if secret:
        headers["X-OTP-Secret"] = secret
    token = os.getenv("MAHANBOT_OTP_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Long-poll slices remain cancellable and do not stall browser manual entry.
    slice_seconds = max(1, min(int(timeout), 2))
    # Manual codes are already read from SQLite. Query the configured Android relay first.
    endpoints = [bot.local_api_base] if relay_config() else list(dict.fromkeys((bot.api_base_url, bot.local_api_base)))
    for base in endpoints:
        if stop_event is not None and stop_event.is_set():
            return None
        try:
            response = requests.get(f"{base}/wait_otp/{bot.nid}",
                                    params={"timeout": slice_seconds, "min_ts": 0},
                                    headers=headers, timeout=(1, slice_seconds + 1))
            if stop_event is not None and stop_event.is_set():
                return None
            if response.status_code == 204:
                continue
            if response.status_code != 200:
                DBHandler.update_runtime(bot.nid, otp_transport_error=f"http_{response.status_code}")
                continue
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("invalid_json_shape")
            # Map remote server timestamps into this process clock using HTTP Date.
            if base != bot.local_api_base and response.headers.get("Date") and data.get("ts"):
                server_now = parsedate_to_datetime(response.headers["Date"]).timestamp()
                skew = time.time() - server_now
                if abs(skew) > 3:
                    data["ts"] = float(data["ts"]) + skew
                    DBHandler.update_runtime(bot.nid, otp_clock_skew_seconds=round(skew, 1))
            code = fresh_record(bot, data)
            if code:
                DBHandler.set_otp(bot.nid, code, ts=float(data["ts"]), request_id=getattr(bot, "_otp_request_id", None))
                DBHandler.update_runtime(bot.nid, otp_transport_error="", otp_state="SMS_RECEIVED")
                return code
            DBHandler.update_runtime(bot.nid, otp_transport_error="stale_or_mismatched_record")
        except requests.Timeout:
            DBHandler.update_runtime(bot.nid, otp_transport_error="network_timeout")
        except requests.ConnectionError:
            DBHandler.update_runtime(bot.nid, otp_transport_error="connection_failed")
        except (ValueError, TypeError, KeyError):
            DBHandler.update_runtime(bot.nid, otp_transport_error="invalid_json_or_timestamp")
    return None
