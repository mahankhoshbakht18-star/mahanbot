"""
routes/unified_selector.py — FastAPI Router for Unified Bank Selection View.

Provides API endpoints and serves the unified minimal UI for the marriage loan
bank selection engine, including applicant CRUD and main DB import capabilities.
"""

import logging
import os
from typing import Any, Dict, List, Optional

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


class ApplicantSaveRequest(BaseModel):
    national_id: str
    full_name: str
    state: Optional[str] = ""
    city: Optional[str] = ""
    priority_banks: Optional[List[str]] = []
    bank_mode: Optional[str] = "online"
    allow_reserve: Optional[bool] = True
    auto_final_submit: Optional[bool] = False
    preferred_branch: Optional[str] = ""
    status: Optional[str] = "در انتظار"


class ImportMainRequest(BaseModel):
    national_ids: Optional[List[str]] = None
    replace: Optional[bool] = False


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
    """List all applicants from cbi_test.db."""
    db = get_db()
    db.init_database()
    applicants = db.get_all_applicants()
    return JSONResponse({"success": True, "applicants": applicants})


@router.get("/api/unified/main-db-applicants")
async def list_main_db_applicants():
    """List available real applicants from production cbi_ultimate.db (read-only)."""
    db = get_db()
    try:
        main_apps = db.get_available_main_db_applicants()
        return JSONResponse({"success": True, "applicants": main_apps})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e), "applicants": []})


@router.post("/api/unified/import-from-main")
async def import_from_main_db(req: Optional[ImportMainRequest] = None):
    """Import applicants from cbi_ultimate.db into cbi_test.db."""
    db = get_db()
    nids = req.national_ids if req else None
    replace = req.replace if req else False
    try:
        imported_count = db.import_from_main_db(national_ids=nids, replace=replace)
        return JSONResponse({
            "success": True,
            "message": f"تعداد {imported_count} متقاضی از دیتابیس اصلی به دیتابیس تست منتقل شدند.",
            "imported_count": imported_count,
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/api/unified/applicant/save")
async def save_applicant(req: ApplicantSaveRequest):
    """Create or update an applicant in cbi_test.db."""
    db = get_db()
    try:
        db.upsert_applicant({
            "national_id": req.national_id,
            "full_name": req.full_name,
            "state": req.state or "",
            "city": req.city or "",
            "priority_banks": req.priority_banks or [],
            "bank_mode": req.bank_mode or "online",
            "allow_reserve": req.allow_reserve if req.allow_reserve is not None else True,
            "auto_final_submit": req.auto_final_submit if req.auto_final_submit is not None else False,
            "preferred_branch": req.preferred_branch or "",
            "status": req.status or "در انتظار",
        })
        return JSONResponse({
            "success": True,
            "message": f"اطلاعات متقاضی {req.full_name} ({req.national_id}) ذخیره شد.",
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@router.delete("/api/unified/applicant/{national_id}")
async def delete_applicant(national_id: str):
    """Delete an applicant from cbi_test.db."""
    db = get_db()
    try:
        db.delete_applicant(national_id)
        return JSONResponse({"success": True, "message": f"متقاضی {national_id} حذف شد."})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


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
