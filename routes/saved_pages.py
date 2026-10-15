"""
routes/saved_pages.py — Saved Pages API & Management Endpoints
Provides inspection, preview, health checks, manifest repairs, and resumption
for saved bank selection pages and applicant checkpoints.
"""
import os
import json
import time
import logging
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse

from core.deps import require_api_key
from core.state import BASE_DIR
from database import DBHandler
from bank_page_manager import (
    BankPageState,
    STATE_METADATA,
    analyze_bank_page_html,
    normalize_persian,
    is_reserve_text,
    classify_capacity_or_error,
)
from bot_select_bank import repair_saved_pages

router = APIRouter()
logger = logging.getLogger("mahanbot.saved_pages")

SAVED_PAGES_DIR = os.path.join(BASE_DIR, "saved_pages")


def _get_safe_folder_path(folder_name: str) -> str:
    """Resolve and validate folder path to prevent path traversal attacks."""
    folder_path = os.path.abspath(os.path.join(SAVED_PAGES_DIR, folder_name))
    common = os.path.commonpath([SAVED_PAGES_DIR, folder_path])
    if common != os.path.abspath(SAVED_PAGES_DIR):
        raise HTTPException(status_code=400, detail="Invalid folder name")
    return folder_path


def _get_safe_file_path(folder_name: str, filename: str) -> str:
    """Resolve and validate snapshot file path."""
    folder_path = _get_safe_folder_path(folder_name)
    file_path = os.path.abspath(os.path.join(folder_path, filename))
    common = os.path.commonpath([folder_path, file_path])
    if common != folder_path:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return file_path


def _match_applicant_record(folder_name: str) -> Optional[Dict[str, Any]]:
    """Match a saved_pages folder name to an applicant in the database."""
    applicants = DBHandler.get_all_applicants()
    norm_folder = normalize_persian(folder_name).strip()

    # 1. Exact match on full_name or national_id
    for app in applicants:
        nid = str(app.get("national_id") or "").strip()
        full_name = normalize_persian(app.get("full_name") or "").strip()
        if nid == norm_folder or full_name == norm_folder:
            return app

    # 2. Substring match
    for app in applicants:
        full_name = normalize_persian(app.get("full_name") or "").strip()
        if full_name and (full_name in norm_folder or norm_folder in full_name):
            return app

    return None


@router.get("/saved-pages")
def list_saved_pages(_: bool = Depends(require_api_key)):
    """
    List all applicant snapshot folders in saved_pages/ merged with manifest,
    HTML analysis, and database checkpoint records.
    """
    if not os.path.exists(SAVED_PAGES_DIR):
        os.makedirs(SAVED_PAGES_DIR, exist_ok=True)

    items = []
    total_saved = 0
    resumable_count = 0
    waiting_capacity_count = 0
    needs_repair_count = 0

    subdirs = [d for d in os.listdir(SAVED_PAGES_DIR) if os.path.isdir(os.path.join(SAVED_PAGES_DIR, d))]

    for folder in sorted(subdirs):
        folder_path = os.path.join(SAVED_PAGES_DIR, folder)
        manifest_path = os.path.join(folder_path, "banks_seen.json")

        manifest_valid = False
        manifest_data: Dict[str, Any] = {}
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest_data = json.load(f)
                manifest_valid = True
            except Exception:
                manifest_valid = False

        if not manifest_valid and os.path.exists(manifest_path):
            needs_repair_count += 1

        # Scan for HTML / PNG files
        all_files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
        html_files = sorted([f for f in all_files if f.endswith(".html")], reverse=True)
        png_files = sorted([f for f in all_files if f.endswith(".png")], reverse=True)

        latest_html = html_files[0] if html_files else None
        latest_png = png_files[0] if png_files else None

        # Analyze latest HTML snapshot if present
        analysis: Dict[str, Any] = {}
        latest_html_mtime = 0.0
        if latest_html:
            latest_path = os.path.join(folder_path, latest_html)
            latest_html_mtime = os.path.getmtime(latest_path)
            try:
                with open(latest_path, "r", encoding="utf-8", errors="replace") as hf:
                    html_content = hf.read()
                analysis = analyze_bank_page_html(html_content)
            except Exception as e:
                logger.warning("Failed to analyze HTML for %s: %s", folder, e)

        # Match with DB applicant
        app_record = _match_applicant_record(folder)
        nid = None
        app_status = None
        loan_type = None
        checkpoint_data: Dict[str, Any] = {}

        if app_record:
            nid = app_record.get("national_id")
            app_status = app_record.get("status")
            app_data = app_record.get("data") or {}
            loan_type = app_data.get("loan_type") or "ازدواج"
            cp = DBHandler.get_checkpoint(nid)
            if cp:
                checkpoint_data = cp

        # Determine High-level State
        page_state = BankPageState.UNKNOWN
        if not manifest_valid and os.path.exists(manifest_path):
            page_state = "NEEDS_REPAIR"
        elif analysis.get("tracking_code") or (app_record and app_record.get("data", {}).get("tracking_code")):
            page_state = BankPageState.REGISTRATION_CONFIRMED
        elif analysis.get("state") and analysis.get("state") != BankPageState.UNKNOWN:
            page_state = analysis.get("state")
        elif checkpoint_data.get("last_operation_result") == "BANK_CAPACITY_FULL":
            page_state = BankPageState.CAPACITY_FULL
        elif checkpoint_data.get("last_operation_result") == "RESERVE_PENDING":
            page_state = BankPageState.RESERVE_PENDING
        elif checkpoint_data.get("expected_next_step") == "FINAL_SUBMIT":
            page_state = BankPageState.MANUAL_ACTION_REQUIRED
        elif analysis.get("is_bank_page"):
            page_state = BankPageState.BANK_PAGE_READY

        if page_state in {BankPageState.BANK_PAGE_READY, BankPageState.BANK_SELECTED, BankPageState.NON_PRESENTIAL_SELECTED, BankPageState.MANUAL_ACTION_REQUIRED, BankPageState.RESERVE_PENDING}:
            resumable_count += 1
        elif page_state == BankPageState.CAPACITY_FULL:
            waiting_capacity_count += 1

        total_saved += 1

        meta = STATE_METADATA.get(page_state, {
            "fa": "نیازمند بررسی",
            "badge_class": "badge-gray",
            "color": "#9CA3AF",
            "description": "وضعیت این صفحه نیازمند بررسی دستی یا ترمیم است.",
        })

        items.append({
            "folder": folder,
            "nid": nid or analysis.get("applicant_nid"),
            "full_name": (app_record.get("full_name") if app_record else None) or analysis.get("applicant_name") or folder,
            "province": analysis.get("province") or (app_record.get("data", {}).get("state") if app_record else None),
            "city": analysis.get("city") or (app_record.get("data", {}).get("city") if app_record else None),
            "loan_type": loan_type or "ازدواج",
            "state": page_state,
            "status_fa": meta["fa"],
            "badge_class": meta["badge_class"],
            "badge_color": meta["color"],
            "state_description": meta["description"],
            "app_status": app_status,
            "snapshot_count": len(html_files),
            "latest_html": latest_html,
            "latest_png": latest_png,
            "last_updated": latest_html_mtime or (os.path.getmtime(manifest_path) if os.path.exists(manifest_path) else 0.0),
            "banks_count": len(manifest_data.get("banks", [])) or len(analysis.get("available_banks", [])),
            "selected_bank": analysis.get("selected_bank") or checkpoint_data.get("selected_bank"),
            "selected_mode": analysis.get("selected_mode") or checkpoint_data.get("bank_mode"),
            "is_reserve": analysis.get("state") == BankPageState.RESERVE_PENDING or checkpoint_data.get("is_reserve", False),
            "error_message": analysis.get("error_message") or checkpoint_data.get("last_error_reason"),
            "tracking_code": analysis.get("tracking_code") or (app_record.get("data", {}).get("tracking_code") if app_record else None),
            "can_resume": page_state in {BankPageState.BANK_PAGE_READY, BankPageState.BANK_SELECTED, BankPageState.NON_PRESENTIAL_SELECTED, BankPageState.MANUAL_ACTION_REQUIRED, BankPageState.RESERVE_PENDING, BankPageState.CAPACITY_FULL},
            "manifest_valid": manifest_valid,
        })

    return {
        "status": "ok",
        "total_saved": total_saved,
        "resumable_count": resumable_count,
        "waiting_capacity_count": waiting_capacity_count,
        "needs_repair_count": needs_repair_count,
        "saved_pages": items,
    }


@router.get("/saved-pages/{folder_name}/snapshot/{filename}")
def get_snapshot_file(folder_name: str, filename: str, _: bool = Depends(require_api_key)):
    """Serve a specific HTML snapshot or PNG screenshot."""
    file_path = _get_safe_file_path(folder_name, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    if filename.endswith(".html"):
        return FileResponse(file_path, media_type="text/html; charset=utf-8")
    elif filename.endswith(".png"):
        return FileResponse(file_path, media_type="image/png")
    elif filename.endswith(".json"):
        return FileResponse(file_path, media_type="application/json; charset=utf-8")
    else:
        return FileResponse(file_path, media_type="application/octet-stream")


@router.get("/saved-pages/{folder_name}/analysis")
def get_saved_page_analysis(folder_name: str, _: bool = Depends(require_api_key)):
    """Perform deep analysis of the applicant's latest saved page HTML."""
    folder_path = _get_safe_folder_path(folder_name)
    html_files = sorted([f for f in os.listdir(folder_path) if f.endswith(".html")], reverse=True)
    if not html_files:
        raise HTTPException(status_code=404, detail="No HTML snapshot found in folder")

    latest_path = os.path.join(folder_path, html_files[0])
    with open(latest_path, "r", encoding="utf-8", errors="replace") as hf:
        content = hf.read()

    analysis = analyze_bank_page_html(content)
    meta = STATE_METADATA.get(analysis.get("state"), STATE_METADATA[BankPageState.UNKNOWN])
    return {
        "status": "ok",
        "folder": folder_name,
        "filename": html_files[0],
        "analysis": analysis,
        "status_fa": meta["fa"],
        "badge_class": meta["badge_class"],
        "description": meta["description"],
    }


@router.post("/saved-pages/{folder_name}/health-check")
def health_check_saved_page(folder_name: str, _: bool = Depends(require_api_key)):
    """Audit the integrity of a saved page folder, manifest, files, and DB checkpoint."""
    folder_path = _get_safe_folder_path(folder_name)
    manifest_path = os.path.join(folder_path, "banks_seen.json")

    issues: List[str] = []
    manifest_status = "ok"
    manifest_data: Dict[str, Any] = {}

    if not os.path.exists(manifest_path):
        manifest_status = "missing"
        issues.append("فایل مانیفست banks_seen.json وجود ندارد.")
    else:
        try:
            with open(manifest_path, "r", encoding="utf-8") as mf:
                manifest_data = json.load(mf)
        except Exception as e:
            manifest_status = "corrupted"
            issues.append(f"فایل مانیفست خراب یا نامعتبر است: {e}")

    # Check files on disk vs manifest snapshots
    files_on_disk = set(os.listdir(folder_path))
    snapshots = manifest_data.get("snapshots", [])
    manifest_files = set()
    for s in snapshots:
        for fn in s.get("files", []):
            manifest_files.add(fn)

    missing_from_disk = manifest_files - files_on_disk
    orphan_files = {f for f in files_on_disk if f.endswith((".html", ".png")) and f not in manifest_files}

    if missing_from_disk:
        issues.append(f"{len(missing_from_disk)} فایل در مانیفست ثبت شده اما در دیسک موجود نیست.")
    if orphan_files:
        issues.append(f"{len(orphan_files)} فایل روی دیسک بدون ثبت در مانیفست وجود دارد.")

    # Match applicant & checkpoint
    app = _match_applicant_record(folder_name)
    has_checkpoint = False
    if app:
        cp = DBHandler.get_checkpoint(app.get("national_id"))
        has_checkpoint = bool(cp)
    else:
        issues.append("اطلاعات متقاضی متناظر در پایگاه داده یافت نشد.")

    healthy = len(issues) == 0

    return {
        "status": "ok",
        "healthy": healthy,
        "folder": folder_name,
        "manifest_status": manifest_status,
        "total_files": len(files_on_disk),
        "snapshots_recorded": len(snapshots),
        "missing_files": list(missing_from_disk),
        "orphan_files": list(orphan_files),
        "has_db_checkpoint": has_checkpoint,
        "issues": issues,
    }


@router.post("/saved-pages/{folder_name}/repair")
def repair_saved_page_single(folder_name: str, _: bool = Depends(require_api_key)):
    """Run atomic repair specifically for one applicant's saved page folder."""
    folder_path = _get_safe_folder_path(folder_name)
    res = repair_saved_pages(SAVED_PAGES_DIR)
    return {"status": "ok", "folder": folder_name, "audit": res}


@router.post("/saved-pages/repair-all")
def repair_all_saved_pages_endpoint(_: bool = Depends(require_api_key)):
    """Reconcile and repair all applicant manifests and orphan snapshots across saved_pages."""
    audit = repair_saved_pages(SAVED_PAGES_DIR)
    return {"status": "ok", **audit}


@router.delete("/saved-pages/{folder_name}/snapshot/{filename}")
def archive_snapshot_file(folder_name: str, filename: str, _: bool = Depends(require_api_key)):
    """Safely archive a snapshot file into an .archive subfolder."""
    file_path = _get_safe_file_path(folder_name, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    folder_path = _get_safe_folder_path(folder_name)
    archive_dir = os.path.join(folder_path, ".archive")
    os.makedirs(archive_dir, exist_ok=True)

    dest = os.path.join(archive_dir, f"{int(time.time())}_{filename}")
    os.replace(file_path, dest)

    # Re-run repair to update manifest cleanly
    repair_saved_pages(SAVED_PAGES_DIR)

    return {"status": "ok", "archived": filename}


@router.post("/saved-pages/resume/{nid}")
async def resume_applicant_checkpoint(nid: str, _: bool = Depends(require_api_key)):
    """Resume applicant bot from checkpoint."""
    app = DBHandler.get_applicant(nid)
    if not app:
        raise HTTPException(status_code=404, detail="Applicant not found")

    # Check if duplicate submission already recorded
    has_sub = DBHandler.has_final_submission(nid)
    if has_sub:
        raise HTTPException(
            status_code=400,
            detail=f"این متقاضی قبلاً ثبت نهایی شده است (کد رهگیری: {has_sub}). امکان ادامه مجدد وجود ندارد."
        )

    # Check checkpoint
    cp = DBHandler.get_checkpoint(nid)
    if not cp:
        raise HTTPException(status_code=400, detail="چک‌پوینتی برای این متقاضی یافت نشد.")

    import core.state as _state
    if _state.JOB_QUEUE is None:
        raise HTTPException(status_code=500, detail="Job queue is not initialized")

    try:
        from core.job_queue import BotJob
        job_id = _state.JOB_QUEUE.submit(BotJob(bot_name="select", nid=nid))
        DBHandler.update_status(nid, "Running", "ادامه عملیات از چک‌پوینت ذخیره‌شده")
        return {"status": "ok", "job_id": job_id, "message": "ربات با موفقیت از آخرین وضعیت ذخیره‌شده راه‌اندازی شد."}
    except Exception as e:
        logger.exception("Failed to resume bot for %s: %s", nid, e)
        raise HTTPException(status_code=500, detail=str(e))
