"""Update province and postal code on the live ve.cbi.ir edit forms.

SelEditCase.aspx only has three submit buttons. Postal code is
#ctl00_ContentPlaceHolder1_btnEditAddr, which opens EditAddress.aspx.
Province and city are #ctl00_ContentPlaceHolder1_btnEditInfo, which opens
RegisterInfoEdit.aspx. Both forms then ask for the national id, tbCaptcha1,
and btnSendConfirmCode before the stored applicant values are written.
"""
import re
import time
from typing import List

from bot_core import BotCore
from bot_helpers import detect_captcha_failure, sleep_with_stop
from browser_actions import BrowserActions
from browser_launcher import BrowserLaunchError, close_browser, safe_goto
from core.helpers import normalize_postal_code

SELECT_URL = "https://ve.cbi.ir/SelEditCase.aspx"
INFO_URL = "https://ve.cbi.ir/RegisterInfoEdit.aspx"
ADDR_URL = "https://ve.cbi.ir/EditAddress.aspx"
NID_INPUT = "#ctl00_ContentPlaceHolder1_tbIDNo"
CAPTCHA_1 = "#ctl00_ContentPlaceHolder1_tbCaptcha1"
SEND_CODE = "#ctl00_ContentPlaceHolder1_btnSendConfirmCode"
ADDR_BUTTON = "#ctl00_ContentPlaceHolder1_btnEditAddr"
INFO_BUTTON = "#ctl00_ContentPlaceHolder1_btnEditInfo"
STATE_SELECT = "#ctl00_ContentPlaceHolder1_ddlState"
CITY_SELECT = "#ctl00_ContentPlaceHolder1_ddlCity"
ZIP_INPUT = "#ctl00_ContentPlaceHolder1_tbZipCD"
LATER_CAPTCHA = [
    "#ctl00_ContentPlaceHolder1_tbCaptcha3",
    "#ctl00_ContentPlaceHolder1_tbCaptcha2",
    "#ctl00_ContentPlaceHolder1_tbCaptcha",
]
OTP_INPUTS = [
    "#ctl00_ContentPlaceHolder1_tbMobileConfCode",
    "#ctl00_ContentPlaceHolder1_tbMobConfCode",
    "#ctl00_ContentPlaceHolder1_tbConfCode",
    "input[name*='ConfCode']",
]
SAVE_BUTTONS = [
    "#ctl00_ContentPlaceHolder1_btnSave",
    "#ctl00_ContentPlaceHolder1_btnContinue",
    "#ctl00_ContentPlaceHolder1_btnContinue2",
    "#ctl00_ContentPlaceHolder1_btnConfirm",
    "#ctl00_ContentPlaceHolder1_btnEdit",
    "input[type='submit']",
]


class EditPostalBot(BotCore):
    def run(self, stop_event) -> bool:
        self._active_stop_event = stop_event
        data = self.user_data or {}
        self._province = str(data.get("state") or data.get("province") or "").strip()
        self._city = str(data.get("city") or "").strip()
        self._address = str(data.get("address") or "").strip()
        self._postal = normalize_postal_code(data.get("postal_code") or data.get("zip_code"))
        if not self._province or not self._postal or len(self._postal) != 10:
            self.log("استان یا کد پستی در پرونده متقاضی کامل نیست. ابتدا در اطلاعات متقاضی ذخیره شود.", "error")
            return False

        playwright = browser = context = page = None
        try:
            playwright, browser, context, page = self.setup_browser(stop_event)
            if not page or stop_event.is_set():
                return False
            page.on("dialog", lambda dialog: self._accept_dialog(dialog))
            self.log(
                f"شروع ویرایش سامانه: استان {self._province} و کد پستی {self._postal[:3]}*******",
                "info",
                page,
            )
            if not self._run_case(page, stop_event, INFO_BUTTON, INFO_URL, "استان", "province"):
                return False
            if not self._run_case(page, stop_event, ADDR_BUTTON, ADDR_URL, "کد پستی", "postal"):
                return False
            self.log("استان و کد پستی از پرونده متقاضی در سامانه مرکزی نوشته شد.", "success", page)
            return True
        except BrowserLaunchError as exc:
            self.log(f"خطای راه‌اندازی مرورگر: {exc.message}", "error")
            return False
        except Exception as exc:
            self.log(f"خطا در تغییر کد پستی: {exc}", "error")
            return False
        finally:
            close_browser(playwright, browser, context, page)

    def _run_case(self, page, stop_event, menu_button: str, target_url: str, title: str, kind: str) -> bool:
        self._case_kind = kind
        self._case_committed = False
        self._province_missing_logged = False
        self._edit_otp_requested = False
        self._last_dialog_message = ""
        if not self._open(page, SELECT_URL, stop_event):
            return False
        page_key = target_url.rsplit("/", 1)[-1].split(".")[0]
        menu = self._first(page, [menu_button])
        if menu:
            self.log(f"کلیک دکمه واقعی سامانه برای {title}.", "info", page)
            try:
                menu.click(timeout=8000)
                self.wait_for_aspnet_postback(page, timeout_ms=12000)
            except Exception as exc:
                self.log(f"کلیک دکمه ویرایش ناموفق بود: {exc}", "warning", page)
        if page_key not in (page.url or ""):
            self.log(f"ورود به {target_url} برای تغییر {title}.", "info", page)
            if not self._open(page, target_url, stop_event):
                return False
        while not stop_event.is_set():
            if self._page_closed(page):
                return False
            if self.is_firewall_challenge(page):
                if not self._solve_firewall_loop(page, stop_event):
                    return False
                continue
            if self._case_committed and self._saved(page) and page_key not in (page.url or ""):
                self.log(f"ویرایش {title} در سامانه ثبت شد.", "success", page)
                return True
            if self._data_form_ready(page):
                if self._write_applicant_fields(page, stop_event):
                    self.wait_for_aspnet_postback(page, timeout_ms=12000)
                    if self._saved(page):
                        self.log(f"ویرایش {title} در سامانه ثبت شد.", "success", page)
                        return True
                if sleep_with_stop(stop_event, 0.6):
                    return False
                continue
            if self._otp_visible(page):
                if not self._submit_otp(page, stop_event):
                    return False
                continue
            if self._first(page, [NID_INPUT, CAPTCHA_1, SEND_CODE]):
                if not self._submit_nid(page, stop_event):
                    return False
                continue
            if sleep_with_stop(stop_event, 0.4):
                return False
        return False

    def _submit_nid(self, page, stop_event) -> bool:
        nid_input = self._first(page, [NID_INPUT, "input[name$='tbIDNo']"])
        if nid_input:
            BrowserActions.human_fill(nid_input, self.nid)
            self.log("کد ملی در فرم واقعی سامانه نوشته شد.", "info", page)
        button = self._first(page, [SEND_CODE, "input[name$='btnSendConfirmCode']"])
        if not button:
            self.log("دکمه بررسی اطلاعات و ارسال کد تایید پیدا نشد.", "error", page)
            return False
        self._last_dialog_action = None
        ok = self.solve_captcha_with_model(
            page,
            CAPTCHA_1,
            button,
            stop_event=stop_event,
            source="edit_postal_nid",
        )
        self.wait_for_aspnet_postback(page, timeout_ms=10000)
        if self._otp_visible(page):
            return True
        if not ok or detect_captcha_failure(page) or getattr(self, "_last_dialog_action", None) == "captcha_invalid":
            self._last_dialog_action = None
            self.log("کپچای tbCaptcha1 رد شد؛ همان فرم دوباره حل می‌شود.", "warning", page)
            return True
        return True

    def _submit_otp(self, page, stop_event) -> bool:
        if not self._edit_otp_requested:
            self.log("سامانه کد تایید خواست. مرورگر باز می‌ماند تا پیامک برسد و تغییر ثبت شود.", "info", page)
            self.mark_otp_entry_time()
            self._edit_otp_requested = True
        code = self.wait_for_otp(stop_event=stop_event, timeout=8)
        if stop_event.is_set() or self._page_closed(page):
            return False
        if not self._otp_visible(page):
            self._edit_otp_requested = False
            return True
        if not code:
            return True
        otp_input = self._first(page, OTP_INPUTS)
        if otp_input:
            BrowserActions.human_fill(otp_input, str(code).strip())
        button = self._save_button(page)
        captcha = self._first(page, LATER_CAPTCHA)
        if captcha and button:
            self.solve_captcha_with_model(
                page, self._selector_of(captcha), button, stop_event=stop_event, source="edit_postal_otp",
            )
        elif button:
            try:
                button.click(timeout=4000)
            except Exception:
                return True
        self._edit_otp_requested = False
        self.wait_for_aspnet_postback(page, timeout_ms=10000)
        return True

    def _data_form_ready(self, page) -> bool:
        if self._case_kind == "province":
            return self._first(page, [STATE_SELECT]) is not None or self._field_after_label(page, "استان") is not None
        return (
            self._first(page, [ZIP_INPUT, CITY_SELECT]) is not None
            or self._field_after_label(page, "کد پستی") is not None
        )

    def _write_applicant_fields(self, page, stop_event) -> bool:
        wrote = False
        if self._case_kind == "province":
            selected = self._select_known(page, STATE_SELECT, self._province) or self._choose_after_label(page, "استان", self._province)
            if not selected:
                if not self._province_missing_logged:
                    self.log(f"فهرست استان در RegisterInfoEdit با مقدار {self._province} پیدا نشد.", "warning", page)
                    self._province_missing_logged = True
                return False
            self.log(f"استان از پرونده متقاضی در RegisterInfoEdit انتخاب شد: {self._province}", "info", page)
            self.wait_for_aspnet_postback(page, timeout_ms=10000)
            wrote = True
            if self._city and (self._select_known(page, CITY_SELECT, self._city) or self._choose_after_label(page, "شهر", self._city)):
                self.log(f"شهر از پرونده متقاضی انتخاب شد: {self._city}", "info", page)
                self.wait_for_aspnet_postback(page, timeout_ms=8000)
        else:
            postal = self._first(page, [ZIP_INPUT]) or self._field_after_label(page, "کد پستی")
            if postal is not None:
                BrowserActions.human_fill(postal, self._postal)
                self.log("کد پستی از پرونده متقاضی در فیلد سامانه نوشته شد.", "info", page)
                wrote = True
            if self._city and (self._select_known(page, CITY_SELECT, self._city) or self._choose_after_label(page, "شهر", self._city)):
                self.wait_for_aspnet_postback(page, timeout_ms=8000)
                wrote = True
            address = self._field_after_label(page, "نشانی") or self._field_after_label(page, "آدرس")
            if address is not None and self._address:
                BrowserActions.human_fill(address, self._address)
                wrote = True
        if not wrote:
            self.log("فیلد کد پستی یا استان در فرم بازشده پیدا نشد.", "error", page)
            return False
        button = self._save_button(page)
        if not button:
            self.log("دکمه ذخیره فرم ویرایش پیدا نشد.", "error", page)
            return False
        captcha = self._first(page, LATER_CAPTCHA)
        self._last_dialog_message = ""
        if captcha:
            saved = bool(self.solve_captcha_with_model(
                page, self._selector_of(captcha), button, stop_event=stop_event, source="edit_postal_save",
            ))
            self._case_committed = saved
            return saved
        try:
            button.click(timeout=4000)
            self._case_committed = True
            return True
        except Exception:
            return False

    def _save_button(self, page):
        for selector in SAVE_BUTTONS:
            found = self._first(page, [selector])
            if not found:
                continue
            try:
                value = (found.get_attribute("value") or "") + (found.inner_text() or "")
            except Exception:
                value = ""
            if "ارسال کد" in value:
                continue
            return found
        return None

    def _field_after_label(self, page, label: str):
        try:
            cells = page.locator("td")
            for index in range(min(cells.count(), 80)):
                cell = cells.nth(index)
                text = (cell.inner_text(timeout=300) or "").strip()
                if label not in text or len(text) > 40:
                    continue
                field = cell.locator("xpath=following-sibling::td[1]").locator("input:not([type='hidden']), select, textarea")
                if field.count() > 0 and field.first.is_visible():
                    return field.first
        except Exception:
            return None
        return None

    def _selector_of(self, field) -> str:
        try:
            element_id = field.get_attribute("id") or ""
        except Exception:
            element_id = ""
        if element_id:
            return f"#{element_id}"
        return ", ".join(LATER_CAPTCHA)

    def _select_known(self, page, selector: str, text: str) -> bool:
        field = self._first(page, [selector])
        if field is None or not text:
            return False
        bare = self._compact(text)
        if bare.startswith("استان"):
            bare = bare[len("استان"):]
        if not bare:
            return False
        try:
            options = field.locator("option")
            for index in range(options.count()):
                raw = options.nth(index).text_content() or ""
                option_text = self._compact(raw)
                if option_text.startswith("استان"):
                    option_text = option_text[len("استان"):]
                if bare != option_text and not (len(bare) > 3 and bare in option_text):
                    continue
                if "سوران" in bare and "سیب" not in bare and "سیب" in option_text:
                    continue
                option_value = options.nth(index).get_attribute("value")
                if not option_value or option_value in {"0", "-1"}:
                    continue
                field.select_option(value=option_value)
                return True
        except Exception:
            return False
        return False

    def _choose_after_label(self, page, label: str, value: str) -> bool:
        field = self._field_after_label(page, label)
        if field is None or not value:
            return False
        target = self._compact(value)
        try:
            options = field.locator("option")
            count = options.count()
            if count == 0:
                BrowserActions.human_fill(field, value)
                return True
            for index in range(count):
                raw = options.nth(index).text_content() or ""
                if target and target in self._compact(raw):
                    option_value = options.nth(index).get_attribute("value")
                    if option_value:
                        field.select_option(value=option_value)
                        return True
        except Exception:
            return False
        return False

    def _otp_visible(self, page) -> bool:
        return self._first(page, OTP_INPUTS) is not None

    def _saved(self, page) -> bool:
        blob = getattr(self, "_last_dialog_message", "") or ""
        try:
            blob += " " + (page.inner_text("body") or "")
        except Exception:
            pass
        return bool(re.search(r"با موفقیت|ویرایش شد|ثبت شد|ذخیره شد", blob))

    def _open(self, page, url: str, stop_event) -> bool:
        for attempt in range(1, 4):
            if stop_event.is_set():
                return False
            try:
                safe_goto(page, url, timeout=45000, wait_until="commit", log_callback=self.log)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=12000)
                except Exception:
                    pass
                return True
            except Exception as exc:
                self.log(f"تاخیر در باز شدن صفحه ({exc})؛ تلاش {attempt} از 3", "warning", page)
                if sleep_with_stop(stop_event, 1.5):
                    return False
        return False

    def _solve_firewall_loop(self, page, stop_event) -> bool:
        deadline = time.time() + int(self.settings.get("firewall_manual_timeout", 180))
        while not stop_event.is_set():
            if self.solve_firewall(page) and sleep_with_stop(stop_event, 0.3):
                return False
            if not self.is_firewall_challenge(page):
                return True
            if time.time() > deadline:
                return False
            if sleep_with_stop(stop_event, 0.5):
                return False
        return False

    def _accept_dialog(self, dialog):
        try:
            msg = dialog.message() if callable(getattr(dialog, "message", None)) else ""
        except Exception:
            msg = ""
        self._last_dialog_message = (msg or "").strip()
        try:
            if msg:
                self.log(f"پیام دیالوگ سامانه: {msg}", "info")
            dialog.accept()
        except Exception:
            pass

    def _page_closed(self, page) -> bool:
        try:
            return page.is_closed()
        except Exception:
            return True

    def _first(self, page, selectors: List[str]):
        scopes = [page]
        try:
            scopes.extend(list(page.frames))
        except Exception:
            pass
        for scope in scopes:
            for selector in selectors:
                try:
                    locator = scope.locator(selector)
                    if locator.count() > 0 and locator.first.is_visible():
                        return locator.first
                except Exception:
                    continue
        return None

    @staticmethod
    def _compact(text: str) -> str:
        value = str(text or "").replace("ي", "ی").replace("ك", "ک").replace("\u200c", "")
        return re.sub(r"\s+", "", value)
