"""
core/sms_gateway.py — Pluggable SMS Gateway Provider for User OTP Authentication.
Supports Kavenegar, FarazSMS/IPPanel, Ghasedak, SMS.ir, and a secure Mock/Dev gateway.
"""
import abc
import json
import logging
import re
import urllib.request
import urllib.parse
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger("mahanbot.auth.sms")

# Mask mobile for audit logs (e.g., 09123456789 -> 0912***6789)
def mask_mobile(mobile: str) -> str:
    m = str(mobile or "").strip()
    if len(m) >= 10:
        return f"{m[:4]}***{m[-4:]}"
    return "***"


def normalize_mobile(mobile: str) -> Optional[str]:
    """Validate and normalize an Iranian mobile number to 09XXXXXXXXX format."""
    digits = str(mobile or "").strip()
    # Normalize Persian / Arabic digits
    fa_digits = "۰۱۲۳۴۵۶۷۸۹"
    ar_digits = "٠١٢٣٤٥٦٧٨٩"
    cleaned = []
    for ch in digits:
        if ch in fa_digits:
            cleaned.append(str(fa_digits.index(ch)))
        elif ch in ar_digits:
            cleaned.append(str(ar_digits.index(ch)))
        elif ch.isdigit() or ch == "+":
            cleaned.append(ch)
    num = "".join(cleaned)
    if num.startswith("+98"):
        num = "0" + num[3:]
    elif num.startswith("0098"):
        num = "0" + num[4:]
    elif num.startswith("98") and len(num) == 12:
        num = "0" + num[2:]
    elif len(num) == 10 and num.startswith("9"):
        num = "0" + num
    if re.fullmatch(r"09\d{9}", num):
        return num
    return None


class BaseSMSGateway(abc.ABC):
    @abc.abstractmethod
    def send_otp(self, mobile: str, code: str, template: Optional[str] = None) -> Tuple[bool, str]:
        """Send OTP verification code to mobile number. Returns (success, message)."""
        pass


class MockSMSGateway(BaseSMSGateway):
    """
    Mock SMS gateway for development, automated tests, and offline installations.
    Safely stores last dispatched codes in-memory for testing verification.
    """
    last_sent: Dict[str, str] = {}
    total_sent: int = 0
    total_failed: int = 0

    def send_otp(self, mobile: str, code: str, template: Optional[str] = None) -> Tuple[bool, str]:
        masked = mask_mobile(mobile)
        logger.info("[SMS MOCK] ارسال کد ورود به شماره %s با موفقیت شبیه‌سازی شد.", masked)
        MockSMSGateway.last_sent[mobile] = code
        MockSMSGateway.total_sent += 1
        return True, f"کد تایید با موفقیت برای شماره {masked} ارسال شد (سرویس آزمایشی)."


class KavenegarGateway(BaseSMSGateway):
    def __init__(self, api_key: str, sender_line: str = "", pattern_code: str = ""):
        self.api_key = api_key.strip()
        self.sender_line = sender_line.strip()
        self.pattern_code = pattern_code.strip()

    def send_otp(self, mobile: str, code: str, template: Optional[str] = None) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "کلید API کاوه‌نگار تنظیم نشده است."
        masked = mask_mobile(mobile)
        try:
            if self.pattern_code:
                url = f"https://api.kavenegar.com/v1/{self.api_key}/verify/lookup.json"
                params = {
                    "receptor": mobile,
                    "token": code,
                    "template": self.pattern_code,
                }
            else:
                url = f"https://api.kavenegar.com/v1/{self.api_key}/sms/send.json"
                text = template.replace("{code}", code) if template else f"کد تایید ورود ماهان‌بات: {code}\nاعتبار: ۵ دقیقه"
                params = {
                    "receptor": mobile,
                    "message": text,
                }
                if self.sender_line:
                    params["sender"] = self.sender_line

            data = urllib.parse.urlencode(params).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "MahanBot-Auth/2.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                status = res_json.get("return", {}).get("status")
                if status == 200:
                    logger.info("پیامک کاوه‌نگار به %s ارسال شد.", masked)
                    return True, "کد تایید ارسال گردید."
                err_msg = res_json.get("return", {}).get("message", "خطای سامانه پیامک")
                return False, f"خطای کاوه‌نگار: {err_msg}"
        except Exception as exc:
            logger.error("خطا در ارسال پیامک کاوه‌نگار: %s", exc)
            return False, f"خطای ارتباط با سامانه کاوه‌نگار: {str(exc)}"


class IPPanelGateway(BaseSMSGateway):
    def __init__(self, api_key: str, sender_line: str = "", pattern_code: str = ""):
        self.api_key = api_key.strip()
        self.sender_line = sender_line.strip()
        self.pattern_code = pattern_code.strip()

    def send_otp(self, mobile: str, code: str, template: Optional[str] = None) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "کلید API پنل پیامکی (FarazSMS/IPPanel) تنظیم نشده است."
        masked = mask_mobile(mobile)
        try:
            url = "http://ippanel.com/api/select"
            payload = {
                "op": "pattern",
                "user": self.api_key,
                "pass": "",
                "fromNum": self.sender_line or "3000505",
                "toNum": mobile,
                "patternCode": self.pattern_code,
                "inputData": [{"code": code}],
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = resp.read().decode("utf-8")
                logger.info("پاسخ وب‌سرویس فراز اس‌ام‌اس: %s", result[:100])
                return True, "کد تایید ارسال گردید."
        except Exception as exc:
            logger.error("خطا در ارسال فراز اس‌ام‌اس: %s", exc)
            return False, f"خطای ارتباط با سامانه پیامکی: {str(exc)}"


class GhasedakGateway(BaseSMSGateway):
    def __init__(self, api_key: str, sender_line: str = "", pattern_code: str = ""):
        self.api_key = api_key.strip()
        self.sender_line = sender_line.strip()
        self.pattern_code = pattern_code.strip()

    def send_otp(self, mobile: str, code: str, template: Optional[str] = None) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "کلید API قاصدک تنظیم نشده است."
        try:
            url = "https://api.ghasedak.me/v2/verification/send/simple"
            params = {
                "receptor": mobile,
                "type": 1,
                "template": self.pattern_code or "mahanbot_otp",
                "param1": code,
            }
            data = urllib.parse.urlencode(params).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"apikey": self.api_key})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return True, "کد تایید ارسال گردید."
        except Exception as exc:
            return False, f"خطای ارتباط با قاصدک: {str(exc)}"


class SmsIrGateway(BaseSMSGateway):
    def __init__(self, api_key: str, sender_line: str = "", pattern_code: str = ""):
        self.api_key = api_key.strip()
        self.sender_line = sender_line.strip()
        self.pattern_code = pattern_code.strip()

    def send_otp(self, mobile: str, code: str, template: Optional[str] = None) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "کلید API سامانه SMS.ir تنظیم نشده است."
        try:
            url = "https://api.sms.ir/v1/send/verify"
            payload = {
                "mobile": mobile,
                "templateId": int(self.pattern_code) if self.pattern_code.isdigit() else 100000,
                "parameters": [{"name": "Code", "value": code}],
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"x-api-key": self.api_key, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return True, "کد تایید ارسال گردید."
        except Exception as exc:
            return False, f"خطای ارتباط با سامانه SMS.ir: {str(exc)}"


def get_sms_gateway(settings: Optional[Dict[str, Any]] = None) -> BaseSMSGateway:
    """Factory to retrieve configured SMS Gateway."""
    from database import DBHandler
    cfg = settings or DBHandler.get_config()
    provider = str(cfg.get("sms_provider") or "mock").strip().lower()
    api_key = str(cfg.get("sms_api_key") or "").strip()
    sender_line = str(cfg.get("sms_sender_line") or "").strip()
    pattern_code = str(cfg.get("sms_pattern_code") or "").strip()

    if not api_key or provider in ("mock", "dev", "test", "none", ""):
        return MockSMSGateway()

    if provider == "kavenegar":
        return KavenegarGateway(api_key, sender_line, pattern_code)
    elif provider in ("ippanel", "farazsms", "faraz"):
        return IPPanelGateway(api_key, sender_line, pattern_code)
    elif provider == "ghasedak":
        return GhasedakGateway(api_key, sender_line, pattern_code)
    elif provider in ("sms_ir", "smsir"):
        return SmsIrGateway(api_key, sender_line, pattern_code)

    return MockSMSGateway()
