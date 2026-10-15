"""
routes/otp.py — OTP receive, wait, manual entry, clear.
"""
import asyncio
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from database import DBHandler
from event_logger import log_event
import core.state as _state
from core.state import OTP_EVENTS, OTP_EVENT_LOCK
from core.deps import require_otp_secret
from event_logger import EVENT_BROADCASTER, build_event
from core.helpers import _normalize_nid, _mask_identifier, _serialize_applicants
from core.models import SMSRequest

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
    otp_ts = float(ts) if ts is not None else time.time()
    success = DBHandler.set_otp(nid, code, ts=otp_ts, status=status_label)
    if not success:
        return False
    log_event(nid, None, "OTP received", "success")
    logger.info("OTP received for %s; notifying waiters", _mask_identifier(nid))
    if _state.MAIN_LOOP:
        _state.MAIN_LOOP.call_soon_threadsafe(lambda: asyncio.create_task(_signal_otp_event(nid)))
    return True


# ── OTP endpoints ─────────────────────────────────────────────────────
@router.post("/receive_sms")
def receive_sms(req: SMSRequest, _: bool = Depends(require_otp_secret)):
    raw_nid = str(req.nid or "").strip()
    nid = _normalize_nid(raw_nid)
    code = str(req.code or "").strip()
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


@router.post("/manual_otp")
@router.post("/otp/manual")
def manual_otp(req: SMSRequest):
    raw_nid = str(req.nid or "").strip()
    nid = _normalize_nid(raw_nid)
    code = str(req.code or "").strip()
    if not (nid or raw_nid) or not code:
        raise HTTPException(status_code=400, detail="Invalid payload")

    success = DBHandler.save_otp(nid or raw_nid, code, status="received")
    if not success and raw_nid and raw_nid != nid:
        success = DBHandler.save_otp(raw_nid, code, status="received")
    if not success:
        logger.warning(
            "Manual OTP not saved: applicant not found for nid=%s raw=%s",
            _mask_identifier(nid), _mask_identifier(raw_nid),
        )
        raise HTTPException(status_code=404, detail="Applicant not found")
    final_nid = nid or raw_nid
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
        record = DBHandler.get_otp_record(nid or raw_nid)
        if not record and raw_nid and raw_nid != nid:
            record = DBHandler.get_otp_record(raw_nid)
        record_ts = 0.0
        if record:
            try:
                record_ts = float(record.get("ts") or 0.0)
            except Exception:
                record_ts = 0.0
        if record and record.get("otp") and record_ts > min_ts:
            logger.info(
                "wait_otp released for %s ts=%s > min_ts=%s",
                _mask_identifier(nid), record_ts, min_ts,
            )
            return {
                "nid": nid,
                "otp": record["otp"],
                "source": "fresh",
                "ts": record_ts or time.time(),
            }

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.info("wait_otp timeout for %s", _mask_identifier(nid))
            return Response(status_code=204)

        event = await _get_otp_event(nid)
        event.clear()
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
