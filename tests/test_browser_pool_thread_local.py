import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.browser_pool import (
    get_shared_playwright,
    invalidate_thread_browser,
    shutdown_thread_pool,
)
from core.browser_profile_health import BrowserProfileHealthManager


class BrowserPoolThreadLocalTests(unittest.TestCase):
    def tearDown(self):
        try:
            shutdown_thread_pool()
        except Exception:
            pass

    def test_playwright_is_thread_local(self):
        created = []

        class _FakePw:
            def stop(self):
                return None

        def _start():
            pw = _FakePw()
            created.append((threading.current_thread().name, id(pw)))
            return pw

        with patch("playwright.sync_api.sync_playwright") as sp:
            sp.return_value.start.side_effect = _start
            invalidate_thread_browser(stop_playwright=True)
            a = {"pw": None}
            b = {"pw": None}

            def _t1():
                a["pw"] = get_shared_playwright()

            def _t2():
                b["pw"] = get_shared_playwright()

            t1 = threading.Thread(target=_t1, name="job-a")
            t2 = threading.Thread(target=_t2, name="job-b")
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        self.assertIsNotNone(a["pw"])
        self.assertIsNotNone(b["pw"])
        self.assertIsNot(a["pw"], b["pw"])
        self.assertEqual(len(created), 2)

    def test_clear_stale_locks_removes_singleton(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            lock = p / "SingletonLock"
            lock.write_text("x", encoding="utf-8")
            (p / "SingletonCookie").write_text("x", encoding="utf-8")
            ok, msg = BrowserProfileHealthManager.clear_stale_locks(p)
            self.assertTrue(ok)
            self.assertFalse(lock.exists())
            self.assertIn("cleared", msg)


if __name__ == "__main__":
    unittest.main()
