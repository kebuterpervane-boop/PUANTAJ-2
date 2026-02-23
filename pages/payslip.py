from datetime import datetime
import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                              QLabel, QComboBox, QMessageBox, QFrame, QFileDialog)
from PySide6.QtWidgets import QProgressDialog  # WHY: show PDF export progress without freezing UI.
from PySide6.QtCore import Qt, QThread, Signal, Slot, QObject  # NEW: threading helpers for smooth UI.
from core.database import Database
from core.user_config import load_config, save_config
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepInFrame
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os
import math

def get_pdf_fonts():
    """Register a Unicode font for Turkish characters if available."""
    registered = pdfmetrics.getRegisteredFontNames()
    if "AppFont" in registered:
        return "AppFont", "AppFont-Bold" if "AppFont-Bold" in registered else "AppFont"
    
    candidates = [
        ("AppFont", "AppFont-Bold", os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arial.ttf"), os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arialbd.ttf")),
        ("AppFont", "AppFont-Bold", os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "segoeui.ttf"), os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "segoeuib.ttf")),
        ("AppFont", "AppFont-Bold", os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "tahoma.ttf"), os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "tahomabd.ttf")),
    ]
    for reg_name, bold_name, reg_path, bold_path in candidates:
        if os.path.exists(reg_path):
            try:
                pdfmetrics.registerFont(TTFont(reg_name, reg_path))
                if os.path.exists(bold_path):
                    pdfmetrics.registerFont(TTFont(bold_name, bold_path))
                    return reg_name, bold_name
                return reg_name, reg_name
            except Exception: continue
    return "Helvetica", "Helvetica-Bold"

class PayslipLoadWorker(QObject):
    """Bordro personel listesini arka planda yukler."""
    finished = Signal(list)  # WHY: personnel_names.
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
            self.finished.emit(names)  # WHY: return data for UI update.
        except Exception as e:
            self.error.emit(str(e))

class PayslipExportWorker(QObject):  # WHY: run PDF exports off the UI thread.
    progress = Signal(int, int)  # WHY: current, total for progress dialog updates.
    finished = Signal(int)  # WHY: number of PDFs generated.
    cancelled = Signal(int)  # WHY: number completed before cancel.
    error = Signal(str)  # WHY: surface export errors safely.

    def __init__(self, make_pdf_fn, tasks):  # WHY: keep worker generic for single or batch export.
        super().__init__()  # WHY: initialize QObject for signal/slot usage.
        self._make_pdf_fn = make_pdf_fn  # WHY: callable to create one PDF.
        self._tasks = tasks  # WHY: list of (person, year, month, path, tersane_id).
        self._stop_requested = False  # WHY: allow cooperative cancel handling.

    def request_stop(self):  # WHY: allow UI to request a safe stop.
        self._stop_requested = True  # WHY: set flag without killing thread.

    @Slot()
    def run(self):  # WHY: thread entry point.
        try:
            total = len(self._tasks)  # WHY: compute total for progress display.
            self.progress.emit(0, total)  # WHY: initialize progress.
            completed = 0  # WHY: track completed PDFs.
            for person, year, month, path, tersane_id in self._tasks:
                if self._stop_requested or QThread.currentThread().isInterruptionRequested():  # WHY: allow cooperative cancel.
                    self.cancelled.emit(completed)  # WHY: notify UI of partial completion.
                    return
                self._make_pdf_fn(person, year, month, path, tersane_id=tersane_id)  # WHY: reuse existing PDF creation logic.
                completed += 1  # WHY: advance progress counter.
                self.progress.emit(completed, total)  # WHY: update progress dialog.
            self.finished.emit(completed)  # WHY: notify UI on normal completion.
        except Exception as e:
            self.error.emit(str(e))  # WHY: forward exception to UI thread.

class PayslipPage(QWidget):
    def __init__(self, signal_manager):
        super().__init__()
        self.db = Database()
        self.signal_manager = signal_manager
        self.tersane_id = 0
        self._needs_refresh = False  # NEW: lazy-load flag to avoid heavy refresh on hidden tabs.
        self._load_thread = None  # NEW: keep thread reference to avoid premature GC.
        self._load_worker = None  # NEW: keep worker reference to avoid GC while thread runs.
        self._export_thread = None  # WHY: keep background PDF export thread reference.
        self._export_worker = None  # WHY: keep export worker alive during run.
        self._export_dialog = None  # WHY: progress dialog for PDF export operations.
        self._export_done_cb = None  # WHY: store export completion callback.
        self._export_cancelled = False  # WHY: track cancel to suppress success toast.
        self.setup_ui()

    def set_tersane_id(self, tersane_id, refresh=True):
        """Global tersane seçiciden gelen tersane_id'yi set eder ve verileri yeniler."""
        self.tersane_id = tersane_id
        self._needs_refresh = True  # NEW: mark dirty; refresh can be deferred.
        if refresh:
            self.update_view()  # WHY: only visible page refreshes to keep UI smooth.

    def update_view(self):
        """Görünür sayfa için güncel tersane verilerini yükle."""
        self._needs_refresh = False  # WHY: clear dirty flag after refresh.
        self.load_personnel()

    def refresh_if_needed(self):
        """Lazy-load için: sayfa görünür olduğunda gerekiyorsa güncelle."""
        if self._needs_refresh:
            self.update_view()

    def _start_export_worker(self, tasks, done_cb=None, label="PDF hazırlanıyor..."):  # WHY: shared PDF export runner to keep UI responsive.
        if self._export_thread and self._export_thread.isRunning():  # WHY: avoid overlapping exports.
            QMessageBox.information(self, "Bilgi", "Devam eden bir dışa aktarma var.")  # WHY: inform user without starting another thread.
            return
        if not tasks:
            return  # WHY: nothing to export.
        self._export_done_cb = done_cb  # WHY: store per-export completion handler.
        self._export_cancelled = False  # WHY: reset cancel state for each new export.
        total = len(tasks)  # WHY: set progress maximum.
        self._export_dialog = QProgressDialog(label, "İptal", 0, total, self)  # WHY: show determinate progress for batch exports.
        self._export_dialog.setWindowModality(Qt.WindowModal)  # WHY: keep modal behavior consistent.
        self._export_dialog.setAutoClose(False)  # WHY: we close explicitly on signals to avoid stuck dialogs.
        self._export_dialog.setAutoReset(False)  # WHY: prevent auto-reset from hiding progress early.
        self._export_dialog.setMinimumDuration(0)  # WHY: show immediately to avoid perceived freeze.
        self._export_dialog.setAttribute(Qt.WA_DeleteOnClose, False)  # WHY: keep dialog alive for late signals.
        self._export_dialog.canceled.connect(self._on_export_dialog_canceled)  # WHY: allow safe cancel without crashing.
        self._export_dialog.rejected.connect(self._on_export_dialog_canceled)  # WHY: handle window close (X) safely.
        self._export_dialog.show()  # WHY: show progress feedback during background work.

        self._export_thread = QThread()  # WHY: run heavy export in background.
        worker = PayslipExportWorker(self.create_payslip_pdf, tasks)  # WHY: reuse existing PDF creation logic.
        self._export_worker = worker  # WHY: keep a strong reference to prevent GC.
        worker.moveToThread(self._export_thread)  # WHY: execute worker in background thread.
        self._export_thread.started.connect(worker.run)  # WHY: start export when thread starts.
        worker.progress.connect(self._on_export_progress)  # WHY: update progress dialog safely.
        worker.finished.connect(self._on_export_finished)  # WHY: handle success payload in UI thread.
        worker.finished.connect(self._export_dialog.accept)  # WHY: close dialog on normal completion.
        worker.finished.connect(self._export_thread.quit)  # WHY: stop thread event loop after completion.
        worker.finished.connect(worker.deleteLater)  # WHY: free worker safely.
        worker.cancelled.connect(self._on_export_cancelled)  # WHY: handle cancel without crashing.
        worker.cancelled.connect(self._export_dialog.accept)  # WHY: close dialog on cancel.
        worker.cancelled.connect(self._export_thread.quit)  # WHY: stop thread on cancel.
        worker.cancelled.connect(worker.deleteLater)  # WHY: free worker on cancel path.
        worker.error.connect(self._on_export_error)  # WHY: surface errors without freezing UI.
        worker.error.connect(self._export_thread.quit)  # WHY: stop thread on error.
        worker.error.connect(worker.deleteLater)  # WHY: free worker on error path.
        self._export_thread.finished.connect(self._on_export_thread_finished)  # WHY: clear refs after thread stops.
        self._export_thread.finished.connect(self._export_thread.deleteLater)  # WHY: free thread object after finish.
        self._export_thread.start()  # WHY: kick off background export.

    def _on_export_progress(self, current, total):  # WHY: update progress dialog safely.
        if not self._export_dialog:
            return  # WHY: dialog already cleaned up; ignore late signals.
        try:
            if total and self._export_dialog.maximum() != total:
                self._export_dialog.setMaximum(total)  # WHY: show actual progress once total is known.
            self._export_dialog.setValue(current)  # WHY: keep UI responsive with progress updates.
        except RuntimeError:
            self._export_dialog = None  # WHY: ignore updates after dialog deletion.

    def _on_export_finished(self, count):  # WHY: handle export completion on UI thread.
        if self._export_dialog:
            try:
                self._export_dialog.close()  # WHY: close dialog on completion.
            except RuntimeError:
                pass  # WHY: dialog already deleted; ignore safely.
        self._export_dialog = None  # WHY: release UI reference after safe close.
        if self._export_cancelled:
            return  # WHY: skip success dialog when user cancelled.
        QMessageBox.information(self, "Başarılı", f"İşlem tamamlandı. Oluşturulan PDF: {count}")  # WHY: confirm completion.
        if self._export_done_cb:
            self._export_done_cb()
            self._export_done_cb = None

    def _on_export_cancelled(self, count):  # WHY: handle export cancel safely.
        if self._export_dialog:
            try:
                self._export_dialog.close()  # WHY: close dialog on cancel.
            except RuntimeError:
                pass  # WHY: dialog already deleted; ignore safely.
        self._export_dialog = None  # WHY: release UI reference after safe close.
        QMessageBox.information(self, "Bilgi", f"İptal edildi. Tamamlanan PDF: {count}")  # WHY: inform user of partial completion.

    def _on_export_error(self, msg):  # WHY: handle export errors uniformly.
        if self._export_dialog:
            try:
                self._export_dialog.close()  # WHY: close dialog on error.
            except RuntimeError:
                pass  # WHY: dialog already deleted; ignore safely.
        self._export_dialog = None  # WHY: release UI reference after safe close.
        QMessageBox.critical(self, "Hata", f"PDF oluşturma sırasında hata: {msg}")  # WHY: show error without crashing UI.

    def _on_export_dialog_canceled(self):  # WHY: safe cancel handling for export progress dialog.
        self._export_cancelled = True  # WHY: mark cancel to skip success toast later.
        if self._export_worker:
            self._export_worker.request_stop()  # WHY: ask worker to stop safely.
        if self._export_thread:
            self._export_thread.requestInterruption()  # WHY: set interruption flag for cooperative stop.
        if self._export_dialog:
            try:
                self._export_dialog.setLabelText("İptal ediliyor...")  # WHY: immediate feedback on cancel.
                self._export_dialog.setCancelButtonText("")  # WHY: prevent repeated cancel clicks.
            except RuntimeError:
                pass  # WHY: dialog already deleted; ignore safely.

    def _on_export_thread_finished(self):  # WHY: clean up thread refs safely after export.
        self._export_thread = None  # WHY: clear thread ref after it stops.
        self._export_worker = None  # WHY: clear worker ref after thread completion.

    def setup_ui(self):
        layout = QVBoxLayout(self)
        title = QLabel("🧾 Bordro Fişi Oluşturucu")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff; margin-bottom: 2px;")
        layout.addWidget(title)

        desc = QLabel("Seçilen personel için dönem bazlı bordro fişi oluşturun.")
        desc.setStyleSheet("color: #999; font-size: 12px; margin-bottom: 10px;")
        layout.addWidget(desc)
        
        # Filtreler (Yıl, Ay, Personel)
        filter_frame = QFrame()
        filter_frame.setStyleSheet("background-color: #333; border-radius: 8px; padding: 15px;")
        filter_layout = QHBoxLayout(filter_frame)
        
        self.combo_year = QComboBox()
        self.combo_year.addItems([str(y) for y in range(2024, 2030)])
        self.combo_year.setCurrentText(str(datetime.now().year))
        self.combo_year.currentTextChanged.connect(self.load_personnel)  # WHY: keep list in sync with selected period.
        
        self.combo_month = QComboBox()
        self.combo_month.addItems(["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", 
                                   "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"])
        self.combo_month.setCurrentIndex(datetime.now().month - 1)
        self.combo_month.currentIndexChanged.connect(self.load_personnel)  # WHY: keep list in sync with selected period.
        
        self.combo_person = QComboBox()
        self.combo_person.setMinimumWidth(200)
        
        btn_refresh = QPushButton("🔄 Personeli Yenile")
        btn_refresh.clicked.connect(self.load_personnel)
        
        filter_layout.addWidget(QLabel("Dönem:"))
        filter_layout.addWidget(self.combo_month)
        filter_layout.addWidget(self.combo_year)
        filter_layout.addSpacing(20)
        filter_layout.addWidget(QLabel("Personel:"))
        filter_layout.addWidget(self.combo_person)
        filter_layout.addWidget(btn_refresh)
        filter_layout.addStretch()
        layout.addWidget(filter_frame)
        
        # Butonlar
        btn_layout = QHBoxLayout()
        btn_single = QPushButton("📄 Tek Kisi Bordro PDF")
        btn_single.setStyleSheet("background-color: #2196F3; color: white; padding: 15px;")
        btn_single.clicked.connect(self.generate_single_pdf)
        btn_all = QPushButton("📦 Tum Personel Bordro PDF")
        btn_all.setStyleSheet("background-color: #4CAF50; color: white; padding: 15px;")
        btn_all.clicked.connect(self.generate_all_pdfs)
        btn_layout.addWidget(btn_single)
        btn_layout.addWidget(btn_all)
        layout.addLayout(btn_layout)
        layout.addStretch()
        self.load_personnel()

    def load_personnel(self):
        self._start_load_worker()  # WHY: load personnel list in background to keep UI smooth.

    def _start_load_worker(self):
        """Arka planda personel listesini yukler (UI donmasini engeller)."""
        if self._load_thread and self._load_thread.isRunning():
            return  # WHY: do not start a second load while one is running.
        year = int(self.combo_year.currentText())
        month = self.combo_month.currentIndex() + 1
        self._load_thread = QThread()  # WHY: run DB work off the UI thread.
        worker = PayslipLoadWorker(self.db, self.tersane_id, year, month)
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

    def _on_load_finished(self, names):
        """Personel listesini combobox'a uygular."""
        try:
            current = self.combo_person.currentText()
            self.combo_person.clear()
            self.combo_person.addItems(names)
            if current:
                self.combo_person.setCurrentText(current)
        except RuntimeError:
            pass  # SAFEGUARD: UI object may be gone; ignore late signals.

    def _on_load_error(self, msg):
        """Arka plan bordro hatasi."""
        QMessageBox.critical(self, "Hata", f"Personel listesi yuklenemedi: {msg}")

    def _on_load_thread_finished(self):
        """Thread kapaninca referanslari temizle."""
        self._load_thread = None  # WHY: clear thread ref after it has fully stopped.
        self._load_worker = None  # WHY: clear worker ref after thread completion.

    def generate_single_pdf(self):
        person = self.combo_person.currentText()
        if not person: return
        year = int(self.combo_year.currentText())
        month = self.combo_month.currentIndex() + 1
        cfg = load_config()
        last_dir = cfg.get("last_export_dir", "")
        default_path = os.path.join(last_dir, f"Bordro_{person}.pdf") if last_dir else f"Bordro_{person}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Kaydet", default_path, "PDF (*.pdf)")
        if path:
            try:
                cfg["last_export_dir"] = os.path.dirname(path)
                save_config(cfg)
            except Exception:
                pass
            tasks = [(person, year, month, path, self.tersane_id)]  # WHY: single-task list for unified worker pipeline.
            self._start_export_worker(tasks, label="Bordro PDF hazırlanıyor...")  # WHY: run PDF generation in background.

    def generate_all_pdfs(self):
        cfg = load_config()
        last_dir = cfg.get("last_export_dir", "")
        folder = QFileDialog.getExistingDirectory(self, "Klasor Sec", last_dir)
        if not folder: return
        try:
            cfg["last_export_dir"] = folder
            save_config(cfg)
        except Exception:
            pass
        year = int(self.combo_year.currentText())
        month = self.combo_month.currentIndex() + 1
        tasks = []  # WHY: batch tasks for worker-driven export.
        for p_name in self.db.get_personnel_names_for_tersane(self.tersane_id, year, month):
            tasks.append((p_name, year, month, os.path.join(folder, f"Bordro_{p_name}.pdf"), self.tersane_id))  # WHY: include tersane scope per PDF.
        self._start_export_worker(tasks, label="Bordro PDF'leri hazırlanıyor...")  # WHY: run batch export in background.

    def compute_payslip(self, person_name, year, month, tersane_id=None):  # WHY: allow tersane-scoped payslip without changing formulas.
        month_str = f"{year}-{month:02d}"
        with self.db.get_connection() as conn:
            c = conn.cursor()
            p_info = c.execute("SELECT maas, ekip_adi, ekstra_odeme, COALESCE(yevmiyeci_mi, 0) FROM personel WHERE ad_soyad=?", (person_name,)).fetchone()
            maas, ekip, ekstra_kalici, yevmiyeci_mi = p_info or (0, None, 0.0, 0)
            # Aylık ekstra varsa personel_ekstra_aylik'ten al; yoksa personel.ekstra_odeme'ye düş.
            aylik_row = c.execute(
                "SELECT miktar FROM personel_ekstra_aylik WHERE ad_soyad=? AND yil=? AND ay=?",
                (person_name, year, month)
            ).fetchone()
            ekstra = aylik_row[0] if aylik_row is not None else ekstra_kalici
            yevmiyeci_mi = bool(yevmiyeci_mi)

            if tersane_id and tersane_id > 0:  # WHY: filter daily records by active tersane when selected.
                records = c.execute("SELECT tarih, giris_saati, cikis_saati, hesaplanan_normal, hesaplanan_mesai, COALESCE(aciklama,'') FROM gunluk_kayit WHERE ad_soyad=? AND tarih LIKE ? AND tersane_id=? ORDER BY tarih", (person_name, f"{month_str}%", tersane_id)).fetchall()  # WHY: include note field for leave/day status rendering.
            else:
                records = c.execute("SELECT tarih, giris_saati, cikis_saati, hesaplanan_normal, hesaplanan_mesai, COALESCE(aciklama,'') FROM gunluk_kayit WHERE ad_soyad=? AND tarih LIKE ? ORDER BY tarih", (person_name, f"{month_str}%")).fetchall()
            avans_records = c.execute("SELECT tur, tutar FROM avans_kesinti WHERE ad_soyad=? AND tarih LIKE ?", (person_name, f"{month_str}%")).fetchall()

        import calendar
        from core.hesaplama import hesapla_maktu_hakedis
        
        # Temel Değişkenler
        records_sorted = sorted(records, key=lambda x: x[0])
        total_avans = sum(r[1] for r in avans_records if r[0] == "Avans")
        total_kesinti = sum(r[1] for r in avans_records if r[0] == "Kesinti")
        
        result_data = {
            'maas': maas, 'ekip': ekip, 'ekstra': ekstra, 'yevmiyeci_mi': yevmiyeci_mi,
            'records': records_sorted, 'total_avans': total_avans, 'total_kesinti': total_kesinti,
            'month': month, 'year': year
        }

        # --- YEVMİYECİ HESABI ---
        if yevmiyeci_mi:
            # 1. Adım: Tüm yevmiyeleri topla (0.86, 1.0, 0.5 vb.)
            raw_total_yevmiye = sum(r[3] for r in records_sorted) # Normal Yevmiye
            raw_total_mesai = sum(r[4] for r in records_sorted)   # Mesai Yevmiye (Adet)
            
            total_raw = raw_total_yevmiye + raw_total_mesai
            
            # 2. Adım: Ay sonu yuvarlama (Kural: En yakın 0.25 katına)
            # Örn: 20.73 -> 20.75
            total_yevmiye_rounded = round(total_raw * 4) / 4.0
            
            # 3. Adım: Para hesabı
            gunluk_ucret = maas # Yevmiyecide maaş alanı günlük ücrettir
            brut = total_yevmiye_rounded * gunluk_ucret
            
            result_data.update({
                'total_normal': raw_total_yevmiye,
                'total_mesai': raw_total_mesai,
                'total_final_yevmiye': total_yevmiye_rounded,
                'gunluk_ucret': gunluk_ucret,
                'brut': brut,
                'net': brut + ekstra - total_avans - total_kesinti,
                'maktu_hesap': None
            })
            
        # --- MAKTU / STANDART HESABI ---
        else:
            # Çalışılan gün sayısı: Normal saati 0'dan büyük olan günler (7.5 veya ceza olsa bile)
            calisan_gun_sayisi = sum(1 for r in records_sorted if r[3] > 0)
            
            # Maktu Hesabı (30 gün kuralı)
            maktu = hesapla_maktu_hakedis(year, month, calisan_gun_sayisi, maas)
            
            # Mesai Hesabı (Mesai (Saat) * Saatlik Ücret) - çarpan yok
            total_mesai_saat = sum(r[4] for r in records_sorted)
            saat_ucreti = (maas / 225.0) # 30 gün * 7.5 saat = 225 saat
            mesai_tutar = total_mesai_saat * saat_ucreti
            
            # Toplam Brüt = Maktu Hakediş + Mesai Tutarı
            brut = maktu['hakedis'] + mesai_tutar
            
            result_data.update({
                'total_normal': calisan_gun_sayisi * 7.5, # Bilgi amaçlı saat
                'total_mesai': total_mesai_saat,
                'gunluk_ucret': maktu['gunluk_ucret'],
                'brut': brut,
                'net': brut + ekstra - total_avans - total_kesinti,
                'maktu_hesap': maktu,
                'calisan_gun_sayisi': calisan_gun_sayisi
            })
            
        return result_data

    def create_payslip_pdf(self, person_name, year, month, filepath, tersane_id=None):  # WHY: allow tersane-scoped PDF without altering calculation logic.
        try:
            data = self.compute_payslip(person_name, year, month, tersane_id=tersane_id)  # WHY: pass tersane filter to daily records.
            tersane_label = "Tüm Tersaneler"  # WHY: default label for global mode.
            if tersane_id and tersane_id > 0:  # WHY: include selected tersane in PDF title.
                tersane = self.db.get_tersane(tersane_id)  # WHY: fetch tersane name safely.
                tersane_label = tersane['ad'] if tersane else f"ID {tersane_id}"  # WHY: fallback keeps PDF usable if name missing.
            doc = SimpleDocTemplate(
                filepath,
                pagesize=A4,
                leftMargin=0.8 * cm,
                rightMargin=0.8 * cm,
                topMargin=0.8 * cm,
                bottomMargin=0.8 * cm
            )
            story = []
            styles = getSampleStyleSheet()
            font_reg, font_bold = get_pdf_fonts()
            
            title_style = ParagraphStyle(
                'Title',
                parent=styles['Heading1'],
                fontName=font_bold,
                fontSize=14,
                alignment=TA_CENTER,
                spaceAfter=8
            )
            story.append(Paragraph(f"BORDRO FISI - {tersane_label}", title_style))  # WHY: include tersane in PDF title.
            
            # Personel Bilgisi
            info = [
                ["Personel:", person_name, "Dönem:", f"{month}/{year}"],
                ["Ekip:", data['ekip'] or "-", "Temel Ucret:", f"{data['maas']:,.2f} TL"]
            ]
            t = Table(info, colWidths=[3*cm, 6*cm, 3*cm, 5*cm])
            t.setStyle(TableStyle([
                ('GRID', (0,0), (-1,-1), 0.8, colors.grey),
                ('FONTNAME', (0,0), (-1,-1), font_reg),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('BOTTOMPADDING', (0,0), (-1,-1), 3),
                ('TOPPADDING', (0,0), (-1,-1), 3),
            ]))
            story.append(t)
            story.append(Spacer(1, 6))
            
            # Detay Tablosu
            yevmiyeci = data['yevmiyeci_mi']
            headers = ["Tarih", "Giris", "Cikis", "Yevmiye" if yevmiyeci else "Normal (Saat)", "Mesai (" + ("Yev" if yevmiyeci else "Saat") + ")", "Durum / Not"]
            table_data = [headers]
            leave_types = {"Yillik Izin", "Is Kazasi Izni", "Raporlu", "Hasta", "Ozur", "Idari Izin", "Dogum Izni", "Evlilik Izni", "Cocuk Izni", "Diger"}
            
            for r in data['records']:
                # r: tarih, giris, cikis, normal, mesai, aciklama
                raw_note = (r[5] or "").strip()
                note = ""
                if raw_note:
                    canonical = self.db._canonicalize_izin_turu(raw_note)
                    if canonical:
                        canonical_norm = self.db._normalize_text_for_compare(canonical).title()
                        note = canonical_norm if canonical_norm in leave_types else canonical
                    else:
                        note = raw_note
                if len(note) > 24:
                    note = note[:24] + "..."
                row = [
                    r[0], r[1] or "-", r[2] or "-",
                    f"{r[3]:.2f}" if yevmiyeci else "Tam Gun" if r[3]>0 else "-", # Maktu'da saat yerine durum
                    f"{r[4]:.2f}",
                    note or "-"
                ]
                table_data.append(row)
                
            row_heights = [0.55 * cm] + [0.40 * cm] * max(1, len(data['records']))
            wt = Table(table_data, colWidths=[2.4*cm, 2.3*cm, 2.3*cm, 2.8*cm, 2.5*cm, 6.7*cm], rowHeights=row_heights)
            wt.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                ('FONTNAME', (0,0), (-1,-1), font_reg),
                ('FONTSIZE', (0,0), (-1,-1), 7),
                ('ALIGN', (3,0), (4,-1), 'CENTER'),
                ('ALIGN', (5,0), (5,-1), 'LEFT'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 2),
                ('BOTTOMPADDING', (0,0), (-1,-1), 2),
            ]))
            story.append(wt)
            story.append(Spacer(1, 6))
            
            # Maktu Detay (Varsa)
            if data['maktu_hesap']:
                m = data['maktu_hesap']
                maktu_line = (
                    f"Maktu: Ay Gun={m['ayin_gercek_gun_sayisi']} | Calisilan={m['calisan_gun']} | "
                    f"Eksik={m['eksik_gun']} | Odemeye Esas={m['odemeye_esas_gun']} | "
                    f"Hakedis={m['hakedis']:,.2f} TL"
                )
                story.append(Paragraph(
                    maktu_line,
                    ParagraphStyle('Maktu', parent=styles['Normal'], fontName=font_reg, fontSize=8, leading=10)
                ))
                story.append(Spacer(1, 4))
            
            # Özet Tablosu
            if yevmiyeci:
                summary = [
                    ["Toplam Yevmiye (Yuvarlanmis):", f"{data['total_final_yevmiye']}"],
                    ["Gunluk Ucret:", f"{data['gunluk_ucret']:,.2f} TL"],
                    ["Brut Tutar:", f"{data['brut']:,.2f} TL"]
                ]
            else:
                summary = [
                    ["Maktu/Normal Hakedis:", f"{data['maktu_hesap']['hakedis'] if data['maktu_hesap'] else 0:,.2f} TL"],
                    ["Mesai Hakedis:", f"{data['brut'] - (data['maktu_hesap']['hakedis'] if data['maktu_hesap'] else 0):,.2f} TL"],
                    ["Brut Tutar:", f"{data['brut']:,.2f} TL"]
                ]
                
            summary.extend([
                ["Ekstra Odeme:", f"{data['ekstra']:,.2f} TL"],
                ["Avans / Kesinti:", f"-{data['total_avans'] + data['total_kesinti']:,.2f} TL"],
                ["NET ODENECEK:", f"{data['net']:,.2f} TL"]
            ])
            
            st = Table(summary, colWidths=[10*cm, 5*cm])
            st.setStyle(TableStyle([
                ('BACKGROUND', (0,-1), (-1,-1), colors.lightgreen),
                ('FONTNAME', (0,0), (-1,-1), font_bold),
                ('GRID', (0,0), (-1,-1), 1, colors.black),
                ('ALIGN', (1,0), (1,-1), 'RIGHT'),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 3),
                ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ]))
            story.append(st)
            
            frame = KeepInFrame(doc.width, doc.height, story, mode='shrink')  # WHY: force single-page layout by shrinking if content overflows.
            doc.build([frame])
            return True
            
        except Exception as e:
            from core.app_logger import log_error
            log_error(f"PDF oluşturma hatası: {e}")
            return False
