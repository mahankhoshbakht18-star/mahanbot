"""
Unit and Integration Test for Auto-Chain Next Applicant on Success.
Verifies that when a target bank opens and an applicant is successfully registered,
the bot automatically starts/enqueues the next eligible waiting applicant.
"""
import sys
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database
from core.job_queue import JobQueue, Job

class AutoChainQueueTests(unittest.TestCase):
    def setUp(self):
        self.orig_db_path = database.DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mahanbot-autochain-")
        database.DB_PATH = str(Path(self.temp_dir.name) / "autochain.db")
        database.DBHandler.init_db()

    def tearDown(self):
        database.DB_PATH = self.orig_db_path
        self.temp_dir.cleanup()

    def test_auto_chain_triggers_highest_priority_waiting_applicant(self):
        # 1. Seed applicants
        # Completed applicant (registered at Bank Melli in Tehran)
        app1_data = json.dumps({"province": "تهران", "preferred_bank": "بانک ملی ایران", "status": "Ready"})
        # Candidate 2: Same bank (Bank Melli) and same province (Tehran) -> Score = 50 + 30 = 80
        app2_data = json.dumps({"province": "تهران", "preferred_bank": "بانک ملی ایران", "status": "Ready"})
        # Candidate 3: Same province (Tehran) but different bank (Bank Mellat) -> Score = 30
        app3_data = json.dumps({"province": "تهران", "preferred_bank": "بانک ملت", "status": "Ready"})
        # Candidate 4: Already registered -> Must NOT be selected
        app4_data = json.dumps({"province": "تهران", "preferred_bank": "بانک ملی ایران", "tracking_code": "11223344", "status": "Success"})

        conn = sqlite3.connect(database.DB_PATH)
        conn.execute("INSERT INTO applicants (national_id, full_name, data, status) VALUES (?, ?, ?, ?)",
                     ("0011111111", "Applicant 1", app1_data, "Ready"))
        conn.execute("INSERT INTO applicants (national_id, full_name, data, status) VALUES (?, ?, ?, ?)",
                     ("0022222222", "Applicant 2 (Target Bank)", app2_data, "Ready"))
        conn.execute("INSERT INTO applicants (national_id, full_name, data, status) VALUES (?, ?, ?, ?)",
                     ("0033333333", "Applicant 3 (Other Bank)", app3_data, "Ready"))
        conn.execute("INSERT INTO applicants (national_id, full_name, data, status) VALUES (?, ?, ?, ?)",
                     ("0044444444", "Applicant 4 (Already Done)", app4_data, "Success"))
        conn.commit()
        conn.close()

        q = JobQueue(max_concurrency=2)
        
        # Trigger next applicant on success of Applicant 1
        next_nid = q.trigger_next_applicant_on_success("0011111111", bot_name="select")

        # Must have selected Candidate 2 (same bank in Tehran!)
        self.assertEqual(next_nid, "0022222222")

        # Verify Job was enqueued in q
        active_job = q.find_active("select", "0022222222")
        self.assertIsNotNone(active_job)
        self.assertEqual(active_job.nid, "0022222222")
        self.assertEqual(active_job.status, "queued")

    def test_worker_loop_auto_chains_in_real_execution(self):
        conn = sqlite3.connect(database.DB_PATH)
        app1_data = json.dumps({"province": "اصفهان", "preferred_bank": "بانک صادرات", "status": "Ready"})
        app2_data = json.dumps({"province": "اصفهان", "preferred_bank": "بانک صادرات", "status": "Ready"})
        conn.execute("INSERT INTO applicants (national_id, full_name, data, status) VALUES (?, ?, ?, ?)",
                     ("1111111111", "Person 1", app1_data, "Ready"))
        conn.execute("INSERT INTO applicants (national_id, full_name, data, status) VALUES (?, ?, ?, ?)",
                     ("2222222222", "Person 2", app2_data, "Ready"))
        conn.commit()
        conn.close()

        q = JobQueue(max_concurrency=1)

        # Mock bot execution: Person 1 succeeds, Person 2 is executed next
        started_nids = []

        def mock_runner(job):
            started_nids.append(job.nid)
            if job.nid == "1111111111":
                # Mark as success in runtime
                database.DBHandler.update_runtime(job.nid, status="SUCCESS", current_step="DONE")
            else:
                database.DBHandler.update_runtime(job.nid, status="SUCCESS", current_step="DONE")

        q._run_job = mock_runner
        q.start()

        # Enqueue only Person 1 initially
        job1 = q.enqueue("select", "1111111111", {})

        # Wait for both to be processed (Person 1 succeeds -> Person 2 auto-starts!)
        import time
        timeout = 4.0
        t0 = time.time()
        while len(started_nids) < 2 and time.time() - t0 < timeout:
            time.sleep(0.05)

        q.stop()

        # Both Person 1 and Person 2 were executed sequentially!
        self.assertIn("1111111111", started_nids)
        self.assertIn("2222222222", started_nids)
        self.assertEqual(started_nids[0], "1111111111")
        self.assertEqual(started_nids[1], "2222222222")

if __name__ == "__main__":
    unittest.main()
