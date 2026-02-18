"""Migration scripts, ordered by version number starting at 1.
Each migration is a function that accepts a sqlite3.Connection and performs schema changes.
"""
from datetime import datetime


def migration_001_add_phone_to_personel(conn):
    """Add a phone column to personel table if not exists."""
    cur = conn.cursor()
    # Check if column exists
    cur.execute("PRAGMA table_info(personel)")
    cols = [r[1] for r in cur.fetchall()]
    if 'phone' not in cols:
        cur.execute("ALTER TABLE personel ADD COLUMN phone TEXT DEFAULT ''")
    conn.commit()


def migration_002_index_gunluk_tarih(conn):
    """Create index on gunluk_kayit(tarih) for faster month queries."""
    cur = conn.cursor()
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gunluk_tarih ON gunluk_kayit(tarih)")
    conn.commit()


def migration_003_ensure_mesai_katsayilari_schema(conn):
    """Ensure mesai_katsayilari table has the correct schema."""
    cur = conn.cursor()
    
    # Check if table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mesai_katsayilari'")
    table_exists = cur.fetchone() is not None
    
    if not table_exists:
        # Create table if it doesn't exist
        cur.execute('''CREATE TABLE mesai_katsayilari (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        saat_araligi_baslangic REAL,
                        saat_araligi_bitis REAL,
                        katsayi REAL,
                        aciklama TEXT)''')
    else:
        # Check if katsayi column exists
        cur.execute("PRAGMA table_info(mesai_katsayilari)")
        cols = {r[1]: r for r in cur.fetchall()}
        
        # If mesai_saati column exists but katsayi doesn't, rename it
        if 'mesai_saati' in cols and 'katsayi' not in cols:
            # Rename column using SQLite's sqlite_rename_column pragma (available in SQLite 3.25.0+)
            # Or recreate the table
            cur.execute('''CREATE TABLE mesai_katsayilari_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            saat_araligi_baslangic REAL,
                            saat_araligi_bitis REAL,
                            katsayi REAL,
                            aciklama TEXT)''')
            
            # Copy data from old table
            cur.execute('''INSERT INTO mesai_katsayilari_new 
                           SELECT id, saat_araligi_baslangic, saat_araligi_bitis, mesai_saati, aciklama 
                           FROM mesai_katsayilari''')
            
            # Drop old table
            cur.execute("DROP TABLE mesai_katsayilari")
            
            # Rename new table to original name
            cur.execute("ALTER TABLE mesai_katsayilari_new RENAME TO mesai_katsayilari")
        
        # If table doesn't have correct columns, recreate it
        if 'katsayi' not in cols and 'mesai_saati' not in cols:
            cur.execute("DROP TABLE IF EXISTS mesai_katsayilari")
            cur.execute('''CREATE TABLE mesai_katsayilari (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            saat_araligi_baslangic REAL,
                            saat_araligi_bitis REAL,
                            katsayi REAL,
                            aciklama TEXT)''')
    
    conn.commit()


def migration_004_ensure_yevmiye_katsayilari_schema(conn):
    """Ensure yevmiye_katsayilari table has the correct schema."""
    cur = conn.cursor()
    
    # Check if table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='yevmiye_katsayilari'")
    table_exists = cur.fetchone() is not None
    
    if not table_exists:
        # Create table if it doesn't exist
        cur.execute('''CREATE TABLE yevmiye_katsayilari (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        saat_araligi_baslangic REAL,
                        saat_araligi_bitis REAL,
                        yevmiye_katsayi REAL,
                        aciklama TEXT)''')
    else:
        # Check if yevmiye_katsayi column exists
        cur.execute("PRAGMA table_info(yevmiye_katsayilari)")
        cols = {r[1]: r for r in cur.fetchall()}
        
        # If the table exists but yevmiye_katsayi column is missing, add it
        if 'yevmiye_katsayi' not in cols:
            cur.execute("ALTER TABLE yevmiye_katsayilari ADD COLUMN yevmiye_katsayi REAL DEFAULT 0.5")
    
    conn.commit()


def migration_005_convert_rules_to_exit_time(conn):
    """
    Convert old "mesai saati araligi" rules to new "cikis saati araligi" rules.
    Heuristic:
    - If any row in a scope has start/end >= 12, treat as already exit-time and skip.
    - Otherwise, shift ranges by mesai_baslangic_saat and (for mesai_katsayilari) convert
      old multiplier logic to a fixed payout: new_value = old_bitis * old_katsayi.
    """
    cur = conn.cursor()

    def table_exists(name):
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return cur.fetchone() is not None

    def has_column(table, col):
        try:
            cur.execute(f"PRAGMA table_info({table})")
            return col in [r[1] for r in cur.fetchall()]
        except Exception:
            return False

    def parse_hhmm_to_minutes(val, default_minutes):
        try:
            parts = str(val).split(":")
            if len(parts) >= 2:
                return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            pass
        return default_minutes

    def get_global_mesai_start_minutes():
        try:
            row = cur.execute("SELECT value FROM settings WHERE key='mesai_baslangic_saat'").fetchone()
            if row and row[0]:
                return parse_hhmm_to_minutes(row[0], 17 * 60 + 30)
        except Exception:
            pass
        return 17 * 60 + 30

    def get_tersane_mesai_start_minutes(tersane_id):
        # Try tersane table if available; fallback to global.
        if tersane_id and table_exists("tersane"):
            try:
                row = cur.execute("SELECT mesai_baslangic FROM tersane WHERE id=?", (tersane_id,)).fetchone()
                if row and row[0]:
                    return parse_hhmm_to_minutes(row[0], get_global_mesai_start_minutes())
            except Exception:
                pass
        return get_global_mesai_start_minutes()

    def group_rows(rows, has_tersane):
        groups = {}
        for r in rows:
            if has_tersane:
                rec_id, bas, bit, val, tersane_id = r
                scope_id = int(tersane_id or 0)
            else:
                rec_id, bas, bit, val = r
                scope_id = 0
            groups.setdefault(scope_id, []).append((rec_id, bas, bit, val))
        return groups

    def looks_like_exit_time(rows):
        # If any start/end >= 12, assume already exit-time rules.
        for _rec_id, bas, bit, _val in rows:
            try:
                if (bas is not None and float(bas) >= 12.0) or (bit is not None and float(bit) >= 12.0):
                    return True
            except Exception:
                continue
        return False

    if table_exists("mesai_katsayilari"):
        has_tersane = has_column("mesai_katsayilari", "tersane_id")
        if has_tersane:
            rows = cur.execute(
                "SELECT id, saat_araligi_baslangic, saat_araligi_bitis, katsayi, tersane_id FROM mesai_katsayilari"
            ).fetchall()
        else:
            rows = cur.execute(
                "SELECT id, saat_araligi_baslangic, saat_araligi_bitis, katsayi FROM mesai_katsayilari"
            ).fetchall()
        for scope_id, scope_rows in group_rows(rows, has_tersane).items():
            if looks_like_exit_time(scope_rows):
                continue
            base_minutes = get_tersane_mesai_start_minutes(scope_id)
            base_hours = base_minutes / 60.0
            for rec_id, bas, bit, val in scope_rows:
                try:
                    bas_f = float(bas or 0.0)
                    bit_f = float(bit or 0.0)
                    val_f = float(val or 0.0)
                    new_bas = base_hours + bas_f
                    new_bit = base_hours + bit_f
                    new_val = bit_f * val_f
                    cur.execute(
                        "UPDATE mesai_katsayilari SET saat_araligi_baslangic=?, saat_araligi_bitis=?, katsayi=? WHERE id=?",
                        (new_bas, new_bit, new_val, rec_id)
                    )
                except Exception:
                    continue

    if table_exists("yevmiye_katsayilari"):
        has_tersane = has_column("yevmiye_katsayilari", "tersane_id")
        if has_tersane:
            rows = cur.execute(
                "SELECT id, saat_araligi_baslangic, saat_araligi_bitis, yevmiye_katsayi, tersane_id FROM yevmiye_katsayilari"
            ).fetchall()
        else:
            rows = cur.execute(
                "SELECT id, saat_araligi_baslangic, saat_araligi_bitis, yevmiye_katsayi FROM yevmiye_katsayilari"
            ).fetchall()
        for scope_id, scope_rows in group_rows(rows, has_tersane).items():
            if looks_like_exit_time(scope_rows):
                continue
            base_minutes = get_tersane_mesai_start_minutes(scope_id)
            base_hours = base_minutes / 60.0
            for rec_id, bas, bit, val in scope_rows:
                try:
                    bas_f = float(bas or 0.0)
                    bit_f = float(bit or 0.0)
                    val_f = float(val or 0.0)
                    new_bas = base_hours + bas_f
                    new_bit = base_hours + bit_f
                    cur.execute(
                        "UPDATE yevmiye_katsayilari SET saat_araligi_baslangic=?, saat_araligi_bitis=?, yevmiye_katsayi=? WHERE id=?",
                        (new_bas, new_bit, val_f, rec_id)
                    )
                except Exception:
                    continue

    conn.commit()


def migration_006_enforce_avans_kesinti_constraints(conn):
    """Enforce valid tur/tutar values in avans_kesinti and add helpful indexes."""
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='avans_kesinti'")
    exists = cur.fetchone() is not None

    if not exists:
        cur.execute(
            '''CREATE TABLE avans_kesinti (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tarih TEXT,
                    ad_soyad TEXT,
                    tur TEXT NOT NULL CHECK(tur IN ('Avans', 'Kesinti')),
                    tutar REAL NOT NULL CHECK(tutar >= 0),
                    aciklama TEXT
                )'''
        )
    else:
        create_sql_row = cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='avans_kesinti'"
        ).fetchone()
        create_sql = (create_sql_row[0] or "") if create_sql_row else ""
        has_tur_check = "CHECK(tur IN ('Avans', 'Kesinti'))" in create_sql
        has_tutar_check = "CHECK(tutar >= 0)" in create_sql

        if not (has_tur_check and has_tutar_check):
            cur.execute(
                '''CREATE TABLE avans_kesinti_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tarih TEXT,
                        ad_soyad TEXT,
                        tur TEXT NOT NULL CHECK(tur IN ('Avans', 'Kesinti')),
                        tutar REAL NOT NULL CHECK(tutar >= 0),
                        aciklama TEXT
                    )'''
            )
            # Normalize legacy rows to match new constraints.
            cur.execute(
                '''INSERT INTO avans_kesinti_new (id, tarih, ad_soyad, tur, tutar, aciklama)
                   SELECT id,
                          tarih,
                          ad_soyad,
                          CASE
                              WHEN TRIM(COALESCE(tur, '')) = 'Avans' THEN 'Avans'
                              WHEN TRIM(COALESCE(tur, '')) = 'Kesinti' THEN 'Kesinti'
                              ELSE 'Kesinti'
                          END,
                          CASE
                              WHEN tutar IS NULL THEN 0
                              WHEN tutar < 0 THEN ABS(tutar)
                              ELSE tutar
                          END,
                          aciklama
                   FROM avans_kesinti'''
            )
            cur.execute("DROP TABLE avans_kesinti")
            cur.execute("ALTER TABLE avans_kesinti_new RENAME TO avans_kesinti")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_avans_kesinti_tarih ON avans_kesinti(tarih)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_avans_kesinti_ad_tarih ON avans_kesinti(ad_soyad, tarih)"
    )
    conn.commit()


def migration_007_upload_batch_infra(conn):
    """Batch yükleme altyapısı: import_batch_id kolonu, app_meta ve upload_batch_log_personel tabloları."""
    cur = conn.cursor()

    # 1) gunluk_kayit'a import_batch_id ekle (varsa ekleme)
    cur.execute("PRAGMA table_info(gunluk_kayit)")
    cols = [r[1] for r in cur.fetchall()]
    if 'import_batch_id' not in cols:
        cur.execute("ALTER TABLE gunluk_kayit ADD COLUMN import_batch_id TEXT DEFAULT NULL")

    # 2) app_meta: uygulama geneli anahtar/değer deposu
    cur.execute('''CREATE TABLE IF NOT EXISTS app_meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    )''')

    # 3) upload_batch_log_personel: yükleme öncesi personel snapshot'ı
    cur.execute('''CREATE TABLE IF NOT EXISTS upload_batch_log_personel (
        batch_id      TEXT,
        ad_soyad      TEXT,
        personel_id   INTEGER,
        old_firma_id  INTEGER,
        old_tersane_id INTEGER,
        old_ekip      TEXT,
        old_gorev     TEXT,
        old_ucret     REAL,
        old_durum     TEXT,
        changed_at    TEXT,
        PRIMARY KEY (batch_id, ad_soyad)
    )''')

    conn.commit()


def migration_008_ensure_import_batch_id(conn):
    """Eski DB'lerde import_batch_id kolonu eksik kalabilir; güvenli şekilde ekle."""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(gunluk_kayit)")
    cols = [r[1] for r in cur.fetchall()]
    if 'import_batch_id' not in cols:
        cur.execute("ALTER TABLE gunluk_kayit ADD COLUMN import_batch_id TEXT DEFAULT NULL")
    conn.commit()


def migration_009_gunluk_kayit_unique_tersane_id(conn):
    """
    UNIQUE(tarih, ad_soyad) kısıtını UNIQUE(tarih, ad_soyad, COALESCE(tersane_id,-1))
    expression index ile değiştirir.  Böylece farklı tersanelerdeki aynı isimli
    personelin aynı tarihteki kaydı artık çakışmaz.

    - Eski DB (inline UNIQUE var): çakışan satırlar dedupe_log'a alınır,
      tablo inline-UNIQUE olmadan yeniden oluşturulur, index eklenir.
    - Yeni DB (inline UNIQUE yok): sadece expression index eklenir.
    - İdempotent: index zaten varsa hiçbir şey yapılmaz.
    """
    cur = conn.cursor()

    # İdempotent kontrol: index zaten mevcutsa atla
    if cur.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_gunluk_unique_tersane'"
    ).fetchone():
        conn.commit()
        return

    # Eski inline UNIQUE(tarih, ad_soyad) var mı?
    row = cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='gunluk_kayit'"
    ).fetchone()
    table_sql = row[0] if row else ''
    has_old_unique = 'UNIQUE(tarih, ad_soyad)' in table_sql

    if has_old_unique:
        # Dedupe log tablosu (savunma amaçlı; normalde bu tablo boş kalır)
        cur.execute('''CREATE TABLE IF NOT EXISTS gunluk_kayit_dedupe_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            orig_id    INTEGER,
            tarih      TEXT,
            ad_soyad   TEXT,
            tersane_id INTEGER,
            reason     TEXT,
            dedupe_at  TEXT
        )''')

        # Yeni anahtar (tarih, ad_soyad, COALESCE(tersane_id,-1)) açısından
        # çakışan satırları bul — eski UNIQUE'in varlığı nedeniyle normalde boş olmalı
        dup_rows = cur.execute('''
            SELECT id, tarih, ad_soyad, tersane_id
            FROM gunluk_kayit
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM gunluk_kayit
                GROUP BY tarih, ad_soyad, COALESCE(tersane_id, -1)
            )
        ''').fetchall()

        if dup_rows:
            now = datetime.now().isoformat()
            cur.executemany(
                "INSERT INTO gunluk_kayit_dedupe_log "
                "(orig_id, tarih, ad_soyad, tersane_id, reason, dedupe_at) "
                "VALUES (?,?,?,?,'migration_009',?)",
                [(r[0], r[1], r[2], r[3], now) for r in dup_rows]
            )
            cur.execute('''
                DELETE FROM gunluk_kayit
                WHERE id NOT IN (
                    SELECT MAX(id)
                    FROM gunluk_kayit
                    GROUP BY tarih, ad_soyad, COALESCE(tersane_id, -1)
                )
            ''')

        # Mevcut sütun listesini al
        cur.execute("PRAGMA table_info(gunluk_kayit)")
        existing_cols = [r[1] for r in cur.fetchall()]

        # Yeni tablo: inline UNIQUE yok; tersane_id/firma_id/manuel_kilit dahil
        cur.execute('''CREATE TABLE gunluk_kayit_new (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            tarih             TEXT,
            ad_soyad          TEXT,
            giris_saati       TEXT,
            cikis_saati       TEXT,
            kayip_sure_saat   TEXT,
            hesaplanan_normal REAL,
            hesaplanan_mesai  REAL,
            aciklama          TEXT,
            import_batch_id   TEXT DEFAULT NULL,
            tersane_id        INTEGER,
            firma_id          INTEGER,
            manuel_kilit      INTEGER DEFAULT 0
        )''')

        # Her iki tabloda da mevcut olan sütunları kopyala
        target_cols = [
            'id', 'tarih', 'ad_soyad', 'giris_saati', 'cikis_saati',
            'kayip_sure_saat', 'hesaplanan_normal', 'hesaplanan_mesai',
            'aciklama', 'import_batch_id', 'tersane_id', 'firma_id', 'manuel_kilit'
        ]
        copy_cols = [c for c in target_cols if c in existing_cols]
        col_sql = ', '.join(copy_cols)
        cur.execute(
            f"INSERT INTO gunluk_kayit_new ({col_sql}) SELECT {col_sql} FROM gunluk_kayit"
        )

        cur.execute("DROP TABLE gunluk_kayit")
        cur.execute("ALTER TABLE gunluk_kayit_new RENAME TO gunluk_kayit")

    # Expression index — hem eski hem yeni DB için
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_gunluk_unique_tersane "
        "ON gunluk_kayit(tarih, ad_soyad, COALESCE(tersane_id, -1))"
    )
    conn.commit()


# Ordered list of migrations
MIGRATIONS = [
    migration_001_add_phone_to_personel,
    migration_002_index_gunluk_tarih,
    migration_003_ensure_mesai_katsayilari_schema,
    migration_004_ensure_yevmiye_katsayilari_schema,
    migration_005_convert_rules_to_exit_time,
    migration_006_enforce_avans_kesinti_constraints,
    migration_007_upload_batch_infra,
    migration_008_ensure_import_batch_id,
    migration_009_gunluk_kayit_unique_tersane_id,
]
