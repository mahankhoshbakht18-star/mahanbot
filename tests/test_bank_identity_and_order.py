"""Bank names shown to people: official name, own emoji, official CBI order."""
import unittest

from core.bank_matcher import BANK_EMOJIS, KNOWN_BANK_CODES, bank_identity, bank_sort_key
from core.persian_normalizer import gregorian_to_jalali, to_persian_digits


class BankIdentityTests(unittest.TestCase):
    def test_every_official_bank_resolves_with_its_own_emoji(self):
        self.assertEqual(set(BANK_EMOJIS), set(KNOWN_BANK_CODES))
        self.assertEqual(len(set(BANK_EMOJIS.values())), len(BANK_EMOJIS))  # all different
        for code, name in KNOWN_BANK_CODES.items():
            for variant in (name, name.replace("‌", " "), name.replace("ی", "ي").replace("ک", "ك"),
                            name + " - دارای ظرفیت ثبت نام متقاضی در صف : 1,450 نفر",
                            name + " [در انتظار تخصیص ظرفیت جدید]"):
                self.assertEqual(bank_identity(variant)[0], code, variant)

    def test_status_words_are_not_mistaken_for_a_bank(self):
        # "جدید" contains "دی"; it must not turn بانک تجارت into بانک دی.
        self.assertEqual(bank_identity("بانک تجارت (در انتظار تخصیص ظرفیت جدید)")[0], "18")

    def test_unknown_bank_keeps_its_name_and_sorts_last(self):
        self.assertEqual(bank_identity("بانک آزمایشی"), (None, "بانک آزمایشی", "🏦"))
        names = ["بانک آزمایشی", "بانک صادرات ایران", "بانک ملت"]
        self.assertEqual(sorted(names, key=bank_sort_key), ["بانک ملت", "بانک صادرات ایران", "بانک آزمایشی"])

    def test_persian_date_and_digits(self):
        self.assertEqual(gregorian_to_jalali(2026, 11, 21), (1405, 8, 30))
        self.assertEqual(gregorian_to_jalali(2025, 3, 21), (1404, 1, 1))
        self.assertEqual(to_persian_digits(1450, group=True), "۱٬۴۵۰")


if __name__ == "__main__":
    unittest.main()
