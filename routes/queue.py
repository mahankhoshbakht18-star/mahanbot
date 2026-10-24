# routes/queue.py
"""
routes/queue.py — API endpoints for Applicant Queue & OTP Queue Management
"""
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query

from core.deps import require_api_key
from core.queue_manager import QueueManager
from core.otp_queue import OTPQueueManager
from database import DBHandler
import core.state as _state

router = APIRouter()


@router.get("/api/queue")
def get_applicant_queue(
    status: Optional[str] = Query(None, description="Filter by queue_status"),
    _: bool = Depends(require_api_key),
):
    """Retrieve ordered applicant queue list."""
    items = QueueManager.get_queue(status_filter=status)
    stats = QueueManager.get_stats()
    return {
        "status": "ok",
        "total": len(items),
        "stats": stats,
        "items": items,
    }


@router.get("/api/queue/stats")
def get_queue_stats(_: bool = Depends(require_api_key)):
    """Retrieve queue summary statistics for dashboard cards."""
    return {"status": "ok", "stats": QueueManager.get_stats()}


@router.post("/api/queue/{nid}/start")
def start_queued_applicant(nid: str, _: bool = Depends(require_api_key)):
    """Start bot execution for a specific applicant in the queue."""
    if not _state.JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not initialized")
    clean_nid = str(nid).strip()
    app = DBHandler.get_applicant(clean_nid)
    if not app:
        raise HTTPException(status_code=404, detail="متقاضی یافت نشد.")

    # Reset any past final_submit claims or locks so it runs freshly
    DBHandler.release_final_submission_claim(clean_nid)
    DBHandler.update_runtime(
        clean_nid,
        status="RUNNING",
        current_step="INITIALIZING",
        fsm_state="STARTING",
        browser_status="STARTING",
        last_error="",
    )

    job = _state.JOB_QUEUE.enqueue("select", clean_nid, {})
    QueueManager.resume_applicant(clean_nid)
    QueueManager.emit_snapshot()
    return {"status": "started", "nid": clean_nid, "job_id": job.id if job else None}


@router.post("/api/queue/{nid}/pause")
def pause_queued_applicant(nid: str, _: bool = Depends(require_api_key)):
    """Pause execution for a specific applicant."""
    clean_nid = str(nid).strip()
    if _state.JOB_QUEUE:
        _state.JOB_QUEUE.cancel_by_nid(clean_nid)
    success = QueueManager.pause_applicant(clean_nid)
    if not success:
        raise HTTPException(status_code=404, detail="متقاضی یافت نشد.")
    return {"status": "paused", "nid": clean_nid}


@router.post("/api/queue/{nid}/resume")
def resume_queued_applicant(nid: str, _: bool = Depends(require_api_key)):
    """Resume execution for a paused applicant."""
    clean_nid = str(nid).strip()
    success = QueueManager.resume_applicant(clean_nid)
    if not success:
        raise HTTPException(status_code=404, detail="متقاضی یافت نشد.")
    return {"status": "resumed", "nid": clean_nid}


@router.post("/api/queue/{nid}/priority")
def set_applicant_priority(nid: str, payload: Dict[str, Any], _: bool = Depends(require_api_key)):
    """Set applicant priority: HIGH, NORMAL, LOW."""
    clean_nid = str(nid).strip()
    priority = str(payload.get("priority") or "NORMAL").upper()
    success = QueueManager.set_priority(clean_nid, priority)
    if not success:
        raise HTTPException(status_code=404, detail="متقاضی یافت نشد.")
    return {"status": "ok", "nid": clean_nid, "priority": priority}


@router.post("/api/queue/{queue_id}/reorder")
def reorder_queue_item(queue_id: str, payload: Dict[str, Any], _: bool = Depends(require_api_key)):
    """Move applicant to a specific position in the queue."""
    target_pos = payload.get("position")
    if target_pos is None:
        raise HTTPException(status_code=400, detail="Position is required")
    success = QueueManager.reorder(queue_id, int(target_pos))
    if not success:
        raise HTTPException(status_code=404, detail="آیتم صف یافت نشد.")
    return {"status": "ok", "queue_id": queue_id, "new_position": target_pos}


@router.post("/api/queue/{nid}/remove")
def remove_from_queue(nid: str, _: bool = Depends(require_api_key)):
    """Cancel and remove applicant from active processing queue without deleting profile."""
    clean_nid = str(nid).strip()
    if _state.JOB_QUEUE:
        _state.JOB_QUEUE.cancel_by_nid(clean_nid)
    success = QueueManager.remove_from_queue(clean_nid)
    if not success:
        raise HTTPException(status_code=404, detail="متقاضی یافت نشد.")
    return {"status": "removed", "nid": clean_nid}


@router.post("/api/queue/start-all")
def start_all_queue(_: bool = Depends(require_api_key)):
    """Start processing eligible waiting applicants in the queue."""
    if not _state.JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not initialized")
    queue_items = QueueManager.get_queue()
    started = []
    for item in queue_items:
        if item["queue_status"] in {"WAITING", "WAITING_BANK"} and not _state.JOB_QUEUE.find_active("select", item["applicant_id"]):
            nid = item["applicant_id"]
            DBHandler.release_final_submission_claim(nid)
            job = _state.JOB_QUEUE.enqueue("select", nid, {})
            if job:
                started.append(nid)
    QueueManager.emit_snapshot()
    return {"status": "ok", "started_count": len(started), "started": started}


@router.post("/api/queue/pause-all")
def pause_all_queue(_: bool = Depends(require_api_key)):
    """Gracefully stop all running workers and pause queue."""
    if _state.JOB_QUEUE:
        _state.JOB_QUEUE.stop_all()
    queue_items = QueueManager.get_queue()
    for item in queue_items:
        if item["queue_status"] in {"RUNNING", "WAITING", "WAITING_BANK"}:
            QueueManager.pause_applicant(item["applicant_id"])
    QueueManager.emit_snapshot()
    return {"status": "ok", "message": "تمام پردازش‌ها با موفقیت متوقف شدند."}


@router.get("/api/otp-queue")
def list_otp_queue(
    limit: int = Query(50, ge=1, le=200),
    _: bool = Depends(require_api_key),
):
    """List recent OTP requests with delivery & verification status."""
    return {"status": "ok", "requests": OTPQueueManager.list_requests(limit=limit)}
