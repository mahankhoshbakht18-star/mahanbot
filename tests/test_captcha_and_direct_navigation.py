"""
tests/test_captcha_and_direct_navigation.py — Verification for:
1. Dual-Engine CAPTCHA Solver (ModelCaptchaSolver + ddddocr, CPU execution, image formats).
2. Direct Bank Selection Page Navigation (SelectBnkShb.aspx) and Session Cookie Retention.
"""
import io
import os
import sys
import unittest
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database import DBHandler, DEFAULT_CONFIG
from bot_select import LoanTypeAdapter, SELECT_BNK_SHB_URL, CHILD_SELECT_URL, TARGET_URL
from bot_select_captcha import ModelCaptchaSolver


class TestCaptchaAndDirectNavigation(unittest.TestCase):
    def setUp(self):
        # Create a synthetic test image with text "A1B2"
        img = Image.new("RGB", (120, 45), color=(255, 255, 255))
        d = ImageDraw.Draw(img)
        d.text((10, 10), "ABCD", fill=(0, 0, 0))
        self.test_img = img

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        self.test_bytes = buf.getvalue()

    def test_direct_bank_selection_urls(self):
        """Verify target_select_url opens SelectBnkShb.aspx directly, while entry_url is SelTasCase.aspx."""
        # Marriage loan
        adapter_marriage = LoanTypeAdapter("marriage")
        self.assertEqual(adapter_marriage.target_select_url, SELECT_BNK_SHB_URL)
        self.assertEqual(adapter_marriage.direct_bank_url, SELECT_BNK_SHB_URL)
        self.assertEqual(adapter_marriage.entry_url, TARGET_URL)
        self.assertIn("SelectBnkShb.aspx", adapter_marriage.target_select_url)

        # Child loan
        adapter_child = LoanTypeAdapter("childbearing")
        self.assertEqual(adapter_child.target_select_url, CHILD_SELECT_URL)
        self.assertEqual(adapter_child.direct_bank_url, CHILD_SELECT_URL)
        self.assertEqual(adapter_child.entry_url, TARGET_URL)
        self.assertIn("ChildSelectBnkShb.aspx", adapter_child.target_select_url)

    def test_cookie_retention_configuration(self):
        """Verify clear_cookies is False by default to maintain persistent login sessions."""
        self.assertFalse(DEFAULT_CONFIG.get("clear_cookies"))

    def test_dual_engine_captcha_solver_initialization(self):
        """Verify ModelCaptchaSolver loads cleanly on CPU without device mismatch."""
        solver = ModelCaptchaSolver(device="cpu")
        self.assertEqual(str(solver.device), "cpu")
        self.assertTrue(solver.is_loaded or solver._ddddocr is not None)

    def test_captcha_solver_with_bytes_and_pil(self):
        """Verify CAPTCHA solver handles both raw bytes and PIL Image without TypeError."""
        solver = ModelCaptchaSolver(device="cpu")
        # Solve with bytes
        res_bytes = solver.solve(self.test_bytes)
        self.assertIsInstance(res_bytes, str)
        self.assertTrue(len(res_bytes) > 0)

        # Solve with PIL Image directly
        res_img = solver.solve(self.test_img)
        self.assertIsInstance(res_img, str)
        self.assertTrue(len(res_img) > 0)


if __name__ == "__main__":
    unittest.main()
