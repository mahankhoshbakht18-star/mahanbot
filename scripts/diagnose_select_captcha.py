"""Compare the six requested bank-selection cases; never submit OTP or final bank forms.

Live runs use a full copy of each saved profile and a SQLite backup. Original
profiles/database are never cleaned, repaired, or passed to a browser.
"""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CASES = {'F1': '6450700394', 'F2': '7040099756', 'F3': '4849936334',
         'C1': '6450699140', 'C2': '7050030546', 'C3': '6680311926'}
TARGET = 'https://ve.cbi.ir/SelectBnkShb.aspx'


def public_url(value):
    p = urlsplit(str(value))
    return urlunsplit((p.scheme, p.netloc, p.path, '', ''))


def redact(value):
    value = str(value)
    for nid in CASES.values():
        value = value.replace(nid, '**' + nid[-4:])
    value = re.sub(r'\b\d{4,}\b', '[digits]', value)
    value = re.sub(r'\b[A-Za-z0-9_+/=-]{24,}\b', '[opaque]', value)
    return re.sub(r'(?i)(token|cookie|password|secret)\s*[:=]\s*\S+', r'\1=[redacted]', value)


def inventory(profile):
    result = {}
    preferences = profile / 'Default/Preferences'
    if preferences.exists():
        prefs = json.loads(preferences.read_text(encoding='utf-8'))
        zooms = prefs.get('partition', {}).get('per_host_zoom_levels', {})
        result['cbi_zoom_factors'] = [round(1.2**details.get('zoom_level', 0), 6)
            for hosts in zooms.values() if isinstance(hosts, dict)
            for host, details in hosts.items() if host == 've.cbi.ir' and isinstance(details, dict)]
    for name in ('Local State', 'Default/Network/Cookies', 'Default/Local Storage',
                 'Default/Session Storage', 'Default/Service Worker', 'Default/IndexedDB', 'Default/Cache'):
        path = profile / name
        files = sorted(path.rglob('*')) if path.is_dir() else [path] if path.is_file() else []
        files = [p for p in files if p.is_file()]
        result[name] = {'exists': path.exists(), 'files': len(files),
                        'bytes': sum(p.stat().st_size for p in files)}
    cookies = profile / 'Default/Network/Cookies'
    if cookies.exists():
        with closing(sqlite3.connect(cookies.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            result['cookie_integrity'] = conn.execute('PRAGMA quick_check').fetchone()[0]
            result['cbi_cookie_metadata'] = [dict(zip(('host', 'name', 'encrypted_bytes', 'persistent'), row))
                for row in conn.execute("SELECT host_key,name,length(encrypted_value),has_expires FROM cookies WHERE host_key LIKE '%cbi.ir'")]
    return result


def original_hashes(profile):
    return {name: hashlib.sha256((profile / name).read_bytes()).hexdigest()
            for name in ('Local State', 'Default/Preferences', 'Default/Network/Cookies')
            if (profile / name).is_file()}


DOM_EVIDENCE = """() => ({
  ready:document.readyState, pageIsValid:window.Page_IsValid ?? null,
  devicePixelRatio:window.devicePixelRatio, viewportWidth:window.innerWidth,
  hasClientValidate:typeof window.Page_ClientValidate==='function',
  hasPostback:typeof window.__doPostBack==='function',
  inputs:Array.from(document.querySelectorAll('input,button')).filter(e=>e.getClientRects().length).map(e=>({
    id:e.id,name:e.name,type:e.type,visible:true,enabled:!e.disabled,readOnly:!!e.readOnly,
    valueLength:(e.value||'').length,maxLength:e.maxLength,required:!!e.required})),
  images:Array.from(document.images).filter(e=>e.getClientRects().length).map(e=>({
    id:e.id,srcPath:(()=>{try{return new URL(e.src).pathname}catch{return ''}})(),
    complete:e.complete,naturalWidth:e.naturalWidth,naturalHeight:e.naturalHeight,
    width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height})),
  validators:Array.from(window.Page_Validators||[]).map(v=>({id:v.id,control:v.controltovalidate,
    enabled:v.enabled!==false,valid:v.isvalid,group:v.validationGroup||'',message:v.errormessage||''})),
  errors:Array.from(document.querySelectorAll('[role=alert],[id*=lblMessage],[id*=labErrMsg],.ValidationSummary'))
    .filter(e=>e.getClientRects().length).map(e=>e.innerText),
  storage:{localCount:localStorage.length,sessionCount:sessionStorage.length}
})"""

INPUT_EVENTS = """(() => {
  window.__selectInputEvents=[];
  document.addEventListener('click',e=>{
    if(e.target.closest('[id$="btnSave"]')){e.preventDefault();e.stopImmediatePropagation();}
  },true);
  for(const kind of ['input','change','keyup','submit','invalid']) document.addEventListener(kind,e=>{
    const entry={kind,id:e.target.id||'',valueLength:(e.target.value||'').length,at:performance.now()};
    if(window.__selectInputEvents.length<150) {
      window.__selectInputEvents.push(entry);
      if(typeof window.__selectRecordInput==='function') window.__selectRecordInput(entry);
    }
  },true);
})()"""


def compare_database(conn, nid):
    row = conn.execute('SELECT status,data FROM applicants WHERE national_id=?', (nid,)).fetchone()
    if row is None:
        return {'missing': True}
    data = json.loads(row[1] or '{}')
    runtime = conn.execute('SELECT payload FROM automation_runtime WHERE nid=?', (nid,)).fetchone()
    runtime = json.loads(runtime[0]) if runtime else {}
    cp = conn.execute('SELECT payload FROM checkpoints WHERE nid=?', (nid,)).fetchone()
    cp = json.loads(cp[0]) if cp else {}
    metrics = list(conn.execute('SELECT result,count(*) FROM captcha_metrics WHERE nid=? GROUP BY result', (nid,)))
    return {'status': row[0], 'fields': {k: {'present': bool(data.get(k)), 'type': type(data.get(k)).__name__,
            'length': len(str(data.get(k) or ''))} for k in ('tracking_code','mobile','state','city','loan_type','priority_banks')},
            'flags': {k:v for k,v in data.items() if isinstance(v, bool)},
            'runtime': {k:runtime.get(k) for k in ('status','current_step','last_error','browser_status','workflow_state')},
            'checkpoint': {k:cp.get(k) for k in ('last_verified_step','bank_state','final_submit_status')},
            'captcha_metrics': dict(metrics),
            'captcha_attempts': dict(Counter(str(a.get('source')) + ':' + str(a.get('result'))
                for a in data.get('captcha_attempts', []) if isinstance(a, dict))),
            'applicant_settings_present': bool(data.get('settings'))}


def run_case(label, nid, output, source_db, *, attempt=False, headed=False, channel='chromium'):
    import database
    from bot_select import BankSelectionBot
    from database import DBHandler
    from playwright.sync_api import sync_playwright
    from network_diagnostics import check_rejection
    folder = output / label
    folder.mkdir()
    clone = folder / 'profile'
    source_profile = ROOT / 'chrome_profiles' / nid
    before = original_hashes(source_profile)
    shutil.copytree(source_profile, clone, ignore=shutil.ignore_patterns('Singleton*', 'LOCK', 'lockfile'))
    db = folder / 'state.db'
    with closing(sqlite3.connect(source_db)) as source, closing(sqlite3.connect(db)) as target:
        source.backup(target)
    database.DB_PATH = str(db)
    DBHandler._settings_cache.clear()
    settings = DBHandler.get_config()
    settings.update(auto_final_submit=False, final_submit=False, captcha_manual_timeout=8)
    stop = threading.Event()
    events = []
    def event(event_type, **values):
        entry = {'at': round(time.monotonic(), 3), 'event': event_type, **values}
        events.append(entry)
        (folder / 'events.json').write_text(json.dumps(events, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    bot = BankSelectionBot(nid, settings, log_callback=lambda *_: None)
    bot.log = lambda message, level='info', page=None, **kw: event('bot_log', level=level, message=redact(message))
    original_solve = bot.captcha_service.solve
    def measured_solve(image, *args, **kwargs):
        from PIL import Image
        started = time.monotonic()
        raw = bytes(image) if isinstance(image, (bytes, bytearray, memoryview)) else Path(image).read_bytes()
        result = original_solve(image, *args, **kwargs)
        (folder / f'ocr-{len([e for e in events if e["event"] == "ocr"])+1:02}.png').write_bytes(raw)
        event('ocr', image_sha256=hashlib.sha256(raw).hexdigest(), image_bytes=len(raw),
              image_pixels=Image.open(io.BytesIO(raw)).size,
              returned=bool(result), prediction_length=len(result or ''),
              solver_error=redact(bot.captcha_service.solver.last_error),
              elapsed=round(time.monotonic()-started, 3))
        return result
    bot.captcha_service.solve = measured_solve
    original_attempt = bot.solve_captcha_with_model
    def measured_attempt(page, input_sel, *args, **kwargs):
        event('model_attempt', source=kwargs.get('source'), fields=page.locator(input_sel).evaluate_all(
            "els => els.map(e=>({id:e.id,enabled:!e.disabled,valueLength:e.value.length}))"))
        return original_attempt(page, input_sel, *args, **kwargs)
    bot.solve_captcha_with_model = measured_attempt
    with sync_playwright() as pw:
        options = {'headless': not headed, 'viewport': {'width': 620, 'height': 730}}
        if channel in {'chrome','msedge'}:
            options['channel'] = channel
        context = pw.chromium.launch_persistent_context(str(clone), **options)
        context.expose_binding('__selectRecordInput', lambda source, entry: event('input_event',
            kind=entry['kind'], id=entry['id'], valueLength=entry['valueLength'], page_ms=entry['at']))
        context.add_init_script(INPUT_EVENTS)
        page = context.pages[0] if context.pages else context.new_page()
        page.on('crash', lambda: event('page_crash'))
        page.on('close', lambda: event('page_closed'))
        page.set_default_timeout(2500)
        page.on('console', lambda msg: event('console', level=msg.type, text=redact(msg.text)))
        page.on('pageerror', lambda err: event('pageerror', text=redact(err)))
        page.on('requestfailed', lambda req: event('requestfailed', url=public_url(req.url), reason=redact(req.failure)))
        page.on('response', lambda res: event('response', url=public_url(res.url), status=res.status))
        def dialog_seen(dialog):
            event('dialog', dialog_type=dialog.type, message=redact(dialog.message))
        page.on('dialog', dialog_seen)
        bot.attach_dialog_handler(page)
        cdp = context.new_cdp_session(page)
        cdp.send('Network.enable')
        # Routing disables Chromium's HTTP cache; preserve it for profile comparisons.
        cdp.send('Network.setBlockedURLs', {'urls': ['*://ve.cbi.ir/Login.aspx*', '*://ve.cbi.ir/Register.aspx*']})
        cdp.on('Network.responseReceived', lambda data: event('network_cache',
            url=public_url(data['response']['url']), from_disk=data['response'].get('fromDiskCache', False),
            from_worker=data['response'].get('fromServiceWorker', False)))
        def snapshot(name):
            evidence = page.evaluate(DOM_EVIDENCE)
            evidence['errors'] = [redact(message) for message in evidence['errors']]
            for validator in evidence['validators']:
                validator['message'] = redact(validator['message'])
            evidence['url'] = public_url(page.url)
            evidence['stage'] = bot.detect_actual_page_stage(page)
            evidence['input_events'] = page.evaluate('window.__selectInputEvents || []')
            (folder / (name + '.json')).write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding='utf-8')
            page.screenshot(path=str(folder / (name + '.png')), mask=[page.locator('input,textarea')], timeout=5000)
            event('snapshot', name=name, stage=evidence['stage'])
            return evidence
        try:
            response = page.goto(TARGET, wait_until='domcontentloaded', timeout=35000)
            event('navigation', status=response.status if response else None)
            page.wait_for_timeout(1500)
            evidence = snapshot('01_loaded')
            check_rejection(page)
            if evidence['stage'] == 'STEP_1_ENTRY_FORM':
                nid_input = page.locator('#ctl00_ContentPlaceHolder1_tbIDNo')
                nid_input.fill(nid)
                nid_input.dispatch_event('change')
                snapshot('02_nid_filled')
                if attempt:
                    timer = threading.Timer(50, stop.set)
                    timer.start()
                    try:
                        result = bot._solve_primary_captcha_fast(page, stop)
                        event('primary_result', result=bool(result), stopped=stop.is_set())
                    finally:
                        timer.cancel()
                    snapshot('03_after_primary')
            event('outcome', stage=bot.detect_actual_page_stage(page))
        except Exception as exc:
            event('diagnostic_error', error=type(exc).__name__, detail=redact(exc))
            try:
                snapshot('error')
            except Exception as snapshot_error:
                event('snapshot_error', error=type(snapshot_error).__name__)
        finally:
            context.close()
    event('original_profile_unchanged', unchanged=before == original_hashes(source_profile))
    return {'case': label, 'events': len(events), 'outcome': next((e for e in reversed(events) if e['event'] in {'outcome','diagnostic_error'}), {})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', default=','.join(CASES))
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--attempt', action='store_true', help='Run the existing select CAPTCHA handler; stop at OTP/bank')
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--channel', choices=('chromium','chrome','msedge'), default='chrome')
    args = parser.parse_args()
    labels = args.cases.split(',')
    if any(label not in CASES for label in labels):
        parser.error('Unknown case label')
    output = ROOT / 'diagnostics' / ('select-captcha-' + time.strftime('%Y%m%d-%H%M%S'))
    output.mkdir(parents=True)
    source_db = output / 'source-state.db'
    with closing(sqlite3.connect((ROOT / 'cbi_ultimate.db').as_uri() + '?mode=ro', uri=True)) as source, \
         closing(sqlite3.connect(source_db)) as target:
        source.backup(target)
    with closing(sqlite3.connect(source_db)) as conn:
        comparison = {label: {'database': compare_database(conn, CASES[label]),
                            'profile': inventory(ROOT / 'chrome_profiles' / CASES[label])} for label in labels}
    (output / 'comparison.json').write_text(json.dumps(comparison, indent=2, ensure_ascii=False), encoding='utf-8')
    print('Diagnostic directory:', output, flush=True)
    if args.live:
        import requests
        with patch.dict(os.environ, {'MAHAN_OTP_RELAY_DISABLED':'1'}), \
             patch('requests.sessions.Session.request', side_effect=requests.ConnectionError('Diagnostic: app/relay requests disabled')):
            for label in labels:
                try:
                    outcome = run_case(label, CASES[label], output, source_db,
                                       attempt=args.attempt, headed=args.headed, channel=args.channel)
                except Exception as exc:
                    outcome = {'case': label, 'error': type(exc).__name__, 'detail': redact(str(exc).split('Call log:')[0])}
                    (output / (label + '-launch-error.json')).write_text(json.dumps(outcome, indent=2), encoding='utf-8')
                print(json.dumps(outcome), flush=True)
                if outcome.get('outcome', {}).get('error') == 'SiteRejectedError':
                    print('Site rejected this run; remaining live cases were not attempted.', flush=True)
                    break


if __name__ == '__main__':
    main()
