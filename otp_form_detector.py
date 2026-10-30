"""Read live form evidence; never cache element handles or enter guessed fields."""
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or '').translate(
        str.maketrans({'ي': 'ی', 'ك': 'ک', 'أ': 'ا', 'إ': 'ا', 'ئ': 'ی', '\u200c': ' '}))).strip().lower()


def live_scopes(page):
    yield page
    origin = urlsplit(str(page.url)).netloc
    for frame in page.frames:
        if frame == page.main_frame or 'tspd' in (frame.name + frame.url).lower():
            continue
        if urlsplit(frame.url).netloc in ('', origin):
            yield frame


READY = """() => {
    const prm = window.Sys?.WebForms?.PageRequestManager?.getInstance();
    return document.readyState !== 'loading' && !prm?.get_isInAsyncPostBack() &&
      !Array.from(document.querySelectorAll('[id$="updateProgress"]')).some(
        e => e.getClientRects().length && getComputedStyle(e).display !== 'none');
}"""

FORM_EVIDENCE = """() => {
    const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
    return Array.from(document.querySelectorAll('input,button,a,img')).filter(visible).map(e => ({
      tag: e.tagName.toLowerCase(), type: e.type || '', id: e.id, name: e.name || '',
      label: Array.from(e.labels || []).map(l => l.textContent).join(' '),
      nearby: /^(SPAN|P|LABEL|STRONG)$/.test(e.previousElementSibling?.tagName || '')
        ? e.previousElementSibling.textContent.slice(0, 100) : '',
      placeholder: e.getAttribute('placeholder') || '', autocomplete: e.autocomplete || '',
      aria: e.getAttribute('aria-label') || '', maxlength: e.maxLength || 0,
      text: /^(BUTTON|A)$/.test(e.tagName) ? e.textContent : (/submit|button/.test(e.type) ? e.value : ''),
      enabled: !e.disabled && !e.readOnly && e.getAttribute('aria-disabled') !== 'true', filled: !!e.value,
      form: Array.from(document.forms).indexOf(e.form || e.closest('form')),
      src: e.tagName === 'IMG' ? e.getAttribute('src') || '' : '',
      imageReady: e.tagName === 'IMG' && e.complete && e.naturalWidth > 0
    }));
}"""


@dataclass
class OTPForm:
    scope: object = None
    fields: dict = field(default_factory=dict)
    reason: str = 'no_otp_evidence'
    elapsed_ms: float = 0.0
    ready: bool = False
    inspected: bool = False

    def resolve(self, role):
        item = self.fields.get(role)
        if not item or self.scope is None:
            return None
        loc = self.scope.locator(item['selector'])
        if loc.count() != 1 or not loc.is_visible() or not loc.is_enabled():
            return None
        if item.get('tag') == 'input' and not loc.is_editable():
            return None
        return loc

    def public_fields(self):
        return {key: {k: v for k, v in item.items() if k in ('id', 'name', 'tag', 'enabled')}
                for key, item in self.fields.items()}


class UnifiedOTPFormDetector:
    def detect(self, page, timeout_ms=0):
        started = time.perf_counter()
        result = OTPForm()
        try:
            if page.is_closed():
                result.reason = 'target_closed'
                return result
            if timeout_ms:
                page.wait_for_function(READY, timeout=timeout_ms)
            elif not page.evaluate(READY):
                result.reason = 'postback_pending'
                return result
            candidates = []
            inspected = False
            for scope in live_scopes(page):
                items = scope.evaluate(FORM_EVIDENCE)
                if not isinstance(items, list):
                    continue
                inspected = True
                roles = {'otp': [], 'captcha': [], 'continue': [], 'resend': [], 'image': []}
                for item in items:
                    if not item.get('enabled') and item.get('tag') != 'img':
                        continue
                    tag, kind = item['tag'], item['type']
                    identity = normalize_text(item['id'] + ' ' + item['name'])
                    words = normalize_text(item['label'] + ' ' + item['placeholder'] + ' ' + item['aria'])
                    if not words:
                        words = normalize_text(item.get('nearby', ''))
                    if tag == 'input' and kind in ('text', 'tel', 'number', 'password', ''):
                        captcha = 'captcha' in identity or any(w in words for w in ('کد امنیتی', 'کد تصویر', 'کپچا'))
                        otp = ('mobileconfcode' in identity or 'mobconfcode' in identity or
                               re.search(r'(^|[_\W])otp([_\W]|$)', identity) or
                               item['autocomplete'] == 'one-time-code' or
                               any(w in words for w in ('کد پیامک', 'کد تایید', 'کد تأیید', 'رمز یکبار', 'رمز یک بار')))
                        if captcha:
                            roles['captcha'].append(item)
                        elif otp:
                            roles['otp'].append(item)
                    if tag in {'button', 'a'} or (tag == 'input' and kind in ('submit', 'button')):
                        text = normalize_text(item['text'] + ' ' + item['aria'])
                        if any(w in text for w in ('ارسال مجدد', 'پیامک جدید', 'کد جدید')) or 'resend' in identity:
                            roles['resend'].append(item)
                        elif 'btncontinue' in identity or any(w in text for w in ('ادامه', 'تایید', 'تأیید')):
                            roles['continue'].append(item)
                    if tag == 'img' and ('captcha' in normalize_text(item['src'] + identity)):
                        roles['image'].append(item)
                if not roles['otp']:
                    continue
                if len(roles['otp']) != 1:
                    return OTPForm(reason='ambiguous_otp_fields')
                otp = roles['otp'][0]
                fields = {'otp': otp}
                for role in ('captcha', 'continue', 'resend'):
                    matches = [i for i in roles[role] if role == 'resend' or i['form'] == otp['form']]
                    if len(matches) > 1:
                        return OTPForm(reason='ambiguous_' + role)
                    if matches:
                        fields[role] = matches[0]
                images = roles['image']
                if len(images) == 1:
                    fields['image'] = images[0]
                for role, item in list(fields.items()):
                    selector = self._selector(item)
                    if not selector or scope.locator(selector).count() != 1:
                        del fields[role]
                        continue
                    item['selector'] = selector
                candidates.append(OTPForm(scope, fields, 'live_form_evidence', ready=True, inspected=True))
            result = candidates[0] if len(candidates) == 1 else OTPForm(
                reason='ambiguous_otp_frames' if candidates else 'no_otp_evidence', inspected=inspected)
        except Exception as exc:
            result = OTPForm(reason='target_closed' if page.is_closed() else type(exc).__name__, inspected=True)
        finally:
            result.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return result

    @staticmethod
    def _selector(item):
        for attr in ('id', 'name', 'placeholder', 'autocomplete', 'aria', 'src'):
            if item.get(attr):
                name = 'aria-label' if attr == 'aria' else attr
                return f'{item["tag"]}[{name}={json.dumps(item[attr], ensure_ascii=False)}]'
        if item['tag'] in {'button', 'a'} and item.get('text'):
            return f'{item["tag"]}:text-is({json.dumps(item["text"].strip(), ensure_ascii=False)})'
        if item.get('label'):
            return f'label:has-text({json.dumps(item["label"].strip(), ensure_ascii=False)}) input'
        return None


def detect_otp_form(page, timeout_ms=0):
    return UnifiedOTPFormDetector().detect(page, timeout_ms)


def fill_otp_form(page, code):
    from otp_transport import normalized_code
    code = normalized_code(code)
    if not re.fullmatch(r'[0-9]{4,8}', code):
        return False
    form = detect_otp_form(page, timeout_ms=1500)
    control = form.resolve('otp')
    if control is None:
        return False
    limit = form.fields['otp'].get('maxlength', 0)
    if limit > 0 and len(code) > limit:
        return False
    control.fill(code, timeout=1500)
    return control.input_value(timeout=500) == code


def captcha_fingerprint(form):
    image = form.fields.get('image', {})
    if not image or not image.get('imageReady'):
        return ''
    return hashlib.sha256(form.scope.locator(image['selector']).screenshot(timeout=1000)).hexdigest()
