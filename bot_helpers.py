"""Shared helper functions used across bot modules.

This module eliminates code duplication by providing single implementations
of commonly needed utilities that were previously copied into each bot file.
"""

import time


def sleep_with_stop(stop_event, seconds: float, step: float = 0.2) -> bool:
    """Sleep for *seconds* while periodically checking *stop_event*.

    Returns ``True`` if *stop_event* was set during the sleep, ``False`` otherwise.
    """
    if seconds <= 0:
        return stop_event.is_set()
    end = time.time() + seconds
    while time.time() < end:
        if stop_event.is_set():
            return True
        time.sleep(min(step, end - time.time()))
    return stop_event.is_set()


CAPTCHA_FAILURE_PHRASES = [
    "کد امنیتی اشتباه",
    "کد امنیتی صحیح",
    "کد امنیتی نادرست",
    "security code",
]


def detect_captcha_failure(page) -> bool:
    """Return ``True`` if the page content indicates a CAPTCHA validation error."""
    try:
        content = page.content()
    except Exception:
        return False
    low = content.lower()
    return any(phrase.lower() in low for phrase in CAPTCHA_FAILURE_PHRASES)
