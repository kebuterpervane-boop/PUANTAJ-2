from datetime import datetime
import pandas as pd
import calendar
import math

# --- SABİTLER ---
NORMAL_GUNLUK_SAAT = 7.5
SABAH_TOLERANS_DK = 8 * 60 + 20
AKSAM_REFERANS_DK = 17 * 60
ERKEN_CIKIS_LIMIT_DK = 16 * 60 + 30
TOLERANS_LIMITI_DK = 17 * 60 + 30
VARDIYA_LIMITI_DK = 19 * 60 + 30
CUMA_KAYIP_TOLERANS_DK = 60
ERKEN_CIKIS_CEZA_DK = 30
MAKTU_REFERANS_GUN = 30

# YEVMİYECİ SABİTLERİ (Kural: 1 Saat = 0.1333 Yevmiye)
YEVMIYE_BIRIM_KATSAYISI = 0.1333 

def parse_time_to_minutes(t_str):
    if not t_str or pd.isna(t_str) or str(t_str).strip() == "" or str(t_str) == "nan":
        return None
    t_str = str(t_str).split('.')[0]
    fmt = "%H:%M:%S" if len(str(t_str)) > 5 else "%H:%M"
    try:
        t = datetime.strptime(str(t_str), fmt)
        return t.hour * 60 + t.minute
    except (ValueError, TypeError):
        return None

def hesapla_ceza_dakika(giris_dk, cikis_dk, kayip_dk, dt_tarih, tersane_saatleri=None):
    """
    Geç gelme, erken çıkma ve gün içi kayıpları hesaplar.
    Sonuç DAKİKA cinsinden ceza süresidir.

    tersane_saatleri: dict opsiyonel - tersane bazlı saat sabitleri (dakika cinsinden)
        - sabah_tolerans_dk: En geç giriş saati (varsayılan: 500 = 08:20)
        - aksam_referans_dk: En erken çıkış saati (varsayılan: 1020 = 17:00)
        - erken_cikis_limit_dk: Erken çıkış ceza sınırı (varsayılan: 990 = 16:30)
    """
    # Tersane bazlı saatleri al, yoksa global sabitleri kullan
    sabah_tolerans = SABAH_TOLERANS_DK
    aksam_referans = AKSAM_REFERANS_DK
    erken_cikis_limit = ERKEN_CIKIS_LIMIT_DK

    cuma_kayip_tolerans = CUMA_KAYIP_TOLERANS_DK  # WHY: default to module constant if no tersane override.

    if tersane_saatleri:
        sabah_tolerans = tersane_saatleri.get('sabah_tolerans_dk', SABAH_TOLERANS_DK)
        aksam_referans = tersane_saatleri.get('aksam_referans_dk', AKSAM_REFERANS_DK)
        erken_cikis_limit = tersane_saatleri.get('erken_cikis_limit_dk', ERKEN_CIKIS_LIMIT_DK)
        cuma_kayip_tolerans = tersane_saatleri.get('cuma_kayip_tolerans_dk', CUMA_KAYIP_TOLERANS_DK)  # WHY: tersane bazlı Cuma toleransı.

    ceza_dakika = 0

    # Sabah Geç Gelme
    if giris_dk > sabah_tolerans:
        ceza_dakika += (giris_dk - sabah_tolerans)

    # Akşam Erken Çıkma
    if cikis_dk < (aksam_referans + 3):
        if cikis_dk >= erken_cikis_limit:
            ceza_dakika += ERKEN_CIKIS_CEZA_DK
        else:
            ceza_dakika += (aksam_referans - cikis_dk)

    # Gün İçi Kayıp
    if kayip_dk > 0:
        if dt_tarih.weekday() == 4:  # Cuma
            if kayip_dk > cuma_kayip_tolerans:  # WHY: tersane bazlı tolerans, fallback global sabit.
                ceza_dakika += (kayip_dk - cuma_kayip_tolerans)
        else:
            ceza_dakika += kayip_dk

    return ceza_dakika

def hesapla_mesai_tutar(cikis_dk, yevmiyeci_mi=False, mesai_baslangic_dk=None, db=None, settings_cache=None):
    """
    Mesai miktarını hesaplar.
    Yevmiyeci için: Birim YEVMİYE cinsinden döner (Örn: 0.5)
    Maaşlı için: "Mesai (Saat)" cinsinden döner (Örn: 4.5). Mesai çarpanı uygulanmaz.
    """
    if mesai_baslangic_dk is None:
        mesai_baslangic_dk = AKSAM_REFERANS_DK

    if cikis_dk <= mesai_baslangic_dk:
        return 0.0

    cikis_saat = cikis_dk / 60.0

    fazla_dk = cikis_dk - mesai_baslangic_dk
    mesai_saat = fazla_dk / 60.0
    yuvarlanmis_mesai_saat = math.ceil(mesai_saat * 2) / 2.0

    # YEVMİYECİ ÖZEL MESAİ KURALI
    if yevmiyeci_mi:
        # Kural: Çıkış saat aralığına göre sabit ek yevmiye verilir.
        # Veritabanındaki yevmiye katsayılarını kontrol et
        yevmiye_katsayilari = []
        if settings_cache and 'yevmiye_katsayilari' in settings_cache:
            yevmiye_katsayilari = settings_cache['yevmiye_katsayilari']
        elif db:
            try: yevmiye_katsayilari = db.get_yevmiye_katsayilari()
            except Exception as e:
                import logging
                logging.warning(f"Yevmiye katsayıları alınamadı: {e}")
            
        # Eğer tablo varsa oradan çek, yoksa mesai vermeyiz
        if yevmiye_katsayilari:
            for _, bas, bit, kat, _ in yevmiye_katsayilari:
                if bas <= cikis_saat < bit:
                    return kat
            return 0.0
        
        # Kural yoksa mesai/yevmiye verilmez
        return 0.0

    # MAAŞLI / STANDART MESAİ KURALI (Saat Bazlı)
    katsayilar = []
    if settings_cache and 'mesai_katsayilari' in settings_cache:
        katsayilar = settings_cache['mesai_katsayilari']
    elif db:
        try: katsayilar = db.get_mesai_katsayilari()
        except Exception as e:
            import logging
            logging.warning(f"Mesai katsayıları alınamadı: {e}")
    
    if katsayilar:
        for id, baslangic, bitis, katsayi, aciklama in katsayilar:
            if baslangic <= cikis_saat < bitis:
                return float(katsayi or 0.0)
        return 0.0
    
    # Kural yoksa mesai verilmez
    return 0.0

def _overlap_minutes(start_a, end_a, start_b, end_b):
    """Iki zaman araliginin cakisma dakikasini hesaplar."""
    start = max(start_a, start_b)
    end = min(end_a, end_b)
    return max(0, end - start)

def hesapla_hakedis(tarih_str, giris_str, cikis_str, kayip_sure_str, holiday_set,
                    holiday_info_func=None, special_status_func=None, person_name=None,
                    yevmiyeci_mi=False, db=None, settings_cache=None):
    """
    Kritik İş Kurallarına Göre Hakediş Hesaplama

    settings_cache içinde 'tersane_saatleri' anahtarı varsa, tersane bazlı saat
    sabitleri (dakika cinsinden) kullanılır. Yoksa global sabitler geçerlidir.
    """
    yevmiyeci_mi = bool(yevmiyeci_mi)
    try:
        dt_tarih = datetime.strptime(tarih_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return 0.0, 0.0, "Hatalı Tarih"

    is_pazar = (dt_tarih.weekday() == 6)
    is_cumartesi = (dt_tarih.weekday() == 5)
    ay_gun = dt_tarih.strftime("%m-%d")
    is_resmi_tatil = (tarih_str in holiday_set) or (ay_gun in holiday_set)
    
    special_status = special_status_func(person_name) if (special_status_func and person_name) else None
    special_status_norm = special_status.strip().lower() if isinstance(special_status, str) else ''

    # --- 1. GELMEDİ DURUMLARI ---
    if not giris_str and not cikis_str:
        # Özel Durumlar (Cumartesi Gelmez vb.)
        if is_cumartesi and special_status_norm == "cumartesi gelmez":
            return (1.0 if yevmiyeci_mi else NORMAL_GUNLUK_SAAT), 0.0, "Cumartesi (Özel)"
        if is_pazar and special_status_norm == "pazar gelmez":
            return (1.0 if yevmiyeci_mi else NORMAL_GUNLUK_SAAT), 0.0, "Pazar (Özel)"
        
        if is_pazar:
            # Yevmiyeci: Gelmediyse 0
            if yevmiyeci_mi:
                return 0.0, 0.0, "Pazar (Gelmedi)"
            # Maktu/Standart: Pazar ücreti ödenir (45 saat kuralı varsayılan olarak kabul edilir)
            return NORMAL_GUNLUK_SAAT, 0.0, "Pazar Tatili"
            
        elif is_resmi_tatil:
            if holiday_info_func:
                info = holiday_info_func(tarih_str)
                if info:
                    tur, normal_ref, mesai_ref = info
                    # Yevmiyeci: Gelmediyse 0
                    if yevmiyeci_mi: return 0.0, 0.0, f"{tur} (Gelmedi)"
                    # Maktu: Tatil ücreti ödenir
                    return normal_ref, mesai_ref, f"{tur} (Gelmedi)"
            return (0.0 if yevmiyeci_mi else NORMAL_GUNLUK_SAAT), 0.0, "Resmi Tatil"
        else:
            # Hafta içi gelmedi
            return 0.0, 0.0, "Gelmedi"

    # --- 2. GELDİ DURUMLARI ---
    
    # Eksik giriş tamamlama (Sadece hafta sonu/tatil için)
    if (is_pazar or is_resmi_tatil) and (giris_str or cikis_str) and (not giris_str or not cikis_str):
        if not giris_str: giris_str = "08:30"
        if not cikis_str: cikis_str = "17:00"

    giris_dk = parse_time_to_minutes(giris_str)
    cikis_dk = parse_time_to_minutes(cikis_str)
    if giris_dk is None or cikis_dk is None:
        return 0.0, 0.0, "Hatalı Saat"

    # PAZAR / TATİL MESAİSİ (Geldiyse)
    if is_pazar:
        # Yevmiyeci: 1 Yevmiye
        if yevmiyeci_mi:
            return 1.0, 0.0, "Pazar (Çalıştı)"
        # Maktu: Normal + Mesai (settings'den oku, default 15.0)
        pazar_mesai = 15.0
        if settings_cache:
            try: pazar_mesai = float(settings_cache.get("pazar_mesaisi", 15.0))
            except (ValueError, TypeError): pass
        elif db:
            try: pazar_mesai = float(db.get_setting("pazar_mesaisi", 15.0))
            except (ValueError, TypeError): pass
        return NORMAL_GUNLUK_SAAT, pazar_mesai, "Pazar Mesaisi"
    
    if is_resmi_tatil:
        if holiday_info_func:
            info = holiday_info_func(tarih_str)
            if info: return info[1], info[2], f"{info[0]} (Çalıştı)"
        return NORMAL_GUNLUK_SAAT, 7.5, "Resmi Tatil Mesaisi"

    # --- NORMAL GÜN HESAPLAMASI ---
    
    # Kayıp Süre Parse
    kayip_dk = 0
    if kayip_sure_str:
        try:
            parts = str(kayip_sure_str).split(':')
            kayip_dk = int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError): pass

    # Ceza Dakikası Hesapla (tersane bazlı saatlerle)
    tersane_saatleri = settings_cache.get('tersane_saatleri') if settings_cache else None
    ceza_dakika = hesapla_ceza_dakika(giris_dk, cikis_dk, kayip_dk, dt_tarih, tersane_saatleri)

    # Ayarları Al - Tersane bazlı mesai başlangıcı varsa onu kullan
    if tersane_saatleri and 'tolerans_limiti_dk' in tersane_saatleri:
        mesai_baslangic_dk = tersane_saatleri['tolerans_limiti_dk']
    else:
        mesai_baslangic_saat = "17:30"
        if settings_cache:
            mesai_baslangic_saat = settings_cache.get("mesai_baslangic_saat", "17:30")
        elif db:
            mesai_baslangic_saat = db.get_setting("mesai_baslangic_saat", "17:30")
        try:
            parts = mesai_baslangic_saat.split(":")
            mesai_baslangic_dk = int(parts[0]) * 60 + int(parts[1])
        except (ValueError, AttributeError):
            mesai_baslangic_dk = AKSAM_REFERANS_DK

    # --- KRİTİK AYRIM: YEVMİYECİ vs MAKTU ---

    if yevmiyeci_mi:
        # KURAL: Günlük Yevmiye = 1 - (Eksik Saat * 0.1333)
        # Eksik Saat = Ceza Dakikası / 60
        eksik_saat = ceza_dakika / 60.0
        # 1 Tam Yevmiye'den düşülür
        gunluk_yevmiye_hakki = 1.0 - (eksik_saat * YEVMIYE_BIRIM_KATSAYISI)
        
        # Negatife düşemez
        normal_return = max(0.0, gunluk_yevmiye_hakki)
        
        # Mesai Hesabı (+0.5 Yevmiye vb.)
        mesai_return = hesapla_mesai_tutar(cikis_dk, True, mesai_baslangic_dk, db, settings_cache)
        
        return round(normal_return, 4), round(mesai_return, 2), ""
        
    else:
        calisma_modu = "cezadan_dus"
        ogle_baslangic = "12:15"
        ogle_bitis = "13:15"
        ara_mola_dk = 20
        yuvarlama_modu = "ondalik"
        if settings_cache:
            calisma_modu = str(settings_cache.get("calisma_hesaplama_modu", calisma_modu) or calisma_modu).strip().lower()
            ogle_baslangic = str(settings_cache.get("ogle_molasi_baslangic", ogle_baslangic) or ogle_baslangic).strip()
            ogle_bitis = str(settings_cache.get("ogle_molasi_bitis", ogle_bitis) or ogle_bitis).strip()
            yuvarlama_modu = str(settings_cache.get("fiili_saat_yuvarlama", yuvarlama_modu) or yuvarlama_modu).strip().lower()
            try:
                ara_mola_dk = max(0, int(float(settings_cache.get("ara_mola_dk", ara_mola_dk))))
            except (ValueError, TypeError):
                ara_mola_dk = 20

        if calisma_modu == "fiili_calisma":
            fiili_dk = max(0, cikis_dk - giris_dk)
            ogle_bas_dk = parse_time_to_minutes(ogle_baslangic)
            ogle_bit_dk = parse_time_to_minutes(ogle_bitis)
            if ogle_bas_dk is not None and ogle_bit_dk is not None and ogle_bit_dk > ogle_bas_dk:
                fiili_dk -= _overlap_minutes(giris_dk, cikis_dk, ogle_bas_dk, ogle_bit_dk)
            # Cuma toleransı: Cuma günü tolerans sınırı altındaki kayıplar fiili süreden düşülmez.
            effective_kayip_dk = kayip_dk
            if dt_tarih.weekday() == 4:  # Cuma
                cuma_tol = tersane_saatleri.get('cuma_kayip_tolerans_dk', CUMA_KAYIP_TOLERANS_DK) if tersane_saatleri else CUMA_KAYIP_TOLERANS_DK
                effective_kayip_dk = max(0, kayip_dk - cuma_tol)
            fiili_dk -= max(0, effective_kayip_dk)
            if ara_mola_dk > 0 and fiili_dk > 0:
                fiili_dk = max(0, fiili_dk - ara_mola_dk)

            normal_return = min(NORMAL_GUNLUK_SAAT, fiili_dk / 60.0)
            if yuvarlama_modu == "tam_saat":
                normal_return = float(math.ceil(normal_return))
            elif yuvarlama_modu == "yarim_saat":
                normal_return = math.ceil(normal_return * 2) / 2.0

            mesai_return = hesapla_mesai_tutar(cikis_dk, False, mesai_baslangic_dk, db, settings_cache)
            return round(max(0.0, normal_return), 2), round(mesai_return, 2), "Fiili Calisma"

        # MAKTU / STANDART
        # KURAL: "Saatten düşülmez - Ceza puanı olarak düşülür"
        # Bu nedenle veritabanına 7.5 (Tam Gün) olarak kaydediyoruz ki gün sayımı bozulmasın.
        # Ceza puanı takibi gerekirse açıklama veya ayrı sütun kullanılmalı, ancak şu an
        # 30 gün kuralı gereği gün sayısının tam olması esastır.
        
        normal_return = max(0.0, NORMAL_GUNLUK_SAAT - (ceza_dakika / 60.0))
        
        # NOT: Eğer cezanın maaştan kesilmesi isteniyorsa bu ayrıca "Ceza Kesintisi" olarak işlenmelidir.
        # Ancak "Saatten düşülmez" kuralı gereği burası 7.5 kalmalıdır.
        
        # Mesai Hesabı (Saat bazlı)
        mesai_return = hesapla_mesai_tutar(cikis_dk, False, mesai_baslangic_dk, db, settings_cache)
        
        aciklama = ""
        if ceza_dakika > 0:
            aciklama = f"Gecikme/Ceza: {ceza_dakika} dk"

        return round(normal_return, 2), round(mesai_return, 2), aciklama

def hesapla_maktu_hakedis(year, month, calisan_gun_sayisi, aylik_maas):
    """
    Kritik Kural:
    - Ay kaç gün çekerse çeksin Referans 30'dur.
    - Hakediş = (Maaş / 30) * (30 - Eksik Gün)
    """
    ayin_gercek_gun_sayisi = calendar.monthrange(year, month)[1]
    
    # Eksik Gün Hesabı: Gerçek Ay Gün Sayısı - Çalışılan Gün
    # Örn: Şubat 28 çeker, 20 gün çalıştı -> 8 gün eksik.
    eksik_gun = ayin_gercek_gun_sayisi - calisan_gun_sayisi
    
    # 30 Gün Kuralı Uygulaması
    # Hakedişe Esas Gün = 30 - Eksik Gün
    # Örn: 30 - 8 = 22 Gün ödenir.
    odemeye_esas_gun = max(0, MAKTU_REFERANS_GUN - eksik_gun)
    
    gunluk_ucret = aylik_maas / MAKTU_REFERANS_GUN if aylik_maas > 0 else 0
    hakedis = gunluk_ucret * odemeye_esas_gun
    
    return {
        'ayin_gercek_gun_sayisi': ayin_gercek_gun_sayisi,
        'calisan_gun': calisan_gun_sayisi,
        'eksik_gun': eksik_gun,
        'odemeye_esas_gun': odemeye_esas_gun,
        'gunluk_ucret': round(gunluk_ucret, 2),
        'hakedis': round(hakedis, 2),
        'aciklama': ""
    }
