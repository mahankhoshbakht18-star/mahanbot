from __future__ import annotations

import apply_phase1_4_refactor as migration


_original_replace_once = migration.replace_once


def _compatible_replace_once(path: str, old: str, new: str) -> bool:
    otp_line = '    code = str(req.code or "").strip()\n'
    if path == "server.py" and old == otp_line:
        text = migration._read(path)
        if old not in text:
            if new in text:
                return False
            raise RuntimeError(f"{path}: OTP normalization lines were not found")
        count = text.count(old)
        if count != 2:
            raise RuntimeError(f"{path}: expected two OTP normalization lines, found {count}")
        migration._write(path, text.replace(old, new))
        return True
    return _original_replace_once(path, old, new)


def _phase1_4_already_applied() -> bool:
    required_markers = {
        "database.py": "def _resolve_database_path() -> str:",
        "server.py": "api_key_is_valid",
        "bot_select.py": "def _wait_for_manual_captcha",
        "runtime_config.py": "def configured_api_key",
    }
    for path, marker in required_markers.items():
        try:
            if marker not in migration._read(path):
                return False
        except (OSError, UnicodeError):
            return False
    try:
        launcher = migration._read("browser_launcher.py")
        captcha_service = migration._read("captcha_service.py")
    except (OSError, UnicodeError):
        return False
    return (
        "--disable-blink-features=AutomationControlled" not in launcher
        and "Manual-only CAPTCHA boundary" in captcha_service
    )


def _configure_partial_migration_compatibility() -> None:
    """Skip only patches already present in this mixed validation tree."""
    try:
        launcher = migration._read("browser_launcher.py")
    except (OSError, UnicodeError):
        launcher = ""
    if "--disable-blink-features=AutomationControlled" not in launcher:
        migration.patch_browser_launcher = lambda: print(
            "browser_launcher.py is already migrated; skipping browser patch."
        )

    try:
        captcha_service = migration._read("captcha_service.py")
    except (OSError, UnicodeError):
        captcha_service = ""
    if "Manual-only CAPTCHA boundary" in captcha_service:
        migration.patch_captcha_service = lambda: print(
            "captcha_service.py already enforces the manual live boundary; skipping legacy patch."
        )


migration.replace_once = _compatible_replace_once


if __name__ == "__main__":
    if _phase1_4_already_applied():
        print("Phase 1-4 refactor is already applied; validation succeeded.")
    else:
        _configure_partial_migration_compatibility()
        migration.main()
