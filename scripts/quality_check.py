from __future__ import annotations

import compileall
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_compile_check() -> bool:
    targets = [
        ROOT / "browser_actions.py",
        ROOT / "event_logger.py",
        ROOT / "bot_core.py",
        ROOT / "bot_register.py",
        ROOT / "bot_select.py",
        ROOT / "bot_status.py",
        ROOT / "browser_launcher.py",
        ROOT / "database.py",
        ROOT / "server.py",
    ]
    ok = True
    for path in targets:
        if not path.exists():
            print(f"MISSING: {path.relative_to(ROOT)}")
            ok = False
            continue
        compiled = compileall.compile_file(str(path), quiet=1, force=True)
        print(f"{'OK' if compiled else 'FAILED'}: {path.relative_to(ROOT)}")
        ok = compiled and ok
    return ok


def run_unit_tests() -> bool:
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return result.wasSuccessful()


def main() -> int:
    compile_ok = run_compile_check()
    tests_ok = run_unit_tests()
    return 0 if compile_ok and tests_ok else 1


if __name__ == "__main__":
    sys.exit(main())
