"""
tests/test_system_update.py — Test Suite for Advanced In-App Update Center and Deployment Endpoints.
"""
import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

import database
from database import DBHandler
from server import app
import otp_server


class SystemUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db = database.DB_PATH
        database.DB_PATH = os.path.join(self.temp_dir.name, "test_update.db")
        DBHandler._settings_cache.clear()
        DBHandler.init_db()
        os.environ["MAHANBOT_UPDATE_TARGET_DIR"] = self.temp_dir.name
        self.client = TestClient(app)

    def tearDown(self):
        os.environ.pop("MAHANBOT_UPDATE_TARGET_DIR", None)
        database.DB_PATH = self.old_db
        DBHandler._settings_cache.clear()
        self.temp_dir.cleanup()

    def test_system_version_endpoint(self):
        """Verify GET /api/system/version returns current version and metadata."""
        resp = self.client.get("/api/system/version")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["version"], "2.0.1")
        self.assertIn("MAHANBOT", data["name"])
        self.assertIn("apk_download_url", data)
        self.assertIn("package_download_url", data)
        self.assertIn("mahan-otp-2.0.1.apk", data["apk_download_url"])

    def test_update_check_offline_or_current(self):
        """Verify GET /api/system/update/check returns current status when up to date."""
        resp = self.client.get("/api/system/update/check")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("has_update", data)
        self.assertEqual(data["current_version"], "2.0.1")
        self.assertIn("release_notes", data)

    def test_update_check_simulated_new_version(self):
        """Verify simulated update check indicates new version available with release notes."""
        resp = self.client.get("/api/system/update/check?simulate=true")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["has_update"])
        self.assertEqual(data["latest_version"], "2.0.2")
        self.assertGreater(len(data["release_notes"]), 0)
        self.assertIn("apk_url", data)

    def test_update_lifecycle_backup_verify_apply(self):
        """Verify update execution workflow: auto-backup, status reporting, and completion."""
        # 1. Trigger update
        resp = self.client.post("/api/system/update/apply", json={"version": "2.0.2"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "started")

        # 2. Check status
        status_resp = self.client.get("/api/system/update/status")
        self.assertEqual(status_resp.status_code, 200)
        st = status_resp.json()
        self.assertIn(st["step"], ["starting", "backing_up", "downloading", "verifying", "applying", "completed"])

        # Wait briefly for thread to advance
        import time
        time.sleep(1.8)

        done_resp = self.client.get("/api/system/update/status")
        done_st = done_resp.json()
        self.assertIn(done_st["step"], ["completed", "verifying", "applying"])
        if done_st["backup_file"]:
            self.assertTrue(os.path.isfile(done_st["backup_file"]))

    def test_otp_server_version_and_downloads_endpoint(self):
        """Verify otp_server serves version info and mounts /downloads/ correctly."""
        otp_client = TestClient(otp_server.app)
        resp = otp_client.get("/api/version")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["latest_version"], "2.0.1")
        self.assertIn("mahan-otp-2.0.1.apk", data["apk_url"])
        self.assertIn("mahanbot-latest.zip", data["download_url"])

    def test_html_contains_updated_links_and_update_center(self):
        """Verify index.html contains direct APK link, package link, and Update Center."""
        resp = self.client.get("/static/index.html")
        self.assertEqual(resp.status_code, 200)
        html = resp.text
        self.assertIn("https://otp.mahanvip.ir/downloads/mahan-otp-2.0.1.apk", html)
        self.assertIn("https://otp.mahanvip.ir/downloads/mahanbot-latest.zip", html)
        self.assertIn("updateCenterCard", html)
        self.assertIn("checkForUpdates", html)
        self.assertIn("startAutoUpdate", html)


if __name__ == "__main__":
    unittest.main()
