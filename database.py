import json
import hashlib
import os
import sqlite3
import sys
import time
import hmac
import secrets
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple


def resource_path(relative_path: str) -> str:
    try:
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


DB_PATH = resource_path("cbi_ultimate.db")

DEFAULT_CONFIG = {
    "automation_mode": "normal",
    "default_timeout_ms": 5000,
    "navigation_timeout_ms": 35000,
    "retry_count": 5,
    "retry_delay": 0.25,
    "browser_display_mode": "external",
    "clear_cookies": True,
    "captcha_mode": "model",
    "captcha_poll_interval": 0.1,
    "captcha_retry_delay": 0.25,
    "captcha_max_retries": 3,
    "captcha_manual_timeout": 300,
    "pause_for_manual_captcha": True,
    "pause_for_manual_otp": True,
    "otp_validity_seconds": 120,
    "otp_retry_limit": 5,
    "otp_hard_stop": False,
    "bank_monitor_enabled": True,
    "capacity_check_enabled": True,
    "bank_refresh_interval": 5.0,
    "bank_list_refresh_seconds": 5.0,
    "bank_capacity_recheck_seconds": 20.0,
    "capacity_check_interval_seconds": 20.0,
    "allow_reserve_bank": True,
    "sniper_mode_enabled": False,
    "sniper_interval_ms": 5000,
    "auto_switch_alternative_bank": True,
    "otp_auth_enabled": True,
    "sms_provider": "mock",
    "sms_api_key": "",
    "sms_sender_line": "",
    "sms_pattern_code": "",
    "sms_otp_template": "?? ???? ?? ?????????: {code}\n??????: ? ?????",
    "fallback_to_in_person": False,
    "bank_cooldown_seconds": 30,
    "gsm_modem_enabled": False,
    "gsm_modem_port": "COM3",
    "gsm_modem_baudrate": 115200,
    "default_bank_mode": "online",
    "resume_from_checkpoint": True,
    "save_page_enabled": True,
    "repair_page_enabled": True,
    "save_html": True,
    "save_screenshot": True,
    "save_only_on_bank_change": True,
    "auto_repair_saved_pages": False,
    "final_submit": False,
    "auto_final_submit": False,
    "diagnostic_logging_enabled": True,
    "headless": False,
    "browser_headless": False,
    "browser_window_width": 540,
    "browser_window_height": 680,
    "browser_window_position": "top_right",
    "browser_start_maximized": False,
    "browser_always_on_top": False,
    "browser_remember_position": True,
    "use_proxy": False,
    "proxy_list": "",
    "proxy_rotation_strategy": "round_robin",
    "proxy_health_check_timeout": 4.0,
    "max_concurrency": 4,
    "anti_detection_enabled": True,
}


def _bounded(value, default, low, high, cast=float):
    try:
        parsed = cast(value)
    except (TypeError, ValueError):
        return default
    return parsed if low <= parsed <= high else default


def normalize_config(stored: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = dict(DEFAULT_CONFIG)
    if isinstance(stored, dict):
        merged.update(stored)
    # Legacy manual/robot/api values migrate to the single local-model flow with manual fallback.
    merged["captcha_mode"] = "model"
    if merged.get("automation_mode") not in {"safe", "normal", "fast"}:
        merged["automation_mode"] = "normal"
    merged["retry_count"] = _bounded(merged.get("retry_count"), 5, 1, 20, int)
    merged["retry_delay"] = _bounded(merged.get("retry_delay"), 0.25, 0.0, 30.0)
    merged["captcha_poll_interval"] = _bounded(
        merged.get("captcha_poll_interval"), 0.1, 0.05, 2.0
    )
    merged["captcha_retry_delay"] = _bounded(
        merged.get("captcha_retry_delay"), 0.25, 0.0, 10.0
    )
    merged["captcha_max_retries"] = _bounded(
        merged.get("captcha_max_retries"), 3, 1, 10, int
    )
    merged["captcha_manual_timeout"] = _bounded(
        merged.get("captcha_manual_timeout"), 300, 10, 3600, int
    )
    # Synchronize bank_list_refresh_seconds, bank_list_check_interval, and bank_refresh_interval
    raw_list_refresh = merged.get("bank_list_refresh_seconds")
    if raw_list_refresh is None:
        raw_list_refresh = merged.get("bank_list_check_interval", merged.get("bank_refresh_interval", 5.0))
    list_refresh = _bounded(raw_list_refresh, 5.0, 1.0, 300.0)
    merged["bank_list_refresh_seconds"] = list_refresh
    merged["bank_list_check_interval"] = list_refresh
    merged["bank_refresh_interval"] = _bounded(
        merged.get("bank_refresh_interval", list_refresh), list_refresh, 1.0, 300.0
    )
    capacity_keys = ('bank_capacity_check_interval', 'capacity_check_interval_seconds', 'bank_capacity_recheck_seconds')
    raw_capacity = next((stored[key] for key in capacity_keys if stored and stored.get(key) is not None), 20.0)
    capacity_interval = _bounded(raw_capacity, 20.0, 1.0, 600.0)
    for key in capacity_keys:
        merged[key] = capacity_interval
    merged["otp_validity_seconds"] = _bounded(
        merged.get("otp_validity_seconds"), 120, 30, 600, int
    )
    merged["otp_retry_limit"] = _bounded(
        merged.get("otp_retry_limit"), 5, 1, 20, int
    )
    merged["default_timeout_ms"] = _bounded(
        merged.get("default_timeout_ms"), 5000, 250, 60000, int
    )
    nav_to = merged.get("navigation_timeout_ms")
    if nav_to == 15000 or nav_to is None:
        nav_to = 35000
    merged["navigation_timeout_ms"] = _bounded(nav_to, 35000, 1000, 120000, int)
    # Synchronize auto_final_submit and final_submit (default False)
    if "auto_final_submit" in (stored or {}):
        auto_sub = bool(stored.get("auto_final_submit"))
    elif "final_submit" in (stored or {}):
        auto_sub = bool(stored.get("final_submit"))
    else:
        auto_sub = False
    merged["auto_final_submit"] = auto_sub
    merged["final_submit"] = auto_sub
    merged["pause_for_manual_captcha"] = bool(merged.get("pause_for_manual_captcha", True))
    merged["pause_for_manual_otp"] = bool(merged.get("pause_for_manual_otp", True))
    merged["allow_reserve_bank"] = bool(merged.get("allow_reserve_bank", False))
    merged["sniper_mode_enabled"] = bool(merged.get("sniper_mode_enabled", False))
    merged["sniper_interval_ms"] = _bounded(merged.get("sniper_interval_ms"), 5000, 200, 10000, int)
    merged["auto_switch_alternative_bank"] = bool(merged.get("auto_switch_alternative_bank", True))
    merged["otp_auth_enabled"] = bool(merged.get("otp_auth_enabled", True))
    merged["sms_provider"] = str(merged.get("sms_provider") or "mock").strip().lower()
    merged["sms_api_key"] = str(merged.get("sms_api_key") or "").strip()
    merged["sms_sender_line"] = str(merged.get("sms_sender_line") or "").strip()
    merged["sms_pattern_code"] = str(merged.get("sms_pattern_code") or "").strip()
    merged["sms_otp_template"] = str(merged.get("sms_otp_template") or "?? ???? ?? ?????????: {code}\n??????: ? ?????")
    merged["fallback_to_in_person"] = bool(merged.get("fallback_to_in_person", False))
    merged["bank_cooldown_seconds"] = _bounded(merged.get("bank_cooldown_seconds"), 30, 5, 600, int)
    merged["max_concurrency"] = _bounded(merged.get("max_concurrency"), 4, 1, 32, int)
    merged["anti_detection_enabled"] = bool(merged.get("anti_detection_enabled", True))
    strat = str(merged.get("proxy_rotation_strategy") or "round_robin").strip().lower()
    if strat not in {"round_robin", "per_applicant", "random_healthy"}:
        strat = "round_robin"
    merged["proxy_rotation_strategy"] = strat
    merged["proxy_health_check_timeout"] = _bounded(merged.get("proxy_health_check_timeout"), 4.0, 1.0, 30.0)
    merged["default_bank_mode"] = str(merged.get("default_bank_mode", "online") or "online").strip().lower()
    if merged["default_bank_mode"] not in {"online", "in_person"}:
        merged["default_bank_mode"] = "online"
    merged["resume_from_checkpoint"] = bool(merged.get("resume_from_checkpoint", True))
    merged["save_page_enabled"] = bool(merged.get("save_page_enabled", True))
    merged["repair_page_enabled"] = bool(merged.get("repair_page_enabled", True))
    merged["diagnostic_logging_enabled"] = bool(merged.get("diagnostic_logging_enabled", True))
    if "browser_headless" in (stored or {}):
        b_headless = bool(stored.get("browser_headless"))
    else:
        b_headless = bool(merged.get("headless", False))
    merged["browser_headless"] = b_headless
    merged["headless"] = b_headless
    merged["browser_window_width"] = _bounded(merged.get("browser_window_width"), 540, 360, 3840, int)
    merged["browser_window_height"] = _bounded(merged.get("browser_window_height"), 680, 320, 2160, int)
    pos = str(merged.get("browser_window_position") or "top_right").strip().lower()
    if pos not in {"top_right", "top_left", "bottom_right", "bottom_left", "center", "custom"}:
        pos = "top_right"
    merged["browser_window_position"] = pos
    merged["browser_start_maximized"] = bool(merged.get("browser_start_maximized", False))
    merged["browser_always_on_top"] = bool(merged.get("browser_always_on_top", False))
    merged["browser_remember_position"] = bool(merged.get("browser_remember_position", True))
    return merged


class DBHandler:
    _settings_cache = {}
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
            timeout=60,
            check_same_thread=False,
        )
        try:
            if row_factory:
                conn.row_factory = sqlite3.Row

            try:
                conn.execute("PRAGMA journal_mode=WAL;")
            except sqlite3.OperationalError:
                pass
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=20000;")
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

                pass
                c.execute("PRAGMA synchronous=NORMAL;")
                c.execute("PRAGMA busy_timeout=5000;")
                c.execute("PRAGMA foreign_keys=ON;")
                # Ensure mobile column exists on app_users
                try:
                    c.execute("ALTER TABLE app_users ADD COLUMN mobile TEXT")
                except Exception:
                    pass
                try:
                    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_mobile ON app_users(mobile) WHERE mobile IS NOT NULL")
                except Exception:
                    pass

                # Auth OTP table for multi-factor login & registration
                c.execute(
                    """CREATE TABLE IF NOT EXISTS auth_otps (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        mobile TEXT NOT NULL,
                        code_hash TEXT NOT NULL,
                        code_salt TEXT NOT NULL,
                        purpose TEXT NOT NULL DEFAULT 'login',
                        attempts INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL DEFAULT 5,
                        expires_at REAL NOT NULL,
                        consumed INTEGER NOT NULL DEFAULT 0,
                        ip_address TEXT,
                        created_at REAL NOT NULL
                    )"""
                )
                c.execute("CREATE INDEX IF NOT EXISTS idx_auth_otps_lookup ON auth_otps(mobile, purpose, expires_at, consumed)")

                # Password reset tokens table
                c.execute(
                    """CREATE TABLE IF NOT EXISTS password_reset_tokens (
                        token TEXT PRIMARY KEY,
                        username TEXT NOT NULL COLLATE NOCASE,
                        expires_at REAL NOT NULL,
                        used INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL
                    )"""
                )

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
                c.execute(
                    """CREATE TABLE IF NOT EXISTS app_users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                        password_hash TEXT NOT NULL,
                        password_salt TEXT NOT NULL,
                        expires_at REAL NOT NULL,
                        is_admin INTEGER NOT NULL DEFAULT 0,
                        active INTEGER NOT NULL DEFAULT 1,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )"""
                )

                c.execute(
                    """CREATE TABLE IF NOT EXISTS automation_runtime (
                        nid TEXT PRIMARY KEY,
                        payload TEXT NOT NULL DEFAULT '{}',
                        updated_at REAL NOT NULL
                    )"""
                )

                c.execute(
                    """CREATE TABLE IF NOT EXISTS checkpoints (
                        nid TEXT PRIMARY KEY,
                        payload TEXT NOT NULL DEFAULT '{}',
                        updated_at REAL NOT NULL
                    )"""
                )

                c.execute(
                    """CREATE TABLE IF NOT EXISTS app_sessions (
                        token TEXT PRIMARY KEY,
                        username TEXT NOT NULL COLLATE NOCASE,
                        expires_at REAL NOT NULL,
                        created_at REAL NOT NULL
                    )"""
                )

                default_config = json.dumps(DEFAULT_CONFIG, ensure_ascii=False)
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('config', ?)", (default_config,))
                c.execute("SELECT value FROM settings WHERE key='config'")
                config_row = c.fetchone()
                try:
                    stored_config = json.loads(config_row[0]) if config_row else {}
                except Exception:
                    stored_config = {}
                merged_config = normalize_config(stored_config)
                c.execute(
                    "UPDATE settings SET value=? WHERE key='config'",
                    (json.dumps(merged_config, ensure_ascii=False),),
                )
                c.execute("SELECT value FROM settings WHERE key='browser_profile'")
                bp_row = c.fetchone()
                if bp_row:
                    try:
                        bp_data = json.loads(bp_row[0]) if bp_row[0] else {}
                        if isinstance(bp_data, dict):
                            vp = bp_data.get("viewport") or {}
                            if vp.get("width") == 1280 and vp.get("height") == 720:
                                bp_data["viewport"] = {"width": 900, "height": 620}
                                bp_data["window_width"] = 900
                                bp_data["window_height"] = 620
                                bp_data["window_position"] = "top_right"
                                bp_data["start_maximized"] = False
                                bp_data["headless"] = False
                                c.execute(
                                    "UPDATE settings SET value=? WHERE key='browser_profile'",
                                    (json.dumps(bp_data, ensure_ascii=False),),
                                )
                    except Exception:
                        pass

                for applicant_id, raw_data in c.execute("SELECT id,data FROM applicants").fetchall():
                    try:
                        applicant_data = json.loads(raw_data) if raw_data else {}
                    except Exception:
                        continue
                    changed = False
                    legacy_spouse = str(applicant_data.get("spouse") or "").strip()
                    if "spouse_national_id" not in applicant_data and legacy_spouse.isdigit() and len(legacy_spouse) == 10:
                        applicant_data["spouse_national_id"] = legacy_spouse
                        changed = True
                    # Preserve postal_code / zip_code as 10-digit string with leading zeros
                    raw_zip = applicant_data.get("postal_code") or applicant_data.get("zip_code")
                    if raw_zip is not None and str(raw_zip).strip():
                        clean_zip = str(raw_zip).strip()
                        if clean_zip.isdigit() and len(clean_zip) < 10:
                            clean_zip = clean_zip.zfill(10)
                        if applicant_data.get("zip_code") != clean_zip or applicant_data.get("postal_code") != clean_zip:
                            applicant_data["zip_code"] = clean_zip
                            applicant_data["postal_code"] = clean_zip
                            changed = True
                    if changed:
                        c.execute(
                            "UPDATE applicants SET data=? WHERE id=?",
                            (json.dumps(applicant_data, ensure_ascii=False), applicant_id),
                        )
                c.execute("SELECT 1 FROM app_users LIMIT 1")
                if not c.fetchone():
                    initial_password = os.getenv("MAHANBOT_ADMIN_PASSWORD")
                    if initial_password:
                        DBHandler._upsert_user_with_connection(
                            conn,
                            username=os.getenv("MAHANBOT_ADMIN_USER", "admin"),
                            password=initial_password,
                            days=3650,
                            is_admin=True,
                        )
                    else:
                        print(
                            "No users exist. Set MAHANBOT_ADMIN_PASSWORD before first startup "
                            "to create the initial administrator."
                        )
                conn.commit()
                try:
                    from core.db_migration import run_database_migration
                    run_database_migration(DB_PATH)
                except Exception as me:
                    print(f"Migration sync error in init_db: {me}")
        except Exception as e:
            print(f"DB Init Error: {e}")

    @staticmethod
    def get_applicant_config(nid: str):
        try:
            from core.applicant_config_service import ApplicantConfigurationService
            return ApplicantConfigurationService(DB_PATH).get_applicant_config(nid)
        except Exception:
            return None

    @staticmethod
    def record_bank_page_observation(applicant_id: int, session_id: str, site_province: str, site_city: str, available_banks: list, raw_options: list, **kwargs) -> int:
        try:
            from core.applicant_config_service import ApplicantConfigurationService
            return ApplicantConfigurationService(DB_PATH).record_bank_page_observation(
                applicant_id, session_id, site_province, site_city, available_banks, raw_options, **kwargs
            )
        except Exception:
            return -1

    @staticmethod
    def get_config() -> Dict[str, Any]:
        cached = DBHandler._settings_cache.get("config")
        if cached is not None:
            return dict(cached)
        try:
            with DBHandler._connect() as conn:
                c = conn.cursor()
                c.execute("SELECT value FROM settings WHERE key='config'")
                row = c.fetchone()
                stored = json.loads(row[0]) if row else {}
                norm = normalize_config(stored)
                DBHandler._settings_cache["config"] = norm
                return dict(norm)
        except Exception:
            return dict(DEFAULT_CONFIG)

    @staticmethod
    def update_config(new_conf: Dict[str, Any]) -> Dict[str, Any]:
        new_conf = normalize_config(new_conf)
        DBHandler._settings_cache["config"] = new_conf
        try:
            with DBHandler._connect() as conn:
                c = conn.cursor()
                c.execute(
                    "INSERT INTO settings (key, value) VALUES ('config', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps(new_conf, ensure_ascii=False),),
                )
                conn.commit()
        except Exception:
            pass
        return dict(new_conf)

    @staticmethod
    def get_setting(key: str) -> Any:
        if key in DBHandler._settings_cache:
            return DBHandler._settings_cache[key]
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
        DBHandler._settings_cache[key] = value
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
    def _hash_password(password: str, salt_hex: str) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256", str(password).encode("utf-8"), bytes.fromhex(salt_hex), 210_000
        ).hex()

    @staticmethod
    def _upsert_user_with_connection(conn, username: str, password: str, days: int, is_admin: bool = False):
        username = str(username or "").strip()
        if not username or not password or int(days) < 1:
            raise ValueError("username, password and positive days are required")
        now = time.time()
        salt = secrets.token_hex(16)
        password_hash = DBHandler._hash_password(password, salt)
        expires_at = now + int(days) * 86400
        conn.execute(
            """INSERT INTO app_users
               (username,password_hash,password_salt,expires_at,is_admin,active,created_at,updated_at)
               VALUES (?,?,?,?,?,1,?,?)
               ON CONFLICT(username) DO UPDATE SET
                 password_hash=excluded.password_hash,
                 password_salt=excluded.password_salt,
                 expires_at=excluded.expires_at,
                 is_admin=excluded.is_admin,
                 active=1,
                 updated_at=excluded.updated_at""",
            (username, password_hash, salt, expires_at, int(bool(is_admin)), now, now),
        )
        return expires_at

    @staticmethod
    def save_app_user(username: str, password: str, days: int, is_admin: bool = False) -> float:
        with DBHandler._connect() as conn:
            expires_at = DBHandler._upsert_user_with_connection(
                conn, username=username, password=password, days=days, is_admin=is_admin
            )
            conn.commit()
            return expires_at

    @staticmethod
    def authenticate_app_user(username: str, password: str) -> Optional[Dict[str, Any]]:
        try:
            with DBHandler._connect(row_factory=True) as conn:
                row = conn.execute(
                    "SELECT * FROM app_users WHERE username=? COLLATE NOCASE", (str(username).strip(),)
                ).fetchone()
                if not row or not row["active"] or float(row["expires_at"]) <= time.time():
                    return None
                candidate = DBHandler._hash_password(password, row["password_salt"])
                if not hmac.compare_digest(candidate, row["password_hash"]):
                    return None
                return dict(row)
        except Exception:
            return None

    @staticmethod
    def get_app_user(username: str) -> Optional[Dict[str, Any]]:
        try:
            with DBHandler._connect(row_factory=True) as conn:
                row = conn.execute(
                    "SELECT id,username,expires_at,is_admin,active,created_at,updated_at "
                    "FROM app_users WHERE username=? COLLATE NOCASE", (str(username).strip(),)
                ).fetchone()
                return dict(row) if row else None
        except Exception:
            return None

    @staticmethod
    def list_app_users():
        with DBHandler._connect(row_factory=True) as conn:
            rows = conn.execute(
                "SELECT id,username,expires_at,is_admin,active,created_at,updated_at "
                "FROM app_users ORDER BY is_admin DESC, username"
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def set_app_user_active(username: str, active: bool) -> bool:
        with DBHandler._connect() as conn:
            cur = conn.execute(
                "UPDATE app_users SET active=?,updated_at=? WHERE username=? COLLATE NOCASE",
                (int(bool(active)), time.time(), str(username).strip()),
            )
            conn.commit()
            return cur.rowcount > 0

    @staticmethod
    def save_app_session(token: str, username: str, expires_at: float) -> None:
        """Persist session token across server restarts."""
        if not token:
            return
        with DBHandler._connect() as conn:
            conn.execute(
                "INSERT INTO app_sessions (token, username, expires_at, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(token) DO UPDATE SET username=excluded.username, expires_at=excluded.expires_at",
                (str(token).strip(), str(username).strip(), float(expires_at), time.time()),
            )
            conn.commit()

    @staticmethod
    def get_app_session(token: str) -> Optional[Dict[str, Any]]:
        """Retrieve persistent session if not expired."""
        if not token:
            return None
        try:
            with DBHandler._connect(row_factory=True) as conn:
                row = conn.execute(
                    "SELECT * FROM app_sessions WHERE token=? AND expires_at > ?",
                    (str(token).strip(), time.time()),
                ).fetchone()
                return dict(row) if row else None
        except Exception:
            return None

    @staticmethod
    def delete_app_session(token: str) -> None:
        """Remove session on logout."""
        if not token:
            return
        try:
            with DBHandler._connect() as conn:
                conn.execute("DELETE FROM app_sessions WHERE token=?", (str(token).strip(),))
                conn.commit()
        except Exception:
            pass


    # ── Mobile & OTP Authentication Engine ──────────────────────────────────
    @staticmethod
    def create_auth_otp(mobile: str, code: str, purpose: str = "login", ip: str = None) -> Tuple[bool, str, int]:
        """
        Securely generates and records an OTP verification request with rate-limiting.
        Returns (success, message, cooldown_seconds).
        """
        mobile = str(mobile or "").strip()
        code = str(code or "").strip()
        if not mobile or len(code) != 6:
            return False, "شماره موبایل یا کد نامعتبر است.", 0

        now = time.time()
        cooldown_window = 120  # 2 minutes cooldown between requests
        max_10m_requests = 4   # max 4 requests per 10 minutes

        try:
            with DBHandler._connect(row_factory=True) as conn:
                # 1. Check resend cooldown
                row = conn.execute(
                    "SELECT created_at FROM auth_otps WHERE mobile=? AND purpose=? ORDER BY id DESC LIMIT 1",
                    (mobile, purpose),
                ).fetchone()
                if row:
                    elapsed = now - float(row["created_at"])
                    if elapsed < cooldown_window:
                        remaining = int(cooldown_window - elapsed)
                        return False, f"لطفاً {remaining} ثانیه دیگر جهت درخواست مجدد کد شکیبا باشید.", remaining

                # 2. Check 10-minute rate limit
                count_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM auth_otps WHERE mobile=? AND created_at > ?",
                    (mobile, now - 600),
                ).fetchone()
                if count_row and int(count_row["c"]) >= max_10m_requests:
                    return False, "تعداد درخواست‌های کد برای این شماره بیش از حد مجاز است. لطفاً ۱۰ دقیقه دیگر تلاش کنید.", 0

                # 3. Hash code with fresh salt
                salt = secrets.token_hex(16)
                code_hash = DBHandler._hash_password(code, salt)
                expires_at = now + 300  # 5 minutes validity

                conn.execute(
                    """INSERT INTO auth_otps
                       (mobile, code_hash, code_salt, purpose, attempts, max_attempts, expires_at, consumed, ip_address, created_at)
                       VALUES (?, ?, ?, ?, 0, 5, ?, 0, ?, ?)""",
                    (mobile, code_hash, salt, purpose, expires_at, str(ip or ""), now),
                )
                conn.commit()
                return True, "کد تایید امن صادر گردید.", cooldown_window
        except Exception as exc:
            print(f"[create_auth_otp error] {exc}")
            return False, "خطای پایگاه‌داده در صدور کد تایید.", 0

    @staticmethod
    def verify_auth_otp(mobile: str, code: str, purpose: str = "login") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Validates OTP code, guards against Brute-Force, prevents replay, and returns matched user if any.
        Returns (success, message, user_or_none).
        """
        mobile = str(mobile or "").strip()
        code = str(code or "").strip()
        if not mobile or not code:
            return False, "شماره موبایل و کد الزامی هستند.", None

        now = time.time()
        try:
            with DBHandler._connect(row_factory=True) as conn:
                # Find latest active unconsumed OTP
                row = conn.execute(
                    """SELECT * FROM auth_otps
                       WHERE mobile=? AND purpose=? AND consumed=0 AND expires_at > ?
                       ORDER BY id DESC LIMIT 1""",
                    (mobile, purpose, now),
                ).fetchone()

                if not row:
                    return False, "کد تایید منقضی شده یا نامعتبر است. لطفاً مجدداً کد دریافت کنید.", None

                otp_id = row["id"]
                attempts = int(row["attempts"])
                max_attempts = int(row["max_attempts"])

                if attempts >= max_attempts:
                    conn.execute("UPDATE auth_otps SET consumed=1 WHERE id=?", (otp_id,))
                    conn.commit()
                    return False, "تعداد دفعات تلاش اشتباه بیش از حد مجاز بود. کد باطل شد؛ لطفاً کد جدید دریافت کنید.", None

                candidate = DBHandler._hash_password(code, row["code_salt"])
                if not hmac.compare_digest(candidate, row["code_hash"]):
                    new_attempts = attempts + 1
                    consumed = 1 if new_attempts >= max_attempts else 0
                    conn.execute("UPDATE auth_otps SET attempts=?, consumed=? WHERE id=?", (new_attempts, consumed, otp_id))
                    conn.commit()
                    rem = max(0, max_attempts - new_attempts)
                    if rem == 0:
                        return False, "کد اشتباه بود و پس از ۵ بار تلاش باطل شد. لطفاً کد جدید بگیرید.", None
                    return False, f"کد تایید وارد شده نادرست است ({rem} تلاش دیگر باقی‌مانده).", None

                # Correct code: mark consumed immediately
                conn.execute("UPDATE auth_otps SET consumed=1 WHERE id=?", (otp_id,))
                conn.commit()

                # Find associated user
                user_row = conn.execute(
                    "SELECT * FROM app_users WHERE mobile=?",
                    (mobile,),
                ).fetchone()

                if user_row:
                    user_data = dict(user_row)
                    if not user_data.get("active"):
                        return False, "حساب کاربری متصل به این شماره غیرفعال شده است.", None
                    if float(user_data.get("expires_at", 0)) <= now:
                        return False, "اعتبار اشتراک این حساب به پایان رسیده است.", None
                    return True, "احراز هویت موفق با پیامک.", user_data

                return True, "شماره موبایل با موفقیت تایید شد.", None
        except Exception as exc:
            print(f"[verify_auth_otp error] {exc}")
            return False, "خطا در تایید کد پیامکی.", None

    @staticmethod
    def get_user_by_mobile(mobile: str) -> Optional[Dict[str, Any]]:
        try:
            with DBHandler._connect(row_factory=True) as conn:
                row = conn.execute("SELECT * FROM app_users WHERE mobile=?", (str(mobile).strip(),)).fetchone()
                return dict(row) if row else None
        except Exception:
            return None

    @staticmethod
    def link_mobile_to_user(username: str, mobile: str) -> Tuple[bool, str]:
        username = str(username or "").strip()
        mobile = str(mobile or "").strip()
        if not username or not mobile:
            return False, "نام کاربری و شماره موبایل الزامی است."
        try:
            with DBHandler._connect(row_factory=True) as conn:
                # Check if mobile taken by another user
                existing = conn.execute(
                    "SELECT username FROM app_users WHERE mobile=? AND username!=? COLLATE NOCASE",
                    (mobile, username),
                ).fetchone()
                if existing:
                    return False, f"این شماره موبایل قبلاً به حساب '{existing['username']}' متصل شده است."

                cur = conn.execute(
                    "UPDATE app_users SET mobile=?, updated_at=? WHERE username=? COLLATE NOCASE",
                    (mobile, time.time(), username),
                )
                conn.commit()
                if cur.rowcount > 0:
                    return True, "شماره موبایل با موفقیت به حساب کاربری متصل شد."
                return False, "کاربر یافت نشد."
        except Exception as exc:
            return False, f"خطای دیتابیس: {exc}"

    @staticmethod
    def create_user_with_mobile(username: str, mobile: str, password: str = None, days: int = 30, is_admin: bool = False) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        username = str(username or "").strip()
        mobile = str(mobile or "").strip()
        if not username or not mobile:
            return False, "نام کاربری و شماره موبایل الزامی است.", None

        now = time.time()
        pass_to_use = password or secrets.token_urlsafe(12)
        salt = secrets.token_hex(16)
        p_hash = DBHandler._hash_password(pass_to_use, salt)
        expires_at = now + int(days) * 86400

        try:
            with DBHandler._connect(row_factory=True) as conn:
                # Check duplicate username
                if conn.execute("SELECT 1 FROM app_users WHERE username=? COLLATE NOCASE", (username,)).fetchone():
                    return False, "این نام کاربری قبلاً ثبت شده است.", None
                # Check duplicate mobile
                if conn.execute("SELECT 1 FROM app_users WHERE mobile=?", (mobile,)).fetchone():
                    return False, "این شماره موبایل قبلاً ثبت شده است.", None

                conn.execute(
                    """INSERT INTO app_users
                       (username, mobile, password_hash, password_salt, expires_at, is_admin, active, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (username, mobile, p_hash, salt, expires_at, int(bool(is_admin)), now, now),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM app_users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
                return True, "حساب کاربری با موفقیت ایجاد شد.", dict(row)
        except Exception as exc:
            return False, f"خطای دیتابیس در ایجاد کاربر: {exc}", None

    @staticmethod
    def create_password_reset_token(username: str) -> Optional[str]:
        username = str(username or "").strip()
        if not username:
            return None
        token = secrets.token_urlsafe(32)
        now = time.time()
        expires_at = now + 900  # 15 minutes
        try:
            with DBHandler._connect() as conn:
                conn.execute(
                    "INSERT INTO password_reset_tokens (token, username, expires_at, used, created_at) VALUES (?, ?, ?, 0, ?)",
                    (token, username, expires_at, now),
                )
                conn.commit()
            return token
        except Exception:
            return None

    @staticmethod
    def reset_password_with_token(token: str, new_password: str) -> Tuple[bool, str]:
        token = str(token or "").strip()
        if not token or len(new_password) < 4:
            return False, "توکن نامعتبر یا گذرواژه کمتر از ۴ حرف است."
        now = time.time()
        try:
            with DBHandler._connect(row_factory=True) as conn:
                row = conn.execute(
                    "SELECT * FROM password_reset_tokens WHERE token=? AND used=0 AND expires_at > ?",
                    (token, now),
                ).fetchone()
                if not row:
                    return False, "لینک یا توکن بازیابی رمز عبور منقضی یا استفاده شده است."

                username = row["username"]
                salt = secrets.token_hex(16)
                p_hash = DBHandler._hash_password(new_password, salt)

                conn.execute(
                    "UPDATE app_users SET password_hash=?, password_salt=?, updated_at=? WHERE username=? COLLATE NOCASE",
                    (p_hash, salt, now, username),
                )
                conn.execute("UPDATE password_reset_tokens SET used=1 WHERE token=?", (token,))
                conn.commit()
                return True, "رمز عبور با موفقیت به‌روزرسانی شد. اکنون می‌توانید وارد شوید."
        except Exception as exc:
            return False, f"خطای پایگاه‌داده: {exc}"

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
        otp_ts = DBHandler._current_timestamp_seconds_ms()
        return DBHandler.set_otp(nid, code, ts=otp_ts, status=status)

    @staticmethod
    def set_otp(nid: str, code: str, ts: Optional[float] = None, status: str = "received", request_id: Optional[str] = None) -> bool:
        try:
            with DBHandler._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                data = DBHandler._load_applicant_data(conn, nid)
                if data is None:
                    return False
                data["otp_code"] = str(code).strip()
                data["otp_status"] = status
                if request_id is not None:
                    data["otp_request_id"] = str(request_id)
                timestamp_seconds = float(ts) if ts is not None else DBHandler._current_timestamp_seconds_ms()
                data["otp_ts"] = timestamp_seconds
                data["otp_ts_ms"] = int(timestamp_seconds * 1000)
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
                    "ts_ms": data.get("otp_ts_ms"),
                    "request_id": data.get("otp_request_id"),
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
                conn.execute('BEGIN IMMEDIATE')
                data = DBHandler._load_applicant_data(conn, nid)
                if data is None:
                    return
                data.pop("otp_code", None)
                data.pop("otp_status", None)
                data.pop("otp_ts", None)
                data.pop("otp_ts_ms", None)
                data.pop("otp_request_id", None)
                conn.execute(
                    "UPDATE applicants SET data=? WHERE national_id=?",
                    (json.dumps(data, ensure_ascii=False), nid),
                )
                conn.commit()
        except Exception:
            pass

    @staticmethod
    def update_applicant_data(nid: str, updates: Dict[str, Any]) -> bool:
        try:
            with DBHandler._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                d = DBHandler._load_applicant_data(conn, nid)
                if d is None:
                    return False
                d.update(updates)
                conn.execute("UPDATE applicants SET data=? WHERE national_id=?", (json.dumps(d, ensure_ascii=False), nid))
                conn.commit()
            return True
        except Exception:
            return False

    @staticmethod
    def get_runtime(nid: str) -> Dict[str, Any]:
        try:
            with DBHandler._connect() as conn:
                row = conn.execute("SELECT payload FROM automation_runtime WHERE nid=?", (nid,)).fetchone()
                return json.loads(row[0]) if row and row[0] else {}
        except Exception:
            return {}

    @staticmethod
    def update_runtime(nid: str, **updates: Any) -> Dict[str, Any]:
        now = time.time()
        try:
            with DBHandler._connect() as conn:
                row = conn.execute("SELECT payload FROM automation_runtime WHERE nid=?", (nid,)).fetchone()
                try:
                    payload = json.loads(row[0]) if row and row[0] else {}
                except Exception:
                    payload = {}
                payload.update(updates)
                payload["last_activity_at"] = updates.get("last_activity_at", now)
                conn.execute(
                    """INSERT INTO automation_runtime(nid,payload,updated_at) VALUES(?,?,?)
                       ON CONFLICT(nid) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at""",
                    (nid, json.dumps(payload, ensure_ascii=False), now),
                )
                conn.commit()
                return payload
        except Exception:
            return {}

    @staticmethod
    def reset_runtime(nid: str, *, run_id: str = "", target_bank: str = "") -> Dict[str, Any]:
        payload = {
            "run_id": run_id,
            "status": "STARTING",
            "current_step": "OPEN_BROWSER",
            "browser_status": "STARTING",
            "banks_available": [],
            "banks_previous": [],
            "new_banks": [],
            "removed_banks": [],
            "target_bank": target_bank,
            "selected_mode": "",
            "selected_branch": "",
            "last_refresh": None,
            "next_refresh": None,
            "retry_count": 0,
            "last_error": "",
            "metrics": {},
            "last_activity_at": time.time(),
        }
        try:
            with DBHandler._connect() as conn:
                conn.execute(
                    """INSERT INTO automation_runtime(nid,payload,updated_at) VALUES(?,?,?)
                       ON CONFLICT(nid) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at""",
                    (nid, json.dumps(payload, ensure_ascii=False), time.time()),
                )
                conn.commit()
        except Exception:
            pass
        return payload

    # ── Checkpoint (Save Page / Resume) ──────────────────────────────────
    @staticmethod
    def save_checkpoint(nid: str, checkpoint_data: Dict[str, Any]) -> Dict[str, Any]:
        """Persist process checkpoint without ever storing OTP or CAPTCHA codes."""
        now = time.time()
        sanitized = dict(checkpoint_data or {})
        # Strictly strip OTP and CAPTCHA values
        for forbidden in ("otp", "otp_code", "captcha", "captcha_code", "cookies", "password"):
            sanitized.pop(forbidden, None)

        user = DBHandler.get_applicant(nid)
        applicant_id = None
        user_data = {}
        if user:
            try:
                u_dict = dict(user)
                applicant_id = u_dict.get("id")
                user_data = json.loads(u_dict.get("data") or "{}")
            except Exception:
                pass

        existing = DBHandler.get_checkpoint(nid)
        merged = dict(existing)
        merged.update(sanitized)
        merged.setdefault("applicant_id", applicant_id)
        merged["nid_masked"] = f"{str(nid)[:2]}******{str(nid)[-2:]}" if len(str(nid)) >= 4 else "****"
        merged.setdefault("loan_type", user_data.get("loan_type", "marriage"))
        merged.setdefault("state", user_data.get("state", ""))
        merged.setdefault("city", user_data.get("city", ""))
        merged.setdefault("priority_banks", user_data.get("priority_banks") or user_data.get("banks") or [])
        merged.setdefault("selected_bank", "")
        merged.setdefault("selected_branch", "")
        merged.setdefault("bank_mode", "")
        merged.setdefault("last_verified_step", "IDLE")
        merged.setdefault("expected_next_step", "STEP_1_ENTRY_FORM")
        merged.setdefault("last_valid_url", "")
        merged.setdefault("last_error_reason", "")
        merged.setdefault("session_active", True)
        merged.setdefault("can_resume", True)
        merged.setdefault("last_operation_result", "")
        merged.setdefault("tracking_code", user_data.get("tracking_code") or "")
        merged["saved_at"] = now

        try:
            with DBHandler._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS checkpoints (
                        nid TEXT PRIMARY KEY,
                        payload TEXT NOT NULL DEFAULT '{}',
                        updated_at REAL NOT NULL
                    )"""
                )
                row = conn.execute("SELECT payload FROM checkpoints WHERE nid=?", (nid,)).fetchone()
                current_payload = json.loads(row[0]) if row and row[0] else {}
                if current_payload.get("final_submit_status") and not sanitized.get("final_submit_status"):
                    merged["final_submit_status"] = current_payload["final_submit_status"]
                if current_payload.get("final_submit_started_at") and not sanitized.get("final_submit_started_at"):
                    merged["final_submit_started_at"] = current_payload["final_submit_started_at"]
                if current_payload.get("final_submit_attempts") and not sanitized.get("final_submit_attempts"):
                    merged["final_submit_attempts"] = current_payload["final_submit_attempts"]
                conn.execute(
                    """INSERT INTO checkpoints(nid, payload, updated_at) VALUES(?,?,?)
                       ON CONFLICT(nid) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at""",
                    (nid, json.dumps(merged, ensure_ascii=False), now),
                )
                conn.commit()
        except Exception:
            pass
        return merged

    @staticmethod
    def get_checkpoint(nid: str) -> Dict[str, Any]:
        try:
            with DBHandler._connect() as conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS checkpoints (
                        nid TEXT PRIMARY KEY,
                        payload TEXT NOT NULL DEFAULT '{}',
                        updated_at REAL NOT NULL
                    )"""
                )
                row = conn.execute("SELECT payload FROM checkpoints WHERE nid=?", (nid,)).fetchone()
                return json.loads(row[0]) if row and row[0] else {}
        except Exception:
            return {}

    @staticmethod
    def clear_checkpoint(nid: str) -> None:
        try:
            with DBHandler._connect() as conn:
                conn.execute("DELETE FROM checkpoints WHERE nid=?", (nid,))
                conn.commit()
        except Exception:
            pass

    @staticmethod
    def record_final_submission_attempt(nid: str) -> None:
        """Keep audit counters without locking an applicant until registration succeeds."""
        with DBHandler._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT payload FROM checkpoints WHERE nid=?', (nid,)).fetchone()
            data = json.loads(row[0]) if row and row[0] else {}
            data.update(final_submit_attempts=int(data.get('final_submit_attempts') or 0) + 1,
                        last_submit_started_at=time.time(), last_submit_outcome='in_flight')
            conn.execute("INSERT INTO checkpoints(nid,payload,updated_at) VALUES(?,?,?) ON CONFLICT(nid) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                         (nid, json.dumps(data, ensure_ascii=False), time.time()))
            conn.commit()

    @staticmethod
    def claim_final_submission(nid: str) -> bool:
        """Durable single-flight latch, written BEFORE a final-submit click (including timeouts)."""
        with DBHandler._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS checkpoints (
                    nid TEXT PRIMARY KEY,
                    payload TEXT NOT NULL DEFAULT '{}',
                    updated_at REAL NOT NULL
                )"""
            )
            row = conn.execute("SELECT payload FROM checkpoints WHERE nid=?", (nid,)).fetchone()
            payload = json.loads(row[0]) if row and row[0] else {}
            if payload.get("final_submit_status") or payload.get("tracking_code"):
                return False
            payload.update(
                final_submit_status="pending",
                final_submit_attempts=int(payload.get("final_submit_attempts") or 0) + 1,
                final_submit_started_at=time.time(),
            )
            conn.execute(
                """INSERT INTO checkpoints(nid,payload,updated_at) VALUES(?,?,?)
                   ON CONFLICT(nid) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at""",
                (nid, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            conn.commit()
            return True

    @staticmethod
    def release_final_submission_claim(nid: str) -> None:
        """Clear a pending final-submit latch so retries are permitted."""
        with DBHandler._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM checkpoints WHERE nid=?", (nid,)).fetchone()
            if row and row[0]:
                payload = json.loads(row[0])
                if payload.get("final_submit_status") == "pending":
                    payload.pop("final_submit_status", None)
                    payload.pop("final_submit_started_at", None)
                    conn.execute(
                        "UPDATE checkpoints SET payload=?, updated_at=? WHERE nid=?",
                        (json.dumps(payload, ensure_ascii=False), time.time(), nid),
                    )
            conn.commit()

    @staticmethod
    def has_final_submission(nid: str) -> Optional[str]:
        """Return tracking code if final submission was already confirmed for this applicant."""
        user = DBHandler.get_applicant(nid)
        if user:
            try:
                u_dict = dict(user)
                d = json.loads(u_dict.get("data") or "{}")
                tc = str(d.get("tracking_code") or "").strip()
                if tc and tc != "-":
                    return tc
                if str(u_dict.get("status") or "").lower() == "success" and d.get("final_submitted"):
                    return tc or "CONFIRMED"
            except Exception:
                pass
        cp = DBHandler.get_checkpoint(nid)
        if cp:
            tc = str(cp.get("tracking_code") or "").strip()
            if tc and tc != "-":
                return tc
            if cp.get("last_verified_step") == "DONE" and cp.get("last_operation_result") == "FINAL_SUBMIT_CONFIRMED":
                return tc or "CONFIRMED"
        return None

    @staticmethod
    def update_last_log(nid: str, log: str) -> None:
        try:
            with DBHandler._connect() as conn:
                conn.execute(
                    "UPDATE applicants SET last_log=? WHERE national_id=?",
                    (str(log), nid),
                )
                conn.commit()
        except Exception:
            pass

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
        clean_code = str(tracking_code or "").strip()
        user = DBHandler.get_applicant(nid)
        if user:
            try:
                with DBHandler._connect() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    d = DBHandler._load_applicant_data(conn, nid)
                    if d is None:
                        return False
                    d["tracking_code"] = clean_code
                    d["final_submitted"] = True
                    d["final_submitted_at"] = time.time()
                    c = conn.cursor()
                    c.execute(
                        "UPDATE applicants "
                        "SET data=?, status='Success', last_log='ثبت نام موفق' "
                        "WHERE national_id=?",
                        (json.dumps(d, ensure_ascii=False), nid),
                    )
                    conn.commit()
                DBHandler.save_checkpoint(
                    nid,
                    {
                        "last_verified_step": "DONE",
                        "expected_next_step": "NONE",
                        "last_operation_result": "FINAL_SUBMIT_CONFIRMED",
                        "tracking_code": clean_code,
                        "can_resume": False,
                    },
                )
                return True
            except Exception as e:
                print(f"Save Success Error: {e}")
        return False

    @staticmethod
    def save_recovered_tracking_code(nid: str, tracking_code: str) -> bool:
        """ذخیره اختصاصی کد رهگیری بازیابی‌شده و به‌روزرسانی آنی وضعیت متقاضی"""
        clean_code = str(tracking_code or "").strip()
        if not clean_code or clean_code == "-":
            return False
        user = DBHandler.get_applicant(nid)
        if not user:
            return False
        try:
            with DBHandler._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                d = DBHandler._load_applicant_data(conn, nid) or {}
                d["tracking_code"] = clean_code
                d["final_submitted"] = True
                d["final_submitted_at"] = time.time()
                d["recovered_at"] = time.time()
                c = conn.cursor()
                c.execute(
                    "UPDATE applicants "
                    "SET data=?, status='Success', last_log=? "
                    "WHERE national_id=?",
                    (json.dumps(d, ensure_ascii=False), f"کد رهگیری بازیابی شد: {clean_code}", nid),
                )
                conn.commit()
            DBHandler.save_checkpoint(
                nid,
                {
                    "last_verified_step": "DONE",
                    "expected_next_step": "NONE",
                    "last_operation_result": "TRACKING_CODE_RECOVERED",
                    "tracking_code": clean_code,
                    "can_resume": False,
                },
            )
            # Emit snapshot to live UI
            try:
                from core.helpers import _emit_applicants_snapshot
                _emit_applicants_snapshot()
            except Exception:
                pass
            try:
                from event_logger import emit_structured_event
                emit_structured_event(
                    nid,
                    "REGISTRATION_CONFIRMED",
                    step="DONE",
                    result=clean_code,
                    reason=f"کد رهگیری {clean_code} با موفقیت از سامانه مرکزی بازیابی و ذخیره شد."
                )
            except Exception:
                pass
            return True
        except Exception as e:
            print(f"Save Recovered Tracking Code Error: {e}")
            return False

    @staticmethod
    def clear_request_data(nid: str, note: str = "ثبت‌نام از سامانه بانک مرکزی حذف شد") -> bool:
        """پاکسازی کد رهگیری و بازنشانی پرونده پس از حذف موفق از سامانه بانک مرکزی"""
        user = DBHandler.get_applicant(nid)
        if not user:
            return False
        try:
            with DBHandler._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                d = DBHandler._load_applicant_data(conn, nid) or {}
                d["tracking_code"] = ""
                d["final_submitted"] = False
                d.pop("final_submitted_at", None)
                d.pop("final_submit_status", None)
                c = conn.cursor()
                c.execute(
                    "UPDATE applicants "
                    "SET data=?, status='Ready', last_log=? "
                    "WHERE national_id=?",
                    (json.dumps(d, ensure_ascii=False), note, nid),
                )
                conn.execute("DELETE FROM checkpoints WHERE nid=?", (nid,))
                conn.commit()
            return True
        except Exception as e:
            print(f"Clear Request Error: {e}")
            return False

    @staticmethod
    def _current_timestamp_seconds_ms() -> float:
        """Return current unix timestamp in seconds with millisecond precision."""
        return time.time_ns() / 1_000_000_000
