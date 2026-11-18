"""
Thread-local Playwright Browser helpers for MAHANBOT.

JobQueue runs every job on an isolated thread. Playwright's sync API is
thread-affine, so a process-wide shared driver breaks the next job with
"Failed to launch browser". Pool state is therefore thread-local:

- One sync_playwright() driver per job thread
- Optional shared Chromium Browser within that same thread (non-persistent)
- Isolated BrowserContext per applicant/session
- Resource guards: abort font/media/images that are not CAPTCHA/ASP.NET essentials
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("mahanbot.browser_pool")

_TLS = threading.local()

# Block heavy assets; keep CAPTCHA / BotDetect / ASP.NET script+xhr+document.
_BLOCKED_RESOURCE_TYPES = {"font", "media"}
_CAPTCHA_URL_HINTS = (
    "captcha",
    "botdetect",
    "botdetectcaptcha",
    "bdc_",
    "challenge",
    "getimage",
    "get=image",
    "gdx",
)


def _tls_get(name: str, default=None):
    return getattr(_TLS, name, default)


def _tls_set(name: str, value) -> None:
    setattr(_TLS, name, value)


def invalidate_thread_browser(*, stop_playwright: bool = False) -> None:
    """Drop dead/corrupt thread-local browser (and optionally Playwright)."""
    browser = _tls_get("browser")
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass
    _tls_set("browser", None)
    _tls_set("launch_key", None)
    if stop_playwright:
        pw = _tls_get("playwright")
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass
        _tls_set("playwright", None)


def get_shared_playwright():
    """One sync_playwright() driver for the current job thread only."""
    existing = _tls_get("playwright")
    if existing is not None:
        return existing
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("playwright is not installed in this environment") from exc
    pw = sync_playwright().start()
    _tls_set("playwright", pw)
    return pw


def _launch_key(launch_options: Dict[str, Any]) -> str:
    channel = launch_options.get("channel") or ""
    headless = bool(launch_options.get("headless", True))
    proxy = ""
    p = launch_options.get("proxy") or {}
    if isinstance(p, dict):
        proxy = str(p.get("server") or "")
    return f"h={headless}|c={channel}|p={proxy}"


def _should_abort_request(request) -> bool:
    try:
        rtype = str(getattr(request, "resource_type", "") or "").lower()
        url = str(getattr(request, "url", "") or "").lower()
    except Exception:
        return False
    # Never touch CAPTCHA / BotDetect / same-origin CBI assets.
    if any(h in url for h in _CAPTCHA_URL_HINTS):
        return False
    if "ve.cbi.ir" in url:
        return False
    if rtype in _BLOCKED_RESOURCE_TYPES:
        return True
    if rtype == "image":
        if url.startswith("data:"):
            return False
        if any(url.endswith(ext) for ext in (".woff", ".woff2", ".ttf", ".otf", ".mp4", ".webm", ".mp3")):
            return True
        # Soft-block only clearly off-site decorative images
        if "localhost" not in url and "127.0.0.1" not in url:
            return True
    return False


def install_resource_guards(context: Any) -> None:
    """Attach route handler that aborts fonts/media and non-essential images."""
    if context is None:
        return
    if getattr(context, "_mahan_resource_guards", False):
        return

    def _handler(route):
        try:
            if _should_abort_request(route.request):
                return route.abort()
        except Exception:
            pass
        try:
            return route.continue_()
        except Exception:
            try:
                return route.abort()
            except Exception:
                return None

    try:
        context.route("**/*", _handler)
        setattr(context, "_mahan_resource_guards", True)
    except Exception as exc:
        logger.debug("resource guards not attached: %s", exc)


def get_shared_browser(playwright: Any, browser_type: Any, launch_options: Dict[str, Any]) -> Any:
    """
    Return a thread-local Chromium Browser for non-persistent contexts.
    Marks browser with _mahan_shared=True so callers can detect pool ownership.
    """
    key = _launch_key(launch_options)
    browser = _tls_get("browser")
    alive = False
    try:
        alive = browser is not None and getattr(browser, "is_connected", lambda: True)()
    except Exception:
        alive = False
    if alive and _tls_get("launch_key") == key:
        return browser

    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass
        _tls_set("browser", None)

    opts = dict(launch_options)
    opts.pop("user_data_dir", None)
    try:
        browser = browser_type.launch(**opts)
    except Exception:
        # Driver may be stuck after a prior cross-thread / closed-browser fault.
        invalidate_thread_browser(stop_playwright=True)
        playwright = get_shared_playwright()
        selected = getattr(browser_type, "name", None) or "chromium"
        browser_type = getattr(playwright, selected if selected in {"chromium", "firefox", "webkit"} else "chromium")
        browser = browser_type.launch(**opts)

    setattr(browser, "_mahan_shared", True)
    _tls_set("browser", browser)
    _tls_set("playwright", playwright)
    _tls_set("launch_key", key)
    logger.info("thread-local browser launched key=%s thread=%s", key, threading.current_thread().name)
    return browser


def acquire_context(
    playwright: Any,
    browser_type: Any,
    launch_options: Dict[str, Any],
    *,
    user_data_dir: Optional[str] = None,
    viewport: Optional[Dict[str, int]] = None,
) -> Tuple[Any, Any]:
    """
    Returns (browser_or_none, context).
    - persistent profile → (None, persistent_context)
    - otherwise → (thread_local_browser, new_context)
    """
    viewport = viewport or {"width": 900, "height": 620}
    if user_data_dir:
        context = browser_type.launch_persistent_context(
            user_data_dir,
            **launch_options,
            viewport=viewport,
        )
        install_resource_guards(context)
        setattr(context, "_mahan_shared_browser", False)
        return None, context

    browser = get_shared_browser(playwright, browser_type, launch_options)
    context = browser.new_context(viewport=viewport)
    install_resource_guards(context)
    setattr(context, "_mahan_shared_browser", True)
    return browser, context


def release_context(context: Any, page: Any = None, *, close_shared_browser: bool = False) -> None:
    """Close page/context; optionally tear down the thread-local Browser."""
    try:
        if page is not None:
            page.close()
    except Exception:
        pass
    try:
        if context is not None:
            context.close()
    except Exception:
        pass
    if close_shared_browser:
        shutdown_shared_browser()


def shutdown_shared_browser() -> None:
    """Close thread-local browser; keep Playwright driver for fast same-thread reopen."""
    invalidate_thread_browser(stop_playwright=False)


def shutdown_thread_pool() -> None:
    """Full teardown for end of job thread."""
    invalidate_thread_browser(stop_playwright=True)
