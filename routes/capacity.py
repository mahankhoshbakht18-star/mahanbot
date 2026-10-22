"""
routes/capacity.py — Step 3 Continuous Capacity Monitoring & Sniping Endpoints
"""
import logging
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException

from core.deps import require_api_key
import core.state as _state
from core.helpers import _serialize_applicants
from database import DBHandler, normalize_config
from capacity_monitor import CAPACITY_MONITOR_REGISTRY, CapacityMonitor, CapacityState
from live_sessions import LIVE_SESSIONS
from event_logger import EVENT_BROADCASTER, build_event

logger = logging.getLogger("mahanbot.routes.capacity")

router = APIRouter(prefix="/api/capacity", tags=["capacity"])


class IntervalUpdateRequest(BaseModel):
    interval_seconds: float = Field(..., ge=1.0, le=600.0, description="Interval in seconds (1 to 600)")


class ConcurrencyUpdateRequest(BaseModel):
    max_concurrency: int = Field(..., ge=1, le=32, description="Max concurrent workers (1 to 32)")


def _emit_snapshot():
    EVENT_BROADCASTER.emit_event(
        build_event("applicants_snapshot", applicants=_serialize_applicants())
    )


@router.get("/status")
def get_capacity_status(_: bool = Depends(require_api_key)):
    """Return live status of all capacity monitors and configuration."""
    cfg = normalize_config(DBHandler.get_config())
    monitors = CAPACITY_MONITOR_REGISTRY.list_all()
    active_count = CAPACITY_MONITOR_REGISTRY.count_active()
    waiting_count = CAPACITY_MONITOR_REGISTRY.count_waiting_capacity()

    max_conc = _state.JOB_QUEUE.max_concurrency if _state.JOB_QUEUE else 4

    return {
        "status": "ok",
        "monitors": monitors,
        "summary": {
            "total_monitors": len(monitors),
            "active_count": active_count,
            "waiting_capacity_count": waiting_count,
            "max_concurrency": max_conc,
        },
        "config": {
            "capacity_check_enabled": cfg.get("capacity_check_enabled", True),
            "bank_capacity_check_interval": cfg.get("bank_capacity_check_interval", 5.0),
            "sniper_mode_enabled": cfg.get("sniper_mode_enabled", True),
            "sniper_interval_ms": cfg.get("sniper_interval_ms", 5000),
            "auto_final_submit": cfg.get("auto_final_submit", False),
            "max_concurrency": max_conc,
        }
    }


@router.post("/start-all")
def start_all_capacity_monitors(_: bool = Depends(require_api_key)):
    """Start bank selection / capacity monitoring for all eligible applicants."""
    if not _state.JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not ready")

    applicants = DBHandler.get_all_applicants()
    started = []
    skipped = []

    for app in applicants:
        nid = str(app.get("national_id", ""))
        status = str(app.get("status", "")).lower()

        # Check if already completed or has confirmed code
        if DBHandler.has_final_submission(nid) or status in {"completed", "registered", "success"}:
            skipped.append({"nid": nid, "reason": "already_completed"})
            continue

        # Check if existing job is running
        existing = _state.JOB_QUEUE.find_active("select", nid)
        if existing:
            skipped.append({"nid": nid, "reason": "already_running"})
            continue

        # Enqueue select job
        job = _state.JOB_QUEUE.enqueue("select", nid, {})
        if job:
            started.append({"nid": nid, "job_id": job.id})
        else:
            skipped.append({"nid": nid, "reason": "queue_rejected"})

    _emit_snapshot()
    return {
        "status": "ok",
        "started_count": len(started),
        "skipped_count": len(skipped),
        "started": started,
        "skipped": skipped,
    }


@router.post("/stop-all")
def stop_all_capacity_monitors(_: bool = Depends(require_api_key)):
    """Stop all active bank selection / capacity monitoring jobs."""
    if not _state.JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not ready")

    cancelled_ids = _state.JOB_QUEUE.stop_all("select")
    _emit_snapshot()
    return {
        "status": "ok",
        "cancelled_count": len(cancelled_ids),
        "cancelled_ids": cancelled_ids,
    }


@router.post("/interval")
def set_capacity_interval(req: IntervalUpdateRequest, _: bool = Depends(require_api_key)):
    """Update capacity check interval dynamically."""
    interval = float(req.interval_seconds)
    DBHandler.update_config({
        "bank_capacity_check_interval": interval,
        "sniper_interval_ms": int(interval * 1000),
    })

    # Apply to all live monitors in memory
    with CAPACITY_MONITOR_REGISTRY._lock:
        for monitor in CAPACITY_MONITOR_REGISTRY._monitors.values():
            monitor.base_interval = interval
            monitor.current_interval = interval
            monitor.sync_runtime()

    _emit_snapshot()
    return {
        "status": "ok",
        "bank_capacity_check_interval": interval,
        "sniper_interval_ms": int(interval * 1000),
    }


@router.post("/concurrency")
def set_concurrency(req: ConcurrencyUpdateRequest, _: bool = Depends(require_api_key)):
    """Update worker concurrency dynamically."""
    if not _state.JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not ready")

    new_limit = _state.JOB_QUEUE.set_max_concurrency(req.max_concurrency)
    DBHandler.update_config({"max_concurrency": new_limit})
    return {"status": "ok", "max_concurrency": new_limit}


@router.post("/pause/{nid}")
def pause_applicant_monitoring(nid: str, _: bool = Depends(require_api_key)):
    """Pause capacity monitoring for a specific applicant."""
    owner = LIVE_SESSIONS.get(nid)
    if not owner or not owner.alive:
        raise HTTPException(status_code=404, detail="No active browser session for applicant")

    LIVE_SESSIONS.enqueue_command(nid, "pause")
    return {"status": "ok", "message": f"Applicant {nid} paused"}


@router.post("/resume/{nid}")
def resume_applicant_monitoring(nid: str, _: bool = Depends(require_api_key)):
    """Resume capacity monitoring for a specific applicant."""
    owner = LIVE_SESSIONS.get(nid)
    if not owner or not owner.alive:
        raise HTTPException(status_code=404, detail="No active browser session for applicant")

    LIVE_SESSIONS.enqueue_command(nid, "resume")
    return {"status": "ok", "message": f"Applicant {nid} resumed"}
