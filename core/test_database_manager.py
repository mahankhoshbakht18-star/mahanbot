"""
core/test_database_manager.py — Independent Test Database Subsystem for MAHANBOT.

Manages `cbi_test.db` completely isolated from production `cbi_ultimate.db`.
Uses the exact schema required by MAHANBOT.
Seeds 5-6 sample test applicants without using real sensitive personal data.
"""

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mahanbot.test_db")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB_PATH = os.path.join(BASE_DIR, "cbi_test.db")

SAMPLE_APPLICANTS = [
    {
        "national_id": "0010000001",
        "full_name": "سعید نمونه (آزمایشی)",
        "state": "تهران",
        "city": "تهران",
        "priority_banks": ["بانک صادرات", "بانک تجارت"],
        "bank_mode": "online",
        "allow_reserve": True,
        "auto_final_submit": True,
        "preferred_branch": "",
        "status": "در انتظار",
    },
    {
        "national_id": "0010000002",
        "full_name": "مریم آزمایشی (آزمایشی)",
        "state": "خراسان رضوی",
        "city": "مشهد",
        "priority_banks": ["بانک ملت", "بانک ملی"],
        "bank_mode": "in_person",
        "allow_reserve": False,
        "auto_final_submit": False,
        "preferred_branch": "شعبه مرکزی",
        "status": "در انتظار",
    },
    {
        "national_id": "0010000003",
        "full_name": "علی تستی (آزمایشی)",
        "state": "اصفهان",
        "city": "اصفهان",
        "priority_banks": ["بانک مسکن", "بانک سپه"],
        "bank_mode": "online",
        "allow_reserve": True,
        "auto_final_submit": True,
        "preferred_branch": "",
        "status": "در انتظار",
    },
    {
        "national_id": "0010000004",
        "full_name": "زهرا تست (آزمایشی)",
        "state": "آذربایجان شرقی",
        "city": "تبریز",
        "priority_banks": ["بانک مهر ایران", "بانک رسالت"],
        "bank_mode": "online",
        "allow_reserve": False,
        "auto_final_submit": False,
        "preferred_branch": "",
        "status": "در انتظار",
    },
    {
        "national_id": "0010000005",
        "full_name": "رضا نمونه (آزمایشی)",
        "state": "فارس",
        "city": "شیراز",
        "priority_banks": ["بانک کشاورزی", "بانک صادرات"],
        "bank_mode": "in_person",
        "allow_reserve": True,
        "auto_final_submit": True,
        "preferred_branch": "شعبه زند",
        "status": "در انتظار",
    },
    {
        "national_id": "0010000006",
        "full_name": "سارا آزمایشی (آزمایشی)",
        "state": "البرز",
        "city": "کرج",
        "priority_banks": ["بانک رفاه کارگران", "بانک سامان"],
        "bank_mode": "online",
        "allow_reserve": True,
        "auto_final_submit": False,
        "preferred_branch": "",
        "status": "در انتظار",
    },
]


class TestDatabaseManager:
    """Manages the isolated test database."""

    def __init__(self, db_path: str = TEST_DB_PATH):
        self.db_path = db_path

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_database(self, force_reseed: bool = False):
        """Create tables if missing and seed sample applicants."""
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")

            # 1. Applicants table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS applicants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT NOT NULL,
                    national_id TEXT UNIQUE NOT NULL,
                    status TEXT DEFAULT 'در انتظار',
                    last_log TEXT DEFAULT '',
                    data TEXT DEFAULT '{}',
                    otp_code TEXT DEFAULT '',
                    otp_created_at REAL DEFAULT 0
                )
                """
            )

            # 2. Settings table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

            # 3. Automation runtime table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_runtime (
                    nid TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

            # 4. Checkpoints table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    nid TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    data TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

            # Seed default settings
            default_settings = {
                "bank_refresh_interval": "5.0",
                "max_refresh_attempts": "30",
                "auto_final_submit": "false",
                "otp_server_url": "http://127.0.0.1:8010",
                "otp_validity_seconds": "120.0",
                "captcha_mode": "model",
            }
            for k, v in default_settings.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
                )

            # Check if applicants exist
            count = conn.execute("SELECT COUNT(*) FROM applicants").fetchone()[0]
            if count == 0 or force_reseed:
                if force_reseed:
                    conn.execute("DELETE FROM applicants")
                for item in SAMPLE_APPLICANTS:
                    data_payload = {
                        "state": item["state"],
                        "city": item["city"],
                        "priority_banks": item["priority_banks"],
                        "bank_mode": item["bank_mode"],
                        "allow_reserve": item["allow_reserve"],
                        "auto_final_submit": item["auto_final_submit"],
                        "preferred_branch": item["preferred_branch"],
                        "tracking_code": None,
                        "last_result": None,
                    }
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO applicants (full_name, national_id, status, data)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            item["full_name"],
                            item["national_id"],
                            item["status"],
                            json.dumps(data_payload, ensure_ascii=False),
                        ),
                    )
                logger.info(f"Seeded {len(SAMPLE_APPLICANTS)} sample applicants in {self.db_path}")

    def get_all_applicants(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, full_name, national_id, status, last_log, data, otp_code, otp_created_at FROM applicants ORDER BY id ASC"
            ).fetchall()
            results = []
            for r in rows:
                parsed_data = {}
                try:
                    parsed_data = json.loads(r["data"] or "{}")
                except Exception:
                    pass
                results.append({
                    "id": r["id"],
                    "full_name": r["full_name"],
                    "national_id": r["national_id"],
                    "status": r["status"],
                    "last_log": r["last_log"],
                    "otp_code": r["otp_code"],
                    "state": parsed_data.get("state", ""),
                    "city": parsed_data.get("city", ""),
                    "priority_banks": parsed_data.get("priority_banks", []),
                    "bank_mode": parsed_data.get("bank_mode", "online"),
                    "allow_reserve": parsed_data.get("allow_reserve", True),
                    "auto_final_submit": parsed_data.get("auto_final_submit", False),
                    "preferred_branch": parsed_data.get("preferred_branch", ""),
                    "tracking_code": parsed_data.get("tracking_code"),
                    "last_result": parsed_data.get("last_result"),
                })
            return results

    get_applicants = get_all_applicants


    def get_applicant(self, national_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, full_name, national_id, status, last_log, data, otp_code, otp_created_at FROM applicants WHERE national_id = ?",
                (national_id,),
            ).fetchone()
            if not row:
                return None
            parsed_data = {}
            try:
                parsed_data = json.loads(row["data"] or "{}")
            except Exception:
                pass
            return {
                "id": row["id"],
                "full_name": row["full_name"],
                "national_id": row["national_id"],
                "status": row["status"],
                "last_log": row["last_log"],
                "otp_code": row["otp_code"],
                "state": parsed_data.get("state", ""),
                "city": parsed_data.get("city", ""),
                "priority_banks": parsed_data.get("priority_banks", []),
                "bank_mode": parsed_data.get("bank_mode", "online"),
                "allow_reserve": parsed_data.get("allow_reserve", True),
                "auto_final_submit": parsed_data.get("auto_final_submit", False),
                "preferred_branch": parsed_data.get("preferred_branch", ""),
                "tracking_code": parsed_data.get("tracking_code"),
                "last_result": parsed_data.get("last_result"),
            }

    def update_status(self, national_id: str, status: str, log_message: Optional[str] = None):
        with self.connect() as conn:
            if log_message:
                conn.execute(
                    "UPDATE applicants SET status = ?, last_log = ? WHERE national_id = ?",
                    (status, log_message, national_id),
                )
            else:
                conn.execute(
                    "UPDATE applicants SET status = ? WHERE national_id = ?",
                    (status, national_id),
                )

    def update_applicant_data(self, national_id: str, updates: Dict[str, Any]):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT data FROM applicants WHERE national_id = ?", (national_id,)
            ).fetchone()
            if not row:
                return
            try:
                current_data = json.loads(row["data"] or "{}")
            except Exception:
                current_data = {}
            current_data.update(updates)
            conn.execute(
                "UPDATE applicants SET data = ? WHERE national_id = ?",
                (json.dumps(current_data, ensure_ascii=False), national_id),
            )

    def set_final_submit_toggle(self, national_id: str, enabled: bool):
        self.update_applicant_data(national_id, {"auto_final_submit": bool(enabled)})

    def save_tracking_code(self, national_id: str, tracking_code: str):
        self.update_applicant_data(
            national_id, {"tracking_code": tracking_code, "last_result": "DEFINITIVE_CONFIRMED"}
        )
        self.update_status(national_id, "ثبت قطعی", f"کد رهگیری: {tracking_code}")

    def save_reserve_confirmation(self, national_id: str):
        self.update_applicant_data(
            national_id, {"tracking_code": "RESERVED", "last_result": "RESERVE_CONFIRMED"}
        )
        self.update_status(national_id, "رزرو شده", "رزرو با موفقیت تأیید شد.")

    def get_otp(self, national_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT otp_code, otp_created_at FROM applicants WHERE national_id = ?",
                (national_id,),
            ).fetchone()
            if row and row["otp_code"]:
                return {"code": row["otp_code"], "created_at": row["otp_created_at"]}
            return None

    def set_otp(self, national_id: str, code: str):
        with self.connect() as conn:
            conn.execute(
                "UPDATE applicants SET otp_code = ?, otp_created_at = ? WHERE national_id = ?",
                (code, time.time(), national_id),
            )

    def invalidate_otp(self, national_id: str, status: str = "expired"):
        with self.connect() as conn:
            conn.execute(
                "UPDATE applicants SET otp_code = '', otp_created_at = 0 WHERE national_id = ?",
                (national_id,),
            )

    def record_checkpoint(self, national_id: str, stage: str, details: str = ""):
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO checkpoints (nid, stage, data, updated_at) VALUES (?, ?, ?, ?)",
                (national_id, stage, json.dumps({"details": details}, ensure_ascii=False), time.time()),
            )


    def reset_applicant_statuses(self):

        with self.connect() as conn:
            rows = conn.execute("SELECT national_id, data FROM applicants").fetchall()
            for r in rows:
                nid = r["national_id"]
                try:
                    data = json.loads(r["data"] or "{}")
                except Exception:
                    data = {}
                data["tracking_code"] = None
                data["last_result"] = None
                conn.execute(
                    "UPDATE applicants SET status = 'در انتظار', last_log = '', data = ? WHERE national_id = ?",
                    (json.dumps(data, ensure_ascii=False), nid),
                )
