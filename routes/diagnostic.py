"""
routes/diagnostic.py — Deep Diagnostic System, Live Health Summary & One-Click ZIP Exporter for MAHANBOT
Provides comprehensive diagnostic exports, real-time health inspection, and test event broadcasting.
"""
import io
import os
import sys
import json
import time
import zipfile
import platform
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, JSONResponse

from core.deps import require_api_key
from core.state import BASE_DIR, JOB_QUEUE
from database import DBHandler
from proxy_manager import get_proxy_pool
from event_logger import EVENT_BROADCASTER, build_event, mask_sensitive_value

router = APIRouter(prefix="/diagnostic", tags=["diagnostic"])
logger = logging.getLogger("mahanbot.diagnostic")

SAVED_PAGES_DIR = os.path.join(BASE_DIR, "saved_pages")
START_TIME = time.time()


def get_diagnostic_summary_dict() -> Dict[str, Any]:
    """Compile a full snapshot of the bot system state, database, proxy pool, and workers."""
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    uptime_sec = round(time.time() - START_TIME, 1)

    # 1. Database & Applicants summary
    all_applicants = DBHandler.get_all_applicants()
    status_counts: Dict[str, int] = {}
    final_submitted_count = 0
    for app in all_applicants:
        st = str(app.get("status") or "Ready")
        status_counts[st] = status_counts.get(st, 0) + 1
        if DBHandler.has_final_submission(str(app.get("national_id") or "")):
            final_submitted_count += 1

    # 2. Concurrency & Job Queue status
    active_jobs = 0
    queued_jobs = 0
    if JOB_QUEUE:
        with getattr(JOB_QUEUE, "_lock", None) or io.BytesIO():
            jobs = getattr(JOB_QUEUE, "_jobs", {})
            active_jobs = sum(1 for j in jobs.values() if getattr(j, "status", "") in {"running", "cancelling"})
            queued_jobs = sum(1 for j in jobs.values() if getattr(j, "status", "") == "queued")

    # 3. Proxy pool status
    proxy_status = get_proxy_pool().get_pool_status()

    # 4. Saved pages stats
    saved_folders = 0
    saved_snapshots = 0
    if os.path.isdir(SAVED_PAGES_DIR):
        try:
            for root, dirs, files in os.walk(SAVED_PAGES_DIR):
                saved_snapshots += sum(1 for f in files if f.endswith((".html", ".json", ".png")))
            saved_folders = len([d for d in os.listdir(SAVED_PAGES_DIR) if os.path.isdir(os.path.join(SAVED_PAGES_DIR, d))])
        except Exception:
            pass

    return {
        "status": "healthy",
        "timestamp": now_iso,
        "uptime_seconds": uptime_sec,
        "system": {
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
            "os": os.name,
            "pid": os.getpid(),
        },
        "concurrency": {
            "max_concurrency": getattr(JOB_QUEUE, "max_concurrency", 4),
            "active_jobs": active_jobs,
            "queued_jobs": queued_jobs,
        },
        "applicants": {
            "total_count": len(all_applicants),
            "status_breakdown": status_counts,
            "final_submitted_count": final_submitted_count,
        },
        "proxy_pool": proxy_status,
        "saved_pages": {
            "folders_count": saved_folders,
            "snapshots_count": saved_snapshots,
        },
        "websocket": {
            "active_clients": len(getattr(EVENT_BROADCASTER, "active_connections", [])),
            "event_history_count": len(getattr(EVENT_BROADCASTER, "event_history", [])),
        },
    }


@router.get("/summary")
def get_diagnostic_summary():
    """Return live diagnostic and health metrics of the entire system."""
    return get_diagnostic_summary_dict()


@router.get("/export-zip")
def export_diagnostic_zip(
    include_saved_pages: bool = Query(True, description="Include saved HTML pages"),
    max_saved_files: int = Query(50, description="Max saved HTML/PNG files to include"),
):
    """
    Generate and stream an in-memory ZIP package containing:
      - diagnostic_summary.json
      - events/event_history.json
      - logs/readable_events.log
      - config/sanitized_config.json
      - database/sanitized_applicants.json
      - saved_pages/** (HTML and JSON snapshots)
    """
    zip_buffer = io.BytesIO()
    timestamp_str = datetime.now().strftime("%Y%m%d-%H%M%S")

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Summary
        summary = get_diagnostic_summary_dict()
        zf.writestr("diagnostic_summary.json", json.dumps(summary, ensure_ascii=False, indent=2))

        # 2. Config (sensitive values masked)
        config = DBHandler.get_config()
        sanitized_cfg = dict(config)
        for secret_key in ("admin_password", "api_key", "secret", "password"):
            if secret_key in sanitized_cfg:
                sanitized_cfg[secret_key] = "********"
        zf.writestr("config/settings_snapshot.json", json.dumps(sanitized_cfg, ensure_ascii=False, indent=2))

        # 3. Applicants (national_id and phone masked)
        raw_applicants = DBHandler.get_all_applicants()
        sanitized_apps = []
        for app in raw_applicants:
            item = dict(app)
            item["national_id"] = mask_sensitive_value(item.get("national_id"))
            item["mobile"] = mask_sensitive_value(item.get("mobile"))
            if "data" in item and isinstance(item["data"], str):
                try:
                    d = json.loads(item["data"])
                    d.pop("otp_code", None)
                    item["data"] = d
                except Exception:
                    pass
            sanitized_apps.append(item)
        zf.writestr("database/applicants_snapshot.json", json.dumps(sanitized_apps, ensure_ascii=False, indent=2))

        # 4. WebSocket Event History (JSON and readable text)
        events = list(getattr(EVENT_BROADCASTER, "event_history", []))
        zf.writestr("events/event_history.json", json.dumps(events, ensure_ascii=False, indent=2))

        readable_lines = []
        for ev in events:
            t = ev.get("timestamp", "-")
            typ = ev.get("type", "EVENT")
            nid = ev.get("nid") or ev.get("request_id") or "-"
            msg = ev.get("message") or ev.get("detail") or str(ev)
            readable_lines.append(f"[{t}] [{typ}] [{nid}] {msg}")
        zf.writestr("logs/readable_events.log", "\n".join(readable_lines))

        # 5. Saved Pages files
        if include_saved_pages and os.path.isdir(SAVED_PAGES_DIR):
            file_counter = 0
            for root, dirs, files in os.walk(SAVED_PAGES_DIR):
                if file_counter >= max_saved_files:
                    break
                for fname in files:
                    if file_counter >= max_saved_files:
                        break
                    full_p = os.path.join(root, fname)
                    rel_p = os.path.relpath(full_p, BASE_DIR)
                    try:
                        zf.write(full_p, arcname=rel_p)
                        file_counter += 1
                    except Exception:
                        pass

    zip_bytes = zip_buffer.getvalue()
    filename = f"mahanbot-diagnostic-{timestamp_str}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(zip_bytes)),
        },
    )


@router.post("/broadcast-test-event")
def broadcast_test_event(payload: Dict[str, Any]):
    """
    Test broadcast an alert event to connected WebSocket clients.
    Useful for testing audio chimes and visual toasts in the frontend.
    """
    event_type = payload.get("type", "favorite_found")
    bank_name = payload.get("bank", "بانک صادرات ایران")
    detail = payload.get("detail", "سهمیه آزمایشی غیرحضوری باز شد")
    message = payload.get("message", f"تست هشدار: سهمیه {bank_name} یافت شد")

    ev = build_event(
        event_type,
        nid="0012345678",
        bank=bank_name,
        detail=detail,
        message=message,
        is_test=True,
    )
    EVENT_BROADCASTER.emit_event(ev)
    return {"status": "broadcasted", "event": ev}
