# -*- coding: utf-8 -*-
"""HTTP surface for the Sadaf module. It does not import banking services."""
import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from modules.sadaf.catalog import public_catalog
from modules.sadaf.customers import delete_customer, get_customer, list_customers, now, save_customer, summary_counts
from modules.sadaf.db import ensure_schema, mark_open_runs_interrupted
from modules.sadaf.reports import backup_database, latest_report, recent_events
from modules.sadaf import relay_inbox, sms
from modules.sadaf.runner import get_runner
from modules.sadaf.settings import get_settings, save_settings

router = APIRouter(prefix="/api/sadaf", tags=["sadaf"])

ensure_schema()
mark_open_runs_interrupted(now())
relay_inbox.start()


def _fail(exc: Exception, code: int) -> HTTPException:
    return HTTPException(status_code=code, detail=str(exc))


@router.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "module": "sadaf", "enabled": os.getenv("SADAF_MODULE", "1") not in {"0", "false", "no"}}


@router.get("/summary")
def summary() -> Dict[str, Any]:
    counts = summary_counts()
    run = get_runner().snapshot()
    return {"counts": counts, "run": run}


@router.get("/catalog")
def catalog() -> Dict[str, Any]:
    return public_catalog()


@router.get("/customers")
def customers(q: str = "") -> Dict[str, Any]:
    return {"items": list_customers(q)}


@router.get("/customers/{customer_id}")
def one_customer(customer_id: int) -> Dict[str, Any]:
    item = get_customer(customer_id)
    if not item:
        raise HTTPException(status_code=404, detail="مشتری پیدا نشد.")
    return item


@router.post("/customers")
def create_customer(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return get_customer(save_customer(payload))
    except ValueError as exc:
        raise _fail(exc, 400) from exc


@router.put("/customers/{customer_id}")
def update_customer(customer_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return get_customer(save_customer(payload, customer_id))
    except ValueError as exc:
        raise _fail(exc, 400) from exc


@router.delete("/customers/{customer_id}")
def remove_customer(customer_id: int) -> Dict[str, bool]:
    delete_customer(customer_id)
    return {"ok": True}


@router.post("/runs/open")
def open_site(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        raw_id = payload.get("customer_id")
        customer_id = int(raw_id) if raw_id else None
        return get_runner().open_site(customer_id)
    except (RuntimeError, ValueError) as exc:
        raise _fail(exc, 409 if isinstance(exc, RuntimeError) else 400) from exc


@router.post("/runs/start")
def start_run(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        raw_id = payload.get("customer_id")
        customer_id = int(raw_id) if raw_id else None
        return get_runner().start(int(payload.get("start_row") or 1), bool(payload.get("force")), customer_id)
    except (RuntimeError, ValueError) as exc:
        raise _fail(exc, 409 if isinstance(exc, RuntimeError) else 400) from exc


@router.post("/runs/captcha")
def run_captcha(payload: Dict[str, str]) -> Dict[str, Any]:
    try:
        return get_runner().submit_code(str(payload.get("code") or ""))
    except (RuntimeError, ValueError) as exc:
        raise _fail(exc, 409 if isinstance(exc, RuntimeError) else 400) from exc


@router.post("/sms")
def post_sms(payload: Dict[str, str]) -> Dict[str, Any]:
    """Operator box: the 7-digit code or a whole NIOPDC SMS. The text is not echoed back."""
    try:
        kind = get_runner().submit_sms(str(payload.get("text") or ""))
    except ValueError as exc:
        raise _fail(exc, 400) from exc
    labels = {"code": "کد تایید سدف گرفته شد.", "request": "کد رهگیری در فایل جهاد ذخیره شد.",
              "delivery": "وضعیت پخش در فایل جهاد به‌روز شد.", "duplicate": "این پیامک قبلاً ثبت شده بود."}
    return {"ok": True, "kind": kind, "message": labels.get(kind, "ذخیره شد.")}


@router.get("/sms/status")
def sms_status() -> Dict[str, Any]:
    state = dict(relay_inbox.STATE)
    return {"relay_connected": bool(state.get("connected")), "relay_error": state.get("last_error") or ""}


@router.get("/jahad")
def jahad() -> Dict[str, Any]:
    return {"items": sms.jahad_rows(), "path": str(sms.JAHAD_FILE)}


@router.get("/jahad.csv")
def jahad_csv():
    path = sms.export_jahad()
    return FileResponse(path, filename="jahad-requests.csv", media_type="text/csv")


@router.post("/runs/command")
def run_command(payload: Dict[str, str]) -> Dict[str, Any]:
    try:
        return get_runner().command(str(payload.get("command") or ""))
    except (RuntimeError, ValueError) as exc:
        raise _fail(exc, 409 if isinstance(exc, RuntimeError) else 400) from exc


@router.get("/events")
def events(limit: int = 30) -> Dict[str, Any]:
    return {"items": recent_events(limit)}


@router.get("/settings")
def read_settings() -> Dict[str, str]:
    return get_settings()


@router.put("/settings")
def write_settings(payload: Dict[str, str]) -> Dict[str, str]:
    try:
        return save_settings(payload)
    except ValueError as exc:
        raise _fail(exc, 400) from exc


@router.post("/backup")
def backup() -> Dict[str, str]:
    return {"path": backup_database()}


@router.get("/reports/latest")
def download_report():
    path = latest_report()
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="هنوز گزارشی ساخته نشده است.")
    return FileResponse(path, filename=os.path.basename(path), media_type="text/csv")
