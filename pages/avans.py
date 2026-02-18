from datetime import datetime
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget,
                             QTableWidgetItem, QHeaderView, QPushButton,
                             QLabel, QComboBox, QLineEdit, QDoubleSpinBox, QMessageBox, QDateEdit)
from PySide6.QtCore import QThread, Signal, Slot, QObject  # NEW: threading helpers for smooth UI.
from core.database import Database
from core.input_validators import ensure_choice, ensure_non_empty, ensure_non_negative_number

class AvansLoadWorker(QObject):
    """Avans verilerini arka planda yukler."""
    finished = Signal(list, list)  # WHY: personnel_names, avans_records.
    error = Signal(str)  # WHY: surface errors without blocking UI thread.

    def __init__(self, db, tersane_id=0, year=None, month=None):
        super().__init__()
        self.db = db  # WHY: keep DB access same as before, only off UI thread.
        self.tersane_id = tersane_id or 0  # WHY: normalize to keep behavior consistent with global (0) mode.
        self.year = year
        self.month = month

    @Slot()
    def run(self):
        try:
            names = self.db.get_personnel_names_for_tersane(self.tersane_id, self.year, self.month)
            recs = self.db.get_avans_list(tersane_id=self.tersane_id)
            self.finished.emit(names, recs)  # WHY: return data for UI update.
        except Exception as e:
            self.error.emit(str(e))

class AvansPage(QWidget):
    def __init__(self, signal_manager):
        super().__init__()
        self.db = Database()
        self.signal_manager = signal_manager
        self.tersane_id = 0
        self._needs_refresh = False  # NEW: lazy-load flag to avoid heavy refresh on hidden tabs.
        self._load_thread = None  # NEW: keep thread reference to avoid premature GC.
        self._load_worker = None  # NEW: keep worker reference to avoid GC while thread runs.
        self.setup_ui()
        self.update_view()  # WHY: initial load via worker for smoother UI.
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
        self._start_load_worker()  # WHY: load data in background to keep UI smooth.

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

    def setup_ui(self):
        layout = QVBoxLayout(self)

        form = QHBoxLayout()
        self.combo_pers = QComboBox()
        self.combo_pers.setFixedWidth(200)

        self.combo_tur = QComboBox()
        self.combo_tur.addItems(["Avans", "Kesinti"])
        self.input_tut = QDoubleSpinBox()
        self.input_tut.setRange(0, 100000)
        self.date_ed = QDateEdit()
        self.date_ed.setDate(datetime.now().date())
        self.date_ed.setCalendarPopup(True)
        self.date_ed.dateChanged.connect(self.update_view)  # WHY: keep list in sync with selected date/month.
        self.input_desc = QLineEdit()
        self.input_desc.setPlaceholderText("Açıklama")

        btn = QPushButton("Kaydet")
        btn.clicked.connect(self.save)

        form.addWidget(self.combo_pers)
        form.addWidget(self.combo_tur)
        form.addWidget(self.date_ed)
        form.addWidget(self.input_tut)
        form.addWidget(self.input_desc)
        form.addWidget(btn)
        layout.addLayout(form)

        self.refresh_list()  # WHY: initial fill after date_ed exists.

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["ID", "Tarih", "Personel", "Tür", "Tutar", "Açıklama"])
        self.table.setColumnHidden(0, True)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("QTableWidget { background-color: #2b2b2b; color: white; alternate-background-color: #2a2a2a; }")
        layout.addWidget(self.table)

        btn_del = QPushButton("Seçiliyi Sil")
        btn_del.clicked.connect(self.delete)
        layout.addWidget(btn_del)

    def refresh_list(self):
        current = self.combo_pers.currentText()
        self.combo_pers.clear()
        year = self.date_ed.date().year()
        month = self.date_ed.date().month()
        data = self.db.get_personnel_names_for_tersane(self.tersane_id, year, month)  # WHY: include assigned or worked personnel.
        self.combo_pers.addItems([r for r in data])
        self.combo_pers.setCurrentText(current)

    def load_list(self):
        recs = self.db.get_avans_list(tersane_id=self.tersane_id)  # WHY: filter by active tersane with OR logic.
        self.table.setRowCount(len(recs))
        for r, row in enumerate(recs):
            self.table.setItem(r, 0, QTableWidgetItem(str(row[0])))
            self.table.setItem(r, 1, QTableWidgetItem(row[1]))
            self.table.setItem(r, 2, QTableWidgetItem(row[2]))
            self.table.setItem(r, 3, QTableWidgetItem(row[3]))
            self.table.setItem(r, 4, QTableWidgetItem(str(row[4])))
            self.table.setItem(r, 5, QTableWidgetItem(row[5]))

    def save(self):
        ok, person = ensure_non_empty(self.combo_pers.currentText(), "Personel")
        if not ok:
            QMessageBox.warning(self, "Hata", person)
            return
        ok, tur = ensure_choice(self.combo_tur.currentText(), ("Avans", "Kesinti"), "Tur")
        if not ok:
            QMessageBox.warning(self, "Hata", tur)
            return
        ok, tutar = ensure_non_negative_number(self.input_tut.value(), "Tutar")
        if not ok:
            QMessageBox.warning(self, "Hata", tutar)
            return
        if tutar <= 0:
            QMessageBox.warning(self, "Hata", "Tutar sifirdan buyuk olmali.")
            return
        self.db.save_avans(self.date_ed.date().toString("yyyy-MM-dd"),
                           person, tur, tutar, self.input_desc.text().strip())
        self.update_view()  # WHY: refresh lists safely after save.
        self.signal_manager.data_updated.emit()

    def delete(self):
        row = self.table.currentRow()
        if row >= 0:
            self.db.delete_avans(self.table.item(row, 0).text())
            self.update_view()  # WHY: refresh lists safely after delete.
            self.signal_manager.data_updated.emit()

    def _start_load_worker(self):
        """Arka planda avans verisini yukler (UI donmasini engeller)."""
        if self._load_thread and self._load_thread.isRunning():
            return  # WHY: do not start a second load while one is running.
        year = self.date_ed.date().year()
        month = self.date_ed.date().month()
        self._load_thread = QThread()  # WHY: run DB work off the UI thread.
        worker = AvansLoadWorker(self.db, self.tersane_id, year, month)
        self._load_worker = worker  # WHY: keep a strong reference to avoid GC while running.
        worker.moveToThread(self._load_thread)  # WHY: execute worker in background thread.
        self._load_thread.started.connect(worker.run)  # WHY: start work when thread starts.
        worker.finished.connect(self._on_load_finished)  # WHY: update UI when data is ready.
        worker.error.connect(self._on_load_error)  # WHY: show errors without crashing UI.
        worker.finished.connect(self._load_thread.quit)  # WHY: stop thread event loop after completion.
        worker.finished.connect(worker.deleteLater)  # WHY: free worker object safely in Qt.
        worker.error.connect(self._load_thread.quit)  # WHY: stop thread on error to avoid orphan threads.
        worker.error.connect(worker.deleteLater)  # WHY: free worker on error path.
        self._load_thread.finished.connect(self._on_load_thread_finished)  # WHY: clear references only after thread stops.
        self._load_thread.finished.connect(self._load_thread.deleteLater)  # WHY: free thread object after finish.
        self._load_thread.start()  # WHY: start background work now that signals are wired.

    def _on_load_finished(self, names, recs):
        """Arka plan avans sonucunu tabloya uygular."""
        try:
            current = self.combo_pers.currentText()
            self.combo_pers.clear()
            self.combo_pers.addItems(names)
            if current:
                self.combo_pers.setCurrentText(current)
            self.table.setRowCount(len(recs))
            for r, row in enumerate(recs):
                self.table.setItem(r, 0, QTableWidgetItem(str(row[0])))
                self.table.setItem(r, 1, QTableWidgetItem(row[1]))
                self.table.setItem(r, 2, QTableWidgetItem(row[2]))
                self.table.setItem(r, 3, QTableWidgetItem(row[3]))
                self.table.setItem(r, 4, QTableWidgetItem(str(row[4])))
                self.table.setItem(r, 5, QTableWidgetItem(row[5]))
        except RuntimeError:
            pass  # SAFEGUARD: UI object may be gone; ignore late signals.

    def _on_load_error(self, msg):
        """Arka plan avans hatasi."""
        QMessageBox.critical(self, "Hata", f"Avans yuklenemedi: {msg}")

    def _on_load_thread_finished(self):
        """Thread kapaninca referanslari temizle."""
        self._load_thread = None  # WHY: clear thread ref after it has fully stopped.
        self._load_worker = None  # WHY: clear worker ref after thread completion.
