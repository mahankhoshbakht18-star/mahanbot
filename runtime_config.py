from __future__ import annotations

import os
import secrets
from typing import Dict, List, Optional


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(
    name: str,
    default: int,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def configured_api_key() -> str:
    return str(os.getenv("MAHANBOT_API_KEY") or "").strip()


def dev_mode_enabled() -> bool:
    return env_bool("MAHANBOT_DEV_MODE", False)


def api_key_is_valid(provided: Optional[str]) -> bool:
    expected = configured_api_key()
    if not expected:
        return dev_mode_enabled()
    return bool(provided) and secrets.compare_digest(str(provided), expected)


def api_headers() -> Dict[str, str]:
    key = configured_api_key()
    return {"X-API-KEY": key} if key else {}


def cors_origins() -> List[str]:
    raw = str(os.getenv("MAHANBOT_CORS_ORIGINS") or "").strip()
    if raw:
        values = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
        return list(dict.fromkeys(values))
    return ["http://127.0.0.1:8000", "http://localhost:8000"]


def legacy_captcha_model_enabled() -> bool:
    """Allow the existing local model only when explicitly enabled.

    WAF/firewall challenges are never covered by this switch and must remain
    operator-driven.
    """

    return env_bool("MAHANBOT_ENABLE_LEGACY_CAPTCHA_MODEL", False)


def mask_secret(value: object, visible_suffix: int = 2) -> str:
    raw = str(value or "")
    if not raw:
        return "***"
    visible_suffix = max(0, min(int(visible_suffix), len(raw)))
    suffix = raw[-visible_suffix:] if visible_suffix else ""
    return "*" * max(4, len(raw) - visible_suffix) + suffix
