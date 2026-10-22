"""
Unit and integration tests for:
1. RecoveryBot (ve.cbi.ir/GetTraceCD.aspx)
2. DeleteRequestBot (ve.cbi.ir/TasReqDelete.aspx)
3. DBHandler.clear_request_data
4. JobQueue dispatching of recovery and delete_req
5. API endpoints for recovery and delete
"""
import os
import unittest
from unittest.mock import MagicMock, patch

from database import DBHandler
from bot_recovery import RecoveryBot, TARGET_URL as RECOVERY_URL
from bot_delete import DeleteRequestBot, TARGET_URL as DELETE_URL
from core.job_queue import JobQueue
from fastapi.testclient import TestClient
import server


class RecoveryAndDeleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(server.app)

    def setUp(self):
        from core import state as _state
        self.prev_jq = _state.JOB_QUEUE
        _state.JOB_QUEUE = JobQueue(max_concurrency=4)
        self.test_nid = "0099887766"
        # Seed test applicant
        import json
        data_json = json.dumps({
            "first_name": "علی",
            "last_name": "رضایی",
            "birth_date": "1370/05/10",
            "marriage_date": "1401/02/15",
            "tracking_code": "9876543210"
        }, ensure_ascii=False)
        with DBHandler._connect() as conn:
            conn.execute("DELETE FROM applicants WHERE national_id = ?", (self.test_nid,))
            conn.execute("INSERT INTO applicants(full_name, national_id, status, data) VALUES(?, ?, 'Ready', ?)",
                         ("متقاضی آزمایشی بازیابی و حذف", self.test_nid, data_json))
            conn.commit()

    def tearDown(self):
        from core import state as _state
        if _state.JOB_QUEUE:
            try:
                _state.JOB_QUEUE.stop_all()
            except Exception:
                pass
        _state.JOB_QUEUE = self.prev_jq
        try:
            with DBHandler._connect() as conn:
                conn.execute("DELETE FROM applicants WHERE national_id = ?", (self.test_nid,))
                conn.execute("DELETE FROM checkpoints WHERE nid = ?", (self.test_nid,))
                conn.commit()
        except Exception:
            pass

    def test_01_urls_and_targets_are_official_cbi(self):
        """Verify the exact target URLs match official CBI endpoints."""
        self.assertEqual(RECOVERY_URL, "https://ve.cbi.ir/GetTraceCD.aspx")
        self.assertEqual(DELETE_URL, "https://ve.cbi.ir/TasReqDelete.aspx")

    def test_02_recovery_bot_dom_selectors(self):
        """Verify RecoveryBot has real DOM controls for GetTraceCD.aspx."""
        bot = RecoveryBot(self.test_nid, {})
        # Check required fields
        self.assertTrue(hasattr(bot, "_fill_recovery_form"))
        self.assertTrue(hasattr(bot, "_second_stage_visible"))
        self.assertTrue(hasattr(bot, "_extract_tracking_code"))

    def test_03_delete_bot_refuses_without_tracking_code(self):
        """Verify DeleteRequestBot rejects operation if tracking code is missing."""
        no_track_nid = "0011223344"
        import json
        with DBHandler._connect() as conn:
            conn.execute("DELETE FROM applicants WHERE national_id = ?", (no_track_nid,))
            conn.execute("INSERT INTO applicants(full_name, national_id, status, data) VALUES(?, ?, 'Ready', ?)",
                         ("متقاضی بدون کد", no_track_nid, json.dumps({"first_name": "سارا", "last_name": "محمدی"}, ensure_ascii=False)))
            conn.commit()
        try:
            bot = DeleteRequestBot(no_track_nid, {})
            stop_event = MagicMock()
            stop_event.is_set.return_value = False
            # Running without tracking code must return False and log error
            res = bot.run(stop_event)
            self.assertFalse(res)
            # Verify status was updated to indicate missing tracking code
            app = DBHandler.get_applicant(no_track_nid)
            self.assertIn("کد رهگیری", str(app["status"] or "") + str(app["last_log"] or ""))
        finally:
            with DBHandler._connect() as conn:
                conn.execute("DELETE FROM applicants WHERE national_id = ?", (no_track_nid,))
                conn.commit()

    def test_04_db_clear_request_data(self):
        """Verify DBHandler.clear_request_data clears tracking_code and resets status to Ready."""
        # Ensure applicant initially has tracking code and is Success
        DBHandler.save_success_data(self.test_nid, "1234567890")
        app_before = DBHandler.get_applicant(self.test_nid)
        import json
        d_before = json.loads(app_before["data"]) if isinstance(app_before["data"], str) else app_before["data"]
        self.assertEqual(d_before.get("tracking_code"), "1234567890")

        # Clear request
        cleared = DBHandler.clear_request_data(self.test_nid, "ثبت نام با موفقیت حذف شد")
        self.assertTrue(cleared)

        app_after = DBHandler.get_applicant(self.test_nid)
        d_after = json.loads(app_after["data"]) if isinstance(app_after["data"], str) else app_after["data"]
        self.assertEqual(d_after.get("tracking_code"), "")
        self.assertFalse(d_after.get("final_submitted", False))
        self.assertEqual(app_after["status"], "Ready")
        # Checkpoint must be wiped
        self.assertFalse(DBHandler.get_checkpoint(self.test_nid))

    def test_05_job_queue_dispatches_recovery_and_delete_req(self):
        """Verify JobQueue accepts and enqueues recovery and delete_req bots."""
        jq = JobQueue(max_concurrency=2)
        job_rec = jq.enqueue("recovery", self.test_nid, {})
        self.assertIsNotNone(job_rec)
        self.assertEqual(job_rec.bot_name, "recovery")
        jq.cancel(job_rec.id)

        job_del = jq.enqueue("delete_req", self.test_nid, {})
        self.assertIsNotNone(job_del)
        self.assertEqual(job_del.bot_name, "delete_req")
        jq.cancel(job_del.id)

    def test_06_delete_request_api_endpoint(self):
        """Verify POST /bot/action/delete-request/{nid} queues deletion job."""
        resp = self.client.post(f"/bot/action/delete-request/{self.test_nid}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn(data.get("status"), ("queued", "already_running"))

    def test_07_recovery_start_api_endpoint(self):
        """Verify POST /jobs/start with bot_name='recovery' queues recovery job."""
        resp = self.client.post("/jobs/start", json={"bot_name": "recovery", "nid": self.test_nid})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn(data.get("status"), ("queued", "already_running"))


if __name__ == "__main__":
    unittest.main()
