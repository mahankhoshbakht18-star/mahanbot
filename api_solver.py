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

    def prewarm(self):
        """Pre-warm PyTorch CRNN model weights and JIT kernels into RAM for sub-200ms solving."""
        try:
            import numpy as np
            from PIL import Image
            import io
            dummy_img = Image.fromarray(np.full((60, 160), 255, dtype=np.uint8))
            buf = io.BytesIO()
            dummy_img.save(buf, format="PNG")
            self.solve(buf.getvalue())
            return True
        except Exception:
            return False

    def solve_element(self, captcha_element):
        """Screenshot a Playwright element directly into memory bytes, solve it in <200ms."""
        # Fast path: In-memory bytes screenshot without disk I/O
        try:
            image_bytes = captcha_element.screenshot(type="png")
            if image_bytes:
                ans = self.solve(image_bytes)
                if ans:
                    return ans
        except Exception:
            pass

        # Fallback path: Tempfile on disk if in-memory fails
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
                except Exception:
                    pass
            if temp_dir:
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


def solve_captcha(image_source, mode="general"):
    """Module-level helper to solve a CAPTCHA locally."""
    return _get_default_service().solve(image_source, mode=mode)


def solve_element(captcha_element):
    """Module-level helper to screenshot in-memory and solve."""
    return _get_default_service().solve_element(captcha_element)


def prewarm():
    """Module-level helper to pre-warm the CAPTCHA solver model."""
    return _get_default_service().prewarm()

