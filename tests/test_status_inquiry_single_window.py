"""Status inquiry: starts at once, only one window at a time, never chains to another applicant."""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from core.job_queue import JobQueue


class StatusInquiryQueueTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.db.get_runtime.return_value = {"status": "SUCCESS", "current_step": "DONE"}
        patches = [
            patch("core.job_queue.DBHandler", self.db),
            patch("core.job_queue.log_event"),
            patch("core.job_queue.job_status_event"),
            patch("core.state_machine.transition_state"),
            patch("core.queue_manager.QueueManager"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.queue = JobQueue(max_concurrency=1)
        self.release = threading.Event()
        self.started = {}

        def fake_run(job):
            self.started[job.nid] = time.monotonic()
            # A registration holds the only worker; a status window stays open until stopped.
            while not (self.release.is_set() or job.stop_event.is_set()):
                time.sleep(0.01)

        self.queue._run_job_isolated = fake_run
        self.queue.start()
        self.addCleanup(self.release.set)

    def wait_for(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_status_does_not_wait_behind_busy_workers(self):
        self.queue.enqueue("select", "1000000001", {})
        self.assertTrue(self.wait_for(lambda: "1000000001" in self.started))
        self.queue.enqueue("status", "2000000002", {})
        self.assertTrue(self.wait_for(lambda: "2000000002" in self.started, timeout=0.5))

    def test_only_one_status_window_at_a_time(self):
        first = self.queue.enqueue("status", "3000000003", {})
        self.assertIsNotNone(first)
        self.assertIsNone(self.queue.enqueue("status", "4000000004", {}))
        self.assertIs(self.queue.active_job_of("status"), first)
        self.queue.cancel(first.id)
        self.assertTrue(self.wait_for(lambda: first.status not in JobQueue.ACTIVE_STATUSES))
        self.assertIsNotNone(self.queue.enqueue("status", "4000000004", {}))

    def test_successful_status_never_starts_next_applicant(self):
        with patch.object(JobQueue, "trigger_next_applicant_on_success") as chain:
            job = self.queue.enqueue("status", "5000000005", {})
            self.release.set()
            self.assertTrue(self.wait_for(lambda: job.status == "completed"))
            chain.assert_not_called()

    def test_successful_registration_still_chains(self):
        with patch.object(JobQueue, "trigger_next_applicant_on_success") as chain:
            job = self.queue.enqueue("select", "6000000006", {})
            self.release.set()
            self.assertTrue(self.wait_for(lambda: job.status == "completed"))
            self.assertTrue(self.wait_for(lambda: chain.called))


if __name__ == "__main__":
    unittest.main()
