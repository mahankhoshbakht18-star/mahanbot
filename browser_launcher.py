import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from playwright.sync_api import sync_playwright

ALLOWED_BROWSERS = {"chromium", "firefox", "webkit"}
DEFAULT_TIMEOUT_MS = 30000
DEFAULT_VIEWPORT = {"width": 1280, "height": 720}
PROFILE_ROOT = Path(os.getenv("MAHANBOT_PROFILE_ROOT", Path.cwd() / "browser_profiles")).resolve()
DEFAULT_HEALTHCHECK_URL = "http://127.0.0.1:8000/static/healthcheck.html"


class BrowserLaunchError(Exception):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


def get_default_browser_profile() -> Dict[str, Any]:
    return {
        "browser": "chromium",
        "headless": False,
        "slow_mo_ms": 0,
        "viewport": DEFAULT_VIEWPORT.copy(),
        "user_data_dir": None,
        "proxy": None,
        "timeout_ms": DEFAULT_TIMEOUT_MS,
    }


def _resolve_user_data_dir(user_data_dir: Optional[str]) -> Optional[str]:
    if not user_data_dir:
        return None
    candidate = Path(user_data_dir)
    if not candidate.is_absolute():
        candidate = PROFILE_ROOT / candidate
    resolved = candidate.resolve()
    if PROFILE_ROOT != resolved and PROFILE_ROOT not in resolved.parents:
        raise BrowserLaunchError(
            "invalid_user_data_dir",
            "user_data_dir must be within the profile root",
            {"profile_root": str(PROFILE_ROOT), "user_data_dir": str(resolved)},
        )
    resolved.mkdir(parents=True, exist_ok=True)
    return str(resolved)


def _normalize_viewport(viewport: Optional[Dict[str, Any]]) -> Dict[str, int]:
    if not isinstance(viewport, dict):
        return DEFAULT_VIEWPORT.copy()
    width = int(viewport.get("width", DEFAULT_VIEWPORT["width"]))
    height = int(viewport.get("height", DEFAULT_VIEWPORT["height"]))
    if width < 320 or height < 200:
        raise BrowserLaunchError(
            "invalid_viewport",
            "Viewport dimensions are too small",
            {"width": width, "height": height},
        )
    return {"width": width, "height": height}


def normalize_browser_profile(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = get_default_browser_profile()
    if profile:
        data.update({k: v for k, v in profile.items() if v is not None})

    browser = str(data.get("browser", "chromium")).lower().strip()
    if browser not in ALLOWED_BROWSERS:
        raise BrowserLaunchError(
            "invalid_browser",
            "Unsupported browser selection",
            {"allowed": sorted(ALLOWED_BROWSERS), "received": browser},
        )
    data["browser"] = browser

    data["headless"] = bool(data.get("headless", False))

    try:
        slow_mo_ms = int(data.get("slow_mo_ms", 0))
    except (TypeError, ValueError):
        raise BrowserLaunchError("invalid_slow_mo", "slow_mo_ms must be an integer")
    if slow_mo_ms < 0 or slow_mo_ms > 10000:
        raise BrowserLaunchError(
            "invalid_slow_mo",
            "slow_mo_ms out of range",
            {"min": 0, "max": 10000, "received": slow_mo_ms},
        )
    data["slow_mo_ms"] = slow_mo_ms

    data["viewport"] = _normalize_viewport(data.get("viewport"))

    try:
        timeout_ms = int(data.get("timeout_ms", DEFAULT_TIMEOUT_MS))
    except (TypeError, ValueError):
        raise BrowserLaunchError("invalid_timeout", "timeout_ms must be an integer")
    if timeout_ms < 1000 or timeout_ms > 120000:
        raise BrowserLaunchError(
            "invalid_timeout",
            "timeout_ms out of range",
            {"min": 1000, "max": 120000, "received": timeout_ms},
        )
    data["timeout_ms"] = timeout_ms

    proxy = data.get("proxy")
    data["proxy"] = proxy.strip() if isinstance(proxy, str) and proxy.strip() else None

    data["user_data_dir"] = _resolve_user_data_dir(data.get("user_data_dir"))
    return data


def merge_browser_profiles(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(base)
    if override:
        for key, value in override.items():
            if value is None:
                continue
            if key == "viewport" and isinstance(value, dict):
                viewport = dict(merged.get("viewport", {}))
                viewport.update(value)
                merged[key] = viewport
            else:
                merged[key] = value
    return normalize_browser_profile(merged)


def open_healthcheck_page(page, *, allowed_domains: Optional[Iterable[str]] = None, log_callback=None) -> bool:
    url = get_healthcheck_url()
    goto = getattr(page, "_original_goto", page.goto)
    try:
        ensure_allowed_url(url, allowed_domains=allowed_domains)
        goto(url, wait_until="load")
        return True
    except BrowserLaunchError as exc:
        if log_callback:
            try:
                log_callback("⚠️ بارگذاری صفحه داخلی سلامت مرورگر ناموفق بود.", "warning", meta=exc.details)
            except TypeError:
                log_callback("⚠️ بارگذاری صفحه داخلی سلامت مرورگر ناموفق بود.", "warning")
        return False
    except Exception as exc:
        if log_callback:
            try:
                log_callback(f"⚠️ خطا در بارگذاری صفحه سلامت: {exc}", "warning")
            except TypeError:
                log_callback(f"⚠️ خطا در بارگذاری صفحه سلامت: {exc}", "warning")
        return False


def ensure_allowed_url(url: str, allowed_domains: Optional[Iterable[str]] = None) -> None:
    return


def safe_goto(page, url: str, *, allowed_domains: Optional[Iterable[str]] = None, log_callback=None, **kwargs):
    return page.goto(url, **kwargs)


def launch_browser(profile: Dict[str, Any]) -> Tuple[Any, Any, Any, Any]:
    normalized = normalize_browser_profile(profile)
    playwright = None
    browser = None
    context = None
    page = None
    try:
        playwright = sync_playwright().start()
        browser_type = getattr(playwright, normalized["browser"])
        launch_options: Dict[str, Any] = {
            "headless": normalized["headless"],
            "slow_mo": normalized["slow_mo_ms"],
        }
        if normalized["browser"] == "chromium":
            launch_options.setdefault("args", []).append("--disable-blink-features=AutomationControlled")
        if normalized["proxy"]:
            launch_options["proxy"] = {"server": normalized["proxy"]}
        if normalized["user_data_dir"]:
            context = browser_type.launch_persistent_context(
                normalized["user_data_dir"],
                **launch_options,
                viewport=normalized["viewport"],
            )
        else:
            browser = browser_type.launch(**launch_options)
            context = browser.new_context(viewport=normalized["viewport"])
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(normalized["timeout_ms"])
        page.set_default_navigation_timeout(normalized["timeout_ms"])
        return playwright, browser, context, page
    except BrowserLaunchError:
        raise
    except Exception as exc:
        raise BrowserLaunchError("launch_failed", "Failed to launch browser", {"error": str(exc)})
    finally:
        if playwright is None and browser is None and context is None and page is None:
            return


def close_browser(playwright, browser, context, page) -> None:
    try:
        if page:
            page.close()
    except Exception:
        pass
    try:
        if context:
            context.close()
    except Exception:
        pass
    try:
        if browser:
            browser.close()
    except Exception:
        pass
    try:
        if playwright:
            playwright.stop()
    except Exception:
        pass
