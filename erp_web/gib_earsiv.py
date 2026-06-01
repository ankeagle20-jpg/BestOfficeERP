# -*- coding: utf-8 -*-
"""
BestOfficeERP – GİB e-Arşiv Fatura Modülü

Fatura verilerini veritabanından çeker, GİB'e taslak oluşturur ve SMS onay sürecini yönetir.
.env içinde GIB_USER ve GIB_PASS tanımlı olmalı.

Kütüphane: `from fatura import Client` kullanılır.
Alternatif: pip install eArsivPortal ile farklı API kullanılabilir (adaptör gerekir).
"""

import os
import sys
import re
import time
import json
import logging
import threading
from dotenv import load_dotenv

_log = logging.getLogger(__name__)

load_dotenv()

_gib_file_log_lock = threading.Lock()


def _gib_trace_file_line(text: str) -> None:
    """GIB_TASLAK_TRACE=1 iken yalnız GİB aşama satırları (dev_http şişmesin)."""
    if (os.getenv("GIB_TASLAK_TRACE") or "").strip().lower() not in ("1", "true", "evet"):
        return
    line = text if str(text).endswith("\n") else f"{text}\n"
    erp_web = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(erp_web, "gib_taslak_trace.log")
    try:
        with _gib_file_log_lock:
            with open(path, "a", encoding="utf-8", errors="replace") as lf:
                lf.write(line)
    except Exception:
        pass


def _gib_dev_http_file_line(text: str) -> None:
    """İş parçacığında da çalışır; Flask request / app.py kancasından bağımsız dev_http.log satırı."""
    line = text if str(text).endswith("\n") else f"{text}\n"
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except Exception:
        pass
    raw = (os.environ.get("BESTOFFICE_DEV_HTTP_LOG") or "").strip()
    erp_web = os.path.dirname(os.path.abspath(__file__))
    path = os.path.abspath(raw) if raw else os.path.join(erp_web, "dev_http.log")
    try:
        with _gib_file_log_lock:
            with open(path, "a", encoding="utf-8", errors="replace") as lf:
                lf.write(line)
    except Exception:
        pass


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
        self.last_taslak_liste_satir = None
        self.last_gonderilen_payload = None
        self.last_gib_asama_izle: list = []
        self.init_error = None
        try:
            if _HAS_EARSIV_PORTAL and self.username and self.password:
                # eArsivPortal test_modu=True iken test ortamını kullanır.
                self.client = EArsivPortalClient(self.username, self.password, test_modu=bool(self.test_mode))
                self.client_type = "earsivportal"
                self._portal_compat_shim()
        except Exception as e:
            self.init_error = str(e)
            self.client = None
            self.client_type = None

    def is_available(self):
        """GİB kütüphanesi ve kimlik bilgileri hazır mı."""
        return self.client is not None

    def _gib_asama(self, adim: str, detay: str | None = None) -> None:
        """Taslak/SMS akışında API ve isteğe bağlı dosyaya adım kaydı (tıkanma teşhisi)."""
        try:
            det = (detay or "")[:800]
            entry = {"ts_ms": int(time.time() * 1000), "adim": str(adim or "")[:120], "detay": det}
            if not hasattr(self, "last_gib_asama_izle") or self.last_gib_asama_izle is None:
                self.last_gib_asama_izle = []
            self.last_gib_asama_izle.append(entry)
            if len(self.last_gib_asama_izle) > 120:
                self.last_gib_asama_izle = self.last_gib_asama_izle[-120:]
        except Exception:
            pass
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            _gib_trace_file_line(f"{ts}\t{adim}\t{detay or ''}")
        except Exception:
            pass

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
            self._portal_install_kod_calistir_ettn_patch(c)
        except Exception:
            pass

    def _portal_install_kod_calistir_ettn_patch(self, client):
        """Dispatch öncesi FATURA_OLUSTUR: yeni taslakta faturaUuid boş (GİB Mayıs 2026)."""
        orig = getattr(client, "_eArsivPortal__kod_calistir", None)
        if not callable(orig) or getattr(orig, "_gib_ettn_patch", False):
            return

        def _wrapped(komut=None, jp=None, **kwargs):
            cmd = getattr(komut, "cmd", None) if komut is not None else None
            jp_in = jp if jp is not None else kwargs.get("jp")
            if cmd == "EARSIV_PORTAL_FATURA_OLUSTUR" and isinstance(jp_in, dict):
                jp_fix = BestOfficeGIBManager._portal_fatura_jp_olustur_duzelt(dict(jp_in))
                if jp is not None:
                    jp = jp_fix
                else:
                    kwargs["jp"] = jp_fix
            if jp is not None:
                return orig(komut=komut, jp=jp, **kwargs)
            return orig(komut=komut, **kwargs)

        _wrapped._gib_ettn_patch = True
        client._eArsivPortal__kod_calistir = _wrapped
        _gib_dev_http_file_line("[GİB] kod_calistir faturaUuid-bos yaması aktif (yeni taslak)")

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
        self._gib_asama("fresh_login_basla")
        self._portal_logout()
        self._portal_login()
        self._gib_asama("fresh_login_tamam")

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
        try:
            fields = getattr(val, "__fields__", None) or getattr(val, "model_fields", None)
            if fields:
                snap = {}
                for fn in fields:
                    snap[fn] = getattr(val, fn, None)
                if snap:
                    return snap
        except Exception:
            pass
        return {}

    @staticmethod
    def ettn_from_out(out) -> str:
        """FaturaOlustur (Pydantic) / dict / str → ETTN metni; asla model nesnesi dönmez."""
        if out is None:
            return ""
        if isinstance(out, str):
            s = out.strip()
            return s if BestOfficeGIBManager._portal_gecerli_ettn(s) else ""
        d = BestOfficeGIBManager._to_dict(out)
        for k in ("ettn", "uuid", "faturaUuid", "fatura_uuid"):
            v = str(d.get(k) or "").strip()
            if BestOfficeGIBManager._portal_gecerli_ettn(v):
                return v
        for k in ("ettn", "uuid", "faturaUuid"):
            try:
                v = getattr(out, k, None)
                if v is not None:
                    s = str(v).strip()
                    if BestOfficeGIBManager._portal_gecerli_ettn(s):
                        return s
            except Exception:
                pass
        return ""

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
    def _portal_gecerli_ettn(val):
        s = str(val or "").strip()
        return len(s) == 36 and "-" in s

    @staticmethod
    def _portal_vkn_digits(val) -> str:
        return re.sub(r"\D", "", str(val or "").strip())

    @staticmethod
    def _portal_row_vkn(d) -> str:
        """TASLAKLARI_GETIR satırından alıcı VKN/TCKN (rakam)."""
        if not isinstance(d, dict):
            return ""
        for k in (
            "aliciVknTckn",
            "aliciVkn",
            "aliciTckn",
            "vknTckn",
            "vkn",
            "tckn",
            "kimlikNo",
            "vergiNo",
        ):
            v = d.get(k)
            if v is not None and str(v).strip():
                return BestOfficeGIBManager._portal_vkn_digits(v)
        return ""

    @staticmethod
    def _portal_belge_serial(belge_no: str) -> int:
        m = re.search(r"(\d{9})$", str(belge_no or "").strip(), re.IGNORECASE)
        return int(m.group(1)) if m else 0

    @staticmethod
    def _portal_ettn_istekten(istek) -> str:
        """FATURA_OLUSTUR yanıt gövdesinde geçen geçerli UUID'leri tara."""
        found = []

        def walk(o):
            if isinstance(o, dict):
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for x in o:
                    walk(x)
            else:
                s = str(o or "").strip()
                if BestOfficeGIBManager._portal_gecerli_ettn(s):
                    found.append(s)

        walk(istek)
        return found[-1] if found else ""

    def _portal_son_taslak_ettn_bul(
        self,
        vkn,
        tarih,
        tutar=None,
        belge_no=None,
    ):
        """
        TASLAKLARI_GETIR ile VKN + tarih eşleşen en güncel taslağın ETTN'sini döndürür.
        GİB listesinde alan adı aliciVknTckn (vknTckn değil).
        """
        vkn_d = self._portal_vkn_digits(vkn)
        tarih_s = str(tarih or "").strip()
        if not vkn_d or not tarih_s:
            return ""
        try:
            bekle_ms = int(str(os.getenv("GIB_ETTN_LISTE_BEKLE_MS") or "800").strip() or "800")
        except ValueError:
            bekle_ms = 800
        if bekle_ms > 0:
            time.sleep(min(bekle_ms, 5000) / 1000.0)

        ht_raw = (os.getenv("GIB_PORTAL_LISTE_HANGI_TIP") or "5000/30000").strip()
        hangi_tips = [t.strip() for t in ht_raw.split("|") if t.strip()] or ["5000/30000"]

        rows = []
        for ht in hangi_tips:
            try:
                rows.extend(self._portal_taslaklari_data_raw(tarih_s, tarih_s, ht) or [])
            except Exception as ex:
                self._gib_asama("ettn_taslak_liste_ex", str(ex)[:160])

        if not rows:
            try:
                for r in self.client.faturalari_getir(baslangic_tarihi=tarih_s, bitis_tarihi=tarih_s) or []:
                    d = self._to_dict(r)
                    if d:
                        rows.append(d)
            except Exception as ex:
                self._gib_asama("ettn_faturalari_getir_ex", str(ex)[:160])

        tutar_hedef = None
        if tutar is not None:
            try:
                tutar_hedef = round(float(tutar), 2)
            except (TypeError, ValueError):
                tutar_hedef = None

        belge_hedef = str(belge_no or "").strip().upper()
        candidates = []
        for d in rows:
            if self._portal_row_vkn(d) != vkn_d:
                continue
            et = self._portal_extract_ettn(d)
            if not self._portal_gecerli_ettn(et):
                continue
            bn = self._portal_extract_belge_no(d)
            if belge_hedef and bn.upper() != belge_hedef:
                continue
            if tutar_hedef is not None:
                row_tut = round(self._portal_odenecek_tutar(d), 2)
                if row_tut > 0 and abs(row_tut - tutar_hedef) > 0.05:
                    continue
            candidates.append((self._portal_belge_serial(bn), et, bn, d))

        if not candidates:
            self._gib_asama(
                "ettn_taslak_yok",
                f"vkn={vkn_d[-4:]} tarih={tarih_s} satir={len(rows)}",
            )
            return ""

        candidates.sort(key=lambda x: x[0], reverse=True)
        serial, et, bn, row = candidates[0]
        self.last_taslak_liste_satir = row
        self._gib_asama("ettn_taslak_bulundu", f"{bn} ettn={et[:36]}")
        return et

    @staticmethod
    def _portal_fatura_jp_olustur_duzelt(jp, mevcut_ettn=None, mevcut_belge_no=None):
        """
        GİB FATURA_OLUSTUR jp düzenlemesi (Mayıs 2026 portal değişikliği).
        Yeni taslak: faturaUuid ve ettn BOŞ — dolu UUID gönderilirse «Ettn eksik» hatası döner.
        Güncelleme: mevcut ETTN + belge numarası ile gönderilir.
        """
        if not isinstance(jp, dict):
            return jp
        guncelle = BestOfficeGIBManager._portal_gecerli_ettn(mevcut_ettn)
        if guncelle:
            uid = str(mevcut_ettn).strip()
            jp["faturaUuid"] = uid
            jp["ettn"] = uid
            bn = str(mevcut_belge_no or jp.get("belgeNumarasi") or "").strip()
            if bn:
                jp["belgeNumarasi"] = bn
        else:
            # Yeni taslak: UUID ve belge no boş — GİB atar (ERP'deki GIB2026… ön atama gönderilmez).
            jp["faturaUuid"] = ""
            jp.pop("ettn", None)
            jp["belgeNumarasi"] = ""
        return jp

    @staticmethod
    def _portal_fatura_olustur_sayfa():
        """Yeni taslak: RG_BASITFATURA + boş faturaUuid (GİB Mayıs 2026). .env ile override."""
        page_env = (os.getenv("GIB_FATURA_OLUSTUR_PAGE") or "").strip()
        if page_env:
            return page_env
        return "RG_BASITFATURA"

    def _portal_ettn_basari_sonrasi(self, istek, payload, fatura_jp):
        """Taslak oluşturma başarılı; GİB yanıtı veya TASLAKLARI_GETIR listesinden ETTN bul."""
        et = self._portal_ettn_istekten(istek)
        if et:
            return et
        if isinstance(istek, dict):
            data = istek.get("data")
            if isinstance(data, dict):
                et = self._portal_extract_ettn(data)
                if et:
                    return et
        vkn = str(
            (payload or {}).get("vkn_veya_tckn")
            or (fatura_jp or {}).get("vknTckn")
            or ""
        ).strip()
        tarih = str(
            (payload or {}).get("tarih")
            or (fatura_jp or {}).get("faturaTarihi")
            or ""
        ).strip()
        tutar = (payload or {}).get("toplam") or (payload or {}).get("odenecekTutar")
        belge = (payload or {}).get("belge_no") or (fatura_jp or {}).get("belgeNumarasi")
        return self._portal_son_taslak_ettn_bul(vkn, tarih, tutar=tutar, belge_no=belge)

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
        uid = str(uuid or "").strip().lower()
        if not uid:
            return None, None
        bugun = datetime.now().date()
        bas_d = bugun - timedelta(days=max(7, int(days_back or 370)))
        bit_d = bugun + timedelta(days=1)
        ht_raw = (os.getenv("GIB_PORTAL_LISTE_HANGI_TIP") or "5000/30000").strip()
        hangi_tips = [t.strip() for t in ht_raw.split("|") if t.strip()] or ["5000/30000"]
        cur = bas_d
        while cur <= bit_d:
            chunk_end = min(cur + timedelta(days=14), bit_d)
            bas_s = cur.strftime("%d/%m/%Y")
            bit_s = chunk_end.strftime("%d/%m/%Y")
            for ht in hangi_tips:
                for d in self._portal_taslaklari_data_raw(bas_s, bit_s, ht):
                    et = self._portal_extract_ettn(d).strip().lower()
                    if et == uid:
                        return d, d
            cur = chunk_end + timedelta(days=1)
        bas = bas_d.strftime("%d/%m/%Y")
        bit = bit_d.strftime("%d/%m/%Y")
        rows = self.client.faturalari_getir(baslangic_tarihi=bas, bitis_tarihi=bit) or []
        for r in rows:
            d = self._to_dict(r)
            if self._portal_extract_ettn(d).strip().lower() == uid:
                return r, d
        return None, None

    def max_gib_belge_serial_for_year(self, yil: int):
        """
        GİB listesinde GIByyyy######### (16 karakter) biçimindeki en büyük 9 haneli sıra.
        Portal çağrısı başarısızsa None; liste boş veya eşleşme yoksa 0.
        """
        try:
            y = int(yil)
        except (TypeError, ValueError):
            return None
        try:
            self._ensure_client()
            self._fresh_login()
            bas = f"01/01/{y}"
            bit = f"31/12/{y}"
            rows = self.client.faturalari_getir(baslangic_tarihi=bas, bitis_tarihi=bit) or []
        except Exception:
            return None
        mx = 0
        pat = re.compile(rf"^GIB{y}(\d{{9}})$", re.IGNORECASE)
        for r in rows:
            try:
                d = self._to_dict(r)
                bn = str(self._portal_extract_belge_no(d) or "").strip()
                m = pat.match(bn)
                if not m:
                    continue
                mx = max(mx, int(m.group(1)))
            except Exception:
                continue
        return mx

    def _fatura_olustur_earsivportal_bounded(self, client, payload, fatura_ver_fn, mevcut_ettn=None, mevcut_belge_no=None):
        """
        eArsivPortal.fatura_olustur ile aynı iş (kisi_getir + fatura_ver + dispatch),
        ancak kütüphanedeki «while True» sonsuz döngüsü yok — portal yanıtı döngüye girerse
        GIB_FATURA_OLUSTUR_MAX_DENEME sonunda net hata döner (api_gib_taslak iş parçacığı 120 sn'de takılı kalmaz).
        """
        self._gib_asama("bounded_giris", f"max_d={os.getenv('GIB_FATURA_OLUSTUR_MAX_DENEME') or '14'}")
        _gib_dev_http_file_line("[GİB] fatura_olustur_bounded giriş")
        kod_calistir = getattr(client, "_eArsivPortal__kod_calistir", None)
        nesne_ver = getattr(client, "_eArsivPortal__nesne_ver", None)
        if not callable(kod_calistir) or not callable(nesne_ver):
            raise RuntimeError("eArsivPortal istemcisi dispatch (kod_calistir) veya nesne_ver bulunamadı.")
        try:
            from eArsivPortal.Models.Komutlar import Komut
        except Exception as ex:
            raise RuntimeError("eArsivPortal Komut modeli yüklenemedi.") from ex

        km = client.komutlar
        try:
            max_d = int(str(os.getenv("GIB_FATURA_OLUSTUR_MAX_DENEME") or "14").strip() or "14")
        except ValueError:
            max_d = 14
        max_d = max(1, min(40, max_d))
        page = self._portal_fatura_olustur_sayfa()
        kom = Komut(cmd=km.FATURA_OLUSTUR.cmd, sayfa=page)
        _gib_dev_http_file_line(f"[GİB] FATURA_OLUSTUR sayfa={page} (kütüphane varsayılanı={km.FATURA_OLUSTUR.sayfa})")

        vkn = str(payload.get("vkn_veya_tckn") or "").strip()
        self._gib_asama("kisi_getir_once", f"vkn_len={len(vkn)}")
        kisi_bilgi = client.kisi_getir(vkn)
        self._gib_asama("kisi_getir_sonra", "ok")

        def _k(attr, pl_key, default=""):
            v = getattr(kisi_bilgi, attr, None)
            if v is not None and str(v).strip():
                return str(v).strip()
            return str(payload.get(pl_key) or default or "").strip()

        ad_m = _k("adi", "ad")
        soy_m = _k("soyadi", "soyad")
        unv_m = _k("unvan", "unvan")
        vd_m = _k("vergiDairesi", "vergi_dairesi")

        try:
            from datetime import datetime
            from pytz import timezone as _tz

            tarih_default = datetime.now(_tz("Turkey")).strftime("%d/%m/%Y")
        except Exception:
            from datetime import datetime

            tarih_default = datetime.now().strftime("%d/%m/%Y")
        tarih_use = str(payload.get("tarih") or "").strip() or tarih_default
        saat_use = str(payload.get("saat") or "12:00:00").strip()
        para = str(payload.get("para_birimi") or "TRY").strip() or "TRY"

        last_data_snip = ""
        for attempt in range(max_d):
            self._gib_asama("portal_fatura_deneme", f"{attempt + 1}/{max_d} page={page}")
            _gib_dev_http_file_line(f"[GİB] FATURA_OLUSTUR deneme {attempt + 1}/{max_d} page={page}")
            fatura = fatura_ver_fn(
                tarih=tarih_use,
                saat=saat_use,
                para_birimi=para,
                vkn_veya_tckn=vkn,
                ad=ad_m,
                soyad=soy_m,
                unvan=unv_m,
                vergi_dairesi=vd_m,
                urun_adi=payload.get("urun_adi") or "Hizmet",
                fiyat=payload.get("fiyat") or 0,
                fatura_notu=payload.get("fatura_notu") or "",
            )
            if isinstance(fatura, dict):
                fatura = self._portal_fatura_jp_olustur_duzelt(fatura, mevcut_ettn, mevcut_belge_no)
                _gib_dev_http_file_line(
                    f"[GİB] jp faturaUuid={fatura.get('faturaUuid')!r} ettn={fatura.get('ettn', '<yok>')!r} belge={str(fatura.get('belgeNumarasi') or '')[:20]!r}"
                )
            try:
                istek = kod_calistir(komut=kom, jp=fatura)
            except Exception as ex_dispatch:
                _gib_dev_http_file_line(f"[GİB] FATURA_OLUSTUR dispatch istisna: {ex_dispatch!r}")
                self._gib_asama("portal_dispatch_istisna", str(ex_dispatch)[:400])
                raise
            if not isinstance(istek, dict):
                last_data_snip = repr(istek)[:900]
                continue
            data = istek.get("data")
            if isinstance(data, str):
                blob = data
            elif data is not None:
                try:
                    blob = json.dumps(data, ensure_ascii=False, default=str)
                except Exception:
                    blob = str(data)
            else:
                blob = ""
            blob_l = blob.lower() if blob else ""
            basarili = blob and (
                "faturanız başarıyla oluşturulmuştur" in blob_l
                or "basariyla olusturulmustur" in blob_l
                or "basariyla oluşturulmuştur" in blob_l
                or "düzenlenen belgeler menüsünden" in blob_l
                or "duzenlenen belgeler menusunden" in blob_l
            )
            if basarili:
                ettn = self._portal_ettn_basari_sonrasi(istek, payload, fatura)
                if not ettn and self._portal_gecerli_ettn(mevcut_ettn):
                    ettn = str(mevcut_ettn).strip()
                self._gib_asama("portal_fatura_basarili", f"ettn={str(ettn)[:36] if ettn else 'bos'}")
                return nesne_ver("FaturaOlustur", {"ettn": ettn or ""})
            last_data_snip = blob[:1600] if blob else json.dumps(istek, ensure_ascii=False, default=str)[:1600]

        _gib_dev_http_file_line(f"[GİB] FATURA_OLUSTUR tükendi ozet={last_data_snip[:900]!r}")
        self._gib_asama("portal_fatura_tukendi", last_data_snip[:200])
        raise RuntimeError(
            f"GİB taslak bu sunucu isteği içinde {max_d} portal denemesi başarısız "
            f"(üst sınır .env GIB_FATURA_OLUSTUR_MAX_DENEME, varsayılan 14). "
            f"Son GİB data özeti: {last_data_snip[:1200]}"
        )

    @_retry_on_connection(max_attempts=3, delay=2.0)
    def fatura_taslak_olustur(self, fatura_data):
        """
        ERP'den gelen verilerle GİB üzerinde taslak fatura oluşturur.
        fatura_data: tarih (GG/AA/YYYY), saat (SS:DD veya SS:DD:SS), vkn, ad, soyad, unvan, vd,
                    hizmet_adi, birim_fiyat, items (liste; her biri name, quantity, unit_price, tax_rate),
                    iban (opsiyonel), note (opsiyonel).
        Returns: ETTN/UUID veya None (hata).
        """
        self.last_gib_asama_izle = []
        self._gib_asama("taslak_basla", "fatura_taslak_olustur")
        self._ensure_client()
        try:
            # Kullanıcı isteği: her taslak işleminde taze login/token.
            self._fresh_login()
        except Exception as e:
            err = str(e).lower()
            if "geçersiz kullanıcı" in err or "invalid" in err or "yetkisiz" in err or "kullanıcı adı" in err:
                raise RuntimeError("GİB geçersiz kullanıcı veya şifre. Lütfen GIB_USER ve GIB_PASS bilgilerinizi kontrol edin.") from e
            self._gib_asama("taslak_fresh_login_hata", str(e)[:400])
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
        # DB `toplam` bazen tüm dönem/kira özeti iken satirlar_json tek satır (ör. 250+KDV=300)
        # kalıyor; GİB `odenecekTutar` satırlardan gelir. YALNIZ yazısı satırla uyumlu olmalı.
        odenecek_yazi_icin = genel_toplam_hesap
        if erp_toplam > 0:
            erp_r = round(erp_toplam, 2)
            if abs(erp_r - genel_toplam_hesap) <= 0.05:
                odenecek_yazi_icin = erp_r
            else:
                self._gib_asama(
                    "toplam_db_satir_cakismasi",
                    f"db_toplam={erp_r} satir_odenecek={genel_toplam_hesap}; tutar_yazi=satir_odenecek",
                )
        self._gib_asama(
            "satirlar_ozet",
            f"n={len(satirlar_norm)} matrah={toplam_matrah} kdv={toplam_kdv} genel={genel_toplam_hesap} erp_toplam={erp_toplam}",
        )

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
            "toplam": fatura_data.get("toplam"),
        }
        self._gib_asama("payload_ust", f"tarih={payload.get('tarih')} saat={payload.get('saat')} urun={urun_adi[:40]}")
        alici_adres = str(fatura_data.get("adres") or "").strip()
        alici_tel = str(fatura_data.get("telefon") or "").strip()
        alici_email = str(fatura_data.get("email") or "").strip()
        mevcut_ettn = str(fatura_data.get("mevcut_ettn") or "").strip()
        mevcut_belge_no = str(fatura_data.get("mevcut_belge_no") or "").strip()

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
                d = BestOfficeGIBManager._portal_fatura_jp_olustur_duzelt(d, mevcut_ettn, mevcut_belge_no)
                try:
                    self.last_gonderilen_payload = dict(d)
                except Exception:
                    self.last_gonderilen_payload = d
            except Exception:
                d = BestOfficeGIBManager._portal_fatura_jp_olustur_duzelt(d, mevcut_ettn, mevcut_belge_no)
            return d

        glb["fatura_ver"] = _fatura_ver_dynamic
        self._gib_asama("portal_bounded_cagri", "basladi")
        try:
            out = self._fatura_olustur_earsivportal_bounded(
                self.client, payload, _fatura_ver_dynamic, mevcut_ettn, mevcut_belge_no
            )
        except Exception as ex:
            self._gib_asama("portal_bounded_hata", str(ex)[:500])
            raise
        finally:
            glb["fatura_ver"] = orig_fatura_ver
        self._gib_asama("portal_bounded_tamam", "yanit_alindi")
        uuid_str = self.ettn_from_out(out)
        liste_satir = getattr(self, "last_taslak_liste_satir", None)
        if not self._portal_gecerli_ettn(uuid_str):
            uuid_str = self._portal_ettn_basari_sonrasi(
                None,
                {
                    "vkn_veya_tckn": (fatura_data.get("vkn") or ""),
                    "tarih": (fatura_data.get("tarih") or ""),
                    "toplam": fatura_data.get("toplam"),
                },
                self.last_gonderilen_payload,
            )
            liste_satir = getattr(self, "last_taslak_liste_satir", None) or liste_satir
        gib_belge = ""
        if isinstance(liste_satir, dict):
            gib_belge = self._portal_extract_belge_no(liste_satir)
        self.last_taslak_raw = {
            "ettn": uuid_str,
            "gib_fatura_no": gib_belge,
            "raw_type": type(out).__name__ if out is not None else "none",
        }
        self._gib_asama("ettn_cikti", uuid_str[:36] if uuid_str else "bos")

        # Kullanıcı isteği: create sonrası son 1 gün listeden ETTN doğrula.
        if self._portal_gecerli_ettn(uuid_str):
            try:
                self._gib_asama("liste_dogrulama_basla", "days_back=1")
                _, dogrulama = self._find_fatura_by_uuid(uuid_str, days_back=1, force_new_session=False)
                if dogrulama:
                    et2 = self._portal_extract_ettn(dogrulama)
                    if et2:
                        uuid_str = et2
                    self._gib_asama("liste_dogrulama_ok", uuid_str[:36])
                    return uuid_str
                self.last_sms_error = "Taslak oluşturuldu fakat son 1 günlük listede ETTN doğrulanamadı."
                self._gib_asama("liste_dogrulama_yok", "1_gun_listede_yok")
            except Exception as exl:
                self.last_sms_error = "Taslak sonrası liste doğrulaması başarısız."
                self._gib_asama("liste_dogrulama_ex", str(exl)[:400])
            return uuid_str
        self.last_sms_error = "Taslak GİB'de oluşmuş olabilir; ETTN ERP'ye aktarılamadı. Portal listesinden ETTN kopyalayın."
        self._gib_asama("ettn_bos", "liste_yedek_basarili_degil")
        return ""

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
            f.notlar,
            f.ettn
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

    fn_db = str(fatura.get("fatura_no") or "").strip()
    et_db = str(fatura.get("ettn") or "").strip()
    mevcut_belge_no = fn_db if fn_db.upper().startswith("GIB") else ""
    mevcut_ettn_out = et_db if BestOfficeGIBManager._portal_gecerli_ettn(et_db) else ""

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
        "mevcut_ettn": mevcut_ettn_out,
        "mevcut_belge_no": mevcut_belge_no,
    }
