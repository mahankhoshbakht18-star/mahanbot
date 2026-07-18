from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Any, Dict, Literal, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from event_logger import EVENT_BROADCASTER, build_event
from server import app, require_api_key
from sms_notify_core import SMS_NOTIFY_REGISTRY, SmsArrivalSignal


logger = logging.getLogger("mahanbot.sms_notify")
SCRIPT_TAG = '<script src="/static/sms_notify_bridge.js" defer></script>'


class SmsNotifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=2, max_length=128)
    message_id: str = Field(min_length=4, max_length=256)
    received_at: Optional[float] = None
    source: Literal["android"] = "android"
    sender_hint: Optional[Literal["bank", "service", "unknown"]] = None


class SmsHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=2, max_length=128)
    sent_at: Optional[float] = None


def _configured_device_key() -> str:
    return str(os.getenv("MAHANBOT_SMS_DEVICE_KEY") or "").strip()


def require_sms_device_key(
    x_device_key: Optional[str] = Header(None, alias="X-DEVICE-KEY"),
    x_device_id: Optional[str] = Header(None, alias="X-DEVICE-ID"),
) -> Optional[str]:
    expected = _configured_device_key()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MAHANBOT_SMS_DEVICE_KEY is not configured",
        )
    supplied = str(x_device_key or "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid device key",
        )
    normalized_device_id = str(x_device_id or "").strip()
    if len(normalized_device_id) > 128:
        raise HTTPException(status_code=400, detail="Invalid X-DEVICE-ID")
    return normalized_device_id or None


def _safe_received_at(value: Optional[float]) -> float:
    now = time.time()
    if value is None:
        return now
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return now
    # Avoid accepting wildly stale/future timestamps from a broken phone clock.
    if parsed <= 0 or abs(parsed - now) > 24 * 60 * 60:
        return now
    return parsed


@app.middleware("http")
async def inject_sms_notify_dashboard_script(request: Request, call_next):
    response = await call_next(request)
    if request.method != "GET" or request.url.path != "/":
        return response
    if response.status_code != 200:
        return response
    if "text/html" not in str(response.headers.get("content-type") or "").lower():
        return response

    body = b""
    async for chunk in response.body_iterator:
        body += chunk
    text = body.decode("utf-8", errors="replace")
    if SCRIPT_TAG not in text:
        if "</body>" in text:
            text = text.replace("</body>", f"{SCRIPT_TAG}</body>", 1)
        else:
            text += SCRIPT_TAG

    headers = dict(response.headers)
    headers.pop("content-length", None)
    headers.pop("content-type", None)
    headers["cache-control"] = "no-store"
    return HTMLResponse(content=text, status_code=response.status_code, headers=headers)


@app.get("/api/v1/sms/notify/health")
def sms_notify_health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "mode": "arrival-notification-only",
        "device_key_configured": bool(_configured_device_key()),
        "message_content_accepted": False,
        "otp_content_accepted": False,
    }


@app.post("/api/v1/sms/notify")
def receive_sms_arrival_notification(
    req: SmsNotifyRequest,
    authenticated_device_id: Optional[str] = Depends(require_sms_device_key),
) -> Dict[str, Any]:
    if authenticated_device_id and authenticated_device_id != req.device_id.strip():
        raise HTTPException(status_code=400, detail="Device ID mismatch")

    signal = SmsArrivalSignal(
        device_id=req.device_id,
        message_id=req.message_id,
        received_at=_safe_received_at(req.received_at),
        source=req.source,
        sender_hint=req.sender_hint,
    )
    accepted, public_event = SMS_NOTIFY_REGISTRY.add(signal)

    if accepted:
        EVENT_BROADCASTER.emit_event(
            build_event(
                "sms_arrived",
                device_id=public_event["device_id"],
                event_id=public_event["event_id"],
                received_at=public_event["received_at"],
                source=public_event["source"],
                sender_hint=public_event["sender_hint"],
                action="Enter the verification code directly in the open browser",
            )
        )
        logger.info(
            "SMS arrival signal accepted from device=%s event=%s",
            public_event["device_id"],
            public_event["event_id"],
        )
    else:
        logger.info(
            "Duplicate SMS arrival signal ignored from device=%s event=%s",
            public_event["device_id"],
            public_event["event_id"],
        )

    return {
        "status": "ok",
        "accepted": accepted,
        "duplicate": not accepted,
        "event": public_event,
        "instruction": "Enter the verification code directly in the browser",
    }


@app.post("/api/v1/sms/notify/heartbeat")
def receive_sms_device_heartbeat(
    req: SmsHeartbeatRequest,
    authenticated_device_id: Optional[str] = Depends(require_sms_device_key),
) -> Dict[str, Any]:
    if authenticated_device_id and authenticated_device_id != req.device_id.strip():
        raise HTTPException(status_code=400, detail="Device ID mismatch")
    row = SMS_NOTIFY_REGISTRY.heartbeat(req.device_id, at=_safe_received_at(req.sent_at))
    return {"status": "ok", "device": row}


@app.get("/api/v1/sms/notify/latest")
def get_latest_sms_notifications(
    since: float = 0.0,
    limit: int = 20,
    _: bool = Depends(require_api_key),
) -> Dict[str, Any]:
    events = SMS_NOTIFY_REGISTRY.latest(since=since, limit=limit)
    return {
        "status": "ok",
        "events": events,
        "server_time": time.time(),
        "message_content_included": False,
        "otp_content_included": False,
    }


@app.get("/api/v1/sms/notify/devices")
def get_sms_notify_devices(_: bool = Depends(require_api_key)) -> Dict[str, Any]:
    return {"status": "ok", "devices": SMS_NOTIFY_REGISTRY.devices(), "server_time": time.time()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server_sms_bridge:app",
        host=os.getenv("MAHANBOT_HOST", "127.0.0.1"),
        port=int(os.getenv("MAHANBOT_PORT", "8000")),
        reload=False,
    )
