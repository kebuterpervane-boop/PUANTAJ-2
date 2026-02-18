import sqlite3
import threading
from datetime import datetime

class AyKilitDB:
    _initialized_paths = set()   # WHY: one-time CREATE TABLE per DB path; avoids repeated I/O overhead.
    _init_lock = threading.Lock()  # WHY: protect set from concurrent first-access by multiple threads.

    def __init__(self, db_path):
        self.db_path = db_path
        self._maybe_init_table()  # WHY: guarded init instead of unconditional to cut overhead per call.

    def _maybe_init_table(self):
        """CREATE TABLE yalnızca ilk erişimde çalışır; sonraki çağrılarda atlanır."""
        with AyKilitDB._init_lock:
            if self.db_path in AyKilitDB._initialized_paths:
                return  # WHY: already initialized for this path in this process; skip.
            self._init_table()
            AyKilitDB._initialized_paths.add(self.db_path)

    def _init_table(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS ay_kilit (
                yil INTEGER,
                ay INTEGER,
                firma_id INTEGER,
                kilitli INTEGER DEFAULT 1,
                kilit_tarihi TEXT,
                PRIMARY KEY (yil, ay, firma_id)
            )''')
            conn.commit()

    def is_month_locked(self, year, month, firma_id):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT kilitli FROM ay_kilit WHERE yil=? AND ay=? AND firma_id=?",
                (year, month, firma_id)
            ).fetchone()
            return bool(row and row[0])

    def lock_month(self, year, month, firma_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ay_kilit (yil, ay, firma_id, kilitli, kilit_tarihi) VALUES (?, ?, ?, 1, ?)",
                (year, month, firma_id, datetime.now().isoformat())
            )
            conn.commit()

    def unlock_month(self, year, month, firma_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ay_kilit (yil, ay, firma_id, kilitli, kilit_tarihi) VALUES (?, ?, ?, 0, ?)",
                (year, month, firma_id, datetime.now().isoformat())
            )
            conn.commit()
