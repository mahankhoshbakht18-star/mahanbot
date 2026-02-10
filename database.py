import sqlite3
import json
import os
import sys
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional


def resource_path(relative_path: str) -> str:
    try:
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


DB_PATH = resource_path("cbi_ultimate.db")


class DBHandler:
    """
    SQLite access layer.

    Goals:
    - reduce 'database is locked' by:
      - WAL mode
      - busy_timeout
      - consistent pragmas on every connection
      - explicit close via context manager
    """

    @staticmethod
    @contextmanager
    def _connect(*, row_factory: bool = False):
        conn = sqlite3.connect(
            DB_PATH,
            timeout=30,            # wait for locks
            check_same_thread=False,  # safer when threads exist (you have JobQueue threads)
        )
        try:
            if row_factory:
                conn.row_factory = sqlite3.Row

            # Pragmas (apply on each connection for safety)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA foreign_keys=ON;")

            yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def init_db():
        try:
            with DBHandler._connect() as conn:
                c = conn.cursor()

                # WAL must be set before heavy write contention begins
                c.execute("PRAGMA journal_mode=WAL;")
                c.execute("PRAGMA synchronous=NORMAL;")
                c.execute("PRAGMA busy_timeout=5000;")
                c.execute("PRAGMA foreign_keys=ON;")

                c.execute(
                    """CREATE TABLE IF NOT EXISTS applicants (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        full_name TEXT,
                        national_id TEXT UNIQUE,
                        status TEXT DEFAULT 'Ready',
                        last_log TEXT DEFAULT '-',
                        data TEXT
                    )"""
                )

                c.execute(
                    """CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )"""
                )

                default_config = json.dumps(
                    {
                        "captcha_delay": 0.1,
                        "retry_count": 1000,
                        "headless": False,
                        "clear_cookies": True,
                        "save_only_mode": False,
                        "sms_auto_resend": True,
                        "captcha_mode": "robot",
                        "final_submit": False,
                    },
                    ensure_ascii=False,
                )
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('config', ?)", (default_config,))
                conn.commit()
        except Exception as e:
            print(f"DB Init Error: {e}")

    @staticmethod
    def get_config() -> Dict[str, Any]:
        try:
            with DBHandler._connect() as conn:
                c = conn.cursor()
                c.execute("SELECT value FROM settings WHERE key='config'")
                row = c.fetchone()
                return json.loads(row[0]) if row else {}
        except Exception:
            return {}

    @staticmethod
    def update_config(new_conf: Dict[str, Any]) -> None:
        try:
            with DBHandler._connect() as conn:
                c = conn.cursor()
                c.execute("UPDATE settings SET value=? WHERE key='config'", (json.dumps(new_conf, ensure_ascii=False),))
                conn.commit()
        except Exception:
            pass

    @staticmethod
    def get_setting(key: str) -> Any:
        try:
            with DBHandler._connect() as conn:
                c = conn.cursor()
                c.execute("SELECT value FROM settings WHERE key=?", (key,))
                row = c.fetchone()
                return json.loads(row[0]) if row else None
        except Exception:
            return None

    @staticmethod
    def update_setting(key: str, value: Any) -> None:
        try:
            with DBHandler._connect() as conn:
                c = conn.cursor()
                c.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value, ensure_ascii=False)),
                )
                conn.commit()
        except Exception:
            pass

    @staticmethod
    def get_applicant(nid: str):
        try:
            with DBHandler._connect(row_factory=True) as conn:
                c = conn.cursor()
                c.execute("SELECT * FROM applicants WHERE national_id=?", (nid,))
                return c.fetchone()
        except Exception:
            return None

    @staticmethod
    def _load_applicant_data(conn: sqlite3.Connection, nid: str) -> Optional[Dict[str, Any]]:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT data FROM applicants WHERE national_id=?", (nid,))
            row = cursor.fetchone()
            if not row:
                return None
            raw = row[0] if isinstance(row, tuple) else row["data"]
            if not raw:
                return {}
            return json.loads(raw)
        except Exception:
            return None

    @staticmethod
    def get_all_applicants():
        try:
            with DBHandler._connect(row_factory=True) as conn:
                c = conn.cursor()
                c.execute("SELECT * FROM applicants ORDER BY id DESC")
                rows = c.fetchall()
                results = []
                for row in rows:
                    r = dict(row)
                    try:
                        r["data"] = json.loads(r["data"]) if r["data"] else {}
                    except Exception:
                        r["data"] = {}
                    results.append(r)
                return results
        except Exception:
            return []

    @staticmethod
    def update_status(nid: str, status: str, log: str) -> None:
        try:
            with DBHandler._connect() as conn:
                c = conn.cursor()
                c.execute(
                    "UPDATE applicants SET status=?, last_log=? WHERE national_id=?",
                    (status, log, nid),
                )
                conn.commit()
        except Exception:
            pass

    @staticmethod
    def save_otp(nid: str, code: str, status: str = "received") -> bool:
        otp_ts = time.time()
        return DBHandler.set_otp(nid, code, ts=otp_ts, status=status)

    @staticmethod
    def set_otp(nid: str, code: str, ts: Optional[float] = None, status: str = "received") -> bool:
        try:
            with DBHandler._connect() as conn:
                data = DBHandler._load_applicant_data(conn, nid)
                if data is None:
                    return False
                data["otp_code"] = str(code).strip()
                data["otp_status"] = status
                data["otp_ts"] = float(ts) if ts is not None else time.time()
                conn.execute(
                    "UPDATE applicants SET data=? WHERE national_id=?",
                    (json.dumps(data, ensure_ascii=False), nid),
                )
                conn.commit()
                return True
        except Exception:
            return False

    @staticmethod
    def get_otp_record(nid: str) -> Optional[Dict[str, Any]]:
        try:
            with DBHandler._connect() as conn:
                data = DBHandler._load_applicant_data(conn, nid)
                if data is None:
                    return None
                code = data.get("otp_code")
                if not code:
                    return None
                return {
                    "otp": str(code).strip(),
                    "ts": data.get("otp_ts"),
                    "status": data.get("otp_status"),
                }
        except Exception:
            return None

    @staticmethod
    def get_otp(nid: str) -> Optional[str]:
        record = DBHandler.get_otp_record(nid)
        if not record:
            return None
        return record.get("otp")

    @staticmethod
    def clear_otp(nid: str) -> None:
        try:
            with DBHandler._connect() as conn:
                data = DBHandler._load_applicant_data(conn, nid)
                if data is None:
                    return
                data.pop("otp_code", None)
                data.pop("otp_status", None)
                data.pop("otp_ts", None)
                conn.execute(
                    "UPDATE applicants SET data=? WHERE national_id=?",
                    (json.dumps(data, ensure_ascii=False), nid),
                )
                conn.commit()
        except Exception:
            pass

    @staticmethod
    def update_applicant_data(nid: str, updates: Dict[str, Any]) -> bool:
        user = DBHandler.get_applicant(nid)
        if not user:
            return False
        try:
            d = json.loads(user["data"]) if user["data"] else {}
            d.update(updates)
            with DBHandler._connect() as conn:
                c = conn.cursor()
                c.execute("UPDATE applicants SET data=? WHERE national_id=?", (json.dumps(d, ensure_ascii=False), nid))
                conn.commit()
            return True
        except Exception:
            return False

    @staticmethod
    def append_captcha_attempt(nid: str, predicted_text: str, result: str, source: Optional[str] = None) -> None:
        user = DBHandler.get_applicant(nid)
        if not user:
            return
        try:
            d = json.loads(user["data"]) if user["data"] else {}
            attempts = d.get("captcha_attempts", [])
            attempts.append(
                {
                    "predicted_text": predicted_text,
                    "result": result,
                    "source": source or "general",
                    "ts": time.time(),
                }
            )
            if len(attempts) > 200:
                attempts = attempts[-200:]
            d["captcha_attempts"] = attempts
            with DBHandler._connect() as conn:
                c = conn.cursor()
                c.execute("UPDATE applicants SET data=? WHERE national_id=?", (json.dumps(d, ensure_ascii=False), nid))
                conn.commit()
        except Exception:
            pass

    @staticmethod
    def add_stopped_bank(nid: str, bank_name: str) -> bool:
        user = DBHandler.get_applicant(nid)
        if not user:
            return False
        try:
            d = json.loads(user["data"]) if user["data"] else {}
            stopped = d.get("stopped_banks", [])
            if bank_name not in stopped:
                stopped.append(bank_name)
            d["stopped_banks"] = stopped
            with DBHandler._connect() as conn:
                c = conn.cursor()
                c.execute("UPDATE applicants SET data=? WHERE national_id=?", (json.dumps(d, ensure_ascii=False), nid))
                conn.commit()
            return True
        except Exception:
            return False

    @staticmethod
    def save_success_data(nid: str, tracking_code: str) -> bool:
        """ذخیره کد رهگیری و تغییر وضعیت به موفق"""
        user = DBHandler.get_applicant(nid)
        if user:
            try:
                d = json.loads(user["data"]) if user["data"] else {}
                d["tracking_code"] = str(tracking_code).strip()

                with DBHandler._connect() as conn:
                    c = conn.cursor()
                    c.execute(
                        "UPDATE applicants "
                        "SET data=?, status='Success', last_log='ثبت نام موفق' "
                        "WHERE national_id=?",
                        (json.dumps(d, ensure_ascii=False), nid),
                    )
                    conn.commit()
                return True
            except Exception as e:
                print(f"Save Success Error: {e}")
        return False
