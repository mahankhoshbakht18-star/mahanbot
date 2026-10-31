"""
Browser Profile Health Manager for MAHANBOT.
Monitors, diagnoses, and classifies Chromium persistent context profiles,
cookie stores, storage integrity, session status, and CAPTCHA states.
"""

from __future__ import annotations

import os
import time
import sqlite3
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List


class ProfileHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    SESSION_VALID = "SESSION_VALID"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    SESSION_REAUTH_REQUIRED = "SESSION_REAUTH_REQUIRED"
    PROFILE_LOCKED = "PROFILE_LOCKED"
    PROFILE_STORAGE_ERROR = "PROFILE_STORAGE_ERROR"
    CAPTCHA_FORM_ERROR = "CAPTCHA_FORM_ERROR"
    SERVER_REJECTED = "SERVER_REJECTED"
    SECURITY_CHALLENGE = "SECURITY_CHALLENGE"
    UNKNOWN = "UNKNOWN"


@dataclass
class ProfileHealthReport:
    nid: str
    health_status: ProfileHealthStatus
    session_status: str
    browser_status: str
    is_locked: bool
    lock_reason: Optional[str] = None
    storage_integrity: str = "ok"
    cookie_count: int = 0
    has_cbi_cookies: bool = False
    has_expired_cbi_cookies: bool = False
    last_successful_login: Optional[float] = None
    last_error: Optional[str] = None
    captcha_status: str = "OK"
    reauth_required: bool = False
    disk_size_bytes: int = 0
    file_count: int = 0
    details: Dict[str, Any] = field(default_factory=dict)
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["health_status"] = self.health_status.value if isinstance(self.health_status, ProfileHealthStatus) else str(self.health_status)
        return d


class BrowserProfileHealthManager:
    """Central authority for auditing and classifying applicant browser profiles."""

    DEFAULT_PROFILE_ROOT = Path(__file__).resolve().parent.parent / "chrome_profiles"

    @classmethod
    def get_profile_root(cls, custom_root: Optional[Path | str] = None) -> Path:
        if custom_root:
            return Path(custom_root).resolve()
        env_root = os.getenv("MAHANBOT_PROFILE_ROOT")
        if env_root:
            return Path(env_root).resolve()
        return cls.DEFAULT_PROFILE_ROOT

    @classmethod
    def get_applicant_profile_dir(cls, nid: str, profile_root: Optional[Path | str] = None) -> Path:
        root = cls.get_profile_root(profile_root)
        return root / str(nid)

    @classmethod
    def check_lock_status(cls, profile_dir: Path) -> Tuple[bool, Optional[str]]:
        """
        Check if the profile directory is currently locked by a running Chromium process
        or another worker process.
        """
        if not profile_dir.exists():
            return False, None

        # 1. Chromium root locks
        for lock_name in ("SingletonLock", "lockfile"):
            lk = profile_dir / lock_name
            if lk.exists():
                return True, f"Chromium lock file present: {lock_name}"

        # 2. Check SQLite network cookies lock (exclusive lock on Windows)
        cookie_file = profile_dir / "Default" / "Network" / "Cookies"
        if cookie_file.exists():
            try:
                # Try opening in append/update mode without truncate to test OS sharing
                with open(cookie_file, "r+b"):
                    pass
            except (IOError, PermissionError) as exc:
                return True, f"Cookie database locked by active process ({exc})"

        # 3. Check LevelDB lock
        leveldb_lock = profile_dir / "Default" / "LOCK"
        if leveldb_lock.exists():
            try:
                with open(leveldb_lock, "r+b"):
                    pass
            except (IOError, PermissionError) as exc:
                return True, f"LevelDB locked by active process ({exc})"

        return False, None

    @classmethod
    def check_storage_integrity(cls, profile_dir: Path) -> Tuple[bool, str]:
        """
        Check integrity of SQLite databases within the profile (e.g. Cookies, Web Data).
        """
        if not profile_dir.exists():
            return True, "Profile directory does not exist yet (clean)"

        cookie_file = profile_dir / "Default" / "Network" / "Cookies"
        if not cookie_file.exists():
            # Legacy path check
            cookie_file = profile_dir / "Default" / "Cookies"

        if not cookie_file.exists():
            return True, "No Cookies database created yet (clean)"

        conn = None
        try:
            # Use read-only URI mode to prevent modifying WAL/SHM
            abs_path = os.path.abspath(str(cookie_file))
            uri = f"file:{abs_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=2.0)
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check")
            res = cur.fetchone()
            if res and res[0] == "ok":
                return True, "ok"
            return False, f"Integrity check failed: {res[0] if res else 'Unknown'}"
        except Exception as exc:
            return False, f"Storage access error: {exc}"
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    @classmethod
    def inspect_cookies(cls, profile_dir: Path) -> Dict[str, Any]:
        """
        Inspect cookies safely without modifying the database or leaking secrets.
        Returns metadata: count, cbi_cookie_names, expired_cbi_count, etc.
        """
        result = {
            "exists": False,
            "count": 0,
            "has_cbi": False,
            "has_expired_cbi": False,
            "cbi_cookie_names": [],
            "expired_cbi_names": [],
            "error": None,
        }
        cookie_file = profile_dir / "Default" / "Network" / "Cookies"
        if not cookie_file.exists():
            cookie_file = profile_dir / "Default" / "Cookies"
        if not cookie_file.exists():
            return result

        result["exists"] = True
        conn = None
        try:
            abs_path = os.path.abspath(str(cookie_file))
            uri = f"file:{abs_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=2.0)
            cur = conn.cursor()
            cur.execute(
                "SELECT host_key, name, expires_utc, is_persistent, has_expires FROM cookies"
            )
            rows = cur.fetchall()

            result["count"] = len(rows)
            # Chrome epoch is microseconds since 1601-01-01
            now_chrome = int((time.time() + 11644473600) * 1000000)

            for host, name, expires_utc, is_persistent, has_expires in rows:
                is_cbi = "cbi.ir" in str(host).lower() or str(name).lower().startswith("cbi")
                if is_cbi:
                    result["has_cbi"] = True
                    result["cbi_cookie_names"].append(str(name))
                    # Check if expired
                    if has_expires and expires_utc and expires_utc < now_chrome:
                        result["has_expired_cbi"] = True
                        result["expired_cbi_names"].append(str(name))
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        return result

    @classmethod
    def inspect_profile(
        cls,
        nid: str,
        profile_root: Optional[Path | str] = None,
        live_page: Optional[Any] = None,
    ) -> ProfileHealthReport:
        """
        Deep diagnostic analysis of an applicant's profile and session health.
        """
        p_dir = cls.get_applicant_profile_dir(nid, profile_root)
        profile_exists = p_dir.exists()

        # Measure directory size and file count
        disk_size = 0
        file_count = 0
        if profile_exists:
            for root, _, files in os.walk(p_dir):
                file_count += len(files)
                for f in files:
                    try:
                        disk_size += os.path.getsize(os.path.join(root, f))
                    except Exception:
                        pass

        # Check lock status
        is_locked, lock_reason = cls.check_lock_status(p_dir)

        # Check SQLite integrity
        integrity_ok, integrity_msg = cls.check_storage_integrity(p_dir)

        # Inspect cookies
        cookie_info = cls.inspect_cookies(p_dir)

        # Retrieve DB state & checkpoint if available
        session_status = "UNKNOWN"
        browser_status = "CLOSED"
        last_error = None
        last_success = None
        checkpoint_step = None
        tracking_code = None

        try:
            from database import DBHandler
            rt = DBHandler.get_runtime(nid) or {}
            cp = DBHandler.get_checkpoint(nid) or {}
            app = DBHandler.get_applicant(nid) or {}

            browser_status = rt.get("browser_status", "CLOSED")
            last_error = rt.get("last_error") or app.get("last_log")
            checkpoint_step = cp.get("last_verified_step")
            tracking_code = cp.get("tracking_code") or app.get("data", {}).get("tracking_code") if isinstance(app.get("data"), dict) else None

            if tracking_code:
                last_success = cp.get("saved_at")
        except Exception:
            pass

        # Determine CAPTCHA status and server rejection
        captcha_status = "OK"
        if last_error:
            le_lower = str(last_error).lower()
            if "captcha" in le_lower:
                captcha_status = "FAILED_OR_WAITING"
            elif "support id" in le_lower or "rejected" in le_lower:
                captcha_status = "SERVER_WAF_REJECTED"

        # Determine overarching health status
        health_status = ProfileHealthStatus.HEALTHY
        reauth_required = False

        if not integrity_ok:
            health_status = ProfileHealthStatus.PROFILE_STORAGE_ERROR
        elif is_locked:
            health_status = ProfileHealthStatus.PROFILE_LOCKED
        elif last_error and ("support id" in str(last_error).lower() or "site_rejected" in str(last_error).lower()):
            health_status = ProfileHealthStatus.SERVER_REJECTED
        elif last_error and ("firewall" in str(last_error).lower() or "challenge" in str(last_error).lower()):
            health_status = ProfileHealthStatus.SECURITY_CHALLENGE
        elif checkpoint_step == "STEP_3_BANK_SELECT" and not cookie_info.get("has_expired_cbi"):
            health_status = ProfileHealthStatus.SESSION_VALID
            session_status = "VALID_STEP3"
        elif checkpoint_step == "STEP_3_BANK_SELECT" and cookie_info.get("has_expired_cbi"):
            health_status = ProfileHealthStatus.SESSION_EXPIRED
            session_status = "EXPIRED"
            reauth_required = True
        elif cookie_info.get("has_expired_cbi"):
            # Expired session cookies on disk
            health_status = ProfileHealthStatus.SESSION_REAUTH_REQUIRED
            session_status = "EXPIRED"
            reauth_required = True
        elif not profile_exists or (file_count == 0 and disk_size == 0):
            health_status = ProfileHealthStatus.HEALTHY
            session_status = "CLEAN_NEW"
        else:
            health_status = ProfileHealthStatus.HEALTHY
            session_status = "READY"

        report = ProfileHealthReport(
            nid=nid,
            health_status=health_status,
            session_status=session_status,
            browser_status=browser_status,
            is_locked=is_locked,
            lock_reason=lock_reason,
            storage_integrity=integrity_msg,
            cookie_count=cookie_info.get("count", 0),
            has_cbi_cookies=cookie_info.get("has_cbi", False),
            has_expired_cbi_cookies=cookie_info.get("has_expired_cbi", False),
            last_successful_login=last_success,
            last_error=last_error,
            captcha_status=captcha_status,
            reauth_required=reauth_required,
            disk_size_bytes=disk_size,
            file_count=file_count,
            details={
                "checkpoint_step": checkpoint_step,
                "cookie_info": cookie_info,
            },
        )

        cls.save_report(report)
        return report

    @classmethod
    def save_report(cls, report: ProfileHealthReport) -> None:
        """Persist profile health observation into SQLite database."""
        try:
            from database import DBHandler
            with DBHandler._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS profile_health (
                        nid TEXT PRIMARY KEY,
                        health_status TEXT,
                        session_status TEXT,
                        browser_status TEXT,
                        last_successful_login REAL,
                        last_error TEXT,
                        captcha_status TEXT,
                        reauth_required INTEGER DEFAULT 0,
                        details_json TEXT,
                        last_checked_at REAL
                    )"""
                )
                cur.execute(
                    """INSERT INTO profile_health
                    (nid, health_status, session_status, browser_status, last_successful_login,
                     last_error, captcha_status, reauth_required, details_json, last_checked_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(nid) DO UPDATE SET
                        health_status=excluded.health_status,
                        session_status=excluded.session_status,
                        browser_status=excluded.browser_status,
                        last_successful_login=excluded.last_successful_login,
                        last_error=excluded.last_error,
                        captcha_status=excluded.captcha_status,
                        reauth_required=excluded.reauth_required,
                        details_json=excluded.details_json,
                        last_checked_at=excluded.last_checked_at
                    """,
                    (
                        report.nid,
                        report.health_status.value,
                        report.session_status,
                        report.browser_status,
                        report.last_successful_login,
                        report.last_error,
                        report.captcha_status,
                        1 if report.reauth_required else 0,
                        json.dumps(report.details, ensure_ascii=False),
                        report.checked_at,
                    ),
                )
                conn.commit()
        except Exception:
            pass

    @classmethod
    def get_report(cls, nid: str) -> Optional[ProfileHealthReport]:
        """Fetch cached profile health report from database."""
        try:
            from database import DBHandler
            with DBHandler._connect(row_factory=True) as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM profile_health WHERE nid = ?", (nid,))
                row = cur.fetchone()
                if not row:
                    return None
                status_str = row["health_status"]
                try:
                    status = ProfileHealthStatus(status_str)
                except Exception:
                    status = ProfileHealthStatus.UNKNOWN
                details = {}
                try:
                    if row["details_json"]:
                        details = json.loads(row["details_json"])
                except Exception:
                    pass
                return ProfileHealthReport(
                    nid=str(row["nid"]),
                    health_status=status,
                    session_status=str(row["session_status"] or "UNKNOWN"),
                    browser_status=str(row["browser_status"] or "CLOSED"),
                    is_locked=False,
                    storage_integrity="ok",
                    last_successful_login=row["last_successful_login"],
                    last_error=row["last_error"],
                    captcha_status=str(row["captcha_status"] or "OK"),
                    reauth_required=bool(row["reauth_required"]),
                    details=details,
                    checked_at=float(row["last_checked_at"] or time.time()),
                )
        except Exception:
            return None

    @classmethod
    def list_all_reports(cls) -> List[ProfileHealthReport]:
        """List health reports for all applicants registered in database."""
        reports = []
        try:
            from database import DBHandler
            applicants = DBHandler.get_all_applicants() or []
            for app in applicants:
                nid = str(app.get("national_id") or app.get("nid") or "")
                if nid:
                    rep = cls.get_report(nid) or cls.inspect_profile(nid)
                    reports.append(rep)
        except Exception:
            pass
        return reports
