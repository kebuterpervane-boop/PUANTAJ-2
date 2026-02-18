import unittest

from core.input_validators import (
    clean_text,
    ensure_choice,
    ensure_hhmm_time,
    ensure_non_empty,
    ensure_non_negative_number,
    ensure_optional_iso_date,
    ensure_positive_int,
    parse_hhmm_to_minutes,
)


class InputValidatorsTests(unittest.TestCase):
    def test_clean_text(self):
        self.assertEqual(clean_text(None), "")
        self.assertEqual(clean_text("  abc  "), "abc")

    def test_ensure_non_empty(self):
        ok, value = ensure_non_empty("  ali  ", "Ad Soyad")
        self.assertTrue(ok)
        self.assertEqual(value, "ali")
        ok, msg = ensure_non_empty("   ", "Ad Soyad")
        self.assertFalse(ok)
        self.assertIn("bos olamaz", msg)

    def test_ensure_non_negative_number(self):
        ok, val = ensure_non_negative_number("12.5", "Tutar")
        self.assertTrue(ok)
        self.assertEqual(val, 12.5)
        ok, msg = ensure_non_negative_number("-1", "Tutar")
        self.assertFalse(ok)
        self.assertIn("negatif olamaz", msg)
        ok, val = ensure_non_negative_number("", "Tutar", default=0)
        self.assertTrue(ok)
        self.assertEqual(val, 0.0)

    def test_ensure_choice(self):
        ok, val = ensure_choice("Avans", ("Avans", "Kesinti"), "Tur")
        self.assertTrue(ok)
        self.assertEqual(val, "Avans")
        ok, msg = ensure_choice("X", ("Avans", "Kesinti"), "Tur")
        self.assertFalse(ok)
        self.assertIn("gecersiz", msg)

    def test_ensure_optional_iso_date(self):
        ok, val = ensure_optional_iso_date("2026-02-13", "Tarih")
        self.assertTrue(ok)
        self.assertEqual(val, "2026-02-13")
        ok, val = ensure_optional_iso_date("", "Tarih")
        self.assertTrue(ok)
        self.assertIsNone(val)
        ok, msg = ensure_optional_iso_date("13.02.2026", "Tarih")
        self.assertFalse(ok)
        self.assertIn("YYYY-MM-DD", msg)

    def test_hhmm_validators(self):
        self.assertEqual(parse_hhmm_to_minutes("08:20"), 500)
        self.assertEqual(parse_hhmm_to_minutes("8:20"), 500)
        self.assertIsNone(parse_hhmm_to_minutes("25:00"))
        self.assertIsNone(parse_hhmm_to_minutes("08-20"))
        ok, val = ensure_hhmm_time("8:5", "Saat")
        self.assertTrue(ok)
        self.assertEqual(val, "08:05")
        ok, msg = ensure_hhmm_time("99:00", "Saat")
        self.assertFalse(ok)
        self.assertIn("HH:MM", msg)

    def test_positive_int_validator(self):
        ok, val = ensure_positive_int("30", "Gun", min_value=1)
        self.assertTrue(ok)
        self.assertEqual(val, 30)
        ok, msg = ensure_positive_int("0", "Gun", min_value=1)
        self.assertFalse(ok)
        self.assertIn("en az 1", msg)
        ok, msg = ensure_positive_int("x", "Gun", min_value=1)
        self.assertFalse(ok)
        self.assertIn("tamsayi", msg)


if __name__ == "__main__":
    unittest.main()
