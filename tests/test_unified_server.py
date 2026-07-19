import unittest

try:
    from unified_server import _env_port, _origin_is_local

    SERVER_RUNTIME_AVAILABLE = True
except ImportError:
    _env_port = None
    _origin_is_local = None
    SERVER_RUNTIME_AVAILABLE = False


@unittest.skipUnless(SERVER_RUNTIME_AVAILABLE, "full unified server dependencies are not installed")
class UnifiedServerTests(unittest.TestCase):
    def test_local_dashboard_origins_are_allowed(self):
        self.assertTrue(_origin_is_local("http://127.0.0.1:8000", 8000))
        self.assertTrue(_origin_is_local("http://localhost:8000", 8000))
        self.assertTrue(_origin_is_local("http://[::1]:8000", 8000))
        self.assertTrue(_origin_is_local("", 8000))

    def test_remote_or_wrong_port_origins_are_blocked(self):
        self.assertFalse(_origin_is_local("https://example.com", 8000))
        self.assertFalse(_origin_is_local("http://127.0.0.1:9000", 8000))
        self.assertFalse(_origin_is_local("null", 8000))

    def test_port_parser_falls_back_for_invalid_values(self):
        self.assertEqual(_env_port("MAHANBOT_TEST_PORT_NOT_SET", 8123), 8123)


if __name__ == "__main__":
    unittest.main()
