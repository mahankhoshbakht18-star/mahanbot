"""Central license client. A browser flag or a local subscription never grants a client license."""
import hashlib
import json
import math
import os
import platform
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests

import database
from database import DBHandler

PRODUCT = 'mahan-marriage-bot'
DEFAULT_SERVER = 'https://otp.mahanvip.ir/api/licensing/v1'
_cache = {}
_cache_lock = threading.Lock()


class LicenseServiceError(Exception):
    def __init__(self, state, message, status=503):
        self.state, self.message, self.status = state, message, status
        super().__init__(message)


def license_required():
    return (os.getenv('MAHANBOT_EDITION', 'ADMIN').strip().upper() == 'CLIENT'
            or os.getenv('MAHANBOT_REQUIRE_LICENSE', '').lower() in {'1', 'true', 'yes', 'on'})


def server_url():
    url = os.getenv('MAHANBOT_LICENSE_SERVER', DEFAULT_SERVER).rstrip('/')
    parsed = urlparse(url)
    local = parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LicenseServiceError('configuration_error', 'آدرس سرور لایسنس معتبر نیست.')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and local):
        raise LicenseServiceError('configuration_error', 'سرور لایسنس باید HTTPS داشته باشد.')
    return url


def machine_hash():
    identity = ''
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography') as key:
                identity = str(winreg.QueryValueEx(key, 'MachineGuid')[0])
        except OSError:
            pass
    else:
        for name in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
            try:
                identity = Path(name).read_text().strip()
                if identity:
                    break
            except OSError:
                pass
    identity = identity or (platform.node() + ':' + str(uuid.getnode()))
    return hashlib.sha256((PRODUCT + ':' + identity).encode()).hexdigest()


def _local():
    with DBHandler._connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS local_license (
            id INTEGER PRIMARY KEY CHECK(id=1),installation_id TEXT NOT NULL,
            activation_token TEXT NOT NULL DEFAULT '',updated_at REAL NOT NULL)''')
        conn.execute('INSERT OR IGNORE INTO local_license(id,installation_id,updated_at) VALUES(1,?,?)', (uuid.uuid4().hex, time.time()))
        row = conn.execute('SELECT installation_id,activation_token FROM local_license WHERE id=1').fetchone()
        conn.commit()
        return {'installation_id': row[0], 'activation_token': row[1]}


def clear_cache():
    with _cache_lock:
        _cache.clear()


def _request(method, path, payload=None, *, token=None):
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        # Do not follow redirects carrying customer credentials to another host.
        response = requests.request(method, server_url() + path, json=payload,
                                    headers=headers, timeout=(2, 3), allow_redirects=False)
    except requests.RequestException:
        raise LicenseServiceError('network_error', 'ارتباط امن با سرور لایسنس برقرار نشد؛ اجرای جدید متوقف است.') from None
    try:
        data = response.json()
    except (ValueError, TypeError):
        raise LicenseServiceError('invalid_response', 'پاسخ سرور لایسنس معتبر نیست.') from None
    if not 200 <= response.status_code < 300:
        states = {401: 'not_activated', 403: 'invalid', 404: 'service_unavailable', 429: 'rate_limited'}
        state = data.get('state', states.get(response.status_code, 'server_error')) if isinstance(data, dict) else 'server_error'
        raise LicenseServiceError(state, {
            'expired': 'اعتبار لایسنس تمام شده است؛ از مدیر تمدید بگیرید.',
            'revoked': 'لایسنس توسط مدیر غیرفعال شده است.',
            'device_limit': 'تعداد دستگاه‌های مجاز تکمیل است؛ با مدیر تماس بگیرید.',
            'device_mismatch': 'لایسنس به دستگاه دیگری تعلق دارد.',
            'not_activated': 'ابتدا لایسنس این دستگاه را فعال کنید.',
            'rate_limited': 'سرور تعداد درخواست‌ها را محدود کرده است؛ کمی بعد بررسی کنید.',
        }.get(state, 'درخواست لایسنس توسط سرور تأیید نشد.'), response.status_code)
    return data


def _validated(data):
    if (not isinstance(data, dict) or data.get('product') != PRODUCT
            or not isinstance(data.get('valid'), bool)
            or data.get('state') not in {'active', 'expired', 'revoked'}
            or not isinstance(data.get('expires_at'), (int, float))
            or not math.isfinite(data.get('expires_at', 0))
            or not isinstance(data.get('license_id'), str)):
        raise LicenseServiceError('invalid_response', 'پاسخ سرور لایسنس معتبر نیست.')
    valid = data['valid'] and data['state'] == 'active' and data['expires_at'] > time.time()
    if data['valid'] and not valid:
        data = dict(data, valid=False, state='expired' if data['expires_at'] <= time.time() else data['state'])
    return {key: data.get(key) for key in ('valid', 'state', 'expires_at', 'customer', 'license_id', 'max_devices')}


def check_license(force=False):
    if not license_required():
        return {'valid': True, 'state': 'administrator', 'required': False, 'server': DEFAULT_SERVER}
    local = _local()
    cache_key = (str(database.DB_PATH), server_url(), local['activation_token'])
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(cache_key)
    if cached and not force and now - cached['at'] < 2:
        state = dict(cached['state'])
        if state.get('valid') and state.get('expires_at', 0) <= time.time():
            state.update(valid=False, state='expired')
        return state
    result = {'valid': False, 'state': 'not_activated', 'message': 'کلید لایسنس را وارد کنید.'}
    if local['activation_token']:
        try:
            result = _validated(_request('POST', '/verify',
                {'installation_id': local['installation_id'], 'machine_hash': machine_hash()}, token=local['activation_token']))
        except LicenseServiceError as exc:
            result = {'valid': False, 'state': exc.state, 'message': exc.message}
    result.update(required=True, installation_id=local['installation_id'], server=server_url())
    with _cache_lock:
        _cache[cache_key] = {'at': time.monotonic(), 'state': dict(result)}
    return result


def can_run(force=False):
    try:
        return bool(check_license(force).get('valid'))
    except (LicenseServiceError, OSError):
        return False


def activate_license(key):
    local = _local()
    response = _request('POST', '/activate', {'license_key': key.strip(), 'installation_id': local['installation_id'], 'machine_hash': machine_hash()})
    decision = _validated(response)
    if not decision['valid'] or not isinstance(response.get('activation_token'), str) or len(response['activation_token']) < 32:
        raise LicenseServiceError('invalid_response', 'فعال‌سازی دستگاه تأیید نشد.')
    with DBHandler._connect() as conn:
        conn.execute('UPDATE local_license SET activation_token=?,updated_at=? WHERE id=1', (response['activation_token'], time.time()))
        conn.commit()
    clear_cache()
    # Return public information only. The device token stays inside the backend.
    return dict(decision, required=license_required(), installation_id=local['installation_id'], server=server_url())


def deactivate_local():
    _local()
    with DBHandler._connect() as conn:
        conn.execute("UPDATE local_license SET activation_token='',updated_at=? WHERE id=1", (time.time(),))
        conn.commit()
    clear_cache()


def manager_request(method, path, payload=None):
    if os.getenv('MAHANBOT_EDITION', 'ADMIN').strip().upper() == 'CLIENT':
        raise LicenseServiceError('forbidden', 'نسخه مشتری دسترسی مدیریت لایسنس ندارد.', 403)
    token = os.getenv('MAHANBOT_LICENSE_ADMIN_TOKEN', '')
    if len(token) < 32:
        raise LicenseServiceError('configuration_error', 'اتصال مدیریت لایسنس تنظیم نشده است؛ فایل خصوصی مدیر را بررسی کنید.')
    return _request(method, '/admin/licenses' + path, payload, token=token)


def pause_unlicensed_sessions(queue=None):
    """Owner threads handle pause commands; never touch another thread's Playwright objects."""
    from live_sessions import LIVE_SESSIONS
    with LIVE_SESSIONS._lock:
        owners = list(LIVE_SESSIONS._sessions.values())
    for owner in owners:
        if owner.alive and owner.bank_page_active:
            if not getattr(owner, '_license_pause_sent', False):
                owner.commands.put('pause')
                owner._license_pause_sent = True
        elif owner.alive:
            owner.cancellation_token.set()
    if queue:
        for job in queue.list_jobs():
            if job['status'] == 'queued':
                queue.cancel(job['id'])


def start_watchdog(queue):
    if not license_required():
        return None
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            if not can_run(force=True):
                pause_unlicensed_sessions(queue)
            else:
                from live_sessions import LIVE_SESSIONS
                with LIVE_SESSIONS._lock:
                    for owner in LIVE_SESSIONS._sessions.values():
                        owner._license_pause_sent = False
            stop.wait(5)

    threading.Thread(target=watch, name='license-heartbeat', daemon=True).start()
    return stop
