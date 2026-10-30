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
            res = self.solver.solve(image_source)
            if res:
                return res
            # Direct dual-engine fallback if solver produced no valid code
            if hasattr(self.solver, "_ddddocr") and self.solver._ddddocr is not None:
                img_bytes = None
                if isinstance(image_source, (bytes, bytearray, memoryview)):
                    img_bytes = bytes(image_source)
                elif isinstance(image_source, (str, os.PathLike)) and os.path.exists(image_source):
                    with open(image_source, "rb") as f:
                        img_bytes = f.read()
                if img_bytes:
                    raw_ocr = str(self.solver._ddddocr.classification(img_bytes) or "").strip().upper()
                    clean = "".join(c for c in raw_ocr if c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                    if 4 <= len(clean) <= 8:
                        return clean
            return res
        except Exception as exc:
            if hasattr(self.solver, "last_error"):
                self.solver.last_error = str(exc)
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
        if captcha_element is None:
            return None

        # Verify element exists and is visible before screenshotting to avoid 5s timeout
        try:
            if hasattr(captcha_element, "count") and captcha_element.count() == 0:
                return None
            if hasattr(captcha_element, "is_visible") and not captcha_element.is_visible():
                return None
        except Exception:
            pass

        # Prefer inner <img> tag if captcha_element is a container wrapper
        target = captcha_element
        try:
            if hasattr(captcha_element, "locator"):
                img_loc = captcha_element.locator("img").first
                if img_loc.count() > 0 and img_loc.is_visible():
                    target = img_loc
        except Exception:
            pass

        # Fast path: In-memory bytes screenshot without disk I/O
        try:
            image_bytes = target.screenshot(type="png", timeout=2500)
            if image_bytes and len(image_bytes) >= 150:
                return self.solve(image_bytes)
        except Exception as exc:
            if hasattr(self.solver, "last_error"):
                self.solver.last_error = f"Screenshot error: {exc}"

        # Fallback path: Tempfile on disk if in-memory fails
        temp_dir = None
        screenshot_path = None
        try:
            if self.temp_root is not None:
                os.makedirs(self.temp_root, exist_ok=True)
            temp_dir = tempfile.mkdtemp(prefix="mahanbot-captcha-", dir=self.temp_root)
            screenshot_path = os.path.join(temp_dir, TEMP_CAPTCHA_FILENAME)
            target.screenshot(path=screenshot_path, type="png", timeout=2500)
            if os.path.isfile(screenshot_path) and os.path.getsize(screenshot_path) >= 150:
                ans = self.solve(screenshot_path)
                if ans:
                    return ans
        except Exception as exc:
            if hasattr(self.solver, "last_error"):
                self.solver.last_error = f"Disk screenshot error: {exc}"
        finally:
            if screenshot_path:
                try:
                    os.remove(screenshot_path)
                except Exception:
                    pass
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

        return None


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

