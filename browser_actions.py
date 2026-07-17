from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence


@dataclass(frozen=True)
class InteractionConfig:
    """Configuration for reliable, visible browser interactions.

    The small delays are intended to give the page enough time to process focus,
    input and change events. They are not an anti-detection mechanism.
    """

    timeout_ms: int = 10_000
    typing_delay_ms: int = 70
    settle_delay_ms: int = 120
    js_fallback_enabled: bool = False

    @classmethod
    def from_env(cls) -> "InteractionConfig":
        return cls(
            timeout_ms=_env_int("MAHANBOT_ACTION_TIMEOUT_MS", 10_000, minimum=500),
            typing_delay_ms=_env_int("MAHANBOT_TYPING_DELAY_MS", 70, minimum=0, maximum=500),
            settle_delay_ms=_env_int("MAHANBOT_ACTION_SETTLE_MS", 120, minimum=0, maximum=2_000),
            js_fallback_enabled=_env_bool("MAHANBOT_JS_FILL_FALLBACK", False),
        )


def _env_int(name: str, default: int, *, minimum: int, maximum: Optional[int] = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class BrowserActions:
    """Shared Playwright actions used by all bot flows.

    Methods return a boolean instead of hiding failures. Existing callers can
    keep using ``force_fill`` while newer code should prefer ``fill_text``.
    """

    _config = InteractionConfig.from_env()

    @classmethod
    def configure(cls, config: InteractionConfig) -> None:
        cls._config = config

    @staticmethod
    def show_log_on_page(page: Any, text: Any, color: str = "blue") -> bool:
        if page is None:
            return False
        safe_color = color if color in {"blue", "green", "red", "orange", "purple", "black"} else "blue"
        try:
            page.evaluate(
                """
                ({ text, color }) => {
                    let box = document.getElementById('bot-log');
                    if (!box) {
                        box = document.createElement('div');
                        box.id = 'bot-log';
                        box.style.cssText = [
                            'position:fixed', 'top:10px', 'left:10px',
                            'z-index:999999', 'padding:8px', 'border-radius:5px',
                            'font-family:Tahoma,sans-serif', 'font-weight:bold',
                            'font-size:12px', 'background:rgba(255,255,255,0.95)',
                            'box-shadow:0 2px 10px rgba(0,0,0,0.2)', 'direction:rtl'
                        ].join(';');
                        document.body.appendChild(box);
                    }
                    box.textContent = String(text ?? '');
                    box.style.borderRight = `5px solid ${color}`;
                    box.style.color = color;
                }
                """,
                {"text": str(text), "color": safe_color},
            )
            return True
        except Exception:
            return False

    @classmethod
    def resolve_locator(
        cls,
        page: Any,
        selectors: Sequence[str] | str,
        *,
        require_visible: bool = True,
    ) -> Optional[Any]:
        if page is None:
            return None
        selector_list: Iterable[str] = [selectors] if isinstance(selectors, str) else selectors
        for selector in selector_list:
            try:
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                if require_visible and not locator.is_visible():
                    continue
                return locator
            except Exception:
                continue
        return None

    @classmethod
    def fill_text(
        cls,
        locator: Any,
        text: Any,
        *,
        config: Optional[InteractionConfig] = None,
        allow_js_fallback: Optional[bool] = None,
        blur_after: bool = True,
    ) -> bool:
        """Fill an editable, visible field and verify the resulting value."""

        if locator is None or text is None:
            return False
        value = str(text)
        cfg = config or cls._config
        js_fallback = cfg.js_fallback_enabled if allow_js_fallback is None else allow_js_fallback

        try:
            locator.wait_for(state="visible", timeout=cfg.timeout_ms)
            if not locator.is_visible() or not locator.is_enabled():
                return False
            try:
                if not locator.is_editable():
                    return False
            except Exception:
                # Older Playwright versions may not expose is_editable.
                if locator.get_attribute("readonly") is not None:
                    return False

            locator.scroll_into_view_if_needed(timeout=cfg.timeout_ms)
            locator.click(timeout=cfg.timeout_ms)

            try:
                locator.press("Control+A")
                locator.press("Backspace")
                locator.type(value, delay=cfg.typing_delay_ms)
            except Exception:
                locator.fill(value, timeout=cfg.timeout_ms)

            cls._settle(locator, cfg.settle_delay_ms)
            if cls._input_value(locator) != value:
                locator.fill(value, timeout=cfg.timeout_ms)
                cls._settle(locator, cfg.settle_delay_ms)

            if cls._input_value(locator) != value and js_fallback:
                locator.evaluate(
                    """
                    (element, nextValue) => {
                        element.value = nextValue;
                        element.dispatchEvent(new Event('input', { bubbles: true }));
                        element.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    """,
                    value,
                )
                cls._settle(locator, cfg.settle_delay_ms)

            success = cls._input_value(locator) == value
            if success and blur_after:
                try:
                    locator.press("Tab")
                except Exception:
                    locator.evaluate("element => element.blur()")
            return success
        except Exception:
            return False

    @classmethod
    def force_fill(cls, locator: Any, text: Any) -> bool:
        """Backward-compatible alias for legacy callers.

        JavaScript fallback remains opt-in through ``MAHANBOT_JS_FILL_FALLBACK``;
        hidden, disabled and read-only controls are never modified.
        """

        return cls.fill_text(locator, text)

    @classmethod
    def select_option(
        cls,
        locator: Any,
        *,
        value: Optional[str] = None,
        label: Optional[str] = None,
        config: Optional[InteractionConfig] = None,
    ) -> bool:
        if locator is None or (value is None and label is None):
            return False
        cfg = config or cls._config
        try:
            locator.wait_for(state="visible", timeout=cfg.timeout_ms)
            if not locator.is_visible() or not locator.is_enabled():
                return False
            locator.scroll_into_view_if_needed(timeout=cfg.timeout_ms)
            locator.click(timeout=cfg.timeout_ms)
            if value is not None:
                locator.select_option(value=str(value), timeout=cfg.timeout_ms)
                expected = str(value)
            else:
                locator.select_option(label=str(label), timeout=cfg.timeout_ms)
                expected = None
            cls._settle(locator, cfg.settle_delay_ms)
            return expected is None or cls._input_value(locator) == expected
        except Exception:
            return False

    @classmethod
    def click_when_ready(
        cls,
        locator: Any,
        *,
        config: Optional[InteractionConfig] = None,
    ) -> bool:
        if locator is None:
            return False
        cfg = config or cls._config
        try:
            locator.wait_for(state="visible", timeout=cfg.timeout_ms)
            if not locator.is_visible() or not locator.is_enabled():
                return False
            locator.scroll_into_view_if_needed(timeout=cfg.timeout_ms)
            locator.click(timeout=cfg.timeout_ms)
            cls._settle(locator, cfg.settle_delay_ms)
            return True
        except Exception:
            return False

    @classmethod
    def fill_dates(cls, page: Any, data: dict[str, Any]) -> bool:
        """Fill birth and marriage date controls when they are present."""

        operations: list[bool] = []
        groups = (
            (
                "birth",
                "#ctl00_ContentPlaceHolder1_tbBrYear",
                "#ctl00_ContentPlaceHolder1_ddlBrMonth",
                "#ctl00_ContentPlaceHolder1_ddlBrDay",
            ),
            (
                "marriage",
                "#ctl00_ContentPlaceHolder1_tbMarrYear",
                "#ctl00_ContentPlaceHolder1_ddlMarryMonth",
                "#ctl00_ContentPlaceHolder1_ddlMarryDay",
            ),
        )
        for prefix, year_selector, month_selector, day_selector in groups:
            year = data.get(f"{prefix}_year") or data.get(f"{prefix}_y")
            month = data.get(f"{prefix}_month") or data.get(f"{prefix}_m")
            day = data.get(f"{prefix}_day") or data.get(f"{prefix}_d")
            if not year:
                continue
            year_locator = cls.resolve_locator(page, year_selector)
            month_locator = cls.resolve_locator(page, month_selector)
            day_locator = cls.resolve_locator(page, day_selector)
            operations.append(cls.fill_text(year_locator, year))
            if month:
                operations.append(cls.select_option(month_locator, value=str(month).zfill(2)))
            if day:
                operations.append(cls.select_option(day_locator, value=str(day).zfill(2)))
        return bool(operations) and all(operations)

    @classmethod
    def select_state_bank(cls, page: Any, data: dict[str, Any]) -> bool:
        state_name = str(data.get("state") or "").strip()
        if not state_name:
            return False
        locator = cls.resolve_locator(page, "#ctl00_ContentPlaceHolder1_ddlState")
        if locator is None:
            return False
        try:
            if cls._input_value(locator) not in {"", "0"}:
                return True
        except Exception:
            pass
        return cls.select_option(locator, label=state_name)

    @staticmethod
    def _input_value(locator: Any) -> str:
        try:
            return str(locator.input_value() or "")
        except Exception:
            return ""

    @staticmethod
    def _settle(locator: Any, milliseconds: int) -> None:
        if milliseconds <= 0:
            return
        try:
            page = locator.page
            page.wait_for_timeout(milliseconds)
        except Exception:
            time.sleep(milliseconds / 1000)
