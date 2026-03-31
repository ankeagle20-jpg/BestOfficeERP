# -*- coding: utf-8 -*-
"""
BestOfficeERP – GİB e-Arşiv Fatura Modülü

Fatura verilerini veritabanından çeker, GİB'e taslak oluşturur ve SMS onay sürecini yönetir.
.env içinde GIB_USER ve GIB_PASS tanımlı olmalı.

Kütüphane: `from fatura import Client` kullanılır.
Alternatif: pip install eArsivPortal ile farklı API kullanılabilir (adaptör gerekir).
"""

import os
import time
from dotenv import load_dotenv

load_dotenv()

# GİB kütüphanesi opsiyonel; yoksa alternatif eArsivPortal denenir
try:
    from fatura import Client as FaturaClient
    _HAS_FATURA_LIB = True
except ImportError:
    FaturaClient = None
    _HAS_FATURA_LIB = False

try:
    from eArsivPortal import eArsivPortal as EArsivPortalClient
    _HAS_EARSIV_PORTAL = True
except ImportError:
    EArsivPortalClient = None
    _HAS_EARSIV_PORTAL = False


# Hizmet adı eşlemesi (GİB'de görünecek)
HIZMET_ADI_MAP = {
    "": "Sanal Ofis Hizmet Bedeli",
    "sanal ofis": "Sanal Ofis Hizmet Bedeli",
    "hazır ofis": "Hazır Ofis Hizmet Bedeli",
    "hazir ofis": "Hazır Ofis Hizmet Bedeli",
    "toplantı odası": "Toplantı Odası Kullanımı",
    "toplanti odasi": "Toplantı Odası Kullanımı",
    "danışmanlık": "Danışmanlık Hizmeti",
    "danismanlik": "Danışmanlık Hizmeti",
}


def _hizmet_adi_gib(hizmet_turu):
    """Hizmet türüne göre GİB'de kullanılacak standart ad."""
    if not hizmet_turu or not str(hizmet_turu).strip():
        return "Sanal Ofis Hizmet Bedeli"
    key = str(hizmet_turu).strip().lower().replace("ı", "i").replace("ş", "s").replace("ö", "o").replace("ü", "u").replace("ç", "c").replace("ğ", "g")
    return HIZMET_ADI_MAP.get(key, (hizmet_turu or "Sanal Ofis Hizmet Bedeli").strip())


def _retry_on_connection(max_attempts=3, delay=2.0):
    """Bağlantı / timeout hatalarında tekrar dene."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    err_msg = str(e).lower()
                    if "geçersiz kullanıcı" in err_msg or "invalid user" in err_msg or "yetkisiz" in err_msg:
                        raise
                    if attempt < max_attempts - 1 and (
                        "connection" in err_msg or "timeout" in err_msg or "bağlantı" in err_msg
                    ):
                        time.sleep(delay)
                        continue
                    raise
            raise last_exc
        return wrapper
    return decorator


class BestOfficeGIBManager:
    """GİB e-Arşiv fatura taslağı oluşturma ve SMS onayı."""

    def __init__(self, test_mode=None):
        self.username = os.getenv("GIB_USER", "").strip()
        self.password = os.getenv("GIB_PASS", "").strip()
        self.test_mode = test_mode if test_mode is not None else (os.getenv("GIB_TEST", "0").strip().lower() in ("1", "true", "evet"))
        self.client = None
        self.client_type = None
        self.last_sms_error = None
        self.init_error = None
        try:
            if _HAS_FATURA_LIB and self.username and self.password:
                self.client = FaturaClient(self.username, self.password, test_mode=self.test_mode)
                self.client_type = "fatura"
            elif _HAS_EARSIV_PORTAL and self.username and self.password:
                # eArsivPortal test_modu=True iken test ortamını kullanır.
                self.client = EArsivPortalClient(self.username, self.password, test_modu=bool(self.test_mode))
                self.client_type = "earsivportal"
        except Exception as e:
            self.init_error = str(e)
            self.client = None
            self.client_type = None

    def is_available(self):
        """GİB kütüphanesi ve kimlik bilgileri hazır mı."""
        return self.client is not None

    def _ensure_client(self):
        if not self.client:
            if self.init_error:
                raise RuntimeError(f"GİB istemcisi başlatılamadı: {self.init_error}")
            if (not _HAS_FATURA_LIB) and (not _HAS_EARSIV_PORTAL):
                raise RuntimeError(
                    "GİB e-Arşiv için desteklenen kütüphaneler yüklü değil. "
                    "Örn: pip install eArsivPortal"
                )
            if not self.username or not self.password:
                raise ValueError("GIB_USER ve GIB_PASS .env dosyasında tanımlı olmalı.")

    @_retry_on_connection(max_attempts=3, delay=2.0)
    def fatura_taslak_olustur(self, fatura_data):
        """
        ERP'den gelen verilerle GİB üzerinde taslak fatura oluşturur.
        fatura_data: tarih (GG/AA/YYYY), saat (SS:DD veya SS:DD:SS), vkn, ad, soyad, unvan, vd,
                    hizmet_adi, birim_fiyat, items (liste; her biri name, quantity, unit_price, tax_rate),
                    iban (opsiyonel), note (opsiyonel).
        Returns: ETTN/UUID veya None (hata).
        """
        self._ensure_client()
        try:
            if self.client_type == "fatura":
                self.client.login()
            elif self.client_type == "earsivportal":
                self.client.giris_yap()
        except Exception as e:
            err = str(e).lower()
            if "geçersiz kullanıcı" in err or "invalid" in err or "yetkisiz" in err or "kullanıcı adı" in err:
                raise RuntimeError("GİB geçersiz kullanıcı veya şifre. Lütfen GIB_USER ve GIB_PASS bilgilerinizi kontrol edin.") from e
            raise

        items = fatura_data.get("items") or []
        if not items and fatura_data.get("hizmet_adi") is not None:
            items = [{
                "name": fatura_data["hizmet_adi"],
                "quantity": 1,
                "unit_price": float(fatura_data.get("birim_fiyat") or fatura_data.get("toplam") or 0),
                "tax_rate": int(fatura_data.get("kdv_orani") or 20),
            }]
        if not items:
            raise ValueError("Fatura en az bir satır (items veya hizmet_adi+birim_fiyat) içermelidir.")

        note = fatura_data.get("note") or ""
        if fatura_data.get("iban"):
            note = f"IBAN: {fatura_data['iban']} - BestOfficeERP"
        if self.client_type == "fatura":
            invoice = {
                "date": fatura_data.get("tarih") or "",
                "time": fatura_data.get("saat") or "00:00:00",
                "tax_number": str(fatura_data.get("vkn") or "").strip(),
                "first_name": (fatura_data.get("ad") or "").strip(),
                "last_name": (fatura_data.get("soyad") or "").strip(),
                "title": (fatura_data.get("unvan") or "").strip(),
                "tax_office": (fatura_data.get("vd") or "").strip(),
                "items": items,
                "note": note[:500] if note else "BestOfficeERP",
            }
            result = self.client.create_draft(invoice)
            if result and isinstance(result, dict):
                return result.get("uuid") or result.get("ettn")
            return result

        if self.client_type == "earsivportal":
            # eArsivPortal tek satır API sunuyor; ilk satırdan temel alanları kullanıyoruz.
            first = items[0] if items else {}
            urun_adi = str(first.get("name") or fatura_data.get("hizmet_adi") or "Hizmet")
            fiyat = float(first.get("unit_price") or fatura_data.get("birim_fiyat") or fatura_data.get("toplam") or 0)
            kdv_orani = int(first.get("tax_rate") or fatura_data.get("kdv_orani") or 20)
            payload = {
                "tarih": fatura_data.get("tarih") or "",
                "saat": fatura_data.get("saat") or "12:00:00",
                "vkn_veya_tckn": str(fatura_data.get("vkn") or "").strip(),
                "ad": (fatura_data.get("ad") or "").strip(),
                "soyad": (fatura_data.get("soyad") or "").strip(),
                "unvan": (fatura_data.get("unvan") or "").strip(),
                "vergi_dairesi": (fatura_data.get("vd") or "").strip(),
                "urun_adi": urun_adi,
                "fiyat": fiyat,
                "fatura_notu": (note[:500] if note else "BestOfficeERP"),
            }
            # Bazı eArsivPortal sürümleri kdv_orani alır, bazıları almaz.
            try:
                out = self.client.fatura_olustur(**payload, kdv_orani=kdv_orani)
            except TypeError:
                out = self.client.fatura_olustur(**payload)
            if hasattr(out, "dict"):
                d = out.dict()
                return d.get("ettn") or d.get("uuid")
            if isinstance(out, dict):
                return out.get("ettn") or out.get("uuid")
            return out

        raise RuntimeError("Desteklenen GİB istemcisi bulunamadı.")

    def sms_onay_ve_imzala(self, uuid, sms_kodu):
        """
        Telefona gelen SMS kodu ile taslak faturayı onaylar.
        Returns: True başarılı, False hata.
        """
        self._ensure_client()
        try:
            if self.client_type == "fatura":
                confirm = self.client.confirm_with_sms(uuid, sms_kodu)
                return bool(confirm)

            if self.client_type == "earsivportal":
                self.client.giris_yap()
                # Önce SMS gönderimi başlat ve oid al.
                imza = self.client.gib_imza()
                oid = None
                if hasattr(imza, "dict"):
                    oid = (imza.dict() or {}).get("oid")
                elif isinstance(imza, dict):
                    oid = imza.get("oid")
                if not oid:
                    return False
                return self.sms_onay_earsivportal(uuid, sms_kodu, oid)

            return False
        except Exception as e:
            print(f"SMS Onay Hatası: {e}")
            return False

    def sms_onay_earsivportal(self, uuid, sms_kodu, oid):
        """eArsivPortal için verilen oid ile SMS kodunu onaylar."""
        self._ensure_client()
        if self.client_type != "earsivportal":
            return False
        try:
            self.client.giris_yap()
            from datetime import datetime, timedelta
            bugun = datetime.now().date()
            bas = (bugun - timedelta(days=31)).strftime("%d/%m/%Y")
            bit = (bugun + timedelta(days=1)).strftime("%d/%m/%Y")
            drafts = self.client.faturalari_getir(baslangic_tarihi=bas, bitis_tarihi=bit) or []
            hedef = None
            for f in drafts:
                d = f.dict() if hasattr(f, "dict") else (f if isinstance(f, dict) else {})
                if str(d.get("ettn") or d.get("uuid") or "").strip() == str(uuid).strip():
                    hedef = d
                    break
            if not hedef:
                return False
            res = self.client.gib_sms_onay(hedef, oid, sms_kodu)
            msg = ""
            if hasattr(res, "dict"):
                msg = str((res.dict() or {}).get("mesaj") or "")
            elif isinstance(res, dict):
                msg = str(res.get("mesaj") or "")
            return ("başar" in msg.lower()) or ("onay" in msg.lower()) or (msg.strip() == "")
        except Exception as e:
            print(f"SMS OID Onay Hatası: {e}")
            return False

    def sms_kodu_gonder(self, uuid):
        """eArsivPortal için SMS gönderimini başlatır ve oid döndürür."""
        self._ensure_client()
        if self.client_type != "earsivportal":
            return None
        self.last_sms_error = None
        for _ in range(3):
            try:
                self.client.giris_yap()
                imza = self.client.gib_imza()
                d = imza.dict() if hasattr(imza, "dict") else (imza if isinstance(imza, dict) else {})
                oid = d.get("oid")
                if oid:
                    return oid
                self.last_sms_error = "OID alınamadı (telefon kayıtlı olmayabilir veya GİB SMS servisi yanıt vermedi)."
            except Exception as e:
                self.last_sms_error = str(e)
                print(f"SMS Gönderim Hatası: {e}")
                time.sleep(1.2)
        return None

    def fatura_durum_getir(self, uuid, days_back=370):
        """UUID ile GİB'deki fatura/onay durumunu bulur (eArsivPortal)."""
        self._ensure_client()
        if self.client_type != "earsivportal":
            return None
        try:
            from datetime import datetime, timedelta
            self.client.giris_yap()
            bugun = datetime.now().date()
            bas = (bugun - timedelta(days=max(7, int(days_back or 370)))).strftime("%d/%m/%Y")
            bit = (bugun + timedelta(days=1)).strftime("%d/%m/%Y")
            rows = self.client.faturalari_getir(baslangic_tarihi=bas, bitis_tarihi=bit) or []
            for r in rows:
                d = r.dict() if hasattr(r, "dict") else (r if isinstance(r, dict) else {})
                if str(d.get("ettn") or d.get("uuid") or "").strip().lower() == str(uuid or "").strip().lower():
                    return d
        except Exception as e:
            print(f"Fatura durum getir hatası: {e}")
        return None


def build_fatura_data_from_db(fatura_id, fetch_one_func):
    """
    Veritabanından fatura + müşteri + KYC çekip GİB fatura_data sözlüğüne dönüştürür.
    fetch_one_func: (sql, params) -> dict kullanacak fonksiyon (örn. db.fetch_one).
    """
    fatura = fetch_one_func(
        """
        SELECT f.id, f.fatura_no, f.fatura_tarihi, f.toplam, f.tutar, f.kdv_tutar, f.musteri_id, f.musteri_adi, f.notlar
        FROM faturalar f WHERE f.id = %s
        """,
        (fatura_id,),
    )
    if not fatura:
        raise ValueError("Fatura bulunamadı.")

    musteri_id = fatura.get("musteri_id")
    cust = None
    kyc = None
    if musteri_id:
        cust = fetch_one_func(
            "SELECT id, name, address, tax_number FROM customers WHERE id = %s",
            (musteri_id,),
        )
        kyc = fetch_one_func(
            "SELECT vergi_dairesi, vergi_no, hizmet_turu, aylik_kira, yeni_adres FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
            (musteri_id,),
        )

    # Tarih GG/AA/YYYY, saat SS:DD veya SS:DD:SS
    ft = fatura.get("fatura_tarihi")
    if hasattr(ft, "strftime"):
        tarih_str = ft.strftime("%d/%m/%Y")
    else:
        s = str(ft or "")[:10]
        if s and len(s) == 10 and s[4] == "-":
            y, a, g = s.split("-")[0], s.split("-")[1], s.split("-")[2]
            tarih_str = f"{g}/{a}/{y}"
        else:
            tarih_str = "01/01/2025"
    saat_str = "12:00:00"

    vkn = (kyc and kyc.get("vergi_no")) or (cust and cust.get("tax_number")) or ""
    if vkn is None:
        vkn = ""
    vkn = str(vkn).strip().replace(" ", "")
    vd = (kyc and kyc.get("vergi_dairesi")) or (cust and cust.get("vergi_dairesi")) or ""
    if not vd and musteri_id:
        try:
            vd_row = fetch_one_func("SELECT vergi_dairesi FROM customers WHERE id = %s", (musteri_id,))
            if vd_row and vd_row.get("vergi_dairesi"):
                vd = (vd_row.get("vergi_dairesi") or "").strip()
        except Exception:
            pass
    vd = (vd or "").strip()

    unvan = (cust and cust.get("name")) or fatura.get("musteri_adi") or "Müşteri"
    unvan = (unvan or "").strip()
    ad, soyad = "", ""
    if unvan and " " in unvan:
        parts = unvan.split()
        ad = parts[0]
        soyad = " ".join(parts[1:]) if len(parts) > 1 else ""
    else:
        ad = unvan or ""

    toplam = float(fatura.get("toplam") or fatura.get("tutar") or 0)

    # Öncelik her zaman kaydedilmiş fatura satırlarıdır.
    # Eski sürüm KYC varsayılanı (sanal ofis/aylık kira/%20) kullandığı için
    # GİB'de yanlış ürün adı/fiyat/KDV oluşabiliyordu.
    items = []
    kdv_orani_genel = None
    hizmet_adi = None
    birim_fiyat = None
    try:
        import json
        satirlar_raw = fatura.get("satirlar_json")
        satirlar = json.loads(satirlar_raw) if satirlar_raw else []
        if isinstance(satirlar, list):
            for s in satirlar:
                try:
                    ad_raw = (s.get("ad") or s.get("mal_hizmet") or s.get("urun_adi") or "").strip()
                    ad = ad_raw or "Hizmet"
                    miktar = float(s.get("miktar") or 0) or 1.0
                    birim_f = float(s.get("birim_fiyat") or 0)
                    isk_oran = float(s.get("iskonto_orani") or 0)
                    isk_tutar_in = s.get("iskonto_tutar")
                    kdv = int(round(float(s.get("kdv_orani") or 0)))
                    brut = max(0.0, miktar * birim_f)
                    if isk_tutar_in is not None and float(isk_tutar_in or 0) > 0:
                        isk_tutar = min(float(isk_tutar_in), brut)
                    else:
                        isk_tutar = brut * (isk_oran / 100.0)
                    net = max(0.0, brut - isk_tutar)
                    unit_net = (net / miktar) if miktar > 0 else net
                    item = {
                        "name": ad,
                        "quantity": round(miktar, 2),
                        "unit_price": round(unit_net, 2),
                        "tax_rate": int(max(0, min(100, kdv))),
                    }
                    items.append(item)
                except Exception:
                    continue
    except Exception:
        items = []

    if items:
        hizmet_adi = items[0].get("name") or "Hizmet"
        birim_fiyat = float(items[0].get("unit_price") or 0)
        kdv_orani_genel = int(items[0].get("tax_rate") or 0)
    else:
        # Fallback: satır yoksa önce KYC, sonra fatura toplamı
        hizmet_turu = (kyc and kyc.get("hizmet_turu")) or ""
        hizmet_adi = _hizmet_adi_gib(hizmet_turu)
        birim_fiyat = float(kyc.get("aylik_kira") or 0) if kyc else toplam
        if birim_fiyat <= 0:
            birim_fiyat = toplam
        kdv_orani_genel = 20
        items = [{
            "name": hizmet_adi,
            "quantity": 1,
            "unit_price": round(birim_fiyat, 2),
            "tax_rate": int(kdv_orani_genel),
        }]

    return {
        "tarih": tarih_str,
        "saat": saat_str,
        "vkn": vkn,
        "ad": ad,
        "soyad": soyad,
        "unvan": unvan,
        "vd": vd,
        "hizmet_adi": hizmet_adi,
        "birim_fiyat": birim_fiyat,
        "toplam": toplam,
        "kdv_orani": int(kdv_orani_genel or 0),
        "items": items,
        "iban": "",
        "note": (fatura.get("notlar") or "").strip() or "BestOfficeERP",
    }
