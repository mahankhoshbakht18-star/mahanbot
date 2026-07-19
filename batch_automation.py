from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from database import DBHandler
from event_logger import EVENT_BROADCASTER, build_event


SUPPORTED_BOTS = {"register", "select", "status"}
ACTIVE_JOB_STATUSES = {"queued", "running", "cancelling"}


def _normalize_nid(value: object) -> str:
    text = str(value or "").strip()
    translation = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return text.translate(translation).replace("-", "").replace(" ", "")


class BatchStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_name: Literal["register", "select", "status"]
    nids: List[str] = Field(default_factory=list, max_length=500)
    loan_type: Optional[str] = Field(default=None, max_length=64)


class BatchController:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_batch: Dict[str, Any] = {
            "id": None,
            "bot_name": None,
            "created_at": None,
            "queued": [],
            "skipped": [],
            "not_found": [],
        }

    def remember(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._last_batch = dict(payload)
            return dict(self._last_batch)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._last_batch)


BATCH_CONTROLLER = BatchController()


def _applicant_nids() -> List[str]:
    results: List[str] = []
    for applicant in DBHandler.get_all_applicants():
        nid = _normalize_nid(applicant.get("national_id"))
        if nid and nid not in results:
            results.append(nid)
    return results


def _job_summary(job_queue: Any) -> Dict[str, Any]:
    jobs = job_queue.list_jobs() if job_queue else []
    counts: Dict[str, int] = {}
    for job in jobs:
        status = str(job.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "counts": counts,
        "active": [job for job in jobs if job.get("status") in ACTIVE_JOB_STATUSES],
        "recent": sorted(jobs, key=lambda item: float(item.get("created_at") or 0), reverse=True)[:50],
    }


def install_batch_automation(app: FastAPI, auth_dependency: Callable[..., Any]) -> None:
    if getattr(app.state, "batch_automation_installed", False):
        return
    app.state.batch_automation_installed = True

    @app.get("/api/v1/batch/status")
    def batch_status(_: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        import server

        return {
            "status": "ok",
            "queue": _job_summary(server.JOB_QUEUE),
            "last_batch": BATCH_CONTROLLER.snapshot(),
            "applicants": len(_applicant_nids()),
            "supported_bots": sorted(SUPPORTED_BOTS),
            "human_checkpoints": ["captcha", "otp", "final_submit"],
        }

    @app.post("/api/v1/batch/start")
    def batch_start(req: BatchStartRequest, _: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        import server

        if server.JOB_QUEUE is None:
            raise HTTPException(status_code=503, detail="Job queue is not ready")

        known_nids = set(_applicant_nids())
        requested = [_normalize_nid(value) for value in req.nids]
        targets = []
        for nid in requested or sorted(known_nids):
            if nid and nid not in targets:
                targets.append(nid)

        if not targets:
            raise HTTPException(status_code=400, detail="No applicants are available")

        queued: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        not_found: List[str] = []
        payload: Dict[str, Any] = {}
        if req.loan_type:
            payload["loan_type"] = req.loan_type

        for nid in targets:
            if nid not in known_nids:
                not_found.append(nid)
                continue
            try:
                job = server.JOB_QUEUE.enqueue(req.bot_name, nid, dict(payload))
                queued.append(job.to_dict())
            except server.DuplicateJobError as exc:
                existing = exc.job
                skipped.append({
                    "nid": nid,
                    "reason": "already_active",
                    "job": existing.to_dict() if existing else None,
                })

        batch_id = f"batch-{int(time.time() * 1000)}"
        result = BATCH_CONTROLLER.remember({
            "id": batch_id,
            "bot_name": req.bot_name,
            "created_at": time.time(),
            "queued": queued,
            "skipped": skipped,
            "not_found": not_found,
        })
        EVENT_BROADCASTER.emit_event(
            build_event(
                "batch_queued",
                batch_id=batch_id,
                bot_name=req.bot_name,
                queued_count=len(queued),
                skipped_count=len(skipped),
                not_found_count=len(not_found),
            )
        )
        return {"status": "ok", "batch": result, "queue": _job_summary(server.JOB_QUEUE)}

    @app.post("/api/v1/batch/cancel-active")
    def batch_cancel_active(_: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        import server

        if server.JOB_QUEUE is None:
            raise HTTPException(status_code=503, detail="Job queue is not ready")

        cancelled: List[Dict[str, Any]] = []
        for job_data in server.JOB_QUEUE.list_jobs():
            if job_data.get("status") not in ACTIVE_JOB_STATUSES:
                continue
            job = server.JOB_QUEUE.cancel(str(job_data.get("id")))
            if job:
                cancelled.append(job.to_dict())

        EVENT_BROADCASTER.emit_event(
            build_event("batch_cancelled", cancelled_count=len(cancelled))
        )
        return {"status": "ok", "cancelled": cancelled, "queue": _job_summary(server.JOB_QUEUE)}
