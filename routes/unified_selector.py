"""
routes/unified_selector.py — FastAPI Router for Unified Bank Selection View.

Provides API endpoints and serves the unified minimal UI for the marriage loan
bank selection engine.
"""

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from core.test_database_manager import TestDatabaseManager
from core.unified_bank_engine import UnifiedBankEngine

logger = logging.getLogger("mahanbot.routes.unified")

router = APIRouter(prefix="", tags=["Unified Selector"])

# Singleton engine instance for the web server
_ENGINE_INSTANCE: Optional[UnifiedBankEngine] = None
_DB_MANAGER: Optional[TestDatabaseManager] = None


def get_engine() -> UnifiedBankEngine:
    global _ENGINE_INSTANCE
    if _ENGINE_INSTANCE is None:
        _ENGINE_INSTANCE = UnifiedBankEngine()
    return _ENGINE_INSTANCE


def get_db() -> TestDatabaseManager:
    global _DB_MANAGER
    if _DB_MANAGER is None:
        _DB_MANAGER = TestDatabaseManager()
    return _DB_MANAGER


class StartRequest(BaseModel):
    national_id: Optional[str] = None


class ToggleSubmitRequest(BaseModel):
    national_id: str
    auto_final_submit: bool


@router.get("/unified-selector")
async def get_unified_selector_page():
    """Serve the minimal unified bank selector HTML page."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html_path = os.path.join(base_dir, "static", "unified_selector.html")
    if not os.path.isfile(html_path):
        raise HTTPException(status_code=404, detail="unified_selector.html not found")
    return FileResponse(html_path, media_type="text/html; charset=utf-8")


@router.get("/api/unified/applicants")
async def list_unified_applicants():
    """List all test applicants from cbi_test.db."""
    db = get_db()
    db.init_database()
    applicants = db.get_applicants()
    return JSONResponse({"success": True, "applicants": applicants})


@router.get("/api/unified/status")
async def get_unified_status():
    """Get the current running status, state, and recent logs of the engine."""
    engine = get_engine()
    return JSONResponse({"success": True, "status": engine.get_status()})


@router.post("/api/unified/start")
async def start_unified_engine(req: Optional[StartRequest] = None):
    """Start the bank selection cycle."""
    engine = get_engine()
    nid = req.national_id if req else None
    engine.start(applicant_nid=nid)
    return JSONResponse({"success": True, "message": "موتور با موفقیت شروع به کار کرد."})


@router.post("/api/unified/pause")
async def pause_unified_engine():
    """Pause the bank selection cycle."""
    engine = get_engine()
    engine.pause()
    return JSONResponse({"success": True, "message": "عملیات متوقف شد."})


@router.post("/api/unified/resume")
async def resume_unified_engine():
    """Resume the bank selection cycle."""
    engine = get_engine()
    engine.resume()
    return JSONResponse({"success": True, "message": "عملیات ادامه یافت."})


@router.post("/api/unified/stop")
async def stop_unified_engine():
    """Stop the bank selection cycle and close browser."""
    engine = get_engine()
    engine.stop()
    return JSONResponse({"success": True, "message": "موتور متوقف شد."})


@router.post("/api/unified/confirm-submit")
async def confirm_manual_submission():
    """Trigger manual final submission when engine is in READY_FOR_MANUAL_CONFIRMATION."""
    engine = get_engine()
    engine.trigger_manual_confirmation()
    return JSONResponse({"success": True, "message": "تأیید نهایی ارسال شد."})


@router.post("/api/unified/toggle-submit")
async def toggle_final_submit(req: ToggleSubmitRequest):
    """Update auto_final_submit toggle for an applicant in the test DB."""
    db = get_db()
    db.set_final_submit_toggle(req.national_id, req.auto_final_submit)
    return JSONResponse({
        "success": True,
        "message": f"تنظیم ثبت خودکار برای متقاضی {req.national_id} به‌روزرسانی شد.",
        "auto_final_submit": req.auto_final_submit,
    })


@router.post("/api/unified/reset-applicants")
async def reset_applicants():
    """Reset applicant statuses in the test DB for a fresh run."""
    db = get_db()
    db.reset_applicant_statuses()
    return JSONResponse({"success": True, "message": "وضعیت متقاضیان ریست شد."})
