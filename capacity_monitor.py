"""
capacity_monitor.py — Independent Step 3 Capacity Monitoring & Sniping Engine.

Manages continuous, non-destructive monitoring of Page 3 (SelectBnkShb.aspx)
for multiple applicants concurrently.
Never reloads or navigates back to Step 1 on capacity full or temporary DOM changes.
"""
import hashlib
import json
import logging
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from bank_page_manager import BankPageState as State, analyze_bank_page_html, capture_dom_html
from database import DBHandler, normalize_config

logger = logging.getLogger("mahanbot.capacity_monitor")


# ── Canonical States requested in Specification ──────────────────────
class CapacityState:
    LOGIN = "LOGIN"
    OTP_REQUIRED = "OTP_REQUIRED"
    PAGE_3_READY = "PAGE_3_READY"
    BANK_SELECTED = "BANK_SELECTED"
    NON_PRESENTIAL_SELECTED = "NON_PRESENTIAL_SELECTED"
    WAITING_CAPACITY = "WAITING_CAPACITY"
    SUBMITTING = "SUBMITTING"
    SUCCESS = "SUCCESS"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    ERROR = "ERROR"
    PAUSED = "PAUSED"


# Map internal controller / manager states to canonical CapacityStates
STATE_MAPPING = {
    State.BANK_PAGE_DETECTED: CapacityState.PAGE_3_READY,
    State.BANK_PAGE_READY: CapacityState.PAGE_3_READY,
    State.BANK_OPTIONS_LOADING: CapacityState.PAGE_3_READY,
    State.BANK_OPTIONS_READY: CapacityState.PAGE_3_READY,
    State.BANK_SELECTED: CapacityState.BANK_SELECTED,
    State.NON_PRESENTIAL_SELECTED: CapacityState.NON_PRESENTIAL_SELECTED,
    State.CAPACITY_FULL: CapacityState.WAITING_CAPACITY,
    State.FINAL_SUBMIT_READY: CapacityState.WAITING_CAPACITY,
    State.FINAL_SUBMIT_IN_PROGRESS: CapacityState.SUBMITTING,
    State.REGISTRATION_CONFIRMED: CapacityState.SUCCESS,
    State.SESSION_EXPIRED: CapacityState.SESSION_EXPIRED,
    State.MANUAL_ACTION_REQUIRED: CapacityState.ERROR,
    State.PAUSED: CapacityState.PAUSED,
    State.RESERVE_PENDING: "RESERVE_PENDING",
}


class CapacityMonitor:
    """
    Independent capacity monitor instance per applicant worker.
    Runs on the worker thread owning the Playwright page.
    """

    def __init__(self, bot, owner, interval_seconds: float = 5.0):
        self.bot = bot
        self.owner = owner
        self.nid = str(bot.nid)
        self.worker_id = f"worker-{self.nid}-{getattr(owner, 'session_id', 'main')[:8]}"
        
        # Timing & Intervals
        self.base_interval = max(2.0, float(interval_seconds))
        self.current_interval = self.base_interval
        self.backoff_factor = 1.0
        self.max_backoff = 30.0
        
        # State tracking
        self.state = CapacityState.PAGE_3_READY
        self.last_checked_at: Optional[float] = None
        self.waiting_since: Optional[float] = None
        self.checks_count = 0
        self.last_system_message = ""
        self.selected_bank = ""
        self.selected_mode = "online"
        self.city = ""
        self.tracking_code = ""
        self.submission_in_flight = False
        
        # Calculate applicant-specific deterministic jitter (stagger requests)
        nid_hash = int(hashlib.md5(self.nid.encode()).hexdigest()[:6], 16)
        self.jitter_offset = (nid_hash % 1000) / 1000.0 * 1.5  # 0.0 to 1.5s offset
        self.next_check_time = time.monotonic() + self.jitter_offset
        
        # Register in global monitor registry
        CAPACITY_MONITOR_REGISTRY.register(self)

    def calculate_next_interval(self) -> float:
        """Calculate interval with jitter and backoff."""
        jitter = random.uniform(-0.3, 0.3)
        effective = min(self.max_backoff, self.current_interval * self.backoff_factor)
        return max(2.0, effective + jitter)

    def record_check_attempt(self, result_kind: str, message: str = ""):
        self.checks_count += 1
        self.last_checked_at = time.time()
        self.last_system_message = message

        if result_kind == "capacity_full":
            if self.waiting_since is None:
                self.waiting_since = time.time()
            self.state = CapacityState.WAITING_CAPACITY
            self.backoff_factor = 1.0  # Reset backoff on normal capacity response
        elif result_kind in {"access_denied", "waf_blocked", "rate_limit"}:
            self.state = CapacityState.ERROR
            self.backoff_factor = min(self.max_backoff / self.base_interval, self.backoff_factor * 1.5)
            self.bot.log(f"⚠️ اعمال فاصله اطمینان به دلیل محدودیت ترافیک ({self.current_interval * self.backoff_factor:.1f}s)...", "warning")
        elif result_kind == "session_expired":
            self.state = CapacityState.SESSION_EXPIRED
        elif result_kind == "success":
            self.state = CapacityState.SUCCESS
            self.waiting_since = None

        # Sync runtime payload into DB for Dashboard consumption
        self.sync_runtime()

    def sync_runtime(self):
        """Update DB automation_runtime with latest capacity monitoring metrics."""
        now = time.time()
        waiting_duration = round(now - self.waiting_since, 1) if self.waiting_since else 0.0
        DBHandler.update_runtime(
            self.nid,
            capacity_monitor_state=self.state,
            capacity_checks_count=self.checks_count,
            capacity_waiting_since=self.waiting_since,
            capacity_waiting_duration=waiting_duration,
            capacity_last_checked=self.last_checked_at,
            capacity_last_message=self.last_system_message,
            capacity_interval=round(self.current_interval * self.backoff_factor, 1),
            capacity_worker_id=self.worker_id,
            target_bank=self.selected_bank,
            selected_mode=self.selected_mode,
            city=self.city,
            tracking_code=self.tracking_code,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Snapshot dictionary for API and UI consumers."""
        now = time.time()
        waiting_duration = round(now - self.waiting_since, 1) if self.waiting_since else 0.0
        app = DBHandler.get_applicant(self.nid) or {}
        app_data = {}
        try:
            raw_d = app.get("data")
            app_data = json.loads(raw_d) if isinstance(raw_d, str) else (raw_d or {})
        except Exception:
            pass

        return {
            "nid": self.nid,
            "name": app.get("full_name") or f"متقاضی {self.nid[-4:]}",
            "worker_id": self.worker_id,
            "city": self.city or app_data.get("city", "-"),
            "state_province": app_data.get("state", "-"),
            "loan_type": app_data.get("loan_type", "marriage"),
            "selected_bank": self.selected_bank or app_data.get("primary_bank", "-"),
            "selected_mode": self.selected_mode,
            "state": self.state,
            "checks_count": self.checks_count,
            "last_checked_at": self.last_checked_at,
            "waiting_since": self.waiting_since,
            "waiting_duration_seconds": waiting_duration,
            "last_system_message": self.last_system_message,
            "interval_seconds": round(self.current_interval * self.backoff_factor, 1),
            "session_active": bool(self.owner.alive and self.owner.bank_page_active),
            "paused": bool(self.owner.paused),
            "tracking_code": self.tracking_code or DBHandler.has_final_submission(self.nid) or "",
        }

    def cleanup(self):
        CAPACITY_MONITOR_REGISTRY.unregister(self.nid)


class CapacityMonitorRegistry:
    """Thread-safe global registry tracking all active CapacityMonitor instances."""

    def __init__(self):
        self._lock = threading.Lock()
        self._monitors: Dict[str, CapacityMonitor] = {}

    def register(self, monitor: CapacityMonitor):
        with self._lock:
            self._monitors[monitor.nid] = monitor

    def unregister(self, nid: str):
        with self._lock:
            self._monitors.pop(str(nid), None)

    def get(self, nid: str) -> Optional[CapacityMonitor]:
        with self._lock:
            return self._monitors.get(str(nid))

    def list_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [m.to_dict() for m in self._monitors.values()]

    def count_active(self) -> int:
        with self._lock:
            return sum(
                1 for m in self._monitors.values()
                if getattr(m.owner, 'alive', False)
                and not (getattr(m.owner, 'cancellation_token', None) and m.owner.cancellation_token.is_set())
            )

    def count_waiting_capacity(self) -> int:
        with self._lock:
            return sum(1 for m in self._monitors.values() if m.state == CapacityState.WAITING_CAPACITY)


CAPACITY_MONITOR_REGISTRY = CapacityMonitorRegistry()
