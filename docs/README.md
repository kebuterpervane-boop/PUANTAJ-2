# Saral Group - Tersane Puantaj Uygulaması

Modern, kullanıcı dostu bir masaüstü uygulaması - Tersane çalışanları için puantaj, izin, bordro ve raporlama yönetimi.

## 🎯 Özellikler

- 📊 Dashboard ve özet raporlar
- 📥 Excel/CSV veri yükleme
- ✏️ Günlük kayıt yönetimi
- 👥 Personel yönetimi
- 💸 Avans ve kesinti takibi
- 📅 Resmi tatil yönetimi
- 🧾 Bordro fişi oluşturma
- 💰 BES (Bireysel Emeklilik Sistemi) yönetimi
- 📋 İzin yönetimi
- 📈 Detaylı raporlama

## 🚀 Geliştirici İçin - Nasıl Build Edilir?

### Ön Gereksinimler

- Python 3.10 veya üzeri
- pip (Python paket yöneticisi)

### Adım 1: Bağımlılıkları Yükleyin

```bash
pip install -r requirements.txt
```

### Adım 2: EXE Dosyası Oluşturun

**Otomatik Yöntem (Önerilen):**
```bash
python build_exe.py
```

Bu script otomatik olarak:
1. ✅ Tüm bağımlılıkları kontrol edip yükler
2. 🧹 Önceki build dosyalarını temizler
3. 📦 PyInstaller ile EXE oluşturur
4. ✨ Sonuç bilgisini gösterir

**Manuel Yöntem:**
```bash
# Önceki build dosyalarını temizle
rmdir /s /q dist build  # Windows
# veya
rm -rf dist build       # Linux/Mac

# PyInstaller ile build
pyinstaller puantaj.spec --clean
```

### Adım 3: EXE Dosyasını Bulun

Build başarılı olursa:
- 📁 `dist/PuantajApp.exe` dosyası oluşturulur
- 📊 Dosya boyutu yaklaşık 100-150 MB olacaktır
- ✅ Tüm bağımlılıklar EXE içine gömülü olarak gelir

## 👨‍💼 Son Kullanıcı İçin - Nasıl Kullanılır?

### Kurulum Gerekmez! ✨

1. **EXE Dosyasını İndirin**
   - `dist/PuantajApp.exe` dosyasını bilgisayarınıza kopyalayın
   - Python yüklü olmasına gerek yoktur

2. **Uygulamayı Çalıştırın**
   - `PuantajApp.exe` dosyasına çift tıklayın
   - İlk açılışta Windows Defender uyarısı gösterebilir (normal bir durumdur)
   - "Daha fazla bilgi" → "Yine de çalıştır" seçin

3. **Giriş Yapın**
   - Kullanıcı Adı: `admin`
   - Şifre: `1234`

4. **Kullanmaya Başlayın!**
   - Sol menüden istediğiniz sayfaya geçin
   - ❓ Yardım/Kılavuz butonuna tıklayarak detaylı kullanım kılavuzunu okuyun

## 📋 Sistem Gereksinimleri

- **İşletim Sistemi:** Windows 10/11 (64-bit)
- **RAM:** Minimum 4 GB (Önerilen 8 GB)
- **Disk Alanı:** 200 MB boş alan

## 🚢 Release Oluşturma (Maintainers İçin)

### GitHub Release ile Otomatik Build

Repository'de otomatik build ve release sistemi kurulmuştur. Yeni bir release oluşturmak için:

**Adım 1: Yeni version tag'i oluşturun**
```bash
# Önce main branch'e merge edin (veya PR onaylayın)
git checkout main
git pull

# Version tag'i oluşturun (örnek: v1.1.0)
git tag -a v1.1.0 -m "Release v1.1.0 - PyInstaller improvements"

# Tag'i GitHub'a gönderin
git push origin v1.1.0
```

**Adım 2: GitHub Actions otomatik çalışır**
- Tag push edildiğinde `.github/workflows/build-release.yml` tetiklenir
- Windows ortamında otomatik build yapılır
- PyInstaller ile `puantaj.spec` kullanılarak EXE oluşturulur
- GitHub Releases'e otomatik yüklenir

**Adım 3: Release'i kontrol edin**
- https://github.com/kebuterpervane-boop/puantajj-app-deneme-copy-main/releases
- EXE dosyasını ve ZIP'i kontrol edin
- İndirip test edin

### Manuel Release (Alternatif)

Eğer manuel olarak release oluşturmak isterseniz:

1. Local'de EXE build edin:
   ```bash
   python build_exe.py
   ```

2. GitHub web arayüzünden release oluşturun:
   - Releases → "Draft a new release"
   - Tag seçin veya oluşturun (örn: v1.1.0)
   - `dist/PuantajApp.exe` dosyasını yükleyin
   - Release notlarını ekleyin
   - "Publish release"

### Version Numaralandırma

Semantic Versioning kullanın: `vMAJOR.MINOR.PATCH`

- **MAJOR** (v2.0.0): Büyük değişiklikler, geriye uyumsuzluk
- **MINOR** (v1.1.0): Yeni özellikler, geriye uyumlu
- **PATCH** (v1.0.1): Bug fix'ler, küçük düzeltmeler

## 📋 Sistem Gereksinimleri

- **İşletim Sistemi:** Windows 10/11 (64-bit)
- **RAM:** Minimum 4 GB (Önerilen 8 GB)
- **Disk Alanı:** 200 MB boş alan

## 🔧 Teknik Detaylar

### Kullanılan Teknolojiler

- **UI Framework:** PySide6 (Qt6)
- **Veritabanı:** SQLite3
- **Veri İşleme:** pandas, numpy
- **Excel:** openpyxl, xlsxwriter
- **PDF:** reportlab
- **Build Tool:** PyInstaller

### Proje Yapısı

- main.py → Uygulama giriş noktası
- database.py → Veritabanı katmanı
- hesaplama.py → Hakediş ve mesai hesaplamaları
- page_*.py → UI sayfaları
- /docs → Dokümantasyon
- /pdf_output → Oluşturulan bordro PDF’leri
- /backups → Yedek dosyalar
- /migrations → DB geçişleri
- /fonts → PDF fontları

## ❓ Sık Sorulan Sorular

**S: "No module named 'pandas'" hatası alıyorum, ne yapmalıyım?**
A: PyInstaller build'i güncel spec dosyası ile yeniden yapın:
```bash
python build_exe.py
```

**S: EXE dosyası çok büyük, küçültebilir miyim?**
A: EXE tek dosya olarak tüm bağımlılıkları içerir (pandas, numpy, Qt vb.). Bu normal bir boyuttur.

**S: Windows Defender virüs uyarısı veriyor?**
A: PyInstaller ile oluşturulan EXE'ler bazen yanlış pozitif verebilir. "Daha fazla bilgi" → "Yine de çalıştır" seçin.

**S: Uygulama açılışta çöküyor?**
A: Log dosyalarını kontrol edin veya console modunda çalıştırın (puantaj.spec'de console=True yapın).

## 📝 Lisans

Bu proje Saral Group için geliştirilmiştir.

## 📞 Destek

Sorularınız için lütfen geliştirme ekibiyle iletişime geçin.
