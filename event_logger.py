from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Mapping, Optional

from fastapi import WebSocket

from database import DBHandler

EVENT_BUFFER_SIZE = 500

_SENSITIVE_KEYS = {
    "otp",
    "otp_code",
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "session",
    "secret",
}

_OTP_PATTERNS = (
    re.compile(
        r"(?i)(\b(?:otp|one[- ]time(?:\s+password)?)(?:\s+(?:received|code|value|from\s+api))?\s*[:=\-]?\s*)([0-9]{4,8})\b"
    ),
    re.compile(
        r"((?:کد\s*)?(?:پیامک|تأیید|تایید|امنیتی)\s*[:=\-]?\s*)([۰-۹0-9]{4,8})"
    ),
)


def _mask_value(value: Any) -> str:
    raw = str(value or "")
    if not raw:
        return "***"
    if len(raw) <= 2:
        return "*" * len(raw)
    return "*" * max(4, len(raw) - 2) + raw[-2:]


def redact_sensitive_text(value: Any) -> str:
    text = str(value or "")
    for pattern in _OTP_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}{_mask_value(match.group(2))}", text)
    return text


def redact_sensitive_payload(value: Any, *, parent_key: Optional[str] = None) -> Any:
    """Recursively sanitize event payloads before persistence or broadcast."""

    normalized_key = str(parent_key or "").strip().lower().replace("-", "_")
    if normalized_key in _SENSITIVE_KEYS:
        return _mask_value(value)

    if isinstance(value, Mapping):
        return {
            str(key): redact_sensitive_payload(item, parent_key=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_payload(item, parent_key=parent_key) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_payload(item, parent_key=parent_key) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


class EventBroadcaster:
    def __init__(self, max_events: int = EVENT_BUFFER_SIZE):
        self.active_connections: List[WebSocket] = []
        self.event_history: Deque[Dict[str, Any]] = deque(maxlen=max_events)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

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
        sanitized_event = redact_sensitive_payload(event)
        self.event_history.append(sanitized_event)
        if not self.active_connections:
            return
        stale_connections = []
        for connection in list(self.active_connections):
            try:
                await self._safe_send(connection, sanitized_event)
            except Exception:
                stale_connections.append(connection)
        for connection in stale_connections:
            self.disconnect(connection)

    def emit_event(self, event: Dict[str, Any]) -> None:
        sanitized_event = redact_sensitive_payload(event)
        if not self._loop or not self._loop.is_running():
            self.event_history.append(sanitized_event)
            return
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop and running_loop is self._loop:
            asyncio.create_task(self.broadcast_event(sanitized_event))
        else:
            asyncio.run_coroutine_threadsafe(self.broadcast_event(sanitized_event), self._loop)

    async def _send_history(self, websocket: WebSocket) -> None:
        for event in list(self.event_history):
            try:
                await self._safe_send(websocket, event)
            except Exception:
                break

    async def _safe_send(self, websocket: WebSocket, event: Dict[str, Any]) -> None:
        payload = json.dumps(redact_sensitive_payload(event), ensure_ascii=False)
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


def build_event(
    event_type: str,
    nid: Optional[str] = None,
    job_id: Optional[str] = None,
    **payload: Any,
) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "type": event_type,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ts": time.time(),
    }
    if nid is not None:
        event["nid"] = nid
    if job_id is not None:
        event["job_id"] = job_id
    event.update(redact_sensitive_payload(payload))
    return event


def log_event(
    nid: str,
    job_id: Optional[str],
    message: str,
    level: str = "info",
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    safe_message = redact_sensitive_text(message)
    safe_meta = redact_sensitive_payload(meta or {})
    DBHandler.update_status(nid, level.title(), safe_message)
    event = build_event(
        "log",
        nid=nid,
        job_id=job_id,
        level=level,
        message=safe_message,
        meta=safe_meta,
    )
    EVENT_BROADCASTER.emit_event(event)
    try:
        applicant = DBHandler.get_applicant(nid)
        if applicant:
            record = dict(applicant)
            try:
                record["data"] = json.loads(record["data"]) if record.get("data") else {}
            except Exception:
                record["data"] = {}
            EVENT_BROADCASTER.emit_event(build_event("applicant_updated", **record))
    except Exception:
        pass


def job_status_event(
    nid: str,
    job_id: Optional[str],
    status: str,
    detail: Optional[str] = None,
) -> None:
    EVENT_BROADCASTER.emit_event(
        build_event("job_status", nid=nid, job_id=job_id, status=status, detail=detail)
    )


def metric_event(
    name: str,
    value: Any,
    nid: Optional[str] = None,
    job_id: Optional[str] = None,
) -> None:
    EVENT_BROADCASTER.emit_event(
        build_event("metric", nid=nid, job_id=job_id, name=name, value=value)
    )


def build_log_callback(job_id: Optional[str]):
    def _callback(
        nid: str,
        message: str,
        level: str = "info",
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        log_event(nid, job_id, message, level, meta=meta)

    return _callback
