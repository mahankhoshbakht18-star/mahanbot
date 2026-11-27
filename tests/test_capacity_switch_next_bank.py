"""Auto final submit rejected for capacity -> next priority bank at once; all full -> new round."""
import unittest
from unittest.mock import MagicMock, patch

import bank_session_controller as bsc

OPTIONS = [
    {"text": "بانک صادرات [در انتظار تخصیص ظرفیت جدید (ذخیره)] - متقاضی در صف : 1674 نفر", "value": "1"},
    {"text": "بانک تجارت [در انتظار تخصیص ظرفیت جدید (ذخیره)] - متقاضی در صف : 1298 نفر", "value": "2"},
]


class SwitchAfterCapacityTests(unittest.TestCase):
    def _ctl(self, target):
        ctl = bsc.BankSelectionSessionController.__new__(bsc.BankSelectionSessionController)
        ctl.bot = MagicMock(nid="1")
        ctl.bot._bank_cooldowns = {}
        ctl.owner = MagicMock()
        ctl._prepared = ctl._submission_ready = True
        ctl._last_attempt_completed = 100.0
        ctl._next_submit = 120.0
        ctl._next_check = 120.0
        ctl._get_target_bank_names = lambda: ["صادرات", "تجارت"]
        self.runtime = {"target_bank": target}
        return ctl

    def _run(self, ctl):
        with patch.object(bsc.DBHandler, "get_runtime", return_value=self.runtime), \
             patch.object(bsc, "_read_dropdown_options", return_value=OPTIONS):
            return ctl._switch_after_capacity(20.0)

    def test_first_bank_full_switches_immediately(self):
        ctl = self._ctl("صادرات")
        self.assertTrue(self._run(ctl))
        self.assertFalse(ctl._prepared)
        self.assertEqual(ctl._next_submit, 0.0)
        self.assertIsNone(ctl._last_attempt_completed)
        self.assertIn(bsc.canonicalize_bank_name("صادرات"), ctl.bot._bank_cooldowns)

    def test_both_full_starts_new_monitoring_round(self):
        ctl = self._ctl("صادرات")
        self._run(ctl)
        self.runtime["target_bank"] = "تجارت"
        ctl._last_attempt_completed = 200.0
        self.assertFalse(self._run(ctl))
        self.assertEqual(ctl.bot._bank_cooldowns, {})  # back to first priority
        self.assertEqual(ctl._last_attempt_completed, 200.0)  # normal interval kept
        self.assertFalse(ctl._prepared)

    def test_single_bank_keeps_existing_retry(self):
        ctl = self._ctl("صادرات")
        ctl._get_target_bank_names = lambda: ["صادرات"]
        self.assertFalse(self._run(ctl))
        self.assertTrue(ctl._prepared)


if __name__ == "__main__":
    unittest.main()
