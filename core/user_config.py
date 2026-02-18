import os
import json
from pathlib import Path

CONFIG_PATH = Path(os.getenv('APPDATA') or Path.home() / '.config') / 'SaralGroup' / 'PuantajApp' / 'ayarlar.json'

DEFAULT_CONFIG = {
    "theme": "dark",
    "font_size": 12
}

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            import logging
            logging.warning(f"Config dosyası okunamadı: {e}")
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
