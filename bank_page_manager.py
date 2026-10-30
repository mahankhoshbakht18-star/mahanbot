"""
bank_page_manager.py — Management & State Machine for Page 3 (SelectBnkShb.aspx)
Handles state classification, capacity detection, reserve identification,
and recovery management for loan bank selection.
"""
import re
import os
import json
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("mahanbot.bank_page")


def capture_dom_html(page):
    """Serialize live form properties on a detached clone; never touch the live DOM."""
    return page.evaluate("""() => {
        const root = document.documentElement.cloneNode(true);
        for (const el of document.querySelectorAll('select, input[type=radio], input[type=checkbox]')) {
            if (!el.id) continue;
            const copy = root.querySelector('#' + CSS.escape(el.id));
            if (!copy) continue;
            if (el.tagName === 'SELECT') Array.from(copy.options).forEach((o, i) =>
                o.toggleAttribute('selected', !!el.options[i].selected));
            else copy.toggleAttribute('checked', el.checked);
        }
        return root.outerHTML;
    }""")

# ── Page 3 Essential States ───────────────────────────────────────────
class BankPageState:
    BANK_PAGE_DETECTED = "BANK_PAGE_DETECTED"
    BANK_OPTIONS_LOADING = "BANK_OPTIONS_LOADING"
    BANK_OPTIONS_READY = "BANK_OPTIONS_READY"
    PREFERRED_BANK_NOT_FOUND = "PREFERRED_BANK_NOT_FOUND"
    FINAL_SUBMIT_READY = "FINAL_SUBMIT_READY"
    FINAL_SUBMIT_IN_PROGRESS = "FINAL_SUBMIT_IN_PROGRESS"
    PAUSED = "PAUSED"
    BANK_PAGE_READY = "BANK_PAGE_READY"
    BANK_SELECTED = "BANK_SELECTED"
    NON_PRESENTIAL_SELECTED = "NON_PRESENTIAL_SELECTED"
    CAPACITY_FULL = "CAPACITY_FULL"
    RESERVE_PENDING = "RESERVE_PENDING"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"
    REGISTRATION_CONFIRMED = "REGISTRATION_CONFIRMED"
    WAITING_CAPACITY = "WAITING_CAPACITY"
    PAGE_3_READY = "PAGE_3_READY"
    SUBMITTING = "SUBMITTING"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


# State display badges in Persian with premium colors
STATE_METADATA = {
    BankPageState.BANK_PAGE_READY: {
        "fa": "آماده انتخاب بانک",
        "badge_class": "badge-info",
        "color": "#60A5FA",
        "description": "صفحه انتخاب بانک بارگذاری شده و گزینه‌ها در دسترس هستند.",
    },
    BankPageState.BANK_SELECTED: {
        "fa": "بانک انتخاب شد",
        "badge_class": "badge-primary",
        "color": "#8B5CF6",
        "description": "بانک اولویت‌دار در فرم انتخاب شده و در انتظار تکمیل مراحل است.",
    },
    BankPageState.NON_PRESENTIAL_SELECTED: {
        "fa": "غیرحضوری فعال",
        "badge_class": "badge-purple",
        "color": "#A78BFA",
        "description": "روش دریافت غیرحضوری (آنلاین) انتخاب و تایید شده است.",
    },
    BankPageState.CAPACITY_FULL: {
        "fa": "تکمیل ظرفیت",
        "badge_class": "badge-warning",
        "color": "#F59E0B",
        "description": "ظرفیت بانک یا روش غیرحضوری در استان تکمیل است؛ در صف بررسی دوره‌ای.",
    },
    BankPageState.RESERVE_PENDING: {
        "fa": "رزرو در انتظار تخصیص ظرفیت",
        "badge_class": "badge-peach",
        "color": "#FFB38A",
        "description": "درخواست به صورت رزرو آماده است و تا تخصیص اعتبار قطعی نیست.",
    },
    BankPageState.SESSION_EXPIRED: {
        "fa": "نشست منقضی‌شده",
        "badge_class": "badge-danger",
        "color": "#EF4444",
        "description": "نشست امنیتی سامانه مرکزی منقضی شده و ورود مجدد الزامی است.",
    },
    BankPageState.MANUAL_ACTION_REQUIRED: {
        "fa": "در انتظار تایید کاربر",
        "badge_class": "badge-amber",
        "color": "#F59E0B",
        "description": "بانک انتخاب‌شده آماده ثبت نهایی است؛ منتظر کلیک کاربر.",
    },
    BankPageState.REGISTRATION_CONFIRMED: {
        "fa": "ثبت قطعی با کد رهگیری",
        "badge_class": "badge-success",
        "color": "#10B981",
        "description": "ثبت نهایی در سامانه مرکزی تایید و کد رهگیری صادر شد.",
    },
    BankPageState.UNKNOWN: {
        "fa": "نامشخص",
        "badge_class": "badge-gray",
        "color": "#9CA3AF",
        "description": "وضعیت صفحه نیازمند بررسی است.",
    },
}


def normalize_persian(text: Any) -> str:
    """Normalize Persian and Arabic characters, spaces, and punctuation."""
    if not text:
        return ""
    s = str(text)
    s = re.sub(r"[\u064B-\u065F\u0670]", "", s)  # strip Arabic diacritics/tashdid
    s = s.replace("ي", "ی").replace("ك", "ک").replace("ۀ", "ه").replace("ة", "ه")
    s = s.replace("\u200c", " ")  # zero-width non-joiner to space
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_reserve_text(text: Any) -> bool:
    """Check if option text indicates waiting / reserve capacity status."""
    norm = normalize_persian(text)
    reserve_markers = (
        "ذخیره",
        "رزرو",
        "در انتظار تخصیص ظرفیت",
        "در انتظار تخصیص",
        "تخصیص ظرفیت جدید",
        "نوبت رزرو",
    )
    return any(m in norm for m in reserve_markers)


def classify_capacity_or_error(text: Any) -> Tuple[Optional[str], str]:
    """
    Classify Persian messages from DOM labels (#ctl00_ContentPlaceHolder1_labErrMsg,
    alerts, or modals) accurately without falling back to generic_error for capacity issues.
    """
    norm = normalize_persian(text)
    if not norm:
        return None, ""

    # Security and authentication errors take precedence over incidental capacity words.
    if any(m in norm.lower() for m in ("نشست شما", "مهلت زمانی", "session expired", "مجددا وارد", "مجدداً وارد")):
        return BankPageState.SESSION_EXPIRED, norm
    if any(m in norm.lower() for m in ("مسدود", "تعداد دفعات", "محدودیت دسترسی", "خطای امنیتی", "rate limit", "too many requests", "request rejected")):
        return "waf_blocked", norm

    # 1) Bank capacity full markers
    capacity_markers = (
        "ظرفیت ثبت نام",
        "ظرفیت ثبت‌نام",
        "ظرفیت غیرحضوری",
        "ظرفیت غیر حضوری",
        "ظرفیت پذیرش",
        "ظرفیت تکمیل",
        "تکمیل شده است",
        "تکمیل گردیده",
        "تکمیل می باشد",
        "تکمیل می‌باشد",
        "تکمیل است",
        "فاقد ظرفیت",
        "امکان انتخاب روش غیرحضوری",
        "امکان انتخاب روش غیر حضوری",
        "امکان ثبت نام غیرحضوری",
        "امکان ثبت نام غیر حضوری",
        "امکان ثبت نام به صورت غیرحضوری",
        "امکان ثبت نام به صورت غیر حضوری",
        "امکان ثبت نام در این بانک وجود ندارد",
        "امکان انتخاب این بانک وجود ندارد",
        "تکمیل ظرفیت",
        "عدم وجود ظرفیت",
        "سقف مجاز",
        "اتمام رسیده",
        "فاقد سهمیه",
        "سهمیه تکمیل",
    )
    if any(m in norm for m in capacity_markers) or (
        ("غیرحضوری" in norm or "غیر حضوری" in norm) and ("تکمیل" in norm or "اتمام" in norm or "عدم" in norm or "وجود ندارد" in norm)
    ) or (
        "ظرفیت" in norm and ("تکمیل" in norm or "اتمام" in norm or "فاقد" in norm or "پایان" in norm or "وجود ندارد" in norm)
    ):
        return BankPageState.CAPACITY_FULL, norm

    # 2) Session expired
    session_markers = ("نشست شما", "مهلت زمانی", "زمان مجاز", "مجددا وارد", "مجدداً وارد", "session expired")
    if any(m in norm.lower() for m in session_markers):
        return BankPageState.SESSION_EXPIRED, norm

    # 3) WAF / IP block
    waf_markers = ("مسدود", "تعداد دفعات", "محدودیت دسترسی", "درخواست شما رد شد", "خطای امنیتی")
    if any(m in norm for m in waf_markers):
        return "waf_blocked", norm

    # 4) OTP expired/invalid
    if any(m in norm for m in ("کد تایید", "کد تأیید", "کد پیامکی")) and any(k in norm for k in ("منقضی", "نامعتبر", "اشتباه")):
        return "otp_expired", norm

    return "generic_error", norm


def parse_bank_dropdown_html(html: str) -> List[Dict[str, Any]]:
    """
    Extract bank options from HTML content containing #ctl00_ContentPlaceHolder1_ddlBankName.
    Returns list of options with value, text, is_reserve, queue_count, and has_capacity.
    """
    banks = []
    # Find select element
    m_sel = re.search(r'<select[^>]*name=["\']ctl00\$ContentPlaceHolder1\$ddlBankName["\'][^>]*>(.*?)</select>', html, re.DOTALL | re.IGNORECASE)
    if not m_sel:
        # Fallback to id
        m_sel = re.search(r'<select[^>]*id=["\']ctl00_ContentPlaceHolder1_ddlBankName["\'][^>]*>(.*?)</select>', html, re.DOTALL | re.IGNORECASE)

    if not m_sel:
        return banks

    options_html = m_sel.group(1)
    pattern = r'<option\s+([^>]*?)>(.*?)</option>'
    for m in re.finditer(pattern, options_html, re.DOTALL | re.IGNORECASE):
        attrs = m.group(1)
        raw_text = m.group(2).strip()
        text = normalize_persian(raw_text)

        # Extract value
        val_m = re.search(r'value=["\']([^"\']*)["\']', attrs, re.IGNORECASE)
        val = val_m.group(1) if val_m else ""

        is_selected = "selected" in attrs.lower()

        if not val or val == "0" or "انتخاب" in text:
            continue

        is_reserve = is_reserve_text(text)
        has_capacity = "دارای ظرفیت" in text or not is_reserve

        # Extract queue count: e.g. متقاضی در صف : 1370 نفر
        queue_count = None
        q_m = re.search(r'متقاضی در صف\s*:\s*(\d+)', text)
        if q_m:
            try:
                queue_count = int(q_m.group(1))
            except ValueError:
                pass

        # Extract clean bank name
        clean_name = re.split(r"\[|\(|-\s*متقاضی|–\s*متقاضی", text, maxsplit=1)[0].strip(" -–—:")

        banks.append({
            "value": val,
            "text": text,
            "bank_name": clean_name,
            "is_selected": is_selected,
            "is_reserve": is_reserve,
            "has_capacity": has_capacity,
            "queue_count": queue_count,
        })

    return banks


def analyze_bank_page_html(html: str) -> Dict[str, Any]:
    """
    Comprehensive static inspection of a saved or live SelectBnkShb.aspx page HTML.
    Extracts all metadata and determines the BankPageState.
    """
    res: Dict[str, Any] = {
        "is_bank_page": False,
        "state": BankPageState.UNKNOWN,
        "applicant_nid": None,
        "applicant_name": None,
        "province": None,
        "city": None,
        "selected_bank": None,
        "selected_mode": None,
        "available_banks": [],
        "reserve_banks": [],
        "capacity_banks": [],
        "has_online_option": False,
        "online_checked": False,
        "in_person_checked": False,
        "error_message": None,
        "warning_message": None,
        "tracking_code": None,
    }

    if not html:
        return res

    # 1. Check if this is indeed the Page 3 (SelectBnkShb.aspx / ChildSelectBnkShb.aspx)
    step3_indicators = (
        "SelectBnkShb",
        "ChildSelectBnkShb",
        "ctl00_ContentPlaceHolder1_ddlBankName",
        "ddlBankName",
        "ctl00_ContentPlaceHolder1_ddlCity",
        "ctl00_ContentPlaceHolder1_UpdatePanel2",
        "ctl00_ContentPlaceHolder1_btnSave",
        "فرم انتخاب بانک",
        "انتخاب بانک",
    )
    if any(ind in html for ind in step3_indicators):
        res["is_bank_page"] = True

    # 2. Extract applicant info if present
    nid_m = re.search(r'id=["\']ctl00_ContentPlaceHolder1_labIDNo["\'][^>]*>(.*?)</span>', html, re.DOTALL | re.IGNORECASE)
    if nid_m:
        res["applicant_nid"] = normalize_persian(re.sub(r'<[^>]+>', '', nid_m.group(1)))

    name_m = re.search(r'id=["\']ctl00_ContentPlaceHolder1_labFLName["\'][^>]*>(.*?)</span>', html, re.DOTALL | re.IGNORECASE)
    if name_m:
        res["applicant_name"] = normalize_persian(re.sub(r'<[^>]+>', '', name_m.group(1)))

    prov_m = re.search(r'id=["\']ctl00_ContentPlaceHolder1_labStateName["\'][^>]*>(.*?)</span>', html, re.DOTALL | re.IGNORECASE)
    if prov_m:
        res["province"] = normalize_persian(re.sub(r'<[^>]+>', '', prov_m.group(1)))

    # Selected city in ddlCity
    city_m = re.search(r'<select[^>]*id=["\']ctl00_ContentPlaceHolder1_ddlCity["\'][^>]*>(.*?)</select>', html, re.DOTALL | re.IGNORECASE)
    if city_m:
        sel_opt = re.search(r'<option[^>]*selected[^>]*>(.*?)</option>', city_m.group(1), re.DOTALL | re.IGNORECASE)
        if sel_opt:
            res["city"] = normalize_persian(re.sub(r'<[^>]+>', '', sel_opt.group(1)))

    # 3. Tracking Code (if page already completed)
    tr_m = re.search(r'id=["\']ctl00_ContentPlaceHolder1_lblTrackingCode["\'][^>]*>(.*?)</span>', html, re.DOTALL | re.IGNORECASE)
    if tr_m:
        raw_txt = re.sub(r'<[^>]+>', '', tr_m.group(1)).strip()
        match_digits = re.search(r'(\d{6,20})', raw_txt)
        if match_digits:
            res["tracking_code"] = match_digits.group(1)
            res["state"] = BankPageState.REGISTRATION_CONFIRMED
            return res

    # 4. Error Message in labErrMsg
    err_m = re.search(r'id=["\']ctl00_ContentPlaceHolder1_labErrMsg["\'][^>]*>(.*?)</span>', html, re.DOTALL | re.IGNORECASE)
    if err_m:
        raw_err = re.sub(r'<[^>]+>', '', err_m.group(1)).strip()
        if raw_err:
            res["error_message"] = normalize_persian(raw_err)

    # Warning text
    warn_m = re.search(r'id=["\']ctl00_ContentPlaceHolder1_labWarning["\'][^>]*>(.*?)</span>', html, re.DOTALL | re.IGNORECASE)
    if warn_m:
        raw_warn = re.sub(r'<[^>]+>', '', warn_m.group(1)).strip()
        if raw_warn:
            res["warning_message"] = normalize_persian(raw_warn)

    # 5. Non-presential mode controls
    res["has_online_option"] = "rbtnGheireHozori" in html or "divGheireHozori" in html or "غیرحضوری" in html
    if "rbtnGheireHozori" in html:
        # Check if checked
        res["online_checked"] = bool(re.search(r'<input[^>]*id=["\']ctl00_ContentPlaceHolder1_rbtnGheireHozori["\'][^>]*checked', html, re.IGNORECASE))
    if "rbtnHozori" in html:
        res["in_person_checked"] = bool(re.search(r'<input[^>]*id=["\']ctl00_ContentPlaceHolder1_rbtnHozori["\'][^>]*checked', html, re.IGNORECASE))

    if res["online_checked"]:
        res["selected_mode"] = "online"
    elif res["in_person_checked"]:
        res["selected_mode"] = "in_person"

    # 6. Extract Bank list
    banks = parse_bank_dropdown_html(html)
    res["available_banks"] = banks
    res["reserve_banks"] = [b for b in banks if b["is_reserve"]]
    res["capacity_banks"] = [b for b in banks if not b["is_reserve"]]

    # Selected bank
    selected_banks = [b for b in banks if b["is_selected"]]
    if selected_banks:
        res["selected_bank"] = selected_banks[0]

    # 7. Evaluate State Machine
    if res["error_message"]:
        kind, _ = classify_capacity_or_error(res["error_message"])
        res["error_kind"] = kind
        if kind == BankPageState.CAPACITY_FULL:
            res["state"] = BankPageState.CAPACITY_FULL
            return res
        elif kind == BankPageState.SESSION_EXPIRED:
            res["state"] = BankPageState.SESSION_EXPIRED
            return res

    if res["selected_bank"]:
        sel_bank = res["selected_bank"]
        if sel_bank.get("is_reserve"):
            res["state"] = BankPageState.RESERVE_PENDING
        elif res["selected_mode"] == "online":
            res["state"] = BankPageState.NON_PRESENTIAL_SELECTED
        else:
            res["state"] = BankPageState.BANK_SELECTED
    elif res["is_bank_page"]:
        res["state"] = BankPageState.BANK_PAGE_READY

    return res


for _state, _label in {
    BankPageState.BANK_PAGE_DETECTED: "صفحه بانک فعال",
    BankPageState.BANK_OPTIONS_LOADING: "در انتظار بارگذاری بانک‌ها",
    BankPageState.BANK_OPTIONS_READY: "فهرست بانک‌ها آماده است",
    BankPageState.PREFERRED_BANK_NOT_FOUND: "بانک اولویت‌دار موجود نیست",
    BankPageState.FINAL_SUBMIT_READY: "آماده تأیید ثبت نهایی",
    BankPageState.FINAL_SUBMIT_IN_PROGRESS: "در انتظار نتیجه درخواست",
    BankPageState.PAUSED: "پایش متوقف؛ نشست حفظ شده",
}.items():
    STATE_METADATA[_state] = {"fa": _label, "badge_class": "badge-info", "color": "#60A5FA", "description": _label}

def extract_province_cities(html: str) -> List[Dict[str, Any]]:
    """
    Extract all city options from #ctl00_ContentPlaceHolder1_ddlCity in Step 3 HTML.
    Returns list of dicts: [{"code": "830", "name": "\xd8\xb2\xd8\xa7\xd9\x87\xd8\xaf\xd8\xa7\xd9\x86", "selected": False}, ...]
    """
    if not html:
        return []
    cities: List[Dict[str, Any]] = []
    city_m = re.search(r'<select[^>]*id=[^>]*ddlCity[^>]*>(.*?)</select>', html, re.DOTALL | re.IGNORECASE)
    if not city_m:
        city_m = re.search(r'<select[^>]*name=[^>]*ddlCity[^>]*>(.*?)</select>', html, re.DOTALL | re.IGNORECASE)
    if not city_m:
        return []

    opts = re.findall(r'<option\s+([^>]*)>(.*?)</option>', city_m.group(1), re.DOTALL | re.IGNORECASE)
    for attrs, raw_text in opts:
        val_m = re.search(r'value=[\'\"]?([^\'\"\s>]+)', attrs, re.IGNORECASE)
        val = val_m.group(1).strip() if val_m else ""
        if not val or val in ("0", "-1", ""):
            continue
        clean_text = normalize_persian(re.sub(r'<[^>]+>', '', raw_text)).strip()
        is_sel = "selected" in attrs.lower()
        cities.append({
            "code": val,
            "name": clean_text,
            "selected": is_sel
        })
    return cities


def repair_html_snapshot(html: str) -> str:
    """
    Ensures HTML snapshot:
    1. Contains <base href="https://ve.cbi.ir/"> for proper asset/CSS loading.
    2. Neutralizes CloseSession() and onunload traps to prevent session kill.
    3. Prevents auto-logout on review.
    """
    if not html:
        return html
    # 1. Base href
    if "<base " not in html.lower():
        head_m = re.search(r'<head[^>]*>', html, re.IGNORECASE)
        if head_m:
            pos = head_m.end()
            html = html[:pos] + '\n<base href="https://ve.cbi.ir/">\n' + html[pos:]
    # 2. Neutralize CloseSession
    if "CloseSession" in html:
        html = re.sub(
            r'function\s+CloseSession\s*\(\)\s*\{[^}]*\}',
            'function CloseSession() { /* neutralized */ }',
            html,
            flags=re.IGNORECASE
        )
    # 3. Disarm onbeforeunload
    html = re.sub(r'window\.onbeforeunload\s*=\s*CloseSession;', 'window.onbeforeunload = null;', html, flags=re.IGNORECASE)
    return html
