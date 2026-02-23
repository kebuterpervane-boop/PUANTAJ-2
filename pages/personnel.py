from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, 
                             QTableWidgetItem, QHeaderView, QPushButton, 
                             QLabel, QMessageBox, QFrame, QLineEdit, QDoubleSpinBox, QComboBox, QDateEdit, QCheckBox, QTextEdit, QScrollArea,
                             QProgressDialog)  # NEW: progress UI for background saves.
from PySide6.QtCore import Qt, QDate, QSize, QThread, Signal, Slot, QObject, QTimer  # NEW: threading helpers for smooth UI.
from core.database import Database
from core.input_validators import ensure_non_empty, ensure_non_negative_number, ensure_optional_iso_date

class PersonnelSaveWorker(QObject):
    """Personel kayitlarini arka planda kaydeder."""
    progress = Signal(int, int)  # current, total
    finished = Signal(int)  # saved_count
    cancelled = Signal(int)  # WHY: notify UI on user cancel without crashing.
    error = Signal(str)

    def __init__(self, db, tasks):
        super().__init__()
        self.db = db
        self.tasks = tasks or []  # WHY: keep worker safe if no tasks.
        self._stop_requested = False  # WHY: allow safe cancel without killing the thread.

    def request_stop(self):
        """Arka plan isini guvenle durdur."""
        self._stop_requested = True  # WHY: checked in run loop to stop gracefully.

    @Slot()
    def run(self):
        try:
            total = len(self.tasks)
            self.progress.emit(0, total)
            for idx, t in enumerate(self.tasks, start=1):
                if self._stop_requested or QThread.currentThread().isInterruptionRequested():
                    self.cancelled.emit(idx - 1)  # WHY: report partial completion on cancel.
                    return  # WHY: exit cleanly to avoid unsafe thread termination.
                self.db.update_personnel(
                    t['ad'], t['maas'], t['ekip'], t.get('ozel'), t.get('ekstra', 0.0),
                    t.get('izin_hakki', 0.0), t.get('ise_baslangic'), t.get('cikis_tarihi'),
                    t.get('ekstra_not'), t.get('avans_not'), t.get('yevmiyeci_mi', 0),
                    tersane_id=t.get('tersane_id')
                )
                # Aylık ekstra varsa ayrı tabloya kaydet.
                if t.get('aylik_ekstra') is not None and t.get('aylik_ekstra_yil') and t.get('aylik_ekstra_ay'):
                    self.db.set_ekstra_aylik(
                        t['ad'], t['aylik_ekstra_yil'], t['aylik_ekstra_ay'],
                        t['aylik_ekstra'], t.get('aylik_ekstra_not') or '',
                        tersane_id=t.get('tersane_id')
                    )
                if idx % 5 == 0 or idx == total:
                    self.progress.emit(idx, total)
            self.finished.emit(total)
        except Exception as e:
            self.error.emit(str(e))

class PersonnelPage(QWidget):
    def __init__(self, signal_manager):
        super().__init__()
        self.db = Database()
        self.signal_manager = signal_manager
        self.tersane_id = 0
        self._needs_refresh = False  # NEW: lazy-load flag to avoid heavy refresh on hidden tabs.
        self._changed_rows = set()
        self._save_thread = None  # NEW: keep thread reference to avoid premature GC.
        self._save_worker = None  # NEW: keep worker reference to avoid GC while thread runs.
        self._save_dialog = None  # NEW: progress dialog reference for background saves.
        self._save_done_cb = None  # NEW: optional callback after save completes.
        self._item_changed_connected = False  # WHY: track connection state to avoid RuntimeWarning on disconnect.
        self.setup_ui()
        self.load_data()
        self.table.itemChanged.connect(self._on_item_changed)
        self._item_changed_connected = True
        self.signal_manager.data_updated.connect(self._on_data_updated)  # NEW: lazy refresh to avoid hidden-tab work.

    def set_tersane_id(self, tersane_id, refresh=True):
        """Global tersane seçiciden gelen tersane_id'yi set eder ve verileri yeniler."""
        self.tersane_id = tersane_id
        self._needs_refresh = True  # NEW: mark dirty; refresh can be deferred.
        if refresh:
            self.update_view()  # WHY: only visible page refreshes to keep UI smooth.

    def update_view(self):
        """Görünür sayfa için güncel tersane verilerini yükle."""
        self._needs_refresh = False  # WHY: clear dirty flag after refresh.
        self.load_data()

    def refresh_if_needed(self):
        """Lazy-load için: sayfa görünür olduğunda gerekiyorsa güncelle."""
        if self._needs_refresh:
            self.update_view()

    def _on_data_updated(self):
        """Veri değiştiğinde sadece görünürsek yenile (lazy)."""
        if not self.isVisible():
            self._needs_refresh = True  # WHY: defer heavy refresh until tab is visible.
            return
        self.update_view()
    def _on_item_changed(self, item):
        # Ad sütunu (0) değiştirilemez, diğerleri değişirse satırı işaretle
        if item.column() != 0:
            self._changed_rows.add(item.row())

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)

        title = QLabel("👥 Personel Yönetimi")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff; margin-bottom: 2px;")
        layout.addWidget(title)

        desc = QLabel("Personel kartları, ekip atamaları ve ücret bilgilerini yönetin.")
        desc.setStyleSheet("color: #999; font-size: 12px; margin-bottom: 10px;")
        layout.addWidget(desc)

        # EKLEME FORMU (Responsive Scroll Area)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")
        
        add_frame = QFrame()
        add_frame.setStyleSheet("background-color: #333; border-radius: 5px; padding: 10px;")
        add_frame.setMinimumHeight(200)
        add_layout = QVBoxLayout(add_frame)
        add_layout.setSpacing(8)

        # ROW 1: Ad, Maaş, Ekip
        row1 = QHBoxLayout()
        self.input_ad = QLineEdit()
        self.input_ad.setPlaceholderText("Ad Soyad")
        self.input_ad.setStyleSheet("padding: 5px; color: white;")
        self.input_ad.setMinimumWidth(150)
        
        self.input_maas = QDoubleSpinBox()
        self.input_maas.setRange(0, 1000000)
        self.input_maas.setPrefix("₺ ")
        self.input_maas.setStyleSheet("padding: 5px; color: white;")
        self.input_maas.setMinimumWidth(120)
        
        self.input_ekip = QLineEdit()
        self.input_ekip.setPlaceholderText("Ekip (Örn: Kaynak)")
        self.input_ekip.setStyleSheet("padding: 5px; color: white;")
        self.input_ekip.setMinimumWidth(120)
        
        row1.addWidget(QLabel("📝 Yeni Personel:"))
        row1.addWidget(self.input_ad)
        row1.addWidget(QLabel("Maaş:"))
        row1.addWidget(self.input_maas)
        row1.addWidget(QLabel("Ekip:"))
        row1.addWidget(self.input_ekip)
        row1.addStretch()
        add_layout.addLayout(row1)

        # ROW 2: Ekstra Ödeme + Not, Yıllık İzin
        row2 = QHBoxLayout()
        self.input_ekstra = QDoubleSpinBox()
        self.input_ekstra.setRange(0, 1000000)
        self.input_ekstra.setPrefix("₺ ")
        self.input_ekstra.setStyleSheet("padding: 5px; color: white;")
        self.input_ekstra.setToolTip("Personelin sabit ekstra ödemesini girin")
        self.input_ekstra.setMinimumWidth(100)
        
        self.input_ekstra_not = QLineEdit()
        self.input_ekstra_not.setPlaceholderText("Ekstra Ödeme Açıklaması (prim, yardım vb.)")
        self.input_ekstra_not.setStyleSheet("padding: 5px; color: white;")
        
        self.input_izin_hakki = QDoubleSpinBox()
        self.input_izin_hakki.setRange(0, 365)
        self.input_izin_hakki.setValue(0)
        self.input_izin_hakki.setSuffix(" gün")
        self.input_izin_hakki.setStyleSheet("padding: 5px; color: white;")
        self.input_izin_hakki.setMinimumWidth(80)

        row2.addWidget(QLabel("Ekstra (₺):"))
        row2.addWidget(self.input_ekstra)
        row2.addWidget(self.input_ekstra_not)
        row2.addWidget(QLabel("Yıllık İzin:"))
        row2.addWidget(self.input_izin_hakki)
        row2.addStretch()
        add_layout.addLayout(row2)

        # ROW 3: Özel Durum, İşe Başlangıç, Çıkış Tarihi
        row3 = QHBoxLayout()
        self.input_ozel = QComboBox()
        self.input_ozel.addItems([
            "Yok",
            "Cumartesi Gelmez",
            "Pazar Gelmez",
            "Hafta Sonu Gelmez",
            "Yarı Zamanlı",
            "Proje Bazlı"
        ])
        self.input_ozel.setStyleSheet("padding: 5px; color: white;")
        self.input_ozel.setMinimumWidth(140)

        self.input_ise_baslangic = QDateEdit()
        self.input_ise_baslangic.setCalendarPopup(True)
        self.input_ise_baslangic.setDate(QDate.currentDate())
        self.input_ise_baslangic.setStyleSheet("padding: 5px; color: white;")
        self.input_ise_baslangic.setMinimumWidth(120)

        self.chk_cikis = QCheckBox("Çıkış:")
        self.chk_cikis.setStyleSheet("color: #ccc;")
        self.input_cikis = QDateEdit()
        self.input_cikis.setCalendarPopup(True)
        self.input_cikis.setDate(QDate.currentDate())
        self.input_cikis.setStyleSheet("padding: 5px; color: white;")
        self.input_cikis.setEnabled(False)
        self.input_cikis.setMinimumWidth(120)
        self.chk_cikis.stateChanged.connect(lambda s: self.input_cikis.setEnabled(bool(s)))

        row3.addWidget(QLabel("Özel Durum:"))
        row3.addWidget(self.input_ozel)
        row3.addWidget(QLabel("İşe Başlangıç:"))
        row3.addWidget(self.input_ise_baslangic)
        row3.addWidget(self.chk_cikis)
        row3.addWidget(self.input_cikis)
        row3.addStretch()
        add_layout.addLayout(row3)

        # ROW 4: Avans Açıklaması, Yevmiyeci + Ekle butonu
        row4 = QHBoxLayout()
        self.input_avans_not = QLineEdit()
        self.input_avans_not.setPlaceholderText("Avans Açıklaması (opsiyonel)")
        self.input_avans_not.setStyleSheet("padding: 5px; color: white;")
        
        self.chk_yevmiyeci = QCheckBox("🔧 Yevmiyeci")
        self.chk_yevmiyeci.setStyleSheet("color: #FFA500; font-weight: bold;")
        self.chk_yevmiyeci.setToolTip("Günlük ücretli (tersane sistemi) olarak işaretle")

        self.combo_tersane = QComboBox()
        self.combo_tersane.setStyleSheet("padding: 5px; color: white;")
        self.combo_tersane.setMinimumWidth(140)
        self._load_tersane_combo()

        btn_add = QPushButton("➕ Ekle")
        btn_add.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 20px;")
        btn_add.clicked.connect(self.add_personnel)
        btn_add.setMinimumHeight(35)

        row4.addWidget(QLabel("Avans Açıklaması:"))
        row4.addWidget(self.input_avans_not)
        row4.addWidget(self.chk_yevmiyeci)
        row4.addWidget(QLabel("Tersane:"))
        row4.addWidget(self.combo_tersane)
        row4.addWidget(btn_add)
        add_layout.addLayout(row4)

        scroll_area.setWidget(add_frame)
        layout.addWidget(scroll_area, 0)

        # BİLGİ KUTUSU
        info = QLabel(
            "💡 Özel Durum Açıklamaları:\n"
            "• Cumartesi/Pazar Gelmez: O gün gelmese de 7.5 saat normal alır\n"
            "• Hafta Sonu Gelmez: Hem cumartesi hem pazar için geçerli\n"
            "• Yarı Zamanlı/Proje Bazlı: Sadece bilgi amaçlı (hesaplamayı etkilemez)\n"
            "🔧 Yevmiyeci: Günlük 1 yevmiye, Pazar gelirse 1, çıkış saatine göre ek yevmiye verir"
        )
        info.setStyleSheet("color: #ccc; background-color: #2b2b2b; padding: 8px; border-radius: 5px; font-size: 11px;")
        layout.addWidget(info)

        # Dönem filtresi
        period_layout = QHBoxLayout()
        period_layout.addWidget(QLabel("Dönem:"))
        self.filter_month = QComboBox()
        self.filter_month.addItems(["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"])
        self.filter_year = QComboBox()
        self.filter_year.addItems([str(y) for y in range(2024, 2031)])
        today = QDate.currentDate()
        
        # Kaydedilmiş dönem ayarlarını yükle
        saved_month = int(self.db.get_setting("personnel_filter_month", str(today.month() - 1)))
        saved_year = self.db.get_setting("personnel_filter_year", str(today.year()))
        saved_all_periods = self.db.get_setting("personnel_all_periods", "True") == "True"
        
        self.filter_month.setCurrentIndex(saved_month)
        self.filter_year.setCurrentText(saved_year)
        self.chk_all_periods = QCheckBox("Tüm Dönem")
        self.chk_all_periods.setChecked(saved_all_periods)
        self.filter_month.setEnabled(not saved_all_periods)
        self.filter_year.setEnabled(not saved_all_periods)
        self.chk_all_periods.stateChanged.connect(self.toggle_period_filter)
        self.filter_month.currentIndexChanged.connect(self.on_period_changed)
        self.filter_year.currentIndexChanged.connect(self.on_period_changed)
        period_layout.addWidget(self.filter_month)
        period_layout.addWidget(self.filter_year)
        period_layout.addWidget(self.chk_all_periods)
        period_layout.addStretch()
        layout.addLayout(period_layout)

        # Arama / Filtre
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Personel ara (ad veya ekip)")
        self.search_input.setStyleSheet("padding: 6px; color: white; background-color: #222; border: 1px solid #555;")
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.load_data)
        self.search_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_input)

        # TABLO
        self.table = QTableWidget()
        # Sütunlar: Ad Soyad, Maaş, Ekip, Ekstra, Ekstra Not, Özel Durum, Yıllık İzin, İşe Başlangıç, Çıkış, Avans Not, Yevmiyeci, Tersane
        self.table.setColumnCount(12)
        self.table.setHorizontalHeaderLabels(["Ad Soyad", "Maaş (₺)", "Ekip", "Ekstra (₺)", "Ekstra Açıklaması", "Özel Durum", "Yıllık İzin", "İşe Başlangıç", "Çıkış", "Avans Açıklaması", "Yevmiyeci", "Tersane"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(9, QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget { background-color: #212121; color: white; gridline-color: #424242; alternate-background-color: #2a2a2a; }
            QHeaderView::section { background-color: #424242; color: white; padding: 5px; font-weight: bold; }
            QLineEdit { background-color: #222; color: white; border: 1px solid #555; }
            QTextEdit { background-color: #222; color: white; border: 1px solid #555; }
        """)
        self.table.setMinimumHeight(250)
        
        layout.addWidget(self.table, 1)
        hint_lbl = QLabel("Ipuclari: Arama kutusunu kullan | Sutun basligina tikla sirala")
        hint_lbl.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint_lbl)

        # BUTONLAR
        bot_layout = QHBoxLayout()
        btn_save = QPushButton("💾 Değişiklikleri Kaydet")
        btn_save.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 10px;")
        btn_save.clicked.connect(self.save_changes)
        
        btn_clean = QPushButton("🧹 Kullanılmayanları Temizle")
        btn_clean.setStyleSheet("background-color: #f44336; color: white; padding: 10px;")
        btn_clean.clicked.connect(self.clean_unused)

        bot_layout.addWidget(btn_clean)
        bot_layout.addStretch()
        bot_layout.addWidget(btn_save)
        layout.addLayout(bot_layout)

    def toggle_period_filter(self):
        all_periods = self.chk_all_periods.isChecked()
        self.filter_month.setEnabled(not all_periods)
        self.filter_year.setEnabled(not all_periods)
        self.db.update_setting("personnel_all_periods", str(all_periods))
        self.load_data()

    def on_period_changed(self):
        # Dönem seçimi değiştiğinde ayarları kaydet
        self.db.update_setting("personnel_filter_month", str(self.filter_month.currentIndex()))
        self.db.update_setting("personnel_filter_year", self.filter_year.currentText())
        self.load_data()

    def _on_search_changed(self):
        if hasattr(self, '_search_timer') and self._search_timer:
            self._search_timer.start()

    def load_data(self):
        self.db.sync_personnel()
        # Yükleme sırasında itemChanged sinyali _changed_rows'u kirletmesin
        if self._item_changed_connected:
            self.table.itemChanged.disconnect(self._on_item_changed)
            self._item_changed_connected = False
        sorting = self.table.isSortingEnabled()
        if sorting:
            self.table.setSortingEnabled(False)
        
        if self.chk_all_periods.isChecked():
            data = self.db.get_all_personnel_detailed(tersane_id=self.tersane_id, use_records_filter=True)  # WHY: list personnel by actual daily records for selected tersane.
        else:
            year = int(self.filter_year.currentText())
            month = self.filter_month.currentIndex() + 1
            data = self.db.get_all_personnel_detailed(year, month, tersane_id=self.tersane_id, use_records_filter=True)  # WHY: filter by selected period + actual daily records.
        search_text = (self.search_input.text() if hasattr(self, "search_input") else "").strip().lower()
        if search_text:
            data = [d for d in data if search_text in (d[0] or "").lower() or search_text in (d[2] or "").lower()]
        self.table.setRowCount(len(data))
        
        # Tersane listesini bir kere yükle (her satır için ayrı sorgu yapma)
        tersaneler = []
        try:
            tersaneler = self.db.get_tersaneler()
        except Exception:
            pass

        # Seçili aya özel ekstra ödemeleri yükle (Tüm Dönem değilse)
        ekstra_aylik_map = {}
        if not self.chk_all_periods.isChecked():
            year = int(self.filter_year.currentText())
            month = self.filter_month.currentIndex() + 1
            ekstra_aylik_map = self.db.get_ekstra_aylik_bulk(year, month, tersane_id=self.tersane_id)

        for row, row_data in enumerate(data):
            # row_data: (ad, maas, ekip, ozel, ekstra, izin_hakki, ise_baslangic, cikis_tarihi, ekstra_not, avans_not, yevmiyeci_mi)
            ad = row_data[0]
            maas = row_data[1]
            ekip = row_data[2]
            ozel = row_data[3]
            izin_hakki = row_data[5]
            ise_baslangic = row_data[6]
            cikis_tarihi = row_data[7]
            avans_not = row_data[9] if len(row_data) > 9 else ""
            yevmiyeci_mi = row_data[10] if len(row_data) > 10 else 0
            # Aylık moda özel ekstra; Tüm Dönem ise personel tablosundaki kalıcı ekstra gösterilir
            if ekstra_aylik_map:
                aylik = ekstra_aylik_map.get(ad)
                ekstra = aylik[0] if aylik else 0.0
                ekstra_not = aylik[1] if aylik else ""
            else:
                ekstra = row_data[4]
                ekstra_not = row_data[8] if len(row_data) > 8 else ""

            item_ad = QTableWidgetItem(ad)
            item_ad.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 0, item_ad)
            self.table.setItem(row, 1, QTableWidgetItem(str(maas)))
            self.table.setItem(row, 2, QTableWidgetItem(ekip if ekip else ""))
            self.table.setItem(row, 3, QTableWidgetItem(f"{ekstra:.2f}"))
            self.table.setItem(row, 4, QTableWidgetItem(ekstra_not or ""))
            self.table.setItem(row, 5, QTableWidgetItem(ozel if ozel else "Yok"))
            self.table.setItem(row, 6, QTableWidgetItem(f"{izin_hakki:.1f}"))
            self.table.setItem(row, 7, QTableWidgetItem(ise_baslangic or ""))
            self.table.setItem(row, 8, QTableWidgetItem(cikis_tarihi or ""))
            self.table.setItem(row, 9, QTableWidgetItem(avans_not or ""))
            self.table.setItem(row, 10, QTableWidgetItem("✓" if yevmiyeci_mi else ""))
            # Tersane dropdown
            tersane_id_for_row, _ = self._get_tersane_info_for_personel(ad)
            combo_t = QComboBox()
            combo_t.setStyleSheet("background-color: #222; color: white; border: none;")
            combo_t.blockSignals(True)  # Kurulum sırasında _changed_rows tetiklenmesin
            for t in tersaneler:
                combo_t.addItem(t[1], t[0])  # (ad, id)
            idx_t = combo_t.findData(tersane_id_for_row)
            if idx_t >= 0:
                combo_t.setCurrentIndex(idx_t)
            combo_t.blockSignals(False)
            combo_t.currentIndexChanged.connect(lambda _, r=row: self._changed_rows.add(r))
            self.table.setCellWidget(row, 11, combo_t)

        if sorting:
            self.table.setSortingEnabled(True)
        # Yükleme tamamlandı — sinyali yeniden bağla, dirty state temizle
        self.table.itemChanged.connect(self._on_item_changed)
        self._item_changed_connected = True
        self._changed_rows.clear()

    def add_personnel(self):
        ok, ad = ensure_non_empty(self.input_ad.text(), "Ad Soyad")
        if not ok:
            QMessageBox.warning(self, "Hata", ad)
            return
        ok, maas = ensure_non_negative_number(self.input_maas.value(), "Maas", default=0)
        if not ok:
            QMessageBox.warning(self, "Hata", maas)
            return
        ekip = self.input_ekip.text().strip()
        ok, ekstra = ensure_non_negative_number(self.input_ekstra.value(), "Ekstra odeme", default=0)
        if not ok:
            QMessageBox.warning(self, "Hata", ekstra)
            return
        ekstra_not = self.input_ekstra_not.text().strip()
        avans_not = self.input_avans_not.text().strip()
        ok, izin_hakki = ensure_non_negative_number(self.input_izin_hakki.value(), "Yillik izin", default=0)
        if not ok:
            QMessageBox.warning(self, "Hata", izin_hakki)
            return
        ise_baslangic = self.input_ise_baslangic.date().toPython().strftime('%Y-%m-%d')
        cikis_tarihi = self.input_cikis.date().toPython().strftime('%Y-%m-%d') if self.chk_cikis.isChecked() else None
        if cikis_tarihi and cikis_tarihi < ise_baslangic:
            QMessageBox.warning(self, "Hata", "Cikis tarihi ise baslangic tarihinden once olamaz.")
            return
        ozel = self.input_ozel.currentText()
        if ozel == "Yok": ozel = None
        yevmiyeci_mi = 1 if self.chk_yevmiyeci.isChecked() else 0
        
        tersane_id = self.combo_tersane.currentData()

        # NEW: background save to keep UI responsive.
        tasks = [{
            'ad': ad, 'maas': maas, 'ekip': ekip, 'ozel': ozel, 'ekstra': ekstra,
            'izin_hakki': izin_hakki, 'ise_baslangic': ise_baslangic, 'cikis_tarihi': cikis_tarihi,
            'ekstra_not': ekstra_not, 'avans_not': avans_not, 'yevmiyeci_mi': yevmiyeci_mi,
            'tersane_id': tersane_id
        }]
        def _after_add():
            self.input_ad.clear()
            self.input_maas.setValue(0)
            self.input_ekip.clear()
            self.input_ekstra.setValue(0)
            self.input_ekstra_not.clear()
            self.input_avans_not.clear()
            self.input_izin_hakki.setValue(0)
            self.input_ozel.setCurrentIndex(0)
            self.input_ise_baslangic.setDate(QDate.currentDate())
            self.chk_cikis.setChecked(False)
            self.chk_yevmiyeci.setChecked(False)
            self.load_data()
            self.signal_manager.data_updated.emit()
        self._start_save_worker(tasks, done_cb=_after_add)

    def save_changes(self):
        try:
            if not self._changed_rows:
                QMessageBox.information(self, "Bilgi", "Kaydedilecek değişiklik yok.")
                return
            tasks = []
            errors = []  # WHY: collect row-level errors without crashing the whole save.
            # WHY: safe table reads to avoid NoneType .text() crashes.
            def _safe_item_text(row, col, default=""):
                item = self.table.item(row, col)
                if item is None:
                    return default  # WHY: fallback when cell is empty or missing.
                try:
                    return item.text()
                except Exception:
                    return default  # WHY: ensure safe read even if widget is invalid.

            # Aylık mod mu? Ekstra, personel tablosuna değil personel_ekstra_aylik'e gidecek.
            all_periods = self.chk_all_periods.isChecked()
            ekstra_yil = int(self.filter_year.currentText()) if not all_periods else None
            ekstra_ay = self.filter_month.currentIndex() + 1 if not all_periods else None

            for row in sorted(self._changed_rows):
                try:
                    ok, ad = ensure_non_empty(_safe_item_text(row, 0, ""), "Ad Soyad")
                    if not ok:
                        raise ValueError(ad)  # WHY: skip invalid rows but keep save running.
                    ok, maas = ensure_non_negative_number(_safe_item_text(row, 1, "0"), "Maas", default=0)
                    if not ok:
                        raise ValueError(maas)
                    ekip = _safe_item_text(row, 2, "")
                    ok, ekstra = ensure_non_negative_number(_safe_item_text(row, 3, "0"), "Ekstra odeme", default=0)
                    if not ok:
                        raise ValueError(ekstra)
                    ekstra_not = _safe_item_text(row, 4, "")
                    ozel = _safe_item_text(row, 5, "Yok")
                    ok, izin_hakki = ensure_non_negative_number(_safe_item_text(row, 6, "0"), "Yillik izin", default=0)
                    if not ok:
                        raise ValueError(izin_hakki)
                    ok, ise_baslangic = ensure_optional_iso_date(_safe_item_text(row, 7, ""), "Ise baslangic")
                    if not ok:
                        raise ValueError(ise_baslangic)
                    ok, cikis_tarihi = ensure_optional_iso_date(_safe_item_text(row, 8, ""), "Cikis tarihi")
                    if not ok:
                        raise ValueError(cikis_tarihi)
                    if ise_baslangic and cikis_tarihi and cikis_tarihi < ise_baslangic:
                        raise ValueError("Cikis tarihi ise baslangic tarihinden once olamaz.")
                    avans_not = _safe_item_text(row, 9, "")
                    yevmiyeci_text = _safe_item_text(row, 10, "").strip()
                    yevmiyeci_mi = 1 if yevmiyeci_text else 0
                    combo_t = self.table.cellWidget(row, 11)
                    tersane_id = combo_t.currentData() if combo_t else None
                    if ozel == "Yok":
                        ozel = None
                    tasks.append({
                        'ad': ad, 'maas': maas, 'ekip': ekip, 'ozel': ozel,
                        # Aylık modda ekstra personel tablosuna gitmez; aylik_ekstra alanlarıyla ayrıca kaydedilir.
                        'ekstra': ekstra if all_periods else 0.0,
                        'izin_hakki': izin_hakki, 'ise_baslangic': ise_baslangic,
                        'cikis_tarihi': cikis_tarihi,
                        'ekstra_not': ekstra_not if all_periods else None,
                        'avans_not': avans_not,
                        'yevmiyeci_mi': yevmiyeci_mi, 'tersane_id': tersane_id,
                        # Aylık ekstra bilgileri — worker bunları personel_ekstra_aylik'e kaydeder.
                        'aylik_ekstra': ekstra if not all_periods else None,
                        'aylik_ekstra_not': ekstra_not if not all_periods else None,
                        'aylik_ekstra_yil': ekstra_yil,
                        'aylik_ekstra_ay': ekstra_ay,
                    })
                except Exception as e:
                    try:
                        from core.app_logger import log_error
                        log_error(f"Personel kaydetme hata (satir {row+1}): {e}")
                    except Exception:
                        pass
                    errors.append(f"Satir {row+1}: {e}")  # WHY: inform user which row failed without crashing.
            # NEW: save in background to keep UI responsive.
            def _after_save():
                self.signal_manager.data_updated.emit()  # WHY: keep existing refresh without duplicating success message.
                self._changed_rows.clear()  # WHY: reset dirty rows after successful background save.
            if errors:
                QMessageBox.warning(self, "Uyari", "Bazi satirlar atlandi:\n" + "\n".join(errors[:5]))  # WHY: show a concise list of row errors.
            if not tasks:
                QMessageBox.warning(self, "Bilgi", "Kaydedilecek gecersiz satir kalmadi.")  # WHY: avoid starting worker with empty tasks.
                return
            self._start_save_worker(tasks, done_cb=_after_save)
        except Exception as e:
            QMessageBox.critical(self, "Hata", str(e))

    def _start_save_worker(self, tasks, done_cb=None):
        """Arka planda personel kaydi baslatir (UI donmasini engeller)."""
        if self._save_thread and self._save_thread.isRunning():
            return  # WHY: do not start a second save while one is running.
        self._save_done_cb = done_cb
        # Progress dialog (spinner + progress)
        self._save_dialog = QProgressDialog("Kaydediliyor...", None, 0, 0, self)  # WHY: keep same UI text but make it cancel-safe.
        self._save_dialog.setWindowModality(Qt.WindowModal)  # WHY: preserve modal behavior.
        self._save_dialog.setAutoClose(False)  # WHY: we close explicitly on signals to avoid stuck dialogs.
        self._save_dialog.setAutoReset(False)  # WHY: keep progress state until we close safely.
        self._save_dialog.setMinimumDuration(0)  # WHY: show immediately to prevent perceived freeze.
        self._save_dialog.setAttribute(Qt.WA_DeleteOnClose, False)  # WHY: prevent C++ object deletion before signals stop.
        self._save_dialog.canceled.connect(self._on_save_dialog_canceled)  # WHY: allow safe user cancel without crashing.
        self._save_dialog.rejected.connect(self._on_save_dialog_canceled)  # WHY: handle window close (X) safely.
        self._save_dialog.show()  # WHY: keep user feedback during background work.

        self._save_thread = QThread()  # WHY: run heavy work off the UI thread.
        worker = PersonnelSaveWorker(self.db, tasks)  # WHY: keep existing worker logic, just manage lifecycle safely.
        self._save_worker = worker  # WHY: keep a strong reference to avoid GC while running.
        worker.moveToThread(self._save_thread)  # WHY: execute worker in background thread.
        self._save_thread.started.connect(worker.run)  # WHY: start work when thread starts.
        worker.progress.connect(self._on_save_progress)  # WHY: update UI safely from worker signals.
        worker.finished.connect(self._on_save_finished)  # WHY: close dialog and notify success on completion.
        worker.finished.connect(self._save_dialog.accept)  # WHY: ensure dialog closes even if handler fails.
        worker.finished.connect(self._save_thread.quit)  # WHY: stop thread event loop after completion.
        worker.finished.connect(worker.deleteLater)  # WHY: free worker object safely in Qt.
        worker.cancelled.connect(self._on_save_cancelled)  # WHY: handle user cancel without crashing.
        worker.cancelled.connect(self._save_dialog.accept)  # WHY: close dialog on cancel to avoid hanging UI.
        worker.cancelled.connect(self._save_thread.quit)  # WHY: stop thread after cancel.
        worker.cancelled.connect(worker.deleteLater)  # WHY: clean up worker on cancel.
        worker.error.connect(self._on_save_error)  # WHY: surface errors without freezing UI.
        worker.error.connect(self._save_thread.quit)  # WHY: stop thread on error to avoid orphan threads.
        worker.error.connect(worker.deleteLater)  # WHY: free worker on error path.
        self._save_thread.finished.connect(self._on_save_thread_finished)  # WHY: clear references only after thread stops.
        self._save_thread.finished.connect(self._save_thread.deleteLater)  # WHY: free thread object after finish.
        self._save_thread.start()  # WHY: start background work now that signals are wired.

    def _on_save_progress(self, current, total):
        """Progress dialog guncelleme."""
        if not self._save_dialog:
            return  # WHY: dialog already cleaned up; ignore late signals.
        try:
            if total and self._save_dialog.maximum() != total:
                self._save_dialog.setMaximum(total)  # WHY: show actual progress once total is known.
            self._save_dialog.setValue(current)  # WHY: keep UI responsive with safe progress updates.
        except RuntimeError:
            self._save_dialog = None  # WHY: ignore updates after dialog is deleted to avoid crashes.

    def _on_save_finished(self, saved_count):
        """Save tamamlandi."""
        if self._save_dialog:
            try:
                self._save_dialog.close()  # WHY: close progress dialog on normal completion.
            except RuntimeError:
                pass  # WHY: dialog already deleted; ignore safely.
        self._save_dialog = None  # WHY: release UI reference after safe close.
        QMessageBox.information(self, "Başarılı", "İşlem Başarıyla Tamamlandı.")  # WHY: explicit success message per request.
        if self._save_done_cb:
            self._save_done_cb()
            self._save_done_cb = None

    def _on_save_error(self, msg):
        """Save hata mesaji."""
        if self._save_dialog:
            try:
                self._save_dialog.close()  # WHY: close progress dialog on error.
            except RuntimeError:
                pass  # WHY: dialog already deleted; ignore safely.
        self._save_dialog = None  # WHY: release UI reference after safe close.
        QMessageBox.critical(self, "Hata", f"Kaydetme sirasinda hata: {msg}")  # WHY: show error without crashing UI.

    def _on_save_dialog_canceled(self):
        """Progress dialog kapatildi/iptal edildi."""
        if self._save_worker:
            self._save_worker.request_stop()  # WHY: ask worker to stop safely instead of killing thread.
        if self._save_thread:
            self._save_thread.requestInterruption()  # WHY: set interruption flag for cooperative stop.
        if self._save_dialog:
            try:
                self._save_dialog.setLabelText("Iptal ediliyor...")  # WHY: give user immediate feedback on cancel.
                self._save_dialog.setCancelButtonText("")  # WHY: disable further cancel spam during shutdown.
            except RuntimeError:
                self._save_dialog = None  # WHY: dialog deleted; avoid accessing it.

    def _on_save_cancelled(self, saved_count):
        """Save iptal edildi mesaji."""
        if self._save_dialog:
            try:
                self._save_dialog.close()  # WHY: close dialog on cancel for clean UI.
            except RuntimeError:
                pass  # WHY: dialog already deleted; ignore safely.
        self._save_dialog = None  # WHY: release UI reference after cancel.
        QMessageBox.information(self, "Bilgi", "Islem iptal edildi.")  # WHY: inform user the cancel completed.

    def _on_save_thread_finished(self):
        """Thread kapaninca referanslari temizle."""
        self._save_thread = None  # WHY: clear thread ref after it has fully stopped.
        self._save_worker = None  # WHY: clear worker ref after thread completion.

    def clean_unused(self):
        if QMessageBox.question(self, "Onay", "Silinecek?", QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
            self.db.delete_unused_personnel()
            self.load_data()
            self.signal_manager.data_updated.emit()

    def _load_tersane_combo(self):
        """Tersane combobox'ını doldurur."""
        self.combo_tersane.clear()
        try:
            tersaneler = self.db.get_tersaneler()
            for t in tersaneler:
                self.combo_tersane.addItem(t[1], t[0])  # (ad, id)
        except Exception:
            self.combo_tersane.addItem("Varsayılan Tersane", 1)

    def _get_tersane_adi_for_personel(self, ad_soyad):
        """Personelin bağlı olduğu tersanenin adını döndürür."""
        tid, tad = self._get_tersane_info_for_personel(ad_soyad)
        return tad

    def _get_tersane_info_for_personel(self, ad_soyad):
        """Personelin bağlı olduğu tersanenin (id, ad) tuple'ını döndürür."""
        try:
            with self.db.get_connection() as conn:
                row = conn.execute(
                    "SELECT p.tersane_id, t.ad FROM personel p LEFT JOIN tersane t ON p.tersane_id = t.id WHERE p.ad_soyad=?",
                    (ad_soyad,)
                ).fetchone()
                if row:
                    return row[0], (row[1] or "")
                return None, ""
        except Exception:
            return None, ""
