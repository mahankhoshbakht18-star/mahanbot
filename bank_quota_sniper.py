"""One native submission in either mode. The owning controller schedules subsequent attempts."""
import time
import uuid
from urllib.parse import parse_qs

from bank_page_manager import BankPageState as State, analyze_bank_page_html, capture_dom_html
from bot_select_bank import _inspect_mode_controls, _is_online_label, _is_in_person_label, _extract_confirmed_submission, extract_confirmed_reservation
from database import DBHandler


def form_ready(page, expected_mode=None):
    """Validate live selections without changing the bank, city, mode or branch."""
    for selector in ('#ctl00_ContentPlaceHolder1_ddlCity', '#ctl00_ContentPlaceHolder1_ddlBankName'):
        control = page.locator(selector)
        if not control.count() or str(control.first.input_value()).strip() in {'', '0'}:
            return False
    controls = _inspect_mode_controls(page)
    def selected(predicate):
        return any(not control.get('disabled') and (
            (control.get('kind') in {'radio', 'checkbox'} and control.get('checked') and predicate(control.get('label', ''), control.get('id', '')))
            or (control.get('kind') == 'select' and any(str(option.get('v')) == str(control.get('value')) and predicate(option.get('t', '')) for option in control.get('options', [])))
        ) for control in controls)
    online = selected(_is_online_label)
    branch = page.locator('#ctl00_ContentPlaceHolder1_ddlBranch')
    branch_visible = branch.count() > 0 and branch.first.is_visible()
    in_person = selected(_is_in_person_label) or (not controls and branch_visible)
    if expected_mode == 'online':
        return online
    if expected_mode == 'in_person' or (in_person and not online):
        return in_person and (not branch_visible or str(branch.first.input_value()).strip() not in {'', '0'})
    return online


def online_form_ready(page):
    return form_ready(page, 'online')


def submit_quota_attempt(bot, page, stop_event, *, timeout=10, blocked=lambda: False, expected_mode=None, allowed=lambda: True):
    """The caller owns the page and operation lock. No reload, fallback, SMS or extra loop."""
    if stop_event.is_set():
        return 'stopped'
    if not form_ready(page, expected_mode):
        return 'form_not_ready'
    button = page.locator('#ctl00_ContentPlaceHolder1_btnSave')
    if not button.count() or not button.first.is_visible() or not button.first.is_enabled():
        return 'form_not_ready'
    if not allowed():
        return 'option_selected_waiting_user'
    if stop_event.is_set():
        return 'stopped'

    ticket = uuid.uuid4().hex
    response_result = {}
    in_flight = set()
    started = time.time()
    bot.consume_recovery_flag('bank_capacity_full')

    def is_final_request(request):
        if request.method != 'POST':
            return False
        fields = parse_qs(request.post_data or '', keep_blank_values=True)
        return (fields.get('__EVENTTARGET') or [''])[0].endswith('$btnSave') or any(key.endswith('$btnSave') for key in fields)

    def on_request(request):
        if is_final_request(request):
            in_flight.add(id(request))

    def on_failed(request):
        if is_final_request(request):
            in_flight.discard(id(request))
            response_result['failed'] = True

    def on_response(response):
        request = response.request
        if not is_final_request(request):
            return
        response_result['status'] = response.status

    def on_finished(request):
        if not is_final_request(request):
            return
        response = request.response()
        if response is not None and response.status == 200:
            try:
                result = analyze_bank_page_html(response.text())
                response_result['capacity'] = result.get('state') == State.CAPACITY_FULL
            except Exception:
                # An unreadable body cannot establish rejection or permit another click.
                response_result['unreadable'] = True
        in_flight.discard(id(request))

    page.on('request', on_request)
    page.on('response', on_response)
    page.on('requestfailed', on_failed)
    page.on('requestfinished', on_finished)
    try:
        # Distinguish a new response showing the same text from an old visible warning.
        page.evaluate('''ticket => {
            if (window.__mahanQuotaObserver) window.__mahanQuotaObserver.disconnect();
            window.__mahanQuotaResult={ticket, changed:false};
            const selectors='#ctl00_ContentPlaceHolder1_labErrMsg, #ctl00_ContentPlaceHolder1_lblMessage, span[id*="lblError"]';
            window.__mahanQuotaObserver=new MutationObserver(records=>{
                for (const record of records) {
                    const el=record.target.nodeType===1 ? record.target : record.target.parentElement;
                    if ((el && (el.matches(selectors) || el.closest(selectors))) ||
                        [...record.addedNodes].some(node=>node.nodeType===1 && (node.matches(selectors)||node.querySelector(selectors)))) {
                        window.__mahanQuotaResult.changed=true;
                    }
                }
            });
            window.__mahanQuotaObserver.observe(document.body,{subtree:true,childList:true,characterData:true});
        }''', ticket)
        bot.emit_event('FINAL_SUBMIT_STARTED', step='FINAL_SUBMIT', reason='quota_sniping',
                       result='native_save_click', message='در حال ارسال ثبت نهایی درخواست با انتخاب‌های همین متقاضی.', page=page)
        if not allowed() or stop_event.is_set():
            return 'stopped' if stop_event.is_set() else 'option_selected_waiting_user'
        DBHandler.record_final_submission_attempt(bot.nid)
        page.click('#ctl00_ContentPlaceHolder1_btnSave', timeout=5000, no_wait_after=True)
        deadline = time.monotonic() + max(.05, timeout)
        while True:
            if stop_event.is_set():
                return 'stopped'
            if blocked() or response_result.get('status') in {403, 429} or bot.consume_recovery_flag('waf_blocked'):
                return 'access_denied'
            stage = bot.detect_actual_page_stage(page)
            if stage == 'WAF_BLOCKED':
                return 'access_denied'
            if response_result.get('status') == 401 or stage in {'STEP_1_ENTRY_FORM', 'STEP_2_OTP'} or bot.consume_recovery_flag('session_expired'):
                return 'session_expired'
            if response_result.get('failed') or response_result.get('status', 200) >= 400:
                return 'submit_rejected'
            if extract_confirmed_reservation(page):
                bot.save_checkpoint('STEP_3_BANK_SELECT', page=page, reserve_confirmed=True,
                                    final_submit_status='reserve_confirmed', last_operation_result='RESERVE_PENDING')
                return 'reserve_confirmed'
            code = _extract_confirmed_submission(bot, page)
            if code:
                DBHandler.save_success_data(bot.nid, code)
                return 'submitted_confirmed'

            data = analyze_bank_page_html(capture_dom_html(page))
            mutation = page.evaluate('ticket => window.__mahanQuotaResult?.ticket===ticket && window.__mahanQuotaResult.changed', ticket)
            fresh_dialog = (getattr(bot, '_last_dialog_ts', 0) >= started
                            and getattr(bot, '_last_dialog_action', '') == 'bank_capacity_full')
            fresh_capacity = fresh_dialog or response_result.get('capacity') or (mutation and data.get('state') == State.CAPACITY_FULL)
            if fresh_capacity and not in_flight:
                bot.emit_event('FINAL_SUBMIT_REJECTED', step='FINAL_SUBMIT', reason='fresh_capacity_rejection',
                               result='bank_capacity_full', message='درخواست جدید به علت تکمیل ظرفیت رد شد؛ بانک، روش پذیرش و شعبه برای تلاش بعدی حفظ شدند.', page=page)
                return 'capacity_full'
            if data.get('error_kind') == 'waf_blocked':
                return 'access_denied'
            if data.get('state') == State.SESSION_EXPIRED:
                return 'session_expired'
            if time.monotonic() >= deadline:
                bot.emit_event('FINAL_SUBMIT_UNCONFIRMED', step='FINAL_SUBMIT', reason='quota_result_unknown',
                               result='awaiting_result', message='نتیجه درخواست روشن نیست؛ وضعیت پاسخ و صفحه بررسی می‌شود.', level='warning', page=page)
                return 'submit_rejected'
            page.wait_for_timeout(50)
    except Exception as exc:
        bot.emit_event('FINAL_SUBMIT_UNCONFIRMED', step='FINAL_SUBMIT', reason=type(exc).__name__,
                       result='awaiting_result', message='پاسخ ثبت درخواست تایید نشد؛ وضعیت پاسخ و صفحه بررسی می‌شود.', level='warning')
        return 'submit_rejected'
    finally:
        page.remove_listener('request', on_request)
        page.remove_listener('response', on_response)
        page.remove_listener('requestfailed', on_failed)
        page.remove_listener('requestfinished', on_finished)
        try:
            page.evaluate('''ticket => {
                if (window.__mahanQuotaResult?.ticket===ticket) {
                    window.__mahanQuotaObserver?.disconnect();
                    delete window.__mahanQuotaObserver; delete window.__mahanQuotaResult;
                }
            }''', ticket)
        except Exception:
            pass  # Page navigation/closure already destroyed this diagnostic observer.
