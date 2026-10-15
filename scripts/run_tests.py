"""Run regression tests without opening the production database."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="mahanbot-tests-") as temp:
        database.DB_PATH = str(Path(temp) / "test.db")
        os.environ.setdefault("MAHANBOT_ADMIN_PASSWORD", "local-test-only-password")
        database.DBHandler._settings_cache.clear()
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        sys.exit(not result.wasSuccessful())
