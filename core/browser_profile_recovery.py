"""
Browser Profile Recovery Manager for MAHANBOT.
Handles targeted cookie hygiene, profile repair, graceful browser closing,
safe backups, and official re-authentication workflows.
"""

from __future__ import annotations

import os
import shutil
import time
import sqlite3
import json
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from core.browser_profile_health import BrowserProfileHealthManager, ProfileHealthStatus


class BrowserProfileRecoveryManager:
    """Central authority for repairing browser profiles without data loss."""

    DEFAULT_BACKUP_ROOT = Path(__file__).resolve().parent.parent / "backups" / "profiles"

    @classmethod
    def audit_log(
        cls,
        nid: str,
        action: str,
        performed_by: str = "system",
        result: str = "success",
        details: str = "",
    ) -> None:
        """Persist audit record of profile repair operations."""
        try:
            from database import DBHandler
            with DBHandler._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS profile_repair_audit_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        nid TEXT,
                        action TEXT,
                        performed_by TEXT,
                        result TEXT,
                        details TEXT,
                        created_at REAL
                    )"""
                )
                cur.execute(
                    """INSERT INTO profile_repair_audit_log
                    (nid, action, performed_by, result, details, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (nid, action, performed_by, result, details, time.time()),
                )
                conn.commit()
        except Exception:
            pass

    @classmethod
    def backup_profile(
        cls,
        nid: str,
        profile_root: Optional[Path | str] = None,
        backup_root: Optional[Path | str] = None,
    ) -> Tuple[bool, Optional[str], str]:
        """
        Create a timestamped backup of the applicant's profile directory.
        """
        p_dir = BrowserProfileHealthManager.get_applicant_profile_dir(nid, profile_root)
        if not p_dir.exists():
            return False, None, "Profile directory does not exist; no backup needed."

        b_root = Path(backup_root).resolve() if backup_root else cls.DEFAULT_BACKUP_ROOT
        b_root.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        target_dir = b_root / f"{nid}_{timestamp}"

        try:
            # Copy profile directory safely
            shutil.copytree(p_dir, target_dir, dirs_exist_ok=True)
            cls.audit_log(nid, "backup_profile", details=f"Backed up to {target_dir}")
            return True, str(target_dir), f"Backup successfully created at {target_dir.name}"
        except Exception as exc:
            cls.audit_log(nid, "backup_profile", result="failed", details=str(exc))
            return False, None, f"Failed to create backup: {exc}"

    @classmethod
    def purge_stale_cbi_cookies(
        cls,
        profile_dir: Path,
        force_all_cbi: bool = False,
    ) -> Tuple[bool, int, str]:
        """
        Targeted purge of CBI session cookies from Chromium SQLite database.
        - If force_all_cbi is True: purges all ve.cbi.ir cookies (used before Step 1 fresh start).
        - If force_all_cbi is False: purges only expired CBI cookies.
        Does NOT touch any non-CBI cookies or other browser storage.
        """
        cookie_file = profile_dir / "Default" / "Network" / "Cookies"
        if not cookie_file.exists():
            cookie_file = profile_dir / "Default" / "Cookies"
        if not cookie_file.exists():
            return True, 0, "No cookies database present"

        # Check if file is locked
        try:
            with open(cookie_file, "r+b"):
                pass
        except (IOError, PermissionError) as exc:
            return False, 0, f"Cannot purge cookies: database is locked by active browser process ({exc})"

        deleted_count = 0
        try:
            abs_path = os.path.abspath(str(cookie_file))
            conn = sqlite3.connect(abs_path, timeout=5.0)
            cur = conn.cursor()

            # Chrome epoch: microseconds since 1601-01-01
            now_chrome = int((time.time() + 11644473600) * 1000000)

            if force_all_cbi:
                cur.execute(
                    "DELETE FROM cookies WHERE host_key LIKE '%cbi.ir%' OR name LIKE 'cbi%'"
                )
            else:
                cur.execute(
                    """DELETE FROM cookies WHERE
                    (host_key LIKE '%cbi.ir%' OR name LIKE 'cbi%') AND
                    (has_expires = 1 AND expires_utc < ?)""",
                    (now_chrome,),
                )

            deleted_count = cur.rowcount
            conn.commit()

            # Vacuum / checkpoint if needed
            cur.execute("PRAGMA integrity_check")
            integrity = cur.fetchone()[0]
            conn.close()

            return True, deleted_count, f"Purged {deleted_count} stale CBI cookies (integrity: {integrity})"
        except Exception as exc:
            return False, deleted_count, f"Cookie purge failed: {exc}"

    @classmethod
    def prepare_session_for_launch(
        cls,
        nid: str,
        stage: str = "STEP_1_ENTRY_FORM",
        profile_root: Optional[Path | str] = None,
    ) -> Tuple[bool, str]:
        """
        Pre-launch hygiene:
        - If starting Step 1 (Entry Form / Initial NID and CAPTCHA):
          Purge any dead/poisoned CBI cookies so BotDetect CAPTCHA generates a fresh session.
        - If resuming Step 3 (Active Bank Selection):
          DO NOT TOUCH COOKIES! Preserve active session.
        """
        p_dir = BrowserProfileHealthManager.get_applicant_profile_dir(nid, profile_root)
        if not p_dir.exists():
            return True, "Fresh profile, no cleanup needed."

        # Orphan locks after manual close / crashed Chromium block the next launch
        BrowserProfileHealthManager.clear_stale_locks(p_dir)
        is_locked, lock_reason = BrowserProfileHealthManager.check_lock_status(p_dir)
        if is_locked:
            return False, f"Profile is locked: {lock_reason}"

        if stage in ("STEP_3_BANK_SELECT", "STEP_2_OTP"):
            # Resuming active workflow: preserve session cookies!
            return True, f"Preserving active session cookies for {stage}"

        # Otherwise, Step 1 fresh entry requires purging stale CBI cookies to avoid BotDetect session desync
        ok, count, msg = cls.purge_stale_cbi_cookies(p_dir, force_all_cbi=True)
        if ok and count > 0:
            cls.audit_log(nid, "prepare_session_for_launch", details=f"Step 1 hygiene: {msg}")
        return ok, msg

    @classmethod
    def repair_corrupted_storage(
        cls,
        nid: str,
        profile_root: Optional[Path | str] = None,
    ) -> Tuple[bool, str]:
        """
        Repair a corrupted SQLite cookie database by backing up and safely recreating it.
        """
        p_dir = BrowserProfileHealthManager.get_applicant_profile_dir(nid, profile_root)
        if not p_dir.exists():
            return False, "Profile does not exist"

        is_locked, lock_reason = BrowserProfileHealthManager.check_lock_status(p_dir)
        if is_locked:
            return False, f"Cannot repair while profile is locked ({lock_reason})"

        cookie_file = p_dir / "Default" / "Network" / "Cookies"
        if not cookie_file.exists():
            cookie_file = p_dir / "Default" / "Cookies"

        if not cookie_file.exists():
            return True, "No cookie file found to repair"

        # Check integrity
        integrity_ok, integrity_msg = BrowserProfileHealthManager.check_storage_integrity(p_dir)
        if integrity_ok:
            return True, "Storage integrity is already OK"

        # Backup first
        cls.backup_profile(nid, profile_root)

        try:
            # Remove corrupted cookies DB and journal files
            for ext in ("", "-journal", "-wal", "-shm"):
                target = Path(str(cookie_file) + ext)
                if target.exists():
                    target.unlink()
            cls.audit_log(nid, "repair_corrupted_storage", details=f"Removed corrupted cookie files: {integrity_msg}")
            return True, "Corrupted storage safely repaired (backed up and reset)"
        except Exception as exc:
            cls.audit_log(nid, "repair_corrupted_storage", result="failed", details=str(exc))
            return False, f"Repair failed: {exc}"

    @classmethod
    def create_fresh_profile(
        cls,
        nid: str,
        profile_root: Optional[Path | str] = None,
        backup_first: bool = True,
    ) -> Tuple[bool, str]:
        """
        Admin-confirmed reset of an applicant's browser profile.
        Backs up the old profile and recreates a clean directory.
        """
        p_dir = BrowserProfileHealthManager.get_applicant_profile_dir(nid, profile_root)

        is_locked, lock_reason = BrowserProfileHealthManager.check_lock_status(p_dir)
        if is_locked:
            return False, f"Cannot reset profile while browser is active ({lock_reason})"

        if backup_first and p_dir.exists():
            cls.backup_profile(nid, profile_root)

        try:
            if p_dir.exists():
                shutil.rmtree(p_dir, ignore_errors=False)
            p_dir.mkdir(parents=True, exist_ok=True)
            cls.audit_log(nid, "create_fresh_profile", details="Profile recreated from scratch")
            return True, "Fresh profile created successfully"
        except Exception as exc:
            cls.audit_log(nid, "create_fresh_profile", result="failed", details=str(exc))
            return False, f"Failed to recreate profile: {exc}"

    @classmethod
    def initiate_reauth_workflow(cls, nid: str) -> Tuple[bool, str]:
        """
        Trigger official re-authentication workflow for an applicant whose session expired.
        Resets checkpoint to STEP_1_ENTRY_FORM, clears stale OTP locks, and logs event.
        """
        try:
            from database import DBHandler
            # Reset checkpoint
            DBHandler.save_checkpoint(
                nid,
                {
                    "last_verified_step": "STEP_1_ENTRY_FORM",
                    "expected_next_step": "STEP_2_OTP",
                    "session_active": False,
                    "last_error_reason": "session_reauth_initiated",
                },
            )
            DBHandler.update_runtime(
                nid,
                current_step="LOAD_FORM",
                status="READY",
                last_error="reauth_required",
            )
            DBHandler.update_status(nid, "آماده احراز هویت مجدد", "درخواست ورود مجدد به سامانه ثبت شد.")
            cls.audit_log(nid, "initiate_reauth_workflow", details="Re-auth initiated")
            return True, "Re-authentication workflow initiated successfully"
        except Exception as exc:
            return False, f"Failed to initiate re-authentication: {exc}"
