# core/state_machine.py
"""
core/state_machine.py — Bot State Machine & State History for MAHANBOT

Defines the official finite state machine (FSM) states and handles strict,
audited transitions for each applicant.
"""
import enum
import logging
import time
from typing import Optional, Dict, Any, Set

from database import DBHandler
from event_logger import EVENT_BROADCASTER, build_event

logger = logging.getLogger("mahanbot.fsm")


class BotState(str, enum.Enum):
    QUEUED = "QUEUED"                            # در صف
    PREPARING = "PREPARING"                      # آماده‌سازی
    STARTING = "STARTING"                        # آغاز فرایند
    REQUESTING_OTP = "REQUESTING_OTP"            # درخواست کد پیامک
    WAITING_OTP = "WAITING_OTP"                  # انتظار دریافت پیامک
    OTP_RECEIVED = "OTP_RECEIVED"                # دریافت کد
    VERIFYING_OTP = "VERIFYING_OTP"              # اعتبارسنجی کد
    AUTHENTICATED = "AUTHENTICATED"              # ورود موفق
    STEP_3_READY = "STEP_3_READY"                # صفحه سوم آماده
    SELECTING_BANK = "SELECTING_BANK"            # انتخاب بانک
    CAPACITY_FULL = "CAPACITY_FULL"              # ظرفیت تکمیل
    WAITING_AVAILABILITY = "WAITING_AVAILABILITY"  # انتظار بررسی مجاز ظرفیت
    READY_TO_SUBMIT = "READY_TO_SUBMIT"          # آماده ثبت نهایی
    SUBMITTING = "SUBMITTING"                    # ثبت درخواست
    CONFIRMED = "CONFIRMED"                      # ثبت موفق تأییدشده
    SESSION_EXPIRED = "SESSION_EXPIRED"          # انقضای نشست
    PAUSED = "PAUSED"                            # توقف موقت
    BLOCKED = "BLOCKED"                          # نیازمند اقدام کاربر
    FAILED = "FAILED"                            # خطای غیرقابل‌بازیابی


STATE_DESCRIPTIONS_FA = {
    BotState.QUEUED.value: "در صف انتظار",
    BotState.PREPARING.value: "آماده‌سازی مقدمات",
    BotState.STARTING.value: "آغاز فرایند",
    BotState.REQUESTING_OTP.value: "درخواست ارسال پیامک",
    BotState.WAITING_OTP.value: "منتظر دریافت پیامک",
    BotState.OTP_RECEIVED.value: "کد پیامک دریافت شد",
    BotState.VERIFYING_OTP.value: "در حال اعتبارسنجی کد",
    BotState.AUTHENTICATED.value: "ورود موفق به سامانه",
    BotState.STEP_3_READY.value: "صفحه سوم آماده",
    BotState.SELECTING_BANK.value: "در حال انتخاب بانک",
    BotState.CAPACITY_FULL.value: "ظرفیت بانک تکمیل",
    BotState.WAITING_AVAILABILITY.value: "منتظر باز شدن ظرفیت",
    BotState.READY_TO_SUBMIT.value: "آماده ثبت نهایی",
    BotState.SUBMITTING.value: "در حال ثبت نهایی",
    BotState.CONFIRMED.value: "ثبت نهایی قطعی و موفق",
    BotState.SESSION_EXPIRED.value: "نشست منقضی شده",
    BotState.PAUSED.value: "توقف موقت",
    BotState.BLOCKED.value: "نیازمند اقدام کاربر",
    BotState.FAILED.value: "خطا در پردازش",
}


# Map FSM BotState to general QueueStatus
STATE_TO_QUEUE_STATUS = {
    BotState.QUEUED.value: "WAITING",
    BotState.PREPARING.value: "ASSIGNED",
    BotState.STARTING.value: "RUNNING",
    BotState.REQUESTING_OTP.value: "RUNNING",
    BotState.WAITING_OTP.value: "WAITING_SMS",
    BotState.OTP_RECEIVED.value: "RUNNING",
    BotState.VERIFYING_OTP.value: "RUNNING",
    BotState.AUTHENTICATED.value: "RUNNING",
    BotState.STEP_3_READY.value: "RUNNING",
    BotState.SELECTING_BANK.value: "RUNNING",
    BotState.CAPACITY_FULL.value: "WAITING_BANK",
    BotState.WAITING_AVAILABILITY.value: "WAITING_BANK",
    BotState.READY_TO_SUBMIT.value: "RUNNING",
    BotState.SUBMITTING.value: "RUNNING",
    BotState.CONFIRMED.value: "COMPLETED",
    BotState.SESSION_EXPIRED.value: "WAITING_USER",
    BotState.PAUSED.value: "PAUSED",
    BotState.BLOCKED.value: "WAITING_USER",
    BotState.FAILED.value: "FAILED",
}

# Strict transition rules matrix
ALLOWED_TRANSITIONS: Dict[BotState, Set[BotState]] = {
    BotState.QUEUED: {BotState.PREPARING, BotState.STARTING, BotState.PAUSED, BotState.FAILED},
    BotState.PREPARING: {BotState.STARTING, BotState.QUEUED, BotState.PAUSED, BotState.FAILED},
    BotState.STARTING: {BotState.REQUESTING_OTP, BotState.AUTHENTICATED, BotState.PAUSED, BotState.FAILED},
    BotState.REQUESTING_OTP: {BotState.WAITING_OTP, BotState.FAILED, BotState.PAUSED, BotState.STARTING},
    BotState.WAITING_OTP: {BotState.OTP_RECEIVED, BotState.REQUESTING_OTP, BotState.BLOCKED, BotState.PAUSED, BotState.FAILED},
    BotState.OTP_RECEIVED: {BotState.VERIFYING_OTP, BotState.WAITING_OTP, BotState.FAILED},
    BotState.VERIFYING_OTP: {BotState.AUTHENTICATED, BotState.WAITING_OTP, BotState.REQUESTING_OTP, BotState.BLOCKED, BotState.FAILED},
    BotState.AUTHENTICATED: {BotState.STEP_3_READY, BotState.SELECTING_BANK, BotState.SESSION_EXPIRED, BotState.PAUSED, BotState.FAILED},
    BotState.STEP_3_READY: {BotState.SELECTING_BANK, BotState.CAPACITY_FULL, BotState.SESSION_EXPIRED, BotState.PAUSED, BotState.FAILED},
    BotState.SELECTING_BANK: {BotState.CAPACITY_FULL, BotState.WAITING_AVAILABILITY, BotState.READY_TO_SUBMIT, BotState.SESSION_EXPIRED, BotState.PAUSED, BotState.FAILED},
    BotState.CAPACITY_FULL: {BotState.WAITING_AVAILABILITY, BotState.SELECTING_BANK, BotState.PAUSED, BotState.FAILED},
    BotState.WAITING_AVAILABILITY: {BotState.SELECTING_BANK, BotState.CAPACITY_FULL, BotState.SESSION_EXPIRED, BotState.PAUSED, BotState.FAILED},
    BotState.READY_TO_SUBMIT: {BotState.SUBMITTING, BotState.CAPACITY_FULL, BotState.SESSION_EXPIRED, BotState.PAUSED, BotState.FAILED},
    BotState.SUBMITTING: {BotState.CONFIRMED, BotState.CAPACITY_FULL, BotState.SESSION_EXPIRED, BotState.FAILED},
    BotState.CONFIRMED: set(),  # Terminal state
    BotState.SESSION_EXPIRED: {BotState.STARTING, BotState.REQUESTING_OTP, BotState.BLOCKED, BotState.PAUSED, BotState.FAILED},
    BotState.PAUSED: {BotState.QUEUED, BotState.STARTING, BotState.SELECTING_BANK, BotState.FAILED},
    BotState.BLOCKED: {BotState.QUEUED, BotState.STARTING, BotState.REQUESTING_OTP, BotState.FAILED},
    BotState.FAILED: {BotState.QUEUED, BotState.PREPARING, BotState.STARTING},
}


def is_valid_transition(from_state: Any, to_state: Any) -> bool:
    """Validate whether transitioning from from_state to to_state is allowed by FSM."""
    try:
        f = from_state if isinstance(from_state, BotState) else BotState(str(getattr(from_state, 'value', from_state)).replace("BotState.", "").upper())
        t = to_state if isinstance(to_state, BotState) else BotState(str(getattr(to_state, 'value', to_state)).replace("BotState.", "").upper())
    except (ValueError, KeyError):
        return False
    if f == t:
        return True
    return t in ALLOWED_TRANSITIONS.get(f, set())


def get_current_state(nid: str) -> BotState:
    """Retrieve the current FSM state for an applicant."""
    clean_nid = str(nid).strip()
    runtime = DBHandler.get_runtime(clean_nid) or {}
    st_val = runtime.get("fsm_state") or runtime.get("current_step")
    if not st_val:
        try:
            with DBHandler._connect() as conn:
                row = conn.execute("SELECT bot_state FROM applicant_queue WHERE applicant_id = ?", (clean_nid,)).fetchone()
                if row and row[0]:
                    st_val = row[0]
        except Exception:
            pass
    try:
        s = str(getattr(st_val, 'value', st_val)).replace("BotState.", "").upper()
        return BotState(s)
    except Exception:
        return BotState.QUEUED


def record_state_transition(
    nid: str,
    from_state: str,
    to_state: str,
    reason: str = "",
    timestamp: Optional[float] = None,
) -> None:
    """Record an audited state transition in database and log history."""
    ts = timestamp or time.time()
    try:
        with DBHandler._connect() as conn:
            conn.execute(
                """INSERT INTO applicant_state_history
                   (applicant_id, from_state, to_state, reason, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (str(nid), str(from_state), str(to_state), str(reason), ts),
            )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to record state transition for %s: %s", nid, e)


def transition_state(
    nid: str,
    new_state: Any,
    detail: str = "",
    reason: str = "",
    extra_data: Optional[Dict[str, Any]] = None,
    force: bool = False,
) -> bool:
    """
    Safely transition an applicant to a new BotState.
    Validates state transitions, updates DB queue row and runtime, emits WebSocket event,
    and records audit trail. Returns True if transition succeeded, False if illegal.
    """
    clean_nid = str(nid).strip()
    try:
        if isinstance(new_state, BotState):
            target_state = new_state
        else:
            s = str(getattr(new_state, 'value', new_state)).replace("BotState.", "").upper()
            target_state = BotState(s)
    except Exception:
        logger.warning("Unknown state target: %s for %s", new_state, clean_nid)
        return False

    norm_new_state = target_state.value
    actual_reason = detail or reason

    prev_state = get_current_state(clean_nid)

    # Validate transition
    if not force and not is_valid_transition(prev_state, target_state):
        logger.warning(
            "Illegal FSM transition for %s: %s -> %s rejected",
            clean_nid, prev_state.value, norm_new_state
        )
        return False

    if prev_state == target_state and actual_reason == "":
        return True

    now = time.time()
    queue_status = STATE_TO_QUEUE_STATUS.get(norm_new_state, "RUNNING")

    # 1. Audit trail
    record_state_transition(clean_nid, prev_state.value, norm_new_state, actual_reason, now)

    # 2. Update runtime state
    runtime_updates = {
        "fsm_state": norm_new_state,
        "current_step": norm_new_state,
        "last_state_reason": actual_reason,
        "last_activity_at": now,
    }
    if extra_data:
        runtime_updates.update(extra_data)
    DBHandler.update_runtime(clean_nid, **runtime_updates)

    # 3. Update queue row if present
    try:
        with DBHandler._connect() as conn:
            conn.execute(
                """UPDATE applicant_queue
                   SET bot_state = ?, queue_status = ?, current_stage = ?,
                       last_activity_at = ?, last_error = CASE WHEN ? = 'FAILED' THEN ? ELSE last_error END,
                       updated_at = ?
                   WHERE applicant_id = ?""",
                (norm_new_state, queue_status, norm_new_state, now, norm_new_state, actual_reason, now, clean_nid),
            )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to update applicant_queue state for %s: %s", clean_nid, e)

    # 4. Emit live event
    try:
        desc = STATE_DESCRIPTIONS_FA.get(norm_new_state, norm_new_state)
        EVENT_BROADCASTER.emit_event(
            build_event(
                "fsm_state_changed",
                nid=clean_nid,
                prev_state=prev_state.value,
                new_state=norm_new_state,
                queue_status=queue_status,
                description=desc,
                reason=actual_reason,
                ts=now,
            )
        )
    except Exception:
        pass

    logger.info("FSM transition [%s]: %s -> %s (reason: %s)", clean_nid, prev_state.value, norm_new_state, actual_reason)
    return True
