"""The initial-registration job must receive the same applicant fields the form saved."""
import unittest

from bot_register import RegistrationBot
from core.helpers import normalize_applicant_payload, validate_registration_readiness


def _form_payload():
    """Values as typed in the new-applicant form, including Persian digits and an empty optional phone."""
    return {
        "full_name": "علی رضایی",
        "national_id": "۰۰۱۲۳۴۵۶۷۸",
        "data": {
            "mobile": "۰۹۱۲۳۴۵۶۷۸۹",
            "spouse_national_id": "۰۰۹۸۷۶۵۴۳۲",
            "phone": "",
            "zip_code": "۰۱۲۳۴۵۶۷۸۹",
            "postal_code": "۰۱۲۳۴۵۶۷۸۹",
            "loan_type": "marriage",
            "child_national_id": "",
            "child_birth_d": "",
            "child_birth_m": "",
            "child_birth_y": "",
            "state": "تهران",
            "city": "ری",
            "military_status": "2",
            "birth_d": "۱۵",
            "birth_m": "۰۶",
            "birth_y": "۱۳۷۰",
            "marriage_d": "1",
            "marriage_m": "2",
            "marriage_y": "1400",
            "priority_banks": ["بانک ملی"],
            "banks": ["بانک ملی"],
        },
    }


class RegistrationFormReadTests(unittest.TestCase):
    def test_form_payload_reaches_registration_reader_unchanged(self):
        raw = _form_payload()
        name, nid, saved = normalize_applicant_payload(raw["full_name"], raw["national_id"], raw["data"])
        applicant = {"full_name": name, "national_id": nid, "data": saved}
        ok, errors, received = validate_registration_readiness(applicant, expected_nid=nid)
        self.assertTrue(ok, errors)
        birth = RegistrationBot._get_birth_parts(None, received)
        marriage = RegistrationBot._get_marriage_parts(None, received)
        expected = {
            "full_name": "علی رضایی",
            "national_id": "0012345678",
            "mobile": "09123456789",
            "spouse_national_id": "0098765432",
            "phone": "",
            "postal_code": "0123456789",
            "state": "تهران",
            "city": "ری",
            "military_status": "2",
            "birth": ("1370", "06", "15"),
            "marriage": ("1400", "2", "1"),
            "priority_banks": ["بانک ملی"],
        }
        actual = {
            "full_name": received["full_name"],
            "national_id": received["national_id"],
            "mobile": received["mobile"],
            "spouse_national_id": received["spouse_national_id"],
            "phone": received["phone"],
            "postal_code": received["postal_code"],
            "state": received["state"],
            "city": received["city"],
            "military_status": received["military_status"],
            "birth": birth,
            "marriage": marriage,
            "priority_banks": received["priority_banks"],
        }
        self.assertEqual(actual, expected)
        self.assertEqual(received["child_national_id"], "")
        self.assertEqual(saved, received)

    def test_missing_required_field_is_named_and_other_values_stay(self):
        raw = _form_payload()
        raw["data"]["birth_y"] = ""
        raw["data"]["birth_m"] = ""
        raw["data"]["birth_d"] = ""
        name, nid, saved = normalize_applicant_payload(raw["full_name"], raw["national_id"], raw["data"])
        ok, errors, received = validate_registration_readiness(
            {"full_name": name, "national_id": nid, "data": saved}, expected_nid=nid
        )
        self.assertFalse(ok)
        self.assertTrue(any("تاریخ تولد" in err for err in errors))
        self.assertEqual(received["postal_code"], "0123456789")
        self.assertEqual(received["spouse_national_id"], "0098765432")
        self.assertEqual(received["phone"], "")

    def test_other_applicant_nid_is_rejected(self):
        raw = _form_payload()
        name, nid, saved = normalize_applicant_payload(raw["full_name"], raw["national_id"], raw["data"])
        ok, errors, _ = validate_registration_readiness(
            {"full_name": name, "national_id": nid, "data": saved},
            expected_nid="1234567890",
        )
        self.assertFalse(ok)
        self.assertTrue(any("عدم تطابق" in err for err in errors))

    def test_english_digits_and_second_read_match(self):
        raw = _form_payload()
        raw["national_id"] = "0012345678"
        raw["data"]["mobile"] = "09123456789"
        name, nid, first = normalize_applicant_payload(raw["full_name"], raw["national_id"], raw["data"])
        _, _, second = normalize_applicant_payload(name, nid, first)
        self.assertEqual(first, second)
        ok, errors, _ = validate_registration_readiness(
            {"full_name": name, "national_id": nid, "data": second}, expected_nid=nid
        )
        self.assertTrue(ok, errors)


if __name__ == "__main__":
    unittest.main()
