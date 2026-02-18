import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QLineEdit, QFileDialog, QMessageBox, QFrame, QComboBox, QSpinBox,
                             QDialog, QTableWidget, QTableWidgetItem, QHeaderView, QDoubleSpinBox, QScrollArea, QApplication,
                             QProgressDialog, QTabWidget)  # NEW: progress UI for background tasks.
from PySide6.QtGui import QPalette, QColor, QFont
from PySide6.QtCore import Qt, QThread, Signal, Slot, QObject  # NEW: threading helpers for smooth UI.

from core.database import Database
from core.input_validators import (
    ensure_hhmm_time,
    ensure_non_empty,
    ensure_non_negative_number,
    ensure_positive_int,
    parse_hhmm_to_minutes,
)
from core.user_config import load_config, save_config

class RecalcWorker(QObject):
    """Uzun süren yeniden hesaplamayi UI disinda calistirir."""
    progress = Signal(int, int)  # WHY: progress for UI without changing calculation logic.
    finished = Signal(int)  # WHY: notify UI on normal completion (updated_count).
    cancelled = Signal(int)  # WHY: notify UI on user cancel without crashing.
    error = Signal(str)  # WHY: surface errors without blocking UI thread.

    def __init__(self, db, tersane_id=0):
        super().__init__()
        self.db = db  # WHY: keep DB access same as before, only off UI thread.
        self.tersane_id = tersane_id or 0  # WHY: normalize to keep behavior consistent with global (0) mode.
        self._stop_requested = False  # WHY: allow safe cancel without killing the thread.

    def request_stop(self):
        """Arka plan isini guvenle durdur."""
        self._stop_requested = True  # WHY: checked in run loop to stop gracefully.

    @Slot()
    def run(self):
        try:
            from core.hesaplama import hesapla_hakedis
            # Tüm kayıtları al (aktif tersane varsa filtrele)
            with self.db.get_connection() as conn:
                c = conn.cursor()
                sql = """
                    SELECT id, tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat
                    FROM gunluk_kayit
                    WHERE COALESCE(manuel_kilit,0)=0
                """
                params = []
                if self.tersane_id and self.tersane_id > 0:
                    sql += " AND tersane_id = ?"
                    params.append(self.tersane_id)
                sql += " ORDER BY tarih DESC"
                records = c.execute(sql, tuple(params)).fetchall()

            total = len(records)
            self.progress.emit(0, total)

            holiday_set = self.db.get_holidays()
            # Aktif tersane için tek seferlik cache (eski davranışla uyumlu).
            if self.tersane_id and self.tersane_id > 0:
                settings_cache = self.db.get_settings_cache(tersane_id=self.tersane_id)
            else:
                settings_cache = self.db.get_settings_cache()
            shipyard_rules = settings_cache.get('shipyard_rules', settings_cache) if settings_cache else None

            updated_count = 0
            with self.db.get_connection() as conn:
                c = conn.cursor()
                # WHY: move existing calculation block out of cancel guard; logic unchanged below.
                for idx, (rec_id, tarih, ad_soyad, giris_str, cikis_str, kayip_str) in enumerate(records, start=1):
                    # WHY: allow cancel/interrupt without crashing or freezing UI.
                    if self._stop_requested or QThread.currentThread().isInterruptionRequested():
                        self.cancelled.emit(updated_count)  # WHY: notify UI that cancel completed.
                        return  # WHY: exit cleanly to avoid unsafe thread termination.
                    # Personelin yevmiyeci/özel durumunu tek sorgu ile al (mantık değişmez).
                    p_row = c.execute("SELECT yevmiyeci_mi, ozel_durum FROM personel WHERE TRIM(ad_soyad)=TRIM(?)", (ad_soyad,)).fetchone()  # WHY: same lookup, moved out of cancel guard.
                    yevmiyeci_mi = bool(p_row[0]) if p_row else False  # WHY: keep existing boolean conversion.
                    # Hakediş hesapla (mevcut hesaplama mantığı korunur).
                    normal, mesai, notlar = hesapla_hakedis(  # WHY: reuse existing calculation unchanged.
                        tarih, giris_str, cikis_str, kayip_str, holiday_set,  # WHY: pass same inputs as before.
                        self.db.get_holiday_info,  # WHY: keep same holiday lookup.
                        self.db.get_personnel_special_status,  # WHY: keep same special-status lookup.
                        ad_soyad, yevmiyeci_mi, self.db,  # WHY: keep same personnel context.
                        settings_cache=shipyard_rules  # WHY: keep dynamic shipyard rules without changing logic.
                    )
                    c.execute("UPDATE gunluk_kayit SET hesaplanan_normal=?, hesaplanan_mesai=?, aciklama=? WHERE id=?",
                              (normal, mesai, notlar, rec_id))  # WHY: persist same computed values.
                    updated_count += 1  # WHY: track updated rows for summary.
                    if idx % 50 == 0 or idx == total:
                        conn.commit()  # WHY: batch commit keeps UI smooth without changing results.
                    if idx % 10 == 0 or idx == total:
                        self.progress.emit(idx, total)  # WHY: update progress without blocking UI.

            self.finished.emit(updated_count)  # WHY: normal completion signal.
        except Exception as e:
            self.error.emit(str(e))

class SettingsPage(QWidget):
    def __init__(self, signal_manager):
        super().__init__()
        self.db = Database()
        self.signal_manager = signal_manager
        self.tersane_id = 0  # NEW: active tersane id; 0 preserves old global behavior.
        self._needs_refresh = False  # NEW: lazy-load flag to avoid heavy refresh on hidden tabs.
        self._recalc_thread = None  # NEW: keep thread reference to avoid premature GC.
        self._recalc_worker = None  # NEW: keep worker reference to avoid GC while thread runs.
        self._recalc_dialog = None  # NEW: progress dialog reference for background recalc.
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Scroll area ekle
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        
        # İçerik widget'ı
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tab_general = QWidget()
        tab_general_layout = QVBoxLayout(tab_general)
        tab_management = QWidget()
        tab_management_layout = QVBoxLayout(tab_management)
        tab_security = QWidget()
        tab_security_layout = QVBoxLayout(tab_security)
        tab_maintenance = QWidget()
        tab_maintenance_layout = QVBoxLayout(tab_maintenance)

        # --- PARAMETRELER ---
        param_frame = QFrame()
        param_frame.setStyleSheet("border-radius: 8px; padding: 15px;")
        p_layout = QVBoxLayout(param_frame)
        p_layout.addWidget(QLabel("⚙️ Hesaplama Parametreleri"))
        self.lbl_active_tersane = QLabel("")  # NEW: shows which tersane's rules are being edited.
        self.lbl_active_tersane.setStyleSheet("color: #90CAF9; font-size: 11px;")
        p_layout.addWidget(self.lbl_active_tersane)
        
        h_layout = QHBoxLayout()
        h_layout.addWidget(QLabel("Pazar Günü Sabit Mesai (Saat):"))
        self.input_sunday = QLineEdit()
        self.input_sunday.setText(str(self.db.get_setting("pazar_mesaisi", 15.0)))
        h_layout.addWidget(self.input_sunday)
        p_layout.addLayout(h_layout)
        
        # Mesai başlangıç saati
        h_mesai = QHBoxLayout()
        h_mesai.addWidget(QLabel("Mesai Başlangıç Saati:"))
        self.input_mesai_baslangic = QLineEdit()
        self.input_mesai_baslangic.setText(str(self.db.get_setting("mesai_baslangic_saat", "17:30")))
        self.input_mesai_baslangic.setPlaceholderText("HH:MM (örn: 17:30)")
        self.input_mesai_baslangic.setToolTip("Normal mesai bitiş saati. Bu saatten sonra mesai başlar.")
        h_mesai.addWidget(self.input_mesai_baslangic)
        p_layout.addLayout(h_mesai)
        
        # En erken çıkış saati
        h_erken_cikis = QHBoxLayout()
        h_erken_cikis.addWidget(QLabel("En Erken Çıkış Saati:"))
        self.input_en_erken_cikis = QLineEdit()
        self.input_en_erken_cikis.setText(str(self.db.get_setting("en_erken_cikis_saat", "19:30")))
        self.input_en_erken_cikis.setPlaceholderText("HH:MM (örn: 19:30)")
        self.input_en_erken_cikis.setToolTip("Bu saatten sonra çıkılması durumunda sabit mesai/yevmiye katsayısı uygulanır.")
        h_erken_cikis.addWidget(self.input_en_erken_cikis)
        p_layout.addLayout(h_erken_cikis)

        h_hesap_modu = QHBoxLayout()
        h_hesap_modu.addWidget(QLabel("Calisma Hesaplama Modu:"))
        self.combo_calisma_modu = QComboBox()
        self.combo_calisma_modu.addItems([
            "Cezadan Dus (7.5 - ceza)",
            "Fiili Calisma (net saat)"
        ])
        mevcut_mod = str(self.db.get_setting("calisma_hesaplama_modu", "cezadan_dus")).strip().lower()
        self.combo_calisma_modu.setCurrentIndex(1 if mevcut_mod == "fiili_calisma" else 0)
        h_hesap_modu.addWidget(self.combo_calisma_modu)
        p_layout.addLayout(h_hesap_modu)

        h_ogle = QHBoxLayout()
        h_ogle.addWidget(QLabel("Ogle Molasi:"))
        self.input_ogle_baslangic = QLineEdit()
        self.input_ogle_baslangic.setText(str(self.db.get_setting("ogle_molasi_baslangic", "12:15")))
        self.input_ogle_baslangic.setPlaceholderText("HH:MM (orn: 12:15)")
        self.input_ogle_bitis = QLineEdit()
        self.input_ogle_bitis.setText(str(self.db.get_setting("ogle_molasi_bitis", "13:15")))
        self.input_ogle_bitis.setPlaceholderText("HH:MM (orn: 13:15)")
        h_ogle.addWidget(self.input_ogle_baslangic)
        h_ogle.addWidget(QLabel("-"))
        h_ogle.addWidget(self.input_ogle_bitis)
        p_layout.addLayout(h_ogle)

        h_ara_mola = QHBoxLayout()
        h_ara_mola.addWidget(QLabel("Gunluk Ara Mola (dk):"))
        self.input_ara_mola_dk = QSpinBox()
        self.input_ara_mola_dk.setRange(0, 120)
        try:
            self.input_ara_mola_dk.setValue(max(0, int(float(self.db.get_setting("ara_mola_dk", 20)))))
        except (ValueError, TypeError):
            self.input_ara_mola_dk.setValue(20)
        h_ara_mola.addWidget(self.input_ara_mola_dk)
        h_ara_mola.addWidget(QLabel("Fiili Saat Yuvarlama:"))
        self.combo_fiili_yuvarlama = QComboBox()
        self.combo_fiili_yuvarlama.addItems([
            "Ondalik (2 hane)",
            "Yarim Saat",
            "Tam Saat (yukari)"
        ])
        yuvarlama = str(self.db.get_setting("fiili_saat_yuvarlama", "ondalik")).strip().lower()
        if yuvarlama == "yarim_saat":
            self.combo_fiili_yuvarlama.setCurrentIndex(1)
        elif yuvarlama == "tam_saat":
            self.combo_fiili_yuvarlama.setCurrentIndex(2)
        else:
            self.combo_fiili_yuvarlama.setCurrentIndex(0)
        h_ara_mola.addWidget(self.combo_fiili_yuvarlama)
        p_layout.addLayout(h_ara_mola)

        # Salary basis controls
        h2 = QHBoxLayout()
        h2.addWidget(QLabel("Maaş Bazı:"))
        self.combo_salary_basis = QComboBox()
        self.combo_salary_basis.addItems(["Sabit Gün (fixed_days)", "Gerçek Ay Günü (actual_days)"])
        current_basis = self.db.get_setting("salary_basis", "fixed_days")
        if current_basis == 'fixed_days':
            self.combo_salary_basis.setCurrentIndex(0)
        else:
            self.combo_salary_basis.setCurrentIndex(1)
        h2.addWidget(self.combo_salary_basis)

        h2.addWidget(QLabel("Sabit Gün Sayısı:"))
        self.input_salary_days = QSpinBox()
        self.input_salary_days.setRange(1, 365)
        self.input_salary_days.setFixedWidth(80)
        self.input_salary_days.setValue(int(self.db.get_setting("salary_days", 30)))
        self.input_salary_days.setToolTip("Maaş hesaplamasında kullanılacak sabit gün sayısı (ör. 30)")
        h2.addWidget(self.input_salary_days)
        p_layout.addLayout(h2)

        btn_save = QPushButton("💾 Ayarları Kaydet")
        btn_save.setStyleSheet("background-color: #2196F3; color: white; padding: 10px;")
        btn_save.clicked.connect(self.save_settings)
        p_layout.addWidget(btn_save)
        
        tab_general_layout.addWidget(param_frame)

        # --- RAPOR AYARLARI: Logo seçimi ---
        logo_frame = QFrame()
        logo_frame.setStyleSheet("border-radius: 8px; padding: 10px; margin-top: 15px;")
        l_layout = QHBoxLayout(logo_frame)
        l_layout.addWidget(QLabel("📎 Rapor Logo (Excel):"))
        self.input_logo = QLineEdit()
        self.input_logo.setReadOnly(True)
        self.input_logo.setStyleSheet("padding: 5px; color: white;")
        self.input_logo.setText(self.db.get_setting('export_logo_path', ''))
        btn_browse_logo = QPushButton("📁 Seç")
        btn_browse_logo.clicked.connect(self.browse_logo)
        l_layout.addWidget(self.input_logo)
        l_layout.addWidget(btn_browse_logo)
        tab_general_layout.addWidget(logo_frame)

        # --- KULLANICI / SIFRE ---
        cred_frame = QFrame()
        cred_frame.setStyleSheet("border-radius: 8px; padding: 15px; background-color: #23272e;")
        cred_layout = QVBoxLayout(cred_frame)
        cred_layout.addWidget(QLabel("Kullanici / Sifre"))
        self.lbl_default_pwd_warn = QLabel("")
        self.lbl_default_pwd_warn.setStyleSheet("color: #FF7043; font-size: 11px;")
        cred_layout.addWidget(self.lbl_default_pwd_warn)

        row_user = QHBoxLayout()
        row_user.addWidget(QLabel("Kullanici Adi:"))
        self.input_admin_user = QLineEdit()
        self.input_admin_user.setText(self.db.get_setting("admin_username", "admin"))
        row_user.addWidget(self.input_admin_user)
        cred_layout.addLayout(row_user)

        row_current = QHBoxLayout()
        row_current.addWidget(QLabel("Mevcut Sifre:"))
        self.input_current_password = QLineEdit()
        self.input_current_password.setEchoMode(QLineEdit.Password)
        row_current.addWidget(self.input_current_password)
        cred_layout.addLayout(row_current)

        row_new = QHBoxLayout()
        row_new.addWidget(QLabel("Yeni Sifre:"))
        self.input_new_password = QLineEdit()
        self.input_new_password.setEchoMode(QLineEdit.Password)
        row_new.addWidget(self.input_new_password)
        cred_layout.addLayout(row_new)

        row_new2 = QHBoxLayout()
        row_new2.addWidget(QLabel("Yeni Sifre (Tekrar):"))
        self.input_new_password2 = QLineEdit()
        self.input_new_password2.setEchoMode(QLineEdit.Password)
        row_new2.addWidget(self.input_new_password2)
        cred_layout.addLayout(row_new2)

        btn_save_cred = QPushButton("Sifreyi Kaydet")
        btn_save_cred.setStyleSheet("background-color: #6A1B9A; color: white; padding: 8px;")
        btn_save_cred.clicked.connect(self.save_credentials)
        cred_layout.addWidget(btn_save_cred)

        tab_security_layout.addWidget(cred_frame)
        self._update_default_pwd_warning()
        
        # --- TERSANE YÖNETİMİ ---
        tersane_frame = QFrame()
        tersane_frame.setStyleSheet("border-radius: 8px; padding: 15px; background-color: #23272e;")
        t_layout = QVBoxLayout(tersane_frame)

        lbl_tersane = QLabel("🏗️ Tersane Yönetimi")
        lbl_tersane.setStyleSheet("font-weight: bold; font-size: 14px; margin-bottom: 10px;")
        t_layout.addWidget(lbl_tersane)

        lbl_tersane_info = QLabel("Her tersane için farklı giriş/çıkış saatleri tanımlayabilirsiniz.\n"
                                   "Veri yükleme sırasında seçilen tersanenin saatleri hesaplamada kullanılır.")
        lbl_tersane_info.setStyleSheet("color: #90CAF9; font-size: 11px; margin-bottom: 8px;")
        lbl_tersane_info.setWordWrap(True)
        t_layout.addWidget(lbl_tersane_info)

        btn_manage_tersane = QPushButton("🏗️ Tersaneleri Yönet")
        btn_manage_tersane.setStyleSheet("background-color: #00897B; color: white; padding: 10px; border-radius: 5px; font-weight: bold;")
        btn_manage_tersane.clicked.connect(self.open_tersane_yonetimi)
        t_layout.addWidget(btn_manage_tersane)

        tab_management_layout.addWidget(tersane_frame)

        # --- MESAİ KATSAYILARI ---
        mesai_frame = QFrame()
        mesai_frame.setStyleSheet("border-radius: 8px; padding: 15px;")
        m_layout = QVBoxLayout(mesai_frame)

        lbl_mesai = QLabel("⏰ Mesai ve Yevmiye Kuralları")
        lbl_mesai.setStyleSheet("font-weight: bold; font-size: 14px; margin-bottom: 10px;")
        m_layout.addWidget(lbl_mesai)
        
        btn_manage_katsayilar = QPushButton("📊 Mesai Kurallarını Yönet")
        btn_manage_katsayilar.setStyleSheet("background-color: #FF9800; color: white; padding: 10px; border-radius: 5px;")
        btn_manage_katsayilar.clicked.connect(self.open_mesai_katsayilari)
        m_layout.addWidget(btn_manage_katsayilar)
        
        btn_manage_yevmiye = QPushButton("🏗️ Yevmiye Kurallarını Yönet")
        btn_manage_yevmiye.setStyleSheet("background-color: #9C27B0; color: white; padding: 10px; border-radius: 5px;")
        btn_manage_yevmiye.clicked.connect(self.open_yevmiye_katsayilari)
        m_layout.addWidget(btn_manage_yevmiye)
        
        tab_management_layout.addWidget(mesai_frame)
        
        # --- YEDEKLEME ---
        backup_frame = QFrame()
        backup_frame.setStyleSheet("border-radius: 8px; padding: 15px; margin-top: 20px;")
        b_layout = QVBoxLayout(backup_frame)
        b_layout.addWidget(QLabel("💾 Veritabanı İşlemleri"))
        
        # Veritabanı konumu
        db_location = QLabel(f"📁 Veritabanı: {self.db.db_file}")
        db_location.setStyleSheet("color: #90CAF9; font-size: 10px; padding: 5px;")
        db_location.setWordWrap(True)
        b_layout.addWidget(db_location)
        
        h_backup = QHBoxLayout()
        btn_backup = QPushButton("📂 Yedek Al")
        btn_backup.setStyleSheet("background-color: #4CAF50; color: white; padding: 10px;")
        btn_backup.clicked.connect(self.backup)
        h_backup.addWidget(btn_backup)
        
        btn_show_folder = QPushButton("📂 Klasörü Aç")
        btn_show_folder.setStyleSheet("background-color: #2196F3; color: white; padding: 10px;")
        btn_show_folder.clicked.connect(self.open_db_folder)
        h_backup.addWidget(btn_show_folder)
        b_layout.addLayout(h_backup)
        
        # TEHLİKELİ BÖLGE - SIFIRLAMA BUTONU
        btn_reset = QPushButton("🗑️ TÜM VERİLERİ SİL (SIFIRLA)")
        btn_reset.setStyleSheet("background-color: #b71c1c; color: white; font-weight: bold; padding: 10px; margin-top: 10px;")
        btn_reset.clicked.connect(self.reset_db)
        
        danger_frame = QFrame()
        danger_frame.setStyleSheet("border-radius: 8px; padding: 15px; margin-top: 12px; background-color: #2a1f1f; border: 1px solid #b71c1c;")
        d_layout = QVBoxLayout(danger_frame)
        lbl_danger = QLabel("Tehlikeli Islemler")
        lbl_danger.setStyleSheet("color: #ff8a80; font-weight: bold;")
        d_layout.addWidget(lbl_danger)
        d_layout.addWidget(btn_reset)
        
        tab_maintenance_layout.addWidget(backup_frame)
        tab_maintenance_layout.addWidget(danger_frame)

        # --- AY KİLİDİ ---
        lock_frame = QFrame()
        lock_frame.setStyleSheet("border-radius: 8px; padding: 15px; margin-top: 20px; background-color: #23272e;")
        lock_layout = QVBoxLayout(lock_frame)
        lock_layout.addWidget(QLabel("🔒 Ay Kilidi"))

        h_lock = QHBoxLayout()
        # Yıl seçici
        from datetime import datetime
        now = datetime.now()
        self.combo_lock_year = QComboBox()
        years = [str(y) for y in range(now.year-2, now.year+2)]
        self.combo_lock_year.addItems(years)
        self.combo_lock_year.setCurrentText(str(now.year))
        h_lock.addWidget(QLabel("Yıl:"))
        h_lock.addWidget(self.combo_lock_year)
        # Ay seçici
        self.combo_lock_month = QComboBox()
        self.combo_lock_month.addItems(["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"])
        self.combo_lock_month.setCurrentIndex(now.month-1)
        h_lock.addWidget(QLabel("Ay:"))
        h_lock.addWidget(self.combo_lock_month)
        # Durum etiketi
        self.lbl_lock_status = QLabel()
        self.lbl_lock_status.setStyleSheet("font-weight: bold; color: #FFD600; margin-left: 20px;")
        h_lock.addWidget(self.lbl_lock_status)
        h_lock.addStretch()
        lock_layout.addLayout(h_lock)

        # Butonlar
        btn_lock = QPushButton("Bu Ayı Kilitle")
        btn_lock.setStyleSheet("background-color: #b71c1c; color: white; padding: 8px 16px; font-weight: bold;")
        btn_unlock = QPushButton("Kilidi Aç")
        btn_unlock.setStyleSheet("background-color: #388e3c; color: white; padding: 8px 16px; font-weight: bold; margin-left: 10px;")
        h_btns = QHBoxLayout()
        h_btns.addWidget(btn_lock)
        h_btns.addWidget(btn_unlock)
        h_btns.addStretch()
        lock_layout.addLayout(h_btns)

        tab_security_layout.addWidget(lock_frame)
        tab_general_layout.addStretch()
        tab_management_layout.addStretch()
        tab_security_layout.addStretch()
        tab_maintenance_layout.addStretch()
        tabs.addTab(tab_general, "Genel")
        tabs.addTab(tab_management, "Yönetim")
        tabs.addTab(tab_security, "Güvenlik")
        tabs.addTab(tab_maintenance, "Bakım")
        content_layout.addWidget(tabs)

        # Scroll area'yı ayarla
        scroll.setWidget(content_widget)
        layout.addWidget(scroll)

        # Olaylar
        self.combo_lock_year.currentTextChanged.connect(self.update_lock_status)
        self.combo_lock_month.currentIndexChanged.connect(self.update_lock_status)
        btn_lock.clicked.connect(self.lock_selected_month)
        btn_unlock.clicked.connect(self.unlock_selected_month)
        self.update_lock_status()
        self._load_settings_for_tersane()  # NEW: load settings for active tersane without breaking global defaults.

    def update_lock_status(self):
        year = int(self.combo_lock_year.currentText())
        month = self.combo_lock_month.currentIndex() + 1
        firma_id = getattr(self.db, 'current_firma_id', 1)
        locked = self.db.is_month_locked(year, month, firma_id)
        if locked:
            self.lbl_lock_status.setText("Durum: Kilitli")
            self.lbl_lock_status.setStyleSheet("font-weight: bold; color: #FF5252; margin-left: 20px;")
        else:
            self.lbl_lock_status.setText("Durum: Açık")
            self.lbl_lock_status.setStyleSheet("font-weight: bold; color: #00E676; margin-left: 20px;")

    def set_tersane_id(self, tersane_id, refresh=True):
        """Global tersane seçiciden gelen tersane_id'yi set eder ve ayarları yeniler."""
        # NEW: tersane_id is stored to scope settings without breaking global defaults.
        try:
            self.tersane_id = tersane_id or 0
            self._needs_refresh = True  # NEW: mark dirty; refresh can be deferred.
            if refresh:
                self.update_view()  # WHY: only visible page refreshes to keep UI smooth.
        except Exception:
            pass  # SAFEGUARD: ignore selector errors to avoid crashing UI.

    def update_view(self):
        """Görünür sayfa için güncel tersane ayarlarını yükle."""
        self._needs_refresh = False  # WHY: clear dirty flag after refresh.
        self._load_settings_for_tersane()

    def refresh_if_needed(self):
        """Lazy-load için: sayfa görünür olduğunda gerekiyorsa güncelle."""
        if self._needs_refresh:
            self.update_view()

    def _load_settings_for_tersane(self):
        """Aktif tersane için mesai/zaman kurallarını UI'ya yükler."""
        try:
            tid = self.tersane_id or 0  # SAFE: 0 = global fallback (old behavior).
            # NEW: per-shipyard settings with global fallback (prevents breaking existing installs).
            pazar_val = self.db.get_tersane_setting("pazar_mesaisi", 15.0, tid, fallback_global=True)
            mesai_val = self.db.get_tersane_setting("mesai_baslangic_saat", "17:30", tid, fallback_global=True)
            erken_val = self.db.get_tersane_setting("en_erken_cikis_saat", "19:30", tid, fallback_global=True)
            calisma_modu = str(self.db.get_tersane_setting("calisma_hesaplama_modu", "cezadan_dus", tid, fallback_global=True)).strip().lower()
            ogle_baslangic = self.db.get_tersane_setting("ogle_molasi_baslangic", "12:15", tid, fallback_global=True)
            ogle_bitis = self.db.get_tersane_setting("ogle_molasi_bitis", "13:15", tid, fallback_global=True)
            ara_mola_dk = self.db.get_tersane_setting("ara_mola_dk", "20", tid, fallback_global=True)
            yuvarlama = str(self.db.get_tersane_setting("fiili_saat_yuvarlama", "ondalik", tid, fallback_global=True)).strip().lower()
            # NEW: set fields safely even if values are missing/None.
            self.input_sunday.setText(str(pazar_val) if pazar_val is not None else "")
            self.input_mesai_baslangic.setText(str(mesai_val) if mesai_val is not None else "")
            self.input_en_erken_cikis.setText(str(erken_val) if erken_val is not None else "")
            self.combo_calisma_modu.setCurrentIndex(1 if calisma_modu == "fiili_calisma" else 0)
            self.input_ogle_baslangic.setText(str(ogle_baslangic) if ogle_baslangic is not None else "")
            self.input_ogle_bitis.setText(str(ogle_bitis) if ogle_bitis is not None else "")
            try:
                self.input_ara_mola_dk.setValue(max(0, int(float(ara_mola_dk))))
            except (ValueError, TypeError):
                self.input_ara_mola_dk.setValue(20)
            if yuvarlama == "yarim_saat":
                self.combo_fiili_yuvarlama.setCurrentIndex(1)
            elif yuvarlama == "tam_saat":
                self.combo_fiili_yuvarlama.setCurrentIndex(2)
            else:
                self.combo_fiili_yuvarlama.setCurrentIndex(0)
            # NEW: update active tersane label for clarity.
            if hasattr(self, "lbl_active_tersane"):
                if tid > 0:
                    tersane = self.db.get_tersane(tid)
                    tersane_name = tersane['ad'] if tersane else f"ID {tid}"
                    self.lbl_active_tersane.setText(f"Aktif Tersane: {tersane_name}")
                else:
                    self.lbl_active_tersane.setText("Aktif Tersane: Tüm Tersaneler (global)")
        except Exception:
            pass  # SAFEGUARD: UI load should never crash the app.

    def lock_selected_month(self):
        year = int(self.combo_lock_year.currentText())
        month = self.combo_lock_month.currentIndex() + 1
        firma_id = getattr(self.db, 'current_firma_id', 1)
        self.db.lock_month(year, month, firma_id)
        QMessageBox.information(self, "Ay Kilitlendi", f"{year} - {month} dönemi başarıyla kilitlendi.")
        self.update_lock_status()

    def unlock_selected_month(self):
        year = int(self.combo_lock_year.currentText())
        month = self.combo_lock_month.currentIndex() + 1
        firma_id = getattr(self.db, 'current_firma_id', 1)
        self.db.unlock_month(year, month, firma_id)
        QMessageBox.information(self, "Kilit Açıldı", f"{year} - {month} dönemi kilidi kaldırıldı.")
        self.update_lock_status()

    def save_settings(self):
        try:
            # NEW: determine active tersane scope; 0 keeps legacy global behavior.
            target_tid = self.tersane_id if (self.tersane_id and self.tersane_id > 0) else 0

            # Pazar mesaisi - sayi kontrolu
            ok, val = ensure_non_negative_number(self.input_sunday.text(), "Pazar Gunu Sabit Mesai")
            if not ok:
                QMessageBox.warning(self, "Hata", f"{val} (orn: 15.0)")
                return
            if target_tid > 0:
                self.db.update_tersane_setting(target_tid, "pazar_mesaisi", val)  # NEW: save under active tersane.
            else:
                self.db.update_setting("pazar_mesaisi", val)  # SAFE: preserve old global setting behavior.
            
            # Mesai başlangıç saati kaydet - format kontrolü
            mesai_baslangic = self.input_mesai_baslangic.text().strip()
            if mesai_baslangic:
                ok, mesai_baslangic = ensure_hhmm_time(mesai_baslangic, "Mesai Baslangic Saati")
                if not ok:
                    QMessageBox.warning(self, "Hata", mesai_baslangic)
                    return
                if target_tid > 0:
                    self.db.update_tersane_setting(target_tid, "mesai_baslangic_saat", mesai_baslangic)  # NEW: per-tersane save.
                else:
                    self.db.update_setting("mesai_baslangic_saat", mesai_baslangic)  # SAFE: legacy global behavior.
            
            # En erken çıkış saati kaydet - format kontrolü
            en_erken_cikis = self.input_en_erken_cikis.text().strip()
            if en_erken_cikis:
                ok, en_erken_cikis = ensure_hhmm_time(en_erken_cikis, "En Erken Cikis Saati")
                if not ok:
                    QMessageBox.warning(self, "Hata", en_erken_cikis)
                    return
                if target_tid > 0:
                    self.db.update_tersane_setting(target_tid, "en_erken_cikis_saat", en_erken_cikis)  # NEW: per-tersane save.
                else:
                    self.db.update_setting("en_erken_cikis_saat", en_erken_cikis)  # SAFE: legacy global behavior.

            calisma_modu = "fiili_calisma" if self.combo_calisma_modu.currentIndex() == 1 else "cezadan_dus"
            ogle_baslangic = self.input_ogle_baslangic.text().strip()
            ogle_bitis = self.input_ogle_bitis.text().strip()
            if ogle_baslangic:
                ok, ogle_baslangic = ensure_hhmm_time(ogle_baslangic, "Ogle molasi baslangici")
                if not ok:
                    QMessageBox.warning(self, "Hata", ogle_baslangic)
                    return
            if ogle_bitis:
                ok, ogle_bitis = ensure_hhmm_time(ogle_bitis, "Ogle molasi bitisi")
                if not ok:
                    QMessageBox.warning(self, "Hata", ogle_bitis)
                    return
            if ogle_baslangic and ogle_bitis:
                if parse_hhmm_to_minutes(ogle_baslangic) >= parse_hhmm_to_minutes(ogle_bitis):
                    QMessageBox.warning(self, "Hata", "Ogle molasi baslangici bitisten kucuk olmali.")
                    return
            yuvarlama_map = {0: "ondalik", 1: "yarim_saat", 2: "tam_saat"}
            yuvarlama_modu = yuvarlama_map.get(self.combo_fiili_yuvarlama.currentIndex(), "ondalik")
            ara_mola = int(self.input_ara_mola_dk.value())

            if target_tid > 0:
                self.db.update_tersane_setting(target_tid, "calisma_hesaplama_modu", calisma_modu)
                self.db.update_tersane_setting(target_tid, "ogle_molasi_baslangic", ogle_baslangic or "12:15")
                self.db.update_tersane_setting(target_tid, "ogle_molasi_bitis", ogle_bitis or "13:15")
                self.db.update_tersane_setting(target_tid, "ara_mola_dk", ara_mola)
                self.db.update_tersane_setting(target_tid, "fiili_saat_yuvarlama", yuvarlama_modu)
            else:
                self.db.update_setting("calisma_hesaplama_modu", calisma_modu)
                self.db.update_setting("ogle_molasi_baslangic", ogle_baslangic or "12:15")
                self.db.update_setting("ogle_molasi_bitis", ogle_bitis or "13:15")
                self.db.update_setting("ara_mola_dk", ara_mola)
                self.db.update_setting("fiili_saat_yuvarlama", yuvarlama_modu)
            
            # salary settings
            basis_text = self.combo_salary_basis.currentText()
            basis = 'fixed_days' if 'Sabit' in basis_text else 'actual_days'
            self.db.update_setting('salary_basis', basis)
            ok, sd = ensure_positive_int(self.input_salary_days.text(), "Sabit gun sayisi", min_value=1)
            if not ok:
                QMessageBox.warning(self, "Hata", sd)
                return
            self.db.update_setting('salary_days', sd)

            # export logo
            logo_path = self.input_logo.text().strip() if hasattr(self, 'input_logo') else ''
            self.db.update_setting('export_logo_path', logo_path)
            
            # Mesai ayarları değişmişse tüm kayıtları yeniden hesapla
            reply = QMessageBox.question(
                self, 
                "Kayıtları Yeniden Hesapla", 
                "Mesai ayarları değiştirildi.\n\nTüm kayıtlar yeniden hesaplandığında doğru değerleri kullanacaklar.\n\nŞimdi yeniden hesaplamak istiyor musunuz?",
                QMessageBox.Yes | QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                # NEW: Recalc işlemini UI thread'i dışında çalıştır (donmayı engeller).
                self._start_recalc_worker(target_tid)
                return  # WHY: sonuç mesajı worker tamamlandığında gösterilecek.
            else:
                QMessageBox.information(self, "Başarılı", "Ayarlar kaydedildi. Tema ve font değişiklikleri uygulanmıştır.")
        except Exception as e:
            QMessageBox.warning(self, "Hata", f"Ayarlar kaydedilirken hata: {str(e)}")

    def _start_recalc_worker(self, tersane_id):
        """Arka planda yeniden hesaplama başlatır (UI donmasını engeller)."""
        try:
            if self._recalc_thread and self._recalc_thread.isRunning():
                return  # WHY: do not start a second recalc while one is running.
            # Progress dialog (spinner + progress)
            self._recalc_dialog = QProgressDialog("Kaydediliyor... Yeniden hesaplaniyor.", None, 0, 0, self)  # WHY: keep same UI text but make it cancel-safe.
            self._recalc_dialog.setWindowModality(Qt.WindowModal)  # WHY: preserve modal behavior.
            self._recalc_dialog.setAutoClose(False)  # WHY: we close explicitly on signals to avoid stuck dialogs.
            self._recalc_dialog.setAutoReset(False)  # WHY: keep progress state until we close safely.
            self._recalc_dialog.setMinimumDuration(0)  # WHY: show immediately to prevent perceived freeze.
            self._recalc_dialog.setAttribute(Qt.WA_DeleteOnClose, False)  # WHY: prevent C++ object deletion before signals stop.
            self._recalc_dialog.canceled.connect(self._on_recalc_dialog_canceled)  # WHY: allow safe user cancel without crashing.
            self._recalc_dialog.rejected.connect(self._on_recalc_dialog_canceled)  # WHY: handle window close (X) safely.
            self._recalc_dialog.show()  # WHY: keep user feedback during background work.

            self._recalc_thread = QThread()  # WHY: run heavy work off the UI thread.
            worker = RecalcWorker(self.db, tersane_id)  # WHY: keep existing worker logic, just manage lifecycle safely.
            self._recalc_worker = worker  # WHY: keep a strong reference to avoid GC while running.
            worker.moveToThread(self._recalc_thread)  # WHY: execute worker in background thread.
            self._recalc_thread.started.connect(worker.run)  # WHY: start work when thread starts.
            worker.progress.connect(self._on_recalc_progress)  # WHY: update UI safely from worker signals.
            worker.finished.connect(self._on_recalc_finished)  # WHY: close dialog and notify success on completion.
            worker.finished.connect(self._recalc_dialog.accept)  # WHY: ensure dialog closes even if handler fails.
            worker.finished.connect(self._recalc_thread.quit)  # WHY: stop thread event loop after completion.
            worker.finished.connect(worker.deleteLater)  # WHY: free worker object safely in Qt.
            worker.cancelled.connect(self._on_recalc_cancelled)  # WHY: handle user cancel without crashing.
            worker.cancelled.connect(self._recalc_dialog.accept)  # WHY: close dialog on cancel to avoid hanging UI.
            worker.cancelled.connect(self._recalc_thread.quit)  # WHY: stop thread after cancel.
            worker.cancelled.connect(worker.deleteLater)  # WHY: clean up worker on cancel.
            worker.error.connect(self._on_recalc_error)  # WHY: surface errors without freezing UI.
            worker.error.connect(self._recalc_thread.quit)  # WHY: stop thread on error to avoid orphan threads.
            worker.error.connect(worker.deleteLater)  # WHY: free worker on error path.
            self._recalc_thread.finished.connect(self._on_recalc_thread_finished)  # WHY: clear references only after thread stops.
            self._recalc_thread.finished.connect(self._recalc_thread.deleteLater)  # WHY: free thread object after finish.
            self._recalc_thread.start()  # WHY: start background work now that signals are wired.
        except Exception as e:
            QMessageBox.warning(self, "Hata", f"Arka plan hesaplama başlatılamadı: {e}")

    def _on_recalc_progress(self, current, total):
        """Progress dialog güncelleme."""
        if not self._recalc_dialog:
            return  # WHY: dialog already cleaned up; ignore late signals.
        try:
            if total and self._recalc_dialog.maximum() != total:
                self._recalc_dialog.setMaximum(total)  # WHY: show actual progress once total is known.
            self._recalc_dialog.setValue(current)  # WHY: keep UI responsive with safe progress updates.
        except RuntimeError:
            self._recalc_dialog = None  # WHY: ignore updates after dialog is deleted to avoid crashes.

    def _on_recalc_finished(self, updated_count):
        """Recalc tamamlandı mesajı."""
        if self._recalc_dialog:
            try:
                self._recalc_dialog.close()  # WHY: close progress dialog on normal completion.
            except RuntimeError:
                pass  # WHY: dialog already deleted; ignore safely.
        self._recalc_dialog = None  # WHY: release UI reference after safe close.
        QMessageBox.information(self, "Başarılı", "İşlem Başarıyla Tamamlandı. Tüm kayıtlar yeniden hesaplandı ve ayarlar kaydedildi.")  # WHY: explicit success message per request.
        if self.signal_manager:
            self.signal_manager.data_updated.emit()

    def _on_recalc_error(self, msg):
        """Recalc hata mesajı."""
        if self._recalc_dialog:
            try:
                self._recalc_dialog.close()  # WHY: close progress dialog on error.
            except RuntimeError:
                pass  # WHY: dialog already deleted; ignore safely.
        self._recalc_dialog = None  # WHY: release UI reference after safe close.
        QMessageBox.critical(self, "Hata", f"Yeniden hesaplama sirasinda hata: {msg}")  # WHY: show error without crashing UI.

    def _on_recalc_dialog_canceled(self):
        """Progress dialog kapatildi/iptal edildi."""
        if self._recalc_worker:
            self._recalc_worker.request_stop()  # WHY: ask worker to stop safely instead of killing thread.
        if self._recalc_thread:
            self._recalc_thread.requestInterruption()  # WHY: set interruption flag for cooperative stop.
        if self._recalc_dialog:
            try:
                self._recalc_dialog.setLabelText("Iptal ediliyor...")  # WHY: give user immediate feedback on cancel.
                self._recalc_dialog.setCancelButtonText("")  # WHY: disable further cancel spam during shutdown.
            except RuntimeError:
                self._recalc_dialog = None  # WHY: dialog deleted; avoid accessing it.

    def _on_recalc_cancelled(self, updated_count):
        """Recalc iptal edildi mesajı."""
        if self._recalc_dialog:
            try:
                self._recalc_dialog.close()  # WHY: close dialog on cancel for clean UI.
            except RuntimeError:
                pass  # WHY: dialog already deleted; ignore safely.
        self._recalc_dialog = None  # WHY: release UI reference after cancel.
        QMessageBox.information(self, "Bilgi", "Islem iptal edildi.")  # WHY: inform user the cancel completed.

    def _on_recalc_thread_finished(self):
        """Thread kapaninca referanslari temizle."""
        self._recalc_thread = None  # WHY: clear thread ref after it has fully stopped.
        self._recalc_worker = None  # WHY: clear worker ref after thread completion.
    
    def _update_default_pwd_warning(self):
        try:
            stored_user = self.db.get_setting("admin_username", "admin")
            is_default = self.db.check_login(stored_user, "1234")
            if is_default:
                self.lbl_default_pwd_warn.setText("Uyari: Varsayilan sifre (1234) kullaniliyor.")
                self.lbl_default_pwd_warn.setVisible(True)
            else:
                self.lbl_default_pwd_warn.setVisible(False)
        except Exception:
            try:
                self.lbl_default_pwd_warn.setVisible(False)
            except Exception:
                pass

    def save_credentials(self):
        try:
            current_pwd = self.input_current_password.text().strip()
            if not current_pwd:
                QMessageBox.warning(self, "Hata", "Mevcut sifre bos olamaz.")
                return
            stored_user = self.db.get_setting("admin_username", "admin")
            if not self.db.check_login(stored_user, current_pwd):
                QMessageBox.warning(self, "Hata", "Mevcut sifre hatali.")
                return

            new_user = self.input_admin_user.text().strip()
            if not new_user:
                QMessageBox.warning(self, "Hata", "Kullanici adi bos olamaz.")
                return

            new_pass = self.input_new_password.text()
            new_pass2 = self.input_new_password2.text()

            if new_pass or new_pass2:
                if new_pass != new_pass2:
                    QMessageBox.warning(self, "Hata", "Yeni sifreler eslesmiyor.")
                    return
                if len(new_pass) < 4:
                    QMessageBox.warning(self, "Hata", "Yeni sifre en az 4 karakter olmali.")
                    return
                self.db.set_admin_credentials(username=new_user, password=new_pass)
            else:
                if new_user != stored_user:
                    self.db.set_admin_credentials(username=new_user, password=None)
                else:
                    QMessageBox.information(self, "Bilgi", "Degisiklik yok.")
                    return

            self.input_current_password.clear()
            self.input_new_password.clear()
            self.input_new_password2.clear()
            self._update_default_pwd_warning()
            QMessageBox.information(self, "Basarili", "Kullanici/sifre guncellendi.")
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Kullanici/sifre kaydedilirken hata: {e}")

    def _validate_time_format(self, time_str):
        """HH:MM formatını kontrol et"""
        ok, _ = ensure_hhmm_time(time_str, "Saat")
        return ok

    def browse_logo(self):
        cfg = load_config()
        last_dir = cfg.get("last_logo_dir", "")
        path, _ = QFileDialog.getOpenFileName(self, "Logo Sec", last_dir, "Image Files (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self.input_logo.setText(path)
            try:
                cfg["last_logo_dir"] = os.path.dirname(path)
                save_config(cfg)
            except Exception:
                pass

    def backup(self):
        cfg = load_config()
        last_dir = cfg.get("last_backup_dir", "")
        folder = QFileDialog.getExistingDirectory(self, "Yedek Klasoru Sec", last_dir)
        if folder:
            try:
                cfg["last_backup_dir"] = folder
                save_config(cfg)
            except Exception:
                pass
            success, msg = self.db.backup_db(folder)
            if success:
                QMessageBox.information(self, "Başarılı", f"Yedek alındı:\n{msg}")
            else:
                QMessageBox.critical(self, "Hata", msg)

    def open_db_folder(self):
        """Veritabanı klasörünü Windows Explorer'da aç"""
        import subprocess
        db_folder = os.path.dirname(self.db.db_file)
        try:
            subprocess.run(['explorer', db_folder])
        except Exception as e:
            QMessageBox.warning(self, "Hata", f"Klasör açılamadı: {e}")

    def reset_db(self):
        # Güvenlik sorusu 1
        reply = QMessageBox.question(self, "ÇOK ÖNEMLİ", 
                                   "TÜM personel, puantaj ve avans kayıtları SİLİNECEK.\nBu işlemin geri dönüşü yoktur!\n\nEmin misiniz?",
                                   QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            # Güvenlik sorusu 2 (Kazara basılmasın diye)
            confirm = QMessageBox.question(self, "Son Onay", 
                                         "Gerçekten her şeyi silip sıfırlamak istiyor musunuz?",
                                         QMessageBox.Yes | QMessageBox.No)
            if confirm == QMessageBox.Yes:
                with self.db.get_connection() as conn:
                    conn.execute("DELETE FROM gunluk_kayit")
                    conn.execute("DELETE FROM personel")
                    conn.execute("DELETE FROM avans_kesinti")
                
                self.signal_manager.data_updated.emit() # Her yeri yenile
                QMessageBox.information(self, "Sıfırlandı", "Veritabanı tertemiz oldu.")
    
    def open_mesai_katsayilari(self):
        """Mesai katsayıları yönetim ekranını aç"""
        dialog = MesaiKatsayilariDialog(self.db, self.signal_manager, self.tersane_id, self)  # NEW: pass active tersane_id.
        dialog.exec()
    
    def open_yevmiye_katsayilari(self):
        """Yevmiye katsayıları yönetim ekranını aç"""
        dialog = YevmiyeKatsayilariDialog(self.db, self.tersane_id, self)  # NEW: pass active tersane_id.
        dialog.exec()

    def open_tersane_yonetimi(self):
        """Tersane yönetim ekranını aç"""
        dialog = TersaneYonetimiDialog(self.db, self)
        dialog.exec()


class TersaneYonetimiDialog(QDialog):
    """Tersane ekleme, düzenleme ve saat ayarları dialog'u"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Tersane Yönetimi")
        self.setMinimumWidth(850)
        self.setMinimumHeight(500)
        self.editing_id = None
        self.setup_ui()
        self.load_data()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel("Her tersane için en geç giriş, en erken çıkış, erken çıkış limiti, mesai başlangıç ve vardiya limiti saatlerini tanımlayın.\n"
                       "Bu saatler, veri yükleme sırasında seçilen tersaneye göre hesaplama motoruna parametre olarak gönderilir.")
        info.setStyleSheet("color: #90CAF9; padding: 10px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Form
        form_frame = QFrame()
        form_frame.setStyleSheet("background-color: #333; border-radius: 8px; padding: 15px;")
        form_layout = QVBoxLayout(form_frame)

        # Satır 1: Tersane adı
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Tersane Adı:"))
        self.input_ad = QLineEdit()
        self.input_ad.setPlaceholderText("Örn: Tuzla Tersanesi")
        row1.addWidget(self.input_ad)
        form_layout.addLayout(row1)

        # Satır 2: Saatler
        row2 = QHBoxLayout()

        row2.addWidget(QLabel("En Geç Giriş:"))
        self.input_giris = QLineEdit("08:20")
        self.input_giris.setFixedWidth(70)
        self.input_giris.setToolTip("Bu saatten sonra gelenler geç sayılır (Örn: 08:20)")
        row2.addWidget(self.input_giris)

        row2.addWidget(QLabel("En Erken Çıkış:"))
        self.input_cikis = QLineEdit("17:00")
        self.input_cikis.setFixedWidth(70)
        self.input_cikis.setToolTip("Normal mesai bitiş saati (Örn: 17:00)")
        row2.addWidget(self.input_cikis)

        row2.addWidget(QLabel("Erken Çıkış Limiti:"))
        self.input_erken = QLineEdit("16:30")
        self.input_erken.setFixedWidth(70)
        self.input_erken.setToolTip("Bu saatten önce çıkış yapanlara tam ceza uygulanır (Örn: 16:30)")
        row2.addWidget(self.input_erken)

        row2.addWidget(QLabel("Mesai Başlangıç:"))
        self.input_mesai = QLineEdit("17:30")
        self.input_mesai.setFixedWidth(70)
        self.input_mesai.setToolTip("Bu saatten sonra mesai başlar (Örn: 17:30)")
        row2.addWidget(self.input_mesai)

        row2.addWidget(QLabel("Vardiya Limiti:"))
        self.input_vardiya = QLineEdit("19:30")
        self.input_vardiya.setFixedWidth(70)
        self.input_vardiya.setToolTip("Bu saatten sonra çıkışlara sabit mesai uygulanır (Örn: 19:30)")
        row2.addWidget(self.input_vardiya)

        form_layout.addLayout(row2)

        # Butonlar
        row3 = QHBoxLayout()
        self.btn_add = QPushButton("Ekle")
        self.btn_add.setStyleSheet("background-color: #4CAF50; color: white; padding: 8px 16px;")
        self.btn_add.clicked.connect(self.add_tersane)
        row3.addWidget(self.btn_add)

        self.btn_update = QPushButton("Güncelle")
        self.btn_update.setStyleSheet("background-color: #FF9800; color: white; padding: 8px 16px;")
        self.btn_update.clicked.connect(self.update_tersane)
        self.btn_update.setVisible(False)
        row3.addWidget(self.btn_update)

        self.btn_cancel = QPushButton("İptal")
        self.btn_cancel.setStyleSheet("background-color: #757575; color: white; padding: 8px 16px;")
        self.btn_cancel.clicked.connect(self.cancel_edit)
        self.btn_cancel.setVisible(False)
        row3.addWidget(self.btn_cancel)

        row3.addStretch()
        form_layout.addLayout(row3)
        layout.addWidget(form_frame)

        # Tablo
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["ID", "Tersane Adı", "En Geç Giriş", "En Erken Çıkış", "Erken Çıkış Limiti", "Mesai Başlangıç", "Vardiya Limiti"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        # Alt butonlar
        btn_row = QHBoxLayout()
        btn_edit = QPushButton("Düzenle")
        btn_edit.setStyleSheet("background-color: #FF9800; color: white; padding: 8px;")
        btn_edit.clicked.connect(self.load_selected_to_form)
        btn_row.addWidget(btn_edit)

        btn_delete = QPushButton("Sil")
        btn_delete.setStyleSheet("background-color: #f44336; color: white; padding: 8px;")
        btn_delete.clicked.connect(self.delete_selected)
        btn_row.addWidget(btn_delete)

        btn_close = QPushButton("Kapat")
        btn_close.setStyleSheet("background-color: #2196F3; color: white; padding: 8px;")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def _validate_time(self, time_str, field_name):
        ok, result = ensure_hhmm_time(time_str, field_name)
        if not ok:
            QMessageBox.warning(self, "Hata", result)
            return False
        return True

    def load_data(self):
        rows = self.db.get_tersaneler()
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j in range(7):
                self.table.setItem(i, j, QTableWidgetItem(str(row[j])))

    def add_tersane(self):
        ok, ad = ensure_non_empty(self.input_ad.text(), "Tersane adı")
        if not ok:
            QMessageBox.warning(self, "Hata", ad)
            return
        giris = self.input_giris.text().strip()
        cikis = self.input_cikis.text().strip()
        erken = self.input_erken.text().strip()
        mesai = self.input_mesai.text().strip()
        vardiya = self.input_vardiya.text().strip()

        for val, name in [(giris, "En Geç Giriş"), (cikis, "En Erken Çıkış"), (erken, "Erken Çıkış Limiti"), (mesai, "Mesai Başlangıç"), (vardiya, "Vardiya Limiti")]:
            if not self._validate_time(val, name):
                return

        result = self.db.add_tersane(ad, giris, cikis, erken, mesai, vardiya)
        if result:
            self.load_data()
            self.input_ad.clear()
            QMessageBox.information(self, "Başarılı", f"'{ad}' tersanesi eklendi.")
        else:
            QMessageBox.warning(self, "Hata", "Tersane eklenemedi (ad zaten mevcut olabilir).")

    def load_selected_to_form(self):
        current_row = self.table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Hata", "Lütfen düzenlemek için bir satır seçin.")
            return
        self.editing_id = int(self.table.item(current_row, 0).text())
        self.input_ad.setText(self.table.item(current_row, 1).text())
        self.input_giris.setText(self.table.item(current_row, 2).text())
        self.input_cikis.setText(self.table.item(current_row, 3).text())
        self.input_erken.setText(self.table.item(current_row, 4).text())
        self.input_mesai.setText(self.table.item(current_row, 5).text())
        self.input_vardiya.setText(self.table.item(current_row, 6).text())
        self.btn_add.setVisible(False)
        self.btn_update.setVisible(True)
        self.btn_cancel.setVisible(True)

    def update_tersane(self):
        if self.editing_id is None:
            return
        ok, ad = ensure_non_empty(self.input_ad.text(), "Tersane adı")
        if not ok:
            QMessageBox.warning(self, "Hata", ad)
            return
        giris = self.input_giris.text().strip()
        cikis = self.input_cikis.text().strip()
        erken = self.input_erken.text().strip()
        mesai = self.input_mesai.text().strip()
        vardiya = self.input_vardiya.text().strip()

        for val, name in [(giris, "En Geç Giriş"), (cikis, "En Erken Çıkış"), (erken, "Erken Çıkış Limiti"), (mesai, "Mesai Başlangıç"), (vardiya, "Vardiya Limiti")]:
            if not self._validate_time(val, name):
                return

        self.db.update_tersane(self.editing_id, ad, giris, cikis, erken, mesai, vardiya)
        self.load_data()
        self.cancel_edit()
        QMessageBox.information(self, "Başarılı", f"'{ad}' tersanesi güncellendi.")

    def cancel_edit(self):
        self.editing_id = None
        self.input_ad.clear()
        self.input_giris.setText("08:20")
        self.input_cikis.setText("17:00")
        self.input_erken.setText("16:30")
        self.input_mesai.setText("17:30")
        self.input_vardiya.setText("19:30")
        self.btn_add.setVisible(True)
        self.btn_update.setVisible(False)
        self.btn_cancel.setVisible(False)
        self.table.clearSelection()

    def delete_selected(self):
        current_row = self.table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Hata", "Lütfen silmek için bir satır seçin.")
            return
        tersane_id = int(self.table.item(current_row, 0).text())
        ad = self.table.item(current_row, 1).text()
        reply = QMessageBox.question(self, "Onay", f"'{ad}' tersanesini silmek (pasif yapmak) istediğinize emin misiniz?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.db.delete_tersane(tersane_id)
            self.load_data()
            QMessageBox.information(self, "Başarılı", f"'{ad}' tersanesi pasif yapıldı.")


class MesaiKatsayilariDialog(QDialog):
    """Mesai katsayılarını yönetme dialog'u"""
    
    def __init__(self, db, signal_manager, tersane_id=0, parent=None):
        super().__init__(parent)
        self.db = db
        self.signal_manager = signal_manager
        self.tersane_id = tersane_id or 0  # NEW: scope katsayı operations to active tersane.
        self._recalc_thread = None  # NEW: keep thread reference to avoid premature GC.
        self._recalc_worker = None  # NEW: keep worker reference to avoid GC while thread runs.
        self._recalc_dialog = None  # NEW: progress dialog reference for background recalc.
        self._recalc_done_message = None  # NEW: show correct success message after background recalc.
        self.setWindowTitle("Mesai Yevmiyesi Kuralları")
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)
        self.setup_ui()
        self.load_data()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Açıklama
        info = QLabel("Çıkış saatine göre 'Mesai (Saat)' değerini tanımlayın.\n"
                     "Örnek: 18:30-19:30 => 3.0, 19:30-20:30 => 4.5\n"
                     "(Aralıklar çıkış saati içindir; mesai çarpanı uygulanmaz. Aralık dışı 0'dır.)")
        info.setStyleSheet("color: #90CAF9; padding: 10px;")
        layout.addWidget(info)
        # NEW: show active tersane scope to prevent cross-shipyard confusion.
        tersane_name = None
        if self.tersane_id and self.tersane_id > 0:
            try:
                t = self.db.get_tersane(self.tersane_id)
                tersane_name = t['ad'] if t else f"ID {self.tersane_id}"
            except Exception:
                tersane_name = f"ID {self.tersane_id}"
        scope_text = f"Aktif Tersane: {tersane_name}" if tersane_name else "Aktif Tersane: Tüm Tersaneler (global)"
        scope_lbl = QLabel(scope_text)
        scope_lbl.setStyleSheet("color: #FFD600; font-size: 11px; padding-left: 10px;")
        layout.addWidget(scope_lbl)
        
        # Ekleme formu
        form_frame = QFrame()
        form_frame.setStyleSheet("background-color: #333; border-radius: 8px; padding: 15px;")
        form_layout = QHBoxLayout(form_frame)
        
        form_layout.addWidget(QLabel("Çıkış Başlangıç:"))
        self.input_baslangic = QDoubleSpinBox()
        self.input_baslangic.setRange(0, 24)
        self.input_baslangic.setSingleStep(0.5)  # 30 dakika adımlar
        self.input_baslangic.setDecimals(2)
        self.input_baslangic.setSuffix(" saat")
        self.input_baslangic.setValue(18.5)
        self.input_baslangic.setToolTip("Örn: 18.5 = 18:30, 19.0 = 19:00, 19.5 = 19:30")
        form_layout.addWidget(self.input_baslangic)
        
        form_layout.addWidget(QLabel("Çıkış Bitiş:"))
        self.input_bitis = QDoubleSpinBox()
        self.input_bitis.setRange(0, 24)
        self.input_bitis.setSingleStep(0.5)  # 30 dakika adımlar
        self.input_bitis.setDecimals(2)
        self.input_bitis.setSuffix(" saat")
        self.input_bitis.setValue(19.5)
        self.input_bitis.setToolTip("Örn: 18.5 = 18:30, 19.0 = 19:00, 19.5 = 19:30")
        form_layout.addWidget(self.input_bitis)
        
        form_layout.addWidget(QLabel("Mesai (Saat):"))
        self.input_katsayi = QDoubleSpinBox()
        self.input_katsayi.setRange(0.0, 24.0)
        self.input_katsayi.setSingleStep(0.25)
        self.input_katsayi.setDecimals(2)
        self.input_katsayi.setSuffix(" saat")
        self.input_katsayi.setValue(0.0)
        form_layout.addWidget(self.input_katsayi)
        
        form_layout.addWidget(QLabel("Açıklama:"))
        self.input_aciklama = QLineEdit()
        self.input_aciklama.setPlaceholderText("Örn: Normal mesai")
        form_layout.addWidget(self.input_aciklama)
        
        self.btn_add = QPushButton("➕ Ekle")
        self.btn_add.setStyleSheet("background-color: #4CAF50; color: white; padding: 8px;")
        self.btn_add.clicked.connect(self.add_katsayi)
        form_layout.addWidget(self.btn_add)
        
        self.btn_update = QPushButton("✏️ Güncelle")
        self.btn_update.setStyleSheet("background-color: #FF9800; color: white; padding: 8px;")
        self.btn_update.clicked.connect(self.update_katsayi)
        self.btn_update.setVisible(False)  # Başlangıçta gizli
        form_layout.addWidget(self.btn_update)
        
        self.btn_cancel = QPushButton("❌ İptal")
        self.btn_cancel.setStyleSheet("background-color: #757575; color: white; padding: 8px;")
        self.btn_cancel.clicked.connect(self.cancel_edit)
        self.btn_cancel.setVisible(False)  # Başlangıçta gizli
        form_layout.addWidget(self.btn_cancel)
        
        layout.addWidget(form_frame)
        
        # Tablo
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["ID", "Çıkış Başlangıç", "Çıkış Bitiş", "Mesai (Saat)", "Açıklama"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self.on_table_selection_changed)
        layout.addWidget(self.table)
        
        # Butonlar
        btn_row = QHBoxLayout()
        btn_edit = QPushButton("✏️ Düzenle")
        btn_edit.setStyleSheet("background-color: #FF9800; color: white; padding: 8px;")
        btn_edit.clicked.connect(self.load_selected_to_form)
        btn_row.addWidget(btn_edit)
        
        btn_delete = QPushButton("🗑️ Seçili Satırı Sil")
        btn_delete.setStyleSheet("background-color: #f44336; color: white; padding: 8px;")
        btn_delete.clicked.connect(self.delete_selected)
        btn_row.addWidget(btn_delete)
        
        btn_close = QPushButton("✅ Kapat")
        btn_close.setStyleSheet("background-color: #2196F3; color: white; padding: 8px;")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        
        layout.addLayout(btn_row)
        
        # Düzenleme modu için durum
        self.editing_id = None
    
    def load_data(self):
        """Katsayıları tabloya yükle"""
        # NEW: load only active tersane's katsayıları; global for "Tüm Tersaneler".
        if self.tersane_id and self.tersane_id > 0:
            katsayilar = self.db.get_mesai_katsayilari(tersane_id=self.tersane_id, fallback_global=False)
        else:
            katsayilar = self.db.get_mesai_katsayilari()
        self.table.setRowCount(len(katsayilar))
        
        for i, (id, baslangic, bitis, katsayi, aciklama) in enumerate(katsayilar):
            # Ondalık saati saat:dakika formatına çevir
            baslangic_str = self.format_saat(baslangic)
            bitis_str = self.format_saat(bitis)
            
            self.table.setItem(i, 0, QTableWidgetItem(str(id)))
            self.table.setItem(i, 1, QTableWidgetItem(baslangic_str))
            self.table.setItem(i, 2, QTableWidgetItem(bitis_str))
            self.table.setItem(i, 3, QTableWidgetItem(f"{katsayi:.2f}"))
            self.table.setItem(i, 4, QTableWidgetItem(aciklama or ""))
    
    def format_saat(self, ondalik_saat):
        """Ondalık saati saat:dakika formatına çevir (17.5 -> 17:30)"""
        saat = int(ondalik_saat)
        dakika = int((ondalik_saat - saat) * 60)
        return f"{saat:02d}:{dakika:02d}"
    
    def recalculate_records(self):
        """Var olan tum kayitlari yeniden hesapla"""
        # NEW: agir is yukunu UI disinda calistir.
        self._start_recalc_worker()

    def _start_recalc_worker(self):
        """Arka planda yeniden hesaplama baslatir (UI donmasini engeller)."""
        if self._recalc_thread and self._recalc_thread.isRunning():
            return  # WHY: do not start a second recalc while one is running.
        # Progress dialog (spinner + progress)
        self._recalc_dialog = QProgressDialog("Yeniden hesaplaniyor...", None, 0, 0, self)  # WHY: keep same UI text but make it cancel-safe.
        self._recalc_dialog.setWindowModality(Qt.WindowModal)  # WHY: preserve modal behavior.
        self._recalc_dialog.setAutoClose(False)  # WHY: we close explicitly on signals to avoid stuck dialogs.
        self._recalc_dialog.setAutoReset(False)  # WHY: keep progress state until we close safely.
        self._recalc_dialog.setMinimumDuration(0)  # WHY: show immediately to prevent perceived freeze.
        self._recalc_dialog.setAttribute(Qt.WA_DeleteOnClose, False)  # WHY: prevent C++ object deletion before signals stop.
        self._recalc_dialog.canceled.connect(self._on_recalc_dialog_canceled)  # WHY: allow safe user cancel without crashing.
        self._recalc_dialog.rejected.connect(self._on_recalc_dialog_canceled)  # WHY: handle window close (X) safely.
        self._recalc_dialog.show()  # WHY: keep user feedback during background work.

        self._recalc_thread = QThread()  # WHY: run heavy work off the UI thread.
        worker = RecalcWorker(self.db, self.tersane_id)  # WHY: keep existing worker logic, just manage lifecycle safely.
        self._recalc_worker = worker  # WHY: keep a strong reference to avoid GC while running.
        worker.moveToThread(self._recalc_thread)  # WHY: execute worker in background thread.
        self._recalc_thread.started.connect(worker.run)  # WHY: start work when thread starts.
        worker.progress.connect(self._on_recalc_progress)  # WHY: update UI safely from worker signals.
        worker.finished.connect(self._on_recalc_finished)  # WHY: close dialog and notify success on completion.
        worker.finished.connect(self._recalc_dialog.accept)  # WHY: ensure dialog closes even if handler fails.
        worker.finished.connect(self._recalc_thread.quit)  # WHY: stop thread event loop after completion.
        worker.finished.connect(worker.deleteLater)  # WHY: free worker object safely in Qt.
        worker.cancelled.connect(self._on_recalc_cancelled)  # WHY: handle user cancel without crashing.
        worker.cancelled.connect(self._recalc_dialog.accept)  # WHY: close dialog on cancel to avoid hanging UI.
        worker.cancelled.connect(self._recalc_thread.quit)  # WHY: stop thread after cancel.
        worker.cancelled.connect(worker.deleteLater)  # WHY: clean up worker on cancel.
        worker.error.connect(self._on_recalc_error)  # WHY: surface errors without freezing UI.
        worker.error.connect(self._recalc_thread.quit)  # WHY: stop thread on error to avoid orphan threads.
        worker.error.connect(worker.deleteLater)  # WHY: free worker on error path.
        self._recalc_thread.finished.connect(self._on_recalc_thread_finished)  # WHY: clear references only after thread stops.
        self._recalc_thread.finished.connect(self._recalc_thread.deleteLater)  # WHY: free thread object after finish.
        self._recalc_thread.start()  # WHY: start background work now that signals are wired.

    def _on_recalc_progress(self, current, total):
        """Progress dialog guncelleme."""
        if not self._recalc_dialog:
            return  # WHY: dialog already cleaned up; ignore late signals.
        try:
            if total and self._recalc_dialog.maximum() != total:
                self._recalc_dialog.setMaximum(total)  # WHY: show actual progress once total is known.
            self._recalc_dialog.setValue(current)  # WHY: keep UI responsive with safe progress updates.
        except RuntimeError:
            self._recalc_dialog = None  # WHY: ignore updates after dialog is deleted to avoid crashes.

    def _on_recalc_finished(self, updated_count):
        """Recalc tamamlandi mesaji."""
        if self._recalc_dialog:
            try:
                self._recalc_dialog.close()  # WHY: close progress dialog on normal completion.
            except RuntimeError:
                pass  # WHY: dialog already deleted; ignore safely.
        self._recalc_dialog = None  # WHY: release UI reference after safe close.
        # Log
        try:
            from core.app_logger import log_info
            log_info(f"Mesai katsayilari degisimi: {updated_count} kayit yeniden hesaplandi.")
        except Exception:
            pass
        # Success message (context-specific if provided).
        if self._recalc_done_message:
            QMessageBox.information(self, "Başarılı", self._recalc_done_message)  # WHY: preserve existing contextual success message.
            self._recalc_done_message = None  # WHY: clear message after showing once.
        else:
            QMessageBox.information(self, "Başarılı", "İşlem Başarıyla Tamamlandı.")  # WHY: explicit success message per request.
        # Dashboard'u yenile
        if self.signal_manager:
            self.signal_manager.data_updated.emit()

    def _on_recalc_error(self, msg):
        """Recalc hata mesaji."""
        if self._recalc_dialog:
            try:
                self._recalc_dialog.close()  # WHY: close progress dialog on error.
            except RuntimeError:
                pass  # WHY: dialog already deleted; ignore safely.
        self._recalc_dialog = None  # WHY: release UI reference after safe close.
        QMessageBox.critical(self, "Hata", f"Yeniden hesaplama sirasinda hata: {msg}")  # WHY: show error without crashing UI.

    def _on_recalc_dialog_canceled(self):
        """Progress dialog kapatildi/iptal edildi."""
        if self._recalc_worker:
            self._recalc_worker.request_stop()  # WHY: ask worker to stop safely instead of killing thread.
        if self._recalc_thread:
            self._recalc_thread.requestInterruption()  # WHY: set interruption flag for cooperative stop.
        if self._recalc_dialog:
            try:
                self._recalc_dialog.setLabelText("Iptal ediliyor...")  # WHY: give user immediate feedback on cancel.
                self._recalc_dialog.setCancelButtonText("")  # WHY: disable further cancel spam during shutdown.
            except RuntimeError:
                self._recalc_dialog = None  # WHY: dialog deleted; avoid accessing it.

    def _on_recalc_cancelled(self, updated_count):
        """Recalc iptal edildi mesaji."""
        if self._recalc_dialog:
            try:
                self._recalc_dialog.close()  # WHY: close dialog on cancel for clean UI.
            except RuntimeError:
                pass  # WHY: dialog already deleted; ignore safely.
        self._recalc_dialog = None  # WHY: release UI reference after cancel.
        QMessageBox.information(self, "Bilgi", "Islem iptal edildi.")  # WHY: inform user the cancel completed.

    def _on_recalc_thread_finished(self):
        """Thread kapaninca referanslari temizle."""
        self._recalc_thread = None  # WHY: clear thread ref after it has fully stopped.
        self._recalc_worker = None  # WHY: clear worker ref after thread completion.

    def add_katsayi(self):
        """Yeni katsayı ekle"""
        baslangic = self.input_baslangic.value()
        bitis = self.input_bitis.value()
        katsayi = self.input_katsayi.value()
        aciklama = self.input_aciklama.text()
        
        if baslangic >= bitis:
            QMessageBox.warning(self, "Hata", "Başlangıç saati bitiş saatinden küçük olmalı!")
            return
        
        # NEW: save katsayı under active tersane_id (None keeps global behavior).
        self.db.add_mesai_katsayisi(baslangic, bitis, katsayi, aciklama, tersane_id=self.tersane_id if self.tersane_id > 0 else None)
        self.load_data()
        
        # Formu temizle - Başlangıç = bitiş, Bitiş = bitiş + 0.5 (yarım saat sonrası)
        self.input_baslangic.setValue(bitis)
        self.input_bitis.setValue(bitis + 0.5)
        self.input_aciklama.clear()
        
        # Var olan kayıtları yeniden hesaplamak ister misin?
        reply = QMessageBox.question(
            self, 
            "Kayıtları Yeniden Hesapla", 
            "Kural değiştirildi.\n\nVar olan tüm kayıtları YENİ kuralla yeniden hesaplamak ister misiniz?\n(Sadece çıkış saati bu aralıkta olan kayıtlar etkilenecektir)",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self._recalc_done_message = "Katsayi eklendi ve kayitlar yeniden hesaplandi!"
            self.recalculate_records()  # NEW: run in background; message will be shown on finish.
        else:
            QMessageBox.information(self, "Başarılı", "Katsayı eklendi. (Eski kayıtlar değişmedi)")
    
    def load_selected_to_form(self):
        """Tablodaki seçili satırı forma yükle - düzenleme modu"""
        current_row = self.table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Hata", "Lütfen düzenlemek için bir satır seçin!")
            return
        
        id_item = self.table.item(current_row, 0)
        if not id_item:
            return
        
        self.editing_id = int(id_item.text())
        
        # Formdan değerleri oku
        baslangic_str = self.table.item(current_row, 1).text()
        bitis_str = self.table.item(current_row, 2).text()
        katsayi_str = self.table.item(current_row, 3).text()
        aciklama_str = self.table.item(current_row, 4).text()
        
        # Saat:dakika formatını ondalık saate çevir
        def time_to_decimal(time_str):
            parts = time_str.split(':')
            if len(parts) == 2:
                saat = int(parts[0])
                dakika = int(parts[1])
                return saat + dakika / 60.0
            return 0.0
        
        self.input_baslangic.setValue(time_to_decimal(baslangic_str))
        self.input_bitis.setValue(time_to_decimal(bitis_str))
        self.input_katsayi.setValue(float(katsayi_str))
        self.input_aciklama.setText(aciklama_str)
        
        # UI modunu değiştir
        self.btn_add.setVisible(False)
        self.btn_update.setVisible(True)
        self.btn_cancel.setVisible(True)
        
        QMessageBox.information(self, "Düzenleme Modu", f"ID {self.editing_id} düzenleniyor.\nDeğerler forma yüklendi.\nDeğişiklikten sonra 'Güncelle' butonuna tıklayın.")
    
    def update_katsayi(self):
        """Seçili katsayıyı güncelle"""
        if self.editing_id is None:
            QMessageBox.warning(self, "Hata", "Düzenleme modu aktif değil!")
            return
        
        baslangic = self.input_baslangic.value()
        bitis = self.input_bitis.value()
        katsayi = self.input_katsayi.value()
        aciklama = self.input_aciklama.text()
        
        if baslangic >= bitis:
            QMessageBox.warning(self, "Hata", "Başlangıç saati bitiş saatinden küçük olmalı!")
            return
        
        # Veritabanında güncelle
        # NEW: keep update scoped to active tersane (does not break global behavior).
        self.db.update_mesai_katsayisi(self.editing_id, baslangic, bitis, katsayi, aciklama, tersane_id=self.tersane_id if self.tersane_id > 0 else None)
        self.load_data()
        self.cancel_edit()
        
        # Var olan kayıtları yeniden hesaplamak ister misin?
        reply = QMessageBox.question(
            self, 
            "Kayıtları Yeniden Hesapla", 
            "Kural güncellendi.\n\nVar olan tüm kayıtları YENİ kuralla yeniden hesaplamak ister misiniz?\n(Sadece çıkış saati bu aralıkta olan kayıtlar etkilenecektir)",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self._recalc_done_message = "Katsayi guncellendi ve kayitlar yeniden hesaplandi!"
            self.recalculate_records()  # NEW: run in background; message will be shown on finish.
        else:
            QMessageBox.information(self, "Başarılı", "Katsayı güncellendi. (Eski kayıtlar değişmedi)")
    
    def cancel_edit(self):
        """Düzenleme modunu iptal et"""
        self.editing_id = None
        self.input_baslangic.setValue(18.5)
        self.input_bitis.setValue(19.5)
        self.input_katsayi.setValue(3.0)
        self.input_aciklama.clear()
        
        self.btn_add.setVisible(True)
        self.btn_update.setVisible(False)
        self.btn_cancel.setVisible(False)
        
        self.table.clearSelection()
    
    def on_table_selection_changed(self):
        """Tablo seçimi değiştiğinde (seçim temizlenmişse düzenleme modunu kapat)"""
        if self.table.currentRow() < 0 and self.editing_id is not None:
            self.cancel_edit()
    
    def delete_selected(self):
        """Seçili satırı sil"""
        current_row = self.table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Hata", "Lütfen silmek için bir satır seçin!")
            return
        
        id_item = self.table.item(current_row, 0)
        if not id_item:
            return
        
        id = int(id_item.text())
        
        reply = QMessageBox.question(self, "Onay", "Bu katsayıyı silmek istediğinize emin misiniz?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.db.delete_mesai_katsayisi(id)
            self.load_data()
            
            # Var olan kayıtları yeniden hesaplamak ister misin?
            reply2 = QMessageBox.question(
                self, 
                "Kayıtları Yeniden Hesapla", 
                "Katsayı silindi.\n\nVar olan tüm kayıtları yeniden hesaplamak ister misiniz?\n(Etkilenen kayıtlar yeni katsayılar ile hesaplanacak)",
                QMessageBox.Yes | QMessageBox.No
            )
            
            if reply2 == QMessageBox.Yes:
                self._recalc_done_message = "Katsayi silindi ve kayitlar yeniden hesaplandi!"
                self.recalculate_records()  # NEW: run in background; message will be shown on finish.
            else:
                QMessageBox.information(self, "Başarılı", "Katsayı silindi. (Eski kayıtlar değişmedi)")


class YevmiyeKatsayilariDialog(QDialog):
    """Yevmiye katsayılarını yönetme dialog'u (Yevmiyeciler için)"""
    
    def __init__(self, db, tersane_id=0, parent=None):
        super().__init__(parent)
        self.db = db
        self.tersane_id = tersane_id or 0  # NEW: scope yevmiye katsayıları to active tersane.
        self.setWindowTitle("Yevmiye Kuralları (Yevmiyeciler)")
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)
        self.editing_id = None
        self.setup_ui()
        self.load_data()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Açıklama
        info = QLabel("Yevmiyeciler için çıkış saatine göre eklenecek yevmiyeyi tanımlayın.\nÖrnek: 19:30-20:30 => 0.5 yevmiye\n(Aralık dışı 0'dır.)")
        info.setStyleSheet("color: #90CAF9; padding: 10px;")
        layout.addWidget(info)
        
        # Ekleme formu
        form_frame = QFrame()
        form_frame.setStyleSheet("background-color: #333; border-radius: 8px; padding: 15px;")
        form_layout = QHBoxLayout(form_frame)
        
        form_layout.addWidget(QLabel("Çıkış Başlangıç:"))
        self.input_baslangic = QDoubleSpinBox()
        self.input_baslangic.setRange(0, 24)
        self.input_baslangic.setSingleStep(0.5)  # 30 dakika adımlar
        self.input_baslangic.setDecimals(2)
        self.input_baslangic.setSuffix(" saat")
        self.input_baslangic.setValue(19.5)
        self.input_baslangic.setToolTip("Örn: 19.5 = 19:30, 20.0 = 20:00, 20.5 = 20:30")
        form_layout.addWidget(self.input_baslangic)
        
        form_layout.addWidget(QLabel("Çıkış Bitiş:"))
        self.input_bitis = QDoubleSpinBox()
        self.input_bitis.setRange(0, 24)
        self.input_bitis.setSingleStep(0.5)  # 30 dakika adımlar
        self.input_bitis.setDecimals(2)
        self.input_bitis.setSuffix(" saat")
        self.input_bitis.setValue(20.5)
        self.input_bitis.setToolTip("Örn: 19.5 = 19:30, 20.0 = 20:00, 20.5 = 20:30")
        form_layout.addWidget(self.input_bitis)
        
        form_layout.addWidget(QLabel("Yevmiye:"))
        self.input_yevmiye = QDoubleSpinBox()
        self.input_yevmiye.setRange(0.0, 5.0)
        self.input_yevmiye.setSingleStep(0.05)
        self.input_yevmiye.setDecimals(2)
        self.input_yevmiye.setSuffix(" yevmiye")
        self.input_yevmiye.setValue(0.2)
        self.input_yevmiye.setToolTip("Bu çıkış aralığında eklenecek yevmiye miktarı")
        form_layout.addWidget(self.input_yevmiye)
        
        form_layout.addWidget(QLabel("Açıklama:"))
        self.input_aciklama = QLineEdit()
        self.input_aciklama.setPlaceholderText("Örn: Normal mesai")
        form_layout.addWidget(self.input_aciklama)
        
        self.btn_add = QPushButton("➕ Ekle")
        self.btn_add.setStyleSheet("background-color: #4CAF50; color: white; padding: 8px;")
        self.btn_add.clicked.connect(self.add_katsayi)
        form_layout.addWidget(self.btn_add)
        
        self.btn_update = QPushButton("✏️ Güncelle")
        self.btn_update.setStyleSheet("background-color: #FF9800; color: white; padding: 8px;")
        self.btn_update.clicked.connect(self.update_katsayi)
        self.btn_update.setVisible(False)  # Başlangıçta gizli
        form_layout.addWidget(self.btn_update)
        
        self.btn_cancel = QPushButton("❌ İptal")
        self.btn_cancel.setStyleSheet("background-color: #757575; color: white; padding: 8px;")
        self.btn_cancel.clicked.connect(self.cancel_edit)
        self.btn_cancel.setVisible(False)  # Başlangıçta gizli
        form_layout.addWidget(self.btn_cancel)
        
        layout.addWidget(form_frame)
        
        # Tablo
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["ID", "Çıkış Başlangıç", "Çıkış Bitiş", "Ek Yevmiye", "Açıklama"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self.on_table_selection_changed)
        layout.addWidget(self.table)
        
        # Butonlar
        btn_row = QHBoxLayout()
        btn_edit = QPushButton("✏️ Düzenle")
        btn_edit.setStyleSheet("background-color: #FF9800; color: white; padding: 8px;")
        btn_edit.clicked.connect(self.load_selected_to_form)
        btn_row.addWidget(btn_edit)
        
        btn_delete = QPushButton("🗑️ Seçili Satırı Sil")
        btn_delete.setStyleSheet("background-color: #f44336; color: white; padding: 8px;")
        btn_delete.clicked.connect(self.delete_selected)
        btn_row.addWidget(btn_delete)
        
        btn_close = QPushButton("✅ Kapat")
        btn_close.setStyleSheet("background-color: #2196F3; color: white; padding: 8px;")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        
        layout.addLayout(btn_row)
    
    def load_data(self):
        """Katsayıları tabloya yükle"""
        # NEW: load only active tersane's katsayıları; global for "Tüm Tersaneler".
        if self.tersane_id and self.tersane_id > 0:
            katsayilar = self.db.get_yevmiye_katsayilari(tersane_id=self.tersane_id, fallback_global=False)
        else:
            katsayilar = self.db.get_yevmiye_katsayilari()
        self.table.setRowCount(len(katsayilar))
        
        for i, (id, baslangic, bitis, yevmiye_katsayi, aciklama) in enumerate(katsayilar):
            # Ondalık saati saat:dakika formatına çevir
            baslangic_str = self.format_saat(baslangic)
            bitis_str = self.format_saat(bitis)
            
            self.table.setItem(i, 0, QTableWidgetItem(str(id)))
            self.table.setItem(i, 1, QTableWidgetItem(baslangic_str))
            self.table.setItem(i, 2, QTableWidgetItem(bitis_str))
            self.table.setItem(i, 3, QTableWidgetItem(f"{yevmiye_katsayi:.2f}"))
            self.table.setItem(i, 4, QTableWidgetItem(aciklama or ""))
    
    def format_saat(self, ondalik_saat):
        """Ondalık saati saat:dakika formatına çevir (2.5 -> 2:30)"""
        saat = int(ondalik_saat)
        dakika = int((ondalik_saat - saat) * 60)
        return f"{saat:02d}:{dakika:02d}"
    
    def load_selected_to_form(self):
        """Tablodaki seçili satırı forma yükle - düzenleme modu"""
        current_row = self.table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Hata", "Lütfen düzenlemek için bir satır seçin!")
            return
        
        id_item = self.table.item(current_row, 0)
        if not id_item:
            return
        
        self.editing_id = int(id_item.text())
        
        # Formdan değerleri oku
        baslangic_str = self.table.item(current_row, 1).text()
        bitis_str = self.table.item(current_row, 2).text()
        yevmiye_str = self.table.item(current_row, 3).text()
        aciklama_str = self.table.item(current_row, 4).text()
        
        # Saat:dakika formatını ondalık saate çevir
        def time_to_decimal(time_str):
            parts = time_str.split(':')
            if len(parts) == 2:
                saat = int(parts[0])
                dakika = int(parts[1])
                return saat + dakika / 60.0
            return 0.0
        
        self.input_baslangic.setValue(time_to_decimal(baslangic_str))
        self.input_bitis.setValue(time_to_decimal(bitis_str))
        self.input_yevmiye.setValue(float(yevmiye_str))
        self.input_aciklama.setText(aciklama_str)
        
        # UI modunu değiştir
        self.btn_add.setVisible(False)
        self.btn_update.setVisible(True)
        self.btn_cancel.setVisible(True)
        
        QMessageBox.information(self, "Düzenleme Modu", f"ID {self.editing_id} düzenleniyor.\nDeğerler forma yüklendi.\nDeğişiklikten sonra 'Güncelle' butonuna tıklayın.")
    
    def update_katsayi(self):
        """Seçili katsayıyı güncelle"""
        if self.editing_id is None:
            QMessageBox.warning(self, "Hata", "Düzenleme modu aktif değil!")
            return
        
        baslangic = self.input_baslangic.value()
        bitis = self.input_bitis.value()
        yevmiye_katsayi = self.input_yevmiye.value()
        aciklama = self.input_aciklama.text()
        
        if baslangic >= bitis:
            QMessageBox.warning(self, "Hata", "Başlangıç saati bitiş saatinden küçük olmalı!")
            return
        
        # Veritabanında güncelle
        # NEW: keep update scoped to active tersane (does not break global behavior).
        self.db.update_yevmiye_katsayisi(self.editing_id, baslangic, bitis, yevmiye_katsayi, aciklama, tersane_id=self.tersane_id if self.tersane_id > 0 else None)
        self.load_data()
        self.cancel_edit()
        
        QMessageBox.information(self, "Başarılı", "Yevmiye katsayısı güncellendi!")
    
    def cancel_edit(self):
        """Düzenleme modunu iptal et"""
        self.editing_id = None
        self.input_baslangic.setValue(19.5)
        self.input_bitis.setValue(20.5)
        self.input_yevmiye.setValue(0.5)
        self.input_aciklama.clear()
        
        self.btn_add.setVisible(True)
        self.btn_update.setVisible(False)
        self.btn_cancel.setVisible(False)
        
        self.table.clearSelection()
    
    def on_table_selection_changed(self):
        """Tablo seçimi değiştiğinde (seçim temizlenmişse düzenleme modunu kapat)"""
        if self.table.currentRow() < 0 and self.editing_id is not None:
            self.cancel_edit()
    
    def add_katsayi(self):
        """Yeni yevmiye katsayısı ekle"""
        baslangic = self.input_baslangic.value()
        bitis = self.input_bitis.value()
        yevmiye_katsayi = self.input_yevmiye.value()
        aciklama = self.input_aciklama.text()
        
        if baslangic >= bitis:
            QMessageBox.warning(self, "Hata", "Başlangıç saati bitiş saatinden küçük olmalı!")
            return
        
        # NEW: save yevmiye katsayı under active tersane_id (None keeps global behavior).
        self.db.add_yevmiye_katsayisi(baslangic, bitis, yevmiye_katsayi, aciklama, tersane_id=self.tersane_id if self.tersane_id > 0 else None)
        self.load_data()
        
        # Formu temizle - Başlangıç = bitiş, Bitiş = bitiş + 0.5 (yarım saat sonrası)
        self.input_baslangic.setValue(bitis)
        self.input_bitis.setValue(bitis + 0.5)
        self.input_aciklama.clear()
        
        QMessageBox.information(self, "Başarılı", "Yevmiye katsayısı eklendi!")
    
    def delete_selected(self):
        """Seçili satırı sil"""
        current_row = self.table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Hata", "Lütfen silmek için bir satır seçin!")
            return
        
        id_item = self.table.item(current_row, 0)
        if not id_item:
            return
        
        id = int(id_item.text())
        
        reply = QMessageBox.question(self, "Onay", "Bu yevmiye katsayısını silmek istediğinize emin misiniz?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.db.delete_yevmiye_katsayisi(id)
            self.load_data()
            QMessageBox.information(self, "Başarılı", "Yevmiye katsayısı silindi!")
