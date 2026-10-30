"""
core/bank_selection/quota_classifier.py — Quota & Status Classification Engine
Differentiates active capacity, reserve eligibility, capacity full, and error conditions.
"""
from enum import Enum
from typing import List, Dict, Any, Optional, Tuple
from core.bank_matcher import PersianBankMatcher, BankOptionInfo


class QuotaStatus(str, Enum):
    ACTIVE_QUOTA = "ACTIVE_QUOTA"
    RESERVE_QUOTA = "RESERVE_QUOTA"
    CAPACITY_FULL = "CAPACITY_FULL"
    NO_MATCH = "NO_MATCH"
    SECURITY_BLOCK = "SECURITY_BLOCK"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    REGISTRATION_CONFIRMED = "REGISTRATION_CONFIRMED"
    UNKNOWN = "UNKNOWN"


class BankQuotaClassifier:
    """Classifies bank page options, applicant preferences, and server outcomes."""

    @classmethod
    def classify_preferences(
        cls,
        options: List[BankOptionInfo],
        target_banks: List[str],
        allow_reserve: bool = False,
    ) -> Tuple[QuotaStatus, Optional[BankOptionInfo], str]:
        """
        Evaluate live options against target bank preferences.
        Returns: (status, matched_option, explanation_text)
        """
        if not options:
            return QuotaStatus.CAPACITY_FULL, None, "هیچ بانکی در فهرست استان فعال نیست"

        if not target_banks:
            # Check if any active bank exists
            for opt in options:
                if opt.has_active_quota:
                    return QuotaStatus.ACTIVE_QUOTA, opt, f"بانک عمومی {opt.clean_title} دارای ظرفیت فعال"
            return QuotaStatus.CAPACITY_FULL, None, "ظرفیت تمام بانک‌ها تکمیل است"

        for target in target_banks:
            if not target:
                continue
            matched = PersianBankMatcher.match_best_bank(target, options, allow_reserve=allow_reserve)
            if matched:
                if matched.has_active_quota:
                    return QuotaStatus.ACTIVE_QUOTA, matched, f"بانک {matched.clean_title} دارای ظرفیت قطعی است"
                elif matched.is_reserve and allow_reserve:
                    return QuotaStatus.RESERVE_QUOTA, matched, f"بانک {matched.clean_title} در حالت سهمیه رزرو است"

        return QuotaStatus.NO_MATCH, None, f"بانک‌های درخواستی ({' / '.join(target_banks)}) در فهرست سهمیه‌دار موجود نیستند"

    @classmethod
    def classify_server_error(cls, error_text: str) -> QuotaStatus:
        """Classify server feedback or error banner text."""
        norm = PersianBankMatcher.normalize(error_text)
        if not norm:
            return QuotaStatus.UNKNOWN

        if any(w in norm for w in ("رد درخواست", "تعداد دفعات غیرمجاز", "محدودیت امنیتی", "کد امنیتی", "waf")):
            return QuotaStatus.SECURITY_BLOCK

        if any(w in norm for w in ("انقضای نشست", "نشست شما منقضی", "کد پیگیری معتبر نیست", "لطفا مجددا وارد")):
            return QuotaStatus.SESSION_EXPIRED

        if any(w in norm for w in ("ظرفیت تکمیل", "ظرفیتی جهت ثبت نام وجود ندارد", "عدم وجود سهمیه")):
            return QuotaStatus.CAPACITY_FULL

        if any(w in norm for w in ("ثبت با موفقیت", "کد رهگیری", "تایید ثبت نام")):
            return QuotaStatus.REGISTRATION_CONFIRMED

        return QuotaStatus.UNKNOWN
