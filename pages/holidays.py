
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QComboBox, QLineEdit, QMessageBox, QFrame, QTextEdit, QDoubleSpinBox,
    QInputDialog
)
from PySide6.QtCore import Qt
from core.database import Database
from core.app_logger import log_error

class HolidaysPage(QWidget):
    def __init__(self, signal_manager):
        super().__init__()
        self.db = Database()
        self.signal_manager = signal_manager
        self.setup_ui()
        self.load_holidays()
        self.table.itemSelectionChanged.connect(self.fill_form_from_selection)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Başlık ve açıklama
        title = QLabel("📅 Resmi Tatil Yönetimi")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff; margin-bottom: 2px;")
        layout.addWidget(title)
        desc = QLabel("Yıl boyunca resmi tatilleri ve özel günleri yönetin. Sol listeden seçin, sağda düzenleyin.")
        desc.setStyleSheet("color: #999; font-size: 12px; margin-bottom: 10px;")
        layout.addWidget(desc)

        main = QHBoxLayout()
        layout.addLayout(main)

        # Sol: Tatil listesi
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Tarih", "Tür", "Normal", "Mesai", "Açıklama"])
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setStyleSheet("""
            QTableWidget { background-color: #232323; color: #fff; font-size: 13px; alternate-background-color: #2a2a2a; }
            QHeaderView::section { background-color: #424242; color: #fff; font-size: 13px; }
        """)
        main.addWidget(self.table, 2)

        # Sağ: Form
        form_frame = QFrame()
        form_frame.setStyleSheet("background-color: #333; border-radius: 8px; padding: 18px;")
        form = QVBoxLayout(form_frame)

        self.input_date = QLineEdit()
        self.input_date.setPlaceholderText("Tarih (MM-DD veya YYYY-MM-DD)")
        self.input_date.setStyleSheet("padding: 6px; font-size: 14px; color: #fff; background: #222;")

        self.input_type = QComboBox()
        self.input_type.addItems(["Resmi Tatil","İdari İzin","Özel Gün","Diğer"])
        self.input_type.setStyleSheet("padding: 6px; font-size: 14px; color: #fff; background: #222;")

        self.input_normal = QDoubleSpinBox()
        self.input_normal.setRange(0, 24)
        self.input_normal.setSingleStep(0.5)
        self.input_normal.setStyleSheet("padding: 6px; font-size: 14px; color: #fff; background: #222;")

        self.input_mesai = QDoubleSpinBox()
        self.input_mesai.setRange(0, 24)
        self.input_mesai.setSingleStep(0.5)
        self.input_mesai.setStyleSheet("padding: 6px; font-size: 14px; color: #fff; background: #222;")

        self.input_aciklama = QTextEdit()
        self.input_aciklama.setPlaceholderText("Açıklama")
        self.input_aciklama.setStyleSheet("padding: 6px; font-size: 14px; color: #fff; background: #222;")
        self.input_aciklama.setFixedHeight(48)

        form.addWidget(QLabel("Tarih:"))
        form.addWidget(self.input_date)
        form.addWidget(QLabel("Tür:"))
        form.addWidget(self.input_type)
        form.addWidget(QLabel("Normal Saat:"))
        form.addWidget(self.input_normal)
        form.addWidget(QLabel("Mesai Saat:"))
        form.addWidget(self.input_mesai)
        form.addWidget(QLabel("Açıklama:"))
        form.addWidget(self.input_aciklama)

        # Butonlar
        btn_row = QHBoxLayout()
        self.btn_new = QPushButton("Yeni")
        self.btn_new.clicked.connect(self.clear_form)
        btn_row.addWidget(self.btn_new)

        self.btn_save = QPushButton("Kaydet/Güncelle")
        self.btn_save.clicked.connect(self.save_holiday)
        btn_row.addWidget(self.btn_save)

        self.btn_delete = QPushButton("Sil")
        self.btn_delete.clicked.connect(self.delete_holiday)
        btn_row.addWidget(self.btn_delete)

        self.btn_refresh = QPushButton("Yenile")
        self.btn_refresh.clicked.connect(self.load_holidays)
        btn_row.addWidget(self.btn_refresh)

        self.btn_seed_defaults = QPushButton("Varsayılan Tatilleri Yükle")
        self.btn_seed_defaults.clicked.connect(self.seed_default_holidays)
        btn_row.addWidget(self.btn_seed_defaults)

        self.btn_nager = QPushButton("Nager.date API'den İndir")
        self.btn_nager.clicked.connect(self.import_nager_holidays)
        btn_row.addWidget(self.btn_nager)

        self.btn_dini = QPushButton("Dini Tatilleri Yükle")
        self.btn_dini.clicked.connect(self.seed_dini_tatil_yil)
        btn_row.addWidget(self.btn_dini)

        form.addLayout(btn_row)
        main.addWidget(form_frame, 3)

    def load_holidays(self):
        try:
            holidays = self.db.get_all_holidays()
        except Exception as e:
            QMessageBox.critical(self, "Veritabanı Hatası", f"Tatiller yüklenemedi: {e}")
            log_error(f"Tatil DB hatası: {e}")
            holidays = []
        if not holidays:
            self.table.setRowCount(0)
            return
        self.table.setRowCount(len(holidays))
        for r, (tarih, tur, normal, mesai, aciklama) in enumerate(holidays):
            item_tarih = QTableWidgetItem(tarih)
            item_tur = QTableWidgetItem(tur)
            item_normal = QTableWidgetItem(str(normal))
            item_mesai = QTableWidgetItem(str(mesai))
            item_aciklama = QTableWidgetItem(aciklama)
            self.table.setItem(r, 0, item_tarih)
            self.table.setItem(r, 1, item_tur)
            self.table.setItem(r, 2, item_normal)
            self.table.setItem(r, 3, item_mesai)
            self.table.setItem(r, 4, item_aciklama)
        self.table.clearSelection()
        self.clear_form()

    def fill_form_from_selection(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        self.input_date.setText(self.table.item(row, 0).text())
        self.input_type.setCurrentText(self.table.item(row, 1).text())
        try:
            self.input_normal.setValue(float(self.table.item(row, 2).text()))
        except (ValueError, TypeError):
            self.input_normal.setValue(0)
        try:
            self.input_mesai.setValue(float(self.table.item(row, 3).text()))
        except (ValueError, TypeError):
            self.input_mesai.setValue(0)
        self.input_aciklama.setText(self.table.item(row, 4).text())

    def clear_form(self):
        self.input_date.clear()
        self.input_type.setCurrentIndex(0)
        self.input_normal.setValue(0)
        self.input_mesai.setValue(0)
        self.input_aciklama.clear()
        self.table.clearSelection()

    def save_holiday(self):
        tarih = self.input_date.text().strip()
        tur = self.input_type.currentText()
        normal = self.input_normal.value()
        mesai = self.input_mesai.value()
        aciklama = self.input_aciklama.toPlainText().strip()
        if not tarih:
            QMessageBox.warning(self, "Hata", "Tarih alanı boş olamaz.")
            return
        # Validasyon: MM-DD veya YYYY-MM-DD
        import re
        if not re.match(r"^(\d{2}-\d{2}|\d{4}-\d{2}-\d{2})$", tarih):
            QMessageBox.warning(self, "Hata", "Tarih formatı MM-DD veya YYYY-MM-DD olmalı.")
            return
        try:
            self.db.add_holiday(tarih, tur, normal, mesai, aciklama)
            self.load_holidays()
            QMessageBox.information(self, "Başarılı", "Tatil kaydedildi.")
        except Exception as e:
            QMessageBox.critical(self, "Veritabanı Hatası", f"Kaydedilemedi: {e}")
            log_error(f"Tatil DB hatası: {e}")

    def delete_holiday(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "Hata", "Silinecek tatil seçilmedi.")
            return
        row = rows[0].row()
        tarih = self.table.item(row, 0).text()
        aciklama = self.table.item(row, 4).text()
        reply = QMessageBox.question(self, "Onay", f"{tarih} - {aciklama} silinsin mi?", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            self.db.delete_holiday(tarih)
            self.load_holidays()
            QMessageBox.information(self, "Başarılı", "Tatil silindi.")
        except Exception as e:
            QMessageBox.critical(self, "Veritabanı Hatası", f"Silinemedi: {e}")
            log_error(f"Tatil DB hatası: {e}")

    def import_nager_holidays(self):
        year, ok = QInputDialog.getInt(
            self, "Yıl Seçin",
            "Hangi yıl için Türkiye tatilleri indirilsin?",
            datetime.now().year, 2020, 2035, 1
        )
        if not ok:
            return
        try:
            import requests
            url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/TR"
            resp = requests.get(url, timeout=8)
            resp.raise_for_status()
            holidays = resp.json()
        except Exception as e:
            QMessageBox.critical(self, "Bağlantı Hatası", f"API'ye ulaşılamadı:\n{e}")
            log_error(f"Nager.date API hatası: {e}")
            return
        if not holidays:
            QMessageBox.information(self, "Bilgi", "API'den veri gelmedi.")
            return
        count = 0
        for h in holidays:
            try:
                date_str = h.get("date", "")  # "2025-01-01" — YYYY-MM-DD, yıla özgü
                name = h.get("localName") or h.get("name") or "Tatil"
                self.db.add_holiday(date_str, "Resmi Tatil", 7.5, 0, name)
                count += 1
            except Exception:
                pass  # WHY: tekrar ekleme veya format hatalarını atla.
        self.load_holidays()
        QMessageBox.information(self, "Başarılı", f"{year} yılı için {count} tatil eklendi.")

    def seed_dini_tatil_yil(self):
        year, ok = QInputDialog.getInt(
            self, "Yıl Seçin",
            "Hangi yıl için Ramazan/Kurban tarihleri yüklensin?\n(2024–2030 destekleniyor)",
            datetime.now().year, 2024, 2030, 1
        )
        if not ok:
            return
        try:
            count = self.db.seed_dini_tatiller(year)
            self.load_holidays()
            if count == 0:
                QMessageBox.information(self, "Bilgi", f"{year} yılı dini tatilleri zaten mevcut.")
            else:
                QMessageBox.information(self, "Başarılı", f"{year} yılı için {count} dini tatil eklendi.")
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Eklenemedi: {e}")
            log_error(f"Dini tatil seed hatası: {e}")

    def seed_default_holidays(self):
        reply = QMessageBox.question(
            self,
            "Onay",
            "Varsayılan resmi tatiller eklensin mi?\nMevcut kayıtlar korunur.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            self.db.seed_default_holidays()
            self.load_holidays()
            QMessageBox.information(self, "Başarılı", "Varsayılan tatiller eklendi.")
        except Exception as e:
            QMessageBox.critical(self, "Veritabanı Hatası", f"Eklenemedi: {e}")
            log_error(f"Tatil DB hatası: {e}")
