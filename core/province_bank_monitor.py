"""
core/province_bank_monitor.py — Province-Level Bank Monitoring & Discrepancy Analyzer.

Enforces clear separation across 3 data tiers:
Tier A: Live displayable bank list on fresh page (Category A).
Tier B: Mode and capacity (In-person, Non-presential/Online, Reserve) (Category B).
Tier C: Official applicant request status (Reserve vs Confirmed) (Category C).

Identifies STALE_ONLY banks (banks present in old HTML snapshots but absent
from the current live province list).
STALE_ONLY is strictly an analytical marker and NEVER grants permission to submit.
"""

import time
from typing import Any, Dict, List, Optional, Set, Tuple


class ProvinceBankMonitor:
    """Manages province-level bank inventory and compares live vs historical lists."""

    def __init__(self, province_name: str):
        self.province_name = str(province_name or "نامشخص").strip()
        self.last_updated: float = 0.0
        # Category A: Live displayable bank list
        self.live_banks: List[Dict[str, Any]] = []
        # Category B: Mode and capacity per bank
        self.bank_capacities: Dict[str, Dict[str, Any]] = {}
        # Historical / Stale observation
        self.historical_banks: Set[str] = set()

    def update_live_banks(self, fresh_options: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Ingest freshly loaded bank options from Page 3.
        Returns a delta dictionary detailing changes.
        """
        old_bank_names = {b["clean_name"] for b in self.live_banks}
        fresh_banks = []

        for opt in fresh_options:
            val = str(opt.get("value") or opt.get("code") or "").strip()
            txt = str(opt.get("text") or opt.get("title") or "").strip()
            if not val or val == "0" or "انتخاب" in txt:
                continue

            clean_name = self.clean_bank_name(txt)
            is_reserve = any(k in txt for k in ["رزرو", "ذخیره", "تخصیص ظرفیت جدید"])
            has_active = ("دارای ظرفیت" in txt) and not is_reserve

            # Extract queue count if visible
            queue_count = None
            q_match = __import__("re").search(r'متقاضی\s*در\s*صف\s*[:=]?\s*([0-9]+)', txt)
            if q_match:
                try:
                    queue_count = int(q_match.group(1))
                except Exception:
                    queue_count = None

            entry = {
                "value": val,
                "text": txt,
                "clean_name": clean_name,
                "has_active_quota": has_active,
                "is_reserve": is_reserve,
                "queue_count": queue_count,
                "category": "A_LIVE_AVAILABLE",
            }
            fresh_banks.append(entry)

            # Update Category B: Mode & capacity
            self.bank_capacities[clean_name] = {
                "clean_name": clean_name,
                "has_active_quota": has_active,
                "is_reserve": is_reserve,
                "in_person_available": True,
                "online_available": not is_reserve,
                "queue_count": queue_count,
                "category": "B_MODE_AND_CAPACITY",
                "updated_at": time.time(),
            }

        fresh_bank_names = {b["clean_name"] for b in fresh_banks}
        added = sorted(fresh_bank_names - old_bank_names)
        removed = sorted(old_bank_names - fresh_bank_names)

        self.live_banks = fresh_banks
        self.last_updated = time.time()

        return {
            "province": self.province_name,
            "total_live_banks": len(fresh_banks),
            "added_banks": added,
            "removed_banks": removed,
            "has_changes": bool(added or removed),
            "timestamp": self.last_updated,
        }

    def register_historical_snapshot(self, snapshot_options: List[Dict[str, Any]]) -> None:
        """Register banks observed in an old saved HTML file."""
        for opt in snapshot_options:
            txt = str(opt.get("text") or opt.get("title") or "").strip()
            clean = self.clean_bank_name(txt)
            if clean:
                self.historical_banks.add(clean)

    def classify_bank_discrepancy(self, target_bank_name: str) -> Dict[str, Any]:
        """
        Determines the strict availability category for a bank:
        - LIVE_AVAILABLE: Visible and actionable on the fresh page.
        - STALE_ONLY: Present in old HTML/history, but ABSENT from current live list!
        - NOT_FOUND: Never seen or absent.
        """
        clean_target = self.clean_bank_name(target_bank_name)
        live_match = next((b for b in self.live_banks if clean_target in b["clean_name"] or b["clean_name"] in clean_target), None)

        if live_match:
            return {
                "bank_name": target_bank_name,
                "status": "LIVE_AVAILABLE",
                "is_actionable": True,
                "has_active_quota": live_match["has_active_quota"],
                "is_reserve": live_match["is_reserve"],
                "entry": live_match,
                "warning": None,
            }

        # Check if it was historically in saved HTML
        was_historical = any(clean_target in h or h in clean_target for h in self.historical_banks)
        if was_historical:
            return {
                "bank_name": target_bank_name,
                "status": "STALE_ONLY",
                "is_actionable": False,
                "has_active_quota": False,
                "is_reserve": False,
                "entry": None,
                "warning": (
                    f"بانک «{target_bank_name}» فقط در فایل‌های ذخیره‌شده قدیمی مشاهده شده و "
                    f"در فهرست زنده فعلی استان وجود ندارد. ارسال درخواست غیرمجاز است."
                ),
            }

        return {
            "bank_name": target_bank_name,
            "status": "NOT_FOUND",
            "is_actionable": False,
            "has_active_quota": False,
            "is_reserve": False,
            "entry": None,
            "warning": f"بانک «{target_bank_name}» در فهرست استان یافت نشد.",
        }

    @staticmethod
    def clean_bank_name(name: str) -> str:
        """Strip brackets, queue markers, and noise to get pure bank identity."""
        import re
        s = str(name or "").strip()
        s = re.sub(r'\[.*?\]', '', s)
        s = re.sub(r'\(.*?\)', '', s)
        s = re.sub(r'-?\s*متقاضی\s*در\s*صف.*', '', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s
