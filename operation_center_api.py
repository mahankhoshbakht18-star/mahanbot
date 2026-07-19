from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from bank_archive import BANK_ARCHIVE_STORE, sanitize_nid
from database import DBHandler


FINAL_SUBMIT_KEY = "bank_final_submit_enabled"


class FinalSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nid: str = Field(min_length=1, max_length=32)
    enabled: bool


def _applicant_data(nid: str) -> Dict[str, Any]:
    row = DBHandler.get_applicant(nid)
    if not row:
        raise HTTPException(status_code=404, detail="Applicant not found")
    try:
        row_data = dict(row) if not isinstance(row, dict) else row
        value = row_data.get("data")
        if isinstance(value, dict):
            return dict(value)
        return json.loads(value or "{}")
    except Exception:
        return {}


def _ensure_archive_folders() -> int:
    created = 0
    for applicant in DBHandler.get_all_applicants():
        nid = sanitize_nid(applicant.get("national_id"))
        if not nid:
            continue
        try:
            BANK_ARCHIVE_STORE.applicant_dir(nid)
            created += 1
        except Exception:
            continue
    return created


def _archive_payload(nid: str) -> Dict[str, Any]:
    records = BANK_ARCHIVE_STORE.list_for_applicant(nid)
    result = []
    for item in records:
        record = dict(item)
        screenshot = record.get("screenshot")
        html = record.get("html")
        metadata = record.get("metadata")
        record["screenshot_url"] = (
            f"/api/v1/archives/{nid}/file/{quote(str(screenshot), safe='/')}" if screenshot else None
        )
        record["html_url"] = (
            f"/api/v1/archives/{nid}/file/{quote(str(html), safe='/')}" if html else None
        )
        record["metadata_url"] = (
            f"/api/v1/archives/{nid}/file/{quote(str(metadata), safe='/')}" if metadata else None
        )
        result.append(record)
    return {"status": "ok", "nid": nid, "count": len(result), "archives": result}


def install_operation_center(app: FastAPI, auth_dependency: Callable[..., Any]) -> None:
    if getattr(app.state, "operation_center_installed", False):
        return
    app.state.operation_center_installed = True

    @app.on_event("startup")
    async def create_archive_folders_on_startup() -> None:
        _ensure_archive_folders()

    @app.get("/api/v1/operation-center/status")
    def operation_center_status(_: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        applicants = DBHandler.get_all_applicants()
        active = []
        for applicant in applicants:
            nid = sanitize_nid(applicant.get("national_id"))
            if nid:
                try:
                    BANK_ARCHIVE_STORE.applicant_dir(nid)
                except Exception:
                    pass
            status = str(applicant.get("status") or "")
            if any(token in status.lower() for token in ("running", "register", "select", "waiting", "awaiting")):
                active.append({
                    "full_name": applicant.get("full_name"),
                    "national_id": applicant.get("national_id"),
                    "status": status,
                })
        return {
            "status": "ok",
            "active_applicants": active,
            "archive_root": str(BANK_ARCHIVE_STORE.root),
            "sms_mode": "arrival-notification-plus-manual-code-submit",
            "final_submit_mode": "per-applicant-one-shot",
        }

    @app.get("/api/v1/operation-center/final-submit/{nid}")
    def get_final_submit_permission(
        nid: str,
        _: Any = Depends(auth_dependency),
    ) -> Dict[str, Any]:
        normalized = sanitize_nid(nid)
        if not normalized:
            raise HTTPException(status_code=400, detail="Invalid applicant national ID")
        data = _applicant_data(normalized)
        return {
            "status": "ok",
            "nid": normalized,
            "enabled": bool(data.get(FINAL_SUBMIT_KEY, False)),
            "mode": "one-shot",
        }

    @app.post("/api/v1/operation-center/final-submit")
    def set_final_submit(
        req: FinalSubmitRequest,
        _: Any = Depends(auth_dependency),
    ) -> Dict[str, Any]:
        normalized = sanitize_nid(req.nid)
        if not normalized:
            raise HTTPException(status_code=400, detail="Invalid applicant national ID")
        _applicant_data(normalized)
        if not DBHandler.update_applicant_data(normalized, {FINAL_SUBMIT_KEY: bool(req.enabled)}):
            raise HTTPException(status_code=500, detail="Final-submit permission could not be saved")
        return {
            "status": "ok",
            "nid": normalized,
            "enabled": bool(req.enabled),
            "mode": "one-shot",
            "message": (
                "One final submission is permitted for this applicant"
                if req.enabled
                else "Final submission permission was revoked for this applicant"
            ),
        }

    @app.get("/api/v1/archives/{nid}")
    def list_archives(nid: str, _: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        normalized = sanitize_nid(nid)
        if not normalized:
            raise HTTPException(status_code=400, detail="Invalid applicant national ID")
        try:
            BANK_ARCHIVE_STORE.applicant_dir(normalized)
        except Exception:
            pass
        return _archive_payload(normalized)

    @app.get("/api/v1/archives/{nid}/file/{relative_path:path}")
    def read_archive_file(
        nid: str,
        relative_path: str,
        _: Any = Depends(auth_dependency),
    ) -> FileResponse:
        normalized = sanitize_nid(nid)
        if not normalized:
            raise HTTPException(status_code=400, detail="Invalid applicant national ID")
        try:
            path = BANK_ARCHIVE_STORE.resolve_file(normalized, relative_path)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="Archive file not found")

        suffix = path.suffix.lower()
        media_type = {
            ".png": "image/png",
            ".html": "text/html; charset=utf-8",
            ".json": "application/json",
        }.get(suffix, "application/octet-stream")
        filename = quote(path.name)
        headers = {
            "Content-Disposition": f"inline; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
        }
        if suffix == ".html":
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
        return FileResponse(
            path=Path(path),
            media_type=media_type,
            headers=headers,
        )
