from __future__ import annotations

from scripts import apply_phase1_4_refactor as migration


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


migration.replace_once = _compatible_replace_once


if __name__ == "__main__":
    migration.main()
