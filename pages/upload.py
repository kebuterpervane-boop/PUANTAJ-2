import pandas as pd
from datetime import datetime
import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QPushButton, QLabel,
                             QFileDialog, QProgressBar, QTextEdit, QMessageBox,
                             QDialog, QHBoxLayout, QComboBox, QDialogButtonBox,
                             QFormLayout, QTableWidget, QTableWidgetItem, QHeaderView,
                             QRadioButton, QButtonGroup, QFrame, QScrollArea,
                             QLineEdit)

from PySide6.QtCore import Qt, QThread, Signal, Slot, QObject
from PySide6.QtGui import QColor
from core.database import Database
from core.hesaplama import hesapla_hakedis
from core.user_config import load_config, save_config

def normalize_time_cell(val):
    if pd.isna(val) or str(val).strip() in ['', 'nan', 'NaT']: return ""
    s = str(val).strip()
    try:
        f = float(s)
        if f < 1.0:
            h = int(f * 24)
            m = int((f * 24 - h) * 60)
            return f"{h:02d}:{m:02d}"
    except (ValueError, TypeError): pass
    return s if ":" in s else ""

def tr_lower(text):
    if not isinstance(text, str):
        return str(text).lower()
    return text.replace("İ", "i").replace("I", "ı").lower()


class UploadWorker(QObject):
    progress = Signal(int)
    finished = Signal(int)
    error = Signal(str)

    def __init__(self, files, db_file, all_personel, settings_cache, skip_keys=None, firma_id=None, tersane_id=None, batch_id=None):
        super().__init__()
        self.files = files
        self.db_file = db_file  # WHY: pass file path instead of shared DB object to avoid cross-thread cache/conn sharing.
        self.all_personel = all_personel
        self.settings_cache = settings_cache
        self.skip_keys = skip_keys or set()
        self.firma_id = firma_id
        self.tersane_id = tersane_id
        self.batch_id = batch_id  # WHY: tag each uploaded row for later rollback.


    @Slot()
    def run(self):
        import logging
        from core.database import Database as _DB
        db = _DB(self.db_file, use_cache=False)  # WHY: thread-local DB instance; use_cache=False avoids shared cache mutation.
        total_saved = 0
        skipped_count = 0
        try:
            for idx, fname in enumerate(self.files):
                try:
                    df, error = self.read_file_smart(fname)
                except Exception as e:
                    self.error.emit(f"<span style='color:#EF5350;'>HATA ({os.path.basename(fname)}): Dosya okunurken hata: {str(e)}</span>")
                    logging.exception(e)
                    continue
                if df is None:
                    self.error.emit(f"<span style='color:#EF5350;'>HATA ({os.path.basename(fname)}): {error}</span>")
                    continue
                try:
                    cols = {k: None for k in ['tarih', 'ad', 'giris', 'cikis', 'kayip']}
                    for c in df.columns:
                        cl = tr_lower(str(c))
                        if 'tarih' in cl: cols['tarih'] = c
                        elif 'ad' in cl and 'soyad' in cl: cols['ad'] = c
                        elif 'giris' in cl or 'giriş' in cl: cols['giris'] = c
                        elif 'cikis' in cl or 'çıkış' in cl: cols['cikis'] = c
                        elif 'kayip' in cl or 'kayıp' in cl: cols['kayip'] = c
                    if not cols['tarih'] or not cols['ad']:
                        self.error.emit(f"<span style='color:#EF5350;'>HATA ({os.path.basename(fname)}): 'Tarih' veya 'Ad Soyad' sutunu tespit edilemedi.</span>")
                        continue
                    batch_data = []
                    row_count = len(df)
                    for i, row in enumerate(df.iterrows()):
                        try:
                            t_val = row[1][cols['tarih']]
                            if pd.isna(t_val) or str(t_val).strip() == '': continue
                            if isinstance(t_val, datetime):
                                tarih_str = t_val.strftime("%Y-%m-%d")
                            else:
                                t_str = str(t_val).split()[0].replace('/', '-').replace('.', '-')
                                parts = t_str.split('-')
                                if len(parts) == 3:
                                    if len(parts[2]) == 4:
                                        tarih_str = f"{parts[2]}-{parts[1]}-{parts[0]}"
                                    else:
                                        tarih_str = t_str
                                else:
                                    tarih_str = t_str
                            ad = str(row[1][cols['ad']]).strip()
                            if not ad or ad.lower() == 'nan': continue

                            # Cakisma kontrolu: skip_keys'de varsa atla
                            record_key = f"{tarih_str}|{ad}"
                            if record_key in self.skip_keys:
                                skipped_count += 1
                                continue

                            giris = normalize_time_cell(row[1].get(cols['giris']))
                            cikis = normalize_time_cell(row[1].get(cols['cikis']))
                            kayip = normalize_time_cell(row[1].get(cols['kayip']))
                            p_inf = self.all_personel.get(ad, {'yevmiyeci': 0, 'ozel_durum': None})
                            h_info = db.get_holiday_info(tarih_str)
                            h_set = {tarih_str} if h_info else set()
                            normal, mesai, notlar = hesapla_hakedis(
                                tarih_str, giris, cikis, kayip, h_set,
                                db.get_holiday_info, lambda x: p_inf['ozel_durum'],
                                ad, p_inf['yevmiyeci'], db=db,
                                settings_cache=self.settings_cache.get('shipyard_rules', self.settings_cache) if self.settings_cache else None  # NEW: shipyard_rules dict.
                            )
                            batch_data.append((tarih_str, ad, giris, cikis, kayip, normal, mesai, notlar, self.firma_id, self.tersane_id, self.batch_id))
                            if i % 50 == 0 and row_count > 0:
                                self.progress.emit(int((i / row_count) * 100))
                        except Exception as e:
                            self.error.emit(f"<span style='color:#FFA726;'>Satir atlandi ({os.path.basename(fname)} / satir {i+1}): {str(e)}</span>")
                            logging.exception(e)
                            continue
                    if batch_data:
                        try:
                            with db.get_connection() as conn:
                                conn.execute("PRAGMA journal_mode=WAL;")
                                conn.execute("PRAGMA synchronous=NORMAL;")
                                conn.executemany(
                                    "INSERT OR REPLACE INTO gunluk_kayit "
                                    "(tarih, ad_soyad, giris_saati, cikis_saati, kayip_sure_saat, "
                                    "hesaplanan_normal, hesaplanan_mesai, aciklama, firma_id, tersane_id, import_batch_id) "
                                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                    batch_data
                                )
                                # Personelleri secilen tersane ile iliskilendir
                                if self.tersane_id:
                                    personel_adlari = list(set(row[1] for row in batch_data))
                                    for ad in personel_adlari:
                                        conn.execute(
                                            "UPDATE personel SET tersane_id=? WHERE ad_soyad=? AND (tersane_id IS NULL OR tersane_id != ?)",
                                            (self.tersane_id, ad, self.tersane_id)
                                        )
                                conn.commit()
                            total_saved += len(batch_data)
                        except Exception as e:
                            self.error.emit(f"<span style='color:#EF5350;'>VERITABANI HATASI ({os.path.basename(fname)}): {str(e)}</span>")
                            logging.exception(e)
                            self.finished.emit(total_saved)
                            return
                    self.progress.emit(int((idx+1)/len(self.files)*100))
                except Exception as e:
                    self.error.emit(f"<span style='color:#EF5350;'>Dosya islenemedi ({os.path.basename(fname)}): {str(e)}</span>")
                    logging.exception(e)
                    continue
            if skipped_count > 0:
                self.error.emit(f"<span style='color:#FFD600;'>{skipped_count} kayit cakisma nedeniyle atlandi.</span>")
            self.finished.emit(total_saved)
        except Exception as e:
            self.error.emit(f"<span style='color:#EF5350;'>Kritik Hata: {str(e)}</span>")
            import logging
            logging.exception(e)
            self.finished.emit(total_saved)

    def read_file_smart(self, fname):
        df = None
        try:
            df = pd.read_excel(fname)
        except Exception as e_xls:
            try:
                df = pd.read_csv(fname)
            except Exception as e_csv:
                try:
                    df = pd.read_csv(fname, sep=';')
                except Exception:
                    return None, f"Dosya okunamadi: {str(e_xls)}"
        if df is None or df.empty:
            return None, "Dosya bos veya okunamadi."
        def find_header_row(dataframe):
            cols = [tr_lower(c) for c in dataframe.columns]
            if any('tarih' in c for c in cols) and any(('ad' in c and 'soyad' in c) for c in cols):
                return dataframe
            for i, row in dataframe.head(20).iterrows():
                row_vals = [tr_lower(str(x)) for x in row.values]
                if any('tarih' in x for x in row_vals) and any(('ad' in x and 'soyad' in x) for x in row_vals):
                    dataframe.columns = dataframe.iloc[i]
                    dataframe = dataframe.iloc[i+1:].reset_index(drop=True)
                    return dataframe
            return None
        df_fixed = find_header_row(df)
        if df_fixed is not None:
            return df_fixed, None
        else:
            return None, "Gerekli kolonlar (Tarih, Ad Soyad) bulunamadi. Lutfen dosya basliklarini kontrol edin."


# ─────────────────────────────────────────────
#  ON IZLEME (PREVIEW) DIALOG
# ─────────────────────────────────────────────

class PreviewDialog(QDialog):
    """Yukleme oncesi ilk 5 satiri gosterir. Read-only."""

    def __init__(self, df, col_mapping, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Veri On Izleme")
        self.setMinimumWidth(750)
        self.setMinimumHeight(350)
        self._build_ui(df, col_mapping)

    def _build_ui(self, df, col_mapping):
        layout = QVBoxLayout(self)

        lbl = QLabel("Asagidaki veriler yuklenecek. Lutfen kontrol edin:")
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; margin-bottom: 6px;")
        layout.addWidget(lbl)

        # Eslestirme bilgisi
        mapping_text = "  |  ".join([
            f"{k.title()}: {v}" for k, v in col_mapping.items() if v
        ])
        lbl_map = QLabel(f"Kolon eslestirmesi:  {mapping_text}")
        lbl_map.setStyleSheet("color: #90CAF9; font-size: 11px; margin-bottom: 8px;")
        layout.addWidget(lbl_map)

        preview_rows = min(5, len(df))
        visible_cols = [v for v in col_mapping.values() if v]

        table = QTableWidget(preview_rows, len(visible_cols))
        table.setHorizontalHeaderLabels([str(c) for c in visible_cols])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setEditTriggers(QTableWidget.NoEditTriggers)

        for r in range(preview_rows):
            for ci, col in enumerate(visible_cols):
                val = str(df.iloc[r].get(col, ""))
                item = QTableWidgetItem(val if val != "nan" else "")
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(r, ci, item)

        layout.addWidget(table)

        total_lbl = QLabel(f"Toplam satir sayisi: {len(df)}")
        total_lbl.setStyleSheet("color: #aaa; font-size: 11px; margin-top: 4px;")
        layout.addWidget(total_lbl)

        btn_row = QHBoxLayout()
        btn_ok = QPushButton("Devam Et")
        btn_ok.setStyleSheet("background-color: #4CAF50; color: white; padding: 8px 24px; font-weight: bold;")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Iptal")
        btn_cancel.setStyleSheet("background-color: #757575; color: white; padding: 8px 24px;")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)


# ─────────────────────────────────────────────
#  CAKISMA (CONFLICT) DIALOG
# ─────────────────────────────────────────────

class ConflictDialog(QDialog):
    """Mevcut kayitlarla cakisan satirlari gosterir.
       Kullanici ATLA veya UZERINE YAZ secebilir."""

    SKIP = "skip"
    OVERWRITE = "overwrite"

    def __init__(self, conflicts, parent=None):
        """
        conflicts: list of (tarih, ad_soyad) tuples
        """
        super().__init__(parent)
        self.setWindowTitle("Cakisma Uyarisi")
        self.setMinimumWidth(600)
        self.setMinimumHeight(350)
        self.choice = self.SKIP
        self._build_ui(conflicts)

    def _build_ui(self, conflicts):
        layout = QVBoxLayout(self)

        warn_lbl = QLabel(f"{len(conflicts)} kayit veritabaninda zaten mevcut:")
        warn_lbl.setStyleSheet("color: #FFA726; font-weight: bold; font-size: 13px; margin-bottom: 6px;")
        layout.addWidget(warn_lbl)

        # Cakisma listesi (max 50 goster)
        table = QTableWidget(min(len(conflicts), 50), 2)
        table.setHorizontalHeaderLabels(["Tarih", "Personel"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, (tarih, ad) in enumerate(conflicts[:50]):
            table.setItem(i, 0, QTableWidgetItem(tarih))
            table.setItem(i, 1, QTableWidgetItem(ad))
        layout.addWidget(table)

        if len(conflicts) > 50:
            more = QLabel(f"... ve {len(conflicts) - 50} kayit daha.")
            more.setStyleSheet("color: #aaa; font-size: 11px;")
            layout.addWidget(more)

        # Secenekler
        frame = QFrame()
        frame.setStyleSheet("background-color: #2a2d35; border-radius: 6px; padding: 12px; margin-top: 8px;")
        opt_layout = QVBoxLayout(frame)
        opt_layout.addWidget(QLabel("Ne yapmak istiyorsunuz?"))

        self.radio_skip = QRadioButton("Mevcut kayitlari koru, cakisanlari ATLA (Varsayilan)")
        self.radio_skip.setChecked(True)
        self.radio_skip.setStyleSheet("color: #66BB6A;")
        self.radio_overwrite = QRadioButton("Mevcut kayitlarin UZERINE YAZ")
        self.radio_overwrite.setStyleSheet("color: #EF5350;")

        self.btn_group = QButtonGroup(self)
        self.btn_group.addButton(self.radio_skip)
        self.btn_group.addButton(self.radio_overwrite)

        opt_layout.addWidget(self.radio_skip)
        opt_layout.addWidget(self.radio_overwrite)
        layout.addWidget(frame)

        btn_row = QHBoxLayout()
        btn_ok = QPushButton("Devam Et")
        btn_ok.setStyleSheet("background-color: #2196F3; color: white; padding: 8px 24px; font-weight: bold;")
        btn_ok.clicked.connect(self._on_accept)
        btn_cancel = QPushButton("Iptal")
        btn_cancel.setStyleSheet("background-color: #757575; color: white; padding: 8px 24px;")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def _on_accept(self):
        self.choice = self.SKIP if self.radio_skip.isChecked() else self.OVERWRITE
        self.accept()


# ─────────────────────────────────────────────
#  ANA UPLOAD SAYFASI
# ─────────────────────────────────────────────

class UploadPage(QWidget):
    def __init__(self, signal_manager):
        super().__init__()
        self.db = Database()
        self.signal_manager = signal_manager
        self.tersane_id = 0
        self._needs_refresh = False  # NEW: lazy-load flag (upload page is light but kept consistent).
        self.setAcceptDrops(True)
        self.setup_ui()

    def set_tersane_id(self, tersane_id, refresh=True):
        """Global tersane seçiciden gelen tersane_id'yi set eder."""
        self.tersane_id = tersane_id
        self._needs_refresh = True  # NEW: mark dirty; no heavy refresh needed here.
        if refresh:
            self._needs_refresh = False  # WHY: nothing to refresh for upload page.

    def refresh_if_needed(self):
        """Lazy-load için: upload sayfasında ekstra iş yok."""
        if self._needs_refresh:
            self._needs_refresh = False  # WHY: upload page has no data table to refresh.

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self._current_batch_id = None   # WHY: last successful upload batch_id for rollback.
        self._current_firma_id = None   # WHY: firma scope for rollback month-lock check.

        # Baslik
        title = QLabel("Excel / CSV Dosyası Yükleme")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff; margin-bottom: 2px;")
        layout.addWidget(title)

        desc = QLabel("Puantaj verilerini dosyadan hızlıca içeri aktarın.")
        desc.setStyleSheet("color: #999; font-size: 12px; margin-bottom: 10px;")
        layout.addWidget(desc)

        lbl_info = QLabel("İpucu: Dosyanızın ilk satırında başlıklar olmasa bile sistem otomatik bulmaya çalışır.\n"
                          "Gerekli Sütunlar: Tarih, Ad Soyad\n"
                          "Opsiyonel: Giriş, Çıkış, Kayıp Süre")
        lbl_info.setStyleSheet("color: #aaa; font-style: italic; margin-bottom: 10px;")
        layout.addWidget(lbl_info)

        # Yuklu aylar/gun bilgisi
        self.lbl_month_info = QLabel()
        self.lbl_month_info.setStyleSheet("color: #FFD600; font-size: 12px; margin-bottom: 8px; padding: 6px; "
                                          "background-color: rgba(255,214,0,0.08); border-radius: 4px;")
        self.lbl_month_info.setWordWrap(True)
        layout.addWidget(self.lbl_month_info)
        self.update_month_info()

        drop_zone = QFrame()
        drop_zone.setStyleSheet("""
            QFrame {
                border: 2px dashed #555;
                border-radius: 12px;
                background-color: #1e1e1e;
                min-height: 120px;
            }
            QFrame:hover {
                border-color: #2196F3;
                background-color: #1a2332;
            }
        """)
        drop_layout = QVBoxLayout(drop_zone)
        drop_layout.setAlignment(Qt.AlignCenter)

        drop_icon = QLabel("📂")
        drop_icon.setStyleSheet("font-size: 36px;")
        drop_icon.setAlignment(Qt.AlignCenter)
        drop_layout.addWidget(drop_icon)

        drop_text = QLabel("Dosyayı buraya sürükleyin\nveya aşağıdaki butona tıklayın")
        drop_text.setStyleSheet("color: #888; font-size: 13px;")
        drop_text.setAlignment(Qt.AlignCenter)
        drop_layout.addWidget(drop_text)
        layout.addWidget(drop_zone)

        btn = QPushButton("Dosya Seç ve Yükle")
        btn.setStyleSheet("background-color: #2196F3; color: white; padding: 12px; font-weight: bold; font-size: 13px; border-radius: 6px;")
        btn.clicked.connect(self.start_upload)
        layout.addWidget(btn)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setMaximum(100)
        self.progress.setStyleSheet("""
            QProgressBar { border: 1px solid #555; border-radius: 4px; text-align: center; height: 22px; }
            QProgressBar::chunk { background-color: #4CAF50; border-radius: 3px; }
        """)
        layout.addWidget(self.progress)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(200)
        self.log.setStyleSheet("font-size: 11px; background-color: #1e1e1e; border: 1px solid #333; border-radius: 4px; padding: 4px;")
        layout.addWidget(self.log)

        self.btn_rollback = QPushButton("Son Yuklemeyi Geri Al")
        self.btn_rollback.setStyleSheet(
            "background-color: #B71C1C; color: white; padding: 8px; font-weight: bold; border-radius: 4px;"
        )
        self.btn_rollback.setEnabled(False)  # WHY: enabled only after a successful upload this session.
        self.btn_rollback.clicked.connect(self._do_rollback)
        layout.addWidget(self.btn_rollback)

        layout.addStretch()

    def update_month_info(self):
        try:
            with self.db.get_connection() as conn:
                rows = conn.execute(
                    "SELECT strftime('%Y-%m', tarih) as ym, COUNT(DISTINCT tarih), COUNT(DISTINCT ad_soyad) "
                    "FROM gunluk_kayit GROUP BY ym ORDER BY ym DESC"
                ).fetchall()
            if rows:
                lines = [f"  {r[0]}  ({r[1]} gün, {r[2]} personel)" for r in rows[:12]]
                txt = "Yüklü Dönemler:\n" + "\n".join(lines)
                if len(rows) > 12:
                    txt += f"\n  ... ve {len(rows) - 12} dönem daha"
            else:
                txt = "Henüz yüklü dönem yok."
            self.lbl_month_info.setText(txt)
        except Exception:
            self.lbl_month_info.setText("")

    def append_log(self, text):
        self.log.append(text)

    def upload_finished(self, total):
        self.update_month_info()
        if total > 0:
            self.append_log(f"<span style='color:#66BB6A;'>{total} kayıt başarıyla işlendi.</span>")
            # Batch_id'yi kalıcı olarak kaydet ve rollback butonunu etkinleştir
            if self._current_batch_id:
                self.db.set_last_upload_batch_id(self._current_batch_id)
                self.btn_rollback.setEnabled(True)  # WHY: allow rollback only after successful upload.
                self.append_log(
                    f"<span style='color:#90CAF9;'>Geri alma mevcut (batch: {self._current_batch_id[:8]}...).</span>"
                )
            self.signal_manager.data_updated.emit()
        else:
            self.append_log("<span style='color:#FFA726;'>Hiçbir kayıt eklenmedi.</span>")
        self.progress.setValue(100)

    def start_upload(self):
        cfg = load_config()
        last_dir = cfg.get("last_upload_dir", "")
        files, _ = QFileDialog.getOpenFileNames(self, "Excel Seç", last_dir, "Excel/CSV Files (*.xlsx *.xls *.csv)")
        if not files:
            return
        self._process_file(files[0], files)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                local_file = url.toLocalFile()
                if local_file.lower().endswith(('.xlsx', '.xls', '.csv')):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(('.xlsx', '.xls', '.csv')):
                self._process_file(path)
                event.acceptProposedAction()
                return
        event.ignore()

    def _process_file(self, path, files=None):
        files = files or [path]
        if not files:
            return
        try:
            cfg = load_config()
            cfg["last_upload_dir"] = os.path.dirname(path)
            save_config(cfg)
        except Exception:
            pass

        # 2) Dosya oku (firma sutununu tanimak icin once oku)
        self.progress.setValue(0)
        self.log.clear()

        import pandas as pd
        df = None
        try:
            df = pd.read_excel(path)
        except Exception:
            try:
                df = pd.read_csv(path)
            except Exception:
                try:
                    df = pd.read_csv(path, sep=';')
                except Exception:
                    QMessageBox.warning(self, "Dosya Hatası", "Dosya okunamadı. Lütfen formatı kontrol edin.")
                    return
        if df is None or df.empty:
            QMessageBox.warning(self, "Dosya Hatası", "Dosya boş veya okunamadı.")
            return

        # Header row detection (same as worker)
        cols_lower = [tr_lower(c) for c in df.columns]
        if not (any('tarih' in c for c in cols_lower) and any(('ad' in c and 'soyad' in c) for c in cols_lower)):
            for i, row in df.head(20).iterrows():
                row_vals = [tr_lower(str(x)) for x in row.values]
                if any('tarih' in x for x in row_vals) and any(('ad' in x and 'soyad' in x) for x in row_vals):
                    df.columns = df.iloc[i]
                    df = df.iloc[i+1:].reset_index(drop=True)
                    break

        # 3) Excel'de FIRMA sutunu var mi?
        firma_col = None
        for c in df.columns:
            cl = tr_lower(str(c))
            if cl.strip() in ('firma', 'firma adi', 'firma adı', 'sirket', 'şirket'):
                firma_col = c
                break

        firma_id = None
        if firma_col is not None:
            # Excel'deki firma adlarini topla, normalize et
            raw_firms = df[firma_col].dropna().astype(str).str.strip()
            raw_firms = raw_firms[raw_firms != ''].replace(r'\s+', ' ', regex=True)
            unique_firms = sorted(raw_firms.unique())
            unique_firms = [f for f in unique_firms if f.lower() != 'nan']

            if unique_firms:
                # DB'ye ekle (yoksa olustur)
                for f_name in unique_firms:
                    self.db.add_firma(f_name)

                if len(unique_firms) == 1:
                    # Tek firma: otomatik sec
                    firma_id = self.db.add_firma(unique_firms[0])
                    self.append_log(f"<span style='color:#66BB6A;'>Firma otomatik secildi: {unique_firms[0]}</span>")
                else:
                    # Birden fazla firma: en cok geceni bul, popup ac
                    freq = raw_firms.value_counts()
                    most_common = freq.index[0] if len(freq) > 0 else None
                    firma_id = self.select_firma_dialog(default_firma=most_common)
            else:
                firma_id = self.select_firma_dialog()
        else:
            # FIRMA sutunu yok: popup ac
            firma_id = self.select_firma_dialog()

        if not firma_id:
            return

        # 3.5) Tersane secimi
        tersane_id = self.select_tersane_dialog()
        if not tersane_id:
            return

        # 4) Ay kilidi kontrolu
        from datetime import datetime
        now = datetime.now()
        year = now.year
        month = now.month
        if hasattr(self, 'combo_year') and hasattr(self, 'combo_month'):
            try:
                year = int(self.combo_year.currentText())
                month = self.combo_month.currentIndex() + 1
            except Exception:
                pass
        if self.db.is_month_locked(year, month, firma_id):
            QMessageBox.warning(self, "Ay Kilitli", "Bu ay kilitlidir. Kayit yuklenemez.")
            return

        # 5) Baslik eslestirme
        zorunlu = {'tarih': None, 'ad': None, 'giris': None, 'cikis': None}
        excel_cols = list(df.columns)
        for c in excel_cols:
            cl = tr_lower(str(c))
            if 'tarih' in cl: zorunlu['tarih'] = c
            if 'ad' in cl and 'soyad' in cl: zorunlu['ad'] = c
            if 'giris' in cl or 'giriş' in cl: zorunlu['giris'] = c
            if 'cikis' in cl or 'çıkış' in cl: zorunlu['cikis'] = c

        eksik = [k for k, v in zorunlu.items() if v is None]
        if eksik:
            mapping = self.header_mapping_dialog(excel_cols, eksik)
            if not mapping:
                return
            for k, v in mapping.items():
                if v != "(Bos birak)":
                    zorunlu[k] = v

        # Tarih ve Ad zorunlu
        if not zorunlu['tarih'] or not zorunlu['ad']:
            QMessageBox.warning(self, "Eksik Sutun", "Tarih ve Ad Soyad sutunlari zorunludur.\nLutfen dosyanizi kontrol edin.")
            return

        # 6) On izleme (Preview)
        preview = PreviewDialog(df, zorunlu, self)
        if preview.exec() != QDialog.Accepted:
            self.append_log("<span style='color:#FFA726;'>Yukleme iptal edildi.</span>")
            return

        # 7) Cakisma kontrolu
        settings_cache = self.db.get_settings_cache(tersane_id=tersane_id)
        with self.db.get_connection() as conn:
            all_personel = {r[0]: {'yevmiyeci': r[1], 'ozel_durum': r[2]}
                          for r in conn.execute("SELECT ad_soyad, yevmiyeci_mi, ozel_durum FROM personel").fetchall()}

        skip_keys = set()
        conflicts = self._detect_conflicts(df, zorunlu)
        if conflicts:
            cdlg = ConflictDialog(conflicts, self)
            if cdlg.exec() != QDialog.Accepted:
                self.append_log("<span style='color:#FFA726;'>Yukleme iptal edildi.</span>")
                return
            if cdlg.choice == ConflictDialog.SKIP:
                skip_keys = {f"{t}|{a}" for t, a in conflicts}
                self.append_log(f"<span style='color:#FFD600;'>{len(conflicts)} cakisan kayit atlanacak.</span>")
            else:
                self.append_log(f"<span style='color:#FFA726;'>{len(conflicts)} mevcut kaydin uzerine yazilacak.</span>")

        # 7.5) Batch ID üret + personel snapshot al (rollback için)
        import uuid
        batch_id = uuid.uuid4().hex
        self._current_batch_id = batch_id
        self._current_firma_id = firma_id
        self.btn_rollback.setEnabled(False)  # WHY: disable until upload succeeds.
        ad_col = zorunlu.get('ad')
        if ad_col and ad_col in df.columns:
            try:
                ad_list = list(set(
                    str(v).strip() for v in df[ad_col].dropna()
                    if str(v).strip() and str(v).strip().lower() != 'nan'
                ))
                self.db.snapshot_personel_for_batch(batch_id, ad_list)
            except Exception:
                pass  # SAFEGUARD: snapshot failure must not block upload.

        # 8) Worker baslat (Thread yapisi AYNEN korunuyor)
        self.thread = QThread()
        self.worker = UploadWorker(files, self.db.db_file, all_personel, settings_cache, skip_keys, firma_id, tersane_id, batch_id)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.error.connect(self.append_log)
        self.worker.finished.connect(self.upload_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _detect_conflicts(self, df, col_mapping):
        """Excel'deki satirlari DB ile karsilastir. Cakisan (tarih, ad) ciflerini dondur."""
        tarih_col = col_mapping.get('tarih')
        ad_col = col_mapping.get('ad')
        if not tarih_col or not ad_col:
            return []

        # Excel'den tarih+ad ciftlerini topla
        excel_keys = []
        for _, row in df.iterrows():
            try:
                t_val = row[tarih_col]
                if pd.isna(t_val) or str(t_val).strip() == '':
                    continue
                if isinstance(t_val, datetime):
                    tarih_str = t_val.strftime("%Y-%m-%d")
                else:
                    t_str = str(t_val).split()[0].replace('/', '-').replace('.', '-')
                    parts = t_str.split('-')
                    if len(parts) == 3 and len(parts[2]) == 4:
                        tarih_str = f"{parts[2]}-{parts[1]}-{parts[0]}"
                    else:
                        tarih_str = t_str
                ad = str(row[ad_col]).strip()
                if ad and ad.lower() != 'nan':
                    excel_keys.append((tarih_str, ad))
            except Exception:
                continue

        if not excel_keys:
            return []

        # DB'den mevcut kayitlari cek (sadece ilgili tarih araliginda)
        tarihs = list(set(k[0] for k in excel_keys))
        if not tarihs:
            return []

        min_t = min(tarihs)
        max_t = max(tarihs)
        try:
            with self.db.get_connection() as conn:
                rows = conn.execute(
                    "SELECT tarih, ad_soyad FROM gunluk_kayit WHERE tarih BETWEEN ? AND ?",
                    (min_t, max_t)
                ).fetchall()
            existing = set((r[0], r[1]) for r in rows)
        except Exception:
            return []

        # Kesisimi bul
        conflicts = [(t, a) for t, a in excel_keys if (t, a) in existing]
        # Tekrarlari kaldir
        seen = set()
        unique_conflicts = []
        for c in conflicts:
            if c not in seen:
                seen.add(c)
                unique_conflicts.append(c)
        return unique_conflicts

    def select_firma_dialog(self, default_firma=None):
        rows = self.db.get_firmalar()
        dialog = QDialog(self)
        dialog.setWindowTitle("Firma Secimi")
        dialog.setMinimumWidth(400)
        vbox = QVBoxLayout(dialog)
        vbox.addWidget(QLabel("Bu veriler hangi firmaya ait?"))

        combo = QComboBox()
        default_idx = 0
        for idx, (fid, ad) in enumerate(rows):
            combo.addItem(ad, fid)
            if default_firma and ad.strip().lower() == default_firma.strip().lower():
                default_idx = idx
        combo.setCurrentIndex(default_idx)
        vbox.addWidget(combo)

        # Yeni firma ekleme alani
        sep = QLabel("— veya yeni firma ekle —")
        sep.setStyleSheet("color: #888; font-size: 11px; margin-top: 8px;")
        sep.setAlignment(Qt.AlignCenter)
        vbox.addWidget(sep)

        new_row = QHBoxLayout()
        new_input = QLineEdit()
        new_input.setPlaceholderText("Yeni firma adi...")
        new_btn = QPushButton("Ekle")
        new_btn.setStyleSheet("background-color: #4CAF50; color: white; padding: 4px 12px;")
        new_row.addWidget(new_input)
        new_row.addWidget(new_btn)
        vbox.addLayout(new_row)

        def on_add_firma():
            ad = new_input.text().strip()
            if not ad:
                QMessageBox.warning(dialog, "Bos Ad", "Firma adi bos olamaz.")
                return
            # Listede zaten var mi?
            for i in range(combo.count()):
                if combo.itemText(i).strip().lower() == ad.lower():
                    combo.setCurrentIndex(i)
                    new_input.clear()
                    return
            # DB'ye ekle
            new_id = self.db.add_firma(ad)
            if new_id:
                combo.addItem(ad, new_id)
                combo.setCurrentIndex(combo.count() - 1)
                new_input.clear()

        new_btn.clicked.connect(on_add_firma)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        vbox.addWidget(btns)
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        if dialog.exec() == QDialog.Accepted:
            return combo.currentData()
        return None

    def select_tersane_dialog(self):
        """Hangi tersanenin verisini yüklüyorsunuz? diye sorar."""
        rows = self.db.get_tersaneler()
        if not rows:
            default_id = self.db.add_tersane("Varsayılan Tersane")
            return default_id

        if len(rows) == 1:
            self.append_log(f"<span style='color:#66BB6A;'>Tersane otomatik secildi: {rows[0][1]}</span>")
            return rows[0][0]

        # Global tersane secili ise onu varsayilan yap
        global_tersane = getattr(self, 'tersane_id', 0) or 0

        dialog = QDialog(self)
        dialog.setWindowTitle("Tersane Secimi")
        dialog.setMinimumWidth(500)
        vbox = QVBoxLayout(dialog)

        lbl = QLabel("Hangi tersanenin verisini yukluyorsunuz?")
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; margin-bottom: 8px;")
        vbox.addWidget(lbl)

        combo = QComboBox()
        default_idx = 0
        for i, row in enumerate(rows):
            label = f"{row[1]}  (Giris: {row[2]}, Cikis: {row[3]})"
            combo.addItem(label, row[0])
            if row[0] == global_tersane:
                default_idx = i
        combo.setCurrentIndex(default_idx)
        vbox.addWidget(combo)

        info = QLabel("Secilen tersanenin giris/cikis saatleri hesaplama motoruna parametre olarak gonderilecek.\n"
                       "Yuklenen personeller bu tersane ile iliskilendirilecek.")
        info.setStyleSheet("color: #90CAF9; font-size: 11px; margin-top: 4px;")
        info.setWordWrap(True)
        vbox.addWidget(info)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        vbox.addWidget(btns)
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)

        if dialog.exec() == QDialog.Accepted:
            selected_id = combo.currentData()
            selected_text = combo.currentText().split("  (")[0]
            self.append_log(f"<span style='color:#66BB6A;'>Tersane secildi: {selected_text}</span>")
            return selected_id
        return None

    def _do_rollback(self):
        """Son yükleme batch'ini tamamen geri alır."""
        batch_id = self._current_batch_id
        if not batch_id:
            QMessageBox.information(self, "Rollback", "Geri alınacak yükleme bulunamadı.")
            return
        reply = QMessageBox.question(
            self, "Geri Al",
            f"Son yükleme ({batch_id[:8]}...) geri alınacak.\n"
            "Bu işlem yüklenen kayıtları çöp kutusuna taşır ve personel tersane atamasını geri yükler.\n\n"
            "Devam etmek istiyor musunuz?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        ok, result = self.db.rollback_upload_batch_full(batch_id, firma_id=self._current_firma_id)
        if ok:
            self.append_log(
                f"<span style='color:#66BB6A;'>Geri alma tamamlandi: {result} kayit trash'e taşındı.</span>"
            )
            self._current_batch_id = None
            self._current_firma_id = None
            self.btn_rollback.setEnabled(False)
            self.update_month_info()
            self.signal_manager.data_updated.emit()
        else:
            QMessageBox.warning(self, "Rollback Hatasi", f"Geri alma başarısız: {result}")

    def header_mapping_dialog(self, excel_cols, eksik):
        dialog = QDialog(self)
        dialog.setWindowTitle("Sutun Eslestirme")
        dialog.setMinimumWidth(450)
        vbox = QVBoxLayout(dialog)

        lbl = QLabel("Bazi sutunlar otomatik bulunamadi.\nLutfen Excel'deki karsiliklari secin:")
        lbl.setStyleSheet("margin-bottom: 8px;")
        vbox.addWidget(lbl)

        # Zorunlu / opsiyonel ayirimi
        zorunlu_keys = {'tarih', 'ad'}
        opsiyonel_keys = {'giris', 'cikis'}

        labels = {
            'tarih': 'Tarih sutunu (zorunlu)',
            'ad': 'Ad Soyad sutunu (zorunlu)',
            'giris': 'Giris saati sutunu (opsiyonel)',
            'cikis': 'Cikis saati sutunu (opsiyonel)',
        }

        form = QFormLayout()
        combos = {}
        for k in eksik:
            cb = QComboBox()
            if k in opsiyonel_keys:
                cb.addItem("(Bos birak)")
            cb.addItems([str(c) for c in excel_cols])
            label_text = labels.get(k, f"{k.title()} sutunu")
            form.addRow(label_text + ":", cb)
            combos[k] = cb
        vbox.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        vbox.addWidget(btns)

        def validate_and_accept():
            for key, cb in combos.items():
                if key in zorunlu_keys and cb.currentText() == "(Bos birak)":
                    QMessageBox.warning(dialog, "Zorunlu Alan", f"{labels[key]} bos birakilamaz.")
                    return
            dialog.accept()

        btns.accepted.connect(validate_and_accept)
        btns.rejected.connect(dialog.reject)

        if dialog.exec() == QDialog.Accepted:
            return {k: cb.currentText() for k, cb in combos.items()}
        return None
