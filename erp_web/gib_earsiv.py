# -*- coding: utf-8 -*-
"""
BestOfficeERP – GİB e-Arşiv Fatura Modülü

Fatura verilerini veritabanından çeker, GİB'e taslak oluşturur ve SMS onay sürecini yönetir.
.env içinde GIB_USER ve GIB_PASS tanımlı olmalı.

Kütüphane: `from fatura import Client` kullanılır.
Alternatif: pip install eArsivPortal ile farklı API kullanılabilir (adaptör gerekir).
"""

import os
import re
import time
import json
import logging
from dotenv import load_dotenv

_log = logging.getLogger(__name__)

load_dotenv()

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


def _normalize_vergi_dairesi(vd):
    """Vergi dairesi adını sadeleştir: 'HİTİT VERGİ DAİRESİ MÜD.' -> 'HİTİT'."""
    s = str(vd or "").strip()
    if not s:
        return ""
    su = s.upper().replace("İ", "I")
    idx = su.find("VERG")
    if idx > 0:
        s = s[:idx].strip()
    s = s.strip(" ,.-")
    return s


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
        self.last_sms_debug = None
        self.last_taslak_raw = None
        self.last_gonderilen_payload = None
        self.init_error = None
        try:
            if _HAS_EARSIV_PORTAL and self.username and self.password:
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
            if not _HAS_EARSIV_PORTAL:
                raise RuntimeError(
                    "GİB e-Arşiv için eArsivPortal kütüphanesi yüklü değil. "
                    "Örn: pip install eArsivPortal"
                )
            if not self.username or not self.password:
                raise ValueError("GIB_USER ve GIB_PASS .env dosyasında tanımlı olmalı.")

    def _portal_logout(self):
        """Portal oturumunu kapat (method adı sürüme göre değişebilir)."""
        try:
            if hasattr(self.client, "logout"):
                self.client.logout()
                return
            if hasattr(self.client, "cikis_yap"):
                self.client.cikis_yap()
                return
        except Exception:
            pass

    def _portal_login(self):
        """Portal oturumunu aç (method adı sürüme göre değişebilir)."""
        if hasattr(self.client, "login"):
            self.client.login()
            return
        self.client.giris_yap()

    def _fresh_login(self):
        """Her işlemde taze oturum: önce logout, sonra login."""
        self._portal_logout()
        self._portal_login()

    @staticmethod
    def _to_dict(val):
        if val is None:
            return {}
        if isinstance(val, dict):
            return val
        md = getattr(val, "model_dump", None)
        if callable(md):
            try:
                out = md()
                return out if isinstance(out, dict) else {}
            except Exception:
                pass
        dc = getattr(val, "dict", None)
        if callable(dc):
            try:
                out = dc()
                return out if isinstance(out, dict) else {}
            except Exception:
                pass
        return {}

    @staticmethod
    def _oid_hunt_in_dict(obj, depth=0, max_depth=12):
        """GİB dispatch yanıtında oid — dict/list iç içe, farklı anahtar adları."""
        if obj is None or depth > max_depth:
            return None
        if isinstance(obj, dict):
            for k in (
                "oid", "OID", "Oid", "operationId", "OPERATIONID",
                "operasyonId", "OperasyonId", "OPERASYONID",
            ):
                v = obj.get(k)
                if v is not None and str(v).strip():
                    return str(v).strip()
            for v in obj.values():
                found = BestOfficeGIBManager._oid_hunt_in_dict(v, depth + 1, max_depth)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = BestOfficeGIBManager._oid_hunt_in_dict(item, depth + 1, max_depth)
                if found:
                    return found
        return None

    @staticmethod
    def _oid_regex_in_text(s: str):
        """GİB bazen oid'yi düz metin veya gömülü JSON parçasında döndürür."""
        if not s or not isinstance(s, str):
            return None
        for pat in (
            r'(?i)["\']?oid["\']?\s*[:=]\s*["\']?([0-9A-Za-z_-]{6,80})',
            r'(?i)["\']?operationId["\']?\s*[:=]\s*["\']?([0-9A-Za-z_-]{6,80})',
        ):
            m = re.search(pat, s)
            if m:
                return m.group(1).strip()
        return None

    def _oid_from_smssifre_response(self, sms_resp):
        """SMSSIFRE_GONDER tam gövdesinden OID çıkar (dict, iç içe, data string JSON, mesaj metni)."""
        if not isinstance(sms_resp, dict):
            return None
        o = self._oid_hunt_in_dict(sms_resp)
        if o:
            return o
        d = sms_resp.get("data")
        o = self._oid_hunt_in_dict(d)
        if o:
            return o
        if isinstance(d, str):
            st = d.strip()
            if st.startswith("{") or st.startswith("["):
                try:
                    parsed = json.loads(st)
                    o = self._oid_hunt_in_dict(parsed)
                    if o:
                        return o
                except Exception:
                    pass
            o = self._oid_regex_in_text(st)
            if o:
                return o
        for m in sms_resp.get("messages") or []:
            if not isinstance(m, dict):
                continue
            o = self._oid_hunt_in_dict(m)
            if o:
                return o
            for key in ("text", "message", "msg", "description"):
                t = m.get(key)
                if isinstance(t, str) and t.strip():
                    o = self._oid_regex_in_text(t)
                    if o:
                        return o
        return None

    @staticmethod
    def _smssifre_yanit_ozeti(sms_resp):
        """PII sızdırmadan OID hata ayıklama (kullanıcıya kısa teknik özet)."""
        if not isinstance(sms_resp, dict):
            return f"yanıt_türü={type(sms_resp).__name__}"
        parts = [f"kök_anahtarlar={list(sms_resp.keys())}"]
        d = sms_resp.get("data")
        parts.append(f"data_tipi={type(d).__name__}")
        if isinstance(d, dict) and d:
            parts.append(f"data_anahtarlar={list(d.keys())[:24]}")
        elif isinstance(d, str) and d:
            parts.append(f"data_uzunluk={len(d)}")
        msgs = sms_resp.get("messages")
        if isinstance(msgs, list) and msgs:
            m0 = msgs[0]
            if isinstance(m0, dict):
                t = (m0.get("text") or m0.get("message") or m0.get("msg") or "")[:240]
            else:
                t = str(m0)[:240]
            if t.strip():
                parts.append(f"messages[0]={t.strip()}")
        return " ".join(parts)

    def _client_dispatch(self, cmd: str, sayfa: str, jp: dict):
        """eArsivPortal özel __kod_calistir (sayfa adı GİB sürümüne göre kritik)."""
        from eArsivPortal.Models.Komutlar import Komut

        kod = getattr(self.client, "_eArsivPortal__kod_calistir", None)
        if not callable(kod):
            raise RuntimeError("eArsivPortal istemcisi dispatch (kod_calistir) bulunamadı.")
        return kod(Komut(cmd=cmd, sayfa=sayfa), jp or {})

    def _portal_sms_gonder_ve_oid_al(self):
        """
        SMS + OID: kütüphanenin gib_imza() yerine doğrudan dispatch.

        - TELEFONNO: önce RG_SMSONAY, olmazsa RG_BASITTASLAKLAR (mlevent/fatura vb. ile uyumlu).
        - SMSSIFRE: önce KCEPTEL=False, OID yoksa KCEPTEL=True (ikinci SMS — operatör/GİB uyumu).
        - pageName: RG_SMSONAY, OID yoksa RG_BASITTASLAKLAR (TELEFONNO ile aynı yedek).
        - OID: dict/list derin arama + data string JSON + mesaj metninde regex.
        """
        self.last_sms_error = None
        self.last_sms_debug = None
        telefon_no = None
        for sayfa in ("RG_SMSONAY", "RG_BASITTASLAKLAR"):
            try:
                tel_resp = self._client_dispatch("EARSIV_PORTAL_TELEFONNO_SORGULA", sayfa, {})
                tv = tel_resp.get("data") if isinstance(tel_resp, dict) else None
                if isinstance(tv, dict):
                    telefon_no = tv.get("telefon") or tv.get("CEPTEL") or tv.get("ceptel")
                if telefon_no:
                    _log.info("GİB TELEFONNO ok (sayfa=%s)", sayfa)
                    break
            except Exception as e:
                _log.debug("TELEFONNO %s: %s", sayfa, e)
        if not telefon_no:
            self.last_sms_error = (
                "GİB kayıtlı cep telefonu alınamadı (RG_SMSONAY / RG_BASITTASLAKLAR). "
                "İnteraktif Vergi Dairesi → e-Arşiv işyeri ayarlarından GSM doğrulayın."
            )
            return None
        last_sms_resp = None
        for sayfa_sms in ("RG_SMSONAY", "RG_BASITTASLAKLAR"):
            for kceptel in (False, True):
                try:
                    sms_resp = self._client_dispatch(
                        "EARSIV_PORTAL_SMSSIFRE_GONDER",
                        sayfa_sms,
                        {"CEPTEL": telefon_no, "KCEPTEL": bool(kceptel), "TIP": ""},
                    )
                    last_sms_resp = sms_resp
                    oid = self._oid_from_smssifre_response(sms_resp) if isinstance(sms_resp, dict) else None
                    if oid:
                        _log.info("GİB SMSSIFRE OID alındı (sayfa=%s, KCEPTEL=%s)", sayfa_sms, kceptel)
                        return oid
                except Exception as e:
                    self.last_sms_error = str(e)
                    _log.warning("SMSSIFRE_GONDER sayfa=%s KCEPTEL=%s: %s", sayfa_sms, kceptel, e)
        ozet = self._smssifre_yanit_ozeti(last_sms_resp) if last_sms_resp is not None else "yanıt_yok"
        _log.warning("GİB SMSSIFRE OID yok. %s", ozet)
        if os.getenv("GIB_SMS_DEBUG", "").strip().lower() in ("1", "true", "evet"):
            try:
                self.last_sms_debug = json.dumps(last_sms_resp, ensure_ascii=False, default=str)[:4000]
            except Exception:
                self.last_sms_debug = repr(last_sms_resp)[:4000]
        self.last_sms_error = (
            "GİB SMSSIFRE yanıtında OID parse edilemedi. Taslaklar portalda listeleniyorsa "
            "satırı seçip «GİB İmza» ile tarayıcıdan imzalayın; veya birkaç dakika sonra "
            "ERP’den «SMS Gönder»i tekrar deneyin."
            f" (Teknik özet: {ozet})"
        )
        return None

    def _find_fatura_by_uuid(self, uuid, days_back=370, force_new_session=True):
        from datetime import datetime, timedelta
        if force_new_session:
            self._fresh_login()
        bugun = datetime.now().date()
        bas = (bugun - timedelta(days=max(7, int(days_back or 370)))).strftime("%d/%m/%Y")
        bit = (bugun + timedelta(days=1)).strftime("%d/%m/%Y")
        rows = self.client.faturalari_getir(baslangic_tarihi=bas, bitis_tarihi=bit) or []
        uid = str(uuid or "").strip().lower()
        for r in rows:
            d = self._to_dict(r)
            if str(d.get("ettn") or d.get("uuid") or "").strip().lower() == uid:
                return r, d
        return None, None

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
            # Kullanıcı isteği: her taslak işleminde taze login/token.
            self._fresh_login()
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

        # eArsivPortal API tek satır parametresi alsa da payload override ile çok satır gönderiyoruz.
        first = items[0] if items else {}
        urun_adi = str(first.get("name") or fatura_data.get("hizmet_adi") or "Hizmet")
        fiyat = float(first.get("unit_price") or fatura_data.get("birim_fiyat") or fatura_data.get("toplam") or 0)
        kdv_orani = int(first.get("tax_rate") or fatura_data.get("kdv_orani") or 20)
        # eArsivPortal'ın içindeki fatura_ver fonksiyonu bazı sürümlerde %20'ye sabit.
        # Bu yüzden fatura_olustur çağrısı öncesi payload üretimini dinamik KDV/fiyat ile override ediyoruz.
        fatura_method = getattr(self.client, "fatura_olustur")
        fn = getattr(fatura_method, "__func__", fatura_method)
        glb = getattr(fn, "__globals__", {})
        orig_fatura_ver = glb.get("fatura_ver")
        if not callable(orig_fatura_ver):
            raise RuntimeError("eArsivPortal iç fatura_ver fonksiyonu bulunamadı.")

        satirlar_norm = []
        toplam_brut = 0.0
        toplam_matrah = 0.0
        toplam_kdv = 0.0
        toplam_iskonto_signed = 0.0
        for it in items:
            try:
                nm = str(it.get("name") or "Hizmet").strip() or "Hizmet"
                qty = float(it.get("quantity") or 1.0)
                if qty <= 0:
                    qty = 1.0
                up = float(it.get("unit_price") or 0.0)
                if up < 0:
                    up = 0.0
                tax = int(it.get("tax_rate") or kdv_orani or 20)
                tax = int(max(0, min(100, tax)))
                disc_rate_signed = float(it.get("discount_rate") or 0.0)
                disc_rate_signed = max(-100.0, min(100.0, disc_rate_signed))
                brut = round(up * qty, 2)
                disc_amount_in = it.get("discount_amount")
                if disc_amount_in is not None and float(disc_amount_in or 0) != 0:
                    disc_signed = max(-brut, min(float(disc_amount_in), brut))
                else:
                    disc_signed = round(brut * (disc_rate_signed / 100.0), 2)
                matrah = round(max(0.0, brut - disc_signed), 2)
                kdv_t = round(matrah * (float(tax) / 100.0), 2)
                toplam = round(matrah + kdv_t, 2)
                satirlar_norm.append({
                    "name": nm,
                    "quantity": qty,
                    "unit_price": up,
                    "tax_rate": tax,
                    "disc_rate_signed": disc_rate_signed,
                    "disc_signed": disc_signed,
                    "brut": brut,
                    "matrah": matrah,
                    "kdv": kdv_t,
                    "toplam": toplam,
                })
                toplam_brut += brut
                toplam_matrah += matrah
                toplam_kdv += kdv_t
                toplam_iskonto_signed += disc_signed
            except Exception:
                continue
        if not satirlar_norm:
            raise ValueError("GİB için satırlar işlenemedi.")
        toplam_brut = round(toplam_brut, 2)
        toplam_matrah = round(toplam_matrah, 2)
        toplam_kdv = round(toplam_kdv, 2)
        toplam_iskonto_signed = round(toplam_iskonto_signed, 2)
        genel_toplam_hesap = round(toplam_matrah + toplam_kdv, 2)
        erp_toplam = float(fatura_data.get("toplam") or 0)
        odenecek_yazi_icin = round(erp_toplam, 2) if erp_toplam > 0 else genel_toplam_hesap

        def _gib_not_metni():
            """Önizlemedeki gibi YALNIZ:#…# üstte; imza satırı GİB’de genelde altta (aynı alan, boş satırla)."""
            try:
                from routes.faturalar_routes import tutar_yaziya_gib
                yazi_line = tutar_yaziya_gib(odenecek_yazi_icin)
            except Exception:
                yazi_line = f"YALNIZ:#{odenecek_yazi_icin}TÜRKLİRASIDIR#"
            parcalar = [yazi_line.strip()]
            irsaliye_mi = bool(fatura_data.get("irsaliye_modu")) or str(fatura_data.get("fatura_tipi") or "").lower() in ("irsaliye", "sevk")
            uretim_metni = (
                "Bu E-Arşiv İrsaliye BESTOFFICE ERP tarafından üretilmiştir"
                if irsaliye_mi
                else "Bu E-Arşiv Fatura BESTOFFICE ERP tarafından üretilmiştir"
            )
            iban_val = str(fatura_data.get("iban") or "").strip()
            iban_line = (
                f"OFİSBİR AKBANK IBAN:{iban_val.replace(' ', '')}"
                if iban_val
                else "OFİSBİR AKBANK IBAN:TR590004600153888000173206"
            )
            parcalar.append(f"{iban_line} - {uretim_metni}")
            n = (fatura_data.get("note") or "").strip()
            if n and n != "BestOfficeERP":
                parcalar.append(n)
            return "\n\n".join(parcalar)

        gib_not_tam = _gib_not_metni()
        # GİB not alanı çok kısaysa yazı kesilir; önizleme + alt not için ~1.5k güvenli.
        _not_max = 1500
        gib_not_kisaltilmis = (gib_not_tam[:_not_max] if gib_not_tam else "")

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
            "fatura_notu": gib_not_kisaltilmis,
        }
        alici_adres = str(fatura_data.get("adres") or "").strip()
        alici_tel = str(fatura_data.get("telefon") or "").strip()
        alici_email = str(fatura_data.get("email") or "").strip()

        def _fatura_ver_dynamic(**kwargs):
            d = orig_fatura_ver(**kwargs)
            try:
                mal_tablo = []
                for r in satirlar_norm:
                    tip_iskonto = r["disc_signed"] >= 0
                    orani_abs = abs(r["disc_rate_signed"])
                    tutari_abs = abs(r["disc_signed"])
                    unit_net = (r["matrah"] / r["quantity"]) if r["quantity"] > 0 else r["matrah"]
                    mal_tablo.append({
                        "malHizmet": r["name"],
                        "miktar": round(r["quantity"], 2),
                        "birim": "C62",
                        "birimFiyat": f"{r['unit_price']:.2f}",
                        "fiyat": f"{unit_net:.2f}",
                        "iskontoOrani": f"{orani_abs:.2f}",
                        "iskontoTutari": f"{tutari_abs:.2f}",
                        "iskontoNedeni": "İSKONTO" if tip_iskonto else "ARTTIRIM",
                        "malHizmetTutari": f"{r['matrah']:.2f}",
                        "kdvOrani": str(int(r["tax_rate"])),
                        "vergiOrani": 0,
                        "kdvTutari": f"{r['kdv']:.2f}",
                        "vergininKdvTutari": "0",
                        "ozelMatrahTutari": "0",
                        "hesaplananotvtevkifatakatkisi": "0",
                        # Bazı sürümler bu alan adlarını kullanıyor.
                        "iskontoArttirimOrani": f"{orani_abs:.2f}",
                        "iskontoArttirimTutari": f"{tutari_abs:.2f}",
                        "iskontoArttirimNedeni": "İSKONTO" if tip_iskonto else "ARTTIRIM",
                    })
                d["malHizmetTable"] = mal_tablo
                d["matrah"] = f"{toplam_matrah:.2f}"
                d["malhizmetToplamTutari"] = f"{toplam_brut:.2f}"
                d["toplamIskonto"] = f"{abs(toplam_iskonto_signed):.2f}"
                d["tip"] = "İskonto" if toplam_iskonto_signed >= 0 else "Arttırım"
                d["hesaplanankdv"] = f"{toplam_kdv:.2f}"
                d["vergilerToplami"] = f"{toplam_kdv:.2f}"
                d["vergilerDahilToplamTutar"] = f"{genel_toplam_hesap:.2f}"
                d["odenecekTutar"] = f"{genel_toplam_hesap:.2f}"
                d["not"] = gib_not_kisaltilmis
                if alici_adres:
                    d["bulvarcaddesokak"] = alici_adres
                if alici_tel:
                    d["tel"] = alici_tel
                if alici_email:
                    d["eposta"] = alici_email
                try:
                    self.last_gonderilen_payload = dict(d)
                except Exception:
                    self.last_gonderilen_payload = d
            except Exception:
                pass
            return d

        glb["fatura_ver"] = _fatura_ver_dynamic
        try:
            out = self.client.fatura_olustur(**payload)
        finally:
            glb["fatura_ver"] = orig_fatura_ver
        self.last_taslak_raw = out
        d = self._to_dict(out)
        uuid = (d.get("ettn") or d.get("uuid") or out)

        # Kullanıcı isteği: create sonrası son 1 gün listeden ETTN doğrula.
        try:
            _, dogrulama = self._find_fatura_by_uuid(uuid, days_back=1, force_new_session=False)
            if dogrulama:
                return dogrulama.get("ettn") or dogrulama.get("uuid") or uuid
            self.last_sms_error = "Taslak oluşturuldu fakat son 1 günlük listede ETTN doğrulanamadı."
        except Exception:
            self.last_sms_error = "Taslak sonrası liste doğrulaması başarısız."
        return uuid

    def sms_onay_ve_imzala(self, uuid, sms_kodu):
        """
        Telefona gelen SMS kodu ile taslak faturayı onaylar.
        Returns: True başarılı, False hata.
        """
        self._ensure_client()
        try:
            self.last_sms_error = None
            self._fresh_login()
            oid = self._portal_sms_gonder_ve_oid_al()
            if not oid:
                if not self.last_sms_error:
                    self.last_sms_error = "OID alınamadı."
                return False
            return self.sms_onay_earsivportal(uuid, sms_kodu, oid)
        except Exception as e:
            print(f"SMS Onay Hatası: {e}")
            self.last_sms_error = str(e)
            return False

    def sms_onay_earsivportal(self, uuid, sms_kodu, oid):
        """eArsivPortal için verilen oid ile SMS kodunu onaylar."""
        self._ensure_client()
        try:
            self.last_sms_error = None
            self._fresh_login()
            hedef = None
            hedef_dict = None
            for _ in range(4):
                hedef, hedef_dict = self._find_fatura_by_uuid(uuid, days_back=370)
                if hedef:
                    break
                time.sleep(1.0)
            if not hedef:
                self.last_sms_error = "UUID taslak listesinde bulunamadı."
                return False
            try:
                res = self.client.gib_sms_onay(hedef, oid, sms_kodu)
            except Exception:
                res = self.client.gib_sms_onay(hedef_dict or {}, oid, sms_kodu)
            msg = str((self._to_dict(res) or {}).get("mesaj") or "")
            ok = ("başar" in msg.lower()) or ("onay" in msg.lower()) or (msg.strip() == "")
            if not ok:
                self.last_sms_error = msg or "GİB SMS doğrulaması olumsuz döndü."
            return ok
        except Exception as e:
            print(f"SMS OID Onay Hatası: {e}")
            self.last_sms_error = str(e)
            return False

    def sms_kodu_gonder(self, uuid):
        """eArsivPortal için SMS gönderimini başlatır ve oid döndürür."""
        self._ensure_client()
        self.last_sms_error = None
        for attempt in range(3):
            try:
                self._fresh_login()
                oid = self._portal_sms_gonder_ve_oid_al()
                if oid:
                    return oid
                if self.test_mode and self.last_sms_error and "GIB_TEST" not in self.last_sms_error:
                    self.last_sms_error += (
                        " Not: GIB_TEST açık — test portalı kullanılıyor; canlı mükellef hesabıyla "
                        "çakışma olabilir (.env GIB_TEST=0 deneyin)."
                    )
                _log.warning("sms_kodu_gonder: OID yok (deneme %s/3): %s", attempt + 1, self.last_sms_error)
            except Exception as e:
                self.last_sms_error = str(e)
                _log.exception("sms_kodu_gonder istisna (deneme %s/3)", attempt + 1)
                print(f"SMS Gönderim Hatası: {e}")
                time.sleep(1.2)
        if not (self.last_sms_error or "").strip():
            self.last_sms_error = (
                "OID alınamadı. GIB_USER / GIB_PASS / GIB_TEST ve GİB’de kayıtlı cep numarasını kontrol edin."
            )
        return None

    def fatura_durum_getir(self, uuid, days_back=370):
        """UUID ile GİB'deki fatura/onay durumunu bulur (eArsivPortal)."""
        self._ensure_client()
        try:
            _, d = self._find_fatura_by_uuid(uuid, days_back=days_back)
            return d
        except Exception as e:
            print(f"Fatura durum getir hatası: {e}")
        return None

    def fatura_html_getir(self, uuid, days_back=370):
        """UUID/ETTN için GİB portalındaki fatura HTML çıktısını döndürür."""
        self._ensure_client()
        try:
            _, d = self._find_fatura_by_uuid(uuid, days_back=days_back, force_new_session=True)
            if not d:
                raise ValueError("GİB kaydında fatura bulunamadı.")
            ettn = str(d.get("ettn") or d.get("uuid") or uuid or "").strip()
            if not ettn:
                raise ValueError("GİB ETTN/UUID bulunamadı.")
            onay = str(d.get("onayDurumu") or d.get("durum") or "").strip()
            if not hasattr(self.client, "fatura_html"):
                raise RuntimeError("GİB istemcisi fatura HTML görüntüleme desteklemiyor.")
            html = self.client.fatura_html(ettn, onay)
            return str(html or "")
        except Exception as e:
            print(f"GİB fatura HTML getir hatası: {e}")
            raise


def build_fatura_data_from_db(fatura_id, fetch_one_func):
    """
    Veritabanından fatura + müşteri + KYC çekip GİB fatura_data sözlüğüne dönüştürür.
    fetch_one_func: (sql, params) -> dict kullanacak fonksiyon (örn. db.fetch_one).
    """
    fatura = fetch_one_func(
        """
        SELECT
            f.id,
            f.fatura_no,
            f.fatura_tarihi,
            f.toplam,
            f.tutar,
            f.kdv_tutar,
            f.satirlar_json,
            f.musteri_id,
            f.musteri_adi,
            f.notlar
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
            "SELECT id, name, address, tax_number, phone, email FROM customers WHERE id = %s",
            (musteri_id,),
        )
        kyc = fetch_one_func(
            "SELECT vergi_dairesi, vergi_no, hizmet_turu, aylik_kira, yeni_adres, yetkili_email, email, yetkili_tel FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
            (musteri_id,),
        )

    # Tarih GG/AA/YYYY, saat SS:DD veya SS:DD:SS
    ft = fatura.get("fatura_tarihi")
    if hasattr(ft, "strftime"):
        tarih_str = ft.strftime("%d/%m/%Y")
        # fatura_tarihi datetime ise saatini koru; 00:00 ise anlık saat kullan.
        try:
            saat_from_ft = ft.strftime("%H:%M:%S")
        except Exception:
            saat_from_ft = ""
        if saat_from_ft and saat_from_ft != "00:00:00":
            saat_str = saat_from_ft
        else:
            from datetime import datetime as _dt
            saat_str = _dt.now().strftime("%H:%M:%S")
    else:
        s = str(ft or "")[:10]
        if s and len(s) == 10 and s[4] == "-":
            y, a, g = s.split("-")[0], s.split("-")[1], s.split("-")[2]
            tarih_str = f"{g}/{a}/{y}"
        else:
            tarih_str = "01/01/2025"
        from datetime import datetime as _dt
        saat_str = _dt.now().strftime("%H:%M:%S")

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
    vd = _normalize_vergi_dairesi(vd)

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
                    birim_f = float(s.get("birim_fiyat") or s.get("unit_price") or 0)
                    isk_oran = float(s.get("iskonto_orani") or 0)
                    isk_tutar_in = s.get("iskonto_tutar")
                    isk_tip = str(s.get("iskonto_tipi") or "").strip().lower()
                    if isk_tip.startswith("art"):
                        if isk_oran > 0:
                            isk_oran = -abs(isk_oran)
                        if isk_tutar_in is not None:
                            _it = float(isk_tutar_in or 0)
                            isk_tutar_in = (-abs(_it) if _it > 0 else _it)
                    kdv = int(round(float(s.get("kdv_orani") or s.get("kdv") or s.get("tax_rate") or 0)))
                    brut = max(0.0, miktar * birim_f)
                    if isk_tutar_in is not None and float(isk_tutar_in or 0) != 0:
                        isk_tutar = max(-brut, min(float(isk_tutar_in), brut))
                    else:
                        isk_tutar = brut * (isk_oran / 100.0)
                    net = max(0.0, brut - isk_tutar)
                    # Kullanıcının satırda girdiği birim fiyatı öncelikle koru.
                    unit_net = birim_f if birim_f > 0 else ((net / miktar) if miktar > 0 else net)
                    item = {
                        "name": ad,
                        "quantity": round(miktar, 2),
                        # Brüt birim fiyatı koru; iskonto GİB alanlarında ayrıca gösterilecek.
                        "unit_price": round(birim_f, 2) if birim_f > 0 else round(unit_net, 2),
                        "tax_rate": int(max(0, min(100, kdv))),
                        # + oran/tutar = iskonto, - oran/tutar = arttırım
                        "discount_rate": round(max(-100.0, min(100.0, isk_oran)), 2),
                        "discount_amount": round(max(-brut, min(brut, isk_tutar)), 2),
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

    notlar_raw = str(fatura.get("notlar") or "")
    irsaliye_modu = "IRSALIYE_MODU" in notlar_raw.upper()
    note_clean = notlar_raw.replace("IRSALIYE_MODU", "").replace("||", "|").strip(" |")

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
        "adres": ((cust.get("address") if cust else "") or (kyc.get("yeni_adres") if kyc else "") or "").strip(),
        "telefon": ((kyc.get("yetkili_tel") if kyc else "") or (cust.get("phone") if cust else "") or "").strip(),
        # Öncelik: Yetkili E-posta -> müşteri e-posta -> şirket e-posta
        "email": ((kyc.get("yetkili_email") if kyc else "") or (cust.get("email") if cust else "") or (kyc.get("email") if kyc else "") or "").strip(),
        "iban": "",
        "note": note_clean or "BestOfficeERP",
        "irsaliye_modu": bool(irsaliye_modu),
    }
