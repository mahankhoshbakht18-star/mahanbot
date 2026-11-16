"""
core/bank_selection/postback_sync.py — ASP.NET WebForms Event-Driven Postback Synchronization
Replaces static sleeps with deterministic Sys.WebForms PageRequestManager monitoring.
"""
import time
from typing import Optional


class PostbackSynchronizer:
    """Synchronizes ASP.NET UpdatePanel postback cycles without arbitrary thread sleeps."""

    @staticmethod
    def is_in_postback(page) -> bool:
        """Query ASP.NET PageRequestManager runtime state."""
        try:
            return bool(page.evaluate("""() => {
                try {
                    if (typeof Sys !== 'undefined' && Sys.WebForms && Sys.WebForms.PageRequestManager) {
                        return Sys.WebForms.PageRequestManager.getInstance().get_isInAsyncPostBack();
                    }
                } catch(e) {}
                return false;
            }"""))
        except Exception:
            return False

    @classmethod
    def wait_for_postback_complete(cls, page, timeout_ms: int = 6000, poll_interval_ms: int = 50) -> bool:
        """
        Wait until any in-flight ASP.NET async postback finishes completely.
        Returns True if postback finished cleanly within timeout, False on timeout or error.
        """
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        started_postback = False

        # First, briefly check if postback is initiated (or already in progress)
        for _ in range(5):
            if cls.is_in_postback(page):
                started_postback = True
                break
            time.sleep(0.02)

        if not started_postback:
            # No postback triggered or finished within 100ms
            return True

        # Wait for postback to complete
        while time.monotonic() < deadline:
            if not cls.is_in_postback(page):
                # Extra small grace period to allow DOM mutations to settle
                try:
                    page.wait_for_timeout(40)
                except Exception:
                    pass
                return True
            try:
                page.wait_for_timeout(poll_interval_ms)
            except Exception:
                time.sleep(poll_interval_ms / 1000.0)

        return False

    @classmethod
    def select_and_sync(
        cls,
        page,
        selector: str,
        value: str,
        timeout_ms: int = 7000,
        label: str = "",
    ) -> bool:
        """
        Select an option in an ASP.NET dropdown and wait for the resulting postback.
        Prefer label when duplicate values exist. Never fires a second synthetic __doPostBack.
        """
        try:
            from core.aspnet_postback import read_viewstate, wait_for_viewstate_change
            locator = page.locator(selector).first
            if not locator.is_visible(timeout=timeout_ms):
                return False

            previous_vs = read_viewstate(page)
            selected = False
            if label:
                try:
                    locator.select_option(label=label)
                    selected = True
                except Exception:
                    selected = False
            if not selected:
                locator.select_option(value=value)
            locator.dispatch_event("change")

            ok = cls.wait_for_postback_complete(page, timeout_ms=timeout_ms)
            wait_for_viewstate_change(page, previous_vs, timeout_ms=min(2500, timeout_ms))
            return ok
        except Exception:
            return False
