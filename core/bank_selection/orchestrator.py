"""
core/bank_selection/orchestrator.py — Central Bank Selection Orchestrator
Coordinates catalog parsing, Persian bank matching, UpdatePanel postback synchronization,
fallback handling, and automated submission for Step 3 (SelectBnkShb.aspx).
"""
import time
import logging
from typing import Dict, Any, List, Optional, Tuple

from core.bank_matcher import PersianBankMatcher, BankOptionInfo
from core.bank_selection.catalog_parser import BankCatalogParser
from core.bank_selection.quota_classifier import BankQuotaClassifier, QuotaStatus
from core.bank_selection.postback_sync import PostbackSynchronizer
from core.step3_bank_page import Step3BankPage
from core.telegram_notifier import TelegramNotifierService
from database import DBHandler

logger = logging.getLogger("mahanbot.bank_orchestrator")


class BankSelectionOrchestrator:
    """Central engine orchestrating the complete lifecycle of CBI Page 3 bank selection."""

    def __init__(self, bot, page):
        self.bot = bot
        self.page = page
        self.step3 = Step3BankPage()

    def inspect_and_evaluate(self) -> Tuple[QuotaStatus, Optional[BankOptionInfo], List[BankOptionInfo], str]:
        """
        1. Read all options from ddlBankName
        2. Differentiate active quota, reserve quota, and waiting count
        3. Match against applicant priority banks
        Returns: (status, matched_option, all_options, message)
        """
        options = BankCatalogParser.parse_playwright_page(self.page)
        if not options:
            # Fallback to step3 bank page locator
            options = self.step3.read_live_bank_options(self.page)

        prefs = self._get_applicant_preferences()
        target_banks = self._extract_target_bank_names(prefs)
        allow_reserve = bool(prefs.get("allow_reserve", False))

        status, matched, msg = BankQuotaClassifier.classify_preferences(
            options=options,
            target_banks=target_banks,
            allow_reserve=allow_reserve,
        )

        # Smart deduplicated archive and Telegram radar broadcast
        self._handle_archiving_and_radar(options)

        return status, matched, options, msg

    def execute_selection_and_submit(
        self,
        matched_option: BankOptionInfo,
        target_display: str,
        is_reserve: bool = False,
    ) -> Tuple[bool, Optional[str], str]:
        """
        Selects the matched bank option in ddlBankName, waits for PostbackSynchronizer,
        clicks btnSave, and extracts tracking code or fallback error.
        Returns: (success, tracking_code, message)
        """
        page, bot = self.page, self.bot
        bot.log(f"🎯 انتخاب بانک ({target_display}) با کد {matched_option.value} و هماهنگ‌سازی پست‌بک...", "info", page)

        # 1. Select bank and synchronize UpdatePanel
        synced = PostbackSynchronizer.select_and_sync(
            page,
            "#ctl00_ContentPlaceHolder1_ddlBankName",
            matched_option.value,
            timeout_ms=7000,
        )
        if not synced:
            # Fallback selection
            self.step3.select_bank_option(page, matched_option.value)

        # 2. Click save / submit button
        bot.log(f"🚀 ارسال فرم نهایی جهت ثبت سهمیه {target_display}...", "info", page)
        self.step3.click_submit_btn(page)

        # 3. Extract tracking code or outcome
        res = self.step3.extract_tracking_or_error(page)

        if res.get("is_success") and res.get("tracking_code"):
            code = res["tracking_code"]
            DBHandler.save_success_data(bot.nid, code)
            bot.log(f"🎉 ثبت نهایی با موفقیت انجام شد! کد رهگیری: {code}", "success", page)
            return True, code, "ثبت نهایی تایید شد"

        # 4. Fallback: Non-presential full -> auto switch to in-person (حضوری)
        if res.get("is_non_presential_full"):
            bot.log(f"⚠️ ظرفیت غیرحضوری {target_display} تکمیل شده؛ تغییر خودکار به گزینه حضوری...", "warning", page)
            if self.step3.switch_to_in_person_mode(page):
                bot.log(f"🔄 حالت ثبت به حضوری تغییر یافت. تلاش مجدد برای ثبت نهایی {target_display}...", "info", page)
                PostbackSynchronizer.select_and_sync(page, "#ctl00_ContentPlaceHolder1_ddlBankName", matched_option.value, timeout_ms=5000)
                self.step3.click_submit_btn(page)
                res2 = self.step3.extract_tracking_or_error(page)
                if res2.get("is_success") and res2.get("tracking_code"):
                    code = res2["tracking_code"]
                    DBHandler.save_success_data(bot.nid, code)
                    bot.log(f"🎉 ثبت نهایی حضوری تایید شد! کد رهگیری: {code}", "success", page)
                    return True, code, "ثبت نهایی حضوری تایید شد"

        err = res.get("error_msg") or "خطا در پردازش پاسخ سرور"
        bot.log(f"⚠️ پیام سرور پس از ارسال: {err}", "warning", page)
        return False, None, err

    def _get_applicant_preferences(self) -> Dict[str, Any]:
        """Fetch applicant data from database or bot object."""
        try:
            row = DBHandler.get_applicant(self.bot.nid)
            import json
            prefs = dict(row).get("data") if row else getattr(self.bot, "user_data", {})
            return json.loads(prefs) if isinstance(prefs, str) else (prefs or {})
        except Exception:
            return getattr(self.bot, "user_data", {}) or {}

    def _extract_target_bank_names(self, prefs: Dict[str, Any]) -> List[str]:
        """Extract prioritized bank names from preferences."""
        p_banks = prefs.get("priority_banks") or prefs.get("banks") or []
        names: List[str] = []
        if isinstance(p_banks, list):
            for item in p_banks:
                if isinstance(item, dict):
                    n = item.get("name") or item.get("bank_name") or ""
                    if n:
                        names.append(n)
                elif isinstance(item, str) and item:
                    names.append(item)
        if not names:
            fallback = prefs.get("primary_bank") or prefs.get("bank_name") or getattr(self.bot, "user_data", {}).get("bank_name") or ""
            if fallback:
                names.append(fallback)
        return names

    def _handle_archiving_and_radar(self, options: List[BankOptionInfo]):
        """Deduplicated snapshot archive and Telegram radar broadcast."""
        try:
            if not self.step3.should_save_page(self.bot, options):
                return
            self.step3.archive_banks_seen_nonblocking(self.bot, options, page=self.page)

            prov, city = self.step3.get_location_names(self.page)
            active_banks = [
                {"bank_name": o.clean_title or o.text, "waiting_count": o.waiting_count, "value": o.value}
                for o in options if o.has_active_quota
            ]
            reserve_banks = [
                {"bank_name": o.clean_title or o.text, "waiting_count": o.waiting_count, "value": o.value}
                for o in options if o.is_reserve
            ]

            notifier = TelegramNotifierService()
            msg = notifier.format_beautiful_radar_message(
                province_name=prov,
                city_name=city,
                active_banks=active_banks,
                reserve_banks=reserve_banks,
                applicant_nid=str(self.bot.nid),
                action_status="پایش فعال ظرفیت"
            )
            admin_cid = notifier.get_admin_chat_id()
            if admin_cid:
                notifier.send_raw_telegram(admin_cid, msg)
            channel_cid = notifier.get_channel_id()
            if channel_cid and channel_cid != admin_cid:
                notifier.send_raw_telegram(channel_cid, msg)
        except Exception as e:
            logger.debug(f"Radar broadcast ignored error: {e}")
