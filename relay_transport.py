"""Authenticated Arvan relay client. Never sends SMS text, phone numbers or OTPs to logs."""
import hashlib
import os
import threading
import time
from email.utils import parsedate_to_datetime

import requests


def _transport_error(bot, code):
    from database import DBHandler
    DBHandler.update_runtime(bot.nid, otp_transport_error=code)


def _check_response(bot, response):
    if response.status_code in {401, 403}:
        # Credentials/configuration must change before another access attempt.
        base, headers = config()
        bot._relay_denied_config = (base, hashlib.sha256(headers['X-BOT-KEY'].encode()).hexdigest())
        _transport_error(bot, "relay_access_denied")
        return False
    if response.status_code == 429:
        try:
            delay = max(1, min(300, float(response.headers.get('Retry-After', 30))))
        except (TypeError, ValueError):
            delay = 30
        bot._relay_retry_at = time.monotonic() + delay
        _transport_error(bot, "relay_rate_limited")
        return False
    if response.status_code not in {200, 204}:
        _transport_error(bot, f"relay_http_{response.status_code}")
        return False
    return True


def _may_request(bot, configured):
    base, headers = configured
    fingerprint = (base, hashlib.sha256(headers['X-BOT-KEY'].encode()).hexdigest())
    stop = getattr(bot, '_active_stop_event', None)
    return (not (stop is not None and stop.is_set())
            and getattr(bot, '_relay_denied_config', None) != fingerprint
            and time.monotonic() >= getattr(bot, '_relay_retry_at', 0))


def config():
    if os.getenv("MAHAN_OTP_RELAY_DISABLED") == "1":
        return None
    key = os.getenv("RELAY_BOT_KEY") or os.getenv("MAHAN_OTP_BOT_KEY")
    url = (os.getenv("MAHAN_OTP_RELAY_URL") or "https://otp.mahanvip.ir").rstrip("/")
    if not key or not url.startswith("https://"):
        return None
    return url, {"X-BOT-KEY": key, "Cache-Control": "no-store"}


def phone_hash(value):
    phone = str(value or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    phone = phone.replace(" ", "").lstrip("+")
    if phone.startswith("0098"):
        phone = "0" + phone[4:]
    elif phone.startswith("98"):
        phone = "0" + phone[2:]
    if len(phone) != 11 or not phone.startswith("09") or not phone.isascii() or not phone.isdigit():
        return ""
    return hashlib.sha256(phone.encode()).hexdigest()


def publish_wait(bot):
    configured = config()
    request_id = getattr(bot, "_otp_request_id", None)
    if not configured or not request_id or not _may_request(bot, configured):
        return False
    lock = bot.__dict__.setdefault("_relay_publish_lock", threading.Lock())
    with lock:
        if not _may_request(bot, configured):
            return False
        if getattr(bot, "_relay_published_request", None) == request_id:
            return True
        base, headers = configured
        issued = float(getattr(bot, "_otp_entry_time", 0) or 0)
        ttl = max(60, min(300, int(bot.settings.get("otp_validity_seconds", 120))))
        if not issued or time.time() - issued >= ttl:
            return False
        data = getattr(bot, "user_data", {}) or {}
        try:
            response = requests.post(base + "/api/v1/bot/wait", headers=headers,
                json={"national_id": str(bot.nid), "waiting_since": issued, "job_id": request_id,
                      "ttl_seconds": max(60, ttl - int(time.time() - issued)),
                      "sender_hash": phone_hash(data.get("mobile") or data.get("phone"))}, timeout=(2, 3))
            if not _check_response(bot, response):
                return False
            if response.status_code != 200 or not isinstance(response.json(), dict) or not response.json().get("accepted"):
                _transport_error(bot, "relay_invalid_response")
                return False
            if request_id == getattr(bot, "_otp_request_id", None):
                bot._relay_published_request = request_id
            return True
        except requests.Timeout:
            _transport_error(bot, "relay_network_timeout")
            return False
        except requests.RequestException:
            _transport_error(bot, "relay_connection_failed")
            return False
        except (ValueError, TypeError):
            _transport_error(bot, "relay_invalid_response")
            return False


def queue_wait(bot):
    if config():
        threading.Thread(target=publish_wait, args=(bot,), name="otp-relay-registration", daemon=True).start()


def poll(bot, timeout=1):
    configured = config()
    if not configured or not _may_request(bot, configured) or not publish_wait(bot):
        return None
    base, headers = configured
    try:
        response = requests.get(base + "/api/v1/bot/pull/" + str(bot.nid), headers=headers,
            params={"timeout": max(0, min(2, int(timeout))), "after_ts": float(getattr(bot, "_otp_entry_time", 0) or 0)}, timeout=(2, 4))
        if not _check_response(bot, response) or response.status_code == 204:
            return None
        record = response.json()
        if not isinstance(record, dict):
            _transport_error(bot, "relay_invalid_response")
            return None
        if response.headers.get("Date") and record.get("ts"):
            skew = time.time() - parsedate_to_datetime(response.headers["Date"]).timestamp()
            if abs(skew) > 3:
                record["ts"] = float(record["ts"]) + skew
        return record
    except requests.Timeout:
        _transport_error(bot, "relay_network_timeout")
        return None
    except requests.RequestException:
        _transport_error(bot, "relay_connection_failed")
        return None
    except (ValueError, TypeError, KeyError):
        _transport_error(bot, "relay_invalid_response")
        return None


def finish(bot, consumed=False):
    configured = config()
    if not configured:
        return
    base, headers = configured
    action = "consumed" if consumed else "clear"
    try:
        requests.post(f"{base}/api/v1/bot/{action}/{bot.nid}", headers=headers,
            params={"request_id": getattr(bot, "_otp_request_id", "")}, timeout=(2, 3))
    except requests.RequestException:
        pass
