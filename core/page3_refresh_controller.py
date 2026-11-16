"""
core/page3_refresh_controller.py — Page3OfficialRefreshController

Orchestrates the authorized, official 5-stage refresh and re-entry lifecycle for Page 3 (SelectBnkShb.aspx).
Never relies on synthetic ddlCity pulses.
Strictly respects official workflow, OTP validity window (~120s empirical), and maintenance gates.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from core.page3_state_machine import ApplicantPage3StateMachine, Page3State
from core.province_bank_monitor import ProvinceBankMonitor
from core.session_diagnostics import Page3SessionDiagnostics, SessionStatus

logger = logging.getLogger("mahanbot.page3_refresh")


class Page3OfficialRefreshController:
    """Controls the official 5-stage reload and re-entry process for Page 3."""

    def __init__(
        self,
        bot: Any,
        page: Any,
        fsm: Optional[ApplicantPage3StateMachine] = None,
        bank_monitor: Optional[ProvinceBankMonitor] = None,
        max_reload_attempts: int = 3,
        otp_validity_window_sec: float = 115.0,
    ):
        self.bot = bot
        self.page = page
        self.applicant_nid = getattr(bot, "nid", "unknown")
        self.fsm = fsm or ApplicantPage3StateMachine(self.applicant_nid, Page3State.PAGE3_READY)
        self.bank_monitor = bank_monitor or ProvinceBankMonitor(getattr(bot, "state_name", "سراسری"))
        self.max_reload_attempts = max(1, int(max_reload_attempts))
        self.otp_validity_window_sec = min(115.0, float(otp_validity_window_sec))

        self._reload_lock = threading.Lock()
        self._reload_attempts = 0
        self._last_checkpoint: Dict[str, Any] = {}
        self._is_reloading = False

    # ── Stage 1: State Snapshot / Checkpoint ─────────────────────────────
    def create_checkpoint(self) -> Dict[str, Any]:
        """
        Stage 1: Capture operational state checkpoint without leaking secrets (no OTP, no ViewState).
        """
        self.fsm.transition(Page3State.REFRESH_REQUESTED, reason="manual_or_scheduled_refresh_requested")
        user_data = getattr(self.bot, "user_data", {}) or {}
        runtime_data = {}
        if hasattr(self.bot, "get_runtime_data"):
            try:
                runtime_data = self.bot.get_runtime_data() or {}
            except Exception:
                runtime_data = {}

        priorities = (
            runtime_data.get("priority_banks")
            or user_data.get("priority_banks")
            or user_data.get("banks")
            or []
        )

        province = getattr(self.bot, "state_name", "") or user_data.get("state") or ""
        city = getattr(self.bot, "city_name", "") or user_data.get("city") or ""

        # Extract current bank options from live page if available
        live_options: List[Dict[str, Any]] = []
        try:
            if self.page and not getattr(self.page, "is_closed", lambda: False)():
                live_options = self.page.evaluate("""() => {
                    const ddl = document.querySelector('#ctl00_ContentPlaceHolder1_ddlBankName');
                    if (!ddl) return [];
                    return Array.from(ddl.options || []).map(o => ({
                        value: (o.value || '').trim(),
                        text: (o.text || '').trim()
                    })).filter(o => o.value && o.value !== '0' && !o.text.includes('انتخاب'));
                }""") or []
        except Exception as exc:
            logger.warning(f"[{self.applicant_nid}] Could not read current live options for checkpoint: {exc}")

        checkpoint = {
            "applicant_nid": self.applicant_nid,
            "browser_stage": getattr(self.bot, "detect_actual_page_stage", lambda p: "STEP_3_BANK_SELECT")(self.page),
            "province": province,
            "city": city,
            "priority_banks": [p.get("name", str(p)) if isinstance(p, dict) else str(p) for p in priorities],
            "mode": user_data.get("bank_mode") or "in_person",
            "allow_reserve": bool(user_data.get("allow_reserve", False)),
            "is_already_confirmed": bool(runtime_data.get("tracking_code") or user_data.get("tracking_code")),
            "banks_snapshot": live_options,
            "checkpoint_time": time.time(),
        }

        self._last_checkpoint = checkpoint
        self.fsm.transition(Page3State.CHECKPOINT_SAVED, reason="checkpoint_captured_safely")
        return checkpoint

    # ── Stage 2: Official Page-3 reload (province quota refresh) ─────────
    def execute_controlled_reload(self, timeout_ms: int = 25000) -> Tuple[bool, str]:
        """
        Stage 2: Official page.reload / GET re-entry for province-level bank quotas.
        ddlCity changes do NOT refresh quotas. OTP/Step1 paths must not call this.
        """
        if not self._reload_lock.acquire(blocking=False):
            return False, "concurrent_reload_blocked"

        try:
            if self._reload_attempts >= self.max_reload_attempts:
                self.fsm.transition(Page3State.ERROR, reason="max_reload_attempts_exceeded")
                return False, f"حداکثر دفعات ریلود ({self.max_reload_attempts}) تکمیل شده است."

            if getattr(self.bot, "_submitting_final", False) is True:
                return False, "ثبت فرم در جریان است؛ ریلود مسدود شد."

            self._is_reloading = True
            self._reload_attempts += 1
            self.fsm.transition(Page3State.RELOADING, reason=f"attempt_{self._reload_attempts}_of_{self.max_reload_attempts}")

            from core.aspnet_postback import refresh_page3_banks_via_official_reload
            method = "GET"
            try:
                from core.loan_workflow import workflow_for
                method = getattr(workflow_for(self.bot), "last_navigation_method", "GET") or "GET"
            except Exception:
                method = "GET"
            logger.info(
                f"[{self.applicant_nid}] Official Page-3 reload for province quota "
                f"(Attempt {self._reload_attempts}; ddlCity ignored)..."
            )
            ok, reason = refresh_page3_banks_via_official_reload(
                self.page, bot=self.bot, timeout_ms=timeout_ms, last_navigation_method=method
            )
            if not ok:
                self.fsm.transition(Page3State.ERROR, reason="official_reload_failed", error=reason)
                return False, reason
            try:
                self.fsm.sync_from_live_dom(self.page, bot=self.bot)
            except Exception:
                pass
            return True, reason or "official_reload_dispatched"

        except Exception as exc:
            self.fsm.transition(Page3State.ERROR, reason="reload_exception", error=str(exc))
            return False, str(exc)
        finally:
            self._is_reloading = False
            self._reload_lock.release()

    # ── Stage 3: Destination Page Detection ──────────────────────────────
    def detect_destination_stage(self) -> Dict[str, Any]:
        """
        Stage 3: Inspect destination after soft postback using unified stage detector + diagnostics.
        """
        if not self.page or getattr(self.page, "is_closed", lambda: False)():
            self.fsm.transition(Page3State.ERROR, reason="browser_closed_after_reload")
            return {"destination": "CLOSED", "status": SessionStatus.SERVER_REJECTED.value}

        url = str(self.page.url or "").lower()
        html = ""
        try:
            html = self.page.content()
        except Exception:
            html = ""

        otp_time = getattr(self.bot, "_otp_entry_time", 0.0) or getattr(self.bot, "_last_otp_received_at", 0.0)
        diagnostics = Page3SessionDiagnostics.evaluate_session(
            current_url=url,
            dom_html=html,
            last_http_status=200,
            otp_entry_time=otp_time,
            max_otp_validity_sec=self.otp_validity_window_sec,
        )

        status = diagnostics.get("status")

        # Destination Routing
        if status == SessionStatus.MAINTENANCE_ACTIVE.value:
            self.fsm.transition(Page3State.MAINTENANCE_PAUSED, reason="maintenance_detected_post_reload")
            return {"destination": "MAINTENANCE", "diagnostics": diagnostics}

        if status == SessionStatus.OTP_EXPIRED.value:
            self.fsm.transition(Page3State.SESSION_EXPIRED, reason="otp_window_expired")
            return {"destination": "OTP_EXPIRED", "diagnostics": diagnostics}

        from core.stage_detector import detect_current_stage, PAGE_3_BANK_STAGE, SMS_OTP_STAGE, NATIONAL_ID_STAGE
        det = detect_current_stage(self.page, bot=self.bot)
        if det.stage == SMS_OTP_STAGE or "seltcase.aspx" in url or "ctl00_ContentPlaceHolder1_tbPass" in html:
            self.fsm.transition(Page3State.OFFICIAL_REENTRY_REQUIRED, reason="returned_to_otp_step")
            return {
                "destination": "OTP_FORM",
                "diagnostics": diagnostics,
                "can_reenter_with_cached_otp": (time.time() - otp_time) < self.otp_validity_window_sec if otp_time else False,
                "ssot_stage": det.stage,
            }

        if det.stage == PAGE_3_BANK_STAGE or "selectbnkshb.aspx" in url or "ctl00_ContentPlaceHolder1_ddlBankName" in html:
            self.fsm.transition(Page3State.PAGE3_READY, reason="retained_page3_after_official_reload")
            return {"destination": "PAGE3_ACTIVE", "diagnostics": diagnostics, "ssot_stage": det.stage}

        if det.stage == NATIONAL_ID_STAGE:
            self.fsm.transition(Page3State.SESSION_EXPIRED, reason="regressed_to_national_id")
            return {"destination": "ENTRY_FORM", "diagnostics": diagnostics, "ssot_stage": det.stage}

        # Any other unexpected redirect
        self.fsm.transition(Page3State.RECOVERY_REQUIRED, reason=f"unexpected_destination_{url}")
        return {"destination": "UNKNOWN", "diagnostics": diagnostics, "ssot_stage": det.stage}

    # ── Stage 4: Re-entry & Bank List Differential ───────────────────────
    def reenter_and_calculate_diff(self) -> Dict[str, Any]:
        """
        Stage 4: Read fresh bank options, compare against Stage 1 checkpoint,
        and update ProvinceBankMonitor.
        """
        self.fsm.transition(Page3State.BANK_LIST_RECEIVED, reason="fetching_live_options")

        fresh_options: List[Dict[str, Any]] = []
        try:
            fresh_options = self.page.evaluate("""() => {
                const ddl = document.querySelector('#ctl00_ContentPlaceHolder1_ddlBankName');
                if (!ddl) return [];
                return Array.from(ddl.options || []).map(o => ({
                    value: (o.value || '').trim(),
                    text: (o.text || '').trim()
                })).filter(o => o.value && o.value !== '0' && !o.text.includes('انتخاب'));
            }""") or []
        except Exception as exc:
            logger.warning(f"[{self.applicant_nid}] Error reading fresh options: {exc}")

        diff = self.bank_monitor.update_live_banks(fresh_options)
        self.fsm.transition(Page3State.BANK_LIST_COMPARED, reason=f"added={len(diff['added_banks'])}_removed={len(diff['removed_banks'])}")

        # Check if priority bank is now available
        priorities = self._last_checkpoint.get("priority_banks", [])
        matched_priority = None
        for p in priorities:
            classification = self.bank_monitor.classify_bank_discrepancy(p)
            if classification["status"] == "LIVE_AVAILABLE":
                matched_priority = classification
                if classification["has_active_quota"]:
                    self.fsm.transition(Page3State.BANK_AVAILABLE, reason=f"active_quota_{p}")
                elif classification["is_reserve"]:
                    self.fsm.transition(Page3State.BANK_RESERVATION_AVAILABLE, reason=f"reserve_quota_{p}")
                break

        return {
            "diff": diff,
            "matched_priority": matched_priority,
            "total_options": len(fresh_options),
        }

    # ── Stage 5: Safe Continuation & Loop Prevention ─────────────────────
    def can_continue_monitoring(self) -> Tuple[bool, str]:
        """
        Stage 5: Guard against endless reload loops.
        """
        if self._last_checkpoint.get("is_already_confirmed"):
            self.fsm.transition(Page3State.REGISTRATION_CONFIRMED, reason="already_has_tracking_code")
            return False, "متقاضی قبلاً کد رهگیری قطعی دریافت کرده است."

        if self._reload_attempts >= self.max_reload_attempts:
            return False, f"سقف مجاز ریلود ({self.max_reload_attempts}) مصرف شده است."

        if self.fsm.current_state in {Page3State.ERROR, Page3State.SESSION_EXPIRED, Page3State.MAINTENANCE_PAUSED}:
            return False, f"وضعیت فعلی اجازه ادامه نمی‌دهد: {self.fsm.current_state.value}"

        return True, "ready_for_next_action"

    def reset_reload_attempts(self) -> None:
        """Reset counter when bank options change or registration proceeds."""
        self._reload_attempts = 0
