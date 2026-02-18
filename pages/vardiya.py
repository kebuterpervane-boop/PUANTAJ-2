from datetime import datetime
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, 
                             QTableWidgetItem, QHeaderView, QPushButton, 
                             QLabel, QComboBox, QMessageBox, QTimeEdit, QSpinBox,
                             QDialog, QDialogButtonBox, QFormLayout, QLineEdit)
from PySide6.QtCore import Qt, QTime
from core.database import Database

class VardiyaEkleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vardiya Tanımı Ekle")
        self.setFixedSize(400, 200)
        layout = QFormLayout(self)
        
        self.input_adi = QLineEdit()
        layout.addRow("Vardiya Adı:", self.input_adi)
        
        self.time_baslangic = QTimeEdit()
        self.time_baslangic.setTime(QTime(8, 0))
        layout.addRow("Başlangıç Saati:", self.time_baslangic)
        
        self.time_bitis = QTimeEdit()
        self.time_bitis.setTime(QTime(17, 0))
        layout.addRow("Bitiş Saati:", self.time_bitis)
        
        self.spin_normal = QSpinBox()
        self.spin_normal.setRange(1, 12)
        self.spin_normal.setValue(8)
        layout.addRow("Normal Çalışma Saati:", self.spin_normal)
        
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
    
    def get_values(self):
        return {
            'adi': self.input_adi.text(),
            'baslangic': self.time_baslangic.time().toString("HH:mm"),
            'bitis': self.time_bitis.time().toString("HH:mm"),
            'normal_saat': float(self.spin_normal.value())
        }

class VardiyaYonetimiPage(QWidget):
    def __init__(self, signal_manager):
        super().__init__()
        self.db = Database()
        self.signal_manager = signal_manager
        self.setup_ui()
        self.load_vardiyalar()
        self.load_personel_vardiya()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        
        # Sol: Vardiya Tanımları
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("📆 Vardiya Tanımları"))
        
        self.table_vardiya = QTableWidget()
        self.table_vardiya.setColumnCount(5)
        self.table_vardiya.setHorizontalHeaderLabels(["Adı", "Başlangıç", "Bitiş", "Normal Saat", "İşlemler"])
        self.table_vardiya.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        left_layout.addWidget(self.table_vardiya)
        
        btn_vardiya_ekle = QPushButton("➕ Vardiya Ekle")
        btn_vardiya_ekle.setStyleSheet("background-color: #2196F3; color: white; padding: 8px;")
        btn_vardiya_ekle.clicked.connect(self.add_vardiya)
        left_layout.addWidget(btn_vardiya_ekle)
        
        # Sağ: Personel-Vardiya Ataması
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("👤 Personel Vardiya Ataması"))
        
        self.table_personel_vardiya = QTableWidget()
        self.table_personel_vardiya.setColumnCount(3)
        self.table_personel_vardiya.setHorizontalHeaderLabels(["Personel", "Vardiya", "İşlemler"])
        self.table_personel_vardiya.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        right_layout.addWidget(self.table_personel_vardiya)
        
        btn_ata = QPushButton("🔗 Vardiya Ata")
        btn_ata.setStyleSheet("background-color: #4CAF50; color: white; padding: 8px;")
        btn_ata.clicked.connect(self.assign_vardiya)
        right_layout.addWidget(btn_ata)
        
        layout.addLayout(left_layout)
        layout.addLayout(right_layout)

    def load_vardiyalar(self):
        self.table_vardiya.setRowCount(0)
        vardiyalar = self.db.get_vardiyalar()
        
        for vid, adi, baslangic, bitis, normal in vardiyalar:
            row = self.table_vardiya.rowCount()
            self.table_vardiya.insertRow(row)
            
            self.table_vardiya.setItem(row, 0, QTableWidgetItem(adi))
            self.table_vardiya.setItem(row, 1, QTableWidgetItem(baslangic))
            self.table_vardiya.setItem(row, 2, QTableWidgetItem(bitis))
            self.table_vardiya.setItem(row, 3, QTableWidgetItem(str(normal)))
            
            btn_sil = QPushButton("🗑️")
            btn_sil.setMaximumWidth(50)
            self.table_vardiya.setCellWidget(row, 4, btn_sil)

    def load_personel_vardiya(self):
        self.table_personel_vardiya.setRowCount(0)
        all_personel = self.db.get_all_personnel_detailed()
        
        for personel in all_personel:
            ad = personel[0]
            vardiya_info = self.db.get_personel_vardiya(ad)
            vardiya_adi = vardiya_info[0] if vardiya_info else "Atanmadı"
            
            row = self.table_personel_vardiya.rowCount()
            self.table_personel_vardiya.insertRow(row)
            
            self.table_personel_vardiya.setItem(row, 0, QTableWidgetItem(ad))
            self.table_personel_vardiya.setItem(row, 1, QTableWidgetItem(vardiya_adi))
            
            btn_degistir = QPushButton("✏️ Değiştir")
            btn_degistir.setMaximumWidth(100)
            self.table_personel_vardiya.setCellWidget(row, 2, btn_degistir)

    def add_vardiya(self):
        dlg = VardiyaEkleDialog(self)
        if dlg.exec() == QDialog.Accepted:
            vals = dlg.get_values()
            if not vals['adi'].strip():
                QMessageBox.warning(self, "Hata", "Vardiya adı boş olamaz.")
                return
            try:
                self.db.add_vardiya(vals['adi'], vals['baslangic'], vals['bitis'], vals['normal_saat'])
                QMessageBox.information(self, "Başarılı", f"{vals['adi']} vardiyası eklendi.")
                self.load_vardiyalar()
            except Exception as e:
                QMessageBox.critical(self, "Hata", f"Vardiya eklenirken hata: {e}")

    def assign_vardiya(self):
        all_personel = [p[0] for p in self.db.get_all_personnel_detailed()]
        vardiyalar = self.db.get_vardiyalar()
        
        if not all_personel or not vardiyalar:
            QMessageBox.warning(self, "Hata", "Personel veya vardiya listesi boş.")
            return
        
        from PySide6.QtWidgets import QInputDialog
        
        personel, ok1 = QInputDialog.getItem(self, "Personel Seç", "Personeli seçin:", all_personel, 0, False)
        if not ok1:
            return
        
        vardiya_names = [v[1] for v in vardiyalar]
        vardiya, ok2 = QInputDialog.getItem(self, "Vardiya Seç", "Vardiyayı seçin:", vardiya_names, 0, False)
        if not ok2:
            return
        
        vardiya_id = next(v[0] for v in vardiyalar if v[1] == vardiya)
        try:
            self.db.assign_personel_vardiya(personel, vardiya_id)
            QMessageBox.information(self, "Başarılı", f"{personel} kişisine {vardiya} vardiyası atandı.")
            self.load_personel_vardiya()
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Vardiya atanırken hata: {e}")
