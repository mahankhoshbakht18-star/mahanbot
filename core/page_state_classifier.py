"""
core/page_state_classifier.py — Deterministic Page State Classifier for CBI Bank/Quota Selection.

Classifies HTML and HTTP status of ve.cbi.ir pages into strictly typed PageState results,
extracting active banks ([دارای ظرفیت ثبت نام]), reserve banks ([در انتظار تخصیص ظرفیت جدید (ذخیره)]),
and security/WAF Support IDs without false positives.
"""
from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Tuple

from core.persian_normalizer import normalize_persian_text


class PageState(str, Enum):
    BANK_ACTIVE_AVAILABLE = "BANK_ACTIVE_AVAILABLE"
    ONLY_RESERVE_AVAILABLE = "ONLY_RESERVE_AVAILABLE"
    TARGET_BANK_ABSENT = "TARGET_BANK_ABSENT"
    PROVINCE_CITY_REQUIRED = "PROVINCE_CITY_REQUIRED"
    WAF_REJECTED = "WAF_REJECTED"
    F5_BOT_CHALLENGE = "F5_BOT_CHALLENGE"
    MAINTENANCE_MODE = "MAINTENANCE_MODE"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    UNKNOWN = "UNKNOWN"


@dataclass
class BankOptionItem:
    value: str
    text: str
    name: str
    canonical: str
    is_active: bool
    is_reserve: bool
    queue_count: Optional[int] = None
    selected: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "text": self.text,
            "name": self.name,
            "canonical": self.canonical,
            "is_active": self.is_active,
            "is_reserve": self.is_reserve,
            "queue_count": self.queue_count,
            "selected": self.selected,
        }


@dataclass
class PageStateResult:
    state: PageState
    active_banks: List[Dict[str, Any]] = field(default_factory=list)
    reserve_banks: List[Dict[str, Any]] = field(default_factory=list)
    all_banks: List[Dict[str, Any]] = field(default_factory=list)
    target_bank_value: Optional[str] = None
    target_bank_status: Optional[str] = None  # "active", "reserve", "absent", or None
    target_bank_name: Optional[str] = None
    matched_bank_text: Optional[str] = None
    support_id: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    http_status: int = 200
    current_url: str = ""

    def has_active_quota(self) -> bool:
        return self.state == PageState.BANK_ACTIVE_AVAILABLE and bool(self.target_bank_value)

    def is_reserve_only(self) -> bool:
        return self.state == PageState.ONLY_RESERVE_AVAILABLE


# Canonical pattern dictionary matching bank names to canonical identifiers
_CANONICAL_PATTERNS: List[Tuple[str, Tuple[str, ...]]] = [
    ("tosee_saderat", ("توسعه صادرات", "توسعهصادرات")),
    ("tosee_taavon", ("توسعه تعاون", "توسعهتعاون")),
    ("mehr_iran", ("قرض الحسنه مهر ایران", "مهر ایران", "مهرایران", "بانک مهر")),
    ("resalat", ("قرض الحسنه رسالت", "رسالت")),
    ("postbank", ("پست بانک", "پستبانک")),
    ("eghtesad_novin", ("اقتصاد نوین", "اقتصادنوین")),
    ("iran_zamin", ("ایران زمین", "ایرانزمین")),
    ("iran_venezuela", ("ایران و ونزویلا", "ایران و ونزوئلا", "ونزویلا", "ونزوئلا")),
    ("sanat_madan", ("صنعت و معدن", "صنعت ومعدن", "صنعتمعدن")),
    ("refah", ("رفاه کارگران", "بانک رفاه", "رفاه")),
    ("melal", ("موسسه اعتباری ملل", "اعتباری ملل", "بانک ملل", "ملل")),
    ("noor", ("موسسه اعتباری نور", "اعتباری نور", "بانک نور", "نور")),
    ("khavarmiyaneh", ("خاورمیانه", "خاور میانه")),
    ("gardeshgari", ("گردشگری",)),
    ("karafarin", ("کارافرین", "کار افرین")),
    ("pasargad", ("پاسارگاد",)),
    ("parsian", ("پارسیان",)),
    ("saderat", ("بانک صادرات ایران", "صادرات ایران", "بانک صادرات", "صادرات")),
    ("melli", ("بانک ملی ایران", "ملی ایران", "بانک ملی", "ملی")),
    ("mellat", ("بانک ملت", "ملت")),
    ("tejarat", ("بانک تجارت", "تجارت")),
    ("sepah", ("بانک سپه", "سپه")),
    ("keshavarzi", ("بانک کشاورزی", "کشاورزی")),
    ("maskan", ("بانک مسکن", "مسکن")),
    ("saman", ("بانک سامان", "سامان")),
    ("sina", ("بانک سینا", "سینا")),
    ("sarmayeh", ("بانک سرمایه", "سرمایه")),
    ("shahr", ("بانک شهر", "شهر")),
    ("ayandeh", ("بانک اینده", "اینده")),
    ("day", ("بانک دی", "دی")),
]


def canonicalize_bank(name: str) -> str:
    norm = normalize_persian_text(name).replace("آ", "ا").replace("أ", "ا").replace("إ", "ا")
    clean = re.sub(r"[^\w\s]", " ", norm)
    compact = re.sub(r"\s+", "", clean)
    for canon_id, patterns in _CANONICAL_PATTERNS:
        for pat in patterns:
            pat_clean = re.sub(r"\s+", "", pat)
            if pat_clean in compact:
                return canon_id
    return compact


def extract_bank_name_from_option(text: str) -> str:
    norm = normalize_persian_text(text)
    split_part = re.split(r"\[|\(|-\s*متقاضی|–\s*متقاضی", norm, maxsplit=1)[0]
    return split_part.strip(" -–—:")


def extract_queue_count(text: str) -> Optional[int]:
    m = re.search(r"صف\s*:\s*([0-9]+)", text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


class PageStateClassifier:
    """
    Deterministic rule-based classifier for CBI bank selection pages.
    Extracts options, validates capacity markers, and classifies states strictly.
    """

    @classmethod
    def classify(
        cls,
        page_html: str,
        http_status: int = 200,
        current_url: str = "",
        target_banks: Optional[List[str]] = None,
    ) -> PageStateResult:
        html = page_html or ""
        norm_html = normalize_persian_text(html)
        errors: List[str] = []

        # 1. Check HTTP Status and Maintenance Mode
        if http_status in (502, 503, 504) or any(
            m in norm_html
            for m in (
                "سامانه در حال بروزرسانی ظرفیت بانک ها می باشد",
                "تا پایان عملیات بروزرسانی",
                "در حال بروزرسانی ظرفیت",
                "سامانه در حال ارتقا",
                "در دست تعمیر",
            )
        ):
            return PageStateResult(
                state=PageState.MAINTENANCE_MODE,
                errors=["سامانه در حال بروزرسانی یا تعمیرات است."],
                http_status=http_status,
                current_url=current_url,
            )

        # 2. Check WAF Rejection & Extract Support ID
        support_id = None
        support_match = re.search(
            r"support id(?: is)?[:\s]*([0-9A-Za-z-]+)",
            html,
            re.IGNORECASE,
        )
        if support_match:
            support_id = support_match.group(1).strip()

        if (
            "the requested url was rejected" in html.lower()
            or (support_id and "administrator" in html.lower())
            or (http_status == 403 and "f5" in html.lower())
        ):
            return PageStateResult(
                state=PageState.WAF_REJECTED,
                support_id=support_id,
                errors=[f"درخواست توسط فایروال WAF رد شد (Support ID: {support_id or 'unknown'})"],
                http_status=http_status,
                current_url=current_url,
            )

        # 3. Check F5 Bot Defense / JavaScript Challenge
        if any(
            m in html
            for m in (
                "/TSPD/?type=",
                "dos-waf",
                "challenge-platform",
                "human visitor",
                "captcha challenge",
            )
        ) and "ddlBankName" not in html and "ddlCity" not in html:
            return PageStateResult(
                state=PageState.F5_BOT_CHALLENGE,
                support_id=support_id,
                errors=["چالش بات فایروال F5 شناسایی شد."],
                http_status=http_status,
                current_url=current_url,
            )

        # 4. Check Session Expiration
        if any(
            m in norm_html
            for m in (
                "مدت زمان نشست شما به پایان رسیده",
                "نشست شما منقضی",
                "انقضای نشست",
                "زمان نشست شما به پایان رسیده",
                "مجددا وارد سامانه شوید",
                "لطفا مجددا وارد",
            )
        ):
            return PageStateResult(
                state=PageState.SESSION_EXPIRED,
                errors=["نشست کاربر در سامانه منقضی شده است."],
                http_status=http_status,
                current_url=current_url,
            )

        # 5. Check if Province/City selection is required
        # If ddlCity exists in HTML and has value 0 or "انتخاب کنید" selected, or bank dropdown not yet populated
        has_city_select = "ddlCity" in html
        city_zero_selected = bool(
            re.search(r'id=["\']?ctl00_ContentPlaceHolder1_ddlCity["\']?[^>]*>.*?<option[^>]*selected[^>]*value=["\']0["\']', html, re.DOTALL)
        )
        has_bank_select = "ddlBankName" in html

        if has_city_select and city_zero_selected and not has_bank_select:
            return PageStateResult(
                state=PageState.PROVINCE_CITY_REQUIRED,
                errors=["انتخاب استان و شهر الزامی است."],
                http_status=http_status,
                current_url=current_url,
            )

        # 6. Parse Bank Dropdown Options (#ctl00_ContentPlaceHolder1_ddlBankName)
        bank_select_match = re.search(
            r'<select[^>]*name=["\'][^"\']*ddlBankName[^"\']*["\'][^>]*>(.*?)</select>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if not bank_select_match:
            # Maybe province/city not selected yet if no bank dropdown
            if has_city_select:
                return PageStateResult(
                    state=PageState.PROVINCE_CITY_REQUIRED,
                    errors=["فهرست بانک‌ها در صفحه یافت نشد؛ نیاز به انتخاب شهر."],
                    http_status=http_status,
                    current_url=current_url,
                )
            return PageStateResult(
                state=PageState.UNKNOWN,
                errors=["فهرست بانک‌ها در صفحه یافت نشد."],
                http_status=http_status,
                current_url=current_url,
            )

        options_html = bank_select_match.group(1)
        raw_options = re.findall(
            r'<option([^>]*)>(.*?)</option>',
            options_html,
            re.IGNORECASE | re.DOTALL,
        )

        all_banks: List[BankOptionItem] = []
        active_banks: List[BankOptionItem] = []
        reserve_banks: List[BankOptionItem] = []

        for attrs, text_raw in raw_options:
            val_match = re.search(r'value=["\']([^"\']*)["\']', attrs, re.IGNORECASE)
            val = val_match.group(1).strip() if val_match else ""
            is_selected = "selected" in attrs.lower()

            clean_text = normalize_persian_text(re.sub(r'<[^>]+>', '', text_raw)).strip()
            if not val or val == "0" or "انتخاب کن" in clean_text:
                continue

            name = extract_bank_name_from_option(clean_text)
            canon = canonicalize_bank(name)
            queue_cnt = extract_queue_count(clean_text)

            is_active = "[دارای ظرفیت ثبت نام]" in clean_text
            is_reserve = (
                "[در انتظار تخصیص ظرفیت جدید (ذخیره)]" in clean_text
                or "ذخیره" in clean_text
            )

            item = BankOptionItem(
                value=val,
                text=clean_text,
                name=name,
                canonical=canon,
                is_active=is_active,
                is_reserve=is_reserve,
                queue_count=queue_cnt,
                selected=is_selected,
            )
            all_banks.append(item)
            if is_active:
                active_banks.append(item)
            elif is_reserve:
                reserve_banks.append(item)

        active_dicts = [b.to_dict() for b in active_banks]
        reserve_dicts = [b.to_dict() for b in reserve_banks]
        all_dicts = [b.to_dict() for b in all_banks]

        # 7. Evaluate against target_banks
        norm_targets: List[str] = [canonicalize_bank(t) for t in (target_banks or []) if t]

        if not norm_targets:
            # No specific target bank specified: evaluate page in general
            if active_banks:
                return PageStateResult(
                    state=PageState.BANK_ACTIVE_AVAILABLE,
                    active_banks=active_dicts,
                    reserve_banks=reserve_dicts,
                    all_banks=all_dicts,
                    target_bank_value=active_banks[0].value,
                    target_bank_status="active",
                    target_bank_name=active_banks[0].name,
                    matched_bank_text=active_banks[0].text,
                    http_status=http_status,
                    current_url=current_url,
                )
            elif reserve_banks:
                return PageStateResult(
                    state=PageState.ONLY_RESERVE_AVAILABLE,
                    active_banks=active_dicts,
                    reserve_banks=reserve_dicts,
                    all_banks=all_dicts,
                    target_bank_value=reserve_banks[0].value,
                    target_bank_status="reserve",
                    target_bank_name=reserve_banks[0].name,
                    matched_bank_text=reserve_banks[0].text,
                    http_status=http_status,
                    current_url=current_url,
                )
            else:
                return PageStateResult(
                    state=PageState.TARGET_BANK_ABSENT,
                    active_banks=active_dicts,
                    reserve_banks=reserve_dicts,
                    all_banks=all_dicts,
                    target_bank_status="absent",
                    errors=["هیچ بانکی با ظرفیت فعال یا رزرو در فهرست وجود ندارد."],
                    http_status=http_status,
                    current_url=current_url,
                )

        # Match target banks in priority order against active banks first!
        for target_canon in norm_targets:
            for active_item in active_banks:
                if active_item.canonical == target_canon:
                    return PageStateResult(
                        state=PageState.BANK_ACTIVE_AVAILABLE,
                        active_banks=active_dicts,
                        reserve_banks=reserve_dicts,
                        all_banks=all_dicts,
                        target_bank_value=active_item.value,
                        target_bank_status="active",
                        target_bank_name=active_item.name,
                        matched_bank_text=active_item.text,
                        http_status=http_status,
                        current_url=current_url,
                    )

        # If not active, check if target bank is available as reserve
        for target_canon in norm_targets:
            for reserve_item in reserve_banks:
                if reserve_item.canonical == target_canon:
                    return PageStateResult(
                        state=PageState.ONLY_RESERVE_AVAILABLE,
                        active_banks=active_dicts,
                        reserve_banks=reserve_dicts,
                        all_banks=all_dicts,
                        target_bank_value=reserve_item.value,
                        target_bank_status="reserve",
                        target_bank_name=reserve_item.name,
                        matched_bank_text=reserve_item.text,
                        http_status=http_status,
                        current_url=current_url,
                    )

        # Target bank was not found in active or reserve options
        return PageStateResult(
            state=PageState.TARGET_BANK_ABSENT,
            active_banks=active_dicts,
            reserve_banks=reserve_dicts,
            all_banks=all_dicts,
            target_bank_status="absent",
            errors=["بانک‌های درخواستی متقاضی در فهرست بانک‌های دارای ظرفیت یا رزرو موجود نیستند."],
            http_status=http_status,
            current_url=current_url,
        )
