from PySide6.QtCore import QObject, Signal

class SignalManager(QObject):
    # Veri değiştiğinde tetiklenecek sinyal
    data_updated = Signal()
    # Aktif tersane değiştiğinde tetiklenecek sinyal (tersane_id gönderir)
    tersane_changed = Signal(int)
