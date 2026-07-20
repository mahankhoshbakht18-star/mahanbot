from __future__ import annotations

import os
import secrets
import time
from typing import Any, Dict, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from event_logger import EVENT_BROADCASTER, build_event
from sms_notify_core import SMS_NOTIFY_REGISTRY, SmsArrivalSignal


app = FastAPI(
    title="MahanBot SMS Arrival Ingress",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


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
            detail="SMS device key is not configured",
        )
    supplied = str(x_device_key or "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid device key")
    normalized_device_id = str(x_device_id or "").strip()
    if len(normalized_device_id) > 128:
        raise HTTPException(status_code=400, detail="Invalid device ID")
    return normalized_device_id or None


def _safe_timestamp(value: Optional[float]) -> float:
    now = time.time()
    if value is None:
        return now
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return now
    if parsed <= 0 or abs(parsed - now) > 24 * 60 * 60:
        return now
    return parsed


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "mode": "arrival-notification-only",
        "device_key_configured": bool(_configured_device_key()),
        "message_content_accepted": False,
        "otp_content_accepted": False,
    }


@app.post("/api/v1/sms/notify")
def receive_notification(
    req: SmsNotifyRequest,
    authenticated_device_id: Optional[str] = Depends(require_sms_device_key),
) -> Dict[str, Any]:
    if authenticated_device_id and authenticated_device_id != req.device_id.strip():
        raise HTTPException(status_code=400, detail="Device ID mismatch")

    signal = SmsArrivalSignal(
        device_id=req.device_id,
        message_id=req.message_id,
        received_at=_safe_timestamp(req.received_at),
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

    return {
        "status": "ok",
        "accepted": accepted,
        "duplicate": not accepted,
        "event": public_event,
        "instruction": "Enter the verification code directly in the browser",
    }


@app.post("/api/v1/sms/notify/heartbeat")
def heartbeat(
    req: SmsHeartbeatRequest,
    authenticated_device_id: Optional[str] = Depends(require_sms_device_key),
) -> Dict[str, Any]:
    if authenticated_device_id and authenticated_device_id != req.device_id.strip():
        raise HTTPException(status_code=400, detail="Device ID mismatch")
    device = SMS_NOTIFY_REGISTRY.heartbeat(req.device_id, at=_safe_timestamp(req.sent_at))
    return {"status": "ok", "device": device}
