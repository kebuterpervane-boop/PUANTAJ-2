# Saral Puantaj - Değişiklik Günlüğü

Tüm önemli değişiklikler bu dosyada belgelenecektir.

Format [Keep a Changelog](https://keepachangelog.com/tr/1.0.0/) standardına dayanır,
ve bu proje [Semantic Versioning](https://semver.org/lang/tr/) kullanır.

## [3.0.2] - 2025-02-18

### Düzeltilen
- Production debug print'leri temizlendi, app_logger'a yönlendirildi
- PyInstaller spec dosyalarındaki yanlış dosya yolları düzeltildi
- Python 3.12+ escape sequence uyarısı giderildi
- UTF-8 BOM encoding sorunu temizlendi

### Eklenen
- Otomatik güncelleme kontrolü sistemi
- GitHub Releases entegrasyonu
- Uygulama içi güncelleme bildirimi
- Otomatik build sistemi (Windows ve Linux için)
- PyInstaller ile tek dosya EXE oluşturma
- Uygulama ikonu
- Build scriptleri (Windows/Linux)
- GitHub Actions workflow

### Değiştirilen
- Version yönetimi merkezi `version.py` dosyasına taşındı
- `update_check.py` GitHub API kullanacak şekilde güncellendi
- `puantaj.spec` dosyası optimize edildi

### Düzeltilen
- N/A

## [1.0.0] - 2026-02-03

### Eklenen
- İlk release versiyonu
- Puantaj yönetim sistemi
- Personel yönetimi
- Maaş bordrosu oluşturma
- Excel import/export
- PDF rapor oluşturma
- Tatil ve izin yönetimi
- Dashboard ve istatistikler
- BES yönetimi
- Disiplin takibi
- Avans yönetimi
- Vardiya yönetimi
- Kullanıcı kılavuzu

[Yayınlanmamış]: https://github.com/kebuterpervane-boop/puantajj-app-deneme-copy-main/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/kebuterpervane-boop/puantajj-app-deneme-copy-main/releases/tag/v1.0.0
