import requests
from typing import Optional, Dict
from core.version import __version__, UPDATE_CHECK_URL

def parse_version(version: str) -> tuple:
    """Versiyon string'ini karşılaştırılabilir tuple'a çevirir (1.2.3 -> (1, 2, 3))"""
    try:
        return tuple(map(int, version.lstrip('v').split('.')))
    except Exception:
        return (0, 0, 0)

def check_for_update() -> Optional[Dict[str, str]]:
    """
    GitHub Releases'den en son versiyonu kontrol eder.
    
    Returns:
        Dict içinde 'version', 'download_url', 'release_notes' bilgileri
        veya güncelleme yoksa None
    """
    try:
        resp = requests.get(UPDATE_CHECK_URL, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            latest_version = data.get('tag_name', '').lstrip('v')
            
            # Versiyon karşılaştırması
            current = parse_version(__version__)
            latest = parse_version(latest_version)
            
            if latest > current:
                # Windows .exe dosyasını bul
                download_url = None
                for asset in data.get('assets', []):
                    if asset['name'].endswith('.exe'):
                        download_url = asset['browser_download_url']
                        break
                
                return {
                    'version': latest_version,
                    'download_url': download_url or data.get('html_url'),
                    'release_notes': data.get('body', 'Güncelleme notları mevcut değil.'),
                    'release_url': data.get('html_url')
                }
    except Exception:
        pass

    return None
