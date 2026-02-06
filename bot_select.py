# bot_select.py
import time
import os
import json
import random
import re
from playwright.sync_api import Error as PlaywrightError
from bot_core import BotCore
from browser_launcher import BrowserLaunchError, close_browser, safe_goto
from browser_actions import BrowserActions
from database import DBHandler
from event_logger import EVENT_BROADCASTER, build_event

TARGET_URL = "https://ve.cbi.ir/SelectBnkShb.aspx"


def sleep_with_stop(stop_event, seconds: float, step: float = 0.2) -> bool:
    if seconds <= 0:
        return stop_event.is_set()
    end = time.time() + seconds
    while time.time() < end:
        if stop_event.is_set():
            return True
        time.sleep(min(step, end - time.time()))
    return stop_event.is_set()


class BankSelectionBot(BotCore):
    def __init__(self, nid, settings, log_callback=None, captcha_service=None, browser_profile=None):
        super().__init__(
            nid,
            settings,
            log_callback=log_callback,
            captcha_service=captcha_service,
            browser_profile=browser_profile,
        )
        self._favorite_notified = set()

    def run(self, stop_event, loan_type="rbtnNaghdi"):
        attempt = 0

        while attempt < self.retry_limit:
            if stop_event.is_set():
                break
            attempt += 1

            playwright = None
            browser = None
            context = None
            page = None
            try:
                playwright, browser, context, page = self.setup_browser(stop_event)
                if stop_event.is_set():
                    return

                otp_attempted = False

                def handle_dialog(dialog):
                    nonlocal otp_attempted
                    try:
                        msg = dialog.message()
                        if any(x in msg for x in ["منقضی", "نامعتبر", "اشتباه", "صحیح نمی باشد"]):
                            DBHandler.clear_otp(self.nid)
                            self.log("♻️ کد نامعتبر پاک شد. انتظار برای پیامک جدید...", "warning")
                            otp_attempted = False
                        dialog.accept()
                    except Exception:
                        pass

                page.on("dialog", handle_dialog)

                self.log(f"🚀 شروع عملیات (دور {attempt})...", "info", page)
                captcha_mode = self.settings.get("captcha_mode", "human")
                step1_timeout = 12000
                otp_timeout = 8000
                firewall_solved_count = 0
                max_firewall_solves = int(self.settings.get("max_firewall_solves_per_attempt", 6))
                next_state_sweep_at = 0.0

                # ✅ DIRECT NAVIGATION (must happen immediately)
                def handle_partial_navigation_error(exc):
                    if "net::ERR_CONNECTION_CLOSED" in str(exc) and page.url.startswith(TARGET_URL):
                        self.log("⚠️ Connection closed but page appears loaded; continuing.", "warning", page)
                        return True
                    return False

                def page_has_target_selectors():
                    selectors = [
                        "#ctl00_ContentPlaceHolder1_tbIDNo",
                        "input[name$='tbIDNo']",
                        "input[name$='tbMobileConfCode']",
                    ]
                    for selector in selectors:
                        try:
                            page.wait_for_selector(selector, timeout=2000, state="attached")
                            return True
                        except Exception:
                            continue
                    return False

                try:
                    safe_goto(page, TARGET_URL, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)
                except Exception as exc:
                    if handle_partial_navigation_error(exc) and page_has_target_selectors():
                        pass
                    else:
                        if sleep_with_stop(stop_event, 1.0):
                            return
                        try:
                            safe_goto(
                                page,
                                TARGET_URL,
                                timeout=60000,
                                wait_until="domcontentloaded",
                                log_callback=self.log,
                            )
                        except Exception as exc_retry:
                            if handle_partial_navigation_error(exc_retry) and page_has_target_selectors():
                                pass
                            else:
                                return
                else:
                    if sleep_with_stop(stop_event, 0.2):
                        return

                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    page.wait_for_function("document.readyState === 'complete'", timeout=15000)
                except Exception:
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass

                def wait_for_state(selector, label, timeout=2000):
                    if stop_event.is_set():
                        return False
                    self.log(f"🔍 Checking {label}...", "info", page)
                    try:
                        page.wait_for_selector(selector, timeout=timeout, state="visible")
                        return True
                    except Exception:
                        return False

                while not stop_event.is_set():
                    if stop_event.is_set():
                        break

                    if page.is_closed():
                        self.log("🛑 مرورگر توسط کاربر بسته شد.", "stopped")
                        stop_event.set()
                        return

                    # High-priority state gate: firewall challenge must short-circuit all other states.
                    if self.is_firewall_challenge(page):
                        DBHandler.update_status(self.nid, "BLOCKED_FIREWALL_MANUAL", "Manual firewall challenge")
                        resumed = self.handle_firewall_challenge(page, stop_event)
                        if stop_event.is_set():
                            break
                        if not resumed:
                            break
                        DBHandler.update_status(self.nid, "Ready", "Firewall challenge cleared")
                        if sleep_with_stop(stop_event, random.uniform(0.5, 0.8)):
                            break
                        continue

                    now = time.time()
                    if now >= next_state_sweep_at:
                        self._log_state_sweep(page)
                        next_state_sweep_at = now + 3.0

                    if otp_attempted and self._detect_otp_failure(page):
                        DBHandler.clear_otp(self.nid)
                        self.log("♻️ کد منقضی/نامعتبر شد؛ انتظار برای پیامک جدید.", "warning", page)
                        otp_attempted = False
                        if sleep_with_stop(stop_event, 1.0):
                            break
                        continue

                    # Soft WAF / blocked message
                    try:
                        if wait_for_state("text=درخواست شما رد شد", "CHECK_BLOCKERS/SOFT_WAF", timeout=1500):
                            self.log("⛔ مسدودی! رفرش...", "error", page)
                            if sleep_with_stop(stop_event, 2):
                                break
                            safe_goto(page, TARGET_URL, timeout=60000, wait_until="domcontentloaded", log_callback=self.log)
                            continue
                    except Exception:
                        pass

                    # مرحله ۱: ورود کد ملی
                    step1_found = self._wait_for_state_any_scope(
                        page,
                        "#ctl00_ContentPlaceHolder1_tbIDNo",
                        "CHECK_STEP_1_ENTRY_FORM",
                        timeout=step1_timeout,
                    )
                    if not step1_found:
                        step1_found = self._wait_for_state_any_scope(
                            page,
                            "input[name$='tbIDNo']",
                            "CHECK_STEP_1_ENTRY_FORM_FALLBACK",
                            timeout=step1_timeout,
                        )

                    if step1_found:
                        if stop_event.is_set():
                            break
                        if otp_attempted:
                            DBHandler.clear_otp(self.nid)
                            self.log("♻️ بازگشت به مرحله اول؛ کد قبلی پاک شد.", "warning", page)
                            otp_attempted = False

                        nid_locator, _ = self._select_editable_input(
                            page,
                            ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name$='tbIDNo']"],
                            "National ID",
                        )
                        if not nid_locator:
                            self.log("❌ Failed to locate National ID input.", "error", page)
                            self._debug_step1_dump(page, "nid_not_found")
                            if sleep_with_stop(stop_event, 1):
                                break
                            continue

                        filled = self._fill_input_with_verification(page, nid_locator, self.nid, "National ID")
                        if not filled:
                            self._debug_step1_dump(page, "nid_fill_failed")
                            if sleep_with_stop(stop_event, 1):
                                break
                            continue

                        if captcha_mode == "human":
                            self.log("🧩 CAPTCHA detected: manual required. Please solve and submit.", "warning", page)
                        else:
                            self._solve_captcha_wrapper(
                                page,
                                "input[name$='tbCaptcha1']",
                                "input[name$='btnSendConfirmCode']",
                                captcha_mode,
                            )

                        try:
                            page.wait_for_selector("input[name$='tbMobileConfCode']", timeout=otp_timeout)
                        except Exception:
                            pass

                    # مرحله ۲: OTP
                    elif self._wait_for_state_any_scope(
                        page,
                        "input[name$='tbMobileConfCode']",
                        "CHECK_STEP_2_OTP_FORM",
                        timeout=otp_timeout,
                    ):
                        if stop_event.is_set():
                            break

                        otp_filled = False
                        while not stop_event.is_set():
                            try:
                                page.wait_for_selector("input[name$='tbMobileConfCode']", timeout=1500, state="visible")
                            except Exception:
                                break
                            otp_code = self._get_otp_from_db_fresh()
                            if otp_code:
                                current_val = page.locator("input[name$='tbMobileConfCode']").input_value()
                                if current_val != otp_code:
                                    self.log(f"✅ دریافت کد پیامک: {otp_code}", "success", page)
                                    page.locator("input[name$='tbMobileConfCode']").fill(otp_code)
                                    if sleep_with_stop(stop_event, 0.5):
                                        break
                                otp_filled = True
                                break

                            self.log("📩 منتظر دریافت پیامک...", "waiting sms", page)
                            if sleep_with_stop(stop_event, 2):
                                break

                        if otp_filled:
                            success = self._solve_captcha_wrapper(
                                page,
                                "#ctl00_ContentPlaceHolder1_tbCaptcha2",
                                "#ctl00_ContentPlaceHolder1_btnContinue1",
                                captcha_mode,
                            )
                            if success:
                                otp_attempted = True
                                self.log("👆 تایید کد پیامک...", "info", page)
                                if sleep_with_stop(stop_event, 3):
                                    break

                    # مرحله ۳: انتخاب بانک
                    elif wait_for_state("#ctl00_ContentPlaceHolder1_ddlBankName", "CHECK_STEP_3_BANK_SELECTION", timeout=5000):
                        if stop_event.is_set():
                            break
                        result = self._process_bank_selection_v2(page, stop_event)
                        if result == "selected":
                            self.log("✅ بانک انتخاب شد.", "success", page)
                        elif result == "no_match":
                            self.log("❌ بانک مورد نظر یافت نشد. رفرش...", "warning", page)
                            try:
                                page.reload()
                            except Exception:
                                pass
                        elif result == "waiting":
                            if sleep_with_stop(stop_event, 2):
                                break

                    # انتخاب شعبه
                    elif wait_for_state("#ctl00_ContentPlaceHolder1_ddlBranch", "CHECK_STEP_4_BRANCH_SELECTION", timeout=5000):
                        if stop_event.is_set():
                            break
                        self._process_branch_selection(page, stop_event)

                    # اگر به لاگین پرت شد
                    elif wait_for_state("#ctl00_ContentPlaceHolder1_btnLogin", "CHECK_STEP_LOGIN_REDIRECT", timeout=5000):
                        if stop_event.is_set():
                            break
                        if otp_attempted:
                            DBHandler.clear_otp(self.nid)
                            self.log("♻️ نشست منقضی شد؛ بازگشت به مرحله اول و انتظار پیامک جدید.", "warning", page)
                            otp_attempted = False
                        self._perform_login_standard(page, captcha_mode)

                    # موفقیت نهایی
                    elif wait_for_state("#ctl00_ContentPlaceHolder1_lblTrackingCode", "CHECK_DONE_TRACKING_CODE", timeout=5000):
                        code = page.locator("#ctl00_ContentPlaceHolder1_lblTrackingCode").inner_text()
                        self.log(f"✅ کد رهگیری: {code}", "success")
                        DBHandler.save_success_data(self.nid, code)
                        return

                    # legacy fallback firewall solver (non-manual page flows)
                    elif self._has_firewall_signal(page):
                        if self.solve_firewall(page):
                            firewall_solved_count += 1
                            if firewall_solved_count > max_firewall_solves:
                                self.log(
                                    f"⛔ Firewall loop detected (> {max_firewall_solves} resolves in one attempt). Marking blocked.",
                                    "error",
                                    page,
                                )
                                self._debug_step1_dump(page, "firewall_stuck_blocked")
                                DBHandler.update_status(self.nid, "Blocked", "Firewall loop detected")
                                break
                            if sleep_with_stop(stop_event, random.uniform(0.5, 0.8)):
                                break
                            continue

                    if sleep_with_stop(stop_event, random.uniform(0.5, 0.8)):
                        break

            except PlaywrightError as pe:
                if self.close_browser_on_stop(stop_event, playwright, browser, context, page):
                    return
                if "Target closed" in str(pe):
                    self.log("🛑 مرورگر بسته شد.", "stopped")
                    stop_event.set()
                    return
                if sleep_with_stop(stop_event, 2):
                    break
            except BrowserLaunchError as exc:
                if self.close_browser_on_stop(stop_event, playwright, browser, context, page):
                    return
                self.log(f"خطا در مرورگر: {exc.message}", "error")
                if sleep_with_stop(stop_event, 2):
                    break
            except Exception:
                if self.close_browser_on_stop(stop_event, playwright, browser, context, page):
                    return
                if sleep_with_stop(stop_event, 2):
                    break
            finally:
                if stop_event.is_set():
                    try:
                        self.log("🛑 Stop detected - closing browser now.", "warning")
                    except Exception:
                        pass
                close_browser(playwright, browser, context, page)

            if not stop_event.is_set():
                sleep_with_stop(stop_event, random.uniform(0.5, 0.8))

    def is_firewall_challenge(self, page) -> bool:
        selectors = ["#ans", "#jar"]
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if locator.count() > 0 and locator.first.is_visible():
                    return True
            except Exception:
                continue

        challenge_markers = [
            "human visitor",
            "support id",
        ]
        try:
            body_text = (page.inner_text("body") or "").lower()
            return any(marker in body_text for marker in challenge_markers)
        except Exception:
            return False

    def handle_firewall_challenge(self, page, stop_event):
        self.log("🛡️ FIREWALL challenge detected - manual action required.", "warning", page)

        support_id = None
        try:
            body_text = page.inner_text("body") or ""
            match = re.search(r"Your support ID is:\s*(\d+)", body_text, re.IGNORECASE)
            if match:
                support_id = match.group(1)
        except Exception:
            pass

        if support_id:
            self.log(f"🧾 Firewall Support ID: {support_id}", "info", page)
            DBHandler.update_status(self.nid, "BLOCKED_FIREWALL_MANUAL", f"Support ID: {support_id}")
        else:
            DBHandler.update_status(self.nid, "BLOCKED_FIREWALL_MANUAL", "Manual firewall challenge")

        next_wait_log_at = 0.0
        while not stop_event.is_set():
            try:
                nid_input = page.locator("#ctl00_ContentPlaceHolder1_tbIDNo")
                entry_ready = nid_input.is_visible() and nid_input.is_enabled()
                if entry_ready:
                    self.log("✅ Firewall challenge cleared by operator. Resuming automation.", "success", page)
                    return True
            except Exception:
                pass

            now = time.time()
            if now >= next_wait_log_at:
                self.log("Waiting for operator...", "warning", page)
                next_wait_log_at = now + 15.0

            if sleep_with_stop(stop_event, 3.0):
                return False

        return False

    def _get_otp_from_db_fresh(self):
        try:
            with DBHandler._connect(row_factory=True) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT data FROM applicants WHERE national_id=?", (self.nid,))
                row = cursor.fetchone()
                if row and row["data"]:
                    data = json.loads(row["data"])
                    otp_code = data.get("otp_code")
                    return str(otp_code).strip() if otp_code else None
        except Exception:
            pass
        return None

    def _select_editable_input(self, page, selectors, label):
        scopes = [("page", page)] + [(f"frame[{idx}]", frame) for idx, frame in enumerate(page.frames)]
        for scope_name, scope in scopes:
            for selector in selectors:
                try:
                    locator = scope.locator(selector)
                    count = locator.count()
                except Exception:
                    continue
                for index in range(count):
                    candidate = locator.nth(index)
                    try:
                        is_visible = candidate.is_visible()
                        is_enabled = candidate.is_enabled()
                        readonly = candidate.get_attribute("readonly")
                        disabled = candidate.get_attribute("disabled")
                        handle = candidate.element_handle()
                        bounding_box = handle.bounding_box() if handle else None
                        if (
                            is_visible
                            and is_enabled
                            and not readonly
                            and not disabled
                            and bounding_box
                        ):
                            self.log(
                                f"✅ Selected {label} input via {selector} ({scope_name}, index {index}).",
                                "info",
                                page,
                            )
                            return candidate, scope
                    except Exception:
                        continue
        return None, None

    def _wait_for_state_any_scope(self, page, selector, label, timeout=2000):
        self.log(f"🔍 Checking {label}...", "info", page)
        scopes = [("page", page)] + [(f"frame[{idx}]", frame) for idx, frame in enumerate(page.frames)]
        for scope_name, scope in scopes:
            try:
                scope.wait_for_selector(selector, timeout=timeout, state="visible")
                self.log(f"✅ {label} detected in {scope_name}.", "info", page)
                return True
            except Exception:
                continue
        return False

    def _has_firewall_signal(self, page):
        selectors = ["#ans", "#jar", "text=در حال بررسی مرورگر شما", "text=cloudflare", "text=firewall"]
        for selector in selectors:
            try:
                loc = page.locator(selector)
                if loc.count() > 0 and loc.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _log_state_sweep(self, page):
        selectors = [
            "#ctl00_ContentPlaceHolder1_tbIDNo",
            "input[name$='tbIDNo']",
            "input[name$='tbMobileConfCode']",
            "#ctl00_ContentPlaceHolder1_ddlBankName",
        ]
        counts = {}
        for selector in selectors:
            try:
                counts[selector] = page.locator(selector).count()
            except Exception:
                counts[selector] = -1
        frame_urls = []
        try:
            for frame in page.frames:
                try:
                    frame_urls.append(frame.url)
                except Exception:
                    frame_urls.append("<unavailable>")
        except Exception:
            frame_urls = ["<unavailable>"]

        try:
            self.log(
                f"🧭 STATE_SWEEP url={page.url} title={page.title()} counts={counts} frames={len(frame_urls)} frame_urls={frame_urls}",
                "info",
                page,
            )
        except Exception:
            pass

    def _fill_input_with_verification(self, page, locator, value, label):
        try:
            locator.fill(value)
        except Exception:
            pass
        try:
            current_val = locator.input_value()
        except Exception:
            current_val = ""
        if current_val == value:
            self.log(f"✅ Filled {label} successfully.", "success", page)
            return True

        try:
            BrowserActions.force_fill(locator, value)
        except Exception:
            pass
        try:
            current_val = locator.input_value()
        except Exception:
            current_val = ""
        if current_val == value:
            self.log(f"✅ Filled {label} successfully (force_fill).", "success", page)
            return True

        try:
            handle = locator.element_handle()
            if handle:
                page.evaluate(
                    "(el, val) => {"
                    "el.value = val;"
                    "el.dispatchEvent(new Event('input', { bubbles: true }));"
                    "el.dispatchEvent(new Event('change', { bubbles: true }));"
                    "}",
                    handle,
                    value,
                )
        except Exception:
            pass

        try:
            current_val = locator.input_value()
        except Exception:
            current_val = ""
        if current_val == value:
            self.log(f"✅ Filled {label} successfully (js injection).", "success", page)
            return True

        self.log(f"❌ Failed to fill {label}.", "error", page)
        return False

    def _collect_selector_debug(self, page, selector):
        info = []
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            return info
        for index in range(count):
            candidate = locator.nth(index)
            try:
                handle = candidate.element_handle()
                bounding_box = handle.bounding_box() if handle else None
                entry = {
                    "selector": selector,
                    "index": index,
                    "id": candidate.get_attribute("id"),
                    "name": candidate.get_attribute("name"),
                    "type": candidate.get_attribute("type"),
                    "disabled": candidate.get_attribute("disabled"),
                    "readonly": candidate.get_attribute("readonly"),
                    "visible": candidate.is_visible(),
                    "enabled": candidate.is_enabled(),
                    "bounding_box": bounding_box,
                    "value": candidate.input_value(),
                }
                info.append(entry)
            except Exception:
                continue
        return info

    def _debug_step1_dump(self, page, reason):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        screenshot_dir = "screenshots"
        screenshot_path = f"{screenshot_dir}/select_{self.nid}_{timestamp}_{reason}.png"
        try:
            os.makedirs(screenshot_dir, exist_ok=True)
        except Exception:
            pass
        try:
            page.evaluate("() => console.log('debug dump triggered')")
        except Exception:
            pass
        try:
            page.screenshot(path=screenshot_path, full_page=True)
        except Exception:
            screenshot_path = "screenshot_failed"

        debug_data = {
            "reason": reason,
            "url": page.url,
            "selectors": [],
            "screenshot": screenshot_path,
        }
        selectors = ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name$='tbIDNo']", "input[name$='tbCaptcha1']"]
        for selector in selectors:
            debug_data["selectors"].extend(self._collect_selector_debug(page, selector))

        try:
            self.log(f"🧾 Step1 debug dump: {json.dumps(debug_data, ensure_ascii=False)}", "error", page)
        except Exception:
            pass

    def _process_bank_selection_v2(self, page, stop_event):
        try:
            dropdown_id = "#ctl00_ContentPlaceHolder1_ddlBankName"
            while not stop_event.is_set():
                options = page.locator(f"{dropdown_id} option").all()
                available_banks = {}

                for opt in options:
                    val = opt.get_attribute("value")
                    txt = opt.inner_text().strip()
                    if val and val != "0":
                        available_banks[txt] = val

                if not available_banks:
                    self.log("⚠️ لیست بانک‌ها خالی است. رفرش صفحه...", "warning", page)
                    if sleep_with_stop(stop_event, 3):
                        return "stopped"
                    try:
                        page.reload()
                    except Exception:
                        pass
                    continue

                runtime_data = self._load_runtime_data()
                user_priorities = runtime_data.get("priority_banks") or runtime_data.get("banks", [])
                favorite_banks = runtime_data.get("favorite_banks", [])
                stopped_banks = set(runtime_data.get("stopped_banks", []))

                if favorite_banks:
                    self._notify_favorite_banks(available_banks, favorite_banks, user_priorities)

                if not user_priorities:
                    self.log("⚠️ لیست اولویت بانک خالی است!", "error")
                    return "error"

                for priority in user_priorities:
                    target_name = priority["name"] if isinstance(priority, dict) else priority
                    if target_name in stopped_banks:
                        continue

                    found_val = None
                    for b_text, b_val in available_banks.items():
                        if target_name and target_name in b_text:
                            found_val = b_val
                            break

                    if found_val:
                        self.log(f"🎯 بانک یافت شد: {target_name}", "selecting", page)
                        page.select_option(dropdown_id, value=found_val)
                        self.log("⏳ در حال بارگذاری شعب...", "info", page)
                        self._wait_for_branch_ready(page)
                        return "selected"

                self.log("⚠️ بانک موردنظر پیدا نشد. رفرش صفحه...", "warning", page)
                if sleep_with_stop(stop_event, 3):
                    return "stopped"
                try:
                    page.reload()
                except Exception:
                    pass
            return "stopped"
        except Exception:
            return "error"

    def _process_branch_selection(self, page, stop_event):
        try:
            branch_ddl = "#ctl00_ContentPlaceHolder1_ddlBranch"
            branch_index = self.settings.get("branch_index", 1)
            try:
                branch_index = int(branch_index)
            except (TypeError, ValueError):
                branch_index = 1
            if branch_index < 1:
                branch_index = 1

            if page.locator(branch_ddl).input_value() == "0":
                page.locator(branch_ddl).select_option(index=branch_index)
                if self.settings.get("final_submit", False):
                    self.log("🔥 ثبت نهایی...", "success", page)
                    page.click("#ctl00_ContentPlaceHolder1_btnSave")
                else:
                    submit_btn = page.locator("#ctl00_ContentPlaceHolder1_btnSave")
                    try:
                        submit_btn.scroll_into_view_if_needed()
                        page.evaluate(
                            "btn => btn.style.border = '3px solid red'",
                            submit_btn.element_handle(),
                        )
                    except Exception:
                        pass
                    self.log("🛑 توقف قبل از ثبت نهایی (حالت تست)", "warning", page)
                    while not stop_event.is_set():
                        time.sleep(1)
        except Exception:
            pass

    def _wait_for_branch_ready(self, page):
        branch_ddl = "#ctl00_ContentPlaceHolder1_ddlBranch"
        try:
            page.wait_for_selector(branch_ddl, timeout=10000)
            page.wait_for_function(
                "(selector) => {"
                "const el = document.querySelector(selector);"
                "return el && !el.disabled && el.offsetParent !== null;"
                "}",
                branch_ddl,
                timeout=10000,
            )
        except Exception:
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                time.sleep(2)

    def _load_runtime_data(self):
        row = DBHandler.get_applicant(self.nid)
        if not row:
            return {}
        try:
            row_data = dict(row) if not isinstance(row, dict) else row
            data = json.loads(row_data["data"]) if row_data.get("data") else {}
        except Exception:
            data = {}
        return data

    def _notify_favorite_banks(self, available_banks, favorite_banks, user_priorities):
        priority_names = [
            p["name"] if isinstance(p, dict) else p for p in (user_priorities or [])
        ]
        for fav in favorite_banks:
            for b_text in available_banks.keys():
                if fav and fav in b_text and fav not in priority_names:
                    if fav in self._favorite_notified:
                        break
                    self._favorite_notified.add(fav)
                    EVENT_BROADCASTER.emit_event(
                        build_event("favorite_found", nid=self.nid, bank=fav, detail=b_text)
                    )
                    self.log(f"⭐ بانک مورد علاقه پیدا شد: {fav}", "info")
                    break

    def _detect_otp_failure(self, page) -> bool:
        phrases = [
            "کد منقضی",
            "منقضی شده",
            "کد تایید اشتباه",
            "کد تایید صحیح نمی باشد",
            "کد تایید نامعتبر",
            "session expired",
        ]
        try:
            content = page.content()
        except Exception:
            return False
        return any(phrase in content for phrase in phrases)

    def _detect_captcha_failure(self, page) -> bool:
        phrases = [
            "کد امنیتی اشتباه",
            "کد امنیتی صحیح",
            "کد امنیتی نادرست",
            "security code",
        ]
        try:
            content = page.content()
        except Exception:
            return False
        return any(phrase in content for phrase in phrases)

    def _solve_captcha_wrapper(self, page, input_sel, btn_sel, mode):
        try:
            captcha_img = page.locator(".BDC_CaptchaImage").first
            if not captcha_img.is_visible():
                return False

            if mode == "human":
                if page.locator(input_sel).input_value():
                    self.log("🧩 CAPTCHA filled by operator; submitting.", "info", page)
                    page.locator(btn_sel).click()
                    return True
                self.log("🧩 CAPTCHA detected: manual required.", "warning", page)
                return False

            if page.locator(input_sel).input_value() and mode == "robot":
                page.locator(btn_sel).click()
                return True

            code = self.captcha_service.solve(captcha_img.screenshot(), mode="general")

            if code and len(code) >= 4:
                self.log(f"🧩 حل شد: {code}", "info", page)
                inp = page.locator(input_sel)
                inp.clear()

                if mode == "human":
                    inp.type(code, delay=random.randint(150, 300))
                    time.sleep(0.5)
                    page.locator(btn_sel).click(delay=random.randint(50, 150))
                else:
                    inp.fill(code)
                    page.locator(btn_sel).click()
                time.sleep(1)
                result = "failed" if self._detect_captcha_failure(page) else "success"
                DBHandler.append_captcha_attempt(self.nid, code, result, source="select")
                return True
            else:
                self.log("❌ خطا در خواندن. رفرش...", "warning", page)
                try:
                    page.locator(".BDC_ReloadLink").first.click()
                except Exception:
                    pass
                time.sleep(1.5)
                if code:
                    DBHandler.append_captcha_attempt(self.nid, code, "failed", source="select")
                return False
        except Exception:
            return False

    def _perform_login_standard(self, page, mode):
        try:
            if not page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").input_value():
                page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").fill(self.nid)
            self._solve_captcha_wrapper(
                page,
                "#ctl00_ContentPlaceHolder1_tbCaptcha",
                "#ctl00_ContentPlaceHolder1_btnLogin",
                mode,
            )
        except Exception:
            pass
