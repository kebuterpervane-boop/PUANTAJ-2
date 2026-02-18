import sqlite3
import shutil
import os
import re
import threading
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from core.hesaplama import hesapla_hakedis, NORMAL_GUNLUK_SAAT
import migrations
try:
    import bcrypt
except Exception:
    bcrypt = None

def get_default_db_path():
    appdata = os.getenv('APPDATA') or os.path.expanduser('~/.config')
    path = Path(appdata) / "SaralGroup" / "PuantajApp"
    path.mkdir(parents=True, exist_ok=True)
    return path / "puantaj.db"

def relocate_old_db_if_present(target_db):
    possible_old = [
        Path.cwd() / "puantaj.db",
        Path.home() / "puantaj.db",
        Path(__file__).parent / "puantaj.db",
    ]
    if target_db.exists(): return False
    for p in possible_old:
        if p.exists():
            bak = target_db.with_suffix('.orig.' + datetime.now().strftime("%Y%m%d%H%M") + '.db')
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, bak)
            shutil.copy2(p, target_db)
            return True
    return False

def _is_bcrypt_hash(value):
    if value is None:
        return False
    if isinstance(value, bytes):
        try:
            value = value.decode('utf-8')
        except Exception:
            return False
    return isinstance(value, str) and value.startswith(('$2a$', '$2b$', '$2y$'))

def _check_bcrypt(password, hashed):
    if bcrypt is None or password is None:
        return False
    try:
        pw_bytes = str(password).encode('utf-8')
        if isinstance(hashed, bytes):
            hash_bytes = hashed
        else:
            hash_bytes = str(hashed).encode('utf-8')
        return bcrypt.checkpw(pw_bytes, hash_bytes)
    except Exception:
        return False

class Database:
    _init_lock = threading.RLock()  # WHY: protect one-time schema bootstrap from concurrent page/thread starts.
    _initialized_db_files = set()  # WHY: avoid rerunning heavy init/migrations for same DB in one process.
    # --- AY KİLİT API ---
    def is_month_locked(self, year, month, firma_id):
        from migrations.ay_kilit import AyKilitDB
        kilit_db = AyKilitDB(self.db_file)
        return kilit_db.is_month_locked(year, month, firma_id)

    def lock_month(self, year, month, firma_id):
        from migrations.ay_kilit import AyKilitDB
        kilit_db = AyKilitDB(self.db_file)
        kilit_db.lock_month(year, month, firma_id)

    def unlock_month(self, year, month, firma_id):
        from migrations.ay_kilit import AyKilitDB  # WHY: match existing import style and fix ModuleNotFoundError without changing logic.
        kilit_db = AyKilitDB(self.db_file)  # WHY: keep same DB wrapper usage for month unlock.
        kilit_db.unlock_month(year, month, firma_id)  # WHY: preserve original unlock behavior; only import fixed.


    def bulk_update_hakedis(self, updates):
        """
        updates: List of (normal, mesai, aciklama, rec_id)
        """
        with self.get_connection() as conn:
            conn.executemany(
                "UPDATE gunluk_kayit SET hesaplanan_normal=?, hesaplanan_mesai=?, aciklama=? WHERE id=?",
                updates
            )
            conn.commit()

    def __init__(self, db_file=None, use_cache=True):
        if db_file is None:
            db_file = str(get_default_db_path())
            relocate_old_db_if_present(Path(db_file))
        self.db_file = str(db_file)
        self.current_firma_id = 1  # Varsayılan firma ID.
        self._use_cache = use_cache  # WHY: worker threads create DB(use_cache=False) to avoid shared cache mutation.
        self._mem_cache = {  # WHY: instance-level cache prevents cross-thread cache sharing.
            'settings_cache': {},
            'personnel_list': {},
        }
        self._ensure_schema_initialized()

    @staticmethod
    def _db_init_key(db_file):
        """Returns normalized key for one-time init; None means always initialize."""
        raw = str(db_file or "").strip()
        lower = raw.lower()
        if lower == ":memory:" or lower.startswith("file::memory:"):
            return None  # SAFE: in-memory DBs are per-connection; do not share init cache.
        if not raw:
            return None
        return os.path.abspath(raw)

    def _ensure_schema_initialized(self):
        """Run schema/migration bootstrap once per DB path for the current process."""
        init_key = self._db_init_key(self.db_file)
        if init_key is None:
            self._initialize_schema()
            return
        cls = type(self)
        with cls._init_lock:
            if init_key in cls._initialized_db_files:
                return
            self._initialize_schema()
            cls._initialized_db_files.add(init_key)

    def _initialize_schema(self):
        self.init_db()
        self.ensure_company_schema()
        self.ensure_tersane_schema()
        self.ensure_tersane_rules_schema()  # NEW: add per-tersane rule storage while keeping old global behavior intact.
        self.ensure_trash_schema()  # NEW: keep trash tables aligned with daily record schema.
        self.ensure_izin_backup_schema()  # NEW: keep pre-leave snapshot for safe leave delete/restore.

    def _cache_key(self, tersane_id):
        """Cache anahtarı için tersane_id normalize edilir."""
        # WHY: ensures 0/None map to same cache bucket without touching business logic.
        try:
            return int(tersane_id or 0)
        except Exception:
            return 0

    def _get_cached(self, group, tersane_id):
        """Cache'den okuma (yoksa None)."""
        # WHY: centralizes cache access to keep behavior consistent.
        if not self._use_cache:
            return None  # WHY: worker DB instances skip cache to avoid sharing mutable state.
        key = self._cache_key(tersane_id)
        return self._mem_cache.get(group, {}).get(key)

    def _set_cached(self, group, tersane_id, value):
        """Cache'e yazma."""
        # WHY: centralizes cache write to avoid repetitive dict handling.
        if not self._use_cache:
            return  # WHY: worker DB instances do not populate cache.
        key = self._cache_key(tersane_id)
        self._mem_cache.setdefault(group, {})[key] = value

    def _invalidate_cache(self, groups=None, tersane_id=None):
        """Cache'i temizler (grup ve/veya tersane bazlı)."""
        # WHY: keep cached reads consistent after writes without changing any DB schema/logic.
        key = self._cache_key(tersane_id)
        targets = groups or ['settings_cache', 'personnel_list']
        for g in targets:
            try:
                if tersane_id is None:
                    self._mem_cache.get(g, {}).clear()
                else:
                    self._mem_cache.get(g, {}).pop(key, None)
            except Exception:
                pass  # SAFEGUARD: cache invalidation must never crash the app.


    def ensure_tersane_schema(self):
        """Tersane tablosu ve ilişkili sütunları oluşturur."""
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS tersane (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ad TEXT UNIQUE NOT NULL,
                en_gec_giris TEXT DEFAULT '08:20',
                en_erken_cikis TEXT DEFAULT '17:00',
                erken_cikis_limit TEXT DEFAULT '16:30',
                mesai_baslangic TEXT DEFAULT '17:30',
                vardiya_limit TEXT DEFAULT '19:30',
                aktif INTEGER DEFAULT 1
            )''')
            # Eski tersane tablosu varsa eksik sütunları ekle (migration)
            c.execute("PRAGMA table_info(tersane)")
            tersane_cols = [r[1] for r in c.fetchall()]
            tersane_yeni_sutunlar = {
                'en_gec_giris': "TEXT DEFAULT '08:20'",
                'en_erken_cikis': "TEXT DEFAULT '17:00'",
                'erken_cikis_limit': "TEXT DEFAULT '16:30'",
                'mesai_baslangic': "TEXT DEFAULT '17:30'",
                'vardiya_limit': "TEXT DEFAULT '19:30'",
            }
            for col, defn in tersane_yeni_sutunlar.items():
                if col not in tersane_cols:
                    try:
                        c.execute(f"ALTER TABLE tersane ADD COLUMN {col} {defn}")
                    except Exception:
                        pass
            # Eski 'aciklama' sütunu varsa kaldıramayız (SQLite DROP COLUMN yok),
            # ama sorun değil - sadece kullanılmaz.
            # Varsayılan tersane ekle
            c.execute("INSERT OR IGNORE INTO tersane (ad) VALUES ('Varsayılan Tersane')")
            # personel tablosuna tersane_id ekle
            c.execute("PRAGMA table_info(personel)")
            personel_cols = [r[1] for r in c.fetchall()]
            if 'tersane_id' not in personel_cols:
                c.execute("ALTER TABLE personel ADD COLUMN tersane_id INTEGER")
            # gunluk_kayit tablosuna tersane_id ekle
            c.execute("PRAGMA table_info(gunluk_kayit)")
            kayit_cols = [r[1] for r in c.fetchall()]
            if 'tersane_id' not in kayit_cols:
                c.execute("ALTER TABLE gunluk_kayit ADD COLUMN tersane_id INTEGER")
            # Eski tersanelerin NULL saat değerlerini varsayılanla doldur
            c.execute("UPDATE tersane SET en_gec_giris='08:20' WHERE en_gec_giris IS NULL")
            c.execute("UPDATE tersane SET en_erken_cikis='17:00' WHERE en_erken_cikis IS NULL")
            c.execute("UPDATE tersane SET erken_cikis_limit='16:30' WHERE erken_cikis_limit IS NULL")
            c.execute("UPDATE tersane SET mesai_baslangic='17:30' WHERE mesai_baslangic IS NULL")
            c.execute("UPDATE tersane SET vardiya_limit='19:30' WHERE vardiya_limit IS NULL")
            # NULL tersane_id'leri varsayılan tersaneye set et
            c.execute("SELECT id FROM tersane WHERE ad='Varsayılan Tersane'")
            default_tersane = c.fetchone()
            if default_tersane:
                c.execute("UPDATE personel SET tersane_id=? WHERE tersane_id IS NULL", (default_tersane[0],))
                c.execute("UPDATE gunluk_kayit SET tersane_id=? WHERE tersane_id IS NULL", (default_tersane[0],))
            conn.commit()

    def ensure_tersane_rules_schema(self):
        """Per-tersane katsayı ve ayar tablolarını/kolonlarını güvenli şekilde ekler."""
        # NEW: keep compatibility by only adding missing structures (no renames, no drops).
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                # NEW: per-shipyard settings table (global settings table stays intact).
                try:
                    c.execute('''CREATE TABLE IF NOT EXISTS tersane_ayarlar (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tersane_id INTEGER NOT NULL,
                        key TEXT NOT NULL,
                        value TEXT,
                        UNIQUE(tersane_id, key)
                    )''')
                except Exception:
                    pass  # SAFEGUARD: keep app running if table creation fails.

                # NEW: add tersane_id columns to katsayi tables if missing (non-breaking).
                for table_name in ("mesai_katsayilari", "yevmiye_katsayilari"):
                    try:
                        c.execute(f"PRAGMA table_info({table_name})")
                        cols = [r[1] for r in c.fetchall()]
                        if 'tersane_id' not in cols:
                            c.execute(f"ALTER TABLE {table_name} ADD COLUMN tersane_id INTEGER")
                    except Exception:
                        pass  # SAFEGUARD: avoid crashing on legacy DBs.

                conn.commit()
        except Exception:
            pass  # SAFEGUARD: schema check is best-effort.

    # --- TERSANE YÖNETİM FONKSİYONLARI ---

    def ensure_trash_schema(self):
        """Trash tablolarini gunluk_kayit semasiyla uyumlu tutar."""
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                # gunluk_kayit_trash: yeni kolonlar ekle
                try:
                    c.execute("PRAGMA table_info(gunluk_kayit_trash)")
                    cols = [r[1] for r in c.fetchall()]
                    new_cols = {
                        'tersane_id': 'INTEGER',
                        'firma_id': 'INTEGER',
                        'manuel_kilit': 'INTEGER DEFAULT 0'
                    }
                    for col, defn in new_cols.items():
                        if col not in cols:
                            try:
                                c.execute(f"ALTER TABLE gunluk_kayit_trash ADD COLUMN {col} {defn}")
                            except Exception:
                                pass
                except Exception:
                    pass

                # Varsayilan degerleri geriye donuk doldur (NULL kalmasin)
                try:
                    c.execute("SELECT id FROM tersane WHERE ad='Varsayılan Tersane'")
                    row = c.fetchone()
                    if row:
                        c.execute("UPDATE gunluk_kayit_trash SET tersane_id=? WHERE tersane_id IS NULL", (row[0],))
                except Exception:
                    pass

                try:
                    c.execute("SELECT id FROM firma WHERE ad='GENEL'")
                    row = c.fetchone()
                    if row:
                        c.execute("UPDATE gunluk_kayit_trash SET firma_id=? WHERE firma_id IS NULL", (row[0],))
                except Exception:
                    pass

                try:
                    c.execute("UPDATE gunluk_kayit_trash SET manuel_kilit=0 WHERE manuel_kilit IS NULL")
                except Exception:
                    pass

                conn.commit()
        except Exception:
            pass  # SAFEGUARD: schema check is best-effort.

    def ensure_izin_backup_schema(self):
        """Izin otomatik kayitlari icin eski satir snapshot tablosunu olusturur."""
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                c.execute('''CREATE TABLE IF NOT EXISTS izin_auto_kayit_backup (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    izin_id INTEGER NOT NULL,
                    tarih TEXT NOT NULL,
                    ad_soyad TEXT NOT NULL,
                    prev_exists INTEGER DEFAULT 0,
                    prev_giris_saati TEXT,
                    prev_cikis_saati TEXT,
                    prev_kayip_sure_saat TEXT,
                    prev_hesaplanan_normal REAL,
                    prev_hesaplanan_mesai REAL,
                    prev_aciklama TEXT,
                    prev_tersane_id INTEGER,
                    prev_firma_id INTEGER,
                    prev_manuel_kilit INTEGER DEFAULT 0,
                    UNIQUE(izin_id, tarih, ad_soyad)
                )''')
                conn.commit()
        except Exception:
            pass  # SAFEGUARD: backup schema is best-effort and must not break app startup.

    def get_tersaneler(self):
        """Aktif tersaneleri döndürür."""
        with self.get_connection() as conn:
            return conn.execute(
                "SELECT id, ad, en_gec_giris, en_erken_cikis, erken_cikis_limit, mesai_baslangic, vardiya_limit FROM tersane WHERE COALESCE(aktif,1)=1 ORDER BY ad"
            ).fetchall()

    def get_tersane(self, tersane_id):
        """Tek bir tersanenin ayarlarını döndürür."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT id, ad, en_gec_giris, en_erken_cikis, erken_cikis_limit, mesai_baslangic, vardiya_limit FROM tersane WHERE id=?",
                (tersane_id,)
            ).fetchone()
            if not row:
                return None
            return {
                'id': row[0], 'ad': row[1],
                'en_gec_giris': row[2], 'en_erken_cikis': row[3],
                'erken_cikis_limit': row[4], 'mesai_baslangic': row[5],
                'vardiya_limit': row[6]
            }

    def add_tersane(self, ad, en_gec_giris="08:20", en_erken_cikis="17:00",
                    erken_cikis_limit="16:30", mesai_baslangic="17:30", vardiya_limit="19:30"):
        """Yeni tersane ekler, id'sini döndürür."""
        with self.get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tersane (ad, en_gec_giris, en_erken_cikis, erken_cikis_limit, mesai_baslangic, vardiya_limit) VALUES (?,?,?,?,?,?)",
                (ad, en_gec_giris, en_erken_cikis, erken_cikis_limit, mesai_baslangic, vardiya_limit)
            )
            conn.commit()
            row = conn.execute("SELECT id FROM tersane WHERE ad=?", (ad,)).fetchone()
            return row[0] if row else None

    def update_tersane(self, tersane_id, ad, en_gec_giris, en_erken_cikis,
                       erken_cikis_limit, mesai_baslangic, vardiya_limit):
        """Tersane ayarlarını günceller."""
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE tersane SET ad=?, en_gec_giris=?, en_erken_cikis=?, erken_cikis_limit=?, mesai_baslangic=?, vardiya_limit=? WHERE id=?",
                (ad, en_gec_giris, en_erken_cikis, erken_cikis_limit, mesai_baslangic, vardiya_limit, tersane_id)
            )
            conn.commit()
        self._invalidate_cache(groups=['settings_cache'], tersane_id=tersane_id)  # WHY: tersane saatleri degisti, cache tazelenmeli.

    def delete_tersane(self, tersane_id):
        """Tersaneyi pasif yapar (soft delete)."""
        with self.get_connection() as conn:
            conn.execute("UPDATE tersane SET aktif=0 WHERE id=?", (tersane_id,))
            conn.commit()
        self._invalidate_cache(groups=['settings_cache'], tersane_id=tersane_id)  # WHY: tersane durumu degisti, cache tazelenmeli.

    def get_tersane_ayarlari_for_hesaplama(self, tersane_id):
        """Hesaplama motoru için tersane saat ayarlarını dakika cinsinden döndürür."""
        tersane = self.get_tersane(tersane_id)
        # Cuma toleransı: tersane ayarından oku (saat → dakika)
        try:
            friday_h = float(self.get_tersane_setting("friday_loss_tolerance_hours", 1.0, tersane_id, fallback_global=True))
        except (ValueError, TypeError):
            friday_h = 1.0
        cuma_dk = max(0, int(friday_h * 60))

        if not tersane:
            # Varsayılan değerler
            return {
                'sabah_tolerans_dk': 8 * 60 + 20,
                'aksam_referans_dk': 17 * 60,
                'erken_cikis_limit_dk': 16 * 60 + 30,
                'tolerans_limiti_dk': 17 * 60 + 30,
                'vardiya_limiti_dk': 19 * 60 + 30,
                'cuma_kayip_tolerans_dk': cuma_dk,  # WHY: settings'ten gelen Cuma toleransı.
            }
        def time_to_minutes(t_str, default_dk):
            try:
                parts = t_str.split(":")
                return int(parts[0]) * 60 + int(parts[1])
            except (ValueError, AttributeError, IndexError):
                return default_dk
        return {
            'sabah_tolerans_dk': time_to_minutes(tersane['en_gec_giris'], 500),
            'aksam_referans_dk': time_to_minutes(tersane['en_erken_cikis'], 1020),
            'erken_cikis_limit_dk': time_to_minutes(tersane['erken_cikis_limit'], 990),
            'tolerans_limiti_dk': time_to_minutes(tersane['mesai_baslangic'], 1050),
            'vardiya_limiti_dk': time_to_minutes(tersane['vardiya_limit'], 1170),
            'cuma_kayip_tolerans_dk': cuma_dk,  # WHY: tersane bazlı Cuma kayıp toleransı (dakika).
        }

    def ensure_company_schema(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            # 1) Firma tablosu
            c.execute('''CREATE TABLE IF NOT EXISTS firma (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ad TEXT UNIQUE NOT NULL
            )''')
            # 2) GENEL firmasını ekle
            c.execute("INSERT OR IGNORE INTO firma (ad) VALUES ('GENEL')")
            c.execute("SELECT id FROM firma WHERE ad='GENEL'")
            genel_id = c.fetchone()[0]
            # 3) personel tablosuna firma_id ekle
            c.execute("PRAGMA table_info(personel)")
            personel_cols = [r[1] for r in c.fetchall()]
            if 'firma_id' not in personel_cols:
                c.execute("ALTER TABLE personel ADD COLUMN firma_id INTEGER")
            # 4) gunluk_kayit tablosuna firma_id ekle
            c.execute("PRAGMA table_info(gunluk_kayit)")
            kayit_cols = [r[1] for r in c.fetchall()]
            if 'firma_id' not in kayit_cols:
                c.execute("ALTER TABLE gunluk_kayit ADD COLUMN firma_id INTEGER")
            # 5) gunluk_kayit tablosuna manuel_kilit ekle (varsa ekleme)
            c.execute("PRAGMA table_info(gunluk_kayit)")
            kayit_cols = [r[1] for r in c.fetchall()]
            if 'manuel_kilit' not in kayit_cols:
                c.execute("ALTER TABLE gunluk_kayit ADD COLUMN manuel_kilit INTEGER DEFAULT 0")
            # 6) NULL firma_id'leri GENEL'e set et
            c.execute("UPDATE personel SET firma_id=? WHERE firma_id IS NULL", (genel_id,))
            c.execute("UPDATE gunluk_kayit SET firma_id=? WHERE firma_id IS NULL", (genel_id,))
            # 7) firma tablosuna aktif kolonu ekle (yoksa)
            c.execute("PRAGMA table_info(firma)")
            firma_cols = [r[1] for r in c.fetchall()]
            if 'aktif' not in firma_cols:
                c.execute("ALTER TABLE firma ADD COLUMN aktif INTEGER DEFAULT 1")
            conn.commit()


    def get_firmalar(self):
        """Aktif firmalari dondurur. aktif kolonu yoksa tum firmalari dondurur."""
        with self.get_connection() as conn:
            try:
                return conn.execute("SELECT id, ad FROM firma WHERE COALESCE(aktif,1)=1 ORDER BY ad").fetchall()
            except Exception:
                return conn.execute("SELECT id, ad FROM firma ORDER BY ad").fetchall()


    def add_firma(self, ad):
        """Firma ekler, varsa ignore eder. Eklenen/mevcut firma id'sini dondurur."""
        ad = ad.strip()
        if not ad:
            return None
        with self.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO firma (ad, aktif) VALUES (?, 1)", (ad,))
            conn.commit()
            row = conn.execute("SELECT id FROM firma WHERE ad=?", (ad,)).fetchone()
            return row[0] if row else None


    def get_connection(self):
        return sqlite3.connect(self.db_file)


    def init_db(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            
            # --- TABLO TANIMLARI ---
            tables = {
                "personel": '''CREATE TABLE IF NOT EXISTS personel (
                                ad_soyad TEXT PRIMARY KEY, maas REAL, ekip_adi TEXT,
                                ozel_durum TEXT, ekstra_odeme REAL DEFAULT 0.0, 
                                yillik_izin_hakki REAL DEFAULT 0.0,
                                ise_baslangic TEXT, cikis_tarihi TEXT, 
                                ekstra_odeme_not TEXT, avans_not TEXT,
                                yevmiyeci_mi INTEGER DEFAULT 0, phone TEXT DEFAULT '')''',
                "gunluk_kayit": '''CREATE TABLE IF NOT EXISTS gunluk_kayit (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    tarih TEXT, ad_soyad TEXT, giris_saati TEXT, cikis_saati TEXT,
                                    kayip_sure_saat TEXT, hesaplanan_normal REAL, hesaplanan_mesai REAL, 
                                    aciklama TEXT, UNIQUE(tarih, ad_soyad))''',
                "resmi_tatiller": '''CREATE TABLE IF NOT EXISTS resmi_tatiller (
                                        tarih TEXT PRIMARY KEY, tur TEXT, normal_saat REAL, 
                                        mesai_saat REAL, aciklama TEXT)''',
                "avans_kesinti": '''CREATE TABLE IF NOT EXISTS avans_kesinti (
                                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                                        tarih TEXT, ad_soyad TEXT,
                                        tur TEXT NOT NULL CHECK(tur IN ('Avans', 'Kesinti')),
                                        tutar REAL NOT NULL CHECK(tutar >= 0),
                                        aciklama TEXT)''',
                "settings": '''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''',
                # NEW: tersane_id column keeps old global rows (NULL) and enables per-shipyard rows.
                "mesai_katsayilari": '''CREATE TABLE IF NOT EXISTS mesai_katsayilari (
                                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                                        saat_araligi_baslangic REAL, saat_araligi_bitis REAL,
                                        tersane_id INTEGER, katsayi REAL, aciklama TEXT)''',
                # NEW: tersane_id column keeps old global rows (NULL) and enables per-shipyard rows.
                "yevmiye_katsayilari": '''CREATE TABLE IF NOT EXISTS yevmiye_katsayilari (
                                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                                            saat_araligi_baslangic REAL, saat_araligi_bitis REAL,
                                            tersane_id INTEGER, yevmiye_katsayi REAL, aciklama TEXT)''',
                # NEW: shipyard-specific settings table; does not replace global settings table.
                "tersane_ayarlar": '''CREATE TABLE IF NOT EXISTS tersane_ayarlar (
                                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                                        tersane_id INTEGER NOT NULL,
                                        key TEXT NOT NULL,
                                        value TEXT,
                                        UNIQUE(tersane_id, key))''',
                "bes_personel": '''CREATE TABLE IF NOT EXISTS bes_personel (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT, ad_soyad TEXT UNIQUE,
                                    devam_ediyor INTEGER DEFAULT 1, gunluk_bes_fiyati REAL DEFAULT 20.0,
                                    created_at TEXT DEFAULT CURRENT_TIMESTAMP)''',
                "bes_hesaplama": '''CREATE TABLE IF NOT EXISTS bes_hesaplama (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT, ay_yil TEXT, ad_soyad TEXT,
                                    calisilan_gun_sayisi INTEGER DEFAULT 0, gunluk_tutar REAL DEFAULT 20.0,
                                    aylik_bes_tutari REAL DEFAULT 0.0, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                                    UNIQUE(ay_yil, ad_soyad))''',
                "izin_takip": '''CREATE TABLE IF NOT EXISTS izin_takip (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT, ad_soyad TEXT, izin_tarihi TEXT,
                                    izin_turu TEXT, gun_sayisi REAL DEFAULT 1.0, aciklama TEXT,
                                    onay_durumu INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''',
                "vardiya": '''CREATE TABLE IF NOT EXISTS vardiya (
                                id INTEGER PRIMARY KEY AUTOINCREMENT, vardiya_adi TEXT UNIQUE,
                                baslangic_saati TEXT, bitis_saati TEXT, normal_saat REAL DEFAULT 8.0)''',
                "personel_vardiya": '''CREATE TABLE IF NOT EXISTS personel_vardiya (
                                        id INTEGER PRIMARY KEY AUTOINCREMENT, ad_soyad TEXT,
                                        vardiya_id INTEGER, atama_tarihi TEXT,
                                        UNIQUE(ad_soyad, vardiya_id), FOREIGN KEY(vardiya_id) REFERENCES vardiya(id))''',
                "izin_tur_ayarlari": '''CREATE TABLE IF NOT EXISTS izin_tur_ayarlari (
                                        id INTEGER PRIMARY KEY AUTOINCREMENT, izin_turu TEXT UNIQUE,
                                        otomatik_kayit INTEGER DEFAULT 1)''',
                "genel_ayarlar": '''CREATE TABLE IF NOT EXISTS genel_ayarlar (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, value INTEGER DEFAULT 0)''',
                "gunluk_kayit_trash": '''CREATE TABLE IF NOT EXISTS gunluk_kayit_trash (
                                            id INTEGER PRIMARY KEY AUTOINCREMENT, orig_id INTEGER, tarih TEXT, 
                                            ad_soyad TEXT, giris_saati TEXT, cikis_saati TEXT, kayip_sure_saat TEXT, 
                                            hesaplanan_normal REAL, hesaplanan_mesai REAL, aciklama TEXT,
                                            tersane_id INTEGER, firma_id INTEGER, manuel_kilit INTEGER DEFAULT 0,
                                            batch_id INTEGER, deleted_at TEXT)''',
                "avans_kesinti_trash": '''CREATE TABLE IF NOT EXISTS avans_kesinti_trash (
                                            id INTEGER PRIMARY KEY AUTOINCREMENT, orig_id INTEGER, tarih TEXT, 
                                            ad_soyad TEXT, tur TEXT, tutar REAL, aciklama TEXT,
                                            batch_id INTEGER, deleted_at TEXT)''',
                "trash_batches": '''CREATE TABLE IF NOT EXISTS trash_batches (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT, start_date TEXT, end_date TEXT, 
                                    created_at TEXT, deleted_daily INTEGER DEFAULT 0, deleted_avans INTEGER DEFAULT 0)'''
            }
            
            for sql in tables.values():
                c.execute(sql)

            # Sütun Kontrolleri
            personel_cols = {
                'ekstra_odeme': 'REAL DEFAULT 0.0', 'yillik_izin_hakki': 'REAL DEFAULT 0.0',
                'ise_baslangic': 'TEXT', 'cikis_tarihi': 'TEXT', 'ekstra_odeme_not': 'TEXT',
                'avans_not': 'TEXT', 'yevmiyeci_mi': 'INTEGER DEFAULT 0', 'phone': "TEXT DEFAULT ''"
            }
            c.execute("PRAGMA table_info(personel)")
            existing = [r[1] for r in c.fetchall()]
            for col, defin in personel_cols.items():
                if col not in existing:
                    try: c.execute(f"ALTER TABLE personel ADD COLUMN {col} {defin}")
                    except Exception as e:
                        import logging
                        logging.debug(f"Sütun eklenemedi (muhtemelen zaten var): {col}: {e}")

            # Varsayılan Ayarlar
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_username', 'admin')")
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_password', '1234')")
            defaults = [
                ('pazar_mesaisi', '15.0'), ('mesai_carpani', '1.5'),
                ('salary_basis', 'fixed_days'), ('salary_days', '30'),
                ('gunluk_bes_fiyati', '20.0'), ('mesai_baslangic_saat', '17:30'),
                ('en_erken_cikis_saat', '19:30'),
                ('calisma_hesaplama_modu', 'cezadan_dus'),
                ('ogle_molasi_baslangic', '12:15'),
                ('ogle_molasi_bitis', '13:15'),
                ('ara_mola_dk', '20'),
                ('fiili_saat_yuvarlama', 'ondalik')
            ]
            for k, v in defaults:
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            
            conn.commit()

        try:
            self.apply_migrations()
        except Exception as e:
            try:
                from core.app_logger import log_error
                log_error(f"Migration error during init_db: {e}")
            except Exception:
                pass
            raise RuntimeError(f"Migration error during init_db: {e}") from e

        try:
            self.init_resmi_tatiller()
        except Exception as e:
            try:
                from core.app_logger import log_error
                log_error(f"Resmi tatil seed/init hatasi: {e}")
            except Exception:
                pass

    # --- GÜVENLİK VE AYARLAR ---

    def check_login(self, username, password):
        real_user = self.get_setting("admin_username", "admin")
        if str(username).strip() != str(real_user).strip():
            return False
        real_pass = self.get_setting("admin_password", "1234")
        if _is_bcrypt_hash(real_pass):
            return _check_bcrypt(password, real_pass)
        return str(password) == str(real_pass)

    def set_admin_credentials(self, username=None, password=None):
        if username is not None:
            self.update_setting("admin_username", str(username).strip())
        if password is not None:
            pw_str = str(password)
            if bcrypt is not None:
                try:
                    hashed = bcrypt.hashpw(pw_str.encode('utf-8'), bcrypt.gensalt())
                    pw_str = hashed.decode('utf-8') if isinstance(hashed, (bytes, bytearray)) else str(hashed)
                except Exception:
                    pw_str = str(password)
            self.update_setting("admin_password", pw_str)


    def get_shipyard_rules(self, tersane_id=None, fallback_global=True):
        """Aktif tersane için kural sözlüğü üretir (shipyard_rules)."""
        # NEW: centralize rule building to keep DRY while preserving old defaults.
        try:
            if tersane_id and tersane_id > 0:
                rules = {
                    # NEW: per-shipyard katsayılar (fallback_global keeps old behavior if empty).
                    'mesai_katsayilari': self.get_mesai_katsayilari(tersane_id=tersane_id, fallback_global=fallback_global),
                    'yevmiye_katsayilari': self.get_yevmiye_katsayilari(tersane_id=tersane_id, fallback_global=fallback_global),
                    # NEW: per-shipyard time rules (fallback_global keeps old behavior if unset).
                    'mesai_baslangic_saat': self.get_tersane_setting("mesai_baslangic_saat", "17:30", tersane_id, fallback_global=fallback_global),
                    'en_erken_cikis_saat': self.get_tersane_setting("en_erken_cikis_saat", "19:30", tersane_id, fallback_global=fallback_global),
                    'pazar_mesaisi': self.get_tersane_setting("pazar_mesaisi", "15.0", tersane_id, fallback_global=fallback_global),
                    'calisma_hesaplama_modu': self.get_tersane_setting("calisma_hesaplama_modu", "cezadan_dus", tersane_id, fallback_global=fallback_global),
                    'ogle_molasi_baslangic': self.get_tersane_setting("ogle_molasi_baslangic", "12:15", tersane_id, fallback_global=fallback_global),
                    'ogle_molasi_bitis': self.get_tersane_setting("ogle_molasi_bitis", "13:15", tersane_id, fallback_global=fallback_global),
                    'ara_mola_dk': self.get_tersane_setting("ara_mola_dk", "20", tersane_id, fallback_global=fallback_global),
                    'fiili_saat_yuvarlama': self.get_tersane_setting("fiili_saat_yuvarlama", "ondalik", tersane_id, fallback_global=fallback_global),
                    'friday_loss_tolerance_hours': self.get_tersane_setting("friday_loss_tolerance_hours", "1.0", tersane_id, fallback_global=fallback_global),  # WHY: tersane bazlı Cuma toleransı (saat).
                }
                # NEW: tersane saatleri hesaplama motoru için eklenir.
                rules['tersane_saatleri'] = self.get_tersane_ayarlari_for_hesaplama(tersane_id)
                return rules
            # GLOBAL fallback (old behavior)
            return {
                'mesai_katsayilari': self.get_mesai_katsayilari(),
                'yevmiye_katsayilari': self.get_yevmiye_katsayilari(),
                'mesai_baslangic_saat': self.get_setting("mesai_baslangic_saat", "17:30"),
                'en_erken_cikis_saat': self.get_setting("en_erken_cikis_saat", "19:30"),
                'pazar_mesaisi': self.get_setting("pazar_mesaisi", "15.0"),
                'calisma_hesaplama_modu': self.get_setting("calisma_hesaplama_modu", "cezadan_dus"),
                'ogle_molasi_baslangic': self.get_setting("ogle_molasi_baslangic", "12:15"),
                'ogle_molasi_bitis': self.get_setting("ogle_molasi_bitis", "13:15"),
                'ara_mola_dk': self.get_setting("ara_mola_dk", "20"),
                'fiili_saat_yuvarlama': self.get_setting("fiili_saat_yuvarlama", "ondalik"),
                'friday_loss_tolerance_hours': self.get_setting("friday_loss_tolerance_hours", "1.0"),  # WHY: global Cuma toleransı (saat).
            }
        except Exception:
            # SAFEGUARD: return minimal defaults if something goes wrong.
            return {
                'mesai_katsayilari': [],
                'yevmiye_katsayilari': [],
                'mesai_baslangic_saat': "17:30",
                'en_erken_cikis_saat': "19:30",
                'pazar_mesaisi': "15.0",
                'calisma_hesaplama_modu': "cezadan_dus",
                'ogle_molasi_baslangic': "12:15",
                'ogle_molasi_bitis': "13:15",
                'ara_mola_dk': "20",
                'fiili_saat_yuvarlama': "ondalik",
                'friday_loss_tolerance_hours': "1.0",
            }

    def get_settings_cache(self, tersane_id=None, use_cache=True):
        # NEW: expose shipyard_rules while keeping old cache keys intact.
        if use_cache:
            cached = self._get_cached('settings_cache', tersane_id)  # WHY: avoid repeated DB reads in UI loops.
            if cached:
                return dict(cached)  # SAFE: shallow copy prevents accidental mutation of cached dict.
        rules = self.get_shipyard_rules(tersane_id=tersane_id, fallback_global=True)
        cache = dict(rules)  # SAFE: copy to keep original keys for legacy callers.
        cache['shipyard_rules'] = rules  # NEW: explicit shipyard_rules dict for dynamic rule access.
        if use_cache:
            self._set_cached('settings_cache', tersane_id, dict(cache))  # WHY: store once per tersane for fast reuse.
        return cache

    # --- TATİL YÖNETİMİ (EKSİK OLAN KISIMLAR) ---

    def add_holiday(self, tarih, tur, normal_saat, mesai_saat, aciklama):
        try:
            dt = datetime.strptime(tarih, "%Y-%m-%d")
            tarih_key = dt.strftime("%m-%d")
        except Exception:
            tarih_key = tarih
        with self.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO resmi_tatiller (tarih, tur, normal_saat, mesai_saat, aciklama) VALUES (?, ?, ?, ?, ?)",
                            (tarih_key, tur, normal_saat, mesai_saat, aciklama))
            conn.commit()
        self.update_records_for_holiday(tarih_key)


    def get_all_holidays(self):
        with self.get_connection() as conn:
            return conn.execute("SELECT tarih, tur, normal_saat, mesai_saat, aciklama FROM resmi_tatiller ORDER BY tarih").fetchall()


    def get_holiday_info(self, tarih):
        try: dt = datetime.strptime(tarih, "%Y-%m-%d"); tarih_key = dt.strftime("%m-%d")
        except Exception: tarih_key = tarih
        with self.get_connection() as conn:
            return conn.execute("SELECT tur, normal_saat, mesai_saat FROM resmi_tatiller WHERE tarih=?", (tarih_key,)).fetchone()


    def delete_holiday(self, tarih):
        with self.get_connection() as conn:
            row = conn.execute("SELECT 1 FROM resmi_tatiller WHERE tarih=?", (tarih,)).fetchone()
            if row:
                delete_key = tarih
            else:
                try:
                    dt = datetime.strptime(tarih, "%Y-%m-%d")
                    delete_key = dt.strftime("%m-%d")
                except Exception:
                    delete_key = tarih
            conn.execute("DELETE FROM resmi_tatiller WHERE tarih=?", (delete_key,))
            conn.commit()
        self.update_records_for_holiday(delete_key)
        

    def get_holidays(self):
        with self.get_connection() as conn:
            return {row[0] for row in conn.execute("SELECT tarih FROM resmi_tatiller").fetchall()}


    def init_resmi_tatiller(self):
        with self.get_connection() as conn:
            existing = conn.execute("SELECT COUNT(1) FROM resmi_tatiller").fetchone()[0]
            if existing and existing > 0:
                return
        self.seed_default_holidays()

    def seed_default_holidays(self):
        tatiller = {
            "01-01": "Yılbaşı",
            "04-23": "Milli İrade",
            "05-01": "Emek Günü",
            "05-19": "Gençlik Spor",
            "07-15": "Demokrasi",
            "08-30": "Zafer",
            "10-29": "Cumhuriyet",
        }
        with self.get_connection() as conn:
            for d, t in tatiller.items():
                conn.execute(
                    "INSERT OR IGNORE INTO resmi_tatiller (tarih, tur, normal_saat, mesai_saat, aciklama) VALUES (?, ?, ?, ?, ?)",
                    (d, "Resmi Tatil", 7.5, 0, t),
                )
            conn.commit()

    # --- PERFORMANS VE HESAPLAMA ---

    def update_records_for_holiday(self, tarih):
        # NEW: per-tersane settings_cache to avoid cross-shipyard mixing.
        settings_cache_by_tersane = {}  # SAFE: memoize by tersane_id to keep performance.
        with self.get_connection() as conn:
            c = conn.cursor()
            sql = "SELECT id, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, tarih FROM gunluk_kayit WHERE "
            if len(tarih) == 5 and tarih[2] == "-": sql += "substr(tarih,6,5)=?"
            else: sql += "tarih=?"
            sql += " AND COALESCE(manuel_kilit,0)=0"
            # NEW: try to include tersane_id column; fallback to legacy schema if needed.
            try:
                rows = c.execute(sql.replace("tarih", "tarih, tersane_id", 1), (tarih,)).fetchall()
                has_tersane_col = True
            except Exception:
                rows = c.execute(sql, (tarih,)).fetchall()
                has_tersane_col = False

            for rec in rows:
                if has_tersane_col:
                    rec_id, ad, giris, cikis, kayip, rec_tarih, rec_tersane_id = rec
                else:
                    rec_id, ad, giris, cikis, kayip, rec_tarih = rec
                    rec_tersane_id = 0  # SAFE: legacy rows treated as global.
                p_res = c.execute("SELECT yevmiyeci_mi, ozel_durum FROM personel WHERE ad_soyad=?", (ad,)).fetchone()
                yevmiyeci = p_res[0] if p_res else 0
                ozel_durum = p_res[1] if p_res else None

                # NEW: choose correct settings_cache by tersane_id (0 = global).
                tid = rec_tersane_id or 0
                if tid not in settings_cache_by_tersane:
                    settings_cache_by_tersane[tid] = self.get_settings_cache(tersane_id=tid) if tid else self.get_settings_cache()
                settings_cache = settings_cache_by_tersane[tid]
                
                normal, mesai, notlar = hesapla_hakedis(
                    rec_tarih, giris, cikis, kayip, {rec_tarih},
                    self.get_holiday_info, lambda x: ozel_durum, ad, yevmiyeci, 
                    db=self, settings_cache=settings_cache.get('shipyard_rules', settings_cache) if settings_cache else None
                )
                c.execute("UPDATE gunluk_kayit SET hesaplanan_normal=?, hesaplanan_mesai=?, aciklama=? WHERE id=?", 
                            (normal, mesai, notlar, rec_id))
            conn.commit()


    def update_records_for_person(self, ad_soyad, start_date=None, end_date=None, tersane_id=None):
        # NEW: tersane_id optional; per-shipyard cache avoids mixing across shipyards.
        settings_cache_by_tersane = {}  # SAFE: memoize by tersane_id for performance.
        with self.get_connection() as conn:
            c = conn.cursor()
            # NEW: try to read person's tersane_id for fallback (keeps old behavior if missing).
            try:
                c.execute("SELECT yevmiyeci_mi, ozel_durum, tersane_id FROM personel WHERE TRIM(ad_soyad)=TRIM(?)", (ad_soyad,))
                pres = c.fetchone()
                yevmiyeci = pres[0] if pres else 0
                ozel_durum = pres[1] if pres else None
                person_tersane_id = pres[2] if pres and len(pres) > 2 else 0
            except Exception:
                c.execute("SELECT yevmiyeci_mi, ozel_durum FROM personel WHERE TRIM(ad_soyad)=TRIM(?)", (ad_soyad,))
                pres = c.fetchone()
                yevmiyeci = pres[0] if pres else 0
                ozel_durum = pres[1] if pres else None
                person_tersane_id = 0  # SAFE: legacy fallback.
            
            # NEW: try to include tersane_id in record query; fallback to legacy schema if needed.
            sql = "SELECT id, tarih, giris_saati, cikis_saati, kayip_sure_saat, tersane_id FROM gunluk_kayit WHERE TRIM(ad_soyad)=TRIM(?) AND COALESCE(manuel_kilit,0)=0"
            params = [ad_soyad]
            if start_date and end_date:
                sql += " AND tarih BETWEEN ? AND ?"
                params.extend([start_date, end_date])
            try:
                rows = c.execute(sql, tuple(params)).fetchall()
                has_tersane_col = True
            except Exception:
                rows = c.execute(sql.replace(", tersane_id", ""), tuple(params)).fetchall()
                has_tersane_col = False
                
            for rec in rows:
                if has_tersane_col:
                    rec_id, tarih, giris, cikis, kayip, rec_tersane_id = rec
                else:
                    rec_id, tarih, giris, cikis, kayip = rec
                    rec_tersane_id = 0
                holiday_info = self.get_holiday_info(tarih)
                holiday_set = {tarih} if holiday_info else set()

                # NEW: choose the most specific tersane_id available.
                effective_tid = tersane_id if (tersane_id and tersane_id > 0) else (rec_tersane_id or person_tersane_id or 0)
                if effective_tid not in settings_cache_by_tersane:
                    settings_cache_by_tersane[effective_tid] = self.get_settings_cache(tersane_id=effective_tid) if effective_tid else self.get_settings_cache()
                settings_cache = settings_cache_by_tersane[effective_tid]
                
                normal, mesai, notlar = hesapla_hakedis(
                    tarih, giris, cikis, kayip, holiday_set,
                    self.get_holiday_info, lambda x: ozel_durum, ad_soyad, yevmiyeci, 
                    db=self, settings_cache=settings_cache.get('shipyard_rules', settings_cache) if settings_cache else None
                )
                c.execute("UPDATE gunluk_kayit SET hesaplanan_normal=?, hesaplanan_mesai=?, aciklama=? WHERE id=?", 
                            (normal, mesai, notlar, rec_id))
            conn.commit()

    # --- PERSONEL VE KAYIT FONKSİYONLARI ---

    def get_all_personnel(self):
        with self.get_connection() as conn:
            return conn.execute("""SELECT ad_soyad, maas, ekip_adi, ozel_durum, ekstra_odeme, yillik_izin_hakki, ise_baslangic, cikis_tarihi, 
                            COALESCE(ekstra_odeme_not, ''), COALESCE(avans_not, ''), COALESCE(yevmiyeci_mi, 0) FROM personel ORDER BY ad_soyad""").fetchall()

    def get_cached_personnel_list(self, tersane_id=None):
        """Personel listesini RAM cache'den döndürür (tersane bazlı)."""
        # WHY: minimize repeated DB reads on tab switches without altering data logic.
        cached = self._get_cached('personnel_list', tersane_id)  # WHY: use tersane-scoped cache when available.
        if cached is not None and len(cached) > 0:
            return list(cached)  # SAFE: return a copy to avoid external mutation.
        if cached is not None and len(cached) == 0:
            try:
                with self.get_connection() as conn:
                    if tersane_id and tersane_id > 0:
                        count = conn.execute("SELECT COUNT(1) FROM personel WHERE tersane_id = ?", (tersane_id,)).fetchone()[0]  # WHY: verify empty cache is still valid.
                    else:
                        count = conn.execute("SELECT COUNT(1) FROM personel").fetchone()[0]  # WHY: avoid refetch if truly empty.
                if count == 0:
                    return []  # WHY: keep empty cache for real empty DB.
            except Exception:
                return list(cached)  # WHY: fallback to cached empty list on unexpected DB errors.
        data = self.get_all_personnel_detailed(tersane_id=tersane_id)  # WHY: force fresh fetch if cache empty or missing.
        self._set_cached('personnel_list', tersane_id, list(data))  # WHY: store for fast reuse.
        return data

    def get_personnel_names_for_tersane(self, tersane_id=0, year=None, month=None):
        """Tersane bazli personel adlarini getirir (atanmis veya puantajda calismis)."""
        # WHY: centralize tersane/personel filtering without touching calculation logic.
        try:
            tid = int(tersane_id or 0)
        except Exception:
            tid = 0  # WHY: normalize invalid inputs to global.
        if tid <= 0:
            # WHY: "Genel" modda cached personel listesi hizli ve yeterli.
            return [p[0] for p in self.get_cached_personnel_list(tersane_id=0)]
        record_filter = ""
        params = [tid, tid]
        if year and month:
            record_filter = " AND strftime('%Y', g.tarih)=? AND strftime('%m', g.tarih)=?"  # WHY: filter by active period if provided.
            params += [str(year), f"{month:02d}"]
        sql = (
            "SELECT DISTINCT TRIM(p.ad_soyad) FROM personel p WHERE p.tersane_id = ? "
            "UNION "
            "SELECT DISTINCT TRIM(g.ad_soyad) FROM gunluk_kayit g WHERE g.tersane_id = ?" + record_filter +
            " ORDER BY 1"
        )  # WHY: include assigned and actually worked personnel for selected tersane.
        with self.get_connection() as conn:
            return [r[0] for r in conn.execute(sql, tuple(params)).fetchall()]


    def get_all_personnel_detailed(self, year=None, month=None, tersane_id=None, include_unassigned=False, use_records_filter=False):  # WHY: allow Personnel page to use gunluk_kayit-based filtering without changing other callers.
        with self.get_connection() as conn:
            c = conn.cursor()
            # NEW: Personel sekmesi icin puantaj (gunluk_kayit) tablosuna gore listeleme.
            if use_records_filter and tersane_id and tersane_id > 0:
                record_filter = " AND g.tersane_id = ?"  # WHY: use selected tersane from daily records, not personel card.
                record_params = [tersane_id]  # WHY: parameterize tersane filter for safety.
                if year and month:
                    record_filter += " AND strftime('%Y', g.tarih) = ? AND strftime('%m', g.tarih) = ?"  # WHY: apply period filter on actual work records.
                    record_params += [str(year), f"{month:02d}"]  # WHY: keep same date formatting as elsewhere.
                c.execute("""SELECT p.ad_soyad, p.maas, p.ekip_adi, p.ozel_durum, p.ekstra_odeme, p.yillik_izin_hakki, p.ise_baslangic, p.cikis_tarihi,
                                COALESCE(p.ekstra_odeme_not, ''), COALESCE(p.avans_not, ''), COALESCE(p.yevmiyeci_mi, 0)
                                FROM personel p
                                WHERE TRIM(p.ad_soyad) IN (
                                    SELECT DISTINCT TRIM(g.ad_soyad)
                                    FROM gunluk_kayit g
                                    WHERE 1=1""" + record_filter + """
                                )
                                ORDER BY p.ad_soyad""", tuple(record_params))  # WHY: list only personnel with actual records for selected tersane/period.
                return c.fetchall()
            if use_records_filter and (not tersane_id or tersane_id <= 0):
                # WHY: "Genel" modunda tum aktif personeller listelensin (tersane/period filtrelenmez).
                c.execute("""SELECT p.ad_soyad, p.maas, p.ekip_adi, p.ozel_durum, p.ekstra_odeme, p.yillik_izin_hakki, p.ise_baslangic, p.cikis_tarihi,
                                COALESCE(p.ekstra_odeme_not, ''), COALESCE(p.avans_not, ''), COALESCE(p.yevmiyeci_mi, 0)
                                FROM personel p
                                ORDER BY p.ad_soyad""")  # WHY: show full active personnel list in general mode.
                return c.fetchall()
            tersane_filter = ""
            tersane_params = []
            if tersane_id and tersane_id > 0:
                if include_unassigned:
                    tersane_filter = " AND (p.tersane_id = ? OR p.tersane_id IS NULL OR p.tersane_id = 0)"  # WHY: include legacy/unassigned personnel for selected tersane.
                    tersane_params = [tersane_id]  # WHY: keep parameterized query for selected tersane.
                else:
                    tersane_filter = " AND p.tersane_id = ?"  # WHY: preserve strict filtering by tersane when requested.
                    tersane_params = [tersane_id]  # WHY: keep parameterized query for selected tersane.
            if year and month:
                c.execute("""SELECT DISTINCT p.ad_soyad, p.maas, p.ekip_adi, p.ozel_durum, p.ekstra_odeme, p.yillik_izin_hakki, p.ise_baslangic, p.cikis_tarihi,
                        COALESCE(p.ekstra_odeme_not, ''), COALESCE(p.avans_not, ''), COALESCE(p.yevmiyeci_mi, 0)
                        FROM personel p INNER JOIN gunluk_kayit g ON p.ad_soyad = g.ad_soyad
                        WHERE strftime('%Y', g.tarih) = ? AND strftime('%m', g.tarih) = ?""" + tersane_filter +
                        " ORDER BY p.ad_soyad", tuple([str(year), f"{month:02d}"] + tersane_params))
            else:
                c.execute("""SELECT p.ad_soyad, p.maas, p.ekip_adi, p.ozel_durum, p.ekstra_odeme, p.yillik_izin_hakki, p.ise_baslangic, p.cikis_tarihi,
                                COALESCE(p.ekstra_odeme_not, ''), COALESCE(p.avans_not, ''), COALESCE(p.yevmiyeci_mi, 0) FROM personel p WHERE 1=1""" + tersane_filter +
                                " ORDER BY p.ad_soyad", tuple(tersane_params))
            return c.fetchall()


    def get_personnel(self, ad_soyad):
        with self.get_connection() as conn:
            row = conn.execute("SELECT ad_soyad, maas, ekip_adi, ozel_durum, ekstra_odeme, yillik_izin_hakki, ise_baslangic, cikis_tarihi, ekstra_odeme_not, avans_not, yevmiyeci_mi FROM personel WHERE TRIM(ad_soyad)=TRIM(?)", (ad_soyad,)).fetchone()
            if not row: return None
            cols = ['ad_soyad', 'maas', 'ekip_adi', 'ozel_durum', 'ekstra_odeme', 'yillik_izin_hakki', 'ise_baslangic', 'cikis_tarihi', 'ekstra_odeme_not', 'avans_not', 'yevmiyeci_mi']
            return dict(zip(cols, row))
            

    def get_personnel_special_status(self, ad_soyad):
        with self.get_connection() as conn:
            res = conn.execute("SELECT ozel_durum FROM personel WHERE TRIM(ad_soyad)=TRIM(?)", (ad_soyad,)).fetchone()
            return res[0] if res else None


    def update_personnel(self, ad_soyad, maas, ekip, ozel_durum=None, ekstra_odeme=0.0, yillik_izin_hakki=0.0, ise_baslangic=None, cikis_tarihi=None, ekstra_odeme_not=None, avans_not=None, yevmiyeci_mi=0, tersane_id=None):
        ad_soyad = ad_soyad.strip()
        with self.get_connection() as conn:
            conn.execute("""INSERT OR REPLACE INTO personel (ad_soyad, maas, ekip_adi, ozel_durum, ekstra_odeme, yillik_izin_hakki, ise_baslangic, cikis_tarihi, ekstra_odeme_not, avans_not, yevmiyeci_mi, tersane_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (ad_soyad, maas, ekip, ozel_durum, ekstra_odeme, yillik_izin_hakki, ise_baslangic, cikis_tarihi, ekstra_odeme_not, avans_not, yevmiyeci_mi, tersane_id))
            conn.commit()
        self._invalidate_cache(groups=['personnel_list'])  # WHY: personel listesi değişti, cache tazelenmeli.
        try: self.update_records_for_person(ad_soyad)
        except Exception as e:
            from core.app_logger import log_error
            log_error(f"Personel kayıt güncelleme hatası ({ad_soyad}): {e}")


    def delete_unused_personnel(self):
        with self.get_connection() as conn:
            conn.execute("DELETE FROM personel WHERE ad_soyad NOT IN (SELECT DISTINCT ad_soyad FROM gunluk_kayit)"); conn.commit()
        self._invalidate_cache(groups=['personnel_list'])  # WHY: personel listesi değişti, cache tazelenmeli.


    def sync_personnel(self):
        with self.get_connection() as conn:
            conn.execute("INSERT INTO personel (ad_soyad, maas, ekip_adi, ozel_durum, ekstra_odeme, yillik_izin_hakki, ise_baslangic, cikis_tarihi) SELECT DISTINCT ad_soyad, 0, '', NULL, 0.0, 0.0, NULL, NULL FROM gunluk_kayit WHERE ad_soyad NOT IN (SELECT ad_soyad FROM personel)"); conn.commit()
        self._invalidate_cache(groups=['personnel_list'])  # WHY: personel listesi değişti, cache tazelenmeli.


    def get_unique_teams(self):
        with self.get_connection() as conn:
            return [r[0] for r in conn.execute("SELECT DISTINCT ekip_adi FROM personel WHERE ekip_adi IS NOT NULL AND ekip_adi != '' ORDER BY ekip_adi").fetchall()]
            

    def get_records_by_date(self, tarih):
        with self.get_connection() as conn:
                return conn.execute("SELECT ad_soyad FROM gunluk_kayit WHERE tarih=? ORDER BY ad_soyad LIMIT 1000", (tarih,)).fetchall()
                

    def get_records_by_month(self, year, month, tersane_id=None):
        month_str = f"{year}-{month:02d}"
        sql = """SELECT g.id, g.tarih, g.ad_soyad, g.giris_saati, g.cikis_saati,
                        g.kayip_sure_saat, g.hesaplanan_normal, g.hesaplanan_mesai, g.aciklama, p.ekip_adi
                    FROM gunluk_kayit g LEFT JOIN personel p ON g.ad_soyad = p.ad_soyad
                    WHERE g.tarih LIKE ?"""
        params = [f"{month_str}%"]
        if tersane_id and tersane_id > 0:
            sql += " AND g.tersane_id = ?"
            params.append(tersane_id)
        sql += " ORDER BY g.tarih, g.ad_soyad"
        with self.get_connection() as conn:
            return conn.execute(sql, tuple(params)).fetchall()


    def get_records_between(self, start_date, end_date, team=None, person=None, tersane_id=None):
        sql = """SELECT g.id, g.tarih, g.ad_soyad, g.giris_saati, g.cikis_saati,
                        g.kayip_sure_saat, g.hesaplanan_normal, g.hesaplanan_mesai, g.aciklama,
                        p.ekip_adi
                    FROM gunluk_kayit g
                    LEFT JOIN personel p ON g.ad_soyad = p.ad_soyad
                    WHERE g.tarih BETWEEN ? AND ?"""
        params = [start_date, end_date]
        if team: sql += " AND p.ekip_adi = ?"; params.append(team)
        if person: sql += " AND TRIM(g.ad_soyad) = TRIM(?)"; params.append(person)
        if tersane_id and tersane_id > 0: sql += " AND g.tersane_id = ?"; params.append(tersane_id)
        sql += " ORDER BY g.tarih, g.ad_soyad"
        with self.get_connection() as conn:
            return conn.execute(sql, tuple(params)).fetchall()
            

    def get_records_between_like(self, start_date, end_date, team=None, person_like=None, tersane_id=None):
        sql = """SELECT g.id, g.tarih, g.ad_soyad, g.giris_saati, g.cikis_saati,
                        g.kayip_sure_saat, g.hesaplanan_normal, g.hesaplanan_mesai, g.aciklama, p.ekip_adi
                    FROM gunluk_kayit g LEFT JOIN personel p ON g.ad_soyad = p.ad_soyad
                    WHERE g.tarih BETWEEN ? AND ?"""
        params = [start_date, end_date]
        if team: sql += " AND p.ekip_adi = ?"; params.append(team)
        if person_like: sql += " AND TRIM(g.ad_soyad) LIKE '%' || ? || '%'"; params.append(person_like)
        if tersane_id and tersane_id > 0: sql += " AND g.tersane_id = ?"; params.append(tersane_id)
        sql += " ORDER BY g.tarih, g.ad_soyad"
        with self.get_connection() as conn:
            return conn.execute(sql, tuple(params)).fetchall()


    def save_daily_record(self, data):
        with self.get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM gunluk_kayit WHERE tarih=? AND ad_soyad=?",
                (data['tarih'], data['ad_soyad'])
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE gunluk_kayit SET giris_saati=?, cikis_saati=?, kayip_sure_saat=?,"
                    " hesaplanan_normal=?, hesaplanan_mesai=?, aciklama=? WHERE id=?",
                    (data['giris'], data['cikis'], data['kayip'],
                     data['normal'], data['mesai'], data['aciklama'], existing[0])
                )
            else:
                conn.execute(
                    "INSERT INTO gunluk_kayit"
                    " (tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat,"
                    "  hesaplanan_normal, hesaplanan_mesai, aciklama)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (data['tarih'], data['ad_soyad'], data['giris'], data['cikis'],
                     data['kayip'], data['normal'], data['mesai'], data['aciklama'])
                )
            conn.commit()


    def update_single_record(self, record_id, col_name, new_value):
        allowed_cols = ['kayip_sure_saat', 'hesaplanan_normal', 'hesaplanan_mesai', 'aciklama']
        if col_name not in allowed_cols: return False
        with self.get_connection() as conn:
            conn.execute(f"UPDATE gunluk_kayit SET {col_name} = ? WHERE id = ?", (new_value, record_id))
            conn.commit()
            return True

    # --- TRASH / SİLME FONKSİYONLARI ---

    def delete_records_between(self, start_date, end_date, firma_id=None, tersane_id=None):
        extra_where = ""
        extra_params = []
        if firma_id is not None:
            extra_where += " AND firma_id=?"
            extra_params.append(firma_id)
        if tersane_id is not None:
            extra_where += " AND tersane_id=?"
            extra_params.append(tersane_id)
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute(
                f"DELETE FROM gunluk_kayit WHERE tarih BETWEEN ? AND ?{extra_where}",
                [start_date, end_date] + extra_params
            )
            d1 = c.rowcount
            c.execute("DELETE FROM avans_kesinti WHERE tarih BETWEEN ? AND ?", (start_date, end_date))
            conn.commit()
            return d1, c.rowcount
            

    def move_records_to_trash(self, start_date, end_date, firma_id=None, tersane_id=None):
        extra_where = ""
        extra_params = []
        if firma_id is not None:
            extra_where += " AND firma_id=?"
            extra_params.append(firma_id)
        if tersane_id is not None:
            extra_where += " AND tersane_id=?"
            extra_params.append(tersane_id)
        with self.get_connection() as conn:
            c = conn.cursor()
            now = datetime.now().isoformat()
            c.execute("INSERT INTO trash_batches (start_date, end_date, created_at) VALUES (?, ?, ?)", (start_date, end_date, now))
            bid = c.lastrowid
            # gunluk_kayit -> trash (schema-uyumlu)
            c.execute("PRAGMA table_info(gunluk_kayit_trash)")
            trash_cols = {r[1] for r in c.fetchall()}
            c.execute("PRAGMA table_info(gunluk_kayit)")
            src_cols = {r[1] for r in c.fetchall()}
            has_extra = all(col in trash_cols and col in src_cols for col in ("tersane_id", "firma_id", "manuel_kilit"))
            if has_extra:
                c.execute(
                    "INSERT INTO gunluk_kayit_trash (orig_id, tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, hesaplanan_normal, hesaplanan_mesai, aciklama, tersane_id, firma_id, manuel_kilit, batch_id, deleted_at) "
                    "SELECT id, tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, hesaplanan_normal, hesaplanan_mesai, aciklama, tersane_id, firma_id, COALESCE(manuel_kilit,0), ?, ? "
                    f"FROM gunluk_kayit WHERE tarih BETWEEN ? AND ?{extra_where}",
                    [bid, now, start_date, end_date] + extra_params
                )
            else:
                c.execute(
                    "INSERT INTO gunluk_kayit_trash (orig_id, tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, hesaplanan_normal, hesaplanan_mesai, aciklama, batch_id, deleted_at) "
                    "SELECT id, tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, hesaplanan_normal, hesaplanan_mesai, aciklama, ?, ? "
                    f"FROM gunluk_kayit WHERE tarih BETWEEN ? AND ?{extra_where}",
                    [bid, now, start_date, end_date] + extra_params
                )
            dd = c.rowcount
            c.execute("INSERT INTO avans_kesinti_trash (orig_id, tarih, ad_soyad, tur, tutar, aciklama, batch_id, deleted_at) SELECT id, tarih, ad_soyad, tur, tutar, aciklama, ?, ? FROM avans_kesinti WHERE tarih BETWEEN ? AND ?", (bid, now, start_date, end_date))
            da = c.rowcount
            c.execute(
                f"DELETE FROM gunluk_kayit WHERE tarih BETWEEN ? AND ?{extra_where}",
                [start_date, end_date] + extra_params
            )
            c.execute("DELETE FROM avans_kesinti WHERE tarih BETWEEN ? AND ?", (start_date, end_date))
            c.execute("UPDATE trash_batches SET deleted_daily=?, deleted_avans=? WHERE id=?", (dd, da, bid))
            conn.commit()
            return bid, dd, da


    def get_trash_batches(self):
        with self.get_connection() as conn:
            return conn.execute("SELECT id, start_date, end_date, created_at, deleted_daily, deleted_avans FROM trash_batches ORDER BY created_at DESC").fetchall()
            

    def restore_trash_batch(self, batch_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            # trash -> gunluk_kayit (schema-uyumlu)
            c.execute("PRAGMA table_info(gunluk_kayit_trash)")
            trash_cols = {r[1] for r in c.fetchall()}
            c.execute("PRAGMA table_info(gunluk_kayit)")
            dest_cols = {r[1] for r in c.fetchall()}
            has_extra = all(col in trash_cols and col in dest_cols for col in ("tersane_id", "firma_id", "manuel_kilit"))
            if has_extra:
                c.execute(
                    "INSERT OR REPLACE INTO gunluk_kayit (tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, hesaplanan_normal, hesaplanan_mesai, aciklama, tersane_id, firma_id, manuel_kilit) "
                    "SELECT tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, hesaplanan_normal, hesaplanan_mesai, aciklama, tersane_id, firma_id, COALESCE(manuel_kilit,0) "
                    "FROM gunluk_kayit_trash WHERE batch_id=?",
                    (batch_id,)
                )
            else:
                c.execute(
                    "INSERT OR REPLACE INTO gunluk_kayit (tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, hesaplanan_normal, hesaplanan_mesai, aciklama) "
                    "SELECT tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, hesaplanan_normal, hesaplanan_mesai, aciklama "
                    "FROM gunluk_kayit_trash WHERE batch_id=?",
                    (batch_id,)
                )
            rd = c.rowcount
            c.execute("INSERT OR REPLACE INTO avans_kesinti (tarih, ad_soyad, tur, tutar, aciklama) SELECT tarih, ad_soyad, tur, tutar, aciklama FROM avans_kesinti_trash WHERE batch_id=?", (batch_id,))
            ra = c.rowcount
            c.execute("DELETE FROM gunluk_kayit_trash WHERE batch_id=?", (batch_id,))
            c.execute("DELETE FROM avans_kesinti_trash WHERE batch_id=?", (batch_id,))
            c.execute("DELETE FROM trash_batches WHERE id=?", (batch_id,))
            conn.commit()
            return rd, ra

    # --- UPLOAD BATCH ROLLBACK ---

    def get_last_upload_batch_id(self):
        """Son yükleme batch_id'sini app_meta'dan döndürür."""
        try:
            with self.get_connection() as conn:
                row = conn.execute(
                    "SELECT value FROM app_meta WHERE key='last_upload_batch_id'"
                ).fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def set_last_upload_batch_id(self, batch_id):
        """Son yükleme batch_id'sini app_meta'ya kaydeder."""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('last_upload_batch_id', ?)",
                    (batch_id,)
                )
                conn.commit()
        except Exception:
            pass  # SAFEGUARD: metadata write failure must not block the UI.

    def snapshot_personel_for_batch(self, batch_id, ad_soyad_list):
        """Yükleme öncesi personel tersane/firma bilgisini log tablosuna kaydeder."""
        if not ad_soyad_list:
            return
        changed_at = datetime.now().isoformat()
        rows = []
        with self.get_connection() as conn:
            for ad in ad_soyad_list:
                row = conn.execute(
                    "SELECT firma_id, tersane_id FROM personel WHERE ad_soyad=?", (ad,)
                ).fetchone()
                rows.append((
                    batch_id, ad,
                    row[0] if row else None,   # old_firma_id
                    row[1] if row else None,   # old_tersane_id
                    changed_at
                ))
            if rows:
                conn.executemany(
                    "INSERT OR IGNORE INTO upload_batch_log_personel "
                    "(batch_id, ad_soyad, old_firma_id, old_tersane_id, changed_at) "
                    "VALUES (?,?,?,?,?)",
                    rows
                )
                conn.commit()

    def rollback_upload_batch_full(self, batch_id, firma_id=None):
        """
        Bir yükleme batch'ini tamamen geri alır:
          1) Ay kilidi kontrolü
          2) import_batch_id=batch_id olan kayıtları trash'e taşı ve sil
          3) Personel tersane_id'lerini snapshot'tan geri yükle
        Döndürür: (True, trashed_count) veya (False, hata_mesajı)
        """
        try:
            with self.get_connection() as conn:
                c = conn.cursor()
                now = datetime.now().isoformat()

                # 1) Ay kilidi kontrolü
                if firma_id is not None:
                    ay_rows = c.execute(
                        "SELECT DISTINCT CAST(strftime('%Y', tarih) AS INTEGER), "
                        "CAST(strftime('%m', tarih) AS INTEGER) "
                        "FROM gunluk_kayit WHERE import_batch_id=?",
                        (batch_id,)
                    ).fetchall()
                    for y, m in ay_rows:
                        if self.is_month_locked(y, m, firma_id):
                            return False, f"{y}-{m:02d} ayı kilitlidir; rollback yapılamaz."

                # 2) gunluk_kayit → trash
                c.execute("PRAGMA table_info(gunluk_kayit_trash)")
                trash_cols = {r[1] for r in c.fetchall()}
                c.execute("PRAGMA table_info(gunluk_kayit)")
                src_cols = {r[1] for r in c.fetchall()}
                has_extra = all(
                    col in trash_cols and col in src_cols
                    for col in ("tersane_id", "firma_id", "manuel_kilit")
                )

                c.execute(
                    "INSERT INTO trash_batches (start_date, end_date, created_at) VALUES (?, ?, ?)",
                    (batch_id, batch_id, now)
                )
                tbid = c.lastrowid

                if has_extra:
                    c.execute(
                        "INSERT INTO gunluk_kayit_trash "
                        "(orig_id, tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, "
                        "hesaplanan_normal, hesaplanan_mesai, aciklama, tersane_id, firma_id, "
                        "manuel_kilit, batch_id, deleted_at) "
                        "SELECT id, tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, "
                        "hesaplanan_normal, hesaplanan_mesai, aciklama, tersane_id, firma_id, "
                        "COALESCE(manuel_kilit, 0), ?, ? "
                        "FROM gunluk_kayit WHERE import_batch_id=?",
                        (tbid, now, batch_id)
                    )
                else:
                    c.execute(
                        "INSERT INTO gunluk_kayit_trash "
                        "(orig_id, tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, "
                        "hesaplanan_normal, hesaplanan_mesai, aciklama, batch_id, deleted_at) "
                        "SELECT id, tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, "
                        "hesaplanan_normal, hesaplanan_mesai, aciklama, ?, ? "
                        "FROM gunluk_kayit WHERE import_batch_id=?",
                        (tbid, now, batch_id)
                    )
                trashed = c.rowcount
                c.execute("UPDATE trash_batches SET deleted_daily=? WHERE id=?", (trashed, tbid))

                # 3) Sil
                c.execute("DELETE FROM gunluk_kayit WHERE import_batch_id=?", (batch_id,))

                # 4) Personel tersane_id'yi geri yükle
                log_rows = c.execute(
                    "SELECT ad_soyad, old_tersane_id FROM upload_batch_log_personel WHERE batch_id=?",
                    (batch_id,)
                ).fetchall()
                for ad, old_tid in log_rows:
                    c.execute("UPDATE personel SET tersane_id=? WHERE ad_soyad=?", (old_tid, ad))

                # 5) Log ve meta temizle
                c.execute("DELETE FROM upload_batch_log_personel WHERE batch_id=?", (batch_id,))
                c.execute(
                    "DELETE FROM app_meta WHERE key='last_upload_batch_id' AND value=?",
                    (batch_id,)
                )

                conn.commit()
                return True, trashed
        except Exception as e:
            return False, str(e)

    # --- AVANS VE DASHBOARD ---

    def get_avans_list(self, limit=50, tersane_id=None):
        if tersane_id and tersane_id > 0:
            with self.get_connection() as conn:
                return conn.execute(
                    "SELECT a.id, a.tarih, a.ad_soyad, a.tur, a.tutar, a.aciklama FROM avans_kesinti a "
                    "WHERE TRIM(a.ad_soyad) IN ("
                    "SELECT DISTINCT TRIM(ad_soyad) FROM personel WHERE tersane_id = ? "
                    "UNION "
                    "SELECT DISTINCT TRIM(ad_soyad) FROM gunluk_kayit WHERE tersane_id = ?"
                    ") ORDER BY a.tarih DESC LIMIT ?",
                    (tersane_id, tersane_id, limit)
                ).fetchall()  # WHY: include assigned or worked personnel for selected tersane.
        with self.get_connection() as conn:
            return conn.execute("SELECT id, tarih, ad_soyad, tur, tutar, aciklama FROM avans_kesinti ORDER BY tarih DESC LIMIT ?", (limit,)).fetchall()


    def get_total_avans(self, ad_soyad):
        with self.get_connection() as conn:
            res = conn.execute("SELECT COALESCE(SUM(tutar), 0) FROM avans_kesinti WHERE ad_soyad=? AND tur='Avans'", (ad_soyad,)).fetchone()
            return res[0] or 0.0


    def save_avans(self, tarih, ad, tur, tutar, aciklama, firma_id=None):
        if firma_id is not None:
            try:
                y, m = int(tarih[:4]), int(tarih[5:7])
                if self.is_month_locked(y, m, firma_id):
                    return False
            except Exception:
                pass
        with self.get_connection() as conn:
            conn.execute("INSERT INTO avans_kesinti (tarih, ad_soyad, tur, tutar, aciklama) VALUES (?,?,?,?,?)", (tarih, ad, tur, tutar, aciklama))
            conn.commit()
        return True

    def delete_avans(self, id, firma_id=None):
        with self.get_connection() as conn:
            if firma_id is not None:
                try:
                    row = conn.execute("SELECT tarih FROM avans_kesinti WHERE id=?", (id,)).fetchone()
                    if row:
                        y, m = int(row[0][:4]), int(row[0][5:7])
                        if self.is_month_locked(y, m, firma_id):
                            return False
                except Exception:
                    pass
            conn.execute("DELETE FROM avans_kesinti WHERE id=?", (id,))
            conn.commit()
        return True


    def get_dashboard_data(self, year, month, tersane_id=None):
        month_str = f"{year}-{month:02d}"
        with self.get_connection() as conn:
            c = conn.cursor()
            sql_puantaj = """SELECT g.ad_soyad, p.maas, p.ekip_adi, p.ekstra_odeme, COALESCE(p.yevmiyeci_mi, 0),
                    SUM(g.hesaplanan_normal), SUM(g.hesaplanan_mesai) FROM gunluk_kayit g LEFT JOIN personel p ON g.ad_soyad = p.ad_soyad
                    WHERE g.tarih LIKE ?"""
            params_p = [f"{month_str}%"]
            if tersane_id and tersane_id > 0:
                sql_puantaj += " AND g.tersane_id = ?"
                params_p.append(tersane_id)
            sql_puantaj += " GROUP BY g.ad_soyad"
            puantaj = c.execute(sql_puantaj, tuple(params_p)).fetchall()

            sql_avans = """SELECT a.ad_soyad, SUM(CASE WHEN a.tur IN ('Avans', 'Kesinti') THEN a.tutar ELSE 0 END) FROM avans_kesinti a
                                WHERE a.tarih LIKE ?"""
            params_a = [f"{month_str}%"]
            if tersane_id and tersane_id > 0:
                sql_avans += " AND a.ad_soyad IN (SELECT ad_soyad FROM personel WHERE tersane_id = ?)"
                params_a.append(tersane_id)
            sql_avans += " GROUP BY a.ad_soyad"
            avans = c.execute(sql_avans, tuple(params_a)).fetchall()

            avans_dict = {r[0]: r[1] for r in avans}
            result = []
            for row in puantaj:
                result.append({
                    "ad_soyad": row[0], "maas": row[1] or 0, "ekip": row[2] or "Diğer",
                    "ekstra": row[3] or 0.0, "yevmiyeci_mi": row[4],
                    "top_normal": row[5], "top_mesai": row[6],
                    "avans": avans_dict.get(row[0], 0.0)
                })
            return result

    # --- DİĞER MODÜLLER (BES, VARDİYA, İZİN) ---

    def add_bes_personel(self, ad_soyad, gunluk_bes_fiyati=None):
        if gunluk_bes_fiyati is None: gunluk_bes_fiyati = float(self.get_setting('gunluk_bes_fiyati', 20.0))
        with self.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO bes_personel (ad_soyad, gunluk_bes_fiyati) VALUES (?, ?)", (ad_soyad, gunluk_bes_fiyati)); conn.commit()


    def get_bes_personel_list(self):
        with self.get_connection() as conn:
            return conn.execute("SELECT ad_soyad, gunluk_bes_fiyati FROM bes_personel WHERE devam_ediyor=1 ORDER BY ad_soyad").fetchall()


    def get_bes_hesaplama_list(self, year, month, tersane_id=None):
        month_str = f"{year}-{month:02d}"
        with self.get_connection() as conn:
            if tersane_id and tersane_id > 0:
                return conn.execute(
                    "SELECT ad_soyad, calisilan_gun_sayisi, gunluk_tutar, aylik_bes_tutari FROM bes_hesaplama "
                    "WHERE ay_yil=? AND TRIM(ad_soyad) IN ("
                    "SELECT DISTINCT TRIM(ad_soyad) FROM personel WHERE tersane_id = ? "
                    "UNION "
                    "SELECT DISTINCT TRIM(ad_soyad) FROM gunluk_kayit WHERE tersane_id = ? AND tarih LIKE ?"
                    ") ORDER BY ad_soyad",
                    (month_str, tersane_id, tersane_id, f"{month_str}%")
                ).fetchall()  # WHY: filter BES list to selected tersane via assigned or worked personnel.
            return conn.execute(
                "SELECT ad_soyad, calisilan_gun_sayisi, gunluk_tutar, aylik_bes_tutari FROM bes_hesaplama WHERE ay_yil=? ORDER BY ad_soyad",
                (month_str,)
            ).fetchall()


    def get_bes_personel_status_map(self):
        with self.get_connection() as conn:
            return {row[0]: int(row[1]) for row in conn.execute("SELECT ad_soyad, devam_ediyor FROM bes_personel").fetchall()}


    def set_bes_personel_status(self, ad_soyad, devam_ediyor):
        with self.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO bes_personel (ad_soyad) VALUES (?)", (ad_soyad,))
            conn.execute("UPDATE bes_personel SET devam_ediyor=? WHERE ad_soyad=?", (devam_ediyor, ad_soyad)); conn.commit()


    def toggle_bes_personel(self, ad_soyad, devam_ediyor):
        self.set_bes_personel_status(ad_soyad, devam_ediyor)


    def delete_bes_hesaplama_from(self, ad_soyad, start_year, start_month):
        start_month_str = f"{start_year}-{start_month:02d}"
        with self.get_connection() as conn:
            conn.execute("DELETE FROM bes_hesaplama WHERE ad_soyad=? AND ay_yil >= ?", (ad_soyad, start_month_str)); conn.commit()


    def calculate_bes_for_month(self, year, month, tersane_id=None):
        month_str = f"{year}-{month:02d}"
        with self.get_connection() as conn:
            c = conn.cursor()
            bes_personel = c.execute(
                "SELECT ad_soyad, gunluk_bes_fiyati FROM bes_personel WHERE devam_ediyor=1"
            ).fetchall()
            if tersane_id and tersane_id > 0:
                allowed = set(self.get_personnel_names_for_tersane(tersane_id, year, month))  # WHY: keep BES list in sync with active tersane.
                bes_personel = [bp for bp in bes_personel if bp[0] in allowed]  # WHY: filter without changing BES formula.
            if not bes_personel:
                return []

            # Tek sorgu ile gun sayilarini al (GROUP BY) -> performans
            if tersane_id and tersane_id > 0:
                rows = c.execute(
                    "SELECT ad_soyad, COUNT(*) FROM gunluk_kayit WHERE tersane_id=? AND tarih LIKE ? GROUP BY ad_soyad",
                    (tersane_id, f"{month_str}%")
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT ad_soyad, COUNT(*) FROM gunluk_kayit WHERE tarih LIKE ? GROUP BY ad_soyad",
                    (f"{month_str}%",)
                ).fetchall()
            count_map = {ad: cnt for ad, cnt in rows}

            results = []
            for ad_soyad, gunluk_fiyat in bes_personel:
                calisilan_gun = count_map.get(ad_soyad, 0)
                if calisilan_gun > 0:
                    aylik_bes = calisilan_gun * gunluk_fiyat
                    c.execute(
                        "INSERT OR REPLACE INTO bes_hesaplama (ay_yil, ad_soyad, calisilan_gun_sayisi, gunluk_tutar, aylik_bes_tutari) VALUES (?, ?, ?, ?, ?)",
                        (month_str, ad_soyad, calisilan_gun, gunluk_fiyat, aylik_bes)
                    )
                    results.append({"ad_soyad": ad_soyad, "aylik_bes": aylik_bes})
            conn.commit()
            return results

    # İzinler

    def _default_izin_turleri(self):
        """Varsayilan izin turleri ve otomatik kayit varsayimi."""
        return [
            ("Hasta", 0),
            ("Raporlu", 1),
            ("\u00d6z\u00fcr", 0),                # Özür
            ("Y\u0131ll\u0131k \u0130zin", 1),   # Yıllık İzin
            ("Do\u011fum \u0130zni", 1),         # Doğum İzni
            ("\u0130dari \u0130zin", 0),         # İdari İzin
            ("Evlilik \u0130zni", 0),            # Evlilik İzni
            ("\u00c7ocuk \u0130zni", 0),         # Çocuk İzni
            ("\u0130\u015f Kazas\u0131 \u0130zni", 0),  # İş Kazası İzni
            ("Di\u011fer", 0),                   # Diğer
        ]


    def _canonicalize_izin_turu(self, izin_turu):
        """Izin turu metnini bilinen canonical adlardan birine cevirir."""
        raw = str(izin_turu or "").strip()
        if not raw:
            return ""

        # WHY: collect multi-step repaired variants for mojibake text (latin1/cp125x decoded utf-8).
        candidates = [raw]
        seen = {raw}
        round_inputs = [raw]
        for _ in range(2):
            next_round = []
            for src in round_inputs:
                for enc in ("latin1", "cp1252", "cp1254"):
                    try:
                        repaired = src.encode(enc, errors="ignore").decode("utf-8", errors="ignore").strip()
                    except Exception:
                        continue
                    if repaired and repaired not in seen:
                        seen.add(repaired)
                        candidates.append(repaired)
                        next_round.append(repaired)
            round_inputs = next_round
            if not round_inputs:
                break

        aliases = {
            "hasta": "Hasta",
            "raporlu": "Raporlu",
            "rapor izni": "Raporlu",
            "raporizni": "Raporlu",
            "ozur": "\u00d6z\u00fcr",
            "ozur izni": "\u00d6z\u00fcr",
            "ozurizni": "\u00d6z\u00fcr",
            "yillik izin": "Y\u0131ll\u0131k \u0130zin",
            "yillikizin": "Y\u0131ll\u0131k \u0130zin",
            "dogum izni": "Do\u011fum \u0130zni",
            "dogumizni": "Do\u011fum \u0130zni",
            "idari izin": "\u0130dari \u0130zin",
            "idariizni": "\u0130dari \u0130zin",
            "evlilik izni": "Evlilik \u0130zni",
            "evlilikizni": "Evlilik \u0130zni",
            "cocuk izni": "\u00c7ocuk \u0130zni",
            "cocukizni": "\u00c7ocuk \u0130zni",
            "is kazasi izni": "\u0130\u015f Kazas\u0131 \u0130zni",
            "iskazasizni": "\u0130\u015f Kazas\u0131 \u0130zni",
            "diger": "Di\u011fer",
        }

        for cand in candidates:
            norm = " ".join(self._normalize_text_for_compare(cand).split())
            compact = re.sub(r"[^a-z0-9]+", "", norm)
            norm_variants = [norm, norm.replace("?", "i"), norm.replace("?", ""), compact]
            for nv in norm_variants:
                mapped = aliases.get(nv)
                if mapped:
                    return mapped

                # Heuristic fallback for badly broken strings.
                tokenized = " ".join(re.sub(r"[^a-z0-9 ]+", " ", nv).split())
                compact_tokens = tokenized.replace(" ", "")
                if ("yillik" in compact_tokens or "yllk" in compact_tokens) and ("izin" in compact_tokens or "izn" in compact_tokens):
                    return "Y\u0131ll\u0131k \u0130zin"
                if tokenized.startswith("rapor") and ("zn" in tokenized or "izin" in tokenized):
                    return "Raporlu"
                if ("dogum" in compact_tokens or "doum" in compact_tokens) and ("zn" in tokenized or "izin" in tokenized):
                    return "Do\u011fum \u0130zni"
                if "idari" in compact_tokens and ("zn" in tokenized or "izin" in tokenized):
                    return "\u0130dari \u0130zin"
                if "evlilik" in compact_tokens and ("zn" in tokenized or "izin" in tokenized):
                    return "Evlilik \u0130zni"
                if ("cocuk" in compact_tokens or "ocuk" in compact_tokens) and ("zn" in tokenized or "izin" in tokenized):
                    return "\u00c7ocuk \u0130zni"
                if ("kaza" in compact_tokens or "kazas" in compact_tokens) and ("zn" in tokenized or "izin" in tokenized):
                    return "\u0130\u015f Kazas\u0131 \u0130zni"
                if ("diger" in compact_tokens or compact_tokens in {"dier", "dgr"}):
                    return "Di\u011fer"
                if ("ozur" in compact_tokens or compact_tokens.startswith("zr")) and ("zn" in tokenized or "izin" in tokenized or compact_tokens in {"ozur", "zr"}):
                    return "\u00d6z\u00fcr"

        return raw


    def init_izin_ayarlari(self):
        defaults = self._default_izin_turleri()
        default_auto = {tur: oto for tur, oto in defaults}

        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT izin_turu, otomatik_kayit FROM izin_tur_ayarlari")
            rows = c.fetchall()

            merged = {}
            for tur, oto in rows:
                canonical = self._canonicalize_izin_turu(tur)
                if not canonical:
                    continue
                prev = merged.get(canonical, 0)
                merged[canonical] = max(prev, 1 if int(oto or 0) else 0)

            for tur, oto in defaults:
                merged.setdefault(tur, oto)

            c.execute("DELETE FROM izin_tur_ayarlari")
            for tur, _ in defaults:
                c.execute(
                    "INSERT OR REPLACE INTO izin_tur_ayarlari (izin_turu, otomatik_kayit) VALUES (?, ?)",
                    (tur, merged.get(tur, default_auto.get(tur, 0)))
                )
            extra = sorted(t for t in merged.keys() if t not in default_auto)
            for tur in extra:
                c.execute(
                    "INSERT OR REPLACE INTO izin_tur_ayarlari (izin_turu, otomatik_kayit) VALUES (?, ?)",
                    (tur, merged[tur])
                )
            conn.commit()


    def get_izin_ayarlari(self):
        defaults = [tur for tur, _ in self._default_izin_turleri()]
        order_case = " ".join([f"WHEN ? THEN {idx}" for idx, _ in enumerate(defaults)])
        params = defaults + [len(defaults)]
        with self.get_connection() as conn:
            return conn.execute(
                f"SELECT izin_turu, otomatik_kayit FROM izin_tur_ayarlari ORDER BY CASE izin_turu {order_case} ELSE ? END, izin_turu",
                params
            ).fetchall()


    def set_izin_otomatik_kayit(self, izin_turu, otomatik):
        canonical = self._canonicalize_izin_turu(izin_turu) or str(izin_turu or "").strip()
        if not canonical:
            return
        with self.get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO izin_tur_ayarlari (izin_turu, otomatik_kayit) VALUES (?, ?)",
                (canonical, 1 if otomatik else 0),
            )
            conn.execute(
                "UPDATE izin_tur_ayarlari SET otomatik_kayit=? WHERE izin_turu=?",
                (1 if otomatik else 0, canonical),
            )
            conn.commit()


    def get_genel_yevmiye_ayari(self):
        with self.get_connection() as conn:
            res = conn.execute("SELECT value FROM genel_ayarlar WHERE key='tum_izinlerde_yevmiye' LIMIT 1").fetchone()
            return res[0] if res else 0


    def set_genel_yevmiye_ayari(self, acik):
        with self.get_connection() as conn:
            exists = conn.execute("SELECT id FROM genel_ayarlar WHERE key='tum_izinlerde_yevmiye'").fetchone()
            if exists: conn.execute("UPDATE genel_ayarlar SET value=? WHERE key='tum_izinlerde_yevmiye'", (1 if acik else 0,))
            else: conn.execute("INSERT INTO genel_ayarlar (key, value) VALUES (?, ?)", ('tum_izinlerde_yevmiye', 1 if acik else 0))
            conn.commit()


    def _normalize_text_for_compare(self, value):
        """Karsilastirma icin metni sade formatta dondurur."""
        txt = str(value or "").strip().lower()
        txt = (
            txt.replace("\u0131", "i")
               .replace("\u0130", "i")
               .replace("\u015f", "s")
               .replace("\u015e", "s")
               .replace("\u00fc", "u")
               .replace("\u00f6", "o")
               .replace("\u00e7", "c")
               .replace("\u011f", "g")
        )
        txt = unicodedata.normalize("NFKD", txt)
        txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
        return " ".join(txt.split())

    def _is_yillik_izin_turu(self, izin_turu):
        """Yillik izin turunu toleransli tespit eder."""
        norm = self._normalize_text_for_compare(izin_turu)
        if norm == "yillik izin":
            return True
        # WHY: handle mojibake text that may be stored as latin-1 interpreted utf-8.
        try:
            repaired = str(izin_turu).encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            return self._normalize_text_for_compare(repaired) == "yillik izin"
        except Exception:
            return False


    def _izin_kapsam_tarihleri(self, izin_tarihi, gun_sayisi):
        """
        Izin kapsamindaki tum tarihleri dondurur.
        Kural: Pazarlar gun sayisina dahil edilmez, ancak araliktaki gunler kapsamda kalir.
        """
        start_date = datetime.strptime(izin_tarihi, '%Y-%m-%d')
        try:
            hedef_gun = max(1, int(float(gun_sayisi)))
        except Exception:
            hedef_gun = 1

        current_date = start_date
        sayilan_gun = 0
        while sayilan_gun < hedef_gun:
            if current_date.weekday() != 6:
                sayilan_gun += 1
            if sayilan_gun < hedef_gun:
                current_date += timedelta(days=1)

        tarihler = []
        kayit_date = start_date
        while kayit_date <= current_date:
            tarihler.append(kayit_date.strftime('%Y-%m-%d'))
            kayit_date += timedelta(days=1)
        return tarihler


    def add_izin_with_auto_kayit(self, ad_soyad, izin_tarihi, izin_turu, gun_sayisi=1.0, aciklama="", tersane_id=0):
        canonical_izin_turu = self._canonicalize_izin_turu(izin_turu) or str(izin_turu or "").strip()
        if not canonical_izin_turu:
            canonical_izin_turu = str(izin_turu or "").strip()
        with self.get_connection() as conn:
            c = conn.cursor()

            personel_row = c.execute(
                "SELECT yillik_izin_hakki, COALESCE(yevmiyeci_mi,0), COALESCE(tersane_id,0), COALESCE(firma_id,0) "
                "FROM personel WHERE TRIM(ad_soyad)=TRIM(?)",
                (ad_soyad,)
            ).fetchone()

            mevcut_hak = personel_row[0] if personel_row else 0
            yevmiyeci_mi = int(personel_row[1]) if personel_row else 0
            personel_tersane_id = int(personel_row[2]) if personel_row else 0
            personel_firma_id = int(personel_row[3]) if personel_row else 0
            if personel_tersane_id <= 0:
                try:
                    personel_tersane_id = int(tersane_id or 0)  # WHY: fallback to active tersane for legacy/unassigned personnel cards.
                except Exception:
                    personel_tersane_id = 0

            if personel_firma_id <= 0:
                firma_row = c.execute("SELECT id FROM firma WHERE ad='GENEL' LIMIT 1").fetchone()
                personel_firma_id = int(firma_row[0]) if firma_row else 0

            if self._is_yillik_izin_turu(canonical_izin_turu) and personel_row:
                c.execute(
                    "UPDATE personel SET yillik_izin_hakki=? WHERE TRIM(ad_soyad)=TRIM(?)",
                    ((mevcut_hak or 0) - float(gun_sayisi), ad_soyad)
                )

            c.execute(
                "INSERT INTO izin_takip (ad_soyad, izin_tarihi, izin_turu, gun_sayisi, aciklama) VALUES (?, ?, ?, ?, ?)",
                (ad_soyad, izin_tarihi, canonical_izin_turu, gun_sayisi, aciklama)
            )
            izin_id = c.lastrowid

            res = c.execute("SELECT otomatik_kayit FROM izin_tur_ayarlari WHERE izin_turu=?", (canonical_izin_turu,)).fetchone()
            if not res:
                for tur_row, oto_row in c.execute("SELECT izin_turu, otomatik_kayit FROM izin_tur_ayarlari").fetchall():
                    if self._canonicalize_izin_turu(tur_row) == canonical_izin_turu:
                        res = (oto_row,)
                        break
            if not (res and res[0]):
                conn.commit()
                return izin_id

            normal_hak = 1.0 if yevmiyeci_mi else float(NORMAL_GUNLUK_SAAT)
            izin_tarihleri = self._izin_kapsam_tarihleri(izin_tarihi, gun_sayisi)

            c.execute("PRAGMA table_info(gunluk_kayit)")
            kayit_cols = {r[1] for r in c.fetchall()}
            has_extra = all(col in kayit_cols for col in ("tersane_id", "firma_id", "manuel_kilit"))

            for kayit_tarihi in izin_tarihleri:
                if has_extra:
                    existing = c.execute(
                        "SELECT id, COALESCE(giris_saati,''), COALESCE(cikis_saati,''), COALESCE(kayip_sure_saat,''), "
                        "COALESCE(hesaplanan_normal,0.0), COALESCE(hesaplanan_mesai,0.0), COALESCE(aciklama,''), "
                        "COALESCE(tersane_id,0), COALESCE(firma_id,0), COALESCE(manuel_kilit,0) "
                        "FROM gunluk_kayit WHERE tarih=? AND TRIM(ad_soyad)=TRIM(?)",
                        (kayit_tarihi, ad_soyad)
                    ).fetchone()
                else:
                    existing = c.execute(
                        "SELECT id, COALESCE(giris_saati,''), COALESCE(cikis_saati,''), COALESCE(kayip_sure_saat,''), "
                        "COALESCE(hesaplanan_normal,0.0), COALESCE(hesaplanan_mesai,0.0), COALESCE(aciklama,'') "
                        "FROM gunluk_kayit WHERE tarih=? AND TRIM(ad_soyad)=TRIM(?)",
                        (kayit_tarihi, ad_soyad)
                    ).fetchone()

                if existing:
                    if has_extra:
                        rec_id, giris, cikis, kayip, prev_normal, prev_mesai, mevcut_aciklama, prev_tersane_id, prev_firma_id, prev_manuel_kilit = existing
                    else:
                        rec_id, giris, cikis, kayip, prev_normal, prev_mesai, mevcut_aciklama = existing
                        prev_tersane_id, prev_firma_id, prev_manuel_kilit = 0, 0, 0

                    c.execute(
                        "INSERT OR REPLACE INTO izin_auto_kayit_backup "
                        "(izin_id, tarih, ad_soyad, prev_exists, prev_giris_saati, prev_cikis_saati, prev_kayip_sure_saat, "
                        "prev_hesaplanan_normal, prev_hesaplanan_mesai, prev_aciklama, prev_tersane_id, prev_firma_id, prev_manuel_kilit) "
                        "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            izin_id, kayit_tarihi, ad_soyad,
                            giris, cikis, kayip, prev_normal, prev_mesai, mevcut_aciklama,
                            prev_tersane_id, prev_firma_id, prev_manuel_kilit
                        )
                    )
                    # Kural: izin tanimlandiysa satir dolu olsa da izin kaydi uygulanir.

                    if has_extra:
                        c.execute(
                            "UPDATE gunluk_kayit "
                            "SET giris_saati='', cikis_saati='', kayip_sure_saat='', hesaplanan_normal=?, hesaplanan_mesai=0.0, aciklama=?, "
                            "tersane_id=CASE WHEN COALESCE(tersane_id,0)=0 THEN ? ELSE tersane_id END, "
                            "firma_id=CASE WHEN COALESCE(firma_id,0)=0 THEN ? ELSE firma_id END, "
                            "manuel_kilit=1 "
                            "WHERE id=?",
                            (normal_hak, canonical_izin_turu, personel_tersane_id, personel_firma_id, rec_id)
                        )
                    else:
                        c.execute(
                            "UPDATE gunluk_kayit "
                            "SET giris_saati='', cikis_saati='', kayip_sure_saat='', hesaplanan_normal=?, hesaplanan_mesai=0.0, aciklama=? "
                            "WHERE id=?",
                            (normal_hak, canonical_izin_turu, rec_id)
                        )
                else:
                    if has_extra:
                        c.execute(
                            "INSERT INTO gunluk_kayit "
                            "(tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, hesaplanan_normal, hesaplanan_mesai, aciklama, tersane_id, firma_id, manuel_kilit) "
                            "VALUES (?, ?, '', '', '', ?, 0.0, ?, ?, ?, 1)",
                            (kayit_tarihi, ad_soyad, normal_hak, canonical_izin_turu, personel_tersane_id, personel_firma_id)
                        )
                    else:
                        c.execute(
                            "INSERT INTO gunluk_kayit "
                            "(tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, hesaplanan_normal, hesaplanan_mesai, aciklama) "
                            "VALUES (?, ?, '', '', '', ?, 0.0, ?)",
                            (kayit_tarihi, ad_soyad, normal_hak, canonical_izin_turu)
                        )

                    c.execute(
                        "INSERT OR REPLACE INTO izin_auto_kayit_backup "
                        "(izin_id, tarih, ad_soyad, prev_exists, prev_giris_saati, prev_cikis_saati, prev_kayip_sure_saat, "
                        "prev_hesaplanan_normal, prev_hesaplanan_mesai, prev_aciklama, prev_tersane_id, prev_firma_id, prev_manuel_kilit) "
                        "VALUES (?, ?, ?, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
                        (izin_id, kayit_tarihi, ad_soyad)
                    )

            conn.commit()
            return izin_id


    def add_izin(self, ad_soyad, izin_tarihi, izin_turu, gun_sayisi=1.0, aciklama="", tersane_id=0):
        return self.add_izin_with_auto_kayit(ad_soyad, izin_tarihi, izin_turu, gun_sayisi, aciklama, tersane_id=tersane_id)


    def get_izin_list(self, year, month, tersane_id=None):
        month_str = f"{year}-{month:02d}"
        with self.get_connection() as conn:
            if tersane_id and tersane_id > 0:
                return conn.execute(
                    "SELECT id, ad_soyad, izin_tarihi, izin_turu, gun_sayisi, aciklama, onay_durumu FROM izin_takip "
                    "WHERE izin_tarihi LIKE ? AND TRIM(ad_soyad) IN ("
                    "SELECT DISTINCT TRIM(ad_soyad) FROM personel WHERE tersane_id = ? "
                    "UNION "
                    "SELECT DISTINCT TRIM(ad_soyad) FROM gunluk_kayit WHERE tersane_id = ? AND tarih LIKE ?"
                    ") ORDER BY ad_soyad",
                    (f"{month_str}%", tersane_id, tersane_id, f"{month_str}%")
                ).fetchall()  # WHY: filter izin list to selected tersane via assigned or worked personnel.
            return conn.execute(
                "SELECT id, ad_soyad, izin_tarihi, izin_turu, gun_sayisi, aciklama, onay_durumu FROM izin_takip WHERE izin_tarihi LIKE ? ORDER BY ad_soyad",
                (f"{month_str}%",)
            ).fetchall()
            

    def approve_izin(self, izin_id):
        with self.get_connection() as conn:
            conn.execute("UPDATE izin_takip SET onay_durumu=1 WHERE id=?", (izin_id,))
            conn.commit()

    def process_izin(self, izin_id, tersane_id=0):
        """Var olan bir izin kaydini yeniden uygular ve onayliya ceker."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT ad_soyad, izin_tarihi, izin_turu, gun_sayisi, aciklama FROM izin_takip WHERE id=?",
                (izin_id,),
            ).fetchone()
        if not row:
            return None

        ad_soyad, izin_tarihi, izin_turu, gun_sayisi, aciklama = row
        self.delete_izin(izin_id)
        new_id = self.add_izin_with_auto_kayit(
            ad_soyad,
            izin_tarihi,
            izin_turu,
            gun_sayisi,
            aciklama,
            tersane_id=tersane_id,
        )
        if new_id:
            self.approve_izin(new_id)
        return new_id


    def delete_izin(self, izin_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            row = c.execute("SELECT id, ad_soyad, izin_tarihi, izin_turu, gun_sayisi FROM izin_takip WHERE id=?", (izin_id,)).fetchone()
            if row:
                izin_row_id, ad_soyad, izin_tarihi, izin_turu, gun_sayisi = row
                if self._is_yillik_izin_turu(izin_turu):
                    p_row = c.execute("SELECT yillik_izin_hakki FROM personel WHERE ad_soyad=?", (ad_soyad,)).fetchone()
                    if p_row:
                        conn.execute("UPDATE personel SET yillik_izin_hakki=? WHERE ad_soyad=?", (p_row[0] + gun_sayisi, ad_soyad))

                c.execute("PRAGMA table_info(gunluk_kayit)")
                kayit_cols = {r[1] for r in c.fetchall()}
                has_extra = all(col in kayit_cols for col in ("tersane_id", "firma_id", "manuel_kilit"))

                backup_rows = c.execute(
                    "SELECT tarih, prev_exists, prev_giris_saati, prev_cikis_saati, prev_kayip_sure_saat, "
                    "prev_hesaplanan_normal, prev_hesaplanan_mesai, prev_aciklama, prev_tersane_id, prev_firma_id, prev_manuel_kilit "
                    "FROM izin_auto_kayit_backup WHERE izin_id=? ORDER BY tarih",
                    (izin_row_id,)
                ).fetchall()

                if backup_rows:
                    for b in backup_rows:
                        (
                            tarih, prev_exists, prev_giris, prev_cikis, prev_kayip,
                            prev_normal, prev_mesai, prev_aciklama, prev_tersane, prev_firma, prev_kilit
                        ) = b
                        if int(prev_exists or 0) == 1:
                            if has_extra:
                                c.execute(
                                    "UPDATE gunluk_kayit SET giris_saati=?, cikis_saati=?, kayip_sure_saat=?, "
                                    "hesaplanan_normal=?, hesaplanan_mesai=?, aciklama=?, tersane_id=?, firma_id=?, manuel_kilit=? "
                                    "WHERE tarih=? AND TRIM(ad_soyad)=TRIM(?)",
                                    (
                                        prev_giris or "", prev_cikis or "", prev_kayip or "",
                                        prev_normal if prev_normal is not None else 0.0,
                                        prev_mesai if prev_mesai is not None else 0.0,
                                        prev_aciklama or "",
                                        prev_tersane if prev_tersane is not None else 0,
                                        prev_firma if prev_firma is not None else 0,
                                        prev_kilit if prev_kilit is not None else 0,
                                        tarih, ad_soyad
                                    )
                                )
                            else:
                                c.execute(
                                    "UPDATE gunluk_kayit SET giris_saati=?, cikis_saati=?, kayip_sure_saat=?, "
                                    "hesaplanan_normal=?, hesaplanan_mesai=?, aciklama=? "
                                    "WHERE tarih=? AND TRIM(ad_soyad)=TRIM(?)",
                                    (
                                        prev_giris or "", prev_cikis or "", prev_kayip or "",
                                        prev_normal if prev_normal is not None else 0.0,
                                        prev_mesai if prev_mesai is not None else 0.0,
                                        prev_aciklama or "",
                                        tarih, ad_soyad
                                    )
                                )
                        else:
                            c.execute(
                                "DELETE FROM gunluk_kayit WHERE tarih=? AND TRIM(ad_soyad)=TRIM(?) AND aciklama=?",
                                (tarih, ad_soyad, izin_turu)
                            )
                    c.execute("DELETE FROM izin_auto_kayit_backup WHERE izin_id=?", (izin_row_id,))
                else:
                    for tarih in self._izin_kapsam_tarihleri(izin_tarihi, gun_sayisi):
                        c.execute(
                            "DELETE FROM gunluk_kayit WHERE tarih=? AND TRIM(ad_soyad)=TRIM(?) AND aciklama=?",
                            (tarih, ad_soyad, izin_turu)
                        )
            conn.execute("DELETE FROM izin_takip WHERE id=?", (izin_id,))
            conn.commit()

    # Vardiyalar

    def add_vardiya(self, vardiya_adi, baslangic_saati, bitis_saati, normal_saat=8.0):
        with self.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO vardiya (vardiya_adi, baslangic_saati, bitis_saati, normal_saat) VALUES (?, ?, ?, ?)", (vardiya_adi, baslangic_saati, bitis_saati, normal_saat)); conn.commit()


    def get_vardiyalar(self):
        with self.get_connection() as conn:
            return conn.execute("SELECT id, vardiya_adi, baslangic_saati, bitis_saati, normal_saat FROM vardiya").fetchall()


    def assign_personel_vardiya(self, ad_soyad, vardiya_id):
        with self.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO personel_vardiya (ad_soyad, vardiya_id, atama_tarihi) VALUES (?, ?, ?)", (ad_soyad, vardiya_id, datetime.now().isoformat())); conn.commit()


    def get_personel_vardiya(self, ad_soyad):
        with self.get_connection() as conn:
            return conn.execute("SELECT v.vardiya_adi, v.baslangic_saati, v.bitis_saati FROM personel_vardiya pv JOIN vardiya v ON pv.vardiya_id = v.id WHERE pv.ad_soyad=?", (ad_soyad,)).fetchone()

    # Katsayılar ve Ayarlar

    def get_setting(self, key, default_val):
        with self.get_connection() as conn:
            res = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return res[0] if res else default_val


    def update_setting(self, key, value):
        with self.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value))); conn.commit()
        self._invalidate_cache(groups=['settings_cache'])  # WHY: global settings changed, cached rules must refresh.

    def get_tersane_setting(self, key, default_val, tersane_id=None, fallback_global=True):
        """Tersane bazlı ayar okur; yoksa global ayara (veya default'a) düşer."""
        # NEW: tersane_id optional to keep old global-only behavior unchanged.
        if not tersane_id or tersane_id <= 0:
            return self.get_setting(key, default_val) if fallback_global else default_val  # SAFE: global fallback preserves old behavior.
        try:
            with self.get_connection() as conn:
                res = conn.execute(
                    "SELECT value FROM tersane_ayarlar WHERE tersane_id=? AND key=?",
                    (tersane_id, key)
                ).fetchone()
            if res and res[0] is not None:
                return res[0]
        except Exception:
            pass  # SAFEGUARD: if per-shipyard read fails, fall back safely.
        return self.get_setting(key, default_val) if fallback_global else default_val

    def update_tersane_setting(self, tersane_id, key, value):
        """Tersane bazlı ayar yazar; global ayarları bozmaz."""
        # NEW: store per-shipyard settings without renaming or removing existing settings.
        if not tersane_id or tersane_id <= 0:
            return self.update_setting(key, value)  # SAFE: fallback to global if tersane_id is invalid.
        try:
            with self.get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO tersane_ayarlar (tersane_id, key, value) VALUES (?, ?, ?)",
                    (tersane_id, key, str(value))
                )
                conn.commit()
            self._invalidate_cache(groups=['settings_cache'], tersane_id=tersane_id)  # WHY: per-tersane settings changed.
        except Exception:
            pass  # SAFEGUARD: avoid crashing if write fails.


    def backup_db(self, target_folder):
        try:
            date_str = datetime.now().strftime("%Y-%m-%d_%H-%M")
            filename = f"Yedek_Puantaj_{date_str}.db"
            target_path = os.path.join(target_folder, filename)
            shutil.copy2(self.db_file, target_path)
            return True, target_path
        except Exception as e:
            return False, str(e)


    def apply_migrations(self):
        db_dir = os.path.dirname(os.path.abspath(self.db_file)) or '.'
        backup_folder = os.path.join(db_dir, 'migrations_backups')
        os.makedirs(backup_folder, exist_ok=True)
        ok, _ = self.backup_db(backup_folder)
        
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('PRAGMA user_version')
            res = cur.fetchone()
            cur_ver = res[0] if res else 0
            
            for i, mig in enumerate(migrations.MIGRATIONS, start=1):
                if i > cur_ver:
                    try:
                        with conn:
                            mig(conn)
                            conn.execute(f"PRAGMA user_version = {i}")
                    except Exception as e:
                        raise RuntimeError(f"Migration {i} failed: {e}")
            return cur_ver


    def get_mesai_katsayilari(self, tersane_id=None, fallback_global=True):
        # NEW: tersane_id optional; default keeps old global behavior intact.
        with self.get_connection() as conn:
            c = conn.cursor()
            try:
                if tersane_id and tersane_id > 0:
                    rows = c.execute(
                        "SELECT id, saat_araligi_baslangic, saat_araligi_bitis, katsayi, aciklama FROM mesai_katsayilari WHERE tersane_id=? ORDER BY saat_araligi_baslangic",
                        (tersane_id,)
                    ).fetchall()
                    if rows or not fallback_global:
                        return rows  # SAFE: return shipyard-specific rows when present.
                    # Fallback to global (NULL/0) to preserve legacy defaults.
                    return c.execute(
                        "SELECT id, saat_araligi_baslangic, saat_araligi_bitis, katsayi, aciklama FROM mesai_katsayilari WHERE tersane_id IS NULL OR tersane_id=0 ORDER BY saat_araligi_baslangic"
                    ).fetchall()
                # Global/default rows (legacy behavior)
                return c.execute(
                    "SELECT id, saat_araligi_baslangic, saat_araligi_bitis, katsayi, aciklama FROM mesai_katsayilari WHERE tersane_id IS NULL OR tersane_id=0 ORDER BY saat_araligi_baslangic"
                ).fetchall()
            except Exception:
                # SAFEGUARD: fallback to legacy schema (no tersane_id column).
                try:
                    return c.execute("SELECT id, saat_araligi_baslangic, saat_araligi_bitis, katsayi, aciklama FROM mesai_katsayilari ORDER BY saat_araligi_baslangic").fetchall()
                except Exception:
                    return c.execute("SELECT id, saat_araligi_baslangic, saat_araligi_bitis, mesai_saati, aciklama FROM mesai_katsayilari ORDER BY saat_araligi_baslangic").fetchall()
            

    def add_mesai_katsayisi(self, baslangic, bitis, katsayi, aciklama="", tersane_id=None):
        # NEW: tersane_id optional; when provided, row is scoped to that shipyard.
        with self.get_connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO mesai_katsayilari (saat_araligi_baslangic, saat_araligi_bitis, tersane_id, katsayi, aciklama) VALUES (?, ?, ?, ?, ?)",
                    (baslangic, bitis, tersane_id, katsayi, aciklama)
                )
            except Exception:
                # SAFEGUARD: fallback to legacy schema without tersane_id.
                conn.execute(
                    "INSERT INTO mesai_katsayilari (saat_araligi_baslangic, saat_araligi_bitis, katsayi, aciklama) VALUES (?, ?, ?, ?)",
                    (baslangic, bitis, katsayi, aciklama)
                )
            conn.commit()
        self._invalidate_cache(groups=['settings_cache'], tersane_id=tersane_id)  # WHY: katsayılar değişti, cache tazelenmeli.
            

    def update_mesai_katsayisi(self, id, baslangic, bitis, katsayi, aciklama="", tersane_id=None):
        # NEW: tersane_id optional; update keeps old behavior if not provided.
        with self.get_connection() as conn:
            try:
                if tersane_id is not None:
                    conn.execute(
                        "UPDATE mesai_katsayilari SET saat_araligi_baslangic=?, saat_araligi_bitis=?, katsayi=?, aciklama=?, tersane_id=? WHERE id=?",
                        (baslangic, bitis, katsayi, aciklama, tersane_id, id)
                    )
                else:
                    conn.execute(
                        "UPDATE mesai_katsayilari SET saat_araligi_baslangic=?, saat_araligi_bitis=?, katsayi=?, aciklama=? WHERE id=?",
                        (baslangic, bitis, katsayi, aciklama, id)
                    )
            except Exception:
                # SAFEGUARD: fallback to legacy schema update.
                conn.execute(
                    "UPDATE mesai_katsayilari SET saat_araligi_baslangic=?, saat_araligi_bitis=?, katsayi=?, aciklama=? WHERE id=?",
                    (baslangic, bitis, katsayi, aciklama, id)
                )
            conn.commit()
        self._invalidate_cache(groups=['settings_cache'], tersane_id=tersane_id)  # WHY: katsayılar değişti, cache tazelenmeli.
            

    def delete_mesai_katsayisi(self, id):
        with self.get_connection() as conn:
            conn.execute("DELETE FROM mesai_katsayilari WHERE id=?", (id,)); conn.commit()
        self._invalidate_cache(groups=['settings_cache'])  # WHY: katsayı silindi, cache tazelenmeli.
            

    def get_katsayi_for_mesai_saati(self, mesai_saati, tersane_id=None, fallback_global=True):
        # NEW: tersane_id optional for per-shipyard lookup; fallback keeps old defaults.
        with self.get_connection() as conn:
            try:
                if tersane_id and tersane_id > 0:
                    res = conn.execute(
                        "SELECT katsayi FROM mesai_katsayilari WHERE tersane_id=? AND ? >= saat_araligi_baslangic AND ? < saat_araligi_bitis ORDER BY saat_araligi_baslangic LIMIT 1",
                        (tersane_id, mesai_saati, mesai_saati)
                    ).fetchone()
                    if res:
                        return res[0]
                    if fallback_global:
                        res = conn.execute(
                            "SELECT katsayi FROM mesai_katsayilari WHERE (tersane_id IS NULL OR tersane_id=0) AND ? >= saat_araligi_baslangic AND ? < saat_araligi_bitis ORDER BY saat_araligi_baslangic LIMIT 1",
                            (mesai_saati, mesai_saati)
                        ).fetchone()
                        return res[0] if res else 1.5
                    return 1.5
                res = conn.execute(
                    "SELECT katsayi FROM mesai_katsayilari WHERE (tersane_id IS NULL OR tersane_id=0) AND ? >= saat_araligi_baslangic AND ? < saat_araligi_bitis ORDER BY saat_araligi_baslangic LIMIT 1",
                    (mesai_saati, mesai_saati)
                ).fetchone()
                return res[0] if res else 1.5
            except Exception:
                # SAFEGUARD: legacy schema without tersane_id.
                res = conn.execute(
                    "SELECT katsayi FROM mesai_katsayilari WHERE ? >= saat_araligi_baslangic AND ? < saat_araligi_bitis ORDER BY saat_araligi_baslangic LIMIT 1",
                    (mesai_saati, mesai_saati)
                ).fetchone()
                return res[0] if res else 1.5


    def get_yevmiye_katsayilari(self, tersane_id=None, fallback_global=True):
        # NEW: tersane_id optional; default keeps old global behavior intact.
        with self.get_connection() as conn:
            try:
                if tersane_id and tersane_id > 0:
                    rows = conn.execute(
                        "SELECT id, saat_araligi_baslangic, saat_araligi_bitis, yevmiye_katsayi, aciklama FROM yevmiye_katsayilari WHERE tersane_id=? ORDER BY saat_araligi_baslangic",
                        (tersane_id,)
                    ).fetchall()
                    if rows or not fallback_global:
                        return rows  # SAFE: return shipyard-specific rows when present.
                    return conn.execute(
                        "SELECT id, saat_araligi_baslangic, saat_araligi_bitis, yevmiye_katsayi, aciklama FROM yevmiye_katsayilari WHERE tersane_id IS NULL OR tersane_id=0 ORDER BY saat_araligi_baslangic"
                    ).fetchall()
                return conn.execute(
                    "SELECT id, saat_araligi_baslangic, saat_araligi_bitis, yevmiye_katsayi, aciklama FROM yevmiye_katsayilari WHERE tersane_id IS NULL OR tersane_id=0 ORDER BY saat_araligi_baslangic"
                ).fetchall()
            except Exception:
                # SAFEGUARD: legacy schema without tersane_id.
                return conn.execute("SELECT id, saat_araligi_baslangic, saat_araligi_bitis, yevmiye_katsayi, aciklama FROM yevmiye_katsayilari ORDER BY saat_araligi_baslangic").fetchall()


    def add_yevmiye_katsayisi(self, baslangic, bitis, katsayi, aciklama="", tersane_id=None):
        # NEW: tersane_id optional; when provided, row is scoped to that shipyard.
        with self.get_connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO yevmiye_katsayilari (saat_araligi_baslangic, saat_araligi_bitis, tersane_id, yevmiye_katsayi, aciklama) VALUES (?, ?, ?, ?, ?)",
                    (baslangic, bitis, tersane_id, katsayi, aciklama)
                )
            except Exception:
                # SAFEGUARD: fallback to legacy schema without tersane_id.
                conn.execute(
                    "INSERT INTO yevmiye_katsayilari (saat_araligi_baslangic, saat_araligi_bitis, yevmiye_katsayi, aciklama) VALUES (?, ?, ?, ?)",
                    (baslangic, bitis, katsayi, aciklama)
                )
            conn.commit()
        self._invalidate_cache(groups=['settings_cache'], tersane_id=tersane_id)  # WHY: katsayılar değişti, cache tazelenmeli.
            

    def update_yevmiye_katsayisi(self, id, baslangic, bitis, katsayi, aciklama="", tersane_id=None):
        # NEW: tersane_id optional; update keeps old behavior if not provided.
        with self.get_connection() as conn:
            try:
                if tersane_id is not None:
                    conn.execute(
                        "UPDATE yevmiye_katsayilari SET saat_araligi_baslangic=?, saat_araligi_bitis=?, yevmiye_katsayi=?, aciklama=?, tersane_id=? WHERE id=?",
                        (baslangic, bitis, katsayi, aciklama, tersane_id, id)
                    )
                else:
                    conn.execute(
                        "UPDATE yevmiye_katsayilari SET saat_araligi_baslangic=?, saat_araligi_bitis=?, yevmiye_katsayi=?, aciklama=? WHERE id=?",
                        (baslangic, bitis, katsayi, aciklama, id)
                    )
            except Exception:
                # SAFEGUARD: fallback to legacy schema update.
                conn.execute(
                    "UPDATE yevmiye_katsayilari SET saat_araligi_baslangic=?, saat_araligi_bitis=?, yevmiye_katsayi=?, aciklama=? WHERE id=?",
                    (baslangic, bitis, katsayi, aciklama, id)
                )
            conn.commit()
        self._invalidate_cache(groups=['settings_cache'], tersane_id=tersane_id)  # WHY: katsayılar değişti, cache tazelenmeli.
            

    def delete_yevmiye_katsayisi(self, id):
        with self.get_connection() as conn:
            conn.execute("DELETE FROM yevmiye_katsayilari WHERE id=?", (id,)); conn.commit()
        self._invalidate_cache(groups=['settings_cache'])  # WHY: katsayı silindi, cache tazelenmeli.
            

    def get_yevmiye_katsayi_for_mesai_saati(self, mesai_saati, tersane_id=None, fallback_global=True):
        # NEW: tersane_id optional for per-shipyard lookup; fallback keeps old defaults.
        with self.get_connection() as conn:
            try:
                if tersane_id and tersane_id > 0:
                    res = conn.execute(
                        "SELECT yevmiye_katsayi FROM yevmiye_katsayilari WHERE tersane_id=? AND ? >= saat_araligi_baslangic AND ? < saat_araligi_bitis ORDER BY saat_araligi_baslangic LIMIT 1",
                        (tersane_id, mesai_saati, mesai_saati)
                    ).fetchone()
                    if res:
                        return res[0]
                    if fallback_global:
                        res = conn.execute(
                            "SELECT yevmiye_katsayi FROM yevmiye_katsayilari WHERE (tersane_id IS NULL OR tersane_id=0) AND ? >= saat_araligi_baslangic AND ? < saat_araligi_bitis ORDER BY saat_araligi_baslangic LIMIT 1",
                            (mesai_saati, mesai_saati)
                        ).fetchone()
                        return res[0] if res else 0.5
                    return 0.5
                res = conn.execute(
                    "SELECT yevmiye_katsayi FROM yevmiye_katsayilari WHERE (tersane_id IS NULL OR tersane_id=0) AND ? >= saat_araligi_baslangic AND ? < saat_araligi_bitis ORDER BY saat_araligi_baslangic LIMIT 1",
                    (mesai_saati, mesai_saati)
                ).fetchone()
                return res[0] if res else 0.5
            except Exception:
                # SAFEGUARD: legacy schema without tersane_id.
                res = conn.execute(
                    "SELECT yevmiye_katsayi FROM yevmiye_katsayilari WHERE ? >= saat_araligi_baslangic AND ? < saat_araligi_bitis ORDER BY saat_araligi_baslangic LIMIT 1",
                    (mesai_saati, mesai_saati)
                ).fetchone()
                return res[0] if res else 0.5
