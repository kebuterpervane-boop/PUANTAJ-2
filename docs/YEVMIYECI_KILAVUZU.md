# Yevmiyeci (Tersane) Sistemi - Kullanım Kılavuzu

## 🏗️ Genel Bakış

Tersanelerde kullanılan günlük ücretli (yevmiyeci) çalışan sistemi uygulamaya entegre edildi. Bu sistemde çalışanlar saatlik değil, günlük olarak ücretlendirilir.

## 📋 Temel Kurallar

### 1. **Günlük Çalışma**
- Çalışan gelirse: **1 yevmiye** alır
- Mesai süresi, geç gelme, erken çıkış önemli değil
- Sadece "geldi mi gelmedi mi" önemli

### 2. **Pazar Günü**
- **Gelmezse:** 0 yevmiye
- **Gelirse:** 1 yevmiye (maaşlıdaki gibi 15 saat değil)

### 3. **Cumartesi**
- Normal çalışma günü
- 1 yevmiye

### 4. **20:00 Mesaisi (Vardiya)**
- Maaşlıda: 4.5 saat eklerdi
- Yevmiyecide: **0.5 yevmiye** ekler
- Yani 20:00'a kadar çalışan: 1 + 0.5 = **1.5 yevmiye** alır

## 💼 Kullanım

### Personeli Yevmiyeci Olarak İşaretleme

1. **Personel** sayfasına git
2. Yeni personel eklerken veya mevcut personeli düzenlerken
3. **🔧 Yevmiyeci** checkbox'ını işaretle
4. **Maaş** alanına günlük yevmiye tutarını gir (örn: 1.000 TL)
5. Kaydet

### Bordro PDF

Yevmiyeci olarak işaretlenen personeller için bordro PDF'inde:
- Başlıklar değişir: "Normal" yerine "Yevmiye", "Mesai Saat" yerine "Mesai"
- Özet tablosu: "ÖZET (YEVMİYECİ)" başlığı
- Toplam Yevmiye ve Mesai Yevmiye gösterilir
- Günlük Yevmiye tutarı görünür

## 📊 Hesaplama Örnekleri

### Örnek 1: Normal Ay (20 Gün Çalışma)
```
Günlük Yevmiye: 1.000 TL

20 gün normal çalışma:  20 × 1.000 = 20.000 TL
3 gün 20:00 mesaisi:     3 × 0.5 × 1.000 = 1.500 TL
                        ─────────────────────────
TOPLAM:                                    21.500 TL
```

### Örnek 2: Pazar Çalışmaları
```
Günlük Yevmiye: 1.000 TL

Normal günler (15 gün):    15 × 1.000 = 15.000 TL
Pazar geldi (2 gün):        2 × 1.000 =  2.000 TL
Pazar gelmedi (2 gün):      2 × 0     =      0 TL
                           ──────────────────────
TOPLAM:                                   17.000 TL
```

### Örnek 3: Mesaili Ay
```
Günlük Yevmiye: 1.000 TL

22 gün normal:              22 × 1.000 = 22.000 TL
5 gün 20:00 mesaisi:         5 × 0.5 × 1.000 = 2.500 TL
                            ──────────────────────
TOPLAM:                                    24.500 TL
```

## 🔄 Maaşlı vs Yevmiyeci Karşılaştırması

### Aynı Gün, Aynı Saatler (08:00 - 20:00):

| Özellik | Maaşlı | Yevmiyeci |
|---------|--------|-----------|
| Normal | 7.5 saat | 1 yevmiye |
| Mesai | 4.5 saat | 0.5 yevmiye |
| **Toplam** | **12 saat** | **1.5 yevmiye** |

**Hesaplama:**
- **Maaşlı:** (12 × saatlik ücret)
- **Yevmiyeci:** (1.5 × günlük yevmiye)

## ⚙️ Teknik Detaylar

### Database
- `personel` tablosuna `yevmiyeci_mi` alanı eklendi (INTEGER, 0 veya 1)
- Mevcut veritabanları otomatik güncellenir

### Hesaplama Modülü
- `hesaplama.py`: `yevmiyeci_mi` parametresi eklendi
- Yevmiyeci için özel hesaplama mantığı
- Ceza sistemi devre dışı (geç gelme, erken çıkış etkilemez)

### Bordro Sistemi
- `page_payslip.py`: Yevmiyeci kontrolü ve hesaplama
- PDF'de yevmiyeci için özel tablo ve başlıklar

## 🧪 Test Sonuçları

Tüm testler başarıyla geçti:
```
✅ Normal Gün (Pazartesi) - 1 yevmiye
✅ 20:00 Mesaisi - 1 + 0.5 = 1.5 yevmiye  
✅ Pazar Gelmedi - 0 yevmiye
✅ Pazar Geldi - 1 yevmiye
✅ Cumartesi - 1 yevmiye (normal gün)
✅ Maaşlı vs Yevmiyeci karşılaştırma
✅ Aylık hesaplama senaryosu
```

Test çalıştırma:
```bash
python tests/test_yevmiyeci.py
```

## 📝 Notlar

### Önemli Farklar
1. **Saatlik değil günlük:** Kaç saat çalıştığı önemli değil
2. **Ceza yok:** Geç gelme, erken çıkış kesinti yapmaz
3. **Pazar özel:** Gelirse 1, gelmezse 0 (maaşlıda her durumda alıyordu)
4. **Mesai farklı:** 20:00 mesaisi maaşlıda 4.5 saat, yevmiyecide 0.5 yevmiye

### Dikkat Edilecekler
- Maaş alanına **günlük yevmiye tutarı** girilmeli
- Checkbox işaretlenmezse normal maaşlı hesaplama yapılır
- Yevmiyeci için "saat" yerine "yevmiye" birimi kullanılır

## 🔧 Kullanım Adımları

1. **Yeni Personel Ekle**
   - Ad Soyad gir
   - Maaş = Günlük yevmiye (örn: 1.000)
   - 🔧 Yevmiyeci checkbox'ı işaretle
   - Kaydet

2. **Kayıtları Yükle**
   - Normal şekilde Excel yükle
   - Yevmiyeci personeller otomatik tanınır
   - Hesaplama yevmiyeci kurallarına göre yapılır

3. **Bordro Oluştur**
   - Bordro Fişi sayfasına git
   - Personel seç
   - PDF oluştur
   - Yevmiyeci için özel format görünür

---

**Tarih:** 3 Şubat 2026  
**Versiyon:** 1.0  
**Geliştirici:** GitHub Copilot + Kullanıcı İşbirliği
