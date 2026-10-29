"""
core/capacity_radar.py — Smart Bank Capacity Radar Engine (MAHANBOT 2.0).

Continuously tracks, records, and analyzes bank capacity observations across provinces and cities.
Detects real status changes without duplicate alerts, supports 18:00 (6 PM) CBI batch awareness,
and dispatches notifications to the Telegram engine.
"""
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from enum import Enum
import hashlib
import json
import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from core.persian_normalizer import normalize_persian_text
from database import DBHandler

logger = logging.getLogger("mahanbot.capacity_radar")


class CapacityStatus(str, Enum):
    AVAILABLE_OBSERVED = "AVAILABLE_OBSERVED"     # Bank option visible with quota in live UI/DOM
    AVAILABLE_CONFIRMED = "AVAILABLE_CONFIRMED"   # Confirmed active quota from validated submission/checkpoint
    FULL = "FULL"                                 # Capacity full message or dialog observed
    RESERVE = "RESERVE"                           # Reserve queue open ([در انتظار تخصیص ظرفیت جدید (ذخیره)])
    UNKNOWN = "UNKNOWN"                           # Not enough info / not seen
    MAINTENANCE = "MAINTENANCE"                   # Site updating / 503 error
    SESSION_EXPIRED = "SESSION_EXPIRED"           # Session expired alert


def is_near_18_batch(window_minutes_before: int = 15, window_minutes_after: int = 20) -> bool:
    """
    Returns True if current local time is within the 18:00 (6:00 PM) CBI batch update window.
    Default window: 17:45 to 18:20 local time.
    """
    now = datetime.now()
    cur_minutes = now.hour * 60 + now.minute
    target_minutes = 18 * 60  # 1080 minutes
    return (target_minutes - window_minutes_before) <= cur_minutes <= (target_minutes + window_minutes_after)


@dataclass
class CapacityObservation:
    province_name: str
    city_name: str
    bank_name: str
    status: CapacityStatus
    loan_type: str = "marriage"
    visit_method: str = "all"
    branch_name: str = ""
    observed_at: float = field(default_factory=time.time)
    data_source: str = "live_session"
    confidence: float = 1.0
    raw_text: str = ""
    event_id: str = field(default_factory=lambda: f"evt-{uuid.uuid4().hex[:12]}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "province_name": self.province_name,
            "city_name": self.city_name,
            "bank_name": self.bank_name,
            "status": self.status.value,
            "loan_type": self.loan_type,
            "visit_method": self.visit_method,
            "branch_name": self.branch_name,
            "observed_at": self.observed_at,
            "data_source": self.data_source,
            "confidence": self.confidence,
            "raw_text": self.raw_text,
        }


class CapacityRadarEngine:
    """
    Central Smart Bank Capacity Radar Engine.
    Handles observation ingestion, state change detection, deduplication, and alerting.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._recent_events_cache: List[Dict[str, Any]] = []
        self._last_state_hash = ""

    @staticmethod
    def is_near_18_batch(target_time: Optional[float] = None) -> bool:
        """
        Check if the observation occurs near the daily 18:00 (6:00 PM) CBI capacity update batch
        (between 17:50 and 18:15 local time).
        """
        t = datetime.fromtimestamp(target_time or time.time())
        current_minute_of_day = t.hour * 60 + t.minute
        # 17:50 is 17 * 60 + 50 = 1070
        # 18:15 is 18 * 60 + 15 = 1095
        return 1070 <= current_minute_of_day <= 1095

    def ingest_observation(
        self,
        province_name: str,
        city_name: str,
        bank_name: str,
        status: CapacityStatus,
        loan_type: str = "marriage",
        visit_method: str = "all",
        branch_name: str = "",
        data_source: str = "live_session",
        confidence: float = 1.0,
        raw_text: str = "",
        observed_at: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[str], str]:
        """
        Ingest a capacity observation.
        Returns (has_changed: bool, old_status: Optional[str], event_id: str).
        """
        clean_province = normalize_persian_text(province_name or "سراسری").strip()
        clean_city = normalize_persian_text(city_name or "کل استان").strip()
        clean_bank = normalize_persian_text(bank_name or "").strip()
        if not clean_bank:
            return False, None, ""

        obs_time = float(observed_at or time.time())
        event_id = f"evt-{uuid.uuid4().hex[:12]}"

        # 1. Store raw observation
        DBHandler.record_radar_observation(
            event_id=event_id,
            province_name=clean_province,
            city_name=clean_city,
            bank_name=clean_bank,
            status=status.value,
            loan_type=loan_type,
            visit_method=visit_method,
            branch_name=branch_name,
            data_source=data_source,
            confidence=confidence,
            raw_text=raw_text,
            observed_at=obs_time,
        )

        # 2. Check and upsert state (Deduplication)
        has_changed, old_status = DBHandler.upsert_radar_state(
            province_name=clean_province,
            city_name=clean_city,
            bank_name=clean_bank,
            loan_type=loan_type,
            visit_method=visit_method,
            current_status=status.value,
            event_id=event_id,
            details=details,
            observed_at=obs_time,
        )

        if has_changed:
            # 3. State transition occurred! Record change event
            DBHandler.record_radar_change_event(
                event_id=event_id,
                province_name=clean_province,
                city_name=clean_city,
                bank_name=clean_bank,
                old_status=old_status or "INITIAL",
                new_status=status.value,
                loan_type=loan_type,
                visit_method=visit_method,
                data_source=data_source,
                changed_at=obs_time,
            )

            # 4. Trigger Telegram notifications asynchronously
            try:
                from core.telegram_notifier import TELEGRAM_NOTIFIER
                TELEGRAM_NOTIFIER.dispatch_capacity_event(
                    event_id=event_id,
                    province_name=clean_province,
                    city_name=clean_city,
                    bank_name=clean_bank,
                    old_status=old_status or "INITIAL",
                    new_status=status.value,
                    loan_type=loan_type,
                    visit_method=visit_method,
                    observed_at=obs_time,
                )
            except Exception as e:
                logger.error(f"Failed to trigger telegram alert: {e}")

            return True, event_id, old_status

        return False, None, old_status

    def ingest_from_page_options(
        self,
        province_name: str,
        city_name: str,
        available_options: List[Tuple[str, str]],  # (text, value)
        loan_type: str = "marriage",
        data_source: str = "live_session",
    ):
        """
        Batch ingest all bank options seen in #ctl00_ContentPlaceHolder1_ddlBankName.
        Automatically categorizes each into AVAILABLE_OBSERVED or RESERVE.
        """
        clean_province = normalize_persian_text(province_name or "").strip()
        clean_city = normalize_persian_text(city_name or "").strip()
        now = time.time()

        for text_item, value_item in available_options:
            norm_text = normalize_persian_text(text_item).strip()
            if not norm_text or value_item == "0" or "انتخاب کن" in norm_text:
                continue

            # Extract clean bank title
            bank_title = norm_text
            markers = ["[دارای", "[در انتظار", "–", "-", "("]
            for m in markers:
                if m in bank_title:
                    bank_title = bank_title.split(m)[0].strip()

            is_active = "[دارای ظرفیت ثبت نام]" in norm_text or "دارای ظرفیت ثبت نام" in norm_text
            is_reserve = (
                "[در انتظار تخصیص ظرفیت جدید (ذخیره)]" in norm_text
                or "در انتظار تخصیص ظرفیت جدید" in norm_text
                or "ذخیره" in norm_text
            )

            if is_active:
                status = CapacityStatus.AVAILABLE_OBSERVED
            elif is_reserve:
                status = CapacityStatus.RESERVE
            else:
                status = CapacityStatus.AVAILABLE_OBSERVED

            # Check if non-in-person/virtual branch is indicated in option
            visit_method = "online" if ("غیرحضوری" in norm_text or "مجازی" in norm_text) else "all"

            self.ingest_observation(
                province_name=clean_province,
                city_name=clean_city,
                bank_name=bank_title,
                status=status,
                loan_type=loan_type,
                visit_method=visit_method,
                data_source=data_source,
                raw_text=norm_text,
                observed_at=now,
            )

    def get_radar_summary(
        self,
        province_name: Optional[str] = None,
        city_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get structured overview for dashboard and status queries."""
        states = DBHandler.get_radar_states(province_name=province_name, city_name=city_name)
        active_count = sum(1 for s in states if s.get("current_status") in ("AVAILABLE_OBSERVED", "AVAILABLE_CONFIRMED"))
        reserve_count = sum(1 for s in states if s.get("current_status") == "RESERVE")
        full_count = sum(1 for s in states if s.get("current_status") == "FULL")
        maintenance = any(s.get("current_status") == "MAINTENANCE" for s in states)

        return {
            "total_tracked": len(states),
            "active_capacity_count": active_count,
            "reserve_capacity_count": reserve_count,
            "full_count": full_count,
            "maintenance_active": maintenance,
            "is_18_batch_window": self.is_near_18_batch(),
            "items": states,
        }


CAPACITY_RADAR = CapacityRadarEngine()
