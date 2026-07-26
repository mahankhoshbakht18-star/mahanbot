from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence


_VALID_INTERACTION_MODES = {"human", "fast"}


def _normalize_mode(value: Any) -> str:
    mode = str(value or "human").strip().lower()
    return mode if mode in _VALID_INTERACTION_MODES else "human"


def _bounded_int(value: Any, default: int, *, minimum: int, maximum: Optional[int] = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


@dataclass(frozen=True)
class InteractionConfig:
    """Configuration for reliable, visible browser interactions.

    ``human`` enters text character by character with a configurable delay.
    ``fast`` uses Playwright's immediate ``fill`` operation. Both modes still
    verify the final value and keep the same visibility/editability checks.
    """

    mode: str = "human"
    timeout_ms: int = 10_000
    typing_delay_ms: int = 70
    settle_delay_ms: int = 120
    js_fallback_enabled: bool = False

    @classmethod
    def from_env(cls) -> "InteractionConfig":
        mode = _normalize_mode(os.getenv("MAHANBOT_INTERACTION_MODE", "human"))
        legacy_delay = _env_int("MAHANBOT_TYPING_DELAY_MS", 70, minimum=0, maximum=500)
        human_delay = _env_int("MAHANBOT_HUMAN_TYPING_DELAY_MS", legacy_delay, minimum=0, maximum=500)
        fast_delay = _env_int("MAHANBOT_FAST_TYPING_DELAY_MS", 0, minimum=0, maximum=100)
        human_settle = _env_int("MAHANBOT_HUMAN_SETTLE_MS", 120, minimum=0, maximum=2_000)
        fast_settle = _env_int("MAHANBOT_FAST_SETTLE_MS", 35, minimum=0, maximum=2_000)
        return cls(
            mode=mode,
            timeout_ms=_env_int("MAHANBOT_ACTION_TIMEOUT_MS", 10_000, minimum=500),
            typing_delay_ms=human_delay if mode == "human" else fast_delay,
            settle_delay_ms=human_settle if mode == "human" else fast_settle,
            js_fallback_enabled=_env_bool("MAHANBOT_JS_FILL_FALLBACK", False),
        )

    @classmethod
    def from_settings(cls, settings: Optional[Mapping[str, Any]]) -> "InteractionConfig":
        base = cls.from_env()
        data = settings or {}
        mode = _normalize_mode(data.get("interaction_mode", base.mode))
        if mode == "fast":
            typing_delay = _bounded_int(
                data.get("fast_typing_delay_ms", base.typing_delay_ms if base.mode == "fast" else 0),
                0,
                minimum=0,
                maximum=100,
            )
            settle_delay = _bounded_int(
                data.get("fast_settle_delay_ms", 35),
                35,
                minimum=0,
                maximum=2_000,
            )
        else:
            typing_delay = _bounded_int(
                data.get("human_typing_delay_ms", base.typing_delay_ms if base.mode == "human" else 70),
                70,
                minimum=0,
                maximum=500,
            )
            settle_delay = _bounded_int(
                data.get("human_settle_delay_ms", 120),
                120,
                minimum=0,
                maximum=2_000,
            )
        return cls(
            mode=mode,
            timeout_ms=_bounded_int(data.get("action_timeout_ms", base.timeout_ms), base.timeout_ms, minimum=500),
            typing_delay_ms=typing_delay,
            settle_delay_ms=settle_delay,
            js_fallback_enabled=bool(data.get("js_fill_fallback", base.js_fallback_enabled)),
        )


def _env_int(name: str, default: int, *, minimum: int, maximum: Optional[int] = None) -> int:
    return _bounded_int(os.getenv(name, str(default)), default, minimum=minimum, maximum=maximum)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class BrowserActions:
    """Shared Playwright actions used by all bot flows."""

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
        """Fill an editable field according to the selected interaction mode."""

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
                if locator.get_attribute("readonly") is not None:
                    return False

            locator.scroll_into_view_if_needed(timeout=cfg.timeout_ms)
            locator.click(timeout=cfg.timeout_ms)
            locator.press("Control+A")
            locator.press("Backspace")

            try:
                if cfg.mode == "fast":
                    locator.fill(value, timeout=cfg.timeout_ms)
                else:
                    locator.type(value, delay=cfg.typing_delay_ms)
            except Exception:
                # Deterministic fallback: use the opposite Playwright primitive.
                if cfg.mode == "fast":
                    locator.type(value, delay=0)
                else:
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
    def force_fill(
        cls,
        locator: Any,
        text: Any,
        *,
        config: Optional[InteractionConfig] = None,
        blur_after: bool = True,
    ) -> bool:
        """Backward-compatible alias for legacy callers."""

        return cls.fill_text(locator, text, config=config, blur_after=blur_after)

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
    def fill_dates(
        cls,
        page: Any,
        data: dict[str, Any],
        *,
        config: Optional[InteractionConfig] = None,
    ) -> bool:
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
            operations.append(cls.fill_text(year_locator, year, config=config))
            if month:
                operations.append(cls.select_option(month_locator, value=str(month).zfill(2), config=config))
            if day:
                operations.append(cls.select_option(day_locator, value=str(day).zfill(2), config=config))
        return bool(operations) and all(operations)

    @classmethod
    def select_state_bank(
        cls,
        page: Any,
        data: dict[str, Any],
        *,
        config: Optional[InteractionConfig] = None,
    ) -> bool:
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
        return cls.select_option(locator, label=state_name, config=config)

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
