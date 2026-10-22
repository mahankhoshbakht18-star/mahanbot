"""
routes/otp.py — OTP receive, wait, manual entry, clear.
"""
import asyncio
import time
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from database import DBHandler
from event_logger import log_event
import core.state as _state
from core.state import OTP_EVENTS, OTP_EVENT_LOCK
from core.deps import require_otp_secret
from event_logger import EVENT_BROADCASTER, build_event
from core.helpers import _normalize_nid, _mask_identifier, _serialize_applicants
from core.models import SMSRequest
from sms_service import route_incoming_sms, parse_sms_text

import logging

router = APIRouter()
logger = logging.getLogger("mahanbot")


def _emit_applicants_snapshot() -> None:
    EVENT_BROADCASTER.emit_event(
        build_event("applicants_snapshot", applicants=_serialize_applicants())
    )


# ── OTP helpers ───────────────────────────────────────────────────────
async def _get_otp_event(nid: str) -> asyncio.Event:
    async with OTP_EVENT_LOCK:
        event = OTP_EVENTS.get(nid)
        if not event:
            event = asyncio.Event()
            OTP_EVENTS[nid] = event
        return event


async def _signal_otp_event(nid: str) -> None:
    event = await _get_otp_event(nid)
    event.set()


def _handle_receive_sms(nid: str, code: str, status_label: str = "received", ts: Optional[float] = None) -> bool:
    import re
    from sms_service import normalize_digits
    code = normalize_digits(code).strip()
    if not re.fullmatch(r"[0-9]{4,8}", code):
        return False
    otp_ts = float(ts) if ts is not None else time.time()
    previous = DBHandler.get_otp_record(nid)
    if previous and previous.get("otp") == code and otp_ts - float(previous.get("ts") or 0) < 600:
        # Forwarder retries must not turn a stale/consumed message into a fresh code.
        return True
    request_id = DBHandler.get_runtime(nid).get("otp_request_id")
    success = DBHandler.set_otp(nid, code, ts=otp_ts, status=status_label, request_id=request_id)
    if not success:
        return False
    DBHandler.update_runtime(nid, otp_state="SMS_RECEIVED")
    log_event(nid, None, "OTP received", "success")
    logger.info("OTP received for %s; notifying waiters", _mask_identifier(nid))
    if _state.MAIN_LOOP:
        _state.MAIN_LOOP.call_soon_threadsafe(lambda: asyncio.create_task(_signal_otp_event(nid)))
    return True


# ── OTP endpoints ─────────────────────────────────────────────────────
@router.post("/receive_sms")
def receive_sms(req: SMSRequest, _: bool = Depends(require_otp_secret)):
    if req.request_id and req.nid and req.request_id != DBHandler.get_runtime(_normalize_nid(req.nid)).get("otp_request_id"):
        raise HTTPException(status_code=409, detail="OTP request does not match the current applicant request")
    # 1. If text/message/body is provided, route via smart parser
    sms_text = req.text or req.message or req.body
    if sms_text:
        ok, res = route_incoming_sms(
            text=sms_text,
            from_number=req.from_number or req.phone,
            explicit_nid=req.nid,
            explicit_code=req.code,
            request_id=req.request_id,
        )
        if ok:
            return {"status": "ok", "ts": time.time(), **res}
        raise HTTPException(status_code=400, detail=res)

    raw_nid = str(req.nid or "").strip()
    nid = _normalize_nid(raw_nid)
    code = str(req.code or "").strip()

    # If code is present but nid is omitted, attempt auto-routing to active waiter
    if code and not nid:
        ok, res = route_incoming_sms(text="", explicit_code=code, request_id=req.request_id)
        if ok:
            return {"status": "ok", "ts": time.time(), **res}

    if not (nid or raw_nid) or not code:
        raise HTTPException(status_code=400, detail="Invalid payload")

    request_ts = time.time()
    success = _handle_receive_sms(nid or raw_nid, code, status_label="received", ts=request_ts)
    if not success and raw_nid and raw_nid != nid:
        success = _handle_receive_sms(raw_nid, code, status_label="received", ts=request_ts)
    if not success:
        logger.warning(
            "OTP not saved: applicant not found for nid=%s raw=%s",
            _mask_identifier(nid), _mask_identifier(raw_nid),
        )
        raise HTTPException(status_code=404, detail="Applicant not found")
    _emit_applicants_snapshot()
    return {"status": "ok", "ts": request_ts}


@router.post("/sms/webhook")
@router.post("/api/sms/webhook")
async def universal_sms_webhook(request: Request, _: bool = Depends(require_otp_secret)):
    """
    Universal SMS Webhook compatible with Android SMS Forwarder apps, Tasker,
    MacroDroid, GSM dongles, and custom webhook forwarders.
    Accepts JSON, Form-data, or raw text body.
    """
    body_text = ""
    from_number = None
    explicit_nid = None
    explicit_code = None
    request_id = None

    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            data = await request.json()
            if isinstance(data, dict):
                body_text = str(data.get("text") or data.get("message") or data.get("body") or data.get("msg") or data.get("content") or "").strip()
                from_number = str(data.get("from") or data.get("sender") or data.get("phone") or data.get("from_number") or "").strip()
                explicit_nid = data.get("nid")
                explicit_code = data.get("code") or data.get("otp")
                request_id = data.get("request_id")
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            body_text = str(form.get("text") or form.get("message") or form.get("body") or form.get("msg") or form.get("content") or "").strip()
            from_number = str(form.get("from") or form.get("sender") or form.get("phone") or "").strip()
            explicit_nid = form.get("nid")
            explicit_code = form.get("code") or form.get("otp")
            request_id = form.get("request_id")
        except Exception:
            pass
    
    if not body_text:
        # Fallback to raw body text
        raw_bytes = await request.body()
        body_text = raw_bytes.decode("utf-8", errors="replace").strip()

    # Query params fallback
    if not body_text and request.query_params:
        body_text = request.query_params.get("text") or request.query_params.get("message") or ""
        from_number = from_number or request.query_params.get("from")

    if not body_text and not explicit_code:
        raise HTTPException(status_code=400, detail="Empty SMS body")

    ok, res = route_incoming_sms(
        text=body_text,
        from_number=from_number,
        explicit_nid=explicit_nid,
        explicit_code=explicit_code,
        request_id=request_id,
    )

    if ok:
        return {"status": "ok", "ts": time.time(), **res}
    raise HTTPException(status_code=422, detail=res)


@router.post("/api/sms/test-parse")
def test_parse_sms(payload: Dict[str, Any]):
    """Test utility to preview SMS parsing and routing without persisting."""
    text = payload.get("text") or payload.get("message") or ""
    parsed = parse_sms_text(text)
    from sms_service import find_active_otp_waiter
    active = find_active_otp_waiter()
    return {
        "status": "success",
        "success": True,
        "parsed": {
            "code": parsed.get("code"),
            "otp_code": parsed.get("code"),
            "nid": parsed.get("nid"),
            "national_id": parsed.get("nid"),
            "phone": parsed.get("phone"),
            "mobile": parsed.get("phone"),
            "raw": parsed.get("raw"),
        },
        "matched_waiter": active.get("national_id") if active else None,
        "active_waiter": {
            "nid": active.get("national_id"),
            "full_name": active.get("full_name"),
            "status": active.get("status"),
        } if active else None,
    }


@router.post("/manual_otp")
@router.post("/otp/manual")
def manual_otp(req: SMSRequest):
    from sms_service import normalize_digits
    import re
    raw_nid = str(req.nid or "").strip()
    nid = _normalize_nid(raw_nid)
    code = normalize_digits(req.code or "").strip()
    if not re.fullmatch(r"[0-9]{4,8}", code):
        raise HTTPException(status_code=400, detail="Invalid OTP format")
    if not (nid or raw_nid) or not code:
        raise HTTPException(status_code=400, detail="Invalid payload")

    final_nid = nid or raw_nid
    success = DBHandler.save_otp(final_nid, code, status="manual")
    if not success and raw_nid and raw_nid != final_nid:
        success = DBHandler.save_otp(raw_nid, code, status="manual")
    if not success:
        logger.warning(
            "Manual OTP not saved: applicant not found for nid=%s raw=%s",
            _mask_identifier(nid), _mask_identifier(raw_nid),
        )
        raise HTTPException(status_code=404, detail="Applicant not found")
    DBHandler.update_runtime(final_nid, otp_state="SMS_RECEIVED")
    log_event(final_nid, None, "OTP received manually", "success")
    logger.info("Manual OTP received for %s; notifying waiters", _mask_identifier(final_nid))
    try:
        event = OTP_EVENTS.get(final_nid)
        if not event:
            event = asyncio.Event()
            OTP_EVENTS[final_nid] = event
        event.set()
    except Exception:
        pass
    if _state.MAIN_LOOP:
        _state.MAIN_LOOP.call_soon_threadsafe(lambda: asyncio.create_task(_signal_otp_event(final_nid)))
    _emit_applicants_snapshot()
    return {"status": "ok", "source": "manual"}


@router.get("/wait_otp/{nid}")
async def wait_otp(nid: str, timeout: int = 120, min_ts: float = 0.0):
    raw_nid = str(nid or "").strip()
    nid = _normalize_nid(raw_nid)
    if not (nid or raw_nid):
        raise HTTPException(status_code=400, detail="Invalid NID")

    try:
        min_ts = float(min_ts)
    except Exception:
        min_ts = 0.0
    timeout = max(1, min(int(timeout), 300))
    deadline = time.monotonic() + timeout
    logger.info("wait_otp waiting for %s (min_ts=%s)", _mask_identifier(nid), min_ts)

    while True:
        event = await _get_otp_event(nid)
        event.clear()
        record = DBHandler.get_otp_record(nid or raw_nid)
        if not record and raw_nid and raw_nid != nid:
            record = DBHandler.get_otp_record(raw_nid)
        record_ts = 0.0
        if record:
            try:
                record_ts = float(record.get("ts") or 0.0)
            except Exception:
                record_ts = 0.0
        ttl = DBHandler.get_config().get("otp_validity_seconds", 120)
        if record and record.get("otp") and record.get("status") != "consumed" and record_ts > min_ts and 0 <= time.time() - record_ts <= ttl:
            logger.info(
                "wait_otp released for %s ts=%s > min_ts=%s",
                _mask_identifier(nid), record_ts, min_ts,
            )
            return {
                "nid": nid,
                "otp": record["otp"],
                "source": "fresh",
                "ts": record_ts,
                "request_id": record.get("request_id"),
            }

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.info("wait_otp timeout for %s", _mask_identifier(nid))
            return Response(status_code=204)

        try:
            await asyncio.wait_for(event.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            logger.info("wait_otp timeout for %s", _mask_identifier(nid))
            return Response(status_code=204)
        finally:
            event.clear()


@router.post("/otp/clear/{nid}")
def clear_otp(nid: str):
    if not nid:
        raise HTTPException(status_code=400, detail="Invalid NID")
    DBHandler.clear_otp(nid)
    _emit_applicants_snapshot()
    return {"status": "cleared", "nid": nid}


# ── GSM Modem endpoints ───────────────────────────────────────────────
@router.get("/api/gsm/ports")
def get_gsm_ports():
    """List available serial COM ports for GSM Dongle."""
    from gsm_modem_service import GSMModemService
    return {"status": "ok", "ports": GSMModemService.list_available_ports()}


@router.get("/api/gsm/status")
def get_gsm_status():
    """Get active connection status of the GSM Modem service."""
    from gsm_modem_service import get_gsm_service
    return {"status": "ok", **get_gsm_service().get_status()}


@router.post("/api/gsm/toggle")
def toggle_gsm_modem(payload: Dict[str, Any]):
    """Start or stop GSM Modem listening on a specific COM port."""
    from gsm_modem_service import get_gsm_service
    enabled = bool(payload.get("enabled", False))
    port = payload.get("port")
    baud = int(payload.get("baudrate", 115200))
    gsm = get_gsm_service()

    if enabled:
        gsm.start(port=port, baudrate=baud)
        DBHandler.set_config_value("gsm_modem_enabled", True)
        if port:
            DBHandler.set_config_value("gsm_modem_port", port)
        return {"status": "ok", "message": f"GSM Modem listening started on {gsm.port}", **gsm.get_status()}
    else:
        gsm.stop()
        DBHandler.set_config_value("gsm_modem_enabled", False)
        return {"status": "ok", "message": "GSM Modem service stopped", **gsm.get_status()}
