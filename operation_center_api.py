from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from bank_archive import BANK_ARCHIVE_STORE, sanitize_nid
from database import DBHandler


class FinalSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


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

    @app.get("/api/v1/operation-center/status")
    def operation_center_status(_: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        settings = DBHandler.get_config() or {}
        applicants = DBHandler.get_all_applicants()
        active = []
        for applicant in applicants:
            status = str(applicant.get("status") or "")
            if any(token in status.lower() for token in ("running", "register", "select", "waiting", "awaiting")):
                active.append({
                    "full_name": applicant.get("full_name"),
                    "national_id": applicant.get("national_id"),
                    "status": status,
                })
        return {
            "status": "ok",
            "final_submit": bool(settings.get("final_submit", False)),
            "active_applicants": active,
            "archive_root": str(BANK_ARCHIVE_STORE.root),
            "sms_mode": "arrival-notification-plus-manual-code-submit",
        }

    @app.post("/api/v1/operation-center/final-submit")
    def set_final_submit(
        req: FinalSubmitRequest,
        _: Any = Depends(auth_dependency),
    ) -> Dict[str, Any]:
        settings = DBHandler.get_config() or {}
        settings["final_submit"] = bool(req.enabled)
        DBHandler.update_config(settings)
        return {
            "status": "ok",
            "final_submit": bool(req.enabled),
            "message": (
                "Final submission is enabled for waiting jobs"
                if req.enabled
                else "Final submission is disabled; jobs will only notify"
            ),
        }

    @app.get("/api/v1/archives/{nid}")
    def list_archives(nid: str, _: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        normalized = sanitize_nid(nid)
        if not normalized:
            raise HTTPException(status_code=400, detail="Invalid applicant national ID")
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
        return FileResponse(
            path=Path(path),
            media_type=media_type,
            filename=path.name,
        )
