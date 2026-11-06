"""
core/maintenance_hypothesis.py — Diagnostic Module for the 18:00 (6 PM) Hypothesis.

Compares CBI system behavior before and after 18:00 (daily maintenance/reconciliation window)
strictly through authorized and legitimate requests.
DISABLED BY DEFAULT.

Classifies hypotheses into:
- CONFIRMED: Supported by official server evidence.
- NOT_CONFIRMED: Inconclusive or insufficient data.
- REJECTED: Refuted by empirical server responses.
"""

import datetime
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mahanbot.maintenance_hypothesis")


class MaintenanceHypothesisTester:
    """Evaluates CBI system changes around 18:00 without circumventing maintenance."""

    def __init__(self, enabled: bool = False):
        self.enabled = bool(enabled)
        self.observations: List[Dict[str, Any]] = []

    def enable(self) -> None:
        self.enabled = True
        logger.info("MaintenanceHypothesisTester enabled.")

    def disable(self) -> None:
        self.enabled = False
        logger.info("MaintenanceHypothesisTester disabled.")

    def record_observation(
        self,
        event_name: str,
        applicant_nid: str,
        page_url: str,
        http_status: Optional[int] = None,
        maintenance_message_seen: bool = False,
        auth_valid: bool = True,
        banks_count: int = 0,
        reserve_banks_count: int = 0,
        confirmed_registration: bool = False,
        receipt_time: Optional[str] = None,
        server_date_header: Optional[str] = None,
        raw_notes: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Log an observation point before or after 18:00."""
        if not self.enabled:
            return None

        local_dt = datetime.datetime.now()
        is_post_18 = local_dt.hour >= 18 or (local_dt.hour == 17 and local_dt.minute >= 50)

        record = {
            "timestamp": time.time(),
            "local_time_iso": local_dt.isoformat(),
            "is_post_18": is_post_18,
            "event_name": event_name,
            "applicant_nid": applicant_nid,
            "page_url": page_url,
            "http_status": http_status,
            "maintenance_message_seen": maintenance_message_seen,
            "auth_valid": auth_valid,
            "banks_count": banks_count,
            "reserve_banks_count": reserve_banks_count,
            "confirmed_registration": confirmed_registration,
            "receipt_time": receipt_time,
            "server_date_header": server_date_header,
            "raw_notes": raw_notes,
        }
        self.observations.append(record)
        return record

    def generate_evaluation_report(self) -> Dict[str, Any]:
        """
        Analyze accumulated observations and categorize hypotheses into
        CONFIRMED, NOT_CONFIRMED, or REJECTED.
        """
        if not self.observations:
            return {
                "tester_enabled": self.enabled,
                "total_observations": 0,
                "summary": "هیچ داده‌ای ثبت نشده است.",
                "hypotheses": {
                    "maintenance_window_at_18": "NOT_CONFIRMED",
                    "bank_quotas_refresh_at_18": "NOT_CONFIRMED",
                    "pre_opened_session_survives_18": "NOT_CONFIRMED",
                    "reserve_auto_converts_post_18": "NOT_CONFIRMED",
                },
                "findings": [],
            }

        pre_18 = [o for o in self.observations if not o["is_post_18"]]
        post_18 = [o for o in self.observations if o["is_post_18"]]

        # 1. Maintenance window hypothesis
        maint_post = any(o["maintenance_message_seen"] for o in post_18)
        maint_hyp = "CONFIRMED" if maint_post else ("REJECTED" if len(post_18) >= 5 else "NOT_CONFIRMED")

        # 2. Bank quotas refresh hypothesis
        post_banks = [o["banks_count"] for o in post_18 if o["banks_count"] > 0]
        pre_banks = [o["banks_count"] for o in pre_18 if o["banks_count"] > 0]
        if post_banks and pre_banks:
            quota_hyp = "CONFIRMED" if max(post_banks) > max(pre_banks) else "NOT_CONFIRMED"
        else:
            quota_hyp = "NOT_CONFIRMED"

        # 3. Pre-opened session survival
        auth_post = [o["auth_valid"] for o in post_18]
        if auth_post:
            session_survives = all(auth_post)
            surv_hyp = "CONFIRMED" if session_survives else ("REJECTED" if not any(auth_post) else "NOT_CONFIRMED")
        else:
            surv_hyp = "NOT_CONFIRMED"

        # 4. Reserve auto-conversion post 18
        res_to_conf = any(o["confirmed_registration"] for o in post_18)
        conv_hyp = "CONFIRMED" if res_to_conf else "NOT_CONFIRMED"

        return {
            "tester_enabled": self.enabled,
            "total_observations": len(self.observations),
            "pre_18_count": len(pre_18),
            "post_18_count": len(post_18),
            "hypotheses": {
                "maintenance_window_at_18": maint_hyp,
                "bank_quotas_refresh_at_18": quota_hyp,
                "pre_opened_session_survives_18": surv_hyp,
                "reserve_auto_converts_post_18": conv_hyp,
            },
            "findings": [
                f"تعداد مشاهدات قبل از ۱۸: {len(pre_18)}",
                f"تعداد مشاهدات بعد از ۱۸: {len(post_18)}",
                f"مشاهده پیام تعمیرات بعد از ۱۸: {'بله' if maint_post else 'خیر'}",
            ],
        }
