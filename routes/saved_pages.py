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

from fastapi import APIRouter, Depends, HTTPException, status, Body
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
    extract_province_cities,
    repair_html_snapshot,
)
from bot_select_bank import repair_saved_pages
from live_sessions import LIVE_SESSIONS

router = APIRouter()
logger = logging.getLogger("mahanbot.saved_pages")

SAVED_PAGES_DIR = os.getenv("MAHANBOT_SAVED_PAGES_DIR", os.path.join(BASE_DIR, "saved_pages"))


def _app_data(app):
    data = (app or {}).get("data") or {}
    return json.loads(data) if isinstance(data, str) else data


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

    # Group folders by canonical applicant / national_id to eliminate duplicate rows
    # and skip ghost folders with 0 snapshots.
    grouped_folders: Dict[str, List[str]] = {}
    for d in sorted(subdirs):
        d_path = os.path.join(SAVED_PAGES_DIR, d)
        all_f = [f for f in os.listdir(d_path) if os.path.isfile(os.path.join(d_path, f))]
        # Check if folder has actual html/png files
        if not any(f.endswith((".html", ".htm", ".png", ".jpg")) for f in all_f):
            continue
        app_rec = _match_applicant_record(d)
        canon_id = (app_rec.get("national_id") if app_rec else None) or d
        grouped_folders.setdefault(canon_id, []).append(d)

    for canon_id, member_folders in grouped_folders.items():
        # Pick the folder with the latest modified HTML snapshot as the primary folder
        best_folder = member_folders[0]
        best_mtime = -1.0
        total_member_html_files = []
        total_member_png_files = []

        for fld in member_folders:
            fld_p = os.path.join(SAVED_PAGES_DIR, fld)
            f_list = [f for f in os.listdir(fld_p) if os.path.isfile(os.path.join(fld_p, f))]
            h_files = [f for f in f_list if f.endswith(".html")]
            p_files = [f for f in f_list if f.endswith(".png")]
            total_member_html_files.extend(h_files)
            total_member_png_files.extend(p_files)
            if h_files:
                newest_f = sorted(h_files, reverse=True)[0]
                mtime = os.path.getmtime(os.path.join(fld_p, newest_f))
                if mtime > best_mtime:
                    best_mtime = mtime
                    best_folder = fld

        folder = best_folder
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
            app_data = _app_data(app_record)
            loan_type = app_data.get("loan_type") or "ازدواج"
            cp = DBHandler.get_checkpoint(nid)
            if cp:
                checkpoint_data = cp

        # Determine High-level State
        page_state = BankPageState.UNKNOWN
        if not manifest_valid and os.path.exists(manifest_path):
            page_state = "NEEDS_REPAIR"
        elif analysis.get("tracking_code") or _app_data(app_record).get("tracking_code"):
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

        owner = LIVE_SESSIONS.get(nid) if nid else None
        live = owner.summary() if owner else {}
        cp_state = checkpoint_data.get("bank_state")
        if live.get("live") and live.get("state") in STATE_METADATA:
            page_state = live["state"]
        elif checkpoint_data.get("saved_at", 0) >= latest_html_mtime:
            if cp_state in STATE_METADATA:
                page_state = cp_state
            elif checkpoint_data.get("last_operation_result") == "BANK_CAPACITY_FULL":
                page_state = BankPageState.CAPACITY_FULL
        can_resume = bool(live.get("live") and live.get("validated") and live.get("bank_page_active")
                          and page_state not in {BankPageState.SESSION_EXPIRED, BankPageState.REGISTRATION_CONFIRMED})
        if latest_html and not analysis.get("is_bank_page") and not analysis.get("tracking_code") and manifest_valid:
            needs_repair_count += 1
        if can_resume:
            resumable_count += 1
        if page_state == BankPageState.CAPACITY_FULL:
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
            "province": analysis.get("province") or _app_data(app_record).get("state"),
            "city": analysis.get("city") or _app_data(app_record).get("city"),
            "loan_type": loan_type or "ازدواج",
            "state": page_state,
            "status_fa": meta["fa"],
            "badge_class": meta["badge_class"],
            "badge_color": meta["color"],
            "state_description": meta["description"],
            "app_status": app_status,
            "snapshot_count": len(total_member_html_files),
            "latest_html": latest_html,
            "latest_png": latest_png,
            "last_updated": latest_html_mtime or (os.path.getmtime(manifest_path) if os.path.exists(manifest_path) else 0.0),
            "banks_count": len(manifest_data.get("banks", [])) or len(analysis.get("available_banks", [])),
            "selected_bank": checkpoint_data.get("selected_bank") or analysis.get("selected_bank"),
            "selected_mode": checkpoint_data.get("bank_mode") or analysis.get("selected_mode"),
            "is_reserve": analysis.get("state") == BankPageState.RESERVE_PENDING or checkpoint_data.get("is_reserve", False),
            "error_message": analysis.get("error_message") or checkpoint_data.get("last_error_reason"),
            "tracking_code": analysis.get("tracking_code") or _app_data(app_record).get("tracking_code"),
            "can_resume": can_resume,
            "live_session": live,
            "has_checkpoint": bool(checkpoint_data),
            "requires_reauthentication": (not live.get("live") or page_state == BankPageState.SESSION_EXPIRED) and not DBHandler.has_final_submission(nid) and not checkpoint_data.get("reserve_confirmed") if nid else False,
            "check_method": "live_dom_only",
            "manifest_valid": manifest_valid,
        })

    return {
        "status": "ok",
        "total_saved": total_saved,
        "resumable_count": resumable_count,
        "waiting_capacity_count": waiting_capacity_count,
        "needs_repair_count": needs_repair_count,
        "saved_pages": items,
        "capacity_check_interval": (DBHandler.get_config()["sniper_interval_ms"] / 1000 if DBHandler.get_config().get("sniper_mode_enabled") else DBHandler.get_config().get("bank_capacity_check_interval", 20)),
        "bank_list_check_interval": DBHandler.get_config().get("bank_list_check_interval", 20),
        "check_method": "native_submit_retry" if DBHandler.get_config().get("sniper_mode_enabled") and DBHandler.get_config().get("auto_final_submit") and DBHandler.get_config().get("capacity_check_enabled") else "live_dom_only",
    }


@router.get("/saved-pages/{folder_name}/snapshot/{filename}")
def get_snapshot_file(folder_name: str, filename: str, _: bool = Depends(require_api_key)):
    """Serve a specific HTML snapshot or PNG screenshot."""
    file_path = _get_safe_file_path(folder_name, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    if filename.endswith(".html"):
        return FileResponse(file_path, media_type="text/html; charset=utf-8", headers={
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'none'; base-uri 'none'",
            "Cache-Control": "no-store",
        })
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
    app = _match_applicant_record(folder_name)
    nid = app.get("national_id") if app else None
    owner = LIVE_SESSIONS.get(nid) if nid else None
    if owner:
        owner.commands.put("repair_checkpoint")
        return {"status": "ok", "folder": folder_name, "queued": True}
    res = repair_saved_pages(SAVED_PAGES_DIR, only_folder=folder_name)
    if nid:
        html_files = sorted((f for f in os.listdir(folder_path) if f.endswith('.html')), reverse=True)
        if html_files:
            latest_file_path = os.path.join(folder_path, html_files[0])
            with open(latest_file_path, "r", encoding="utf-8", errors="replace") as stream:
                raw_html = stream.read()
            repaired_html = repair_html_snapshot(raw_html)
            if repaired_html != raw_html:
                try:
                    with open(latest_file_path, "w", encoding="utf-8") as stream:
                        stream.write(repaired_html)
                except Exception:
                    pass
            analysis = analyze_bank_page_html(repaired_html)
            if analysis.get("is_bank_page"):
                bank = analysis.get("selected_bank") or {}
                DBHandler.save_checkpoint(nid, {"last_verified_step": "STEP_3_BANK_SELECT",
                    "session_active": False, "can_resume": False, "repair_source": "offline_snapshot",
                    "selected_bank": bank.get("bank_name") or "",
                    "bank_mode": analysis.get("selected_mode") or _app_data(app).get("bank_mode", ""),
                    "bank_state": analysis["state"]})
    return {"status": "ok", "folder": folder_name, "audit": res}


@router.post("/saved-pages/repair-all")
def repair_all_saved_pages_endpoint(_: bool = Depends(require_api_key)):
    """Reconcile and repair all applicant manifests and orphan snapshots across saved_pages."""
    results = []
    if os.path.isdir(SAVED_PAGES_DIR):
        for folder in os.listdir(SAVED_PAGES_DIR):
            if os.path.isdir(os.path.join(SAVED_PAGES_DIR, folder)):
                results.append(repair_saved_page_single(folder, True))
    audits = [item.get("audit", {}) for item in results]
    return {"status": "ok", "folders": len(results), "results": results,
            "manifests_repaired": sum(a.get("manifests_repaired", 0) for a in audits),
            "orphan_files_added": sum(a.get("orphan_files_added", 0) for a in audits),
            "missing_files": [f for a in audits for f in a.get("missing_files", [])],
            "duplicates": [f for a in audits for f in a.get("duplicates", [])]}


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
    owner = LIVE_SESSIONS.get(nid)
    if not owner or not owner.summary()["live"]:
        raise HTTPException(status_code=409, detail="نشست زنده موجود نیست؛ ورود مجدد و بازیابی تنظیمات لازم است.")
    if owner.current_state in {BankPageState.SESSION_EXPIRED, BankPageState.REGISTRATION_CONFIRMED}:
        raise HTTPException(status_code=409, detail="نشست نیازمند ورود مجدد است یا درخواست قبلاً ثبت شده است.")
    if not owner.bank_page_active:
        raise HTTPException(status_code=409, detail="هنوز نشست زنده صفحه انتخاب بانک فعال نشده است")
    owner.commands.put("resume")
    return {"status": "ok", "session_id": owner.session_id, "validation_pending": True,
            "message": "بررسی DOM و ادامه در همان نشست به مالک مرورگر سپرده شد."}


@router.post("/saved-pages/pause/{nid}")
def pause_live_session(nid: str, _: bool = Depends(require_api_key)):
    owner = LIVE_SESSIONS.get(nid)
    if not owner:
        raise HTTPException(status_code=409, detail="نشست زنده وجود ندارد")
    owner.commands.put("pause")
    return {"status": "ok", "message": "پایش متوقف می‌شود؛ مرورگر و نشست حفظ می‌شوند."}


@router.post("/saved-pages/reauthenticate/{nid}")
def reauthenticate_applicant(nid: str, _: bool = Depends(require_api_key)):
    if not DBHandler.get_applicant(nid):
        raise HTTPException(status_code=404, detail="Applicant not found")
    if DBHandler.has_final_submission(nid) or DBHandler.get_checkpoint(nid).get("reserve_confirmed"):
        raise HTTPException(status_code=409, detail="درخواست یا رزرو قبلاً تأیید شده است")
    cp = DBHandler.get_checkpoint(nid)
    final_status = cp.get("final_submit_status")
    if final_status == "pending" and (
        cp.get("bank_state") == "CAPACITY_FULL"
        or cp.get("last_operation_result") in ("CAPACITY_FULL", "BANK_CAPACITY_FULL")
        or "تکمیل" in str(cp.get("last_error_reason", ""))
        or cp.get("browser_closed_by_user")
    ):
        DBHandler.release_final_submission_claim(nid)
        final_status = None
    if final_status:
        raise HTTPException(status_code=409, detail="ابتدا نتیجه درخواست قبلی را بررسی کنید؛ ورود مجدد خودکار مجاز نیست")
    owner = LIVE_SESSIONS.get(nid)
    if owner:
        if owner.current_state != BankPageState.SESSION_EXPIRED:
            raise HTTPException(status_code=409, detail="عملیات زنده فعال است؛ ورود مجدد مجاز نیست")
        owner.commands.put("reauthenticate")
        return {"status": "ok", "message": "ورود مجدد با حفظ تنظیمات درخواست شد."}
    import core.state as state
    if state.JOB_QUEUE is None:
        raise HTTPException(status_code=503, detail="Job queue is not initialized")
    job = state.JOB_QUEUE.enqueue("select", nid, {})
    if not job:
        raise HTTPException(status_code=409, detail="عملیات دیگری برای این متقاضی فعال است")
    return {"status": "ok", "job_id": job.id, "message": "ورود مجدد با بازیابی تنظیمات در صف قرار گرفت."}


@router.get("/saved-pages/logs/{nid}")
def live_session_logs(nid: str, _: bool = Depends(require_api_key)):
    from event_logger import EVENT_BROADCASTER, sanitize_meta
    events = [sanitize_meta(dict(e)) for e in EVENT_BROADCASTER.event_history
              if str(e.get("nid") or e.get("national_id") or "") == nid]
    return {"status": "ok", "events": events[-100:]}


@router.get("/saved-pages/{folder_name}/gallery")
def get_snapshot_gallery(folder_name: str, _: bool = Depends(require_api_key)):
    """Return all HTML and PNG snapshots for a saved applicant folder (including merged folders)."""
    folder_path = _get_safe_folder_path(folder_name)
    app_rec = _match_applicant_record(folder_name)
    target_folders = [folder_name]
    if app_rec and app_rec.get("national_id"):
        nid = app_rec.get("national_id")
        for d in os.listdir(SAVED_PAGES_DIR):
            if d != folder_name and (d == nid or d == app_rec.get("full_name")):
                target_folders.append(d)

    snapshots = []
    for fld in target_folders:
        fld_path = os.path.join(SAVED_PAGES_DIR, fld)
        if not os.path.isdir(fld_path):
            continue
        for fname in os.listdir(fld_path):
            p = os.path.join(fld_path, fname)
            if not os.path.isfile(p):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in {".html", ".htm", ".png", ".jpg", ".jpeg"}:
                mtime = os.path.getmtime(p)
                sz = os.path.getsize(p)
                snapshots.append({
                    "folder": fld,
                    "filename": fname,
                    "type": "html" if ext in {".html", ".htm"} else "image",
                    "size_bytes": sz,
                    "size_kb": round(sz / 1024, 1),
                    "mtime": mtime,
                    "date_str": time.strftime("%Y/%m/%d %H:%M:%S", time.localtime(mtime)),
                    "url": f"/saved-pages/{fld}/snapshot/{fname}"
                })

    snapshots.sort(key=lambda x: x["mtime"], reverse=True)
    return {"status": "ok", "folder": folder_name, "total": len(snapshots), "snapshots": snapshots}


@router.get("/saved-pages/{folder_name}/cities")
def get_saved_page_cities(folder_name: str, _: bool = Depends(require_api_key)):
    """Extract all available city options for the applicant province from latest HTML snapshot."""
    folder_path = _get_safe_folder_path(folder_name)
    html_files = sorted([f for f in os.listdir(folder_path) if f.endswith(".html")], reverse=True)
    if not html_files:
        app_rec = _match_applicant_record(folder_name)
        if app_rec and app_rec.get("national_id"):
            alt_fld = os.path.join(SAVED_PAGES_DIR, app_rec["national_id"])
            if os.path.isdir(alt_fld):
                html_files = sorted([f for f in os.listdir(alt_fld) if f.endswith(".html")], reverse=True)
                if html_files:
                    folder_path = alt_fld

    if not html_files:
        return {"status": "ok", "cities": [], "message": "هیچ فایل اسنپ‌شاتی یافت نشد"}

    with open(os.path.join(folder_path, html_files[0]), "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    cities = extract_province_cities(content)
    return {"status": "ok", "cities": cities, "count": len(cities)}


@router.post("/saved-pages/switch-city/{nid}")
def switch_saved_page_city(nid: str, payload: Dict[str, Any] = Body(...), _: bool = Depends(require_api_key)):
    """Switch city dropdown on live browser session for applicant nid."""
    city_code = str(payload.get("city_code") or "").strip()
    if not city_code:
        raise HTTPException(status_code=400, detail="کد شهر الزامی است")

    owner = LIVE_SESSIONS.get(nid)
    if not owner or not owner.page or getattr(owner.page, "is_closed", lambda: False)():
        raise HTTPException(status_code=400, detail="نشست زنده فعالی برای این متقاضی یافت نشد")

    from core.step3_bank_page import Step3BankPage
    step3 = Step3BankPage()
    ok = step3.switch_city(owner.page, city_code)
    if ok:
        return {"status": "ok", "message": f"شهر با موفقیت به کد {city_code} تغییر یافت"}
    raise HTTPException(status_code=500, detail="خطا در تغییر شهر در صفحه بانک مرکزی")


@router.post("/saved-pages/live-repair/{nid}")
def live_repair_saved_page(nid: str, _: bool = Depends(require_api_key)):
    """Safely reset or re-arm live session for applicant nid without throwing TargetClosedError."""
    owner = LIVE_SESSIONS.get(nid)
    if owner and owner.page and not getattr(owner.page, "is_closed", lambda: False)():
        from core.step3_bank_page import Step3BankPage
        step3 = Step3BankPage()
        step3.disable_logout_trap(owner.page)
        return {"status": "ok", "message": "نشست زنده با موفقیت ایمن‌سازی و احیا شد"}

    if owner:
        LIVE_SESSIONS.pop(nid, None)
    return {"status": "ok", "message": "نشست قدیمی پاکسازی شد. اکنون می‌توانید پایش مجدد را آغاز نمایید."}
