"""
Unified live-DOM stage detector — Single Source of Truth for MAHANBOT FSM stages.

Canonical FSM: INIT -> NATIONAL_ID_STAGE -> SMS_OTP_STAGE -> PAGE_3_BANK_STAGE
               -> APPLICANT_INFO_STAGE -> COMPLETED (+ SESSION_EXPIRED / control states).

Legacy aliases (STEP_1_ENTRY_FORM, STEP_2_OTP, …) are preserved so existing callers
keep working while all detection routes through detect_current_stage().
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

# Canonical FSM names (.cursorrules)
INIT = "INIT"
NATIONAL_ID_STAGE = "NATIONAL_ID_STAGE"
SMS_OTP_STAGE = "SMS_OTP_STAGE"
PAGE_3_BANK_STAGE = "PAGE_3_BANK_STAGE"
APPLICANT_INFO_STAGE = "APPLICANT_INFO_STAGE"
COMPLETED = "COMPLETED"
SESSION_EXPIRED = "SESSION_EXPIRED"

# Control / diagnostic stages
UNKNOWN = "UNKNOWN"
BROWSER_CLOSED = "BROWSER_CLOSED"
OFFLINE_SNAPSHOT = "OFFLINE_SNAPSHOT"
WAF_BLOCKED = "WAF_BLOCKED"
FIREWALL_CHALLENGE = "FIREWALL_CHALLENGE"
FACILITY_SELECTION = "FACILITY_SELECTION"

# Legacy strings used across bot_core / loan_workflow / tests
CANONICAL_TO_LEGACY = {
    INIT: "UNKNOWN",
    NATIONAL_ID_STAGE: "STEP_1_ENTRY_FORM",
    SMS_OTP_STAGE: "STEP_2_OTP",
    PAGE_3_BANK_STAGE: "STEP_3_BANK_SELECT",
    APPLICANT_INFO_STAGE: "REGISTER_STEP_3",
    COMPLETED: "DONE",
    SESSION_EXPIRED: "SESSION_EXPIRED",
    UNKNOWN: "UNKNOWN",
    BROWSER_CLOSED: "BROWSER_CLOSED",
    OFFLINE_SNAPSHOT: "OFFLINE_SNAPSHOT",
    WAF_BLOCKED: "WAF_BLOCKED",
    FIREWALL_CHALLENGE: "FIREWALL_CHALLENGE",
    FACILITY_SELECTION: "FACILITY_SELECTION",
}

LEGACY_TO_CANONICAL = {v: k for k, v in CANONICAL_TO_LEGACY.items()}
LEGACY_TO_CANONICAL["STEP_1_ENTRY_FORM"] = NATIONAL_ID_STAGE
LEGACY_TO_CANONICAL["STEP_2_OTP"] = SMS_OTP_STAGE
LEGACY_TO_CANONICAL["STEP_3_BANK_SELECT"] = PAGE_3_BANK_STAGE
LEGACY_TO_CANONICAL["REGISTER_STEP_3"] = APPLICANT_INFO_STAGE
LEGACY_TO_CANONICAL["DONE"] = COMPLETED

# Page3 FSM mapping
CANONICAL_TO_PAGE3 = {
    NATIONAL_ID_STAGE: "WAITING_FOR_AUTH",
    SMS_OTP_STAGE: "OTP_REQUIRED",
    PAGE_3_BANK_STAGE: "PAGE3_READY",
    COMPLETED: "REGISTRATION_CONFIRMED",
    SESSION_EXPIRED: "SESSION_EXPIRED",
    UNKNOWN: "RECOVERY_REQUIRED",
}

# UnifiedBankEngine coarse mapping
CANONICAL_TO_ENGINE = {
    NATIONAL_ID_STAGE: "STEP1_FORM",
    SMS_OTP_STAGE: "STEP2_OTP_DETECTED",
    PAGE_3_BANK_STAGE: "PAGE3_DETECTED",
    COMPLETED: "CONFIRMED",
    SESSION_EXPIRED: "OTP_EXPIRED_RESTART",
}


@dataclass(frozen=True)
class StageDetection:
    stage: str
    legacy: str
    confidence: float = 0.0
    reason: str = ""

    @property
    def page3_hint(self) -> str:
        return CANONICAL_TO_PAGE3.get(self.stage, "RECOVERY_REQUIRED")

    @property
    def engine_hint(self) -> str:
        return CANONICAL_TO_ENGINE.get(self.stage, "ERROR")


def to_legacy(stage: str) -> str:
    if stage in CANONICAL_TO_LEGACY:
        return CANONICAL_TO_LEGACY[stage]
    if stage in LEGACY_TO_CANONICAL or stage in CANONICAL_TO_LEGACY.values():
        return stage
    return stage


def to_canonical(stage: str) -> str:
    if stage in CANONICAL_TO_LEGACY:
        return stage
    return LEGACY_TO_CANONICAL.get(stage, UNKNOWN)


def _visible(page, selector: str) -> bool:
    try:
        loc = page.locator(selector)
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def _session_expired_in_dom(page) -> bool:
    try:
        error = page.locator(
            '[role="alert"]:visible, [id$="labErrMsg"]:visible, [id$="lblError"]:visible, .ValidationSummary:visible'
        )
        words = " ".join(error.all_text_contents() or [])
        words = words.replace("ي", "ی").replace("ك", "ک").lower()
        return any(term in words for term in ("نشست منقضی", "session expired", "جلسه منقضی"))
    except Exception:
        return False


def detect_current_stage(page: Any, bot: Any = None) -> StageDetection:
    """
    Deterministic stage detection from live DOM markers.
    Prefer DOM controls over URL (ASP.NET keeps the same URL across wizard steps).
    """
    if page is None:
        return StageDetection(INIT, to_legacy(INIT), 0.0, "page_is_none")

    try:
        scheme = urlparse(str(getattr(page, "url", "") or "")).scheme
        if scheme in {"file", "data"}:
            return StageDetection(OFFLINE_SNAPSHOT, to_legacy(OFFLINE_SNAPSHOT), 1.0, "offline_uri")
    except Exception:
        pass

    try:
        if page.is_closed():
            return StageDetection(BROWSER_CLOSED, to_legacy(BROWSER_CLOSED), 1.0, "target_closed")
    except Exception:
        return StageDetection(BROWSER_CLOSED, to_legacy(BROWSER_CLOSED), 1.0, "is_closed_raised")

    # Optional WAF / firewall hooks from bot
    try:
        from network_diagnostics import check_rejection, SiteRejectedError
        check_rejection(page)
    except Exception as exc:
        try:
            from network_diagnostics import SiteRejectedError as _SRE
            if isinstance(exc, _SRE):
                return StageDetection(WAF_BLOCKED, to_legacy(WAF_BLOCKED), 0.99, "site_rejected")
        except Exception:
            if exc.__class__.__name__ == "SiteRejectedError":
                return StageDetection(WAF_BLOCKED, to_legacy(WAF_BLOCKED), 0.99, "site_rejected")
    if bot is not None:
        try:
            if hasattr(bot, "is_firewall_challenge") and bot.is_firewall_challenge(page):
                return StageDetection(FIREWALL_CHALLENGE, to_legacy(FIREWALL_CHALLENGE), 0.99, "firewall")
        except Exception:
            pass

    if _session_expired_in_dom(page):
        return StageDetection(SESSION_EXPIRED, to_legacy(SESSION_EXPIRED), 0.95, "session_expired_alert")

    # COMPLETED — tracking code
    for sel in ("#ctl00_ContentPlaceHolder1_lblTrackingCode", "span[id*='TrackingCode']"):
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                txt = (loc.first.inner_text() or "").strip()
                if txt:
                    return StageDetection(COMPLETED, to_legacy(COMPLETED), 0.99, f"tracking:{sel}")
        except Exception:
            continue

    # SMS OTP — priority over bank on shared wizard pages
    try:
        from otp_form_detector import detect_otp_form
        otp_form = detect_otp_form(page)
        if otp_form.resolve("otp") is not None:
            return StageDetection(SMS_OTP_STAGE, to_legacy(SMS_OTP_STAGE), 0.98, otp_form.reason or "otp_form")
        if str(getattr(otp_form, "reason", "")).startswith("ambiguous"):
            return StageDetection(UNKNOWN, to_legacy(UNKNOWN), 0.0, "ambiguous_otp_fields")
        inspected = bool(getattr(otp_form, "inspected", False)) or otp_form.reason == "postback_pending"
    except Exception:
        inspected = False

    if not inspected:
        for sel in (
            "#ctl00_ContentPlaceHolder1_tbMobileConfCode",
            "input[name$='tbMobileConfCode']",
            "#ctl00_ContentPlaceHolder1_tbMobConfCode",
        ):
            if _visible(page, sel):
                return StageDetection(SMS_OTP_STAGE, to_legacy(SMS_OTP_STAGE), 0.96, f"otp_sel:{sel}")

    # PAGE 3 — bank dropdown
    for sel in ("#ctl00_ContentPlaceHolder1_ddlBankName", "select[name$='ddlBankName']"):
        if _visible(page, sel):
            return StageDetection(PAGE_3_BANK_STAGE, to_legacy(PAGE_3_BANK_STAGE), 0.98, f"bank_sel:{sel}")

    # Facility selection
    for sel in (
        "#ctl00_ContentPlaceHolder1_rbtnNaghdi",
        "#ctl00_ContentPlaceHolder1_rbtnKala",
        "input[name$='rbtnNaghdi']",
        "input[name$='rbtnKala']",
    ):
        if _visible(page, sel):
            return StageDetection(FACILITY_SELECTION, to_legacy(FACILITY_SELECTION), 0.95, f"facility:{sel}")

    # National ID / Step 1
    for sel in ("#ctl00_ContentPlaceHolder1_tbIDNo", "input[name$='tbIDNo']"):
        if _visible(page, sel):
            return StageDetection(NATIONAL_ID_STAGE, to_legacy(NATIONAL_ID_STAGE), 0.97, f"nid_sel:{sel}")

    # Applicant info / register continue
    if _visible(page, "#ctl00_ContentPlaceHolder1_btnContinue2"):
        return StageDetection(APPLICANT_INFO_STAGE, to_legacy(APPLICANT_INFO_STAGE), 0.9, "btnContinue2")

    return StageDetection(UNKNOWN, to_legacy(UNKNOWN), 0.0, "no_dom_markers")


def detect_current_stage_legacy(page: Any, bot: Any = None) -> str:
    """Drop-in replacement for BotCore.detect_actual_page_stage return values."""
    return detect_current_stage(page, bot=bot).legacy
