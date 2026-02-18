# Maktu Ücret Sistemi - Kullanım Kılavuzu

## 🎯 Özet

Maktu ücret sisteminde hesaplama, ayın takvimde kaç gün olduğuna bakmaksızın **30 gün referans** alınarak yapılır.

## 📐 Hesaplama Mantığı

### Temel Prensipler:
- **Sabit Referans:** 30 gün
- **Eksik Gün Sayısı:** Ayın gerçek gün sayısı - Çalışılan gün
- **Ödemeye Esas Gün:** 30 - Eksik Gün

### Matematiksel Formül:

$$Hakediş = \left(\frac{\text{Aylık Maaş}}{30}\right) \times (30 - \text{Eksik Gün})$$

## 💼 Örnek Senaryolar

### Senaryo 1: Şubat Ayı (28 gün)
- Maaş: 30.000 TL
- Şubat ayı gerçek gün sayısı: 28 gün
- Çalışılan gün: 20 gün
- Eksik gün: 28 - 20 = **8 gün**
- Ödemeye esas gün: 30 - 8 = **22 gün**

**Hesaplama:**
```
Günlük Yevmiye = 30.000 / 30 = 1.000 TL
Hakediş = 1.000 × 22 = 22.000 TL
```

### Senaryo 2: Ocak Ayı (31 gün)
- Maaş: 30.000 TL
- Ocak ayı gerçek gün sayısı: 31 gün
- Çalışılan gün: 25 gün
- Eksik gün: 31 - 25 = **6 gün**
- Ödemeye esas gün: 30 - 6 = **24 gün**

**Hesaplama:**
```
Günlük Yevmiye = 30.000 / 30 = 1.000 TL
Hakediş = 1.000 × 24 = 24.000 TL
```

### Senaryo 3: Tam Çalışma (Nisan, 30 gün)
- Maaş: 30.000 TL
- Nisan ayı gerçek gün sayısı: 30 gün
- Çalışılan gün: 30 gün
- Eksik gün: 30 - 30 = **0 gün**
- Ödemeye esas gün: 30 - 0 = **30 gün**

**Hesaplama:**
```
Günlük Yevmiye = 30.000 / 30 = 1.000 TL
Hakediş = 1.000 × 30 = 30.000 TL (TAM MAAŞ)
```

## 🔍 Kritik Noktalar

### Maktu Ücret vs Yevmiyeli Ücret

| Durum | Maktu Ücret | Yevmiyeli |
|-------|-------------|-----------|
| Şubat 28 gün, 20 gün çalışma | 22.000 TL | 20.000 TL |
| Ocak 31 gün, 25 gün çalışma | 24.000 TL | 25.000 TL |

**Maktu ücret avantajı:** Şubat gibi kısa aylarda, çalışılan günden daha fazla ödeme yapılır çünkü 30 gün referans alınır.

## 💻 Kod Kullanımı

```python
from hesaplama import hesapla_maktu_hakedis

# Şubat 2026, 20 gün çalışma, 30.000 TL maaş
sonuc = hesapla_maktu_hakedis(
    year=2026,
    month=2,
    calisan_gun_sayisi=20,
    aylik_maas=30000
)

print(f"Hakediş: {sonuc['hakedis']:,.2f} TL")
print(f"Açıklama: {sonuc['aciklama']}")
```

**Çıktı:**
```
Hakediş: 22,000.00 TL
Açıklama: Şubat ayı 28 gün olmasına rağmen, 20 gün çalıştınız. 
Maktu ücret sisteminde 30 gün referans alınır. Eksik gününüz: 8 gün. 
Ödemeye esas: 22 gün. Hakediş: 22,000.00 TL
```

## 📄 Bordro PDF'de Görünüm

Bordro PDF'de artık **"Maktu Ücret Hesaplama Detayı"** bölümü yer alacak:

| Açıklama | Değer |
|----------|-------|
| Ayın Gerçek Gün Sayısı | 28 gün |
| Çalışılan Gün Sayısı | 20 gün |
| Eksik Gün | 8 gün |
| Referans Gün (Sabit) | 30 gün |
| Ödemeye Esas Gün | 22 gün |
| Günlük Ücret | 1,000.00 TL |
| **Maktu Hakediş** | **22,000.00 TL** |

## 🧪 Test Çalıştırma

```bash
python tests/test_maktu_ucret.py
```

Tüm test senaryoları otomatik olarak doğrulanır.

## ⚙️ Uygulama Entegrasyonu

Maktu ücret hesaplaması şu modüllere entegre edildi:

1. **hesaplama.py**: `hesapla_maktu_hakedis()` fonksiyonu
2. **page_payslip.py**: Bordro hesaplama ve PDF oluşturma
3. **PDF Bordro**: Detaylı maktu ücret tablosu ve formül açıklaması

## 📝 Notlar

- Her ay için referans **her zaman 30 gün**dır
- Artık yıllarda Şubat 29 gün olsa bile, hesaplama 30 gün üzerinden yapılır
- Negatif ödeme olmaz: `max(0, 30 - eksik_gun)` kontrolü vardır
- Formül her zaman tutarlıdır, ay fark etmez

---

**Son Güncelleme:** Şubat 2026  
**Geliştirici:** Puantaj App Team
