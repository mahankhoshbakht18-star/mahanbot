# tests/test_retry_and_stale_tracking.py
import json
import unittest
from unittest.mock import MagicMock, patch

from database import DBHandler
from core.job_queue import JobQueue
from browser_launcher import safe_goto


class RetryAndStaleTrackingTests(unittest.TestCase):
    def setUp(self):
        self.nid = "1122334455"
        with DBHandler._connect() as conn:
            conn.execute("DELETE FROM applicants WHERE national_id = ?", (self.nid,))
            conn.execute("DELETE FROM checkpoints WHERE nid = ?", (self.nid,))
            conn.execute("DELETE FROM automation_runtime WHERE nid = ?", (self.nid,))
            conn.commit()

    def tearDown(self):
        with DBHandler._connect() as conn:
            conn.execute("DELETE FROM applicants WHERE national_id = ?", (self.nid,))
            conn.execute("DELETE FROM checkpoints WHERE nid = ?", (self.nid,))
            conn.execute("DELETE FROM automation_runtime WHERE nid = ?", (self.nid,))
            conn.commit()

    def test_01_stale_tracking_code_does_not_falsely_claim_final_submission(self):
        """If applicant only has an old tracking code but status is 'Ready' and not final_submitted, has_final_submission is None."""
        data = json.dumps({"tracking_code": "OLD_123456", "final_submitted": False})
        with DBHandler._connect() as conn:
            conn.execute(
                "INSERT INTO applicants(full_name, national_id, status, data) VALUES(?, ?, 'Ready', ?)",
                ("تست رهگیری قدیمی", self.nid, data)
            )
            conn.commit()

        # Must return None so bot doesn't falsely claim final submit or block re-run
        self.assertIsNone(DBHandler.has_final_submission(self.nid))

    def test_02_confirmed_final_submission_returns_tracking_code(self):
        """If applicant status is Success and final_submitted is True, has_final_submission returns code."""
        data = json.dumps({"tracking_code": "NEW_987654", "final_submitted": True})
        with DBHandler._connect() as conn:
            conn.execute(
                "INSERT INTO applicants(full_name, national_id, status, data) VALUES(?, ?, 'Success', ?)",
                ("ثبت موفق واقعی", self.nid, data)
            )
            conn.commit()

        self.assertEqual(DBHandler.has_final_submission(self.nid), "NEW_987654")

    def test_03_clear_request_data_purges_all_traces(self):
        """DBHandler.clear_request_data clears tracking_code, checkpoints, and automation_runtime."""
        data = json.dumps({"tracking_code": "STALE_555", "final_submitted": True})
        with DBHandler._connect() as conn:
            conn.execute(
                "INSERT INTO applicants(full_name, national_id, status, data) VALUES(?, ?, 'Success', ?)",
                ("حذف شده", self.nid, data)
            )
            conn.execute(
                "INSERT INTO checkpoints(nid, payload, updated_at) VALUES(?, ?, 1000)",
                (self.nid, json.dumps({"final_submit_status": "confirmed"}))
            )
            conn.execute(
                "INSERT INTO automation_runtime(nid, payload, updated_at) VALUES(?, ?, 1000)",
                (self.nid, json.dumps({"status": "SUCCESS", "current_step": "DONE"}))
            )
            conn.commit()

        cleared = DBHandler.clear_request_data(self.nid, "تست پاکسازی")
        self.assertTrue(cleared)

        app = dict(DBHandler.get_applicant(self.nid))
        app_d = json.loads(app.get("data") or "{}")
        self.assertEqual(app_d.get("tracking_code"), "")
        self.assertEqual(app.get("status"), "Ready")
        self.assertIsNone(DBHandler.has_final_submission(self.nid))
        self.assertEqual(DBHandler.get_checkpoint(self.nid), {})
        self.assertEqual(DBHandler.get_runtime(self.nid), {})

    def test_04_safe_goto_recovers_when_cbi_elements_present_despite_timeout(self):
        """safe_goto does not raise when timeout happens but CBI elements are already in the DOM."""
        class DummyPage:
            def __init__(self):
                self.url = "https://ve.cbi.ir/TasTrace.aspx"
            def goto(self, url, **kwargs):
                raise Exception("Timeout 30000ms exceeded")
            def wait_for_load_state(self, state, timeout=None):
                pass
            def locator(self, sel):
                mock_loc = MagicMock()
                if "tbTraceCD" in sel:
                    mock_loc.count.return_value = 1
                else:
                    mock_loc.count.return_value = 0
                return mock_loc

        dummy_page = DummyPage()
        # safe_goto should recover gracefully and return None instead of raising
        result = safe_goto(dummy_page, "https://ve.cbi.ir/TasTrace.aspx")
        self.assertIsNone(result)

    def test_05_job_queue_does_not_falsely_complete_on_stale_tracking_code(self):
        """JobQueue does not mark job as completed unless current execution actually achieved SUCCESS with DONE."""
        jq = JobQueue(max_concurrency=2)
        # Mock DBHandler.get_runtime returning STOPPED
        with patch("core.job_queue.DBHandler.get_runtime") as mock_runtime, \
             patch("core.job_queue.DBHandler.has_final_submission") as mock_has_final:
            mock_runtime.return_value = {"status": "STOPPED", "current_step": "STOPPED"}
            # Even if stale tracking code exists in DB:
            mock_has_final.return_value = "STALE_9999"

            job = jq.enqueue("select", self.nid, {})
            current_runtime = mock_runtime.return_value
            is_success = (
                current_runtime.get("status") == "SUCCESS"
                and current_runtime.get("current_step") in {"DONE", "REGISTRATION_CONFIRMED", "SUCCESS"}
            )
            self.assertFalse(is_success)
        jq.stop_all()


if __name__ == "__main__":
    unittest.main()
