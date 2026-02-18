# Maktu Ücret Sistemi - Değişiklik Özeti

## ✅ Yapılan Değişiklikler

### 1. hesaplama.py
- `calendar` modülü import edildi
- `MAKTU_REFERANS_GUN = 30` sabiti eklendi
- `hesapla_maktu_hakedis()` fonksiyonu eklendi:
  - Ayın gerçek gün sayısını hesaplar
  - Eksik gün hesabı yapar
  - Ödemeye esas gün bulur (30 - eksik gün)
  - Günlük ücret ve hakediş hesaplar
  - Detaylı açıklama döner

### 2. page_payslip.py

#### compute_payslip() metodu:
- `hesapla_maktu_hakedis` import edildi
- Çalışılan gün sayısı hesaplanıyor
- Maktu ücret hesaplaması yapılıyor
- Return edilen dict'e yeni alanlar eklendi:
  - `maktu_hesap`: Detaylı maktu ücret bilgileri
  - `calisan_gun_sayisi`: Çalışılan gün sayısı
  - `month_days`: Ayın gerçek gün sayısı

#### create_payslip_pdf() metodu:
- PDF'e yeni bölüm eklendi: **"Maktu Ücret Hesaplama Detayı"**
- Tablo içeriği:
  - Ayın gerçek gün sayısı
  - Çalışılan gün sayısı
  - Eksik gün
  - Referans gün (30)
  - Ödemeye esas gün
  - Günlük ücret
  - Maktu hakediş
- Formül açıklaması eklendi (matematiksel gösterim)

### 3. Test Dosyası (tests/test_maktu_ucret.py)
4 farklı senaryo test ediliyor:
- Şubat 28 gün, 20 gün çalışma → 22.000 TL
- Ocak 31 gün, 25 gün çalışma → 24.000 TL
- Nisan 30 gün, tam çalışma → 30.000 TL
- Şubat artık yıl 29 gün, 20 gün çalışma → 21.000 TL

### 4. Dokümantasyon
- `MAKTU_UCRET_KILAVUZU.md`: Detaylı kullanım kılavuzu
- `MAKTU_UCRET_DEGISIKLIKLER.md`: Bu dosya

## 🎯 Kullanım Örneği

```python
from hesaplama import hesapla_maktu_hakedis

# Şubat 2026 - 28 gün, 20 gün çalışma, 30.000 TL maaş
sonuc = hesapla_maktu_hakedis(2026, 2, 20, 30000)

print(f"Eksik gün: {sonuc['eksik_gun']}")  # 8
print(f"Ödemeye esas: {sonuc['odemeye_esas_gun']}")  # 22
print(f"Hakediş: {sonuc['hakedis']:,.2f} TL")  # 22,000.00
```

## 📊 Maktu Ücret Hesaplama Formülü

$$Hakediş = \left(\frac{\text{Aylık Maaş}}{30}\right) \times (30 - \text{Eksik Gün})$$

**Adımlar:**
1. Eksik Gün = Ayın Gerçek Gün Sayısı - Çalışılan Gün
2. Ödemeye Esas Gün = 30 - Eksik Gün
3. Günlük Ücret = Aylık Maaş / 30
4. Hakediş = Günlük Ücret × Ödemeye Esas Gün

## 🔥 Önemli Avantajlar

### Şubat Ayı Senaryosu
- **Gerçek durum:** 28 gün, 20 gün çalışıldı
- **Yevmiyeli sistemde:** 20 × 1.000 = 20.000 TL alınırdı
- **Maktu sistemde:** 22 × 1.000 = **22.000 TL** alınır
- **Fark:** +2.000 TL (çalışan lehine)

### Neden?
Çünkü maktu sistemde referans her zaman 30 gündür. Şubat'ın 28 gün olması, çalışanın aleyhine sayılmaz.

## 📱 PDF Bordro Görünümü

Bordro PDF'inde yeni bölüm:

```
┌────────────────────────────────────────────────┐
│  MAKTU ÜCRET HESAPLAMA DETAYI                  │
├────────────────────────────────────────────────┤
│ Ayın Gerçek Gün Sayısı:         28 gün        │
│ Çalışılan Gün Sayısı:           20 gün        │
│ Eksik Gün:                       8 gün        │
│ Referans Gün (Sabit):           30 gün        │
│ Ödemeye Esas Gün:               22 gün        │
│ Günlük Ücret:              1,000.00 TL        │
│ Maktu Hakediş:            22,000.00 TL        │
└────────────────────────────────────────────────┘

Hesaplama Formülü:
Hakediş = (Aylık Maaş / 30) × (30 - Eksik Gün)
Hakediş = (30,000 / 30) × (30 - 8)
Hakediş = 1,000 × 22
Hakediş = 22,000.00 TL
```

## ✅ Test Sonuçları

Tüm testler başarıyla geçti:
```
✅ TEST 1: Şubat 28 gün - BAŞARILI
✅ TEST 2: Ocak 31 gün - BAŞARILI
✅ TEST 3: Nisan 30 gün tam çalışma - BAŞARILI
✅ TEST 4: Şubat artık yıl 29 gün - BAŞARILI
```

## 🚀 Nasıl Çalıştırılır?

### Test Çalıştırma:
```bash
python tests/test_maktu_ucret.py
```

### Uygulama Kullanımı:
1. Uygulamayı çalıştır
2. "Bordro Fişi" sayfasına git
3. Personel, yıl ve ay seç
4. "Tek Kişi Bordro PDF" veya "Tüm Personel Bordro PDF" butonuna tıkla
5. PDF'de "Maktu Ücret Hesaplama Detayı" bölümünü gör

## 📝 Notlar

- Sistem her ay için 30 gün referans alır
- Hesaplama tüm aylar için tutarlıdır
- Negatif ödeme olmaz (max kontrolü var)
- Artık yıl otomatik tespit edilir
- PDF'de hem tablo hem formül gösterimi var

---

**Tarih:** 3 Şubat 2026  
**Versiyon:** 1.0  
**Geliştirici:** GitHub Copilot + Kullanıcı İşbirliği
