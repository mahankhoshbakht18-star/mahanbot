"""
routes/jobs.py — Job start, restart, cancel, active list.
"""
import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from core.deps import require_api_key
import core.state as _state
from core.models import JobStartRequest
from core.helpers import _serialize_applicants
from database import DBHandler

from browser_launcher import normalize_browser_profile
from event_logger import EVENT_BROADCASTER, build_event

router = APIRouter()


def _emit_applicants_snapshot() -> None:
    EVENT_BROADCASTER.emit_event(
        build_event("applicants_snapshot", applicants=_serialize_applicants())
    )


@router.post("/jobs/start")
def start_job(req: JobStartRequest, _: bool = Depends(require_api_key)):
    if not _state.JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not ready")

    bot_name = req.bot_name.lower()
    existing = _state.JOB_QUEUE.find_active(bot_name, req.nid)
    if existing:
        return {"status": "already_running", "job": existing.to_dict()}

    payload: Dict[str, Any] = {}
    if req.loan_type:
        payload["loan_type"] = req.loan_type
    if req.browser_profile_override:
        payload["browser_profile_override"] = normalize_browser_profile(req.browser_profile_override)

    cp = DBHandler.get_checkpoint(req.nid)
    if cp.get("final_submit_status") == "pending" and (
        cp.get("bank_state") == "CAPACITY_FULL"
        or cp.get("last_operation_result") in ("CAPACITY_FULL", "BANK_CAPACITY_FULL")
        or "تکمیل" in str(cp.get("last_error_reason", ""))
        or cp.get("browser_closed_by_user")
    ):
        DBHandler.release_final_submission_claim(req.nid)

    job = _state.JOB_QUEUE.enqueue(bot_name, req.nid, payload)
    if job is None:
        existing = _state.JOB_QUEUE.find_active(bot_name, req.nid)
        return {"status": "already_running", "job": existing.to_dict() if existing else None}
    _emit_applicants_snapshot()
    return {"status": "queued", "job": job.to_dict()}


@router.post("/jobs/restart")
def restart_job(req: JobStartRequest, _: bool = Depends(require_api_key)):
    if not _state.JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not ready")
    cp = DBHandler.get_checkpoint(req.nid)
    final_status = cp.get("final_submit_status")
    if final_status == "pending" and (
        cp.get("bank_state") == "CAPACITY_FULL"
        or cp.get("last_operation_result") in ("CAPACITY_FULL", "BANK_CAPACITY_FULL")
        or "تکمیل" in str(cp.get("last_error_reason", ""))
        or cp.get("browser_closed_by_user")
    ):
        DBHandler.release_final_submission_claim(req.nid)
        final_status = None
    if final_status:
        raise HTTPException(status_code=409, detail="Previous submission requires reconciliation before restart")
    existing = _state.JOB_QUEUE.find_active(req.bot_name.lower(), req.nid)
    if existing:
        _state.JOB_QUEUE.cancel(existing.id)
        deadline = time.monotonic() + 10.0
        while existing.status in _state.JOB_QUEUE.ACTIVE_STATUSES and time.monotonic() < deadline:
            time.sleep(0.05)
        if existing.status in _state.JOB_QUEUE.ACTIVE_STATUSES:
            raise HTTPException(status_code=409, detail="Previous job is still stopping")
    payload: Dict[str, Any] = {"force": True}
    if req.loan_type:
        payload["loan_type"] = req.loan_type
    job = _state.JOB_QUEUE.enqueue(req.bot_name.lower(), req.nid, payload)
    if job is None:
        raise HTTPException(status_code=409, detail="Another job became active for this applicant")
    _emit_applicants_snapshot()
    return {"status": "queued", "job": job.to_dict()}


@router.post("/jobs/cancel/{job_id}")
def cancel_job(job_id: str, _: bool = Depends(require_api_key)):
    if not _state.JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not ready")
    job = _state.JOB_QUEUE.cancel(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _emit_applicants_snapshot()
    return {"status": job.status, "job": job.to_dict()}


@router.get("/jobs/active")
def get_active_jobs(_: bool = Depends(require_api_key)):
    if not _state.JOB_QUEUE:
        return []
    return [j for j in _state.JOB_QUEUE.list_jobs() if j["status"] in _state.JOB_QUEUE.ACTIVE_STATUSES]


@router.post("/bot/stop/{nid}")
def stop_bot_by_nid(nid: str, _: bool = Depends(require_api_key)):
    if not _state.JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not ready")
    job = _state.JOB_QUEUE.cancel_by_nid(nid)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _emit_applicants_snapshot()
    return {"status": job.status, "job": job.to_dict()}


@router.post("/bot/action/delete-request/{nid}")
def delete_request(nid: str, _: bool = Depends(require_api_key)):
    from database import DBHandler
    applicant = DBHandler.get_applicant(nid)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")
    from event_logger import log_event
    log_event(nid, None, "Delete request submitted", "warning")
    _emit_applicants_snapshot()
    return {"status": "ok"}
