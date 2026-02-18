# Yevmiyeci Sistemi - Değişiklik Özeti

## ✅ Yapılan Tüm Değişiklikler

### 1. **database.py**

#### Veritabanı Şeması
```python
# personel tablosuna yeni alan
ALTER TABLE personel ADD COLUMN yevmiyeci_mi INTEGER DEFAULT 0
```

#### Değiştirilen Fonksiyonlar
- `init_db()`: yevmiyeci_mi alanı migration'ı eklendi
- `update_personnel()`: yevmiyeci_mi parametresi eklendi
- `get_all_personnel_detailed()`: SELECT sorgusuna yevmiyeci_mi eklendi
- `update_records_for_person()`: Personelin yevmiyeci durumunu alıp hesapla_hakedis'e geçiriyor

### 2. **hesaplama.py**

#### Yeni Parametre
- `hesapla_hakedis()`: `yevmiyeci_mi` parametresi eklendi
- `hesapla_mesai()`: `yevmiyeci_mi` parametresi eklendi

#### Hesaplama Mantığı

**Pazar Günü - Gelmedi:**
```python
if yevmiyeci_mi and is_pazar:
    return 0.0, 0.0, "Pazar (Yevmiyeci - Gelmedi)"
```

**Pazar Günü - Geldi:**
```python
if is_pazar:
    if yevmiyeci_mi:
        return 1.0, 0.0, "Pazar (Yevmiyeci - Geldi)"
    else:
        return NORMAL_GUNLUK_SAAT, 15.0, "Pazar Mesaisi (Fiks)"
```

**Normal Günler:**
```python
if yevmiyeci_mi:
    normal_saat = 1.0  # Günlük 1 yevmiye
    ceza_dakika = 0    # Ceza sistemi devre dışı
else:
    normal_saat = NORMAL_GUNLUK_SAAT - (ceza_dakika / 60.0)
```

**20:00 Mesaisi:**
```python
if cikis_dk >= VARDIYA_LIMITI_DK:
    return 0.5 if yevmiyeci_mi else 4.5
```

### 3. **page_personnel.py**

#### UI Değişiklikleri
- Yeni checkbox eklendi: `self.chk_yevmiyeci`
- Tablo sütunu eklendi: "Yevmiyeci" (11. sütun)
- Bilgi kutusuna açıklama eklendi

#### Veri Yönetimi
- `add_personnel()`: yevmiyeci_mi durumu kaydediliyor
- `load_data()`: Tabloda ✓ işareti gösteriliyor
- `save_changes()`: Yevmiyeci durumu güncelleniyor

### 4. **page_payslip.py**

#### Bordro Hesaplama
```python
if yevmiyeci_mi:
    total_normal = sum(r[3] for r in records_sorted)  # Yevmiye sayısı
    total_mesai = sum(r[4] for r in records_sorted)   # Mesai yevmiye
    gunluk_yevmiye = maas
    brut = (total_normal * gunluk_yevmiye) + (total_mesai * gunluk_yevmiye)
```

#### PDF Çıktısı
- Başlık: "Çalışma Detayları: (YEVMİYECİ)"
- Sütun: "Normal" → "Yevmiye"
- Özet: "ÖZET (YEVMİYECİ)"
- Gösterim: "Toplam Yevmiye", "Mesai Yevmiye", "Günlük Yevmiye"

### 5. **Testler (tests/test_yevmiyeci.py)**

7 test senaryosu:
1. Normal gün - 1 yevmiye ✅
2. 20:00 mesaisi - 1.5 yevmiye ✅
3. Pazar gelmedi - 0 yevmiye ✅
4. Pazar geldi - 1 yevmiye ✅
5. Cumartesi - 1 yevmiye ✅
6. Maaşlı vs Yevmiyeci karşılaştırma ✅
7. Aylık hesaplama senaryosu ✅

### 6. **Dokümantasyon**
- `YEVMIYECI_KILAVUZU.md`: Detaylı kullanım kılavuzu
- `YEVMIYECI_DEGISIKLIKLER.md`: Bu dosya

## 📊 Hesaplama Karşılaştırması

### Maaşlı Sistem
| Durum | Normal | Mesai | Toplam |
|-------|--------|-------|--------|
| Normal gün (08:00-17:00) | 7.5 saat | 0 | 7.5 saat |
| 20:00 mesaisi | 7.5 saat | 4.5 saat | 12 saat |
| Pazar gelmedi | 7.5 saat | 0 | 7.5 saat |
| Pazar geldi | 7.5 saat | 15 saat | 22.5 saat |

### Yevmiyeci Sistem
| Durum | Normal | Mesai | Toplam |
|-------|--------|-------|--------|
| Normal gün (08:00-17:00) | 1 yevmiye | 0 | 1 yevmiye |
| 20:00 mesaisi | 1 yevmiye | 0.5 yevmiye | 1.5 yevmiye |
| Pazar gelmedi | 0 | 0 | 0 |
| Pazar geldi | 1 yevmiye | 0 | 1 yevmiye |

## 🎯 Kullanım Senaryosu

### Adım 1: Yevmiyeci Personel Ekle
```
Ad Soyad: Ahmet Yılmaz
Maaş: 1000  (günlük yevmiye)
Ekip: Kaynak
☑ Yevmiyeci
```

### Adım 2: Kayıt Yükle
- Excel dosyasını normal şekilde yükle
- Sistem otomatik olarak yevmiyeci hesaplama yapar

### Adım 3: Bordro Oluştur
```
Toplam Yevmiye: 21.5
Mesai Yevmiye: 1.5
Günlük Yevmiye: 1,000.00 TL
Brut Hakediş: 23,000.00 TL
```

## 🔍 Kritik Farklar

### 1. Maaş Alanı
- **Maaşlı:** Aylık maaş (örn: 30.000 TL/ay)
- **Yevmiyeci:** Günlük yevmiye (örn: 1.000 TL/gün)

### 2. Hesaplama Birimi
- **Maaşlı:** Saat
- **Yevmiyeci:** Gün (yevmiye)

### 3. Ceza Sistemi
- **Maaşlı:** Geç gelme, erken çıkış kesinti yapar
- **Yevmiyeci:** Ceza yok, sadece geldi/gelmedi

### 4. Pazar Günü
- **Maaşlı:** Gelsin/gelmesin 7.5 saat + mesai alır
- **Yevmiyeci:** Gelmezse 0, gelirse 1 yevmiye

### 5. Mesai
- **Maaşlı:** 20:00 mesaisi → 4.5 saat
- **Yevmiyeci:** 20:00 mesaisi → 0.5 yevmiye

## 📝 Veritabanı Migration

Mevcut veritabanları otomatik güncellenir:
```sql
-- İlk açılışta otomatik çalışır
ALTER TABLE personel ADD COLUMN yevmiyeci_mi INTEGER DEFAULT 0
```

Tüm mevcut personeller varsayılan olarak `yevmiyeci_mi = 0` (maaşlı) olur.

## ✅ Test Sonuçları

```bash
python tests/test_yevmiyeci.py
```

**Çıktı:**
```
============================================================
TEST 1: NORMAL GÜN (Pazartesi) - YEVMİYECİ
Normal: 1.0 yevmiye
Mesai: 0.0 yevmiye
✅ TEST 1 BAŞARILI

TEST 2: 20:00 MESAİSİ - YEVMİYECİ
Normal: 1.0 yevmiye
Mesai: 0.5 yevmiye
Toplam: 1.5 yevmiye (1 + 0.5)
✅ TEST 2 BAŞARILI

TEST 3: PAZAR GELMEDİ - YEVMİYECİ
Normal: 0.0 yevmiye
✅ TEST 3 BAŞARILI

TEST 4: PAZAR GELDİ - YEVMİYECİ
Normal: 1.0 yevmiye
✅ TEST 4 BAŞARILI

TEST 5: CUMARTESİ - YEVMİYECİ
Normal: 1.0 yevmiye
✅ TEST 5 BAŞARILI

🎉 TÜM YEVMİYECİ TESTLERİ BAŞARIYLA TAMAMLANDI!
```

## 🚀 Deployment

1. Uygulamayı çalıştır
2. Veritabanı otomatik güncellenir
3. Personel sayfasında checkbox görünür
4. Hemen kullanıma hazır

## 🔧 Geriye Dönük Uyumluluk

- Mevcut maaşlı personeller etkilenmez
- Tüm eski kayıtlar aynı şekilde çalışır
- Sadece yevmiyeci işaretli personeller yeni sistemi kullanır
- İki sistem birlikte sorunsuz çalışır

---

**Tarih:** 3 Şubat 2026  
**Versiyon:** 1.0  
**Toplam Değişiklik:** 5 dosya, 7 test senaryosu  
**Geliştirici:** GitHub Copilot + Kullanıcı İşbirliği
