from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_GIT_BLOBS = {
    "captcha_service.py": "60b4672b53ab4f07d69b86541ce29a0df01f8788",
    "my_captcha_model.pth": "57a2f6fe7bc48036e765706ba8f081cfbbf36f62",
}


def git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def main() -> int:
    failures: list[str] = []
    for relative, expected in EXPECTED_GIT_BLOBS.items():
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing: {relative}")
            continue
        actual = git_blob_sha(path)
        if actual != expected:
            failures.append(f"changed: {relative} expected={expected} actual={actual}")

    if failures:
        print("Frozen captcha boundary check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Frozen captcha boundary is unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
