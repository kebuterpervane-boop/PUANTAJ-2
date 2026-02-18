import os
import tempfile
import unittest
from pathlib import Path

from core.database import Database


class IzinAutoKayitTests(unittest.TestCase):
    def setUp(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = Path(db_path)
        self.db = Database(str(self._db_path))
        self.db.init_izin_ayarlari()

        with self.db.get_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM personel")
            c.execute("DELETE FROM gunluk_kayit")
            c.execute("DELETE FROM izin_takip")
            c.execute("DELETE FROM izin_tur_ayarlari")
            for tur, oto in self.db._default_izin_turleri():
                c.execute(
                    "INSERT INTO izin_tur_ayarlari (izin_turu, otomatik_kayit) VALUES (?, ?)",
                    (tur, oto),
                )
            conn.commit()

    def tearDown(self):
        try:
            self._db_path.unlink(missing_ok=True)
        except Exception:
            pass

    def _insert_personel(self, ad_soyad, yevmiyeci=0, tersane_id=1, firma_id=1, yillik_izin=14):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO personel (ad_soyad, maas, ekip_adi, yevmiyeci_mi, tersane_id, firma_id, yillik_izin_hakki) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ad_soyad, 30000.0, "A", int(yevmiyeci), int(tersane_id), int(firma_id), float(yillik_izin)),
            )
            conn.commit()

    def test_canonical_type_with_auto_writes_daily_record(self):
        self._insert_personel("TEST PERSON", tersane_id=2, firma_id=1)
        self.db.set_izin_otomatik_kayit("Raporlu", True)

        self.db.add_izin_with_auto_kayit("TEST PERSON", "2026-01-05", "Raporlu", 1, "test")

        with self.db.get_connection() as conn:
            izin = conn.execute(
                "SELECT izin_turu FROM izin_takip WHERE ad_soyad=?",
                ("TEST PERSON",),
            ).fetchone()
            gunluk = conn.execute(
                "SELECT tarih, hesaplanan_normal, hesaplanan_mesai, aciklama, tersane_id "
                "FROM gunluk_kayit WHERE ad_soyad=?",
                ("TEST PERSON",),
            ).fetchone()

        self.assertIsNotNone(izin)
        self.assertEqual(izin[0], "Raporlu")
        self.assertIsNotNone(gunluk)
        self.assertEqual(gunluk[0], "2026-01-05")
        self.assertEqual(float(gunluk[1] or 0), 7.5)
        self.assertEqual(float(gunluk[2] or 0), 0.0)
        self.assertEqual(gunluk[3], "Raporlu")
        self.assertEqual(int(gunluk[4] or 0), 2)

    def test_broken_type_alias_still_applies_auto(self):
        self._insert_personel("TEST PERSON 2", tersane_id=1, firma_id=1)

        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM izin_tur_ayarlari")
            conn.execute(
                "INSERT INTO izin_tur_ayarlari (izin_turu, otomatik_kayit) VALUES (?, ?)",
                ("Do?um ?zni", 1),
            )
            conn.commit()

        self.db.add_izin_with_auto_kayit(
            "TEST PERSON 2",
            "2026-01-06",
            "Do?um ?zni",
            1,
            "test",
        )

        with self.db.get_connection() as conn:
            izin = conn.execute(
                "SELECT izin_turu FROM izin_takip WHERE ad_soyad=?",
                ("TEST PERSON 2",),
            ).fetchone()
            gunluk = conn.execute(
                "SELECT aciklama, hesaplanan_normal FROM gunluk_kayit WHERE ad_soyad=?",
                ("TEST PERSON 2",),
            ).fetchone()

        self.assertIsNotNone(izin)
        self.assertEqual(izin[0], "Do\u011fum \u0130zni")
        self.assertIsNotNone(gunluk)
        self.assertEqual(gunluk[0], "Do\u011fum \u0130zni")
        self.assertEqual(float(gunluk[1] or 0), 7.5)

    def test_unassigned_person_uses_active_tersane_fallback(self):
        self._insert_personel("TEST PERSON 3", tersane_id=0, firma_id=0)
        self.db.set_izin_otomatik_kayit("Raporlu", True)

        self.db.add_izin_with_auto_kayit(
            "TEST PERSON 3",
            "2026-01-07",
            "Raporlu",
            1,
            "test",
            tersane_id=7,
        )

        with self.db.get_connection() as conn:
            gunluk = conn.execute(
                "SELECT tersane_id, firma_id FROM gunluk_kayit WHERE ad_soyad=?",
                ("TEST PERSON 3",),
            ).fetchone()

        self.assertIsNotNone(gunluk)
        self.assertEqual(int(gunluk[0] or 0), 7)
        self.assertGreaterEqual(int(gunluk[1] or 0), 1)

        month_rows = self.db.get_records_by_month(2026, 1, tersane_id=7)
        self.assertTrue(any(r[2] == "TEST PERSON 3" for r in month_rows))

    def test_auto_disabled_type_does_not_write_daily_record(self):
        self._insert_personel("TEST PERSON 4", tersane_id=1, firma_id=1)
        self.db.set_izin_otomatik_kayit("\u00d6z\u00fcr", False)

        self.db.add_izin_with_auto_kayit("TEST PERSON 4", "2026-01-08", "\u00d6z\u00fcr", 1, "test")

        with self.db.get_connection() as conn:
            izin_count = conn.execute(
                "SELECT COUNT(*) FROM izin_takip WHERE ad_soyad=?",
                ("TEST PERSON 4",),
            ).fetchone()[0]
            gunluk_count = conn.execute(
                "SELECT COUNT(*) FROM gunluk_kayit WHERE ad_soyad=?",
                ("TEST PERSON 4",),
            ).fetchone()[0]

        self.assertEqual(int(izin_count), 1)
        self.assertEqual(int(gunluk_count), 0)

    def test_personnel_save_does_not_overwrite_auto_izin_record(self):
        self._insert_personel("TEST PERSON 5", tersane_id=3, firma_id=1)
        self.db.set_izin_otomatik_kayit("Raporlu", True)
        self.db.add_izin_with_auto_kayit("TEST PERSON 5", "2026-01-09", "Raporlu", 1, "test")

        self.db.update_personnel(
            "TEST PERSON 5",
            32000.0,
            "A",
            ozel_durum=None,
            ekstra_odeme=0.0,
            yillik_izin_hakki=14.0,
            ise_baslangic=None,
            cikis_tarihi=None,
            ekstra_odeme_not="",
            avans_not="",
            yevmiyeci_mi=0,
            tersane_id=3,
        )

        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT hesaplanan_normal, hesaplanan_mesai, aciklama, manuel_kilit "
                "FROM gunluk_kayit WHERE ad_soyad=? AND tarih=?",
                ("TEST PERSON 5", "2026-01-09"),
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(float(row[0] or 0), 7.5)
        self.assertEqual(float(row[1] or 0), 0.0)
        self.assertEqual(row[2], "Raporlu")
        self.assertEqual(int(row[3] or 0), 1)

    def test_process_izin_marks_record_approved(self):
        self._insert_personel("TEST PERSON 6", tersane_id=4, firma_id=1)
        self.db.set_izin_otomatik_kayit("Raporlu", True)
        izin_id = self.db.add_izin_with_auto_kayit("TEST PERSON 6", "2026-01-10", "Raporlu", 1, "test")

        new_id = self.db.process_izin(izin_id, tersane_id=4)

        self.assertIsNotNone(new_id)
        with self.db.get_connection() as conn:
            izin = conn.execute(
                "SELECT onay_durumu FROM izin_takip WHERE id=?",
                (new_id,),
            ).fetchone()
            gunluk = conn.execute(
                "SELECT aciklama, manuel_kilit FROM gunluk_kayit WHERE ad_soyad=? AND tarih=?",
                ("TEST PERSON 6", "2026-01-10"),
            ).fetchone()

        self.assertIsNotNone(izin)
        self.assertEqual(int(izin[0] or 0), 1)
        self.assertIsNotNone(gunluk)
        self.assertEqual(gunluk[0], "Raporlu")
        self.assertEqual(int(gunluk[1] or 0), 1)


if __name__ == "__main__":
    unittest.main()
