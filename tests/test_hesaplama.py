import unittest
from datetime import datetime, timedelta

from core.hesaplama import (
    hesapla_hakedis,
    hesapla_maktu_hakedis,
    parse_time_to_minutes,
)


def _find_weekday(start_date_str, target_weekday):
    dt = datetime.strptime(start_date_str, "%Y-%m-%d")
    while dt.weekday() != target_weekday:
        dt += timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


class HesaplamaTests(unittest.TestCase):
    def test_parse_time_to_minutes(self):
        self.assertEqual(parse_time_to_minutes("08:30"), 510)
        self.assertEqual(parse_time_to_minutes("08:30:59"), 510)
        self.assertIsNone(parse_time_to_minutes(""))
        self.assertIsNone(parse_time_to_minutes("not-a-time"))

    def test_maktu_hakedis_30_gun_kurali(self):
        result = hesapla_maktu_hakedis(2025, 2, 20, 30000)
        self.assertEqual(result["ayin_gercek_gun_sayisi"], 28)
        self.assertEqual(result["eksik_gun"], 8)
        self.assertEqual(result["odemeye_esas_gun"], 22)
        self.assertEqual(result["gunluk_ucret"], 1000.0)
        self.assertEqual(result["hakedis"], 22000.0)

    def test_hakedis_pazar_gelmedi_maasli(self):
        pazar = _find_weekday("2025-01-01", 6)
        normal, mesai, aciklama = hesapla_hakedis(
            pazar, "", "", "", holiday_set=set(), yevmiyeci_mi=False
        )
        self.assertEqual(normal, 7.5)
        self.assertEqual(mesai, 0.0)
        self.assertEqual(aciklama, "Pazar Tatili")

    def test_hakedis_yevmiyeci_gecikme_cezasi(self):
        pazartesi = _find_weekday("2025-01-01", 0)
        normal, mesai, aciklama = hesapla_hakedis(
            pazartesi,
            "09:20",
            "18:00",
            "",
            holiday_set=set(),
            yevmiyeci_mi=True,
        )
        self.assertEqual(normal, 0.8667)
        self.assertEqual(mesai, 0.0)
        self.assertEqual(aciklama, "")

    def test_hakedis_fiili_calisma_modu(self):
        settings_cache = {
            "calisma_hesaplama_modu": "fiili_calisma",
            "ogle_molasi_baslangic": "12:00",
            "ogle_molasi_bitis": "13:00",
            "ara_mola_dk": 30,
            "fiili_saat_yuvarlama": "ondalik",
        }
        sali = _find_weekday("2025-01-01", 1)
        normal, mesai, aciklama = hesapla_hakedis(
            sali,
            "08:00",
            "18:00",
            "00:30",
            holiday_set=set(),
            yevmiyeci_mi=False,
            settings_cache=settings_cache,
        )
        self.assertEqual(normal, 7.5)
        self.assertEqual(mesai, 0.0)
        self.assertEqual(aciklama, "Fiili Calisma")


if __name__ == "__main__":
    unittest.main()
