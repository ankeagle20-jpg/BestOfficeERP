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

# GİB kütüphanesi opsiyonel; yoksa stub kullanılır
try:
    from fatura import Client as FaturaClient
    _HAS_FATURA_LIB = True
except ImportError:
    FaturaClient = None
    _HAS_FATURA_LIB = False


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
        if _HAS_FATURA_LIB and self.username and self.password:
            self.client = FaturaClient(self.username, self.password, test_mode=self.test_mode)

    def is_available(self):
        """GİB kütüphanesi ve kimlik bilgileri hazır mı."""
        return self.client is not None

    def _ensure_client(self):
        if not self.client:
            if not _HAS_FATURA_LIB:
                raise RuntimeError(
                    "GİB e-Arşiv için 'fatura' kütüphanesi yüklü değil. "
                    "Örn: pip install fatura veya eArsivPortal kullanın."
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
            self.client.login()
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

    def sms_onay_ve_imzala(self, uuid, sms_kodu):
        """
        Telefona gelen SMS kodu ile taslak faturayı onaylar.
        Returns: True başarılı, False hata.
        """
        self._ensure_client()
        try:
            confirm = self.client.confirm_with_sms(uuid, sms_kodu)
            if confirm:
                return True
            return False
        except Exception as e:
            print(f"SMS Onay Hatası: {e}")
            return False


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
            g, a, y = s.split("-")[0], s.split("-")[1], s.split("-")[2]
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
    hizmet_turu = (kyc and kyc.get("hizmet_turu")) or ""
    hizmet_adi = _hizmet_adi_gib(hizmet_turu)
    birim_fiyat = float(kyc.get("aylik_kira") or 0) if kyc else toplam
    if birim_fiyat <= 0:
        birim_fiyat = toplam

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
        "kdv_orani": 20,
        "items": [
            {
                "name": hizmet_adi,
                "quantity": 1,
                "unit_price": round(birim_fiyat, 2),
                "tax_rate": 20,
            }
        ],
        "iban": "",
        "note": (fatura.get("notlar") or "").strip() or "BestOfficeERP",
    }
