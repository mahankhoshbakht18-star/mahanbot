"""One audited workflow for the live loan browser; legacy stage names are adapters."""
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit, urlunsplit

from database import DBHandler
from otp_form_detector import detect_otp_form, normalize_text

logger = logging.getLogger('mahanbot.workflow')

STEP1 = {'STEP1_IDENTIFICATION', 'STEP1_CAPTCHA_REQUIRED'}
STEP2 = {'STEP2_OTP_REQUIRED', 'STEP2_CAPTCHA_REQUIRED', 'STEP2_READY_TO_CONTINUE',
         'OTP_REAUTH_REQUIRED', 'WAITING_FOR_NEW_OTP'}
STEP3 = {'STEP3_ACTIVE', 'STEP3_CHECKING_BANKS', 'STEP3_QUOTA_AVAILABLE', 'STEP3_NO_MATCH'}
FINAL = {'FINAL_SUBMIT_PENDING', 'FINAL_SUBMITTING', 'FINAL_SUBMIT_CONFIRMED'}
CONTROL = {'UNKNOWN', 'RELOAD_PENDING', 'PAGE_REDETECTING', 'SESSION_EXPIRED',
           'SESSION_RECOVERING', 'PAUSED', 'FAILED'}
STATES = STEP1 | STEP2 | STEP3 | FINAL | CONTROL
# Observation and cancellation can interrupt any nonterminal state. Actions remain
# constrained by their phase and by the durable final-submission guard.
TRANSITIONS = {s: {'PAGE_REDETECTING', 'PAUSED', 'FAILED', 'SESSION_EXPIRED'} for s in STATES}
for targets in TRANSITIONS.values():
    targets.add('FINAL_SUBMIT_PENDING')
for phase, next_phase in ((STEP1, STEP2), (STEP2, STEP3), (STEP3, STEP2 | STEP1 | FINAL)):
    for state in phase:
        TRANSITIONS[state] |= phase | next_phase | {'RELOAD_PENDING', 'SESSION_RECOVERING'}
TRANSITIONS['PAGE_REDETECTING'] = STATES
for state in CONTROL:
    TRANSITIONS[state] |= STEP1 | STEP2 | STEP3 | CONTROL
TRANSITIONS['FINAL_SUBMIT_PENDING'] |= {'FINAL_SUBMITTING', 'FINAL_SUBMIT_CONFIRMED'}
TRANSITIONS['FINAL_SUBMITTING'] |= {'FINAL_SUBMIT_PENDING', 'FINAL_SUBMIT_CONFIRMED'}
TRANSITIONS['FINAL_SUBMIT_CONFIRMED'] = {'FINAL_SUBMIT_CONFIRMED'}

STAGE_MAP = {'STEP_1_ENTRY_FORM': 'STEP1_IDENTIFICATION', 'STEP_2_OTP': 'STEP2_OTP_REQUIRED',
             'STEP_3_BANK_SELECT': 'STEP3_ACTIVE', 'DONE': 'FINAL_SUBMIT_CONFIRMED',
             'BROWSER_CLOSED': 'FAILED', 'WAF_BLOCKED': 'PAUSED', 'FIREWALL_CHALLENGE': 'PAUSED',
             'SESSION_EXPIRED': 'SESSION_EXPIRED'}


@dataclass
class WorkflowDetection:
    state: str = 'UNKNOWN'
    confidence: float = 0.0
    url: str = ''
    detected_fields: dict = field(default_factory=dict)
    visible_errors: list = field(default_factory=list)
    session_status: str = 'unknown'
    reason: str = 'insufficient_live_dom'
    elapsed_ms: float = 0.0


def detect_current_workflow_state(page, bot):
    started = time.perf_counter()
    parts = urlsplit(str(getattr(page, 'url', '')))
    result = WorkflowDetection(url=urlunsplit((parts.scheme, parts.netloc, parts.path, '', '')))
    from core.stage_detector import detect_current_stage
    detection = detect_current_stage(page, bot=bot)
    stage = detection.legacy
    result.state = STAGE_MAP.get(stage, 'UNKNOWN')
    if stage == 'SESSION_EXPIRED':
        result.state = 'SESSION_EXPIRED'
    result.confidence = detection.confidence if result.state != 'UNKNOWN' else 0
    result.reason = 'live_dom:' + detection.stage + ':' + (detection.reason or stage)
    if stage == 'DONE':
        from bot_select_bank import _extract_confirmed_submission
        if not _extract_confirmed_submission(bot, page):
            result.state, result.confidence, result.reason = 'UNKNOWN', 0, 'unverified_tracking_message'
    if stage == 'STEP_2_OTP':
        form = detect_otp_form(page)
        result.detected_fields = form.public_fields()
        result.reason = form.reason
        result.session_status = 'authentication_required'
        if 'captcha' in form.fields:
            result.state = 'STEP2_CAPTCHA_REQUIRED'
        if form.resolve('otp') is not None and form.resolve('continue') is not None:
            if all(form.scope.locator(form.fields[r]['selector']).input_value(timeout=300).strip()
                   for r in ('otp', 'captcha') if r in form.fields):
                result.state = 'STEP2_READY_TO_CONTINUE'
    elif stage == 'STEP_3_BANK_SELECT':
        result.session_status = 'live_dom_observed'
    elif stage == 'STEP_1_ENTRY_FORM':
        result.session_status = 'authentication_required'
        try:
            captcha = page.locator('input[id*="tbCaptcha"]:visible, input[name*="tbCaptcha"]:visible')
            if captcha.count():
                result.state = 'STEP1_CAPTCHA_REQUIRED'
        except Exception:
            pass
    elif stage == 'BROWSER_CLOSED':
        result.session_status, result.reason = 'closed', 'target_closed'
    elif stage in {'WAF_BLOCKED', 'FIREWALL_CHALLENGE'}:
        result.session_status, result.reason = 'blocked', 'security_or_rate_limit'
    elif stage == 'SESSION_EXPIRED':
        result.session_status = 'expired'
        result.visible_errors = ['session_expired']
    if not page.is_closed() and stage not in {'OFFLINE_SNAPSHOT', 'BROWSER_CLOSED'}:
        try:
            error = page.locator('[role="alert"]:visible, [id$="labErrMsg"]:visible, [id$="lblError"]:visible')
            words = normalize_text(' '.join(error.all_text_contents()))
            # Record categories only: a server message may echo personal input.
            if any(term in words for term in ('نشست منقضی', 'session expired')):
                result.state, result.session_status = 'SESSION_EXPIRED', 'expired'
                result.visible_errors = ['session_expired']
            elif 'منقضی' in words:
                result.visible_errors = ['otp_expired']
            elif any(term in words for term in ('اشتباه', 'نامعتبر')):
                result.visible_errors = ['invalid_input']
        except Exception:
            pass
    result.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return result


class LoanWorkflow:
    def __init__(self, bot):
        self.bot = bot
        self.state = 'UNKNOWN'
        self.reason = ''
        self.recovery_count = 0
        self.successful_reentries = 0
        self.last_step3_visit = 0.0
        self.trace = []
        self._bound_page = None
        self.http_status = 0
        self.last_navigation_method = 'GET'

    def bind(self, page):
        owner = getattr(self.bot, 'live_owner', None)
        if owner is not None:
            owner.assert_owner()
        if page is self._bound_page:
            return
        self.unbind()
        self._bound_page = page
        page.on('response', self._on_response)

    def unbind(self):
        if self._bound_page is not None:
            try:
                self._bound_page.remove_listener('response', self._on_response)
            except Exception:
                pass
            self._bound_page = None

    def _on_response(self, response):
        try:
            if response.request.is_navigation_request() or response.request.method == 'POST':
                self.http_status = response.status
                if response.request.is_navigation_request():
                    self.last_navigation_method = response.request.method
        except Exception:
            pass

    def transition(self, state, reason, detection=None):
        if state == self.state:
            return
        if state not in TRANSITIONS.get(self.state, set()):
            raise ValueError(f'Illegal workflow transition: {self.state}->{state}')
        owner = getattr(self.bot, 'live_owner', None)
        if owner is not None:
            owner.assert_owner()
        previous, self.state, self.reason = self.state, state, reason
        now = time.time()
        if state == 'STEP3_ACTIVE':
            self.last_step3_visit = now
        self.trace = (self.trace + [{'at': now, 'before': previous, 'after': state, 'reason': reason}])[-20:]
        fields = {'workflow_state': state, 'workflow_reason': reason, 'workflow_checked_at': now,
                  'workflow_previous_state': previous, 'workflow_session_id': getattr(owner, 'session_id', ''),
                  'browser_context_id': getattr(owner, 'context_id', ''), 'last_step3_visit': self.last_step3_visit,
                  'workflow_recovery_count': self.recovery_count,
                  'workflow_successful_reentries': self.successful_reentries}
        if detection:
            fields.update(workflow_detection_ms=detection.elapsed_ms,
                          session_status=detection.session_status)
        DBHandler.update_runtime(self.bot.nid, **fields)
        DBHandler.save_checkpoint(self.bot.nid, {'workflow_state': state, 'workflow_reason': reason,
                                                 'workflow_checked_at': now, 'workflow_trace': self.trace})
        logger.info(json.dumps({'event': 'workflow_transition', 'applicant': str(self.bot.nid)[:2] + '******' + str(self.bot.nid)[-2:],
                    'session_id': fields['workflow_session_id'], 'before': previous, 'after': state,
                    'reason': reason, 'detection_ms': detection.elapsed_ms if detection else None}))

    def redetect(self, page, reason='live_dom_observation'):
        self.bind(page)
        result = detect_current_workflow_state(page, self.bot)
        if self.http_status in {403, 429}:
            result.state, result.reason = 'PAUSED', 'security_or_rate_limit'
        elif self.http_status == 401:
            result.state, result.session_status = 'SESSION_EXPIRED', 'expired'
        elif self.http_status >= 500:
            result.state, result.reason = 'UNKNOWN', 'server_error'
        if result.state != self.state and self.state != 'FINAL_SUBMIT_CONFIRMED':
            if result.state not in TRANSITIONS[self.state]:
                self.transition('PAGE_REDETECTING', reason)
            self.transition(result.state, reason + ':' + result.reason, result)
        DBHandler.update_runtime(self.bot.nid, workflow_checked_at=time.time(),
                                 workflow_detection_ms=result.elapsed_ms)
        return result

    def recovery_allowed(self):
        cp = DBHandler.get_checkpoint(self.bot.nid)
        if cp.get('final_submit_status') in {'pending', 'unknown', 'confirmed', 'reserve_confirmed'}:
            self.transition('FINAL_SUBMIT_PENDING', 'previous_submission_requires_reconciliation')
            return False
        limit = max(1, int(self.bot.settings.get('workflow_max_recoveries', 3)))
        if self.recovery_count >= limit:
            self.transition('PAUSED', 'recovery_limit')
            return False
        self.recovery_count += 1
        return True

    def diagnostic(self, page, reason):
        try:
            evidence = asdict(detect_current_workflow_state(page, self.bot))
        except Exception as exc:
            evidence = {'state': 'UNKNOWN', 'reason': type(exc).__name__}
        evidence.update(error_kind=reason, recorded_at=time.time())
        DBHandler.save_checkpoint(self.bot.nid, {'workflow_diagnostic': evidence})
        return evidence

    def pump_commands(self):
        owner = getattr(self.bot, 'live_owner', None)
        if owner is None:
            return False
        owner.assert_owner()
        pending = []
        while not owner.commands.empty():
            command = owner.commands.get_nowait()
            if command == 'pause':
                owner.paused = True
                self.transition('PAUSED', 'user_pause')
            elif command == 'resume':
                owner.paused = False
                self.recovery_count = 0
                self.transition('PAGE_REDETECTING', 'user_resume')
            elif command == 'reauthenticate':
                owner.paused = False
                self.recovery_count = 0
                self.bot._set_recovery_flag('session_expired')
            else:
                pending.append(command)
        for command in pending:
            owner.commands.put(command)
        return owner.paused


def workflow_for(bot):
    workflow = getattr(bot, 'loan_workflow', None)
    if not isinstance(workflow, LoanWorkflow):
        workflow = bot.loan_workflow = LoanWorkflow(bot)
    return workflow
