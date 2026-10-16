import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from fastapi import WebSocket

from database import DBHandler

EVENT_BUFFER_SIZE = 500

# Mandatory structured event names (Section 10)
STRUCTURED_EVENT_TYPES = {
    "BANK_PAGE_DETECTED",
    "BANK_PREFERENCES_LOADED",
    "BANK_OPTION_FOUND",
    "BANK_OPTION_SELECTED",
    "BANK_SELECTION_VERIFIED",
    "BANK_MODE_SELECTED",
    "BANK_MODE_VERIFIED",
    "BRANCH_SELECTED",
    "BANK_CAPACITY_FULL",
    "BANK_OPTION_NOT_FOUND",
    "BANK_SELECTION_FAILED",
    "BANK_FORM_READY",
    "BANK_FINAL_SUBMIT_CONFIRMED",
    "OTP_REQUIRED",
    "OTP_EXPIRED",
    "CAPTCHA_MANUAL_REQUIRED",
    "CHECKPOINT_SAVED",
    "CHECKPOINT_RESTORED",
    "RECOVERY_STARTED",
    "RECOVERY_COMPLETED",
    "WAF_BLOCKED",
    "FINAL_SUBMIT_STARTED",
    "FINAL_SUBMIT_CONFIRMED",
    "FINAL_SUBMIT_REJECTED",
}

SENSITIVE_KEYS = {
    "otp",
    "otp_code",
    "captcha",
    "captcha_code",
    "cookie",
    "cookies",
    "password",
    "password_hash",
    "password_salt",
    "token",
    "secret",
    "national_id",
    "spouse_national_id",
    "mobile",
    "phone",
    "zip_code",
    "postal_code",
}


def mask_sensitive_value(value: Any, visible: int = 2) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= visible * 2:
        return "*" * len(text)
    return f"{text[:visible]}{'*' * (len(text) - visible * 2)}{text[-visible:]}"


def sanitize_log_text(text: str) -> str:
    """Mask 10-digit national IDs / postal codes and 11-digit mobile numbers in public log text."""
    if not text:
        return ""
    out = str(text)
    # Mask 11-digit mobile numbers (09xxxxxxxxx)
    out = re.sub(r"\b(09\d)(\d{6})(\d{2})\b", r"\1******\3", out)
    # Mask 10-digit numeric strings (National IDs / Postal Codes)
    out = re.sub(r"\b(\d{2})(\d{6})(\d{2})\b", r"\1******\3", out)
    return out


def sanitize_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    cleaned: Dict[str, Any] = {}
    for k, v in meta.items():
        key_low = str(k).lower()
        if key_low in {"otp", "otp_code", "captcha", "captcha_code", "cookie", "cookies", "password", "secret", "token"}:
            continue
        if key_low in SENSITIVE_KEYS:
            cleaned[k] = mask_sensitive_value(v)
        elif isinstance(v, dict):
            cleaned[k] = sanitize_meta(v)
        elif isinstance(v, str):
            cleaned[k] = sanitize_log_text(v)
        else:
            cleaned[k] = v
    return cleaned


class EventBroadcaster:
    def __init__(self, max_events: int = EVENT_BUFFER_SIZE):
        self.active_connections: List[WebSocket] = []
        self.event_history: Deque[Dict[str, Any]] = deque(maxlen=max_events)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_log_by_nid: Dict[str, Dict[str, Any]] = {}

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        await self._send_history(websocket)
        await self.broadcast_event(
            build_event("metric", name="ws_clients", value=len(self.active_connections))
        )

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            self.emit_event(build_event("metric", name="ws_clients", value=len(self.active_connections)))

    async def broadcast_event(self, event: Dict[str, Any]) -> None:
        self.event_history.append(event)
        if not self.active_connections:
            return
        stale_connections = []
        for connection in list(self.active_connections):
            try:
                await self._safe_send(connection, event)
            except Exception:
                stale_connections.append(connection)
        for connection in stale_connections:
            self.disconnect(connection)

    def should_suppress_duplicate(self, nid: Optional[str], message: str, event_code: Optional[str] = None, window_sec: float = 5.0) -> bool:
        key = str(nid or "system")
        sig = f"{event_code or ''}:{message}"
        now = time.time()
        prev = self._last_log_by_nid.get(key)
        if prev and prev.get("sig") == sig and (now - prev.get("ts", 0.0)) < window_sec:
            prev["count"] = prev.get("count", 1) + 1
            return True
        self._last_log_by_nid[key] = {"sig": sig, "ts": now, "count": 1}
        return False

    def emit_event(self, event: Dict[str, Any]) -> None:
        if not self._loop or not self._loop.is_running():
            self.event_history.append(event)
            return
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop and running_loop is self._loop:
            asyncio.create_task(self.broadcast_event(event))
        else:
            asyncio.run_coroutine_threadsafe(self.broadcast_event(event), self._loop)

    async def _send_history(self, websocket: WebSocket) -> None:
        for event in list(self.event_history):
            try:
                await self._safe_send(websocket, event)
            except Exception:
                break

    async def _safe_send(self, websocket: WebSocket, event: Dict[str, Any]) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        send_lock = getattr(websocket.state, "send_lock", None)
        if send_lock:
            async with send_lock:
                await websocket.send_text(payload)
        else:
            await websocket.send_text(payload)


EVENT_BROADCASTER = EventBroadcaster()


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    EVENT_BROADCASTER.set_loop(loop)


def create_job_id() -> str:
    return uuid.uuid4().hex


def build_event(event_type: str, nid: Optional[str] = None, job_id: Optional[str] = None, **payload: Any) -> Dict[str, Any]:
    event = {
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "ts": time.time(),
    }
    if nid is not None:
        event["nid"] = mask_sensitive_value(nid) if event_type == "public_log" else nid
        event["request_id"] = mask_sensitive_value(nid)
    if job_id is not None:
        event["job_id"] = job_id
    event.update(payload)
    return event


def log_event(
    nid: str,
    job_id: Optional[str],
    message: str,
    level: str = "info",
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    safe_msg = sanitize_log_text(message)
    safe_meta = sanitize_meta(meta)
    event_code = safe_meta.get("event_code")

    if EVENT_BROADCASTER.should_suppress_duplicate(nid, safe_msg, event_code=event_code):
        return

    DBHandler.update_last_log(nid, safe_msg)
    event = build_event("log", nid=nid, job_id=job_id, level=level, message=safe_msg)
    if safe_meta:
        event["meta"] = safe_meta
        for field in ("event_code", "step", "reason", "result"):
            if field in safe_meta:
                event[field] = safe_meta[field]
    EVENT_BROADCASTER.emit_event(event)
    try:
        applicant = DBHandler.get_applicant(nid)
        if applicant:
            record = dict(applicant)
            try:
                record["data"] = json.loads(record["data"]) if record.get("data") else {}
            except Exception:
                record["data"] = {}
            # Strip raw OTP from applicant broadcast
            if isinstance(record["data"], dict):
                record["data"] = dict(record["data"])
                record["data"].pop("otp_code", None)
            record["runtime"] = DBHandler.get_runtime(nid)
            EVENT_BROADCASTER.emit_event(build_event("applicant_updated", **record))
    except Exception:
        pass


def emit_structured_event(
    nid: str,
    event_code: str,
    *,
    step: str,
    reason: str = "",
    result: str = "",
    message: str = "",
    level: str = "info",
    job_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Emit one of the 15 canonical structured events with timestamp, masked request ID, step, reason, and result."""
    meta: Dict[str, Any] = {
        "event_code": event_code,
        "request_id": mask_sensitive_value(nid),
        "step": step,
        "reason": sanitize_log_text(reason),
        "result": sanitize_log_text(result),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
    }
    if extra:
        meta.update(sanitize_meta(extra))
    formatted_msg = message or f"[{event_code}] step={step} reason={reason or '-'} result={result or '-'}"
    if event_code not in formatted_msg:
        formatted_msg = f"[{event_code}] {formatted_msg}"
    log_event(nid, job_id, formatted_msg, level=level, meta=meta)

    # Real-time WebSocket event dispatch for frontend notifications, audio alerts, and confetti
    try:
        if event_code == "BANK_OPTION_FOUND":
            EVENT_BROADCASTER.emit_event(
                build_event(
                    "favorite_found",
                    nid=nid,
                    job_id=job_id,
                    bank=result or reason,
                    detail=reason,
                    message=f"سهمیه بانک {result or reason} آماده ثبت شد!",
                )
            )
        elif event_code in {"BANK_FINAL_SUBMIT_CONFIRMED", "FINAL_SUBMIT_CONFIRMED"}:
            EVENT_BROADCASTER.emit_event(
                build_event(
                    "registration_confirmed",
                    nid=nid,
                    job_id=job_id,
                    tracking_code=result,
                    detail=reason,
                    message=f"ثبت نام قطعی انجام شد! {result}",
                )
            )
        elif event_code == "OTP_REQUIRED":
            EVENT_BROADCASTER.emit_event(
                build_event(
                    "otp_required",
                    nid=nid,
                    job_id=job_id,
                    detail=reason,
                    message="کد پیامک OTP مورد نیاز است.",
                )
            )
        elif event_code == "BANK_CAPACITY_FULL":
            EVENT_BROADCASTER.emit_event(
                build_event(
                    "capacity_full",
                    nid=nid,
                    job_id=job_id,
                    detail=reason,
                    message=f"ظرفیت بانک تکمیل است: {reason}",
                )
            )
    except Exception:
        pass

    return meta


def job_status_event(nid: str, job_id: Optional[str], status: str, detail: Optional[str] = None) -> None:
    EVENT_BROADCASTER.emit_event(
        build_event("job_status", nid=nid, job_id=job_id, status=status, detail=detail)
    )


def metric_event(name: str, value: Any, nid: Optional[str] = None, job_id: Optional[str] = None) -> None:
    EVENT_BROADCASTER.emit_event(
        build_event("metric", nid=nid, job_id=job_id, name=name, value=value)
    )


def build_log_callback(job_id: Optional[str]):
    def _callback(nid: str, message: str, level: str = "info", meta: Optional[Dict[str, Any]] = None) -> None:
        log_event(nid, job_id, message, level, meta=meta)

    return _callback
