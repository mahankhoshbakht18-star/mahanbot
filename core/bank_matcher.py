"""
core/bank_matcher.py — Persian Bank Matching & Quota Classification Engine
Handles Persian character normalization, composite bank precedence,
active vs. reserve quota classification, and known CBI static codes.
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# Complete 30 official CBI bank codes (static values in ddlBankName)
KNOWN_BANK_CODES: Dict[str, str] = {
    "11": "بانک صنعت و معدن",
    "12": "بانک ملت",
    "13": "بانک رفاه کارگران",
    "14": "بانک مسکن",
    "15": "بانک سپه",
    "16": "بانک کشاورزی",
    "17": "بانک ملی ایران",
    "18": "بانک تجارت",
    "19": "بانک صادرات ایران",
    "20": "بانک توسعه صادرات",
    "21": "پست بانک ایران",
    "22": "بانک توسعه تعاون",
    "53": "بانک کارآفرین",
    "54": "بانک پارسیان",
    "55": "بانک اقتصاد نوین",
    "56": "بانک سامان",
    "57": "بانک پاسارگاد",
    "58": "بانک سرمایه",
    "59": "بانک سینا",
    "60": "بانک قرض‌الحسنه مهر ایران",
    "61": "بانک شهر",
    "62": "بانک آینده",
    "64": "بانک گردشگری",
    "66": "بانک دی",
    "69": "بانک ایران زمین",
    "70": "بانک قرض‌الحسنه رسالت",
    "75": "موسسه اعتباری ملل",
    "78": "بانک خاورمیانه",
    "80": "موسسه اعتباری نور",
    "95": "بانک مشترک ایران و ونزوئلا",
}

# Reverse lookup: Normalized Bank Canonical Name -> Static Value
BANK_CANONICAL_TO_CODE: Dict[str, str] = {
    "صنعت و معدن": "11",
    "ملت": "12",
    "رفاه": "13",
    "رفاه کارگران": "13",
    "مسکن": "14",
    "سپه": "15",
    "کشاورزی": "16",
    "ملی": "17",
    "تجارت": "18",
    "صادرات": "19",
    "توسعه صادرات": "20",
    "پست بانک": "21",
    "توسعه تعاون": "22",
    "کارافرین": "53",
    "کارآفرین": "53",
    "پارسیان": "54",
    "اقتصاد نوین": "55",
    "سامان": "56",
    "پاسارگاد": "57",
    "سرمایه": "58",
    "سینا": "59",
    "مهر ایران": "60",
    "قرض الحسنه مهر ایران": "60",
    "شهر": "61",
    "اینده": "62",
    "آینده": "62",
    "گردشگری": "64",
    "دی": "66",
    "ایران زمین": "69",
    "رسالت": "70",
    "قرض الحسنه رسالت": "70",
    "ملل": "75",
    "موسسه اعتباری ملل": "75",
    "خاورمیانه": "78",
    "نور": "80",
    "موسسه اعتباری نور": "80",
    "ایران و ونزوئلا": "95",
}


@dataclass
class BankOptionInfo:
    value: str
    text: str
    clean_title: str = ""
    has_active_quota: bool = False
    is_reserve: bool = False
    waiting_count: int = 0
    raw_waiting_text: str = ""


class PersianBankMatcher:
    """Robust Persian Bank name normalization and matching engine."""

    # Explicit precedence for composite names to prevent partial substring collisions:
    # E.g. 'توسعه صادرات' MUST match before 'صادرات'
    COMPOSITE_PRECEDENCE: List[Tuple[str, str]] = [
        ("توسعه صادرات", "20"),
        ("توسعه تعاون", "22"),
        ("قرض الحسنه مهر ایران", "60"),
        ("مهر ایران", "60"),
        ("قرض الحسنه رسالت", "70"),
        ("پست بانک", "21"),
        ("اقتصاد نوین", "55"),
        ("صنعت و معدن", "11"),
        ("ایران زمین", "69"),
        ("ایران و ونزوئلا", "95"),
        ("موسسه اعتباری ملل", "75"),
        ("موسسه اعتباری نور", "80"),
        ("رفاه کارگران", "13"),
        ("کارافرین", "53"),
        ("کارآفرین", "53"),
    ]

    @staticmethod
    def normalize(text: Optional[str]) -> str:
        """Unified Persian normalization: letters, digits, half-spaces, punctuation."""
        if not text:
            return ""
        s = str(text).strip()
        # Arabic to Persian letters
        s = s.replace("ي", "ی").replace("ئ", "ی").replace("ى", "ی")
        s = s.replace("ك", "ک").replace("ة", "ه")
        # Persian and Arabic digits to English
        persian_digits = "۰۱۲۳۴۵۶۷۸۹"
        arabic_digits = "٠١٢٣٤٥٦٧٨٩"
        for i in range(10):
            s = s.replace(persian_digits[i], str(i)).replace(arabic_digits[i], str(i))
        # Remove tatweel and diacritics
        s = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", s)
        # Normalize zero-width space and multiple spaces
        s = s.replace("\u200c", " ").replace("\xa0", " ")
        s = re.sub(r"\s+", " ", s).strip()
        return s

    @classmethod
    def clean_bank_title(cls, text: str) -> str:
        """Extract bank name without capacity status brackets or queue counts."""
        normalized = cls.normalize(text)
        # Remove queue count if present (e.g. متقاضی در صف : 1,450 نفر)
        cleaned = re.sub(r"متقاضی\s*در\s*صف.*", "", normalized).strip()
        # Remove brackets like [دارای ظرفیت...] or (ذخیره)
        cleaned = re.sub(r"\[.*?\]|\(.*?\)", "", cleaned).strip()
        # Remove prefixes
        cleaned = re.sub(r"^(بانک\s+|موسسه\s+اعتباری\s+|پست\s+بانک\s+)", "", cleaned).strip()
        # If 'پست بانک' was stripped to empty, keep 'پست بانک'
        if not cleaned and "پست بانک" in normalized:
            return "پست بانک"
        return cleaned

    @classmethod
    def parse_option_text(cls, text: str) -> BankOptionInfo:
        """Parse raw dropdown text, extract waiting queue count, and classify active vs reserve quota."""
        normalized = cls.normalize(text)
        clean_title = cls.clean_bank_title(normalized)

        # Extract waiting queue count: متقاضی در صف : 1,450 نفر
        waiting_match = re.search(r"متقاضی\s*در\s*صف\s*:\s*([\d,]+)", normalized)
        waiting_count = 0
        raw_waiting = ""
        if waiting_match:
            raw_waiting = waiting_match.group(1).strip()
            try:
                waiting_count = int(raw_waiting.replace(",", "").replace("،", ""))
            except Exception:
                waiting_count = 0

        is_reserve = bool(
            "ذخیره" in normalized
            or "در انتظار" in normalized
            or "تخصیص ظرفیت جدید" in normalized
        )
        has_active = bool(
            "دارای ظرفیت ثبت نام" in normalized
            or "دارای ظرفیت" in normalized
        ) and not is_reserve

        return BankOptionInfo(
            value="",
            text=normalized,
            clean_title=clean_title,
            has_active_quota=has_active,
            is_reserve=is_reserve,
            waiting_count=waiting_count,
            raw_waiting_text=raw_waiting,
        )

    @classmethod
    def canonicalize_bank_name(cls, name: str) -> str:
        """Return canonical short name for matching."""
        n = cls.normalize(name)
        n = re.sub(r"^(بانک\s+|موسسه\s+اعتباری\s+)", "", n).strip()
        # Check composite names first
        for comp, _ in cls.COMPOSITE_PRECEDENCE:
            if comp in n:
                return comp
        # Check single names
        for key in BANK_CANONICAL_TO_CODE:
            if key in n:
                return key
        return n

    @classmethod
    def match_best_bank(
        cls,
        priority_name: str,
        available_options: List[BankOptionInfo],
        allow_reserve: bool = False,
    ) -> Optional[BankOptionInfo]:
        """
        Match applicant's target bank against available options in Step 3.
        Takes into account:
        1. Composite precedence (توسعه صادرات vs صادرات)
        2. Active quota first, reserve quota only if allow_reserve is True
        """
        if not priority_name or not available_options:
            return None

        norm_priority = cls.normalize(priority_name)
        canon_priority = cls.canonicalize_bank_name(norm_priority)

        # 1. Filter eligible options based on allow_reserve
        candidates: List[BankOptionInfo] = []
        for opt in available_options:
            parsed = cls.parse_option_text(opt.text)
            opt_info = BankOptionInfo(
                value=opt.value,
                text=opt.text,
                clean_title=parsed.clean_title,
                has_active_quota=parsed.has_active_quota,
                is_reserve=parsed.is_reserve,
            )
            if opt_info.has_active_quota:
                candidates.append(opt_info)
            elif allow_reserve and opt_info.is_reserve:
                candidates.append(opt_info)

        if not candidates:
            return None

        # 2. Check for exact code match if priority is known static code
        target_code = BANK_CANONICAL_TO_CODE.get(canon_priority)

        # 3. Match using composite precedence logic
        # If priority is 'صادرات', it must NOT match 'توسعه صادرات'
        # If priority is 'توسعه صادرات', it MUST match 'توسعه صادرات'
        for cand in candidates:
            cand_norm = cls.normalize(cand.text)
            cand_canon = cls.canonicalize_bank_name(cand_norm)

            # Code-based direct match
            if target_code and cand.value == target_code:
                return cand

            # Strict equality on canonical name
            if cand_canon == canon_priority:
                return cand

        # 4. Fallback: substring containment with strict composite avoidance
        for cand in candidates:
            cand_norm = cls.normalize(cand.text)
            if canon_priority in cand_norm:
                # Disallow 'صادرات' matching 'توسعه صادرات'
                if canon_priority == "صادرات" and "توسعه صادرات" in cand_norm:
                    continue
                # Disallow 'تعاون' matching 'توسعه تعاون'
                if canon_priority == "تعاون" and "توسعه تعاون" in cand_norm:
                    continue
                # Disallow 'مهر' matching 'مهر ایران' if separate
                if canon_priority == "مهر" and "مهر ایران" in cand_norm:
                    continue
                return cand

        return None
