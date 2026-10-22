"""Authoritative licenses. Neither license keys nor device tokens are stored in clear text."""
import hashlib
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

PRODUCT = 'mahan-marriage-bot'


class LicenseError(Exception):
    def __init__(self, state, message, status=403):
        self.state, self.message, self.status = state, message, status
        super().__init__(message)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class LicenseStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS licenses (
                    id TEXT PRIMARY KEY, product TEXT NOT NULL, customer TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE, key_hint TEXT NOT NULL,
                    expires_at REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                    max_devices INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY, license_id TEXT NOT NULL REFERENCES licenses(id),
                    installation_id TEXT NOT NULL, machine_hash TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL, last_seen REAL NOT NULL,
                    UNIQUE(license_id, installation_id)
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, license_id TEXT NOT NULL, action TEXT NOT NULL, at REAL NOT NULL
                );
            ''')

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA journal_mode=WAL')
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def decision(row):
        if not row:
            raise LicenseError('invalid', 'لایسنس معتبر پیدا نشد.')
        state = 'revoked' if not row['active'] else 'expired' if row['expires_at'] <= time.time() else 'active'
        return dict(product=PRODUCT, valid=state == 'active', state=state,
                    license_id=row['id'], customer=row['customer'], expires_at=row['expires_at'],
                    max_devices=row['max_devices'])

    @staticmethod
    def audit(conn, license_id, action):
        conn.execute('INSERT INTO audit(license_id,action,at) VALUES(?,?,?)', (license_id, action, time.time()))

    def issue(self, customer, days, max_devices):
        key = 'MHN-MARR-' + secrets.token_urlsafe(24)
        now, license_id = time.time(), uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute('INSERT INTO licenses VALUES(?,?,?,?,?,?,1,?,?,?)',
                         (license_id, PRODUCT, customer, digest(key), 'MHN-MARR-…' + key[-4:],
                          now + days * 86400, max_devices, now, now))
            self.audit(conn, license_id, 'issued')
        return dict(license_id=license_id, license_key=key, expires_at=now + days * 86400)

    def list_licenses(self):
        with self.connect() as conn:
            rows = conn.execute('''SELECT l.id,l.customer,l.key_hint,l.expires_at,l.active,l.max_devices,l.created_at,
                (SELECT count(*) FROM devices d WHERE d.license_id=l.id AND d.active=1) AS devices_used
                FROM licenses l WHERE product=? ORDER BY created_at DESC''', (PRODUCT,)).fetchall()
            return [dict(row) for row in rows]

    def change(self, license_id, *, days=None, active=None):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM licenses WHERE id=? AND product=?', (license_id, PRODUCT)).fetchone()
            if row is None:
                raise LicenseError('not_found', 'لایسنس پیدا نشد.', 404)
            if days is not None:
                expires = max(time.time(), row['expires_at']) + days * 86400
                conn.execute('UPDATE licenses SET expires_at=?,updated_at=? WHERE id=?', (expires, time.time(), license_id))
                self.audit(conn, license_id, 'renewed')
            if active is not None:
                conn.execute('UPDATE licenses SET active=?,updated_at=? WHERE id=?', (int(active), time.time(), license_id))
                self.audit(conn, license_id, 'enabled' if active else 'revoked')
            return self.decision(conn.execute('SELECT * FROM licenses WHERE id=?', (license_id,)).fetchone())

    def activate(self, key, installation_id, machine_hash):
        if not key.startswith('MHN-MARR-'):
            raise LicenseError('invalid', 'کلید باید متعلق به بات وام ازدواج ماهان باشد.')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM licenses WHERE key_hash=? AND product=?', (digest(key), PRODUCT)).fetchone()
            state = self.decision(row)
            if not state['valid']:
                raise LicenseError(state['state'], 'لایسنس غیرفعال یا منقضی شده است.')
            device = conn.execute('SELECT * FROM devices WHERE license_id=? AND installation_id=?', (row['id'], installation_id)).fetchone()
            if device and device['machine_hash'] != machine_hash:
                raise LicenseError('device_mismatch', 'این شناسه نصب متعلق به دستگاه دیگری است.')
            count = conn.execute('SELECT count(*) FROM devices WHERE license_id=? AND active=1', (row['id'],)).fetchone()[0]
            if (not device or not device['active']) and count >= row['max_devices']:
                raise LicenseError('device_limit', 'تعداد دستگاه‌های مجاز این لایسنس تکمیل شده است.')
            token, now = secrets.token_urlsafe(32), time.time()
            if device:
                conn.execute('UPDATE devices SET token_hash=?,active=1,last_seen=? WHERE id=?', (digest(token), now, device['id']))
            else:
                conn.execute('INSERT INTO devices VALUES(?,?,?,?,?,1,?,?)',
                             (uuid.uuid4().hex, row['id'], installation_id, machine_hash, digest(token), now, now))
            self.audit(conn, row['id'], 'activated')
            return dict(state, activation_token=token)

    def verify(self, token, installation_id, machine_hash):
        with self.connect() as conn:
            device = conn.execute('SELECT * FROM devices WHERE token_hash=?', (digest(token),)).fetchone()
            if not device or not device['active']:
                raise LicenseError('not_activated', 'دستگاه فعال نیست؛ لایسنس را فعال کنید.')
            if device['installation_id'] != installation_id or device['machine_hash'] != machine_hash:
                raise LicenseError('device_mismatch', 'اعتبار دستگاه تأیید نشد.')
            row = conn.execute('SELECT * FROM licenses WHERE id=? AND product=?', (device['license_id'], PRODUCT)).fetchone()
            decision = self.decision(row)
            conn.execute('UPDATE devices SET last_seen=? WHERE id=?', (time.time(), device['id']))
            return decision

    def devices(self, license_id):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute('SELECT id,installation_id,active,created_at,last_seen FROM devices WHERE license_id=?', (license_id,)).fetchall()]

    def release_device(self, license_id, device_id):
        with self.connect() as conn:
            cur = conn.execute('UPDATE devices SET active=0 WHERE id=? AND license_id=?', (device_id, license_id))
            if not cur.rowcount:
                raise LicenseError('not_found', 'دستگاه پیدا نشد.', 404)
            self.audit(conn, license_id, 'device_released')
