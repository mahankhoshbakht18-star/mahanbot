from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".captcha-freeze.sha256"


def main() -> int:
    failures: list[str] = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        expected, relative = line.split(maxsplit=1)
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing: {relative}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            failures.append(f"changed: {relative}")
    if failures:
        print("CAPTCHA freeze guard failed: " + ", ".join(failures))
        return 1
    print("CAPTCHA freeze guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
