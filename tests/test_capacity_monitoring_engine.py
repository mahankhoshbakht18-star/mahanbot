"""
tests/test_capacity_monitoring_engine.py — Comprehensive multi-worker capacity monitoring tests
"""
import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

try:
    import test_critical_repair as repair
except ImportError:
    from tests import test_critical_repair as repair
from fastapi.testclient import TestClient
from server import app
from database import DBHandler, normalize_config
from live_sessions import LIVE_SESSIONS
from capacity_monitor import (
    CapacityMonitor,
    CapacityState,
    CAPACITY_MONITOR_REGISTRY,
)
from bank_session_controller import BankSelectionSessionController
from bank_page_manager import BankPageState as State


class CapacityMonitoringEngineTests(repair.RepairTestCase):
    def setUp(self):
        with LIVE_SESSIONS._lock:
            LIVE_SESSIONS._sessions.clear()
        super().setUp()
        import core.state as _state
        from core.job_queue import JobQueue
        self.previous_queue = _state.JOB_QUEUE
        # Exercise queue/API bookkeeping without launching a real applicant worker.
        self.worker_patch = patch.object(JobQueue, '_run_job', return_value=None)
        self.worker_patch.start()
        _state.JOB_QUEUE = JobQueue(max_concurrency=4)
        self.client = TestClient(app)
        self.auth_token = "test-session-token"
        DBHandler.save_app_session(self.auth_token, "admin", time.time() + 3600)
        self.client.cookies.set("mahan_auth", self.auth_token)

    def tearDown(self):
        import core.state as _state
        if _state.JOB_QUEUE:
            try:
                _state.JOB_QUEUE.stop_all()
                _state.JOB_QUEUE.stop()
            except Exception:
                pass
        _state.JOB_QUEUE = self.previous_queue
        self.worker_patch.stop()
        with LIVE_SESSIONS._lock:
            LIVE_SESSIONS._sessions.clear()
        with CAPACITY_MONITOR_REGISTRY._lock:
            CAPACITY_MONITOR_REGISTRY._monitors.clear()
        import gc
        gc.collect()
        try:
            super().tearDown()
        except Exception:
            pass

    def test_capacity_monitor_lifecycle_and_jitter(self):
        """Ensure deterministic jitter offset and registry registration/cleanup."""
        bot1 = Mock()
        bot1.nid = "1111111111"
        owner1 = Mock()
        owner1.alive = True
        owner1.bank_page_active = True
        owner1.paused = False
        owner1.session_id = "sess-111"
        owner1.cancellation_token = threading.Event()

        bot2 = Mock()
        bot2.nid = "2222222222"
        owner2 = Mock()
        owner2.alive = True
        owner2.bank_page_active = True
        owner2.paused = False
        owner2.session_id = "sess-222"
        owner2.cancellation_token = threading.Event()

        mon1 = CapacityMonitor(bot1, owner1, interval_seconds=5.0)
        mon2 = CapacityMonitor(bot2, owner2, interval_seconds=5.0)

        # Jitter must be deterministic and distinct for different NIDs
        self.assertNotEqual(mon1.jitter_offset, mon2.jitter_offset)
        self.assertGreaterEqual(mon1.jitter_offset, 0.0)
        self.assertLessEqual(mon1.jitter_offset, 1.5)

        # Verify registry tracking
        self.assertEqual(CAPACITY_MONITOR_REGISTRY.count_active(), 2)
        self.assertIsNotNone(CAPACITY_MONITOR_REGISTRY.get("1111111111"))
        self.assertIsNotNone(CAPACITY_MONITOR_REGISTRY.get("2222222222"))

        # Cleanup
        mon1.cleanup()
        self.assertIsNone(CAPACITY_MONITOR_REGISTRY.get("1111111111"))
        self.assertIsNotNone(CAPACITY_MONITOR_REGISTRY.get("2222222222"))
        mon2.cleanup()
        self.assertEqual(CAPACITY_MONITOR_REGISTRY.count_active(), 0)

    def test_multi_worker_state_transitions_and_isolation(self):
        """Simulate concurrent workers in different states without mutual interference."""
        bots = []
        owners = []
        monitors = []

        nids = ["1000000001", "2000000002", "3000000003"]
        for nid in nids:
            b = Mock()
            b.nid = nid
            b.log = Mock()
            o = Mock()
            o.alive = True
            o.bank_page_active = True
            o.paused = False
            o.session_id = f"session-{nid}"
            o.cancellation_token = threading.Event()
            m = CapacityMonitor(b, o, interval_seconds=5.0)
            bots.append(b)
            owners.append(o)
            monitors.append(m)

        m1, m2, m3 = monitors

        # Worker 1: Capacity Full (WAITING_CAPACITY)
        m1.record_check_attempt("capacity_full", "ظرفیت غیرحضوری استان تکمیل شده است.")
        self.assertEqual(m1.state, CapacityState.WAITING_CAPACITY)
        self.assertEqual(m1.checks_count, 1)
        self.assertIsNotNone(m1.waiting_since)

        # Worker 2: Rate limit / WAF
        initial_interval = m2.current_interval
        m2.record_check_attempt("waf_blocked", "429 Too Many Requests")
        self.assertEqual(m2.state, CapacityState.ERROR)
        self.assertGreater(m2.backoff_factor, 1.0)
        self.assertGreater(m2.calculate_next_interval(), initial_interval)

        # Worker 3: Sniping Success
        m3.record_check_attempt("success", "کد رهگیری: 99887766")
        m3.tracking_code = "99887766"
        self.assertEqual(m3.state, CapacityState.SUCCESS)

        # Verify Worker 1 and 2 were NOT affected by Worker 3's success
        self.assertEqual(m1.state, CapacityState.WAITING_CAPACITY)
        self.assertEqual(m2.state, CapacityState.ERROR)
        self.assertEqual(CAPACITY_MONITOR_REGISTRY.count_waiting_capacity(), 1)

        # Worker 1 retries again -> check count increments, waiting_since persists
        first_waiting_since = m1.waiting_since
        time.sleep(0.01)
        m1.record_check_attempt("capacity_full", "ظرفیت غیرحضوری استان تکمیل شده است.")
        self.assertEqual(m1.checks_count, 2)
        self.assertEqual(m1.waiting_since, first_waiting_since)

    def test_api_endpoints_status_and_controls(self):
        """Test API endpoints for live status, interval, concurrency, and batch actions."""
        # 1. Status endpoint
        resp = self.client.get("/api/capacity/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("monitors", data)
        self.assertIn("summary", data)
        self.assertIn("config", data)

        # 2. Interval update endpoint
        resp = self.client.post("/api/capacity/interval", json={"interval_seconds": 8.0})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["bank_capacity_check_interval"], 8.0)
        # Verify invalid interval rejection
        resp_bad = self.client.post("/api/capacity/interval", json={"interval_seconds": 0.5})
        self.assertEqual(resp_bad.status_code, 422)

        # 3. Concurrency update endpoint
        resp = self.client.post("/api/capacity/concurrency", json={"max_concurrency": 8})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["max_concurrency"], 8)

        # 4. Batch controls (start-all / stop-all)
        resp_stop = self.client.post("/api/capacity/stop-all")
        self.assertEqual(resp_stop.status_code, 200)
        self.assertEqual(resp_stop.json()["status"], "ok")

        resp_start = self.client.post("/api/capacity/start-all")
        self.assertEqual(resp_start.status_code, 200)
        self.assertEqual(resp_start.json()["status"], "ok")

    def test_strict_online_mode_preservation_under_capacity_full(self):
        """Ensure that when online mode is selected, capacity full never flips it to in_person."""
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        fixture_html = (Path(__file__).parent / "fixtures/bank_page_webforms.html").read_text(encoding="utf-8")
        context.route("**/*", lambda route: route.fulfill(content_type="text/html", body=fixture_html))
        page = context.new_page()
        page.goto("http://127.0.0.1/SelectBnkShb.aspx")

        self.owner.bind(context, page)
        controller = BankSelectionSessionController(self.bot, self.owner)
        controller._monotonic = lambda: 100.0

        # Simulate capacity full error message from server
        FULL_MSG = "ظرفیت غیرحضوری بانک انتخابی شما در استان تکمیل شده و امکان ثبت‌نام به‌صورت غیرحضوری را ندارید."
        page.evaluate('''msg => {
            document.querySelector('#ctl00_ContentPlaceHolder1_labErrMsg').textContent = msg;
        }''', FULL_MSG)

        # Observe page
        stage, observed = controller.observe()
        self.assertEqual(stage, "STEP_3_BANK_SELECT")
        self.assertEqual(observed.get("state"), State.CAPACITY_FULL)
        self.assertIn("تکمیل شده", observed.get("error_message", ""))

        # Transition to CAPACITY_FULL
        controller.transition(State.CAPACITY_FULL, observed.get("error_message"))

        # Verify capacity_monitor recorded WAITING_CAPACITY
        self.assertEqual(controller.capacity_monitor.state, CapacityState.WAITING_CAPACITY)
        self.assertEqual(controller.capacity_monitor.selected_mode, "online")

        # Cleanup
        browser.close()
        pw.stop()
