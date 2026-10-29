"""
routes/radar.py — MAHANBOT 2.0 Smart Bank Capacity Radar & Telegram Notification Endpoints
"""
import logging
import time
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.deps import require_api_key
from database import DBHandler
from core.capacity_radar import CAPACITY_RADAR, is_near_18_batch, CapacityStatus
from core.telegram_notifier import TELEGRAM_NOTIFIER
from core.capacity_analytics import CAPACITY_ANALYTICS

logger = logging.getLogger("mahanbot.routes.radar")
router = APIRouter(prefix="/api/radar", tags=["radar"])


class TelegramConfigRequest(BaseModel):
    bot_token: Optional[str] = Field(None, description="Telegram Bot Token from @BotFather")
    admin_chat_id: Optional[str] = Field(None, description="Admin Chat ID or Channel ID")
    channel_id: Optional[str] = Field(None, description="Optional public/private broadcast channel ID")
    notifications_enabled: Optional[bool] = Field(None, description="Enable or disable automated alerts")


class TelegramTestRequest(BaseModel):
    chat_id: Optional[str] = Field(None, description="Target chat ID (defaults to admin_chat_id)")
    message: Optional[str] = Field(None, description="Optional custom test message")


class TelegramSubscriberRequest(BaseModel):
    chat_id: str = Field(..., description="Telegram Chat ID")
    first_name: Optional[str] = Field(None)
    username: Optional[str] = Field(None)
    role: str = Field("subscriber", description="admin or subscriber")
    is_active: bool = Field(True)
    notify_available: bool = Field(True)
    notify_reserve: bool = Field(True)
    filter_provinces: Optional[List[str]] = Field(None)
    filter_banks: Optional[List[str]] = Field(None)


class RadarManualObservationRequest(BaseModel):
    province: str
    city: str
    bank_name: str
    status: str = Field("AVAILABLE_OBSERVED", description="AVAILABLE_OBSERVED, RESERVE, FULL")
    loan_type: str = "ازدواج"
    visit_method: str = "in_person"
    source: str = "manual_api"
    source_applicant_nid: Optional[str] = None


@router.get("/summary")
def get_radar_summary(
    province: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    _: bool = Depends(require_api_key),
):
    """Return live radar summary and current batch 18:00 window status."""
    states = CAPACITY_RADAR.get_all_states(province_name=province, status_filter=status)
    near_18 = is_near_18_batch()

    active_count = sum(1 for s in states if s.get("status") in ("AVAILABLE_OBSERVED", "AVAILABLE_CONFIRMED"))
    reserve_count = sum(1 for s in states if s.get("status") == "RESERVE")
    full_count = sum(1 for s in states if s.get("status") == "FULL")

    cfg = DBHandler.get_config()
    bot_token = cfg.get("telegram_bot_token", "")
    admin_chat_id = cfg.get("telegram_admin_chat_id", "")
    notifications_enabled = cfg.get("telegram_notifications_enabled", True)

    return {
        "status": "ok",
        "near_18_batch": near_18,
        "summary": {
            "total_monitored": len(states),
            "available_count": active_count,
            "reserve_count": reserve_count,
            "full_count": full_count,
            "timestamp": int(time.time()),
        },
        "telegram_status": {
            "configured": bool(bot_token and admin_chat_id),
            "notifications_enabled": bool(notifications_enabled),
            "has_token": bool(bot_token),
            "admin_chat_id": str(admin_chat_id) if admin_chat_id else None,
        },
        "states": states,
    }


@router.get("/events")
def get_recent_radar_events(
    limit: int = Query(50, ge=1, le=200),
    province: Optional[str] = Query(None),
    bank_name: Optional[str] = Query(None),
    _: bool = Depends(require_api_key),
):
    """Return historical capacity state changes."""
    events = DBHandler.get_recent_radar_events(limit=limit, province_name=province, bank_name=bank_name)
    return {
        "status": "ok",
        "count": len(events),
        "events": events,
    }


@router.get("/analytics")
def get_radar_analytics(_: bool = Depends(require_api_key)):
    """Return full historical analytics report on capacity patterns and the 18:00 window."""
    report = CAPACITY_ANALYTICS.get_full_analytics_report()
    return {
        "status": "ok",
        "analytics": report,
    }


@router.post("/observe")
def record_manual_observation(
    req: RadarManualObservationRequest,
    _: bool = Depends(require_api_key),
):
    """Ingest observation from manual submission or external scraper."""
    valid_status_names = [e.name for e in CapacityStatus]
    status_upper = req.status.upper()
    if status_upper not in valid_status_names:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of {valid_status_names}")

    parsed_status = CapacityStatus[status_upper]
    has_changed, event_id, _ = CAPACITY_RADAR.ingest_observation(
        province_name=req.province,
        city_name=req.city,
        bank_name=req.bank_name,
        status=parsed_status,
        loan_type=req.loan_type,
        visit_method=req.visit_method,
        data_source=req.source,
    )

    return {
        "status": "ok",
        "has_changed": has_changed,
        "event_id": event_id,
        "message": "Observation processed",
    }


# ── Telegram Endpoints ───────────────────────────────────────────────────

@router.get("/telegram/config")
def get_telegram_config(_: bool = Depends(require_api_key)):
    """Get telegram notifier configuration."""
    cfg = DBHandler.get_config()
    token = cfg.get("telegram_bot_token", "")
    masked_token = f"{token[:6]}...{token[-4:]}" if token and len(token) > 10 else ("Configured" if token else "")

    return {
        "status": "ok",
        "telegram_bot_token_masked": masked_token,
        "telegram_admin_chat_id": cfg.get("telegram_admin_chat_id", ""),
        "telegram_channel_id": cfg.get("telegram_channel_id", ""),
        "telegram_notifications_enabled": cfg.get("telegram_notifications_enabled", True),
    }


@router.post("/telegram/config")
def update_telegram_config(
    req: TelegramConfigRequest,
    _: bool = Depends(require_api_key),
):
    """Update telegram notifier settings."""
    updates = {}
    if req.bot_token is not None:
        updates["telegram_bot_token"] = req.bot_token.strip()
    if req.admin_chat_id is not None:
        updates["telegram_admin_chat_id"] = req.admin_chat_id.strip()
    if req.channel_id is not None:
        updates["telegram_channel_id"] = req.channel_id.strip()
    if req.notifications_enabled is not None:
        updates["telegram_notifications_enabled"] = bool(req.notifications_enabled)

    if updates:
        DBHandler.update_config(updates)

    return {"status": "ok", "updated": list(updates.keys())}


@router.post("/telegram/test")
def send_telegram_test(
    req: TelegramTestRequest,
    _: bool = Depends(require_api_key),
):
    """Send a test message through Telegram."""
    success, detail = TELEGRAM_NOTIFIER.send_test_message(
        chat_id=req.chat_id,
        custom_message=req.message,
    )
    if not success:
        raise HTTPException(status_code=400, detail=detail)

    return {"status": "ok", "message": detail}


@router.get("/telegram/subscribers")
def list_telegram_subscribers(_: bool = Depends(require_api_key)):
    """List all registered subscribers."""
    subscribers = DBHandler.get_telegram_subscribers()
    return {"status": "ok", "subscribers": subscribers}


@router.post("/telegram/subscribers")
def save_telegram_subscriber(
    req: TelegramSubscriberRequest,
    _: bool = Depends(require_api_key),
):
    """Add or update telegram subscriber preferences."""
    import json
    data = {
        "chat_id": req.chat_id,
        "first_name": req.first_name,
        "username": req.username,
        "role": req.role,
        "is_active": 1 if req.is_active else 0,
        "notify_available": 1 if req.notify_available else 0,
        "notify_reserve": 1 if req.notify_reserve else 0,
        "filter_provinces": json.dumps(req.filter_provinces or [], ensure_ascii=False),
        "filter_banks": json.dumps(req.filter_banks or [], ensure_ascii=False),
    }
    DBHandler.save_telegram_subscriber(data)
    return {"status": "ok", "message": "Subscriber saved"}


@router.post("/telegram/process-queue")
def process_telegram_queue(_: bool = Depends(require_api_key)):
    """Manually trigger sending pending messages in queue."""
    processed = TELEGRAM_NOTIFIER.process_queue_batch(limit=20)
    return {"status": "ok", "processed": processed}
