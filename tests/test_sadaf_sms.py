# -*- coding: utf-8 -*-
import csv
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.sadaf import db, sms

SENDER = "98404010066600"
CODE_SMS = "*NIOPDC*\nکد تایید 8510273"
REQUEST_SMS = "*NIOPDC*\nثبت درخواست:36858316: 2000 لیتر نفتگاز\n14:32 1405/09/05"
DELIVERY_SMS = "*NIOPDC*\nتایید پخش:36858316: 60 لیتر نفتگاز\n09:10 1405/09/07"


class ParseTests(unittest.TestCase):
    def test_code_is_seven_digits(self):
        self.assertEqual(sms.parse(CODE_SMS, SENDER), {"kind": "code", "code": "8510273"})

    def test_bare_code_template_from_niopdc(self):
        self.assertEqual(sms.parse("کد تایید 8510273", SENDER)["code"], "8510273")

    def test_persian_digits_and_yeh(self):
        self.assertEqual(sms.parse("*NIOPDC* كد تائيد ۸۵۱۰۲۷۳", SENDER)["code"], "8510273")

    def test_loan_lengths_are_not_niopdc_codes(self):
        for code in ("1234", "123456", "12345678"):
            self.assertIsNone(sms.parse(f"*NIOPDC* کد تایید {code}", SENDER))

    def test_loan_message_is_ignored(self):
        self.assertIsNone(sms.parse("کد تایید مربوط به سامانه ازدواج: 1234567", "2000411"))

    def test_other_sender_is_ignored(self):
        self.assertIsNone(sms.parse(CODE_SMS, "30001234"))

    def test_request(self):
        parsed = sms.parse(REQUEST_SMS, SENDER)
        self.assertEqual(parsed["kind"], "request")
        self.assertEqual((parsed["tracking"], parsed["amount"], parsed["product"]), ("36858316", "2000", "نفتگاز"))
        self.assertEqual(parsed["sms_at"], "1405/09/05 14:32")

    def test_delivery(self):
        parsed = sms.parse(DELIVERY_SMS, SENDER)
        self.assertEqual((parsed["kind"], parsed["tracking"], parsed["amount"]), ("delivery", "36858316", "60"))

    def test_password_and_card_are_secret(self):
        self.assertEqual(sms.parse("*NIOPDC* کلمه عبور شما 1234567 است", SENDER), {"kind": "secret"})
        self.assertEqual(sms.parse("*NIOPDC* کارت بانکی شما 6037991234567890", SENDER), {"kind": "secret"})

    def test_pasted_text_needs_header(self):
        self.assertIsNone(sms.parse("ثبت درخواست:36858316: 2000 لیتر نفتگاز"))
        self.assertEqual(sms.parse(REQUEST_SMS)["kind"], "request")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(db, "DB_FILE", root / "sadaf.db"),
            patch.object(sms, "JAHAD_FILE", root / "jahad-requests.csv"),
            patch.object(sms, "REPORTS", root),
        ]
        for item in self.patches:
            item.start()
        sms._SUBMIT.clear()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def rows(self):
        with sms.JAHAD_FILE.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.reader(handle))

    def test_request_then_delivery_updates_same_row(self):
        sms.note_submit({"id": 2, "full_name": "امیر نرانی", "username": "769732A3611"})
        self.assertEqual(sms.ingest(sms.parse(REQUEST_SMS, SENDER), "test", "m1"), "request")
        self.assertEqual(sms.ingest(sms.parse(DELIVERY_SMS, SENDER), "test", "m2"), "delivery")
        self.assertEqual(sms.ingest(sms.parse(REQUEST_SMS, SENDER), "test", "m3"), "request")
        rows = self.rows()
        self.assertEqual(rows[0], sms.JAHAD_COLUMNS)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][:6], ["امیر نرانی", "769732A3611", "36858316", "نفتگاز", "2000", "1405/09/05 14:32"])
        self.assertIn("60", rows[1][6])

    def test_duplicate_message_id(self):
        sms.ingest(sms.parse(REQUEST_SMS, SENDER), "test", "same")
        self.assertEqual(sms.ingest(sms.parse(REQUEST_SMS, SENDER), "test", "same"), "duplicate")

    def test_secret_is_not_stored(self):
        self.assertEqual(sms.manual("*NIOPDC* کلمه عبور شما 1234567 است"), "secret")
        sms._ready()
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sms_codes").fetchone()[0], 0)
            texts = [row[0] for row in conn.execute("SELECT message FROM events")]
        self.assertFalse(any("1234567" in text for text in texts))

    def test_code_taken_once_and_not_logged(self):
        since = time.time() - 1
        self.assertEqual(sms.manual("8510273"), "code")
        self.assertEqual(sms.take_code(since)["code"], "8510273")
        self.assertIsNone(sms.take_code(since))
        with db.connect() as conn:
            texts = [row[0] for row in conn.execute("SELECT message FROM events")]
        self.assertFalse(any("8510273" in text for text in texts))

    def test_old_code_not_taken(self):
        sms.manual("8510273")
        self.assertIsNone(sms.take_code(time.time() + 10))


if __name__ == "__main__":
    unittest.main()
