"""Page-three lifecycle. Observation never reloads or synthesizes WebForms postbacks."""
import json
import time

from bank_page_manager import BankPageState as State, analyze_bank_page_html, capture_dom_html
from database import DBHandler, normalize_config
from submission_policy import submission_policy
from bot_select_bank import (
    process_bank_selection_v2,
    _extract_confirmed_submission,
    extract_confirmed_reservation,
)
from network_diagnostics import check_rejection


class BankSelectionSessionController:
    def __init__(self, bot, owner):
        self.bot, self.owner = bot, owner
        self.state = State.BANK_PAGE_DETECTED
        self._prepared = False
        self._last_preferences = None
        self._next_check = 0.0
        self._next_submit = 0.0
        self._monotonic = time.monotonic
        self._submit_response_timeout = 10.0
        self._quota_deadline_restored = False
        self._submitting = False
        self._requests = set()
        self._response_serial = 0
        self._last_final_response = {}
        self._awaiting_result = False
        self._attempt_serial = 0
        self._unknown_since = 0.0
        self._unknown_error = None
        self._last_attempt_completed = None
        self._submission_ready = False
        self._blocked = False
        self._last_observation = {}
        self._state_before_pause = None
        self._observation_issue = ""
        bot.bank_state_observer = self.transition
        owner.page.on("response", self._on_response)
        owner.page.on("request", self._on_request)
        owner.page.on("requestfailed", self._on_request_failed)
        owner.page.on("requestfinished", self._on_request_finished)
        bot.consume_recovery_flag("session_expired")  # Discard pre-Page-3 recovery history.
        try:
            cfg = normalize_config(DBHandler.get_config())
            interval = float(cfg.get("bank_refresh_interval") or cfg.get("bank_capacity_check_interval") or 5.0)
            if interval <= 0 or interval > 60:
                interval = 5.0
        except Exception:
            interval = 5.0
        from capacity_monitor import CapacityMonitor
        self.capacity_monitor = CapacityMonitor(bot, owner, interval_seconds=interval)

    @staticmethod
    def _is_final_request(request):
        if request.method != "POST" or not isinstance(request.post_data, str):
            return False
        from urllib.parse import parse_qs
        fields = parse_qs(request.post_data or "", keep_blank_values=True)
        return (fields.get("__EVENTTARGET") or [""])[0].endswith("$btnSave") or any(key.endswith("$btnSave") for key in fields)

    def _on_request(self, request):
        if self._is_final_request(request):
            self._requests.add(id(request))

    def _on_request_failed(self, request):
        if self._is_final_request(request):
            self._requests.discard(id(request))

    def _on_response(self, response):
        try:
            req = response.request
            if req and (req.is_navigation_request() or req.method == "POST"):
                if response.status == 401:
                    self.bot._set_recovery_flag("session_expired")
                elif response.status in {403, 429}:
                    self._blocked = True
        except Exception:
            pass

    def _on_request_finished(self, request):
        if self._is_final_request(request):
            data = {}
            response = None
            try:
                response = request.response()
            except Exception:
                response = None
            if response is not None and getattr(response, 'status', 0) == 200:
                try:
                    html = response.text()
                    data = analyze_bank_page_html(html)
                    data['reserve_confirmed'] = extract_confirmed_reservation(html=html)
                except Exception:
                    pass  # Missing response content is an unknown outcome, never success.
            self._requests.discard(id(request))
            self._response_serial += 1
            status_val = getattr(response, 'status', 0) if response else 0
            self._last_final_response = {'serial': self._response_serial, 'status': status_val, 'data': data}

    def transition(self, state, reason=""):
        owner, bot = self.owner, self.bot
        owner.assert_owner()
        if self.state == state and owner.current_state == state and reason == getattr(self, "_last_reason", ""):
            return
        self._last_reason = reason
        previous = self.state
        self.state = owner.current_state = state
        owner.last_successful_transition = time.time()
        active = owner.bank_page_active and state != State.SESSION_EXPIRED
        selected = self._last_observation.get("selected_bank") or {}
        selection = {}
        if selected:
            selection["selected_bank"] = selected.get("bank_name", "")
        if self._last_observation.get("selected_mode"):
            selection["bank_mode"] = self._last_observation["selected_mode"]
        owner.last_valid_checkpoint = bot.save_checkpoint(
            "STEP_3_BANK_SELECT", page=owner.page, bank_state=state,
            session_id=owner.session_id, session_active=active,
            last_operation_result=state, last_error_reason=reason,
            **selection,
        )
        error_text = (getattr(bot, "_bank_selection_issue_message", "") or reason
                      if reason == getattr(bot, "_bank_selection_issue", None) else reason)
        bot.set_runtime_state("PAUSED" if state == State.PAUSED else
                              "WAITING_USER_INPUT" if state in {State.SESSION_EXPIRED, State.MANUAL_ACTION_REQUIRED} else
                              "BANK_CAPACITY_FULL" if state == State.CAPACITY_FULL else "MONITORING",
                              state, browser_status="OPEN", session_id=owner.session_id,
                              last_error=error_text if state in {State.MANUAL_ACTION_REQUIRED, State.SESSION_EXPIRED, State.CAPACITY_FULL} else "")
        if state in {State.PAUSED, State.MANUAL_ACTION_REQUIRED, State.SESSION_EXPIRED}:
            DBHandler.update_runtime(bot.nid, next_refresh=0)
        bot.emit_event("BANK_STATE_CHANGED", step=state, reason=reason,
                       result=f"{previous}->{state}", message=f"وضعیت صفحه بانک: {state}")
        if hasattr(self, "capacity_monitor"):
            from capacity_monitor import STATE_MAPPING
            self.capacity_monitor.state = STATE_MAPPING.get(state, state)
            if selected and selected.get("bank_name"):
                self.capacity_monitor.selected_bank = selected.get("bank_name", "")
            if self._last_observation.get("selected_mode"):
                self.capacity_monitor.selected_mode = self._last_observation["selected_mode"]
            if self._last_observation.get("city"):
                self.capacity_monitor.city = self._last_observation["city"]
            if reason:
                self.capacity_monitor.last_system_message = str(reason)
            self.capacity_monitor.sync_runtime()

    def observe(self):
        self.owner.assert_owner()
        page, bot = self.owner.page, self.bot
        if page.is_closed():
            self.owner.bank_page_active = False
            self.owner.validated_at = 0
            return "closed", {}
        stage = bot.detect_actual_page_stage(page)
        check_rejection(page)
        html = capture_dom_html(page)
        data = analyze_bank_page_html(html)
        for name, selector in (("selected_branch", "#ctl00_ContentPlaceHolder1_ddlBranch, #ctl00_ContentPlaceHolder1_lstBranch, #ctl00_ContentPlaceHolder1_hdnBranchCode"), ("city_value", "#ctl00_ContentPlaceHolder1_ddlCity")):
            control = page.locator(selector)
            data[name] = control.first.input_value() if control.count() and control.first.is_visible() else ""
        if not data.get("selected_branch"):
            data["selected_branch"] = DBHandler.get_runtime(bot.nid).get("selected_branch", "")
        self._last_observation = data
        self._observation_issue = ""
        observed_nid = str(data.get("applicant_nid") or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")).strip()
        if stage == "OFFLINE_SNAPSHOT" or (observed_nid and observed_nid != str(bot.nid)):
            self._observation_issue = "offline_snapshot" if stage == "OFFLINE_SNAPSHOT" else "applicant_identity_mismatch"
            self.owner.bank_page_active = False
            self.owner.validated_at = 0
            return "UNTRUSTED_PAGE", data
        if stage == "STEP_3_BANK_SELECT" and data.get("is_bank_page"):
            self.owner.bank_page_active = True
            self.owner.validated_at = time.time()
        elif stage in {"STEP_1_ENTRY_FORM", "STEP_2_OTP"}:
            self.owner.bank_page_active = False
            self.owner.validated_at = 0
            return State.SESSION_EXPIRED, data
        return stage, data

    @staticmethod
    def _selection_signature(data):
        return json.dumps({key: data.get(key) for key in
                           ("city", "city_value", "province", "available_banks", "selected_bank", "selected_mode", "selected_branch")},
                          ensure_ascii=False, sort_keys=True)

    def _preferences(self):
        row = DBHandler.get_applicant(self.bot.nid)
        prefs = dict(row).get('data') if row else self.bot.user_data
        return json.loads(prefs) if isinstance(prefs, str) else (prefs or {})

    def _get_target_bank_names(self):
        prefs = self._preferences()
        p_banks = prefs.get("priority_banks") or prefs.get("banks") or []
        names = []
        if isinstance(p_banks, list):
            for item in p_banks:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("bank_name") or ""
                    if name:
                        names.append(name)
                elif isinstance(item, str) and item:
                    names.append(item)
        if not names:
            fallback = prefs.get("primary_bank") or prefs.get("bank_name") or prefs.get("bank") or (self.bot.user_data or {}).get("bank_name") or ""
            if fallback:
                names.append(fallback)
        return names

    def _submission_allowed(self):
        from core.licensing import can_run
        return submission_policy(normalize_config(DBHandler.get_config()), self._preferences())[0] and can_run()

    def _sniping_tick(self, cfg):
        from bank_quota_sniper import submit_quota_attempt, form_ready
        bot, owner = self.bot, self.owner
        enabled, interval = submission_policy(cfg, self._preferences())
        if not enabled or self._submitting or self._requests:
            return 'waiting'
        now = self._monotonic()
        if self._last_attempt_completed is not None:
            self._next_submit = self._last_attempt_completed + interval
        if now < self._next_submit:
            return 'waiting'
        expected_mode = DBHandler.get_runtime(bot.nid).get('selected_mode') or self._last_observation.get('selected_mode')
        if not form_ready(owner.page, expected_mode):
            self._prepared = self._submission_ready = False
            self.transition(State.BANK_OPTIONS_READY, 'reapplying_applicant_selections')
            return 'waiting'
        from core.licensing import can_run
        if not can_run(force=True):
            owner.paused = True
            self.transition(State.PAUSED, 'license_not_valid')
            return 'waiting'
        self._submitting = True
        self._attempt_serial = self._response_serial
        self.transition(State.FINAL_SUBMIT_IN_PROGRESS, 'automatic_final_submission')
        DBHandler.update_runtime(bot.nid, submission_in_flight=True)
        try:
            result = submit_quota_attempt(bot, owner.page, owner.cancellation_token,
                                          timeout=self._submit_response_timeout, blocked=lambda: self._blocked,
                                          expected_mode=expected_mode, allowed=self._submission_allowed)
        finally:
            self._submitting = False
            DBHandler.update_runtime(bot.nid, submission_in_flight=bool(self._requests))
        if result != 'option_selected_waiting_user':
            self._last_attempt_completed = self._monotonic()
            self._next_submit = self._last_attempt_completed + interval
        self._next_check = self._next_submit
        bot.save_checkpoint('STEP_3_BANK_SELECT', page=owner.page, last_submit_outcome=result)
        if result == 'stopped':
            return 'stopped'
        _, latest = self.observe()
        self._last_selection = self._selection_signature(latest)
        if hasattr(self, 'capacity_monitor'):
            self.capacity_monitor.current_interval = interval
            self.capacity_monitor.record_check_attempt('success' if result == 'submitted_confirmed' else result, latest.get('error_message') or result)
        if result == 'submitted_confirmed':
            self.transition(State.REGISTRATION_CONFIRMED)
            bot.set_runtime_state('SUCCESS', State.REGISTRATION_CONFIRMED, browser_status='OPEN')
            DBHandler.update_runtime(bot.nid, next_refresh=0)
            return 'confirmed'
        self._awaiting_result = result == 'submit_rejected'
        self._unknown_since = time.time()
        self._unknown_error = latest.get('error_message')
        state = {'capacity_full': State.CAPACITY_FULL, 'reserve_confirmed': State.RESERVE_PENDING,
                 'option_selected_waiting_user': State.FINAL_SUBMIT_READY,
                 'session_expired': State.SESSION_EXPIRED}.get(result, State.MANUAL_ACTION_REQUIRED)
        if result == 'access_denied':
            self._blocked = True
        self.transition(state, result)
        enabled, interval = submission_policy(normalize_config(DBHandler.get_config()), self._preferences())
        retry = result == 'capacity_full' and enabled
        DBHandler.update_runtime(bot.nid, last_refresh=time.time(), next_refresh=time.time() + interval if retry else 0,
                                 check_interval=interval, check_method='native_submit_retry')
        return 'waiting'

    def tick(self, force=False):
        owner, bot = self.owner, self.bot
        owner.assert_owner()
        with owner.operation_lock:
            if self._submitting:
                return "waiting"
            if owner.cancellation_token.is_set():
                return "stopped"
            if self.state == State.REGISTRATION_CONFIRMED:
                return 'confirmed'
            commands = []
            while not owner.commands.empty():
                commands.append(owner.commands.get_nowait())
            for command in commands:
                if command == "reauthenticate":
                    owner.bank_page_active = False
                    return "reauthenticate"
                if command == "pause":
                    self._state_before_pause = self.state
                    owner.paused = True
                elif command == "resume":
                    owner.paused = False
                    if self.state == State.PAUSED and self._state_before_pause:
                        self.transition(self._state_before_pause)
                    force = True
                elif command == "repair_checkpoint":
                    from bot_select_bank import repair_saved_pages, _applicant_archive_dir
                    from pathlib import Path
                    folder = Path(_applicant_archive_dir(bot))
                    repair_saved_pages(str(folder.parent), only_folder=folder.name)
                    bot.save_checkpoint("STEP_3_BANK_SELECT", page=owner.page,
                                        bank_state=self.state, session_id=owner.session_id,
                                        session_active=owner.bank_page_active)

            # Neutralize CBI unload logout trap immediately upon entering Step 3
            try:
                from core.step3_bank_page import Step3BankPage
                Step3BankPage().disable_logout_trap(owner.page)
            except Exception:
                pass

            # Always pump/validate the current page, even while paused.
            stage, data = self.observe()
            if stage == "closed":
                return "stopped"
            if self._observation_issue:
                self.transition(State.MANUAL_ACTION_REQUIRED, self._observation_issue)
                return "waiting"
            if self.state == State.SESSION_EXPIRED or stage == State.SESSION_EXPIRED or data.get("state") == State.SESSION_EXPIRED or bot.consume_recovery_flag("session_expired") or bot.consume_recovery_flag("otp_expired"):
                owner.bank_page_active = False
                owner.validated_at = 0
                self.transition(State.SESSION_EXPIRED, "live_dom_requires_authentication")
                return "reauthenticate"

            code = _extract_confirmed_submission(bot, owner.page) if stage == "DONE" else data.get("tracking_code")
            if code:
                DBHandler.save_success_data(bot.nid, code)
                if hasattr(self, "capacity_monitor"):
                    self.capacity_monitor.tracking_code = str(code)
                    self.capacity_monitor.record_check_attempt("success", "ثبت قطعی سامانه تأیید شد.")
                self.transition(State.REGISTRATION_CONFIRMED)
                bot.set_runtime_state("SUCCESS", State.REGISTRATION_CONFIRMED, browser_status="OPEN")
                return "confirmed"
            if owner.paused:
                self.transition(State.PAUSED)
                return "waiting"
            if stage != "STEP_3_BANK_SELECT":
                owner.validated_at = 0
                if stage in {"STEP_1_ENTRY_FORM", "STEP_2_OTP"}:
                    self.transition(State.SESSION_EXPIRED, "navigated_away_to_entry_stage")
                    return "reauthenticate"
                self.transition(State.MANUAL_ACTION_REQUIRED, "unrecognized_live_dom")
                return "waiting"

            # ── UNIFIED 7-PIECE STEP 3 EXECUTION ──────────────────────────
            res = self.evaluate_and_execute_bank_logic(owner.page)
            if res in {"confirmed", "cycle_otp", "reauthenticate", "stopped"}:
                return res

            bot.clear_otp_locks()
            bot.consume_recovery_flag("otp_expired")
            bot.consume_recovery_flag("captcha_invalid")
            if bot.consume_recovery_flag("waf_blocked") or data.get("error_kind") == "waf_blocked":
                self._blocked = True
            if self._blocked:
                self.transition(State.MANUAL_ACTION_REQUIRED, "security_or_rate_limit")
                return "waiting"

            cp = DBHandler.get_checkpoint(bot.nid)
            if cp.get('reserve_confirmed') or extract_confirmed_reservation(owner.page):
                bot.save_checkpoint('STEP_3_BANK_SELECT', page=owner.page, reserve_confirmed=True, final_submit_status='reserve_confirmed')
                self.transition(State.RESERVE_PENDING, 'reservation_confirmed_no_resubmit')
                DBHandler.update_runtime(bot.nid, next_refresh=0)
                return 'waiting'

            cfg = normalize_config(DBHandler.get_config())
            prefs = self._preferences()
            enabled, interval = submission_policy(cfg, prefs)
            bot.settings.update(cfg)
            bot.settings['auto_final_submit'] = enabled
            bot.settings['final_submit'] = enabled
            DBHandler.update_runtime(bot.nid, submission_auto_enabled=enabled, check_interval=interval,
                                     submission_in_flight=bool(self._requests))
            if hasattr(self, 'capacity_monitor'):
                self.capacity_monitor.current_interval = interval
            if self._requests:
                self._awaiting_result = True
                self.transition(State.FINAL_SUBMIT_IN_PROGRESS, 'request_in_flight')
                return 'waiting'
            if self._awaiting_result:
                response = self._last_final_response
                fresh = response.get('serial', 0) > self._attempt_serial and response.get('status') == 200
                response_data = response.get('data', {}) if fresh else {}
                response_nid = str(response_data.get('applicant_nid') or '')
                if response_nid and response_nid != str(bot.nid):
                    self.transition(State.MANUAL_ACTION_REQUIRED, 'applicant_identity_mismatch')
                    return 'waiting'
                if response_data.get('state') == State.SESSION_EXPIRED:
                    self.transition(State.SESSION_EXPIRED, 'session_expired_in_final_response')
                    return 'reauthenticate'
                if response_data.get('error_kind') == 'waf_blocked':
                    self._blocked = True
                    self.transition(State.MANUAL_ACTION_REQUIRED, 'security_or_rate_limit')
                    return 'waiting'
                if response_data.get('reserve_confirmed'):
                    bot.save_checkpoint('STEP_3_BANK_SELECT', page=owner.page, reserve_confirmed=True, final_submit_status='reserve_confirmed')
                    self._awaiting_result = False
                    self.transition(State.RESERVE_PENDING, 'reservation_confirmed_no_resubmit')
                    return 'waiting'
                if response_data.get('tracking_code'):
                    DBHandler.save_success_data(bot.nid, response_data['tracking_code'])
                    self.transition(State.REGISTRATION_CONFIRMED)
                    bot.set_runtime_state('SUCCESS', State.REGISTRATION_CONFIRMED, browser_status='OPEN')
                    DBHandler.update_runtime(bot.nid, next_refresh=0)
                    return 'confirmed'
                dialog_capacity = (getattr(bot, '_last_dialog_ts', 0) > self._unknown_since
                                   and getattr(bot, '_last_dialog_action', '') == 'bank_capacity_full')
                dom_capacity = (data.get('state') == State.CAPACITY_FULL and data.get('error_message') != self._unknown_error
                                and response.get('status', 200) < 400)
                if response_data.get('state') == State.CAPACITY_FULL or dialog_capacity or dom_capacity:
                    self._awaiting_result = False
                    self._last_attempt_completed = self._monotonic()
                    self._next_submit = self._last_attempt_completed + interval
                    self.transition(State.CAPACITY_FULL, 'late_capacity_rejection')
                else:
                    self.transition(State.MANUAL_ACTION_REQUIRED, 'previous_submission_requires_reconciliation')
                    return 'waiting'

            relevant = {key: prefs.get(key) for key in ('priority_banks', 'bank_priority', 'banks', 'primary_bank', 'alternative_banks',
                         'bank_mode', 'mode', 'state', 'city', 'priority_branches', 'branch_code', 'allow_reserve', 'stopped_banks')}
            relevant['default_bank_mode'] = cfg.get('default_bank_mode')
            signature = json.dumps(relevant, ensure_ascii=False, sort_keys=True)
            changed = signature != self._last_preferences
            self._last_preferences = signature
            if changed:
                self._prepared = False
            observation = self._selection_signature(data)
            if self._prepared and observation != getattr(self, '_last_selection', None):
                self._prepared = False
            if self.state == State.PREFERRED_BANK_NOT_FOUND:
                # ZERO CAMPING ON STEP 3:
                # As proven by CBI server field tests and HTML analysis, bank quotas are provincial
                # and cached in __VIEWSTATE on initial page entry (!IsPostBack). Staying on Step 3
                # never fetches new bank quotas from the database.
                # When priority bank is not available, we immediately disarm the unload trap
                # and cycle back to the OTP/Captcha form to force the CBI server to reload
                # the fresh provincial quota list from database.

                target_names = self._get_target_bank_names()
                target_display = " / ".join(target_names) or "بانک اولویت‌دار متقاضی"

                try:
                    from core.step3_bank_page import Step3BankPage
                    Step3BankPage().disable_logout_trap(owner.page)
                except Exception:
                    pass

                # Check if OTP has genuinely expired (> 115s from entry)
                try:
                    otp_entry_time = float(getattr(bot, "_otp_entry_time", 0) or 0)
                except Exception:
                    otp_entry_time = 0.0
                try:
                    otp_validity = float(bot.settings.get("otp_validity_seconds", 115))
                except Exception:
                    otp_validity = 115.0

                otp_expired_by_time = otp_entry_time > 0 and (time.time() - otp_entry_time) >= otp_validity
                has_otp_expired_flag = bot.consume_recovery_flag("otp_expired") or bot.consume_recovery_flag("session_expired")

                if otp_expired_by_time or has_otp_expired_flag:
                    bot.log(f"⏱️ مهلت پیامک جاری ({int(otp_validity)} ثانیه) به پایان رسید؛ بازگشت به مرحله ۱ جهت اخذ پیامک جدید...", "warning", owner.page)
                    owner.bank_page_active = False
                    self.transition(State.SESSION_EXPIRED, "otp_expired_refresh")
                    return "reauthenticate"

                elapsed_sec = int(time.time() - otp_entry_time) if otp_entry_time > 0 else 0
                rem_sec = max(0, int(otp_validity - elapsed_sec))
                bot.log(f"⚡ بانک ({target_display}) در سهمیه فعلی استان نیست؛ رفرش فوری و بازگشت به مرحله پیامک/کپچا (اعتبار پیامک: {rem_sec} ثانیه) جهت کوئری تازه دیتابیس سرور...", "info", owner.page)

                # Disarm trap and reload page to go to OTP stage
                try:
                    from core.step3_bank_page import Step3BankPage
                    Step3BankPage().disable_logout_trap(owner.page)
                    owner.bank_page_active = False
                    owner.page.reload()
                except Exception as e:
                    logger.warning(f"Reload failed: {e}")

                self._prepared = False
                self.transition(State.BANK_OPTIONS_LOADING, "cycled_back_to_otp")
                return "cycle_otp"

            if not self._prepared:
                self.transition(State.BANK_OPTIONS_READY)
                result = process_bank_selection_v2(bot, owner.page, owner.cancellation_token, capacity_sniping=True)
                if owner.cancellation_token.is_set():
                    return 'stopped'
                self._prepared = result not in {'no_match', 'waiting'}
                self._submission_ready = result in {'option_selected_waiting_user', 'capacity_full'}
                _, latest = self.observe()
                self._last_selection = self._selection_signature(latest)
                mapped = {'capacity_full': State.CAPACITY_FULL, 'no_match': State.PREFERRED_BANK_NOT_FOUND,
                          'waiting': State.BANK_OPTIONS_LOADING, 'option_selected_waiting_user': State.FINAL_SUBMIT_READY,
                          'submitted_confirmed': State.REGISTRATION_CONFIRMED, 'already_submitted': State.REGISTRATION_CONFIRMED,
                          'reserve_confirmed': State.RESERVE_PENDING}
                target = mapped.get(result, State.MANUAL_ACTION_REQUIRED)
                if result == 'option_selected_waiting_user' and latest.get('state') == State.CAPACITY_FULL:
                    target = State.CAPACITY_FULL
                self.transition(target, getattr(bot, '_bank_selection_issue', '') or latest.get('error_message') or result)
                if result in {'submitted_confirmed', 'already_submitted'}:
                    return 'confirmed'

                if result == 'no_match':
                    poll_interval = max(3.0, float(bot.settings.get("bank_list_refresh_seconds", bot.settings.get("bank_refresh_interval", 5.0))))
                    now = self._monotonic()
                    self._next_bank_poll = now + poll_interval
                    if not getattr(self, "_bank_not_found_since", 0):
                        self._bank_not_found_since = now
                    self._prepared = True  # Keep prepared=True so tick() waits for _next_bank_poll!
                    DBHandler.update_runtime(
                        bot.nid,
                        status="پایش بانک‌ها",
                        current_step="PREFERRED_BANK_NOT_FOUND",
                        last_error=f"بانک اولویت‌دار در فهرست باز نیست؛ در حال پایش مداوم (بررسی بعدی در {int(poll_interval)} ثانیه)...",
                        next_refresh=time.time() + poll_interval,
                        check_interval=poll_interval,
                        check_method="live_poll",
                    )
                else:
                    self._bank_not_found_since = 0

            if enabled and self._submission_ready:
                return self._sniping_tick(cfg)
            if self.state != State.PREFERRED_BANK_NOT_FOUND:
                DBHandler.update_runtime(bot.nid, next_refresh=0, check_method='live_dom_only')
            return 'waiting' 

    def perform_warm_tab_handover(self) -> bool:
        """
        Piece 6: Zero-Downtime Warm Tab Handover.
        When OTP expires or session times out:
        1. Opens new tab in browser context (context.new_page())
        2. Navigates to SelTasCase.aspx (Step 1)
        3. Fills National ID, solves primary captcha, requests fresh OTP
        4. Closes old expired tab safely
        5. Re-binds owner.page and bot.page to new_page
        """
        bot, owner = self.bot, self.owner
        old_page = owner.page
        try:
            context = getattr(owner, "context", None)
            if context is None:
                context = getattr(old_page, "context", None)
            if context is None:
                return False
            new_page = context.new_page()
            bot.log("🌐 باز کردن تب جدید موازی جهت تمدید سشن و اخذ پیامک تازه (Warm Tab Handover)...", "info", new_page)

            entry_url = getattr(bot, "entry_url", "https://ve.cbi.ir/SelTasCase.aspx")
            new_page.goto(entry_url, wait_until="commit", timeout=15000)

            from browser_actions import BrowserActions
            BrowserActions.fast_fill(new_page, "#ctl00_ContentPlaceHolder1_tbIDNo, input[name$='tbIDNo']", str(bot.nid))

            from bot_select_captcha import solve_captcha_step
            captcha_in = BrowserActions.find_captcha_input(new_page)
            btn_sel = "#ctl00_ContentPlaceHolder1_btnCheck, input[type='submit']"
            if captcha_in and captcha_in.is_visible():
                solve_captcha_step(bot, new_page, captcha_in, btn_sel)
                new_page.wait_for_timeout(500)

            bot.log("📨 درخواست پیامک جدید در تب تازه با موفقیت ارسال شد؛ بستن تب منقضی‌شده قبلی...", "success", new_page)
            try:
                old_page.close()
            except Exception:
                pass

            owner.page = new_page
            bot.page = new_page
            owner.bank_page_active = False
            return True
        except Exception as e:
            logger.warning(f"Warm tab handover failed: {e}")
            return False

    def _attempt_submission_with_fallbacks(self, step3, page, matched_option, target_display: str, is_reserve: bool = False):
        """
        Attempts to submit bank selection.
        If non-presential quota full error occurs, automatically switches to in-person (rbtnHozori)
        and re-submits.
        Returns tracking code string if confirmed, or None.
        """
        bot = self.bot
        try:
            step3.select_bank_option(page, matched_option.value)
            step3.click_submit_btn(page)
            res = step3.extract_tracking_or_error(page)

            if res.get("is_success") and res.get("tracking_code"):
                return res["tracking_code"]

            # 1. Fallback: Non-presential capacity full -> switch to in-person (حضوری)
            if res.get("is_non_presential_full"):
                bot.log(f"⚠️ ظرفیت غیرحضوری {target_display} تکمیل شده؛ تغییر خودکار به گزینه حضوری...", "warning", page)
                if step3.switch_to_in_person_mode(page):
                    bot.log(f"🔄 حالت ثبت به حضوری تغییر یافت. تلاش مجدد برای ثبت نهایی {target_display}...", "info", page)
                    step3.select_bank_option(page, matched_option.value)
                    step3.click_submit_btn(page)
                    res = step3.extract_tracking_or_error(page)
                    if res.get("is_success") and res.get("tracking_code"):
                        return res["tracking_code"]

            err_msg = res.get("error_msg") or ""
            if err_msg:
                bot.log(f"⚠️ پیام سامانه پس از ارسال: {err_msg}", "warning", page)
            return None
        except Exception as e:
            bot.log(f"❌ خطا در پردازش ارسال فرم بانک: {e}", "error", page)
            return None

    def evaluate_and_execute_bank_logic(self, page) -> str:
        """
        Piece 2, 3, 4, 5:
        1. Read all options from ddlBankName via Step3BankPage
        2. Differentiate active quota, reserve quota, and waiting count
        3. Smart deduplication: check if banks changed before saving snapshot & updating banks_seen.json
        4. Broadcast formatted radar notification to Telegram
        5. Match applicant priority banks using PersianBankMatcher
        6. Check auto_final_submit & allow_reserve rules:
           - Active quota:
             - If auto_final_submit is True -> select bank, wait for UpdatePanel2, click btnSave -> return 'confirmed'
             - If auto_final_submit is False -> do NOT submit, reload & return 'cycle_otp'
           - Reserve quota:
             - If allow_reserve is True AND auto_final_submit is True -> select bank, wait for UpdatePanel2, click btnSave -> return 'confirmed'
             - Else -> do NOT submit, reload & return 'cycle_otp'
           - No match:
             - reload & return 'cycle_otp' (or 'reauthenticate' if OTP expired)
        """
        bot, owner = self.bot, self.owner
        from core.step3_bank_page import Step3BankPage
        from core.bank_matcher import PersianBankMatcher, BankOptionInfo
        from core.telegram_notifier import TelegramNotifierService
        from database import DBHandler

        step3 = Step3BankPage()
        step3.disable_logout_trap(page)

        # 1. Read live options
        options = step3.read_live_bank_options(page)
        prov, city = step3.get_location_names(page)

        active_banks = [
            {"bank_name": o.clean_title or o.text, "waiting_count": o.waiting_count, "value": o.value}
            for o in options if o.has_active_quota
        ]
        reserve_banks = [
            {"bank_name": o.clean_title or o.text, "waiting_count": o.waiting_count, "value": o.value}
            for o in options if o.is_reserve
        ]

        # 2. Smart deduplicated archive (Piece 2)
        if step3.should_save_page(bot, options):
            step3.archive_banks_seen_nonblocking(bot, options, page=page)
            # 3. Format and dispatch beautiful radar notification (Piece 3)
            try:
                notifier = TelegramNotifierService()
                msg = notifier.format_beautiful_radar_message(
                    province_name=prov,
                    city_name=city,
                    active_banks=active_banks,
                    reserve_banks=reserve_banks,
                    applicant_nid=str(bot.nid),
                    action_status="پایش فعال ظرفیت"
                )
                admin_cid = notifier.get_admin_chat_id()
                if admin_cid:
                    notifier.send_raw_telegram(admin_cid, msg)
                channel_cid = notifier.get_channel_id()
                if channel_cid and channel_cid != admin_cid:
                    notifier.send_raw_telegram(channel_cid, msg)
            except Exception as e:
                logger.warning(f"Radar telegram dispatch failed: {e}")

        # 4. Check applicant priorities and toggles (Piece 4)
        cfg = DBHandler.get_config()
        auto_final_submit = bool(cfg.get("auto_final_submit", bot.settings.get("auto_final_submit", True)))
        prefs = self._preferences()
        p_banks = prefs.get("priority_banks") or prefs.get("banks") or []

        matched_option = None
        target_allow_reserve = False
        target_display = ""

        if isinstance(p_banks, list) and p_banks:
            for item in p_banks:
                p_name = ""
                allow_res = False
                if isinstance(item, dict):
                    p_name = item.get("name") or item.get("bank_name") or ""
                    allow_res = bool(item.get("allow_reserve", False))
                elif isinstance(item, str):
                    p_name = item
                if not p_name:
                    continue
                match = PersianBankMatcher.match_best_bank(p_name, options, allow_reserve=allow_res)
                if match:
                    matched_option = match
                    target_allow_reserve = allow_res
                    target_display = p_name
                    break

        if not matched_option:
            fallback = prefs.get("primary_bank") or prefs.get("bank_name") or (bot.user_data or {}).get("bank_name") or ""
            if fallback:
                target_allow_reserve = bool(prefs.get("allow_reserve", False))
                matched_option = PersianBankMatcher.match_best_bank(fallback, options, allow_reserve=target_allow_reserve)
                target_display = fallback

        # Active Quota branch
        if matched_option and matched_option.has_active_quota:
            bot.log(f"🎯 بانک اولویت‌دار ({target_display}) با ظرفیت قطعی شناسایی شد!", "success", page)
            if auto_final_submit:
                bot.log(f"🚀 تیک ثبت نهایی فعال است؛ انتخاب بانک ({target_display}) و ارسال فرم نهایی...", "info", page)
                tracking_code = self._attempt_submission_with_fallbacks(step3, page, matched_option, target_display, is_reserve=False)
                if tracking_code:
                    DBHandler.save_success_data(bot.nid, tracking_code)
                    self.transition(State.REGISTRATION_CONFIRMED)
                    bot.set_runtime_state("SUCCESS", State.REGISTRATION_CONFIRMED, browser_status="OPEN")
                    bot.log(f"🎉 ثبت نهایی با موفقیت انجام شد! کد رهگیری: {tracking_code}", "success", page)
                    return "confirmed"
            else:
                bot.log(f"⏸️ بانک اولویت‌دار ({target_display}) دارای ظرفیت است اما تیک «ثبت نهایی» غیرفعال است؛ ثبت انجام نشد (ادامه پایش)...", "warning", page)

        # Reserve Quota branch
        elif matched_option and matched_option.is_reserve:
            if target_allow_reserve and auto_final_submit:
                bot.log(f"📝 بانک اولویت‌دار ({target_display}) در حالت رزرو است و هر دو تیک (رزرو و ثبت نهایی) فعال هستند؛ ثبت درخواست رزرو...", "info", page)
                tracking_code = self._attempt_submission_with_fallbacks(step3, page, matched_option, target_display, is_reserve=True)
                if tracking_code:
                    DBHandler.save_success_data(bot.nid, tracking_code)
                    self.transition(State.REGISTRATION_CONFIRMED)
                    bot.set_runtime_state("SUCCESS", State.REGISTRATION_CONFIRMED, browser_status="OPEN")
                    bot.log(f"🎉 ثبت رزرو با موفقیت تایید شد! کد رهگیری: {tracking_code}", "success", page)
                    return "confirmed"
            else:
                bot.log(f"⏸️ بانک ({target_display}) در حالت رزرو است اما شرایط ثبت (تیک رزرو و ثبت نهایی) برقرار نیست؛ ادامه پایش...", "info", page)

        # Piece 5: Zero-camping OTP Cycle or Warm Handover
        try:
            otp_entry_time = float(getattr(bot, "_otp_entry_time", 0) or 0)
        except Exception:
            otp_entry_time = 0.0
        try:
            otp_validity = float(bot.settings.get("otp_validity_seconds", 115))
        except Exception:
            otp_validity = 115.0

        otp_expired = (otp_entry_time > 0 and (time.time() - otp_entry_time) >= otp_validity) or bot.consume_recovery_flag("otp_expired")
        if otp_expired:
            bot.log(f"⏱️ مهلت پیامک جاری ({int(otp_validity)} ثانیه) به پایان رسید؛ انتقال به تب تازه (Warm Handover)...", "warning", page)
            self.perform_warm_tab_handover()
            self.transition(State.SESSION_EXPIRED, "otp_expired_warm_handover")
            return "reauthenticate"

        elapsed_sec = int(time.time() - otp_entry_time) if otp_entry_time > 0 else 0
        rem_sec = max(0, int(otp_validity - elapsed_sec))
        
        # 5-second polling interval when preferred bank is not available
        poll_interval = float(bot.settings.get("bank_refresh_interval") or 5.0)
        bot.log(f"⏳ بانک مدنظر در سهمیه فعلی نیست؛ پایش مجدد ظرفیت هر {int(poll_interval)} ثانیه تا پایان مهلت پیامک (اعتبار باقی‌مانده: {rem_sec} ثانیه)...", "info", page)
        
        try:
            page.wait_for_timeout(int(poll_interval * 1000))
        except Exception:
            pass

        # Check if OTP expired during the 5s wait
        if otp_entry_time > 0 and (time.time() - otp_entry_time) >= otp_validity:
            bot.log(f"⏱️ مهلت پیامک جاری ({int(otp_validity)} ثانیه) به پایان رسید؛ انتقال به تب تازه (Warm Handover)...", "warning", page)
            self.perform_warm_tab_handover()
            self.transition(State.SESSION_EXPIRED, "otp_expired_warm_handover")
            return "reauthenticate"

        # Disarm logout trap and trigger native CBI server refresh
        step3.disable_logout_trap(page)
        from bot_select_bank import trigger_bank_capacity_refresh
        refreshed = trigger_bank_capacity_refresh(bot, page)
        if not refreshed:
            owner.bank_page_active = False
            try:
                page.reload()
            except Exception:
                pass
            self.transition(State.BANK_OPTIONS_LOADING, "cycled_back_to_otp")
            return "cycle_otp"
        
        return "waiting"

    def run(self):
        self.owner.bank_page_active = True
        self.transition(State.BANK_PAGE_DETECTED)
        try:
            while not self.owner.cancellation_token.is_set():
                try:
                    result = self.tick()
                    if result in {"stopped", "confirmed", "reauthenticate", "cycle_otp"}:
                        return result
                except Exception as exc:
                    # A snapshot/DOM failure must never unwind into browser teardown/retry.
                    self.owner.validated_at = 0
                    self._blocked = True
                    self.transition(State.MANUAL_ACTION_REQUIRED, type(exc).__name__)
                try:
                    self.owner.page.wait_for_timeout(200)
                except Exception:
                    return "stopped"
            return "stopped"
        finally:
            if hasattr(self, "capacity_monitor"):
                self.capacity_monitor.cleanup()
