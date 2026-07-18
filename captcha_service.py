from __future__ import annotations

from typing import Any, Optional, Tuple


class CaptchaService:
    """Manual-only CAPTCHA boundary for live workflows.

    The live banking workflow must pause for the operator to enter CAPTCHA in
    the visible browser. No OCR model, external solver, or firewall solver is
    loaded by this service.
    """

    def __init__(self, model: Any = None, ocr_firewall: Any = None) -> None:
        self._model = None
        self._ocr_firewall = None

    def solve(self, image_bytes: Any, mode: str = "general") -> Optional[str]:
        # Returning None tells the bots to switch to their manual operator flow.
        return None


def load_captcha_resources() -> Tuple[None, None]:
    """Compatibility helper retained for server startup."""
    return None, None
