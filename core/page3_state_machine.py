"""
core/page3_state_machine.py — Isolated, testable State Machine for Page 3 Lifecycle.

Each applicant maintains an independent state machine instance with:
- Strict entry and exit conditions
- Configurable timeouts per state
- Pause and resume capabilities
- Detailed audit logs (reason, error, duration)
- Isolation from other applicants' contexts
"""

import enum
import logging
import time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("mahanbot.page3_fsm")


class Page3State(str, enum.Enum):
    WAITING_FOR_AUTH = "WAITING_FOR_AUTH"
    OTP_REQUIRED = "OTP_REQUIRED"
    OTP_VERIFIED = "OTP_VERIFIED"
    PAGE3_LOADING = "PAGE3_LOADING"
    PAGE3_READY = "PAGE3_READY"
    REFRESH_REQUESTED = "REFRESH_REQUESTED"
    CHECKPOINT_SAVED = "CHECKPOINT_SAVED"
    RELOADING = "RELOADING"
    OFFICIAL_REENTRY_REQUIRED = "OFFICIAL_REENTRY_REQUIRED"
    BANK_LIST_RECEIVED = "BANK_LIST_RECEIVED"
    BANK_LIST_COMPARED = "BANK_LIST_COMPARED"
    BANK_AVAILABLE = "BANK_AVAILABLE"
    BANK_RESERVATION_AVAILABLE = "BANK_RESERVATION_AVAILABLE"
    NON_PRESENTIAL_CAPACITY_FULL = "NON_PRESENTIAL_CAPACITY_FULL"
    SUBMISSION_PENDING = "SUBMISSION_PENDING"
    RESERVATION_CONFIRMED = "RESERVATION_CONFIRMED"
    REGISTRATION_CONFIRMED = "REGISTRATION_CONFIRMED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    MAINTENANCE_PAUSED = "MAINTENANCE_PAUSED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    ERROR = "ERROR"


# Default timeouts in seconds per state
DEFAULT_STATE_TIMEOUTS: Dict[Page3State, float] = {
    Page3State.WAITING_FOR_AUTH: 30.0,
    Page3State.OTP_REQUIRED: 115.0,
    Page3State.OTP_VERIFIED: 15.0,
    Page3State.PAGE3_LOADING: 25.0,
    Page3State.PAGE3_READY: 180.0,
    Page3State.REFRESH_REQUESTED: 5.0,
    Page3State.CHECKPOINT_SAVED: 5.0,
    Page3State.RELOADING: 30.0,
    Page3State.OFFICIAL_REENTRY_REQUIRED: 60.0,
    Page3State.BANK_LIST_RECEIVED: 10.0,
    Page3State.BANK_LIST_COMPARED: 10.0,
    Page3State.BANK_AVAILABLE: 30.0,
    Page3State.BANK_RESERVATION_AVAILABLE: 30.0,
    Page3State.NON_PRESENTIAL_CAPACITY_FULL: 15.0,
    Page3State.SUBMISSION_PENDING: 25.0,
    Page3State.RESERVATION_CONFIRMED: 0.0,  # Terminal / indefinitely held
    Page3State.REGISTRATION_CONFIRMED: 0.0,  # Terminal
    Page3State.SESSION_EXPIRED: 10.0,
    Page3State.MAINTENANCE_PAUSED: 0.0,  # Paused until resumed
    Page3State.RECOVERY_REQUIRED: 30.0,
    Page3State.ERROR: 0.0,
}

# Permitted state transitions map for strict integrity
VALID_TRANSITIONS: Dict[Page3State, Set[Page3State]] = {
    Page3State.WAITING_FOR_AUTH: {
        Page3State.OTP_REQUIRED, Page3State.PAGE3_LOADING, Page3State.ERROR, Page3State.MAINTENANCE_PAUSED
    },
    Page3State.OTP_REQUIRED: {
        Page3State.OTP_VERIFIED, Page3State.SESSION_EXPIRED, Page3State.ERROR, Page3State.MAINTENANCE_PAUSED, Page3State.RECOVERY_REQUIRED
    },
    Page3State.OTP_VERIFIED: {
        Page3State.PAGE3_LOADING, Page3State.ERROR, Page3State.RECOVERY_REQUIRED
    },
    Page3State.PAGE3_LOADING: {
        Page3State.PAGE3_READY, Page3State.OFFICIAL_REENTRY_REQUIRED, Page3State.SESSION_EXPIRED, Page3State.ERROR, Page3State.MAINTENANCE_PAUSED
    },
    Page3State.PAGE3_READY: {
        Page3State.REFRESH_REQUESTED, Page3State.BANK_LIST_RECEIVED, Page3State.BANK_AVAILABLE,
        Page3State.BANK_RESERVATION_AVAILABLE, Page3State.NON_PRESENTIAL_CAPACITY_FULL,
        Page3State.OFFICIAL_REENTRY_REQUIRED, Page3State.SESSION_EXPIRED, Page3State.MAINTENANCE_PAUSED, Page3State.ERROR
    },
    Page3State.REFRESH_REQUESTED: {
        Page3State.CHECKPOINT_SAVED, Page3State.ERROR, Page3State.PAGE3_READY
    },
    Page3State.CHECKPOINT_SAVED: {
        Page3State.RELOADING, Page3State.BANK_LIST_RECEIVED, Page3State.PAGE3_READY, Page3State.PAGE3_LOADING,
        Page3State.REGISTRATION_CONFIRMED, Page3State.OFFICIAL_REENTRY_REQUIRED, Page3State.ERROR
    },
    Page3State.RELOADING: {
        Page3State.RELOADING, Page3State.PAGE3_LOADING, Page3State.PAGE3_READY, Page3State.OFFICIAL_REENTRY_REQUIRED,
        Page3State.SESSION_EXPIRED, Page3State.MAINTENANCE_PAUSED, Page3State.ERROR
    },
    Page3State.OFFICIAL_REENTRY_REQUIRED: {
        Page3State.OTP_REQUIRED, Page3State.OTP_VERIFIED, Page3State.PAGE3_LOADING,
        Page3State.SESSION_EXPIRED, Page3State.ERROR, Page3State.MAINTENANCE_PAUSED
    },
    Page3State.BANK_LIST_RECEIVED: {
        Page3State.BANK_LIST_COMPARED, Page3State.PAGE3_READY, Page3State.ERROR
    },
    Page3State.BANK_LIST_COMPARED: {
        Page3State.BANK_AVAILABLE, Page3State.BANK_RESERVATION_AVAILABLE,
        Page3State.NON_PRESENTIAL_CAPACITY_FULL, Page3State.PAGE3_READY,
        Page3State.REFRESH_REQUESTED, Page3State.ERROR
    },
    Page3State.BANK_AVAILABLE: {
        Page3State.SUBMISSION_PENDING, Page3State.NON_PRESENTIAL_CAPACITY_FULL,
        Page3State.PAGE3_READY, Page3State.ERROR
    },
    Page3State.BANK_RESERVATION_AVAILABLE: {
        Page3State.SUBMISSION_PENDING, Page3State.PAGE3_READY, Page3State.ERROR
    },
    Page3State.NON_PRESENTIAL_CAPACITY_FULL: {
        Page3State.BANK_AVAILABLE, Page3State.BANK_RESERVATION_AVAILABLE,
        Page3State.SUBMISSION_PENDING, Page3State.PAGE3_READY, Page3State.ERROR
    },
    Page3State.SUBMISSION_PENDING: {
        Page3State.REGISTRATION_CONFIRMED, Page3State.RESERVATION_CONFIRMED,
        Page3State.NON_PRESENTIAL_CAPACITY_FULL, Page3State.PAGE3_READY,
        Page3State.REFRESH_REQUESTED, Page3State.SESSION_EXPIRED,
        Page3State.ERROR, Page3State.RECOVERY_REQUIRED
    },
    Page3State.RESERVATION_CONFIRMED: {
        Page3State.SUBMISSION_PENDING, Page3State.REGISTRATION_CONFIRMED, Page3State.PAGE3_READY
    },
    Page3State.REGISTRATION_CONFIRMED: set(),  # Final success state
    Page3State.SESSION_EXPIRED: {
        Page3State.WAITING_FOR_AUTH, Page3State.OFFICIAL_REENTRY_REQUIRED, Page3State.ERROR
    },
    Page3State.MAINTENANCE_PAUSED: {
        Page3State.PAGE3_LOADING, Page3State.WAITING_FOR_AUTH, Page3State.PAGE3_READY, Page3State.ERROR
    },
    Page3State.RECOVERY_REQUIRED: {
        Page3State.PAGE3_LOADING, Page3State.OFFICIAL_REENTRY_REQUIRED, Page3State.SESSION_EXPIRED, Page3State.ERROR
    },
    Page3State.ERROR: {
        Page3State.WAITING_FOR_AUTH, Page3State.PAGE3_LOADING  # Explicit user recovery
    },
}


class ApplicantPage3StateMachine:
    """Manages the full lifecycle state machine for an applicant's Page 3."""

    def __init__(self, applicant_nid: str, initial_state: Page3State = Page3State.WAITING_FOR_AUTH):
        self.applicant_nid = str(applicant_nid).strip()
        self.current_state = initial_state
        self.state_entered_at = time.time()
        self.paused = False
        self.paused_state: Optional[Page3State] = None
        self.last_reason = "initialization"
        self.last_error: Optional[str] = None
        self.history: List[Dict[str, Any]] = [
            {
                "state": initial_state.value,
                "entered_at": self.state_entered_at,
                "reason": self.last_reason,
            }
        ]
        self.custom_timeouts: Dict[Page3State, float] = {}

    def set_custom_timeout(self, state: Page3State, timeout_sec: float) -> None:
        self.custom_timeouts[state] = max(0.0, float(timeout_sec))

    def get_timeout(self, state: Page3State) -> float:
        return self.custom_timeouts.get(state, DEFAULT_STATE_TIMEOUTS.get(state, 30.0))

    def is_timed_out(self) -> bool:
        if self.paused:
            return False
        timeout = self.get_timeout(self.current_state)
        if timeout <= 0.0:
            return False  # Infinite timeout
        return (time.time() - self.state_entered_at) > timeout

    def transition(self, next_state: Page3State, reason: str = "", error: Optional[str] = None) -> bool:
        """
        Transition to a new state if valid.
        Returns True if successful, False if transition was rejected.
        """
        if self.paused and next_state != Page3State.MAINTENANCE_PAUSED and next_state != Page3State.ERROR:
            logger.warning(f"[{self.applicant_nid}] Cannot transition while paused in {self.current_state}")
            return False

        allowed = VALID_TRANSITIONS.get(self.current_state, set())
        # Always allow transitioning to ERROR or MAINTENANCE_PAUSED
        if next_state not in allowed and next_state not in {Page3State.ERROR, Page3State.MAINTENANCE_PAUSED}:
            logger.warning(f"[{self.applicant_nid}] Invalid transition requested: {self.current_state} -> {next_state}")
            return False

        now = time.time()
        prev_state = self.current_state
        self.current_state = next_state
        self.state_entered_at = now
        self.last_reason = reason or "state_changed"
        self.last_error = error

        record = {
            "from": prev_state.value,
            "to": next_state.value,
            "at": now,
            "reason": self.last_reason,
        }
        if error:
            record["error"] = error
        self.history.append(record)
        # Cap history at 100 entries to prevent memory leak
        if len(self.history) > 100:
            self.history = self.history[-100:]

        logger.info(f"[{self.applicant_nid}] State changed: {prev_state.value} -> {next_state.value} ({self.last_reason})")
        return True

    def pause(self, reason: str = "user_or_system_pause") -> None:
        if not self.paused:
            self.paused = True
            self.paused_state = self.current_state
            self.transition(Page3State.MAINTENANCE_PAUSED, reason=reason)

    def resume(self, reason: str = "user_or_system_resume") -> bool:
        if self.paused:
            target = self.paused_state or Page3State.PAGE3_READY
            self.paused = False
            self.paused_state = None
            return self.transition(target, reason=reason)
        return False

    def sync_from_live_dom(self, page, bot=None) -> Optional[Page3State]:
        """Align Page3 FSM with unified detect_current_stage (SSOT) without inventing transitions."""
        from core.stage_detector import detect_current_stage, PAGE_3_BANK_STAGE, SMS_OTP_STAGE, COMPLETED, SESSION_EXPIRED, NATIONAL_ID_STAGE
        det = detect_current_stage(page, bot=bot)
        target_map = {
            PAGE_3_BANK_STAGE: Page3State.PAGE3_READY,
            SMS_OTP_STAGE: Page3State.OTP_REQUIRED,
            COMPLETED: Page3State.REGISTRATION_CONFIRMED,
            SESSION_EXPIRED: Page3State.SESSION_EXPIRED,
            NATIONAL_ID_STAGE: Page3State.WAITING_FOR_AUTH,
        }
        target = target_map.get(det.stage)
        if target is None or target == self.current_state:
            return self.current_state
        if self.transition(target, reason=f"ssot:{det.reason or det.stage}"):
            return self.current_state
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "applicant_nid": self.applicant_nid,
            "current_state": self.current_state.value,
            "entered_at": self.state_entered_at,
            "duration": round(time.time() - self.state_entered_at, 2),
            "is_timed_out": self.is_timed_out(),
            "paused": self.paused,
            "last_reason": self.last_reason,
            "last_error": self.last_error,
            "history_count": len(self.history),
        }
