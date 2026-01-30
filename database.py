import sqlite3
import json
import os
import sys
from typing import Any, Dict

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

DB_PATH = resource_path('cbi_ultimate.db')

class DBHandler:
    @staticmethod
    def init_db():
        try:
            conn = sqlite3.connect(DB_PATH)
            DBHandler._migrate(conn)
            conn.close()
        except Exception as e: print(f"DB Init Error: {e}")

    @staticmethod
    def _migrate(conn: sqlite3.Connection):
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("PRAGMA user_version")
        version = c.fetchone()[0]

        if version < 1:
            c.execute('''CREATE TABLE IF NOT EXISTS applicants 
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                          full_name TEXT, 
                          national_id TEXT UNIQUE, 
                          status TEXT DEFAULT 'Ready', 
                          last_log TEXT DEFAULT '-', 
                          data TEXT,
                          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                          updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
            c.execute('''CREATE TABLE IF NOT EXISTS settings 
                         (key TEXT PRIMARY KEY, value TEXT,
                          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                          updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
            c.execute('''CREATE TABLE IF NOT EXISTS events
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          type TEXT,
                          ts TEXT,
                          level TEXT,
                          job_id TEXT,
                          bot TEXT,
                          nid TEXT,
                          message TEXT,
                          meta TEXT,
                          created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')

            DBHandler._add_column_if_missing(conn, "applicants", "created_at", "TEXT")
            DBHandler._add_column_if_missing(conn, "applicants", "updated_at", "TEXT")
            DBHandler._add_column_if_missing(conn, "settings", "created_at", "TEXT")
            DBHandler._add_column_if_missing(conn, "settings", "updated_at", "TEXT")

            default_config = json.dumps({
                'captcha_delay': 0.1,
                'retry_count': 1000,
                'headless': False,
                'clear_cookies': True,
                'save_only_mode': False,
                'sms_auto_resend': True,
                'captcha_mode': 'human',
                'final_submit': False
            })
            c.execute("PRAGMA user_version = 1")
        default_config = json.dumps({
            'captcha_delay': 0.1,
            'retry_count': 1000,
            'headless': False,
            'clear_cookies': True,
            'save_only_mode': False,
            'sms_auto_resend': True,
            'captcha_mode': 'human',
            'final_submit': False
        })
        c.execute(
            "INSERT OR IGNORE INTO settings (key, value, created_at, updated_at) VALUES ('config', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (default_config,),
        )
        conn.commit()

    @staticmethod
    def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, col_type: str):
        c = conn.cursor()
        c.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in c.fetchall()}
        if column not in columns:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            if column in {"created_at", "updated_at"}:
                c.execute(f"UPDATE {table} SET {column} = COALESCE({column}, CURRENT_TIMESTAMP)")

    @staticmethod
    def get_config():
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT value FROM settings WHERE key='config'")
            row = c.fetchone()
            conn.close()
            return json.loads(row[0]) if row else {}
        except: return {}

    @staticmethod
    def update_config(new_conf):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute(
                "UPDATE settings SET value=?, updated_at=CURRENT_TIMESTAMP WHERE key='config'",
                (json.dumps(new_conf),),
            )
            conn.commit()
            conn.close()
        except: pass

    @staticmethod
    def get_applicant(nid):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM applicants WHERE national_id=?", (nid,))
            res = c.fetchone()
            conn.close()
            return res
        except: return None

    @staticmethod
    def get_all_applicants():
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM applicants ORDER BY id DESC")
            rows = c.fetchall()
            results = []
            for row in rows:
                r = dict(row)
                try: r['data'] = json.loads(r['data']) if r['data'] else {}
                except: r['data'] = {}
                results.append(r)
            conn.close()
            return results
        except: return []

    @staticmethod
    def update_status(nid, status, log):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute(
                "UPDATE applicants SET status=?, last_log=?, updated_at=CURRENT_TIMESTAMP WHERE national_id=?",
                (status, log, nid),
            )
            conn.commit()
            conn.close()
        except: pass

    @staticmethod
    def save_otp(nid, code):
        user = DBHandler.get_applicant(nid)
        if user:
            try:
                d = json.loads(user['data'])
                d['otp_code'] = str(code).strip()
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute(
                    "UPDATE applicants SET data=?, updated_at=CURRENT_TIMESTAMP WHERE national_id=?",
                    (json.dumps(d), nid),
                )
                conn.commit()
                conn.close()
                return True
            except: pass
        return False

    @staticmethod
    def clear_otp(nid):
        user = DBHandler.get_applicant(nid)
        if user:
            try:
                d = json.loads(user['data'])
                if 'otp_code' in d:
                    del d['otp_code']
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute(
                        "UPDATE applicants SET data=?, updated_at=CURRENT_TIMESTAMP WHERE national_id=?",
                        (json.dumps(d), nid),
                    )
                    conn.commit()
                    conn.close()
            except: pass

    # --- تابع جدید برای ذخیره موفقیت ---
    @staticmethod
    def save_success_data(nid, tracking_code):
        """ذخیره کد رهگیری و تغییر وضعیت به موفق"""
        user = DBHandler.get_applicant(nid)
        if user:
            try:
                d = json.loads(user['data'])
                # ذخیره کد رهگیری
                d['tracking_code'] = str(tracking_code).strip()
                
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                # آپدیت دیتا و وضعیت همزمان
                c.execute(
                    "UPDATE applicants SET data=?, status='Success', last_log='ثبت نام موفق', updated_at=CURRENT_TIMESTAMP WHERE national_id=?",
                    (json.dumps(d), nid),
                )
                conn.commit()
                conn.close()
                return True
            except Exception as e: print(f"Save Success Error: {e}")
        return False

    @staticmethod
    def add_event(event: Dict[str, Any], retention_limit: int = 1000):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute(
                """INSERT INTO events (type, ts, level, job_id, bot, nid, message, meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.get("type"),
                    event.get("ts"),
                    event.get("level"),
                    event.get("job_id"),
                    event.get("bot"),
                    event.get("nid"),
                    event.get("message"),
                    json.dumps(event.get("meta") or {}),
                ),
            )
            if retention_limit > 0:
                c.execute(
                    """DELETE FROM events WHERE id NOT IN (
                        SELECT id FROM events ORDER BY id DESC LIMIT ?
                    )""",
                    (retention_limit,),
                )
            conn.commit()
            conn.close()
        except Exception:
            pass

    @staticmethod
    def get_stats() -> Dict[str, Any]:
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM applicants")
            applicants = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM applicants WHERE status LIKE '%Success%'")
            success_count = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM applicants WHERE data LIKE '%\"tracking_code\"%'")
            tracking_codes = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM events")
            events = c.fetchone()[0]
            conn.close()
            return {
                "applicants": applicants,
                "events": events,
                "success_count": success_count,
                "tracking_codes": tracking_codes,
            }
        except Exception:
            return {"applicants": 0, "events": 0, "success_count": 0, "tracking_codes": 0}
