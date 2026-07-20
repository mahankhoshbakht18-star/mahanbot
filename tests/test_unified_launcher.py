import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import unified_launcher


class UnifiedLauncherTests(unittest.TestCase):
    def test_env_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mahanbot.env"
            expected = {
                "MAHANBOT_BROWSER_CHANNEL": "msedge",
                "MAHANBOT_SMS_DEVICE_KEY": "device-test-key",
                "MAHANBOT_SMS_PORT": "8010",
            }
            unified_launcher._write_env(path, expected)
            self.assertEqual(unified_launcher._read_env(path), expected)

    def test_env_reader_ignores_invalid_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mahanbot.env"
            path.write_text("VALID_KEY=ok\nBAD KEY=no\n# comment\n", encoding="utf-8")
            self.assertEqual(unified_launcher._read_env(path), {"VALID_KEY": "ok"})

    def test_phone_setup_contains_notification_only_notice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original = unified_launcher.PHONE_SETUP_FILE
            try:
                unified_launcher.PHONE_SETUP_FILE = Path(temp_dir) / "PHONE_SETUP.txt"
                endpoint = unified_launcher._write_phone_setup(
                    {
                        "MAHANBOT_SMS_PORT": "8010",
                        "MAHANBOT_SMS_DEVICE_KEY": "safe-device-key",
                    }
                )
                text = unified_launcher.PHONE_SETUP_FILE.read_text(encoding="utf-8")
                self.assertIn("/api/v1/sms/notify", endpoint)
                self.assertIn("Only the arrival notification is accepted", text)
                self.assertNotIn("otp=", text.lower())
            finally:
                unified_launcher.PHONE_SETUP_FILE = original

    def test_sqlite_validation_rejects_corrupt_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            corrupt = Path(temp_dir) / "corrupt.db"
            corrupt.write_bytes(b"this is not sqlite")
            self.assertFalse(unified_launcher._is_valid_sqlite(corrupt))

    def test_database_backup_is_valid_and_preserves_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.db"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
                connection.execute("INSERT INTO sample(value) VALUES (?)", ("kept",))
                connection.commit()

            original_backup_dir = unified_launcher.BACKUP_DIR
            try:
                unified_launcher.BACKUP_DIR = root / "backups"
                backup = unified_launcher._backup_database(source)
            finally:
                unified_launcher.BACKUP_DIR = original_backup_dir

            self.assertIsNotNone(backup)
            self.assertTrue(unified_launcher._is_valid_sqlite(backup))
            with closing(sqlite3.connect(backup)) as connection:
                row = connection.execute("SELECT value FROM sample").fetchone()
            self.assertEqual(row, ("kept",))

            # Regression test for WinError 32: the launcher must close every
            # SQLite handle before returning the backup path. Windows refuses
            # to rename a database while any connection still owns the file.
            renamed = backup.with_name("renamed-backup.db")
            backup.replace(renamed)
            self.assertTrue(renamed.is_file())
            self.assertTrue(unified_launcher._is_valid_sqlite(renamed))


if __name__ == "__main__":
    unittest.main()
