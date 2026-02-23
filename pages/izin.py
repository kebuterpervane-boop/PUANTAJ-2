from datetime import datetime

from PySide6.QtCore import QDate, QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from core.database import Database


class IzinEkleDialog(QDialog):
    def __init__(self, personel_list, izin_turleri=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("İzin Kaydı Ekle")
        self.setFixedSize(400, 280)
        layout = QFormLayout(self)

        self.combo_personel = QComboBox()
        self.combo_personel.addItems(personel_list)
        layout.addRow("Personel:", self.combo_personel)

        self.date_baslangic = QDateEdit()
        self.date_baslangic.setCalendarPopup(True)
        self.date_baslangic.setDate(QDate.currentDate())
        layout.addRow("Başlangıç Tarihi:", self.date_baslangic)

        self.date_bitis = QDateEdit()
        self.date_bitis.setCalendarPopup(True)
        self.date_bitis.setDate(QDate.currentDate())
        layout.addRow("Bitiş Tarihi:", self.date_bitis)

        self.combo_tur = QComboBox()
        izin_turleri = list(izin_turleri or [])
        if not izin_turleri:
            izin_turleri = ["Hasta", "Raporlu", "Özür", "Yıllık İzin", "Doğum İzni", "İdari İzin", "Diğer"]
        self.combo_tur.addItems(izin_turleri)
        layout.addRow("İzin Türü:", self.combo_tur)

        self.spin_gun = QSpinBox()
        self.spin_gun.setRange(1, 365)
        self.spin_gun.setValue(1)
        self.spin_gun.setReadOnly(True)
        layout.addRow("Gün Sayısı:", self.spin_gun)

        self.input_aciklama = QLineEdit()
        layout.addRow("Açıklama:", self.input_aciklama)

        self.date_baslangic.dateChanged.connect(self.update_gun_sayisi)
        self.date_bitis.dateChanged.connect(self.update_gun_sayisi)
        self.update_gun_sayisi()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_values(self):
        return {
            "personel": self.combo_personel.currentText(),
            "tarih": self.date_baslangic.date().toPython().strftime("%Y-%m-%d"),
            "tur": self.combo_tur.currentText(),
            "gun": int(self.spin_gun.value()),
            "aciklama": self.input_aciklama.text(),
        }

    def update_gun_sayisi(self):
        bas = self.date_baslangic.date()
        bit = self.date_bitis.date()
        if bit < bas:
            bit = bas
            self.date_bitis.setDate(bas)

        # Pazarları çıkararak hesapla (yıllık izinden düşmesin)
        gun_sayisi = 0
        current = bas
        while current <= bit:
            # Pazar değilse say (Qt'de Pazar = 7)
            if current.dayOfWeek() != 7:
                gun_sayisi += 1
            current = current.addDays(1)

        self.spin_gun.setValue(max(1, gun_sayisi))


class IzinLoadWorker(QObject):
    """İzin listesini arka planda yükler."""

    finished = Signal(list)
    error = Signal(str)

    def __init__(self, db, year, month, tersane_id=0):
        super().__init__()
        self.db = db
        self.year = year
        self.month = month
        self.tersane_id = tersane_id or 0

    @Slot()
    def run(self):
        try:
            izin_list = self.db.get_izin_list(self.year, self.month, tersane_id=self.tersane_id)
            self.finished.emit(izin_list)
        except Exception as e:
            self.error.emit(str(e))


class IzinYonetimiPage(QWidget):
    def __init__(self, signal_manager):
        super().__init__()
        self.db = Database()
        self.signal_manager = signal_manager
        self.tersane_id = 0
        self._needs_refresh = False
        self._load_thread = None
        self._load_worker = None
        self.setup_ui()
        self.load_data()
        self.signal_manager.data_updated.connect(self._on_data_updated)

    def set_tersane_id(self, tersane_id, refresh=True):
        """Global tersane seçicisinden gelen tersane_id'yi set eder ve verileri yeniler."""
        self.tersane_id = tersane_id
        self._needs_refresh = True
        if refresh:
            self.update_view()

    def update_view(self):
        """Görünür sayfa için güncel tersane verilerini yükle."""
        self._needs_refresh = False
        self.load_data()

    def refresh_if_needed(self):
        """Sayfa görünür olduğunda gerekiyorsa yenile."""
        if self._needs_refresh:
            self.update_view()

    def _on_data_updated(self):
        """Veri değiştiğinde sadece görünürsek yenile."""
        if not self.isVisible():
            self._needs_refresh = True
            return
        self.update_view()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()

        # İzin Kaydı Tab
        izin_widget = QWidget()
        izin_layout = QVBoxLayout(izin_widget)

        title = QLabel("İzin Yönetimi")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff; margin-bottom: 2px;")
        izin_layout.addWidget(title)

        desc = QLabel("İzin kayıtlarını ekleyin, takip edin ve izin türü ayarlarını yönetin.")
        desc.setStyleSheet("color: #999; font-size: 12px; margin-bottom: 10px;")
        izin_layout.addWidget(desc)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Ay:"))
        self.combo_month = QComboBox()
        self.combo_month.addItems(
            ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
        )
        today = datetime.now()
        self.combo_month.setCurrentIndex(today.month - 1)
        self.combo_month.currentIndexChanged.connect(self.load_data)
        filter_layout.addWidget(self.combo_month)

        filter_layout.addWidget(QLabel("Yıl:"))
        self.combo_year = QComboBox()
        self.combo_year.addItems([str(y) for y in range(2024, 2030)])
        self.combo_year.setCurrentText(str(today.year))
        self.combo_year.currentTextChanged.connect(self.load_data)
        filter_layout.addWidget(self.combo_year)
        izin_layout.addLayout(filter_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Personel", "Tarih", "Tür", "Gün", "Durum", "İşlemler"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("QTableWidget { alternate-background-color: #2a2a2a; }")
        izin_layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        btn_ekle = QPushButton("İzin Ekle")
        btn_ekle.setStyleSheet("background-color: #2196F3; color: white; padding: 8px;")
        btn_ekle.clicked.connect(self.add_izin)
        btn_layout.addWidget(btn_ekle)
        izin_layout.addLayout(btn_layout)
        izin_layout.addStretch()

        tabs.addTab(izin_widget, "İzin Kaydı")

        # İzin Ayarları Tab
        ayarlar_widget = QWidget()
        ayarlar_layout = QVBoxLayout(ayarlar_widget)

        ayarlar_title = QLabel("İzin Türü Ayarları")
        ayarlar_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        ayarlar_layout.addWidget(ayarlar_title)

        info_label = QLabel("Yevmiye verilecek izin türlerini seçin:")
        ayarlar_layout.addWidget(info_label)

        self.checkbox_dict = {}
        self.db.init_izin_ayarlari()
        izin_ayarlari = self.db.get_izin_ayarlari()

        group = QGroupBox("İzin Türleri")
        group_layout = QVBoxLayout(group)
        for tur, otomatik in izin_ayarlari:
            checkbox = QCheckBox(f"{tur}")
            checkbox.setChecked(bool(otomatik))
            checkbox.stateChanged.connect(lambda state, t=tur: self.save_izin_ayari(t))
            self.checkbox_dict[tur] = checkbox
            group_layout.addWidget(checkbox)

        ayarlar_layout.addWidget(group)
        ayarlar_layout.addStretch()

        tabs.addTab(ayarlar_widget, "İzin Ayarları")
        layout.addWidget(tabs)

    def save_izin_ayari(self, izin_turu):
        """İzin türü ayarını kaydet."""
        checkbox = self.checkbox_dict[izin_turu]
        self.db.set_izin_otomatik_kayit(izin_turu, checkbox.isChecked())

    def load_data(self):
        self._start_load_worker()

    def _start_load_worker(self):
        """Arka planda izin verisini yükler."""
        if self._load_thread and self._load_thread.isRunning():
            return
        year = int(self.combo_year.currentText())
        month = self.combo_month.currentIndex() + 1
        self._load_thread = QThread()
        worker = IzinLoadWorker(self.db, year, month, self.tersane_id)
        self._load_worker = worker
        worker.moveToThread(self._load_thread)
        self._load_thread.started.connect(worker.run)
        worker.finished.connect(self._on_load_finished)
        worker.error.connect(self._on_load_error)
        worker.finished.connect(self._load_thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(self._load_thread.quit)
        worker.error.connect(worker.deleteLater)
        self._load_thread.finished.connect(self._on_load_thread_finished)
        self._load_thread.finished.connect(self._load_thread.deleteLater)
        self._load_thread.start()

    def _on_load_finished(self, izin_list):
        """İzin listesini tabloya uygular."""
        try:
            self.table.setRowCount(0)
            for row_data in izin_list:
                izin_id, ad_soyad, tarih, tur, gun, aciklama, onay = row_data
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setItem(row, 0, QTableWidgetItem(ad_soyad))
                self.table.setItem(row, 1, QTableWidgetItem(tarih))
                self.table.setItem(row, 2, QTableWidgetItem(tur))
                self.table.setItem(row, 3, QTableWidgetItem(str(gun)))

                durum_text = "Onaylı" if onay else "Bekleme"
                self.table.setItem(row, 4, QTableWidgetItem(durum_text))

                actions_widget = QWidget()
                actions_layout = QHBoxLayout(actions_widget)
                actions_layout.setContentsMargins(2, 2, 2, 2)
                actions_layout.setSpacing(6)

                if not onay:
                    btn_isle = QPushButton("İşle")
                    btn_isle.setStyleSheet("background-color: #4CAF50; color: white;")
                    btn_isle.clicked.connect(lambda checked, izin_id=izin_id: self.process_izin(izin_id))
                    actions_layout.addWidget(btn_isle)

                btn_sil = QPushButton("Sil")
                btn_sil.setStyleSheet("background-color: #f44336; color: white;")
                btn_sil.clicked.connect(lambda checked, izin_id=izin_id: self.delete_izin(izin_id))
                actions_layout.addWidget(btn_sil)

                self.table.setCellWidget(row, 5, actions_widget)
        except RuntimeError:
            pass

    def _on_load_error(self, msg):
        QMessageBox.critical(self, "Hata", f"İzin yüklenemedi: {msg}")

    def _on_load_thread_finished(self):
        self._load_thread = None
        self._load_worker = None

    def add_izin(self):
        year = int(self.combo_year.currentText())
        month = self.combo_month.currentIndex() + 1
        personel_list = self.db.get_personnel_names_for_tersane(self.tersane_id, year, month)
        if not personel_list:
            QMessageBox.warning(self, "Hata", "Personel listesi boş.")
            return

        izin_turleri = [tur for tur, _ in self.db.get_izin_ayarlari()]
        dlg = IzinEkleDialog(personel_list, izin_turleri, self)
        if dlg.exec() == QDialog.Accepted:
            vals = dlg.get_values()
            try:
                self.db.add_izin_with_auto_kayit(
                    vals["personel"],
                    vals["tarih"],
                    vals["tur"],
                    vals["gun"],
                    vals["aciklama"],
                    tersane_id=self.tersane_id,
                )
                QMessageBox.information(
                    self,
                    "Başarılı",
                    f"{vals['personel']} için {vals['gun']} gün {vals['tur']} izni eklendi.",
                )
                self.load_data()
                self.signal_manager.data_updated.emit()
            except Exception as e:
                QMessageBox.critical(self, "Hata", f"İzin eklenirken hata: {e}")

    def process_izin(self, izin_id):
        reply = QMessageBox.question(
            self,
            "Onay",
            "Seçili izin kaydı yeniden işlenecek ve durum Onaylı olacak. Emin misiniz?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            new_id = self.db.process_izin(izin_id, tersane_id=self.tersane_id)
            if not new_id:
                QMessageBox.warning(self, "Bilgi", "İzin kaydı bulunamadı.")
                return
            self.load_data()
            self.signal_manager.data_updated.emit()
            QMessageBox.information(self, "Başarılı", "İzin kaydı işlendi ve Onaylı olarak güncellendi.")
        except Exception as e:
            self.load_data()  # DB durumu değişmiş olabilir, tabloyu güncelle
            QMessageBox.critical(self, "Hata", f"İzin işlenirken hata: {e}")

    def delete_izin(self, izin_id):
        reply = QMessageBox.question(
            self,
            "Onay",
            "Seçili izin kaydı silinecek. Emin misiniz?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            try:
                self.db.delete_izin(izin_id)
                self.load_data()
                self.signal_manager.data_updated.emit()
                QMessageBox.information(self, "Başarılı", "İzin kaydı silindi.")
            except Exception as e:
                QMessageBox.critical(self, "Hata", f"İzin silinirken hata: {e}")
