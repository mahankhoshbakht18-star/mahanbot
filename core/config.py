"""
core/config.py — Environment & ``.env`` configuration loader.

A dependency-free loader so secrets (server passwords, API keys, tokens)
live in a single ``.env`` file instead of being hardcoded in scripts.
"""
import os
from pathlib import Path
from typing import Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = BASE_DIR / ".env"

_TRUE_VALUES = {"1", "true", "yes", "on"}


def load_dotenv(path=None, override: bool = False) -> int:
    """Parse ``KEY=VALUE`` lines from *path* into ``os.environ``.

    Lines starting with ``#`` are comments. Values may be quoted with
    single or double quotes. Existing environment variables win unless
    *override* is true. Returns the number of variables loaded.
    """
    env_path = Path(path) if path else Path(
        os.getenv("MAHANBOT_ENV_FILE", DEFAULT_ENV_PATH)
    )
    if path is None:
        load_dotenv(BASE_DIR / ".relay.private.env", override=False)
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError:
        return 0

    loaded = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.getenv(name, default)


def get_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def get_int_env(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


def get_csv_env(name: str, default: str = "") -> list:
    raw = os.getenv(name, default) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def snapshot_public_config() -> Dict[str, object]:
    """Non-secret configuration summary safe to expose via logs/health."""
    return {
        "cors_origins": get_csv_env("MAHANBOT_CORS_ORIGINS"),
        "require_api_key": get_bool_env("MAHANBOT_REQUIRE_API_KEY", False),
        "api_key_configured": bool(os.getenv("MAHANBOT_API_KEY")),
        "otp_secret_configured": bool(os.getenv("MAHANBOT_OTP_SECRET")),
        "cookie_secure": get_bool_env("MAHANBOT_COOKIE_SECURE", False),
    }
