"""Run regression tests without opening the production database."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="mahanbot-tests-") as temp:
        database.DB_PATH = str(Path(temp) / "test.db")
        os.environ["MAHANBOT_SAVED_PAGES_DIR"] = str(Path(temp) / "saved_pages")
        os.environ["MAHANBOT_PROFILE_ROOT"] = str(Path(temp) / "chrome_profiles")
        os.environ["MAHANBOT_PREVIEW_DIR"] = str(Path(temp) / "previews")
        os.environ["MAHANBOT_UPDATE_TARGET_DIR"] = str(Path(temp) / "update_target")
        os.environ["MAHAN_OTP_RELAY_DISABLED"] = "1"
        os.environ.setdefault("MAHANBOT_ADMIN_PASSWORD", "local-test-only-password")
        database.DBHandler._settings_cache.clear()
        import requests
        # Network behavior is supplied by each test's mocks. Never contact a running
        # production server or the Android relay, including from background threads.
        with patch("requests.sessions.Session.request", side_effect=requests.ConnectionError("Network disabled in tests")):
            suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
            result = unittest.TextTestRunner(verbosity=2).run(suite)
        sys.exit(not result.wasSuccessful())
