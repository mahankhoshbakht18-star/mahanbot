"""
routes/page3_management.py — Official Page 3 Lifecycle & Diagnostics API Routes.

Exposes endpoints for:
- Live session diagnostics (Page 3 health status).
- Official controlled refresh and re-entry.
- Read-only deep analysis of saved Page 3 HTML snapshots (SavedPage3Analyzer).
- Province bank differential comparisons (ProvinceBankMonitor).
- 18:00 (6 PM) maintenance hypothesis reports.
"""

import os
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from core.deps import require_api_key
from core.page3_refresh_controller import Page3OfficialRefreshController
from core.page3_state_machine import ApplicantPage3StateMachine, Page3State
from core.province_bank_monitor import ProvinceBankMonitor
from core.saved_page_analyzer import SavedPage3Analyzer
from core.session_diagnostics import Page3SessionDiagnostics
from core.page3_maintenance_hypothesis import MaintenanceHypothesisTester
from database import DBHandler

router = APIRouter(prefix="/api/page3", tags=["page3_management"])

# Shared singleton for maintenance hypothesis tracking (disabled by default)
MAINTENANCE_TESTER = MaintenanceHypothesisTester(enabled=False)


@router.get("/diagnostics/{nid}")
async def get_page3_diagnostics(nid: str, user=Depends(require_api_key)):
    """Evaluate live session health for an applicant using multi-signal analysis."""
    nid = str(nid).strip()
    runtime = DBHandler.get_runtime(nid) or {}
    url = runtime.get("current_url") or ""
    dom_status = runtime.get("browser_status") or "CLOSED"
    otp_time = runtime.get("otp_received_at") or 0.0

    eval_result = Page3SessionDiagnostics.evaluate_session(
        current_url=url,
        dom_html="",
        last_http_status=200 if dom_status == "OPEN" else None,
        otp_entry_time=float(otp_time),
    )
    return JSONResponse(eval_result)


@router.post("/refresh-official/{nid}")
async def trigger_official_refresh(nid: str, user=Depends(require_api_key)):
    """
    Request an official, controlled Page 3 reload and re-entry.
    Respects official workflow, destination redetection, and stops endless cycles.
    """
    nid = str(nid).strip()
    applicant = DBHandler.get_applicant(nid)
    if not applicant:
        raise HTTPException(status_code=404, detail="متقاضی یافت نشد.")

    from live_sessions import LIVE_SESSIONS
    session_obj = LIVE_SESSIONS.get(nid)
    page = getattr(session_obj, "page", None) if session_obj else None

    # Fallback to bot instance if present in runtime
    ctrl = Page3OfficialRefreshController(bot=applicant, page=page)
    cp = ctrl.create_checkpoint()

    if not page or getattr(page, "is_closed", lambda: True)():
        return JSONResponse({
            "success": False,
            "stage": "checkpoint_created",
            "checkpoint": cp,
            "message": "مرورگر متقاضی باز نیست؛ چک‌پوینت وضعیت با موفقیت ذخیره شد.",
        })

    ok, msg = ctrl.execute_controlled_reload()
    if not ok:
        return JSONResponse({"success": False, "message": msg, "checkpoint": cp})

    dest = ctrl.detect_destination_stage()
    diff = {}
    if dest.get("destination") == "PAGE3_ACTIVE":
        diff = ctrl.reenter_and_calculate_diff()

    return JSONResponse({
        "success": True,
        "message": "ریلود رسمی کنترل‌شده انجام شد.",
        "checkpoint": cp,
        "destination": dest,
        "diff": diff,
    })


@router.get("/analyze-saved")
async def analyze_saved_page(file_path: str = Query(..., description="Path to saved HTML file"), user=Depends(require_api_key)):
    """
    Read-only deep analysis of a saved Page 3 HTML file without leaking secrets.
    """
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="فایل یافت نشد.")

    analysis = SavedPage3Analyzer.analyze_file(file_path)
    return JSONResponse(analysis)


@router.get("/compare-banks/{nid}")
async def compare_applicant_banks(nid: str, user=Depends(require_api_key)):
    """
    Compare current live options with historical options and detect STALE_ONLY banks.
    """
    nid = str(nid).strip()
    applicant = DBHandler.get_applicant(nid)
    if not applicant:
        raise HTTPException(status_code=404, detail="متقاضی یافت نشد.")

    runtime = DBHandler.get_runtime(nid) or {}
    live_banks = runtime.get("banks_available") or []
    prev_banks = runtime.get("banks_previous") or []

    monitor = ProvinceBankMonitor(province_name=runtime.get("province") or "سراسری")
    monitor.update_live_banks([{"value": str(i+1), "text": b} for i, b in enumerate(live_banks)])
    monitor.register_historical_snapshot([{"text": b} for b in prev_banks])

    target_bank = runtime.get("target_bank") or ""
    classification = monitor.classify_bank_discrepancy(target_bank) if target_bank else None

    return JSONResponse({
        "applicant_nid": nid,
        "live_banks_count": len(live_banks),
        "target_bank": target_bank,
        "target_classification": classification,
        "stale_banks": [b for b in prev_banks if b not in live_banks],
    })


@router.get("/maintenance-hypothesis")
async def get_maintenance_hypothesis_report(user=Depends(require_api_key)):
    """Return findings for the 18:00 maintenance hypothesis."""
    return JSONResponse(MAINTENANCE_TESTER.generate_evaluation_report())


@router.post("/maintenance-hypothesis/toggle")
async def toggle_maintenance_hypothesis(enable: bool = Query(...), user=Depends(require_api_key)):
    """Enable or disable the 18:00 maintenance hypothesis observer."""
    if enable:
        MAINTENANCE_TESTER.enable()
    else:
        MAINTENANCE_TESTER.disable()
    return JSONResponse({"enabled": MAINTENANCE_TESTER.enabled})
