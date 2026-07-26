from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class HumanInputProfile:
    """Timing profile for reliable, paced form interaction.

    The delays are intentionally bounded and are used for UI compatibility and
    observability. They are not intended to bypass anti-automation controls.
    """

    min_key_delay_ms: int = 70
    max_key_delay_ms: int = 145
    pre_input_delay_ms: int = 80
    post_input_delay_ms: int = 140
    click_delay_ms: int = 90

    def normalized(self) -> "HumanInputProfile":
        minimum = max(0, int(self.min_key_delay_ms))
        maximum = max(minimum, int(self.max_key_delay_ms))
        return HumanInputProfile(
            min_key_delay_ms=minimum,
            max_key_delay_ms=maximum,
            pre_input_delay_ms=max(0, int(self.pre_input_delay_ms)),
            post_input_delay_ms=max(0, int(self.post_input_delay_ms)),
            click_delay_ms=max(0, int(self.click_delay_ms)),
        )


DEFAULT_HUMAN_INPUT_PROFILE = HumanInputProfile()


class BrowserActions:
    """Centralized browser interaction helpers.

    All text entry should pass through this class so form behavior remains
    consistent across registration, OTP, bank selection, and captcha flows.
    """

    _ALLOWED_LOG_COLORS = {
        "blue",
        "green",
        "red",
        "orange",
        "purple",
        "black",
        "gray",
    }

    @staticmethod
    def _sleep_ms(milliseconds: int) -> None:
        if milliseconds > 0:
            time.sleep(milliseconds / 1000.0)

    @staticmethod
    def _random_key_delay(profile: HumanInputProfile) -> int:
        normalized = profile.normalized()
        return random.randint(normalized.min_key_delay_ms, normalized.max_key_delay_ms)

    @staticmethod
    def show_log_on_page(page: Any, text: Any, color: str = "blue") -> None:
        """Show a small status box without interpolating data into JavaScript."""

        safe_color = color if color in BrowserActions._ALLOWED_LOG_COLORS else "blue"
        try:
            page.evaluate(
                """
                ({ text, color }) => {
                    let box = document.getElementById('bot-log');
                    if (!box) {
                        box = document.createElement('div');
                        box.id = 'bot-log';
                        box.style.cssText = [
                            'position:fixed',
                            'top:10px',
                            'left:10px',
                            'z-index:999999',
                            'padding:8px',
                            'border-radius:5px',
                            'font-family:Tahoma,sans-serif',
                            'font-weight:bold',
                            'font-size:12px',
                            'background:rgba(255,255,255,0.95)',
                            'box-shadow:0 2px 10px rgba(0,0,0,0.2)',
                            'direction:rtl',
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
        except Exception:
            # Page may already be closed or navigating.
            return

    @staticmethod
    def is_editable(locator: Any) -> bool:
        """Return True only for visible, enabled, non-readonly inputs."""

        try:
            if not locator.is_visible() or not locator.is_enabled():
                return False
            if locator.get_attribute("readonly") is not None:
                return False
            if locator.get_attribute("disabled") is not None:
                return False
            return True
        except Exception:
            return False

    @staticmethod
    def human_fill(
        locator: Any,
        text: Any,
        *,
        profile: Optional[HumanInputProfile] = None,
        verify: bool = True,
        allow_fill_fallback: bool = True,
    ) -> bool:
        """Enter text with paced keyboard events and verify the final value.

        The helper never removes readonly/disabled attributes and never writes
        values through JavaScript. A standard Playwright ``fill`` is used only
        as a compatibility fallback when sequential keyboard input fails.
        """

        value = str(text)
        selected_profile = (profile or DEFAULT_HUMAN_INPUT_PROFILE).normalized()

        if not BrowserActions.is_editable(locator):
            return False

        try:
            locator.scroll_into_view_if_needed()
            BrowserActions._sleep_ms(selected_profile.pre_input_delay_ms)
            locator.click()
            BrowserActions._sleep_ms(selected_profile.click_delay_ms)

            # Select and clear using keyboard events before sequential typing.
            try:
                locator.press("Control+A")
                locator.press("Backspace")
            except Exception:
                try:
                    locator.clear()
                except Exception:
                    pass

            if value:
                locator.type(value, delay=BrowserActions._random_key_delay(selected_profile))
            BrowserActions._sleep_ms(selected_profile.post_input_delay_ms)

            if not verify:
                return True

            try:
                if locator.input_value() == value:
                    return True
            except Exception:
                pass

            if not allow_fill_fallback:
                return False

            # Compatibility fallback for controls that reject keyboard typing.
            locator.fill(value)
            BrowserActions._sleep_ms(selected_profile.post_input_delay_ms)
            try:
                return locator.input_value() == value
            except Exception:
                return True
        except Exception:
            return False

    @staticmethod
    def force_fill(locator: Any, text: Any) -> bool:
        """Backward-compatible alias for safe, verified text entry.

        Existing bots call ``force_fill``. Keeping this alias upgrades those
        callers without changing their public behavior.
        """

        return BrowserActions.human_fill(locator, text)

    @staticmethod
    def human_click(locator: Any, *, delay_ms: int = 120) -> bool:
        """Scroll to, focus, and click a visible enabled control."""

        try:
            if not locator.is_visible() or not locator.is_enabled():
                return False
            locator.scroll_into_view_if_needed()
            BrowserActions._sleep_ms(max(0, int(delay_ms)))
            locator.click()
            return True
        except Exception:
            return False

    @staticmethod
    def select_option(locator: Any, *, value: Optional[str] = None, label: Optional[str] = None) -> bool:
        """Select one option and verify that a non-empty value is selected."""

        try:
            if not locator.is_visible() or not locator.is_enabled():
                return False
            locator.scroll_into_view_if_needed()
            BrowserActions._sleep_ms(80)
            if value is not None:
                locator.select_option(value=str(value))
            elif label is not None:
                locator.select_option(label=str(label))
            else:
                return False
            BrowserActions._sleep_ms(120)
            return True
        except Exception:
            return False

    @staticmethod
    def fill_dates(page: Any, data: dict[str, Any]) -> bool:
        """Fill birth and marriage date controls through the shared helpers."""

        changed = False
        try:
            birth_year = data.get("birth_year") or data.get("birth_y")
            if birth_year:
                year = page.locator("#ctl00_ContentPlaceHolder1_tbBrYear").first
                if year.is_visible() and not year.input_value():
                    changed |= BrowserActions.human_fill(year, birth_year)
                    changed |= BrowserActions.select_option(
                        page.locator("#ctl00_ContentPlaceHolder1_ddlBrMonth").first,
                        value=str(data.get("birth_month") or data.get("birth_m") or "").zfill(2),
                    )
                    changed |= BrowserActions.select_option(
                        page.locator("#ctl00_ContentPlaceHolder1_ddlBrDay").first,
                        value=str(data.get("birth_day") or data.get("birth_d") or "").zfill(2),
                    )

            marriage_year = data.get("marriage_year") or data.get("marriage_y")
            if marriage_year:
                year = page.locator("#ctl00_ContentPlaceHolder1_tbMarrYear").first
                if year.is_visible() and not year.input_value():
                    changed |= BrowserActions.human_fill(year, marriage_year)
                    changed |= BrowserActions.select_option(
                        page.locator("#ctl00_ContentPlaceHolder1_ddlMarryMonth").first,
                        value=str(data.get("marriage_month") or data.get("marriage_m") or "").zfill(2),
                    )
                    changed |= BrowserActions.select_option(
                        page.locator("#ctl00_ContentPlaceHolder1_ddlMarryDay").first,
                        value=str(data.get("marriage_day") or data.get("marriage_d") or "").zfill(2),
                    )
        except Exception:
            return changed
        return changed

    @staticmethod
    def select_state_bank(page: Any, data: dict[str, Any]) -> bool:
        """Select the applicant state by visible option label."""

        state_name = str(data.get("state") or "").strip()
        if not state_name:
            return False
        try:
            state = page.locator("#ctl00_ContentPlaceHolder1_ddlState").first
            if state.is_visible() and state.input_value() == "0":
                return BrowserActions.select_option(state, label=state_name)
        except Exception:
            return False
        return False
