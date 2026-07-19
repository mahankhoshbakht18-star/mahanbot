import unittest

from batch_automation import BatchController, _job_summary, _normalize_nid


class FakeQueue:
    def list_jobs(self):
        return [
            {"id": "1", "status": "queued", "created_at": 2},
            {"id": "2", "status": "running", "created_at": 3},
            {"id": "3", "status": "failed", "created_at": 1},
        ]


class BatchAutomationTests(unittest.TestCase):
    def test_normalizes_persian_and_arabic_digits(self):
        self.assertEqual(_normalize_nid(" ۱۲۳-٤٥٦ ٧٨٩٠ "), "1234567890")

    def test_job_summary_counts_and_sorts(self):
        result = _job_summary(FakeQueue())
        self.assertEqual(result["counts"]["queued"], 1)
        self.assertEqual(result["counts"]["running"], 1)
        self.assertEqual(len(result["active"]), 2)
        self.assertEqual(result["recent"][0]["id"], "2")

    def test_controller_returns_copies(self):
        controller = BatchController()
        saved = controller.remember({"id": "batch-1", "queued": []})
        saved["id"] = "changed"
        self.assertEqual(controller.snapshot()["id"], "batch-1")


if __name__ == "__main__":
    unittest.main()
