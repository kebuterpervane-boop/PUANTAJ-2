# 🚀 Puantaj App - Güncelleme ve Dağıtım Rehberi

## 📌 ÖNEMLİ: Veriler Nerede?

Kullanıcının verileri **EXE dosyasından ayrı** bir yerde tutuluyor:
```
C:\Users\[KullanıcıAdı]\AppData\Roaming\SaralGroup\PuantajApp\puantaj.db
```

Bu sayede:
✅ Yeni exe gönderdiğinde **eski veriler kaybolmaz**
✅ Her kullanıcının verisi kendi bilgisayarında güvende
✅ Program kaldırılsa bile veriler yerinde kalır

---

## 🔄 Güncelleme Yapmak İçin

### 1. Kod Değişikliği Yaptın
```bash
# Workspace klasörüne git
cd "c:\Users\slims\Desktop\puantaj app deneme - Copy"

# Değişiklikleri test et
.\.venv\Scripts\python.exe main.py
```

### 2. Yeni EXE Oluştur
```bash
# PyInstaller ile build et
.\.venv\Scripts\pyinstaller.exe --clean puantaj.spec

# Sonuç: dist\PuantajApp.exe
```

### 3. Kullanıcıya Gönder
- Sadece `dist\PuantajApp.exe` dosyasını gönder
- Kullanıcı eski exe'nin üzerine yazsın
- **VERİLER KAYBOLMAZ** (farklı klasörde)

---

## 💾 Yedekleme Talimatları

### Kullanıcıya Söyle:
1. Programı aç
2. **Ayarlar** sekmesine git
3. "📂 Yedek Al" butonuna bas
4. Yedek klasörü seç (Desktop gibi)
5. Yedek dosyası: `puantaj_backup_YYYYMMDD_HHMMSS.db`

### Manuel Yedekleme:
```
Veritabanı konumu: 
Windows tuşu + R → %APPDATA%\SaralGroup\PuantajApp
puantaj.db dosyasını kopyala
```

---

## 🛡️ Güvenlik İpuçları

1. **Yedek almayı unutma** (güncelleme öncesi)
2. **Test et** (kendi bilgisayarında çalıştır)
3. **Versiyonla** (PuantajApp_v1.2.exe gibi)
4. **Otomatik yedekleme** ayarlar sayfasında var

---

## 📝 Değişiklik Geçmişi

### v1.0 (Şubat 2026)
- İlk stabil versiyon
- Çarşaf excel export
- Dinamik hakediş hesaplama
- Otomatik yedekleme

---

## 🆘 Sorun Giderme

**Soru:** Veri kaybı olur mu?
**Cevap:** Hayır! Veriler APPDATA'da, exe'den ayrı.

**Soru:** Eski exe'yi silsem?
**Cevap:** Silebilirsin, veriler etkilenmez.

**Soru:** Yeni bilgisayara taşıma?
**Cevap:** Yedek al → puantaj.db dosyasını kopyala → yeni PC'de restore et.

---

## 📞 İletişim
Geliştirici: [İsmin]
Tarih: Şubat 2026
