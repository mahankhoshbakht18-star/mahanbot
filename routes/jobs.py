"""
routes/jobs.py — Job start, restart, cancel, active list.
"""
import time
from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException

from core.deps import require_api_key, _admin_user, require_reconciled_submission
import core.state as _state
from core.models import JobStartRequest
from core.job_queue import JobQueue
from core.helpers import _serialize_applicants, _normalize_nid, validate_registration_readiness
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
    nid = _normalize_nid(req.nid) or str(req.nid or "").strip()
    if bot_name in {"delete", "delete_req"}:
        raise HTTPException(403, "برای حذف از مسیر تأیید صریح حذف ثبت‌نام استفاده کنید")
    existing = _state.JOB_QUEUE.find_active(bot_name, nid)
    if existing:
        return {"status": "already_running", "job": existing.to_dict()}
    if bot_name in JobQueue.IMMEDIATE_BOTS:
        open_job = _state.JOB_QUEUE.active_job_of(bot_name)
        if open_job:
            raise HTTPException(
                status_code=409,
                detail=f"مرورگر استعلام وضعیت متقاضی {open_job.nid} هنوز باز است؛ اول آن پنجره را ببندید یا «توقف» را بزنید.",
            )

    if bot_name == "register":
        applicant = DBHandler.get_applicant(nid)
        ok, reg_errors, _ = validate_registration_readiness(
            dict(applicant) if applicant else None,
            expected_nid=nid,
            loan_type_override=req.loan_type,
        )
        if not ok:
            raise HTTPException(status_code=422, detail="اطلاعات ثبت‌نام اولیه ناقص یا نامعتبر است: " + "؛ ".join(reg_errors))

    payload: Dict[str, Any] = {}
    if req.loan_type:
        payload["loan_type"] = req.loan_type
    if req.browser_profile_override:
        payload["browser_profile_override"] = normalize_browser_profile(req.browser_profile_override)

    if bot_name in {'select', 'register'}:
        require_reconciled_submission(nid)
    DBHandler.update_runtime(
        nid,
        status="RUNNING",
        current_step="INITIALIZING",
        browser_status="STARTING",
        last_error="",
    )

    job = _state.JOB_QUEUE.enqueue(bot_name, nid, payload)
    if job is None:
        existing = _state.JOB_QUEUE.find_active(bot_name, nid)
        return {"status": "already_running", "job": existing.to_dict() if existing else None}
    _emit_applicants_snapshot()
    return {"status": "queued", "job": job.to_dict()}


@router.post("/jobs/restart")
def restart_job(req: JobStartRequest, _: bool = Depends(require_api_key)):
    bot_name = req.bot_name.lower()
    nid = _normalize_nid(req.nid) or str(req.nid or "").strip()
    if bot_name in {"delete", "delete_req"}:
        raise HTTPException(403, "راه‌اندازی مجدد حذف نیازمند تأیید تازه مدیر است")
    if not _state.JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not ready")
    if bot_name == "register":
        applicant = DBHandler.get_applicant(nid)
        ok, reg_errors, _ = validate_registration_readiness(
            dict(applicant) if applicant else None,
            expected_nid=nid,
            loan_type_override=req.loan_type,
        )
        if not ok:
            raise HTTPException(status_code=422, detail="اطلاعات ثبت‌نام اولیه ناقص یا نامعتبر است: " + "؛ ".join(reg_errors))
    if bot_name in {'select', 'register'}:
        require_reconciled_submission(nid)
    DBHandler.update_runtime(
        nid,
        status="RUNNING",
        current_step="INITIALIZING",
        browser_status="STARTING",
        last_error="",
    )
    existing = _state.JOB_QUEUE.find_active(bot_name, nid)
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
    job = _state.JOB_QUEUE.enqueue(bot_name, nid, payload)
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


class ExplicitDeleteApproval(BaseModel):
    confirmed: Literal[True]


@router.post("/bot/action/delete-request/{nid}")
def delete_request(nid: str, approval: Optional[ExplicitDeleteApproval] = None, admin: Dict[str, Any] = Depends(_admin_user)):
    from database import DBHandler
    applicant = DBHandler.get_applicant(nid)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")
    if not _state.JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not ready")
    job = _state.JOB_QUEUE.enqueue("delete_req", nid, {"delete_approved": True,
        "delete_confirmed_by": str(admin.get("username") or admin.get("id"))})
    if job is None:
        existing = _state.JOB_QUEUE.find_active("delete_req", nid)
        return {"status": "already_running", "job": existing.to_dict() if existing else None}
    from event_logger import log_event
    log_event(nid, None, "Delete request enqueued for CBI TasReqDelete", "warning")
    _emit_applicants_snapshot()
    return {"status": "queued", "job": job.to_dict()}


@router.post("/bot/action/edit-postal/{nid}")
def edit_postal(nid: str, _: Dict[str, Any] = Depends(_admin_user)):
    applicant = DBHandler.get_applicant(nid)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")
    if not _state.JOB_QUEUE:
        raise HTTPException(status_code=500, detail="Job queue not ready")
    job = _state.JOB_QUEUE.enqueue("edit_postal", nid, {})
    if job is None:
        existing = _state.JOB_QUEUE.find_active("edit_postal", nid)
        return {"status": "already_running", "job": existing.to_dict() if existing else None}
    from event_logger import log_event
    log_event(nid, None, "Postal edit enqueued for CBI RegisterInfoEdit", "info")
    _emit_applicants_snapshot()
    return {"status": "queued", "job": job.to_dict()}
