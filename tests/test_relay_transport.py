import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import relay_transport as transport


class RelayTransportTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"MAHAN_OTP_RELAY_DISABLED": "0", "RELAY_BOT_KEY": "fixture-key", "MAHAN_OTP_RELAY_URL": "https://relay.invalid"})
        self.env.start()
        self.bot = SimpleNamespace(nid="0012345679", settings={"otp_validity_seconds": 120},
            user_data={"mobile": "09123456789"}, _otp_entry_time=time.time()-1, _otp_request_id="fixture-request")
    def tearDown(self):
        self.env.stop()
    def test_phone_hash_normalization_matches_android(self):
        self.assertEqual(transport.phone_hash("+989123456789"), transport.phone_hash("۰۹۱۲۳۴۵۶۷۸۹"))
        self.assertEqual(transport.phone_hash("2000411"), "")
    @patch("relay_transport.requests.post")
    def test_register_sends_hash_not_phone_and_reuses_request(self, post):
        post.return_value = Mock(status_code=200, **{"json.return_value": {"accepted": True}})
        self.assertTrue(transport.publish_wait(self.bot))
        self.assertTrue(transport.publish_wait(self.bot))
        self.assertEqual(post.call_count, 1)
        data = post.call_args.kwargs["json"]
        self.assertNotIn("09123456789", str(data))
        self.assertEqual(data["job_id"], self.bot._otp_request_id)
    @patch("relay_transport.requests.post")
    def test_failed_publish_can_retry(self, post):
        post.return_value = Mock(status_code=503)
        self.assertFalse(transport.publish_wait(self.bot))
        self.assertFalse(transport.publish_wait(self.bot))
        self.assertEqual(post.call_count, 2)
    @patch("relay_transport.requests.post")
    def test_old_request_is_bound_when_cleared(self, post):
        transport.finish(self.bot)
        self.assertEqual(post.call_args.kwargs["params"]["request_id"], "fixture-request")
