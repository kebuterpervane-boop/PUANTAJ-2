import sqlite3
import unittest

from migrations.migrations import migration_006_enforce_avans_kesinti_constraints


class AvansKesintiMigrationTests(unittest.TestCase):
    def test_migration_rebuilds_and_normalizes_legacy_rows(self):
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE avans_kesinti (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tarih TEXT,
                ad_soyad TEXT,
                tur TEXT,
                tutar REAL,
                aciklama TEXT
            )"""
        )
        cur.execute(
            "INSERT INTO avans_kesinti (tarih, ad_soyad, tur, tutar, aciklama) VALUES (?,?,?,?,?)",
            ("2026-02-13", "Ali Veli", "Yanlis", -125.0, "legacy"),
        )
        conn.commit()

        migration_006_enforce_avans_kesinti_constraints(conn)

        row = conn.execute(
            "SELECT tur, tutar FROM avans_kesinti WHERE ad_soyad=?",
            ("Ali Veli",),
        ).fetchone()
        self.assertEqual(row[0], "Kesinti")
        self.assertEqual(row[1], 125.0)

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO avans_kesinti (tarih, ad_soyad, tur, tutar, aciklama) VALUES (?,?,?,?,?)",
                ("2026-02-13", "Test", "InvalidTur", 10.0, ""),
            )

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO avans_kesinti (tarih, ad_soyad, tur, tutar, aciklama) VALUES (?,?,?,?,?)",
                ("2026-02-13", "Test", "Avans", -1.0, ""),
            )

        conn.execute(
            "INSERT INTO avans_kesinti (tarih, ad_soyad, tur, tutar, aciklama) VALUES (?,?,?,?,?)",
            ("2026-02-13", "Test", "Avans", 1.0, ""),
        )
        conn.commit()

    def test_migration_creates_table_when_missing(self):
        conn = sqlite3.connect(":memory:")
        migration_006_enforce_avans_kesinti_constraints(conn)

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO avans_kesinti (tarih, ad_soyad, tur, tutar, aciklama) VALUES (?,?,?,?,?)",
                ("2026-02-13", "Yeni", "X", 0.0, ""),
            )

        idx_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_avans_kesinti_%'"
        ).fetchall()
        self.assertGreaterEqual(len(idx_rows), 2)


if __name__ == "__main__":
    unittest.main()
