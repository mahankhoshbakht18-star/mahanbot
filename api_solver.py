"""Local CAPTCHA service.

The module keeps the legacy filename so existing imports continue to work, but
it performs no network requests and never sends CAPTCHA images off the machine.
"""

import os
import shutil
import tempfile
import threading

from bot_select_captcha import ModelCaptchaSolver


TEMP_CAPTCHA_FILENAME = "temp_captcha_element.png"


class CaptchaService:
    """Thin service wrapper around :class:`ModelCaptchaSolver`."""

    def __init__(self, solver=None, temp_root=None):
        self.solver = solver or ModelCaptchaSolver()
        self.temp_root = os.fspath(temp_root) if temp_root is not None else None

    def solve(self, image_source, mode="general"):
        """Solve an image locally; ``mode`` is retained for call compatibility."""
        del mode
        if image_source is None:
            return None
        try:
            return self.solver.solve(image_source)
        except Exception:
            # A bad screenshot or unavailable model is recoverable by refreshing
            # the CAPTCHA, so callers receive a simple unsuccessful result.
            return None

    def solve_element(self, captcha_element):
        """Screenshot a Playwright element, solve it, and always remove the file."""
        temp_dir = None
        screenshot_path = None
        try:
            if self.temp_root is not None:
                os.makedirs(self.temp_root, exist_ok=True)
            temp_dir = tempfile.mkdtemp(prefix="mahanbot-captcha-", dir=self.temp_root)
            screenshot_path = os.path.join(temp_dir, TEMP_CAPTCHA_FILENAME)
            captcha_element.screenshot(path=screenshot_path, type="png")
            return self.solve(screenshot_path)
        finally:
            if screenshot_path:
                try:
                    os.remove(screenshot_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
            if temp_dir:
                # Remove any Playwright side files as well, without ever touching
                # the caller-provided temp root.
                shutil.rmtree(temp_dir, ignore_errors=True)


_DEFAULT_SERVICE = None
_DEFAULT_SERVICE_LOCK = threading.Lock()


def _get_default_service():
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        with _DEFAULT_SERVICE_LOCK:
            if _DEFAULT_SERVICE is None:
                _DEFAULT_SERVICE = CaptchaService()
    return _DEFAULT_SERVICE


def solve_captcha_api(image_bytes=None):
    """Legacy entry point that now delegates exclusively to the local model."""
    return _get_default_service().solve(image_bytes)
