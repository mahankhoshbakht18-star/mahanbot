"""
tests/test_proxy_concurrency.py — Comprehensive Unit & Integration Tests for Phase 3:
1. Proxy Parsing, Health Check, Cooldown & Rotation Strategies (round_robin, per_applicant, random_healthy)
2. Anti-Detection Stealth Fingerprint Shield (WebGL, navigator.webdriver, Chrome runtime evasions)
3. Dynamic Concurrency Worker Scaling in JobQueue
4. Settings API & Proxy Test Endpoints
"""
import time
import socket
import unittest
from unittest.mock import MagicMock, patch

from database import normalize_config
from proxy_manager import (
    parse_proxy_string,
    check_proxy_health,
    ProxyPool,
    get_proxy_pool,
)
from browser_fingerprint import (
    get_stealth_scripts,
    get_stealth_browser_args,
    apply_stealth_shield,
    STEALTH_EVASION_JS,
)
from core.job_queue import JobQueue


class TestProxyManager(unittest.TestCase):
    """Test proxy parsing, health checking, and rotation strategies."""

    def test_parse_proxy_string_variants(self):
        # 1. Plain HTTP
        p1 = parse_proxy_string("http://192.168.1.100:8080")
        self.assertIsNotNone(p1)
        self.assertEqual(p1["protocol"], "http")
        self.assertEqual(p1["host"], "192.168.1.100")
        self.assertEqual(p1["port"], 8080)
        self.assertNotIn("username", p1)
        self.assertEqual(p1["server"], "http://192.168.1.100:8080")

        # 2. Host:port without protocol (defaults to http)
        p2 = parse_proxy_string("10.0.0.1:3128")
        self.assertIsNotNone(p2)
        self.assertEqual(p2["protocol"], "http")
        self.assertEqual(p2["host"], "10.0.0.1")
        self.assertEqual(p2["port"], 3128)
        self.assertEqual(p2["server"], "http://10.0.0.1:3128")

        # 3. Host:port:user:pass format (standard proxy format)
        p3 = parse_proxy_string("proxy.example.com:8080:myuser:mypassword")
        self.assertIsNotNone(p3)
        self.assertEqual(p3["host"], "proxy.example.com")
        self.assertEqual(p3["port"], 8080)
        self.assertEqual(p3["username"], "myuser")
        self.assertEqual(p3["password"], "mypassword")
        self.assertEqual(p3["server"], "http://proxy.example.com:8080")

        # 4. SOCKS5 with embedded credentials
        p4 = parse_proxy_string("socks5://admin:secret123@172.16.0.5:1080")
        self.assertIsNotNone(p4)
        self.assertEqual(p4["protocol"], "socks5")
        self.assertEqual(p4["host"], "172.16.0.5")
        self.assertEqual(p4["port"], 1080)
        self.assertEqual(p4["username"], "admin")
        self.assertEqual(p4["password"], "secret123")
        self.assertEqual(p4["server"], "socks5://172.16.0.5:1080")

        # 5. Invalid strings
        self.assertIsNone(parse_proxy_string(""))
        self.assertIsNone(parse_proxy_string("   "))
        self.assertIsNone(parse_proxy_string("# Comment line"))
        self.assertIsNone(parse_proxy_string("invalid_no_port"))

    def test_proxy_pool_round_robin_rotation(self):
        pool = ProxyPool()
        pool.load_from_text("1.1.1.1:8080\n2.2.2.2:8080\n3.3.3.3:8080")
        self.assertEqual(pool.get_pool_status()["total_count"], 3)

        first = pool.get_proxy(strategy="round_robin")
        second = pool.get_proxy(strategy="round_robin")
        third = pool.get_proxy(strategy="round_robin")
        fourth = pool.get_proxy(strategy="round_robin")

        self.assertEqual(first["server"], "http://1.1.1.1:8080")
        self.assertEqual(second["server"], "http://2.2.2.2:8080")
        self.assertEqual(third["server"], "http://3.3.3.3:8080")
        self.assertEqual(fourth["server"], "http://1.1.1.1:8080")  # wraps around

    def test_proxy_pool_sticky_per_applicant_strategy(self):
        pool = ProxyPool()
        pool.load_from_text("10.0.0.1:8080\n10.0.0.2:8080")

        p_app1_first = pool.get_proxy(strategy="per_applicant", applicant_id="app_123")
        p_app1_second = pool.get_proxy(strategy="per_applicant", applicant_id="app_123")

        # Sticky session: same applicant gets identical proxy across calls
        self.assertEqual(p_app1_first["server"], p_app1_second["server"])

    def test_proxy_pool_health_and_cooldown(self):
        pool = ProxyPool()
        pool.load_from_text("1.1.1.1:8080\n2.2.2.2:8080")

        # Mark 1.1.1.1 as failed
        pool.mark_failure("http://1.1.1.1:8080", cooldown_seconds=60)
        status = pool.get_pool_status()
        self.assertEqual(status["cooldown_count"], 1)
        self.assertEqual(status["active_count"], 1)

        # Now get_proxy should skip failed and return 2.2.2.2
        next_p = pool.get_proxy(strategy="round_robin")
        self.assertEqual(next_p["server"], "http://2.2.2.2:8080")

        # Mark success restores health
        pool.mark_success("http://1.1.1.1:8080")
        self.assertEqual(pool.get_pool_status()["cooldown_count"], 0)

    @patch("socket.create_connection")
    def test_check_proxy_health_success_and_failure(self, mock_create_conn):
        # Case 1: Successful connection
        mock_sock = MagicMock()
        mock_create_conn.return_value = mock_sock

        node = parse_proxy_string("1.2.3.4:8080")
        res = check_proxy_health(node, timeout=2.0)
        self.assertTrue(res["healthy"])
        self.assertGreaterEqual(res["latency_ms"], 0)
        self.assertIsNone(res["error"])
        mock_sock.close.assert_called_once()

        # Case 2: Failed connection (timed out)
        mock_create_conn.side_effect = socket.timeout("Timed out")
        res_fail = check_proxy_health(node, timeout=1.0)
        self.assertFalse(res_fail["healthy"])
        self.assertIn("Timed out", res_fail["error"])

    @patch("proxy_manager.check_proxy_health")
    def test_proxy_pool_test_all(self, mock_health):
        mock_health.side_effect = [
            {"healthy": True, "latency_ms": 42.5, "server": "http://1.1.1.1:8080", "raw": "1.1.1.1:8080", "error": None},
            {"healthy": False, "latency_ms": 0, "server": "http://2.2.2.2:8080", "raw": "2.2.2.2:8080", "error": "Connection refused"},
        ]
        pool = ProxyPool()
        pool.load_from_text("1.1.1.1:8080\n2.2.2.2:8080")
        results = pool.test_all(timeout=2.0)

        self.assertEqual(len(results), 2)
        healthy_count = sum(1 for r in results if r["healthy"])
        failed_count = sum(1 for r in results if not r["healthy"])
        self.assertEqual(healthy_count, 1)
        self.assertEqual(failed_count, 1)


class TestAntiDetectionShield(unittest.TestCase):
    """Test browser fingerprint shield evasions and CLI flags."""

    def test_stealth_scripts_contents(self):
        scripts = get_stealth_scripts()
        self.assertIsInstance(scripts, list)
        self.assertGreater(len(scripts), 0)

        combined_js = "\n".join(scripts)
        # Verify core bot detection evasions exist in JS payload:
        self.assertIn("webdriver", combined_js)
        self.assertIn("chrome", combined_js)
        self.assertIn("plugins", combined_js)
        self.assertIn("languages", combined_js)
        self.assertIn("UNMASKED_RENDERER_WEBGL", combined_js)
        self.assertIn("Intel", combined_js)
        self.assertIn("fa-IR", combined_js)

    def test_stealth_browser_args(self):
        args = get_stealth_browser_args()
        self.assertIsInstance(args, list)
        self.assertIn("--disable-blink-features=AutomationControlled", args)
        self.assertTrue(any("lang" in arg for arg in args))

    def test_apply_stealth_shield(self):
        mock_context = MagicMock()
        apply_stealth_shield(mock_context)
        # Should have called add_init_script
        self.assertTrue(mock_context.add_init_script.called)


class TestDynamicConcurrencyWorkerScaling(unittest.TestCase):
    """Test dynamic scaling of worker threads in JobQueue."""

    def test_worker_scaling(self):
        # Initialize job queue with 2 workers and start
        queue = JobQueue(max_concurrency=2)
        queue.start()
        try:
            self.assertEqual(queue.max_concurrency, 2)
            self.assertEqual(len(queue.workers), 2)

            # Scale up to 5 workers
            scaled_count = queue.set_max_concurrency(5)
            self.assertEqual(scaled_count, 5)
            self.assertEqual(queue.max_concurrency, 5)
            self.assertEqual(len(queue.workers), 5)

            # Scale down to 3 workers
            scaled_count = queue.set_max_concurrency(3)
            self.assertEqual(scaled_count, 3)
            self.assertEqual(queue.max_concurrency, 3)
        finally:
            queue.stop()


class TestDatabaseAndSettingsEndpoints(unittest.TestCase):
    """Test database config normalization and proxy/concurrency endpoints."""

    def test_normalize_config_concurrency_and_proxies(self):
        cfg = normalize_config({})
        self.assertEqual(cfg["max_concurrency"], 4)
        self.assertEqual(cfg["proxy_rotation_strategy"], "round_robin")
        self.assertTrue(cfg["anti_detection_enabled"])
        self.assertEqual(cfg["proxy_health_check_timeout"], 4.0)

        # Test valid configuration
        valid = normalize_config({
            "max_concurrency": 16,
            "proxy_rotation_strategy": "per_applicant",
            "anti_detection_enabled": False,
        })
        self.assertEqual(valid["max_concurrency"], 16)
        self.assertEqual(valid["proxy_rotation_strategy"], "per_applicant")
        self.assertFalse(valid["anti_detection_enabled"])

        # Test out-of-bounds resets to default
        bounded = normalize_config({
            "max_concurrency": 999,  # out of bounds -> default 4
            "proxy_rotation_strategy": "invalid_mode",  # reset to round_robin
        })
        self.assertEqual(bounded["max_concurrency"], 4)
        self.assertEqual(bounded["proxy_rotation_strategy"], "round_robin")

    def test_proxy_pool_singleton(self):
        pool = get_proxy_pool()
        self.assertIsNotNone(pool)
        pool.load_from_text("127.0.0.1:8080\n127.0.0.1:8081")
        self.assertEqual(pool.get_pool_status()["total_count"], 2)


if __name__ == "__main__":
    unittest.main()
