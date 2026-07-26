import time
import unittest

from sms_notify_core import SmsArrivalSignal, SmsNotifyRegistry


class SmsNotifyRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = SmsNotifyRegistry(
            max_events=20,
            event_ttl_seconds=120,
            device_online_seconds=30,
        )

    def test_arrival_signal_does_not_expose_message_id_or_sensitive_content(self):
        signal = SmsArrivalSignal(
            device_id="android-test",
            message_id="private-local-message-id",
            received_at=time.time(),
        )
        accepted, public = self.registry.add(signal)

        self.assertTrue(accepted)
        self.assertNotIn("message_id", public)
        self.assertNotIn("body", public)
        self.assertNotIn("otp", public)
        self.assertEqual(len(public["event_id"]), 16)

    def test_duplicate_signal_is_ignored(self):
        now = time.time()
        first = SmsArrivalSignal("device-a", "event-1", now)
        second = SmsArrivalSignal("device-a", "event-1", now + 1)

        accepted_first, _ = self.registry.add(first)
        accepted_second, _ = self.registry.add(second)

        self.assertTrue(accepted_first)
        self.assertFalse(accepted_second)
        self.assertEqual(len(self.registry.latest()), 1)

    def test_same_message_id_on_different_devices_is_not_duplicate(self):
        now = time.time()
        accepted_a, _ = self.registry.add(SmsArrivalSignal("device-a", "event-1", now))
        accepted_b, _ = self.registry.add(SmsArrivalSignal("device-b", "event-1", now))

        self.assertTrue(accepted_a)
        self.assertTrue(accepted_b)
        self.assertEqual(len(self.registry.latest()), 2)

    def test_latest_respects_cursor(self):
        now = time.time()
        self.registry.add(SmsArrivalSignal("device-a", "event-1", now, created_at=now))
        self.registry.add(SmsArrivalSignal("device-a", "event-2", now + 1, created_at=now + 1))

        events = self.registry.latest(since=now + 0.5)

        self.assertEqual(len(events), 1)
        self.assertGreater(events[0]["created_at"], now + 0.5)

    def test_heartbeat_marks_device_online(self):
        row = self.registry.heartbeat("device-a")
        devices = self.registry.devices()

        self.assertTrue(row["online"])
        self.assertEqual(devices[0]["device_id"], "device-a")
        self.assertTrue(devices[0]["online"])

    def test_invalid_empty_identifiers_are_rejected(self):
        with self.assertRaises(ValueError):
            SmsArrivalSignal("", "event-1", time.time())
        with self.assertRaises(ValueError):
            SmsArrivalSignal("device-a", "", time.time())


if __name__ == "__main__":
    unittest.main()
