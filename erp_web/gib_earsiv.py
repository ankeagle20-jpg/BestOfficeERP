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
import uuid as _uuid_stdlib
import logging
import threading
from dotenv import load_dotenv

_log = logging.getLogger(__name__)

load_dotenv()

# portal_kesilen_fatura_listesi_normalized için kısa TTL önbellek (aynı tarih aralığında GİB’i yormamak)
_portal_kesilen_list_cache_lock = threading.Lock()
_portal_kesilen_list_cache: dict[str, tuple[float, list]] = {}


def _portal_kesilen_list_cache_key(bas_date, bit_date) -> str:
    """Env’deki liste parametreleri sonucu etkilediği için anahtara dahil edilir."""
    ht = (os.getenv("GIB_PORTAL_LISTE_HANGI_TIP") or "5000/30000").strip()
    try:
        pbd = int((os.getenv("GIB_PORTAL_LISTE_GUN_ONCE") or "62").strip() or "62")
    except ValueError:
        pbd = 62
    try:
        pad = int((os.getenv("GIB_PORTAL_LISTE_GUN_SONRA") or "14").strip() or "14")
    except ValueError:
        pad = 14
    try:
        ch = int((os.getenv("GIB_PORTAL_LISTE_CHUNK_GUN") or "8").strip() or "8")
    except ValueError:
        ch = 8
    return f"{bas_date.isoformat()}|{bit_date.isoformat()}|{ht}|{pbd}|{pad}|{ch}"


def _portal_kesilen_list_cache_ttl_saniye() -> int:
    try:
        return int((os.getenv("GIB_PORTAL_LISTE_CACHE_SANIYE") or "180").strip() or "180")
    except ValueError:
        return 180


def portal_kesilen_fatura_listesi_cache_clear() -> None:
    """GİB ile canlı kontrol sonrası vb. için portal liste önbelleğini boşalt."""
    with _portal_kesilen_list_cache_lock:
        _portal_kesilen_list_cache.clear()


try:
    from eArsivPortal import eArsivPortal as EArsivPortalClient
    _HAS_EARSIV_PORTAL = True
except ImportError:
    EArsivPortalClient = None
    _HAS_EARSIV_PORTAL = False

_EARSIVPORTAL_FATURA_OLUSTUR_PATCHED = False


def _gib_mal_hizmet_metin(name) -> str:
    """Portalın aşırı uzun / özel tire karakterlerinde şaşırmasını azalt."""
    s = str(name or "").strip() or "Hizmet"
    for a, b in (
        ("\u2014", "-"),
        ("\u2013", "-"),
        ("\u2212", "-"),
        ("\u00a0", " "),
    ):
        s = s.replace(a, b)
    return s[:500]


def _gib_normalize_alici_for_portal(fatura: dict, vkn_veya_tckn) -> None:
    """
    Tüzel kişi (10 haneli VKN): aliciAdi/aliciSoyadi bazen fatura satırına bölünür
    (ör. «Hizmet» + «bedeli — MAYIS …»); em-dash yalnızca soyadda olunca eski heuristik
    hiç çalışmıyordu. aliciUnvan doluysa GİB şablonu için ad/soyadı her zaman ünvandan üret.
    """
    v = str(vkn_veya_tckn or "").replace(" ", "").strip()
    if len(v) != 10:
        return
    unv = str(fatura.get("aliciUnvan") or "").strip()
    if not unv:
        return
    parts = unv.split(None, 1)
    fatura["aliciAdi"] = (parts[0][:80] if parts else "")[:80]
    fatura["aliciSoyadi"] = ((parts[1] or "")[:80]) if len(parts) > 1 else ""


def _gib_dispatch_fatura_jp(fatura: dict) -> dict:
    """GİB dispatch jp: faturaUuid doğrula ve JSON anahtar sırasında ilk sırada tut."""
    fid = str(fatura.get("faturaUuid") or "").strip()
    if len(fid) != 36:
        raise ValueError(f"GİB faturaUuid 36 karakter olmalı; len={len(fid)} değer={fid[:48]!r}")
    out = {"faturaUuid": fid}
    for k, v in fatura.items():
        if k != "faturaUuid":
            out[k] = v
    return out


def _patch_earsivportal_fatura_olustur_loop():
    """
    eArsivPortal sürümlerinde fatura_olustur: başarısız yanıtta while True + aynı UUID ile
    sonsuz deneme yapıyor; GİB yoğun/ETTN geçici hatalarında işlem asla bitmiyor.
    Sınırlı deneme + her tekrarda yeni faturaUuid (GİB önerisiyle uyumlu).
    """
    global _EARSIVPORTAL_FATURA_OLUSTUR_PATCHED
    if not _HAS_EARSIV_PORTAL or _EARSIVPORTAL_FATURA_OLUSTUR_PATCHED:
        return
    import eArsivPortal.Core as _ep_core_mod
    from datetime import datetime as _dt_mod
    from pytz import timezone as _tz_tr

    EP_cls = _ep_core_mod.eArsivPortal
    if getattr(EP_cls, "_bestoffice_ep_patch_applied", False):
        _EARSIVPORTAL_FATURA_OLUSTUR_PATCHED = True
        return

    def _bestoffice_fatura_olustur(
        self,
        tarih="07/10/1995",
        saat="14:28:37",
        para_birimi="TRY",
        vkn_veya_tckn="11111111111",
        ad="Ömer Faruk",
        soyad="Sancak",
        unvan="",
        vergi_dairesi="",
        urun_adi="Python Yazılım Hizmeti",
        fiyat=100,
        fatura_notu="— QNB Finansbank —\nTR70 0011 1000 0000 0118 5102 59\nÖmer Faruk Sancak",
    ):
        kod = getattr(self, "_eArsivPortal__kod_calistir")
        nesne = getattr(self, "_eArsivPortal__nesne_ver")
        kisi_bilgi = self.kisi_getir(vkn_veya_tckn)
        vkn_c = str(vkn_veya_tckn or "").replace(" ", "").strip()
        ka = str(getattr(kisi_bilgi, "adi", None) or "").strip()
        ks = str(getattr(kisi_bilgi, "soyadi", None) or "").strip()
        ku = str(getattr(kisi_bilgi, "unvan", None) or "").strip()
        ea, es, eu = (ad or "").strip(), (soyad or "").strip(), (unvan or "").strip()
        # Tüzel (10 haneli VKN): MERNIS «adi» bazen satır açıklaması gibi geliyor; ERP müşteri kaydı öncelikli.
        if len(vkn_c) == 10:
            ad_i, soy_i, un_i = ea or ka, es or ks, eu or ku
        else:
            ad_i, soy_i, un_i = ka or ea, ks or es, ku or eu
        # fatura_taslak_olustur, fatura_ver'i bu modülün globals'ına yazar; Core.fatura_ver değişmez.
        # Burada Libs'teki ham fatura_ver kullanılırsa çok satırlı payload uygulanmaz → GİB ETTN/validasyon hataları.
        import sys
        _gib_mod = sys.modules.get("gib_earsiv")
        fv = getattr(_gib_mod, "fatura_ver", None) if _gib_mod else None
        if not callable(fv):
            fv = _ep_core_mod.fatura_ver
        from uuid import uuid4
        yeni_uuid = str(uuid4())  # 36 karakter: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        print(f"[GİB UUID] faturaUuid={yeni_uuid}, len={len(yeni_uuid)}")
        fatura = fv(
            tarih=tarih or _dt_mod.now(_tz_tr("Turkey")).strftime("%d/%m/%Y"),
            saat=saat,
            para_birimi=para_birimi,
            vkn_veya_tckn=vkn_veya_tckn,
            ad=ad_i,
            soyad=soy_i,
            unvan=un_i,
            vergi_dairesi=kisi_bilgi.vergiDairesi or vergi_dairesi,
            urun_adi=urun_adi,
            fiyat=fiyat,
            fatura_notu=fatura_notu,
        )
        fatura["faturaUuid"] = yeni_uuid
        if len(vkn_c) == 10 and un_i:
            fatura["aliciUnvan"] = un_i[:255]
        try:
            max_try = int((os.getenv("GIB_FATURA_OLUSTUR_MAX_DENEME") or "14").strip() or "14")
        except ValueError:
            max_try = 14
        max_try = max(3, min(max_try, 40))
        last_data = None
        for attempt in range(max_try):
            if attempt:
                yeni_uuid = str(uuid4())  # 36 karakter: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
                print(f"[GİB UUID] faturaUuid={yeni_uuid}, len={len(yeni_uuid)}")
                fatura["faturaUuid"] = yeni_uuid
            _gib_normalize_alici_for_portal(fatura, vkn_veya_tckn)
            jp = _gib_dispatch_fatura_jp(fatura)
            import json as _json_dbg
            _fatura_log = {
                k: v
                for k, v in jp.items()
                if k
                in (
                    "faturaUuid",
                    "aliciAdi",
                    "aliciSoyadi",
                    "aliciUnvan",
                    "vknTckn",
                    "faturaTarihi",
                    "matrah",
                    "odenecekTutar",
                    "malHizmetTable",
                )
            }
            print(f"[GİB FATURA LOG] {_json_dbg.dumps(_fatura_log, ensure_ascii=False)[:500]}")
            istek = kod(self.komutlar.FATURA_OLUSTUR, jp)
            data = istek.get("data")
            last_data = data
            if isinstance(data, str) and "Faturanız başarıyla oluşturulmuştur." in data:
                return nesne("FaturaOlustur", {"ettn": jp.get("faturaUuid")})
            try:
                nm = f"{jp.get('aliciAdi')} {jp.get('aliciSoyadi')}"
            except Exception:
                nm = "?"
            _log.warning("GİB FATURA_OLUSTUR deneme %s/%s: %s | %s", attempt + 1, max_try, nm, data)
            time.sleep(0.18)
        from eArsivPortal.Core.Hatalar import eArsivPortalHatasi

        tail = str(last_data)[:900] if last_data is not None else ""
        raise eArsivPortalHatasi(
            f"GİB taslak {max_try} denemede tamamlanamadı (her denemede yeni ETTN/UUID). Son yanıt: {tail}"
        )

    EP_cls._bestoffice_ep_orig_fatura_olustur = EP_cls.fatura_olustur
    EP_cls.fatura_olustur = _bestoffice_fatura_olustur
    EP_cls._bestoffice_ep_patch_applied = True
    _EARSIVPORTAL_FATURA_OLUSTUR_PATCHED = True


if _HAS_EARSIV_PORTAL:
    try:
        _patch_earsivportal_fatura_olustur_loop()
    except Exception as _ep_patch_ex:
        _log.warning("eArsivPortal fatura_olustur yaması uygulanamadı: %s", _ep_patch_ex)


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


def gib_fatura_html_watermark_etiket(html) -> str | None:
    """GİB e-arşiv fatura HTML/PDF içindeki filigran metninden durum.

    Dönüş:
      • «İptal»: HTML’de iptal/geçersiz filigranı varsa
      • «İmzasız»: imzasız filigranı varsa
      • «İmzalı»: HTML geçerli bir e-Arşiv faturası ve filigran yoksa
      • None: HTML kısa/geçersiz veya tanımlanamadı (örn. hata sayfası)
    """
    if not isinstance(html, str) or len(html) < 80:
        return None
    # İptal su damgası (Türkçe / ASCII karışık HTML)
    if re.search(r"iptal\s+edilm", html, flags=re.IGNORECASE):
        return "İptal"
    if re.search(r"(geçersizdir|gecersizdir|geçersiz\s+fatura)", html, flags=re.IGNORECASE):
        return "İptal"
    # Taslak / önizleme
    if re.search(r"imzasız|imzasiz", html, flags=re.IGNORECASE):
        return "İmzasız"
    # Filigran yok: gerçekten geçerli bir e-Arşiv fatura HTML/PDF mi?
    fatura_belirtisi = bool(
        re.search(r"e[\-\s]*ar[şs]iv\s+fatura", html, flags=re.IGNORECASE)
        or re.search(r"earsivfatura", html, flags=re.IGNORECASE)
        or re.search(r"fatura\s+no\s*:", html, flags=re.IGNORECASE)
        or re.search(r"ettn\s*:", html, flags=re.IGNORECASE)
    )
    if fatura_belirtisi:
        return "İmzalı"
    return None


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
                self._portal_compat_shim()
                self._portal_http_timeout_shim()
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
        self._portal_compat_shim()
        self._portal_http_timeout_shim()

    def _portal_http_timeout_shim(self):
        """eArsivPortal `requests` oturumunda timeout yok; tek takılı POST saatlerce kilitler."""
        c = self.client
        if not c or getattr(c, "_bestoffice_requests_timeout_shim", False):
            return
        sess = getattr(c, "oturum", None)
        if sess is None:
            return
        try:
            read_s = int((os.getenv("GIB_HTTP_READ_TIMEOUT_S") or "90").strip() or "90")
        except ValueError:
            read_s = 90
        try:
            conn_s = int((os.getenv("GIB_HTTP_CONNECT_TIMEOUT_S") or "15").strip() or "15")
        except ValueError:
            conn_s = 15
        read_s = max(25, min(180, read_s))
        conn_s = max(5, min(60, conn_s))
        timeout_tuple = (float(conn_s), float(read_s))
        orig_post = sess.post
        orig_request = sess.request

        def post_with_timeout(url, data=None, json=None, **kwargs):
            if kwargs.get("timeout") is None:
                kwargs["timeout"] = timeout_tuple
            return orig_post(url, data=data, json=json, **kwargs)

        def request_with_timeout(method, url, **kwargs):
            if kwargs.get("timeout") is None and str(method).upper() == "POST":
                kwargs["timeout"] = timeout_tuple
            return orig_request(method, url, **kwargs)

        sess.post = post_with_timeout
        sess.request = request_with_timeout
        setattr(c, "_bestoffice_requests_timeout_shim", True)
        _log.info("GİB portal HTTP timeout (connect, read) = (%ss, %ss)", conn_s, read_s)

    def _portal_compat_shim(self):
        """
        Bazı eArsivPortal sürümleri private isimli metotları çağırıyor
        (_eArsivPortal__giris_yap gibi). Eksikse public metodlara alias aç.
        """
        c = self.client
        if not c:
            return
        try:
            if not hasattr(c, "_eArsivPortal__giris_yap"):
                if hasattr(c, "giris_yap"):
                    setattr(c, "_eArsivPortal__giris_yap", getattr(c, "giris_yap"))
                elif hasattr(c, "login"):
                    setattr(c, "_eArsivPortal__giris_yap", getattr(c, "login"))
            if not hasattr(c, "_eArsivPortal__cikis_yap"):
                if hasattr(c, "cikis_yap"):
                    setattr(c, "_eArsivPortal__cikis_yap", getattr(c, "cikis_yap"))
                elif hasattr(c, "logout"):
                    setattr(c, "_eArsivPortal__cikis_yap", getattr(c, "logout"))
        except Exception:
            pass

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
        try:
            if hasattr(self.client, "login"):
                self.client.login()
                return
            self.client.giris_yap()
        except Exception:
            print(f"[GİB LOGIN] user={os.getenv('GIB_USER','YOK')[:4]}*** test={os.getenv('GIB_TEST','0')}")
            raise

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
    def _parse_tr_amount_portal(val):
        """Portal/GİB tutarları: TR (1.375,00), US/JSON (11071.2), tam sayı."""
        if val is None:
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip().replace("₺", "").replace("TL", "").replace(" ", "")
        if not s or s.lower() in ("null", "none", "-", "—"):
            return 0.0
        # Hem ',' hem '.' varsa ondalık ayracı sağdaki olandır.
        if "," in s and "." in s:
            last_comma = s.rfind(",")
            last_dot = s.rfind(".")
            if last_comma > last_dot:
                # 1.375,00 -> 1375.00
                s = s.replace(".", "").replace(",", ".")
            else:
                # 1,375.00 -> 1375.00
                s = s.replace(",", "")
        elif "," in s:
            # 1375,00 -> 1375.00
            s = s.replace(".", "").replace(",", ".")
        else:
            if re.fullmatch(r"-?\d+", s):
                return float(s)
            # 1375.00 (nokta ondalık) veya 1.375 (binlik) ayrımı:
            # Son parça 1-2 hane ise ondalık kabul et, aksi halde binlikleri kaldır.
            if re.fullmatch(r"-?\d+\.\d{1,2}", s):
                pass
            elif re.fullmatch(r"-?\d+\.\d+", s):
                pass
            else:
                s = s.replace(".", "")
        try:
            return float(s)
        except ValueError:
            return 0.0

    @staticmethod
    def _portal_extract_belge_no(d):
        if not isinstance(d, dict):
            return ""
        # GİB TASLAKLARI_GETIR listesi çoğunlukla belgeNo döner (belgeNumarasi değil).
        for k in (
            "belgeNo",
            "belgeNumarasi",
            "faturaNo",
            "fatura_no",
            "belge_no",
            "invoiceNumber",
        ):
            v = d.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        normalized = {}
        for k, v in d.items():
            nk = re.sub(r"[^a-z0-9]", "", str(k or "").lower())
            normalized[nk] = v
        for nk in ("belgeno", "belgenumarasi", "faturano", "invoicenumber"):
            v = normalized.get(nk)
            if v is not None and str(v).strip():
                return str(v).strip()
        return ""

    @staticmethod
    def _portal_extract_ettn(d):
        if not isinstance(d, dict):
            return ""
        for k in ("ettn", "uuid", "faturaUuid", "fatura_uuid", "ettnId"):
            v = d.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        normalized = {}
        for k, v in d.items():
            nk = re.sub(r"[^a-z0-9]", "", str(k or "").lower())
            normalized[nk] = v
        for nk in ("ettn", "uuid", "faturauuid", "ettnid"):
            v = normalized.get(nk)
            if v is not None and str(v).strip():
                return str(v).strip()
        return ""

    @staticmethod
    def _portal_parse_any_date_to_iso(val):
        """GG/MM/YYYY, GG.AA.YYYY veya YYYY-MM-DD → YYYY-MM-DD."""
        if val is None:
            return None
        if hasattr(val, "strftime"):
            try:
                return val.strftime("%Y-%m-%d")
            except Exception:
                return None
        s_full = str(val).strip()
        if not s_full or s_full.lower() in ("null", "none", "-", "—"):
            return None
        s = s_full.split()[0].split("T")[0].strip()
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
        if m:
            g, a, y = m.group(1), m.group(2), m.group(3)
            return f"{y}-{int(a):02d}-{int(g):02d}"
        m2 = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", s)
        if m2:
            g, a, y = m2.group(1), m2.group(2), m2.group(3)
            return f"{y}-{int(a):02d}-{int(g):02d}"
        m3 = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", s)
        if m3:
            g, a, y = m3.group(1), m3.group(2), m3.group(3)
            return f"{y}-{int(a):02d}-{int(g):02d}"
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return s[:10]
        return None

    @staticmethod
    def _portal_fatura_tarihi_iso(d):
        """GİB liste satırından fatura/belge/düzenleme tarihini YYYY-MM-DD yapar."""
        if not isinstance(d, dict):
            return None
        for k in (
            "faturaTarihi",
            "faturaTar",
            "belgeTarihi",
            "duzenlemeTarihi",
            "duzenlenmeTarihi",
            "olusturmaTarihi",
            "tarih",
            "invoiceDate",
        ):
            iso = BestOfficeGIBManager._portal_parse_any_date_to_iso(d.get(k))
            if iso:
                return iso
        norm = {}
        for k, v in d.items():
            nk = re.sub(r"[^a-z0-9]", "", str(k).lower())
            norm[nk] = v
        for nk in (
            "faturatarihi",
            "belgetarihi",
            "duzenlenmetarihi",
            "olusturmatarihi",
            "islemtarihi",
        ):
            iso = BestOfficeGIBManager._portal_parse_any_date_to_iso(norm.get(nk))
            if iso:
                return iso
        # Anahtar adı bilinmeyen ama tarih içeren ilk skaler değeri dene.
        for v in d.values():
            if isinstance(v, (dict, list)):
                continue
            iso = BestOfficeGIBManager._portal_parse_any_date_to_iso(v)
            if iso:
                return iso
        return None

    @staticmethod
    def _portal_odenecek_tutar(d):
        if not isinstance(d, dict):
            return 0.0
        for k in (
            "odenecekTutar",
            "toplamTutar",
            "genelToplam",
            "vergilerDahilToplamTutar",
            "vergilerDahilToplam",
            "vergilerDahilTutar",
            "faturaTutari",
            "tutar",
            "toplam",
        ):
            if k in d and d.get(k) is not None and str(d.get(k)).strip() != "":
                t = BestOfficeGIBManager._parse_tr_amount_portal(d.get(k))
                if t != 0.0:
                    return round(t, 2)
        best = 0.0
        for k, v in d.items():
            if v is None or isinstance(v, (dict, list)):
                continue
            nk = re.sub(r"[^a-z0-9]", "", str(k).lower())
            if nk in ("kdvtutari", "kdvtutar", "hesaplanankdv", "matrah"):
                continue
            if any(x in nk for x in ("toplamtutar", "odenecektutar", "geneltoplam", "faturatutar", "vergilerdahil")):
                t = BestOfficeGIBManager._parse_tr_amount_portal(v)
                if t > best:
                    best = t
        # Son çare: satırdaki skaler değerlerde makul en büyük para değerini al.
        if best == 0.0:
            for k, v in d.items():
                if v is None or isinstance(v, (dict, list)):
                    continue
                nk = re.sub(r"[^a-z0-9]", "", str(k).lower())
                if any(x in nk for x in ("vkn", "tckn", "uuid", "ettn", "id", "belgeno", "belgenumarasi")):
                    continue
                t = BestOfficeGIBManager._parse_tr_amount_portal(v)
                if t > best and t < 10_000_000:
                    best = t
        return round(best, 2) if best else 0.0

    @staticmethod
    def _portal_row_portal_raporunda_goster(d):
        """İade hariç anlamlı satırlar (taslak + imzalı)."""
        if not isinstance(d, dict) or not d:
            return False
        if str(d.get("faturaTipi") or "SATIS").strip().upper() == "IADE":
            return False
        et = BestOfficeGIBManager._portal_extract_ettn(d)
        bn = BestOfficeGIBManager._portal_extract_belge_no(d)
        unv = (d.get("aliciUnvanAdSoyad") or d.get("aliciUnvan") or "").strip()
        if not et and not bn and not unv:
            return False
        return True

    @staticmethod
    def _portal_row_gib_kesinlik(d):
        """imzalı | taslak | iptal (küçük harf)."""
        if not isinstance(d, dict):
            return "taslak"
        norm = {}
        for k, v in d.items():
            nk = re.sub(r"[^a-z0-9]", "", str(k or "").lower())
            norm[nk] = v

        # Portal bazen iptal bilgisini ayrı sütun yerine metin alanında döner.
        for v in d.values():
            if isinstance(v, str) and 8 < len(v) < 900:
                if re.search(r"iptal\s+edilm", v, flags=re.IGNORECASE):
                    return "iptal"

        # Öncelik 1: İptal/itiraz alanında çarpı (x/×/✖/❌) veya iptal metni varsa -> İPTAL.
        iptal_val = (
            norm.get("iptalitirazdurumu")
            or norm.get("iptaldurumu")
            or norm.get("iptal")
            or ""
        )
        iptal_s = str(iptal_val).strip().lower()
        if (
            "iptal" in iptal_s
            or "i̇ptal" in iptal_s
            or "red" in iptal_s
            or "itiraz" in iptal_s
            or any(ch in str(iptal_val) for ch in ("✖", "❌", "×", "x", "X"))
        ):
            return "iptal"
        # Bazı portal cevaplarında "İptal/İtiraz Durumu" alanı metin yerine kod döner:
        # iptalItiraz=0 ve talepDurum=1 => "İptal Kabul Edildi" (resmi iptal).
        iptal_kod_raw = (
            norm.get("iptalitiraz")
            or norm.get("iptalitirazdurumu")
            or ""
        )
        iptal_kod = str(iptal_kod_raw).strip().lower()
        talep_raw = norm.get("talepdurum") or ""
        talep = str(talep_raw).strip().lower()
        if (
            iptal_kod in ("0", "iptal kabul edildi", "kabul edildi", "kabul")
            and talep in ("1", "var", "true", "evet")
        ):
            return "iptal"

        # Öncelik 2: Onay sütununda tik (✓/✔/☑) veya onaylandı metni varsa -> İMZALI.
        # «Onaylı» anahtarı normalize edilince ı harfi düştüğü için «onayl» olur; «onayli» ile eşleşmezdi.
        # Genel «durum» alanına düşmeyin: portal çoğu satırda 1 vb. kod döndürüp hepsini imzalı saydırıyordu.
        onay_val = (
            norm.get("onayli")
            or norm.get("onayl")
            or norm.get("onaydurumu")
            or norm.get("onaydurum")
            or norm.get("onayflg")
            or norm.get("onayflag")
            or ""
        )
        onay_s = str(onay_val).strip().lower()
        if (
            any(ch in str(onay_val) for ch in ("✓", "✔", "☑"))
            or ("onaylan" in onay_s and "onaylanmad" not in onay_s)
            or onay_s in ("1", "true", "evet")
        ):
            return "imzalı"

        # Öncelik 3: Onay sütununda daire/çizgili daire veya onaylanmadı metni -> TASLAK.
        if (
            "onaylanmad" in onay_s
            or "taslak" in onay_s
            or any(ch in str(onay_val) for ch in ("⊘", "⦸", "◯", "○", "Ø", "ø"))
            or onay_s in ("0", "false", "hayir")
        ):
            return "taslak"

        # Bilinmeyen durumda güvenli varsayılan: taslak (imzalıyı fazla göstermemek için).
        return "taslak"

    def _portal_row_normalized_rapor(self, d):
        """Finans GİB raporu API satırı (id yok; route ERP ile birleştirir)."""
        unvan = (
            (d.get("aliciUnvanAdSoyad") or d.get("aliciUnvan") or "").strip()
        )
        if not unvan:
            ad = (d.get("aliciAdi") or "").strip()
            soy = (d.get("aliciSoyadi") or "").strip()
            unvan = (f"{ad} {soy}".strip()) or "—"
        bn = self._portal_extract_belge_no(d)
        et = self._portal_extract_ettn(d)
        iso = self._portal_fatura_tarihi_iso(d)
        if not iso:
            iso = ""
        tut = self._portal_odenecek_tutar(d)
        kes = self._portal_row_gib_kesinlik(d)
        gib_etiket = {"imzalı": "İmzalı", "taslak": "Taslak", "iptal": "İptal"}.get(kes, "Taslak")
        return {
            "id": None,
            "fatura_tarihi": iso,
            "fatura_no": bn,
            "ettn": et,
            "musteri_adi": unvan,
            "tutar": tut,
            "kaynak": "gib_portal",
            "gib_durum": gib_etiket,
            "gib_kesinlik": kes,
        }

    def _portal_taslaklari_data_raw(self, bas_s: str, bit_s: str, hangi_tip: str):
        """
        TASLAKLARI_GETIR cevabını JSON’daki gibi ham dict listesi olarak alır
        (eArsivPortal’ın create_model ile bazı anahtarları budaması riskine karşı).
        """
        self._ensure_client()
        try:
            resp = self._client_dispatch(
                "EARSIV_PORTAL_TASLAKLARI_GETIR",
                "RG_BASITTASLAKLAR",
                {
                    "baslangic": bas_s,
                    "bitis": bit_s,
                    "hangiTip": hangi_tip or "5000/30000",
                    "table": [],
                },
            )
        except Exception as e:
            _log.warning("GİB TASLAKLARI_GETIR (%s–%s tip=%s): %s", bas_s, bit_s, hangi_tip, e)
            return []
        if not isinstance(resp, dict):
            return []
        data = resp.get("data")
        if data is None:
            return []
        if isinstance(data, dict):
            inner = (
                data.get("fatura")
                or data.get("faturalar")
                or data.get("liste")
                or data.get("rows")
            )
            if isinstance(inner, list):
                data = inner
            elif inner is None and (data.get("ettn") or data.get("belgeNo") or data.get("belgeNumarasi")):
                return [data]
            else:
                return []
        if not isinstance(data, list):
            return []
        out = []
        for row in data:
            if isinstance(row, dict):
                out.append(row)
            else:
                d = self._to_dict(row)
                if d:
                    out.append(d)
        return out

    @_retry_on_connection(max_attempts=3, delay=2.0)
    def portal_kesilen_fatura_listesi_normalized(self, bas_date, bit_date):
        """
        GİB TASLAKLARI_GETIR ile kesinleşmiş satış satırlarını çeker; tarih filtresi
        portalın kullandığı alan ERP’den farklı olabileceği için sorgu aralığı genişletilir,
        sonuç kullanıcının seçtiği fatura tarihine göre süzülür. Uzun aralıklar günlük dilimlerle taranır.
        """
        from datetime import date as date_cls
        from datetime import timedelta

        ttl = _portal_kesilen_list_cache_ttl_saniye()
        cache_key = _portal_kesilen_list_cache_key(bas_date, bit_date)
        if ttl > 0:
            with _portal_kesilen_list_cache_lock:
                ent = _portal_kesilen_list_cache.get(cache_key)
                if ent:
                    ts, data = ent
                    if time.monotonic() - ts < ttl:
                        return [dict(x) for x in data]

        self._ensure_client()
        self._fresh_login()

        try:
            pad_before = int(os.getenv("GIB_PORTAL_LISTE_GUN_ONCE", "62").strip() or "62")
        except ValueError:
            pad_before = 62
        try:
            pad_after = int(os.getenv("GIB_PORTAL_LISTE_GUN_SONRA", "14").strip() or "14")
        except ValueError:
            pad_after = 14
        try:
            chunk_days = int(os.getenv("GIB_PORTAL_LISTE_CHUNK_GUN", "8").strip() or "8")
        except ValueError:
            chunk_days = 8
        if chunk_days < 1:
            chunk_days = 1

        ht_raw = (os.getenv("GIB_PORTAL_LISTE_HANGI_TIP") or "5000/30000").strip()
        hangi_tips = [t.strip() for t in ht_raw.split("|") if t.strip()] or ["5000/30000"]

        bas_e = bas_date - timedelta(days=max(0, pad_before))
        bit_e = bit_date + timedelta(days=max(0, pad_after))
        today = date_cls.today()
        if bit_e > today + timedelta(days=2):
            bit_e = today + timedelta(days=2)

        seen_bn = set()
        seen_et = set()
        merged_raw = []
        cur = bas_e
        while cur <= bit_e:
            chunk_end = min(cur + timedelta(days=chunk_days - 1), bit_e)
            bas_s = cur.strftime("%d/%m/%Y")
            bit_s = chunk_end.strftime("%d/%m/%Y")
            for ht in hangi_tips:
                for d in self._portal_taslaklari_data_raw(bas_s, bit_s, ht):
                    if not isinstance(d, dict):
                        continue
                    bn = self._portal_extract_belge_no(d).strip().upper()
                    et = self._portal_extract_ettn(d).strip().lower()
                    if bn and bn in seen_bn:
                        continue
                    if et and et in seen_et:
                        continue
                    if bn:
                        seen_bn.add(bn)
                    if et:
                        seen_et.add(et)
                    merged_raw.append(d)
                time.sleep(0.22)
            cur = chunk_end + timedelta(days=1)

        items = []
        seen_out = set()
        for d in merged_raw:
            if not d or not self._portal_row_portal_raporunda_goster(d):
                continue
            it = self._portal_row_normalized_rapor(d)
            fn = (it.get("fatura_no") or "").strip().upper()
            et = (it.get("ettn") or "").strip().lower()
            iso = (it.get("fatura_tarihi") or "").strip()
            if iso:
                try:
                    inv_d = date_cls.fromisoformat(iso[:10])
                    if inv_d < bas_date or inv_d > bit_date:
                        continue
                except ValueError:
                    pass
            key = fn or et
            if not key:
                continue
            if key in seen_out:
                continue
            seen_out.add(key)
            items.append(it)
        if ttl > 0:
            with _portal_kesilen_list_cache_lock:
                _portal_kesilen_list_cache[cache_key] = (time.monotonic(), [dict(x) for x in items])
        return items

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

    def _fatura_taslak_dispatch_hazirlik(self, fatura_data):
        """
        fatura_taslak_olustur ile aynı çok satırlı / dinamik fatura_ver sarmalayıcısını kurar.
        Dönüş: payload, glb, orig_fatura_ver, dyn_fn (patch sonrası fatura_olustur ile uyumlu).
        """
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
        urun_adi = _gib_mal_hizmet_metin(str(first.get("name") or fatura_data.get("hizmet_adi") or "Hizmet"))
        fiyat = float(first.get("unit_price") or fatura_data.get("birim_fiyat") or fatura_data.get("toplam") or 0)
        kdv_orani = int(first.get("tax_rate") or fatura_data.get("kdv_orani") or 20)
        # eArsivPortal'ın içindeki fatura_ver fonksiyonu bazı sürümlerde %20'ye sabit.
        # Bu yüzden fatura_olustur çağrısı öncesi payload üretimini dinamik KDV/fiyat ile override ediyoruz.
        fatura_method = getattr(self.client, "fatura_olustur")
        fn = getattr(fatura_method, "__func__", fatura_method)
        glb = getattr(fn, "__globals__", {})
        # Her zaman Libs'teki ham fatura_ver ile sarmala; glb["fatura_ver"] önceki taslaktan
        # dinamik sarmalayıcı kalırsa zincir / yanlış closure oluşabiliyor.
        try:
            from eArsivPortal.Libs.FaturaVer import fatura_ver as orig_fatura_ver
        except Exception:
            orig_fatura_ver = None
        if not callable(orig_fatura_ver):
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
                nm = _gib_mal_hizmet_metin(it.get("name") or "Hizmet")
                qty = float(it.get("quantity") or 1.0)
                if qty <= 0:
                    qty = 1.0
                up = float(it.get("unit_price") or 0.0)
                if up < 0:
                    up = 0.0
                # GIB tarafinda 0 tutarli satirlar taslak olusumunu bozabiliyor.
                if round(up * qty, 2) <= 0:
                    continue
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
                    unit_net = (r["matrah"] / r["quantity"]) if r["quantity"] > 0 else r["matrah"]
                    qty = float(r["quantity"] or 1.0)
                    if qty > 0 and abs(qty - round(qty)) < 1e-9 and qty < 1e9:
                        miktar_val = int(round(qty))
                    else:
                        miktar_val = round(qty, 4)
                    mal_tablo.append({
                        "malHizmet": _gib_mal_hizmet_metin(r["name"]),
                        "miktar": miktar_val,
                        "birim": "C62",
                        "birimFiyat": f"{unit_net:.2f}",
                        "fiyat": f"{unit_net:.2f}",
                        "iskontoOrani": 0,
                        "iskontoTutari": "0",
                        "iskontoNedeni": "",
                        "malHizmetTutari": f"{r['matrah']:.2f}",
                        "kdvOrani": str(int(r["tax_rate"])),
                        "vergiOrani": 0,
                        "kdvTutari": f"{r['kdv']:.2f}",
                        "vergininKdvTutari": "0",
                        "ozelMatrahTutari": "0",
                        "hesaplananotvtevkifatakatkisi": "0",
                    })
                d["malHizmetTable"] = mal_tablo
                d["matrah"] = f"{toplam_matrah:.2f}"
                # Portal şablonu: malhizmet toplamı = satır matrahları toplamı (iskonto sonrası), brüt değil.
                d["malhizmetToplamTutari"] = f"{toplam_matrah:.2f}"
                d["toplamIskonto"] = "0.00"
                d["tip"] = "İskonto"
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

        return {
            "payload": payload,
            "glb": glb,
            "orig_fatura_ver": orig_fatura_ver,
            "dyn_fn": _fatura_ver_dynamic,
        }

    def _gib_dispatch_jp_sentezle(self, payload):
        """fatura_olustur / _bestoffice ile aynı mantıkla portal jp sözlüğünü üretir (dispatch çağrılmaz)."""
        import sys
        import eArsivPortal.Core as _ep_core_mod
        from datetime import datetime as _dt_mod
        from pytz import timezone as _tz_tr

        kisi_bilgi = self.client.kisi_getir(payload["vkn_veya_tckn"])
        vkn_c = str(payload.get("vkn_veya_tckn") or "").replace(" ", "").strip()
        ka = str(getattr(kisi_bilgi, "adi", None) or "").strip()
        ks = str(getattr(kisi_bilgi, "soyadi", None) or "").strip()
        ku = str(getattr(kisi_bilgi, "unvan", None) or "").strip()
        ea = str(payload.get("ad") or "").strip()
        es = str(payload.get("soyad") or "").strip()
        eu = str(payload.get("unvan") or "").strip()
        if len(vkn_c) == 10:
            ad_i, soy_i, un_i = ea or ka, es or ks, eu or ku
        else:
            ad_i, soy_i, un_i = ka or ea, ks or es, ku or eu
        _gib_mod = sys.modules.get("gib_earsiv")
        fv = getattr(_gib_mod, "fatura_ver", None) if _gib_mod else None
        if not callable(fv):
            fv = _ep_core_mod.fatura_ver
        ornek_uuid = "00000000-0000-4000-8000-000000000001"
        fatura = fv(
            tarih=payload.get("tarih") or _dt_mod.now(_tz_tr("Turkey")).strftime("%d/%m/%Y"),
            saat=payload.get("saat") or "12:00:00",
            para_birimi="TRY",
            vkn_veya_tckn=payload["vkn_veya_tckn"],
            ad=ad_i,
            soyad=soy_i,
            unvan=un_i,
            vergi_dairesi=kisi_bilgi.vergiDairesi or (payload.get("vergi_dairesi") or ""),
            urun_adi=payload.get("urun_adi") or "Hizmet",
            fiyat=payload.get("fiyat") or 0,
            fatura_notu=payload.get("fatura_notu") or "",
        )
        fatura["faturaUuid"] = ornek_uuid
        if len(vkn_c) == 10 and un_i:
            fatura["aliciUnvan"] = un_i[:255]
        _gib_normalize_alici_for_portal(fatura, payload["vkn_veya_tckn"])
        return _gib_dispatch_fatura_jp(fatura)

    def gib_dispatch_jp_onizle(self, fatura_data):
        """
        GİB'e POST edilen `jp` gövdesinin analizi (earsiv-services/dispatch içindeki JSON).
        Taslak oluşturulmaz; yalnızca MERNIS + fatura_ver + çok satırlı tablo ile aynı yolu izler.
        """
        self._ensure_client()
        self._fresh_login()
        h = self._fatura_taslak_dispatch_hazirlik(fatura_data)
        glb = h["glb"]
        orig = h["orig_fatura_ver"]
        dyn = h["dyn_fn"]
        payload = h["payload"]
        glb["fatura_ver"] = dyn
        try:
            return self._gib_dispatch_jp_sentezle(payload)
        finally:
            glb["fatura_ver"] = orig

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
            self._fresh_login()
        except Exception as e:
            err = str(e).lower()
            if "geçersiz kullanıcı" in err or "invalid" in err or "yetkisiz" in err or "kullanıcı adı" in err:
                raise RuntimeError("GİB geçersiz kullanıcı veya şifre. Lütfen GIB_USER ve GIB_PASS bilgilerinizi kontrol edin.") from e
            raise

        h = self._fatura_taslak_dispatch_hazirlik(fatura_data)
        glb = h["glb"]
        orig_fatura_ver = h["orig_fatura_ver"]
        _fatura_ver_dynamic = h["dyn_fn"]
        payload = h["payload"]
        glb["fatura_ver"] = _fatura_ver_dynamic
        try:
            import time as _time_dbg
            _t0 = _time_dbg.time()
            print(f"[GİB DEBUG] fatura_olustur başlıyor... payload keys={list(payload.keys())}")
            out = self.client.fatura_olustur(**payload)
            print(f"[GİB DEBUG] fatura_olustur bitti, süre={_time_dbg.time()-_t0:.1f}s")
        finally:
            glb["fatura_ver"] = orig_fatura_ver
        self.last_taslak_raw = out
        d = self._to_dict(out)
        uuid = str(d.get("ettn") or d.get("uuid") or "").strip()
        if not uuid:
            self.last_sms_error = "GİB taslak yanıtında ETTN/UUID bulunamadı."
            return None
        try:
            _uuid_stdlib.UUID(uuid)
        except Exception:
            self.last_sms_error = f"GİB yanıtında geçersiz ETTN/UUID biçimi: {uuid[:64]!r}"
            return None

        _liste_dogrula = (os.getenv("GIB_TASLAK_LISTE_DOGRULA") or "").strip().lower() in (
            "1", "true", "evet", "yes",
        )
        if not _liste_dogrula:
            self.last_sms_error = None
            return uuid
        try:
            _, dogrulama = self._find_fatura_by_uuid(uuid, days_back=5, force_new_session=False)
            if dogrulama:
                self.last_sms_error = None
                return dogrulama.get("ettn") or dogrulama.get("uuid") or uuid
            self.last_sms_error = "Taslak oluşturuldu fakat son 5 günlük listede ETTN doğrulanamadı."
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
                    satir_mal_ad = ad_raw or "Hizmet"
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
                        "name": satir_mal_ad,
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
