import tempfile
import unittest
from pathlib import Path

from bank_archive import BankArchiveStore, sanitize_filename, sanitize_nid


class BankArchiveTests(unittest.TestCase):
    def test_sanitize_nid_normalizes_persian_digits(self):
        self.assertEqual(sanitize_nid(" ۱۲۳-۴۵۶ ۷۸۹۰ "), "1234567890")

    def test_sanitize_filename_keeps_persian_bank_name(self):
        result = sanitize_filename('بانک ملی: شعبه/مرکزی')
        self.assertIn("بانک", result)
        self.assertNotIn(":", result)
        self.assertNotIn("/", result)

    def test_resolve_file_rejects_path_outside_applicant(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = BankArchiveStore(root)
            applicant = store.applicant_dir("1234567890")
            target = applicant / "بانک_ملی.json"
            target.write_text("{}", encoding="utf-8")
            self.assertEqual(store.resolve_file("1234567890", "1234567890/banks/بانک_ملی.json"), target)
            outside = root / "outside.txt"
            outside.write_text("x", encoding="utf-8")
            with self.assertRaises(ValueError):
                store.resolve_file("1234567890", "outside.txt")


if __name__ == "__main__":
    unittest.main()
