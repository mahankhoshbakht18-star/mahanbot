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
            interval = float(cfg.get("bank_capacity_check_interval", 5.0))
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
        for name, selector in (("selected_branch", "#ctl00_ContentPlaceHolder1_ddlBranch"), ("city_value", "#ctl00_ContentPlaceHolder1_ddlCity")):
            control = page.locator(selector)
            data[name] = control.first.input_value() if control.count() and control.first.is_visible() else ""
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

    def _submission_allowed(self):
        return submission_policy(normalize_config(DBHandler.get_config()), self._preferences())[0]

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

            # Always pump/validate the current page, even while paused.
            stage, data = self.observe()
            if stage == "closed":
                return "stopped"
            if self._observation_issue:
                self.transition(State.MANUAL_ACTION_REQUIRED, self._observation_issue)
                return "waiting"
            if self.state == State.SESSION_EXPIRED or stage == State.SESSION_EXPIRED or data.get("state") == State.SESSION_EXPIRED or bot.consume_recovery_flag("session_expired"):
                owner.bank_page_active = False
                owner.validated_at = 0
                self.transition(State.SESSION_EXPIRED, "live_dom_requires_authentication")
                return "waiting"

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
                self.transition(State.MANUAL_ACTION_REQUIRED, "unrecognized_live_dom")
                return "waiting"

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
                    return 'waiting'
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
            if not self._prepared:
                self.transition(State.BANK_OPTIONS_READY)
                result = process_bank_selection_v2(bot, owner.page, owner.cancellation_token, capacity_sniping=True)
                if owner.cancellation_token.is_set():
                    return 'stopped'
                self._prepared = True
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
            if enabled and self._submission_ready:
                return self._sniping_tick(cfg)
            DBHandler.update_runtime(bot.nid, next_refresh=0, check_method='live_dom_only')
            return 'waiting'

    def run(self):
        self.owner.bank_page_active = True
        self.transition(State.BANK_PAGE_DETECTED)
        try:
            while not self.owner.cancellation_token.is_set():
                try:
                    result = self.tick()
                    if result in {"stopped", "confirmed", "reauthenticate"}:
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
