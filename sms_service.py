"""
sms_service.py — Universal SMS / OTP Parser and Routing Service for MAHANBOT
Handles parsing raw SMS texts from Android Forwarders, GSM Modems, or Webhooks,
extracting 6-digit verification codes, and automatically matching them to active applicants.
"""
import re
import time
import logging
from typing import Dict, Any, Optional, Tuple, List

from database import DBHandler
from core.helpers import _normalize_nid

logger = logging.getLogger("mahanbot.sms")

# Central Bank / CBI SMS keywords
CBI_SMS_KEYWORDS = (
    "تسهیلات",
    "ازدواج",
    "فرزند",
    "فرزندآوری",
    "بانک مرکزی",
    "کد تایید",
    "کد تأیید",
    "کد پیامکی",
    "ve.cbi.ir",
    "cbi.ir",
)


def normalize_digits(text: str) -> str:
    """Normalize Persian and Arabic digits to English ASCII digits."""
    if not text:
        return ""
    persian_digits = "۰۱۲۳۴۵۶۷۸۹"
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    cleaned = str(text)
    for i, d in enumerate(persian_digits):
        cleaned = cleaned.replace(d, str(i))
    for i, d in enumerate(arabic_digits):
        cleaned = cleaned.replace(d, str(i))
    return cleaned


def extract_otp_code(text: str) -> Optional[str]:
    """Extract a 6-digit OTP verification code from text."""
    parsed = parse_sms_text(text)
    return parsed.get("code")


def extract_national_id(text: str) -> Optional[str]:
    """Extract a 10-digit National ID from text."""
    parsed = parse_sms_text(text)
    return parsed.get("nid")


def extract_mobile_number(text: str) -> Optional[str]:
    """Extract an Iranian mobile number from text."""
    parsed = parse_sms_text(text)
    return parsed.get("phone")


def parse_incoming_sms(text: str, from_number: Optional[str] = None) -> Dict[str, Any]:
    """Convenience alias for parsing full incoming SMS structure."""
    parsed = parse_sms_text(text)
    return {
        "otp_code": parsed.get("code"),
        "national_id": parsed.get("nid"),
        "mobile": parsed.get("phone") or from_number,
        "sender": from_number,
        "raw": parsed.get("raw"),
    }


def route_and_apply_sms(text: str, from_number: Optional[str] = None, explicit_nid: Optional[str] = None, explicit_code: Optional[str] = None) -> Dict[str, Any]:
    """Route and apply incoming SMS, returning a unified dictionary result."""
    ok, info = route_incoming_sms(text, from_number=from_number, explicit_nid=explicit_nid, explicit_code=explicit_code)
    return {
        "success": ok,
        "national_id": info.get("nid"),
        "code": info.get("code"),
        "matched_by": info.get("matched_by"),
        "error": info.get("error"),
        "info": info,
    }


def parse_sms_text(text: str) -> Dict[str, Optional[str]]:
    """
    Intelligently extract OTP code, National ID, and Phone Number from raw SMS text.
    Handles Persian and English digits, varying text patterns from ve.cbi.ir.
    """
    if not text:
        return {"code": None, "nid": None, "phone": None, "raw": ""}

    raw = str(text)

    # Normalize Persian / Arabic digits to English digits
    persian_digits = "۰۱۲۳۴۵۶۷۸۹"
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    cleaned = raw
    for i, d in enumerate(persian_digits):
        cleaned = cleaned.replace(d, str(i))
    for i, d in enumerate(arabic_digits):
        cleaned = cleaned.replace(d, str(i))

    # 1. Extract 6-digit OTP code
    # ve.cbi.ir SMS codes are strictly 6 digits
    code = None
    # Look for explicit code markers first
    code_match = re.search(r'(?:کد\s*(?:تایید|تأیید|پیامکی|احراز)?\s*[:\-]?\s*)(\d{6})\b', cleaned, re.IGNORECASE)
    if code_match:
        code = code_match.group(1)
    else:
        # Fallback: find any standalone 6-digit number
        # Exclude numbers that look like dates or part of 10-digit NIDs
        standalone_6 = re.findall(r'(?<!\d)(\d{6})(?!\d)', cleaned)
        if standalone_6:
            code = standalone_6[0]

    # 2. Extract 10-digit National ID if mentioned in the message
    nid = None
    nid_match = re.search(r'(?:کد\s*ملی|کدملی|شناسه\s*ملی)\s*[:\-]?\s*(\d{10})\b', cleaned, re.IGNORECASE)
    if nid_match:
        nid = _normalize_nid(nid_match.group(1))
    else:
        # Search for standalone 10-digit number
        standalone_10 = re.findall(r'(?<!\d)(\d{10})(?!\d)', cleaned)
        if standalone_10:
            for cand in standalone_10:
                norm = _normalize_nid(cand)
                # Verify if this NID exists in our database
                if DBHandler.get_applicant(norm):
                    nid = norm
                    break

    # 3. Extract Mobile Number if present
    phone = None
    phone_match = re.search(r'(?:09\d{9}|\+989\d{9}|989\d{9})', cleaned)
    if phone_match:
        raw_phone = phone_match.group(0)
        if raw_phone.startswith("+98"):
            raw_phone = "0" + raw_phone[3:]
        elif raw_phone.startswith("98"):
            raw_phone = "0" + raw_phone[2:]
        phone = raw_phone

    return {
        "code": code,
        "nid": nid,
        "phone": phone,
        "raw": raw,
    }


def find_active_otp_waiter() -> Optional[Dict[str, Any]]:
    """
    Find the applicant who is currently waiting on the OTP/SMS step.
    Checks applicants with status in ('Waiting SMS', 'منتظر پیامک', 'Running')
    and runtime state ('WAITING_USER_INPUT', 'WAITING_SMS').
    """
    applicants = DBHandler.get_all_applicants()
    waiting_candidates = []

    for app in applicants:
        st = str(app.get("status") or "").strip().lower()
        nid = str(app.get("national_id") or "").strip()
        runtime = DBHandler.get_runtime_state(nid) if hasattr(DBHandler, "get_runtime_state") else {}
        step = runtime.get("current_step") or ""

        # Candidates waiting on SMS
        if (
            "waiting sms" in st
            or "پیامک" in st
            or step in {"WAITING_SMS", "OTP_FORM", "STEP_2_OTP", "OTP_MANUAL_REQUIRED"}
            or (st == "running" and step == "WAITING_SMS")
        ):
            waiting_candidates.append({
                "nid": nid,
                "app": app,
                "updated_at": float(runtime.get("updated_at") or 0.0),
            })

    if not waiting_candidates:
        return None

    # Pick the most recently active waiting candidate
    waiting_candidates.sort(key=lambda x: x["updated_at"], reverse=True)
    return waiting_candidates[0]["app"]


def route_incoming_sms(
    text: str,
    from_number: Optional[str] = None,
    explicit_nid: Optional[str] = None,
    explicit_code: Optional[str] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Main entry point for incoming SMS.
    Parses message, determines recipient applicant, saves OTP, and notifies the bot.
    Returns: (success: bool, info: dict)
    """
    parsed = parse_sms_text(text)
    code = explicit_code or parsed.get("code")
    nid = explicit_nid or parsed.get("nid")
    phone = from_number or parsed.get("phone")

    if not code:
        logger.warning("No 6-digit verification code found in incoming SMS: %s", text[:80])
        return False, {"error": "no_code_found", "parsed": parsed}

    matched_by = "none"
    matched_app = None

    # 1. Match by explicit NID or NID parsed from text
    if nid:
        norm_nid = _normalize_nid(nid)
        matched_app = DBHandler.get_applicant(norm_nid)
        if matched_app:
            nid = norm_nid
            matched_by = "nid"

    # 2. Match by sender phone number in applicant records
    if not matched_app and phone:
        norm_phone = phone
        if norm_phone.startswith("+98"):
            norm_phone = "0" + norm_phone[3:]
        elif norm_phone.startswith("98"):
            norm_phone = "0" + norm_phone[2:]

        for app in DBHandler.get_all_applicants():
            app_data = app.get("data") or {}
            app_mobile = str(app_data.get("mobile") or app_data.get("phone") or "").strip()
            if app_mobile and (app_mobile == norm_phone or norm_phone in app_mobile or app_mobile in norm_phone):
                matched_app = app
                nid = app.get("national_id")
                matched_by = "phone"
                break

    # 3. Match active applicant currently waiting on SMS
    if not matched_app:
        active_waiter = find_active_otp_waiter()
        if active_waiter:
            matched_app = active_waiter
            nid = active_waiter.get("national_id")
            matched_by = "active_waiter"

    if not matched_app or not nid:
        logger.warning(
            "Could not match incoming SMS to any applicant (code=%s, phone=%s, parsed_nid=%s)",
            code, phone, parsed.get("nid")
        )
        return False, {
            "error": "applicant_not_matched",
            "code": code,
            "parsed": parsed,
            "hint": "No applicant is currently in OTP waiting status and phone/NID did not match.",
        }

    # Record the received OTP via the standard OTP receiver
    from routes.otp import _handle_receive_sms, _emit_applicants_snapshot
    success = _handle_receive_sms(nid, code, status_label="auto_received", ts=time.time())

    if success:
        _emit_applicants_snapshot()
        logger.info(
            "SMS OTP %s routed successfully to NID %s (matched_by=%s)",
            code, nid, matched_by
        )
        return True, {
            "nid": nid,
            "code": code,
            "matched_by": matched_by,
            "applicant_name": matched_app.get("full_name") or nid,
        }

    return False, {"error": "save_failed", "nid": nid, "code": code}
