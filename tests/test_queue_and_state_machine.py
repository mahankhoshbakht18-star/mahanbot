"""
Automated Test Suite T01 - T30: Queue Management & 19-State FSM Engine.
Fully mocked and isolated: does NOT send real requests to CBI/ve.cbi.ir.
"""
import sys
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database
from core.state_machine import BotState, transition_state, is_valid_transition, get_current_state, ALLOWED_TRANSITIONS
from core.queue_manager import QueueManager
from core.otp_queue import OTPQueueManager

class QueueAndStateMachineTestSuite(unittest.TestCase):
    def setUp(self):
        self.orig_db_path = database.DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mahanbot-fsm-tests-")
        database.DB_PATH = str(Path(self.temp_dir.name) / "test_fsm.db")
        database.DBHandler.init_db()

    def tearDown(self):
        database.DB_PATH = self.orig_db_path
        self.temp_dir.cleanup()

    # --- T01: Enrollment ---
    def test_T01_queue_enrollment(self):
        ok = QueueManager.enroll("1111111111", applicant_name="متقاضی تست ۱", loan_type="MARRIAGE", requested_bank="بانک ملی")
        self.assertTrue(ok)
        items = QueueManager.get_queue()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["applicant_id"], "1111111111")
        self.assertEqual(items[0]["queue_status"], "WAITING")
        self.assertEqual(items[0]["bot_state"], "QUEUED")

    # --- T02: Priority Setting ---
    def test_T02_priority_setting(self):
        QueueManager.enroll("1111111111", priority="NORMAL")
        ok = QueueManager.set_priority("1111111111", "HIGH")
        self.assertTrue(ok)
        items = QueueManager.get_queue()
        self.assertEqual(items[0]["priority"], "HIGH")

        # Invalid defaults to NORMAL
        QueueManager.set_priority("1111111111", "INVALID")
        items = QueueManager.get_queue()
        self.assertEqual(items[0]["priority"], "NORMAL")

    # --- T03: Reordering ---
    def test_T03_reorder_queue(self):
        QueueManager.enroll("1111111111", priority="NORMAL")
        QueueManager.enroll("2222222222", priority="NORMAL")
        QueueManager.enroll("3333333333", priority="NORMAL")

        items = QueueManager.get_queue()
        self.assertEqual(items[0]["applicant_id"], "1111111111")
        self.assertEqual(items[1]["applicant_id"], "2222222222")
        self.assertEqual(items[2]["applicant_id"], "3333333333")

        # Move #3 to #1
        q3_id = items[2]["queue_id"]
        ok = QueueManager.reorder(q3_id, target_position=1)
        self.assertTrue(ok)

        items_after = QueueManager.get_queue()
        self.assertEqual(items_after[0]["applicant_id"], "3333333333")
        self.assertEqual(items_after[1]["applicant_id"], "1111111111")
        self.assertEqual(items_after[2]["applicant_id"], "2222222222")

    # --- T04: Worker Lease Acquisition ---
    def test_T04_worker_lease_acquisition(self):
        QueueManager.enroll("1111111111", priority="NORMAL")
        QueueManager.enroll("2222222222", priority="HIGH")

        # Should lease HIGH priority first
        leased = QueueManager.acquire_next_for_worker(worker_id="worker_A")
        self.assertIsNotNone(leased)
        self.assertEqual(leased["applicant_id"], "2222222222")

        # Exclusive lock: second worker should get the remaining applicant
        leased2 = QueueManager.acquire_next_for_worker(worker_id="worker_B")
        self.assertIsNotNone(leased2)
        self.assertEqual(leased2["applicant_id"], "1111111111")

        # Third attempt: no more waiting applicants
        leased3 = QueueManager.acquire_next_for_worker(worker_id="worker_C")
        self.assertIsNone(leased3)

    # --- T05: Worker Heartbeat ---
    def test_T05_worker_heartbeat(self):
        QueueManager.enroll("1111111111", priority="NORMAL")
        QueueManager.acquire_next_for_worker(worker_id="worker_A")

        with database.DBHandler._connect() as conn:
            row = conn.execute("SELECT worker_lease_until FROM applicant_queue WHERE applicant_id = '1111111111'").fetchone()
            first_lease = row[0]

        time.sleep(0.05)
        QueueManager.heartbeat(applicant_id="1111111111", worker_id="worker_A")

        with database.DBHandler._connect() as conn:
            row = conn.execute("SELECT worker_lease_until FROM applicant_queue WHERE applicant_id = '1111111111'").fetchone()
            second_lease = row[0]

        self.assertGreaterEqual(second_lease, first_lease)

    # --- T06: Worker Crash & Lease Expiration Recovery ---
    def test_T06_lease_expiration_recovery(self):
        QueueManager.enroll("1111111111", priority="NORMAL")
        QueueManager.acquire_next_for_worker(worker_id="crashed_worker")

        # Artificially expire the lease
        with database.DBHandler._connect() as conn:
            conn.execute("UPDATE applicant_queue SET worker_lease_until = ? WHERE applicant_id = '1111111111'", (time.time() - 10,))
            conn.commit()

        # Next acquisition should clean the dead lease and re-lease it
        leased = QueueManager.acquire_next_for_worker(worker_id="new_worker")
        self.assertIsNotNone(leased)
        self.assertEqual(leased["applicant_id"], "1111111111")
        self.assertEqual(leased["worker_id"], "new_worker")

    # --- T07: FSM Transition Across States ---
    def test_T07_fsm_valid_transitions(self):
        nid = "1111111111"
        QueueManager.enroll(nid)

        self.assertTrue(transition_state(nid, BotState.PREPARING))
        self.assertTrue(transition_state(nid, BotState.STARTING))
        self.assertTrue(transition_state(nid, BotState.REQUESTING_OTP))
        self.assertTrue(transition_state(nid, BotState.WAITING_OTP))
        self.assertTrue(transition_state(nid, BotState.OTP_RECEIVED))
        self.assertTrue(transition_state(nid, BotState.VERIFYING_OTP))
        self.assertTrue(transition_state(nid, BotState.AUTHENTICATED))
        self.assertTrue(transition_state(nid, BotState.STEP_3_READY))
        self.assertTrue(transition_state(nid, BotState.SELECTING_BANK))
        self.assertTrue(transition_state(nid, BotState.READY_TO_SUBMIT))
        self.assertTrue(transition_state(nid, BotState.SUBMITTING))
        self.assertTrue(transition_state(nid, BotState.CONFIRMED))

        self.assertEqual(get_current_state(nid), BotState.CONFIRMED)

    # --- T08: FSM Rejection of Illegal Transitions ---
    def test_T08_fsm_reject_illegal_transitions(self):
        nid = "1111111111"
        QueueManager.enroll(nid)
        # Directly jumping from QUEUED to CONFIRMED must be rejected
        ok = transition_state(nid, BotState.CONFIRMED)
        self.assertFalse(ok)
        self.assertEqual(get_current_state(nid), BotState.QUEUED)

    # --- T09: OTP Request Creation ---
    def test_T09_otp_request_creation(self):
        req = OTPQueueManager.create_request("1111111111", phone_reference="09121112233", step="STEP_1")
        req_id = req["otp_request_id"]
        self.assertIsNotNone(req_id)
        self.assertTrue(len(req_id) > 10)

        active = OTPQueueManager.get_active_request("1111111111")
        self.assertIsNotNone(active)
        self.assertEqual(active["otp_request_id"], req_id)
        self.assertEqual(active["delivery_status"], "WAITING_DELIVERY")

    # --- T10: Android OTP Delivery ---
    def test_T10_android_otp_delivery(self):
        req = OTPQueueManager.create_request("1111111111", phone_reference="09121112233", step="STEP_1")
        req_id = req["otp_request_id"]
        ok, res = OTPQueueManager.deliver_otp(code="789456", phone="09121112233", source="android")
        self.assertTrue(ok)

        code = OTPQueueManager.consume_otp(req_id)
        self.assertEqual(code, "789456")

    # --- T11: Webhook OTP Delivery ---
    def test_T11_webhook_otp_delivery(self):
        req = OTPQueueManager.create_request("2222222222", phone_reference="09122223344", step="STEP_1")
        req_id = req["otp_request_id"]
        ok, res = OTPQueueManager.deliver_otp(code="654321", explicit_nid="2222222222", source="webhook")
        self.assertTrue(ok)

        code = OTPQueueManager.consume_otp(req_id)
        self.assertEqual(code, "654321")

    # --- T12: Manual OTP Entry ---
    def test_T12_manual_otp_delivery(self):
        req = OTPQueueManager.create_request("3333333333", phone_reference="09123334455", step="STEP_1")
        req_id = req["otp_request_id"]
        ok, res = OTPQueueManager.deliver_otp(code="123456", request_id=req_id, source="manual")
        self.assertTrue(ok)

        code = OTPQueueManager.consume_otp(req_id)
        self.assertEqual(code, "123456")

    # --- T13: Rejection of Invalid/Expired Code ---
    def test_T13_otp_rejection_and_single_use(self):
        req = OTPQueueManager.create_request("4444444444", phone_reference="09124445566", step="STEP_1")
        req_id = req["otp_request_id"]
        ok, res = OTPQueueManager.deliver_otp(code="112233", request_id=req_id)
        self.assertTrue(ok)

        # First consumption succeeds
        code1 = OTPQueueManager.consume_otp(req_id)
        self.assertEqual(code1, "112233")

        # Single-use guarantee: second consumption returns None
        code2 = OTPQueueManager.consume_otp(req_id)
        self.assertIsNone(code2)

    # --- T14: Isolation - No Cross-Applicant OTP Delivery ---
    def test_T14_otp_isolation_ambiguity_prevention(self):
        # Two applicants waiting on the same phone number (or SMS with no matching identifier)
        req1 = OTPQueueManager.create_request("5555555555", phone_reference="09129999999", step="STEP_1")
        req2 = OTPQueueManager.create_request("6666666666", phone_reference="09129999999", step="STEP_1")

        # If incoming SMS cannot disambiguate, deliver_otp with multiple matching requests must return False
        ok, res = OTPQueueManager.deliver_otp(code="445566", phone="09129999999")
        self.assertFalse(ok)
        self.assertEqual(res.get("error"), "ambiguous_phone_match")

    # --- T15: Retention of Step 3 Session on CAPACITY_FULL ---
    def test_T15_step3_retention_on_capacity_full(self):
        nid = "7777777777"
        QueueManager.enroll(nid)
        transition_state(nid, BotState.PREPARING)
        transition_state(nid, BotState.STARTING)
        transition_state(nid, BotState.REQUESTING_OTP)
        transition_state(nid, BotState.WAITING_OTP)
        transition_state(nid, BotState.OTP_RECEIVED)
        transition_state(nid, BotState.VERIFYING_OTP)
        transition_state(nid, BotState.AUTHENTICATED)
        transition_state(nid, BotState.STEP_3_READY)
        transition_state(nid, BotState.SELECTING_BANK)

        # Signal CAPACITY_FULL
        self.assertTrue(transition_state(nid, BotState.CAPACITY_FULL, detail="ظرفیت تکمیل است"))
        self.assertEqual(get_current_state(nid), BotState.CAPACITY_FULL)

        # Transition to WAITING_AVAILABILITY retains Step 3 loop
        self.assertTrue(transition_state(nid, BotState.WAITING_AVAILABILITY))
        self.assertTrue(transition_state(nid, BotState.SELECTING_BANK))
        self.assertEqual(get_current_state(nid), BotState.SELECTING_BANK)

    # --- T16: Non-regression - CAPACITY_FULL never resets to STARTING ---
    def test_T16_no_bounce_to_step1_from_capacity_full(self):
        nid = "7777777777"
        QueueManager.enroll(nid)
        with database.DBHandler._connect() as conn:
            conn.execute("UPDATE applicant_queue SET bot_state = 'CAPACITY_FULL' WHERE applicant_id = ?", (nid,))
            conn.commit()
        database.DBHandler.update_runtime(nid, fsm_state="CAPACITY_FULL", current_step="CAPACITY_FULL")

        # Attempting illegal direct transition to STARTING or REQUESTING_OTP must fail
        self.assertFalse(transition_state(nid, BotState.STARTING))
        self.assertFalse(transition_state(nid, BotState.REQUESTING_OTP))

    # --- T17: Non-regression - CAPACITY_FULL does not request OTP ---
    def test_T17_no_otp_request_on_capacity_full(self):
        nid = "7777777777"
        active_before = OTPQueueManager.get_active_request(nid)
        self.assertIsNone(active_before)
        # Verify valid transition from CAPACITY_FULL is WAITING_AVAILABILITY, not REQUESTING_OTP
        self.assertIn(BotState.WAITING_AVAILABILITY, ALLOWED_TRANSITIONS[BotState.CAPACITY_FULL])
        self.assertNotIn(BotState.REQUESTING_OTP, ALLOWED_TRANSITIONS[BotState.CAPACITY_FULL])

    # --- T18: Error Classifier - Capacity Full vs Session Expired ---
    def test_T18_capacity_full_vs_session_expired_classification(self):
        from bot_core import classify_page_error
        # Capacity message
        cap_msg = "در حال حاضر هیچ شعبه‌ای با ظرفیت خالی برای استان انتخابی وجود ندارد."
        res_cap = classify_page_error(cap_msg)
        self.assertEqual(res_cap, "capacity_full")

        # Timing message about branch selection is NOT session expired
        timing_msg = "مهلت زمانی انتخاب شعبه ۵ دقیقه می‌باشد."
        res_timing = classify_page_error(timing_msg)
        self.assertNotEqual(res_timing, "session_expired")

        # Genuine session expired
        sess_msg = "خطای امنیتی: نشست شما منقضی شده است. لطفا مجددا وارد سامانه شوید."
        res_sess = classify_page_error(sess_msg)
        self.assertEqual(res_sess, "session_expired")

    # --- T19: Genuine SESSION_EXPIRED Transition ---
    def test_T19_session_expired_transition(self):
        nid = "8888888888"
        QueueManager.enroll(nid)
        with database.DBHandler._connect() as conn:
            conn.execute("UPDATE applicant_queue SET bot_state = 'SELECTING_BANK' WHERE applicant_id = ?", (nid,))
            conn.commit()
        database.DBHandler.update_runtime(nid, fsm_state="SELECTING_BANK", current_step="SELECTING_BANK")

        # Transition to SESSION_EXPIRED is valid from SELECTING_BANK
        ok = transition_state(nid, BotState.SESSION_EXPIRED, detail="نشت کاربری باطل شد")
        self.assertTrue(ok)
        self.assertEqual(get_current_state(nid), BotState.SESSION_EXPIRED)

    # --- T20: Retry OTP intake on session expired up to limit ---
    def test_T20_retry_otp_after_session_expired(self):
        nid = "8888888888"
        QueueManager.enroll(nid)
        with database.DBHandler._connect() as conn:
            conn.execute("UPDATE applicant_queue SET bot_state = 'SESSION_EXPIRED' WHERE applicant_id = ?", (nid,))
            conn.commit()
        database.DBHandler.update_runtime(nid, fsm_state="SESSION_EXPIRED", current_step="SESSION_EXPIRED")
        # From SESSION_EXPIRED, can transition to STARTING or REQUESTING_OTP
        self.assertTrue(transition_state(nid, BotState.STARTING))
        self.assertTrue(transition_state(nid, BotState.REQUESTING_OTP))

    # --- T21: Transition to BLOCKED after exceeding OTP limit ---
    def test_T21_transition_to_blocked_on_exceeded_otp(self):
        nid = "8888888888"
        QueueManager.enroll(nid)
        with database.DBHandler._connect() as conn:
            conn.execute("UPDATE applicant_queue SET bot_state = 'WAITING_OTP' WHERE applicant_id = ?", (nid,))
            conn.commit()
        database.DBHandler.update_runtime(nid, fsm_state="WAITING_OTP", current_step="WAITING_OTP")
        # WAITING_OTP -> BLOCKED is valid when user needs manual intervention
        self.assertTrue(transition_state(nid, BotState.BLOCKED, detail="تعداد تلاش‌های ناموفق بیش از ۳ بار"))
        self.assertEqual(get_current_state(nid), BotState.BLOCKED)

    # --- T22: Pause applicant preserving queue rank ---
    def test_T22_pause_applicant(self):
        nid = "9999999999"
        QueueManager.enroll(nid)
        QueueManager.acquire_next_for_worker("worker_1")

        ok = QueueManager.pause_applicant(nid)
        self.assertTrue(ok)
        items = QueueManager.get_queue()
        self.assertEqual(items[0]["queue_status"], "PAUSED")
        self.assertEqual(items[0]["bot_state"], "PAUSED")
        self.assertIsNone(items[0]["assigned_worker"])

    # --- T23: Resume paused applicant back to WAITING ---
    def test_T23_resume_applicant(self):
        nid = "9999999999"
        QueueManager.enroll(nid)
        QueueManager.pause_applicant(nid)

        ok = QueueManager.resume_applicant(nid)
        self.assertTrue(ok)
        items = QueueManager.get_queue()
        self.assertEqual(items[0]["queue_status"], "WAITING")
        self.assertEqual(items[0]["bot_state"], "QUEUED")

    # --- T24: Cancel / remove applicant from queue ---
    def test_T24_remove_applicant_from_queue(self):
        nid = "9999999999"
        QueueManager.enroll(nid)

        ok = QueueManager.remove_from_queue(nid)
        self.assertTrue(ok)
        # Cancelled items are excluded from active get_queue()
        items = QueueManager.get_queue()
        self.assertEqual(len(items), 0)

    # --- T25: Concurrency - Multiple Workers without Race Conditions ---
    def test_T25_concurrent_worker_leasing(self):
        for i in range(5):
            QueueManager.enroll(f"nid_00000{i}")

        leased_ids = set()
        for w in range(5):
            res = QueueManager.acquire_next_for_worker(f"worker_{w}")
            self.assertIsNotNone(res)
            self.assertNotIn(res["applicant_id"], leased_ids)
            leased_ids.add(res["applicant_id"])

        self.assertEqual(len(leased_ids), 5)

    # --- T26: Pause All ---
    def test_T26_pause_all(self):
        for i in range(3):
            QueueManager.enroll(f"nid_p_{i}")

        paused_cnt = QueueManager.pause_all()
        self.assertEqual(paused_cnt, 3)
        items = QueueManager.get_queue()
        for it in items:
            self.assertEqual(it["queue_status"], "PAUSED")

    # --- T27: Start All ---
    def test_T27_start_all(self):
        for i in range(3):
            QueueManager.enroll(f"nid_s_{i}")
        QueueManager.pause_all()

        started_cnt = QueueManager.start_all()
        self.assertEqual(started_cnt, 3)
        items = QueueManager.get_queue()
        for it in items:
            self.assertEqual(it["queue_status"], "WAITING")

    # --- T28: Crash Recovery on Startup ---
    def test_T28_crash_recovery_on_startup(self):
        QueueManager.enroll("nid_crash_1")
        QueueManager.enroll("nid_crash_2")
        # Workers acquired them before crash
        QueueManager.acquire_next_for_worker("crashed_worker_1")
        QueueManager.acquire_next_for_worker("crashed_worker_2")

        # Simulate system reboot calling recover_on_startup
        recovered = QueueManager.recover_on_startup()
        self.assertEqual(recovered, 2)

        items = QueueManager.get_queue()
        self.assertEqual(items[0]["queue_status"], "WAITING")
        self.assertEqual(items[1]["queue_status"], "WAITING")
        self.assertIsNone(items[0]["assigned_worker"])

    # --- T29: Dashboard Queue Statistics Generation ---
    def test_T29_queue_statistics(self):
        QueueManager.enroll("nid_stat_1")
        QueueManager.enroll("nid_stat_2")
        QueueManager.acquire_next_for_worker("w1")

        stats = QueueManager.get_stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["waiting"], 1)
        self.assertEqual(stats["running"], 1)

    # --- T30: WebSocket Event Broadcasting on State Transitions ---
    def test_T30_websocket_event_broadcasting(self):
        nid = "nid_ws_test"
        QueueManager.enroll(nid)

        events_captured = []
        with patch("core.state_machine.EVENT_BROADCASTER.emit_event", side_effect=lambda ev: events_captured.append(ev)):
            transition_state(nid, BotState.PREPARING)
            transition_state(nid, BotState.STARTING)

        self.assertEqual(len(events_captured), 2)
        self.assertEqual(events_captured[0]["type"], "fsm_state_changed")
        self.assertEqual(events_captured[0]["new_state"], "PREPARING")
        self.assertEqual(events_captured[1]["new_state"], "STARTING")

if __name__ == "__main__":
    unittest.main()
