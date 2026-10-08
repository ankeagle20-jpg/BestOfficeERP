"""Sözleşmeler WhatsApp gönderimi. Metin ve tam numara tabloya veya loga yazılmaz."""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from db import execute, execute_returning, fetch_one
from odeme_linki import normalize_wa_telefon, ofisbir_istegi

logger = logging.getLogger(__name__)

IST = ZoneInfo("Europe/Istanbul")
MESAJ_LIMIT = 1000
TEKRAR_SANIYE = 600
USER_DK = 10
USER_GUN = 100
KIRACI_DK = 20
KIRACI_GUN = 300
_HAZIR = False

_YINE = "Bu mesaj bu numaraya az önce gönderildi."
_SUREN = "Gönderim sürüyor"
_UZUN = "Mesaj çok uzun"
_BOS = "Mesaj boş"
_TEL = "Telefon numarası geçersiz"
_MUSTERI = "Önce müşteri seçin."
_YOK = "Müşteri bulunamadı"
_ORIGIN = "İstek reddedildi"
_KAYITSIZ = "Bu numara WhatsApp'ta kayıtlı değil"
_ONCEDEN = "Bu mesaj daha önce kuyruğa alındı"
_GONDERILDI_ONCEDEN = "Bu mesaj daha önce gönderildi"
_BASARISIZ = "Gönderilemedi: kuyruk kabul etmedi"
_EK_BUYUK = "Makbuz eki çok büyük"
_EK_YOK = "Ek kabul edilmedi"
_SERVIS = "Servise ulaşılamadı"
_KUYRUK_DOLU = "Kuyruk dolu"
_OTURUM_YOK = "Oturum açık değil"
_BELIRSIZ = "Sonuç belirsiz, telefondan kontrol edin"
_NEDEN_METIN = {
    "ek_cok_buyuk": _EK_BUYUK,
    "ek_gecersiz": _EK_YOK,
    "ulasilamadi": _SERVIS,
    "yanit_yok": _SERVIS,
    "numara_gecersiz": _TEL,
    "kuyruk_dolu": _KUYRUK_DOLU,
    "oturum_yok": _OTURUM_YOK,
}
_YINE_KAPALI = frozenset(("ek_cok_buyuk", "ek_gecersiz"))
_KOD_ES = {
    "ek_cok_buyuk": "ek_cok_buyuk",
    "ek_gecersiz": "ek_gecersiz",
    "numara_gecersiz": "numara_gecersiz",
    "gecersiz": "numara_gecersiz",
    "kuyruk_dolu": "kuyruk_dolu",
    "WA_CONCURRENT_LIMIT": "kuyruk_dolu",
    "oturum_yok": "oturum_yok",
    "hazir_degil": "oturum_yok",
    "ulasilamadi": "ulasilamadi",
    "yanit_yok": "yanit_yok",
}
_KOD_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_KAYIT_YOK = "Kayıt yok"
_DENEME_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_GERI_METIN = {
    "kiraci": "WhatsApp servisi bağlı değil, WhatsApp Web sayfası açılıyor",
    "bagli_degil": "WhatsApp servisi bağlı değil, WhatsApp Web sayfası açılıyor",
    "ulasilamadi": "Servise ulaşılamadı, WhatsApp Web sayfası açılıyor",
    "oturum": "Oturum yenileniyor, 1 dk sonra tekrar deneyin.",
    "qr": "WhatsApp oturumu yenilenmeli (QR)",
}
_DK_USER = "Bu dakika içinde sizin gönderim sınırınız doldu. Bir dakika sonra tekrar deneyin."
_GUN_USER = "Bugünkü gönderim sınırınız doldu."
_DK_KIRACI = "Bu dakika içinde kiracı gönderim sınırı doldu. Bir dakika sonra tekrar deneyin."
_GUN_KIRACI = "Bugünkü kiracı gönderim sınırı doldu."
_MAKBUZ_YINE = "Bu makbuz daha önce gönderildi."
_TAHSILAT_YOK = "Tahsilat bulunamadı"
_PDF_OLMADI = "Makbuz PDF gönderilemedi"
PDF_BAYT_LIMIT = 2 * 1024 * 1024
_MAKBUZ_NO_RE = re.compile(r"^[0-9]{1,18}$")


def ensure_tablo() -> None:
    global _HAZIR
    if _HAZIR:
        return
    execute(
        """
        CREATE TABLE IF NOT EXISTS sozlesme_whatsapp_gonderim (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            musteri_id INTEGER NOT NULL,
            buton TEXT NOT NULL,
            durum TEXT NOT NULL,
            telefon_maske TEXT NOT NULL,
            telefon_hash TEXT NOT NULL,
            metin_uzunluk INTEGER NOT NULL,
            metin_hash TEXT NOT NULL,
            anahtar TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            tahsilat_id INTEGER,
            makbuz_no TEXT,
            neden TEXT
        )
        """
    )
    execute(
        """
        ALTER TABLE sozlesme_whatsapp_gonderim
        ADD COLUMN IF NOT EXISTS tahsilat_id INTEGER
        """
    )
    execute(
        """
        ALTER TABLE sozlesme_whatsapp_gonderim
        ADD COLUMN IF NOT EXISTS makbuz_no TEXT
        """
    )
    execute(
        """
        ALTER TABLE sozlesme_whatsapp_gonderim
        ADD COLUMN IF NOT EXISTS neden TEXT
        """
    )
    execute(
        """
        CREATE INDEX IF NOT EXISTS sozlesme_whatsapp_gonderim_user_zaman
        ON sozlesme_whatsapp_gonderim (user_id, created_at)
        """
    )
    execute(
        """
        CREATE INDEX IF NOT EXISTS sozlesme_whatsapp_gonderim_zaman
        ON sozlesme_whatsapp_gonderim (created_at)
        """
    )
    execute(
        """
        CREATE INDEX IF NOT EXISTS sozlesme_whatsapp_gonderim_tahsilat
        ON sozlesme_whatsapp_gonderim (tahsilat_id)
        """
    )
    _HAZIR = True


def telefon_maske(norm: str) -> str:
    d = re.sub(r"\D", "", str(norm or ""))
    if len(d) < 4:
        return "***"
    return "***" + d[-4:]


def ozet_hash(metin: str) -> str:
    return hashlib.sha256(str(metin or "").encode("utf-8")).hexdigest()


def telefon_ozet(norm: str) -> str:
    return hashlib.sha256(str(norm or "").encode("utf-8")).hexdigest()


def deneme_gecerli(ham: str) -> str:
    s = str(ham or "").strip().lower()
    if not _DENEME_RE.match(s):
        return ""
    return s


def deneme_anahtar(deneme: str) -> str:
    return "szwa:d:" + deneme


def _neden_kodu(ham) -> str:
    s = str(ham or "").strip()
    if s in _KOD_ES:
        return _KOD_ES[s]
    k = s.lower()
    if _KOD_RE.match(k):
        return k
    return ""


def durum_mesaji(durum: str, cakisma: bool, neden: str = "") -> str:
    kod = str(durum or "")
    if kod == "gonderiliyor":
        return _SUREN
    if kod == "kuyrukta":
        return _ONCEDEN if cakisma else "Kuyruğa alındı"
    if kod == "gonderildi":
        return _GONDERILDI_ONCEDEN if cakisma else "Gönderildi"
    if kod == "belirsiz":
        return _BELIRSIZ
    if kod == "basarisiz":
        return _NEDEN_METIN.get(_neden_kodu(neden)) or _BASARISIZ
    return _ONCEDEN


def origin_uygun(origin: str, host: str) -> bool:
    ham = str(origin or "").strip()
    if not ham:
        return False
    parca = urlsplit(ham)
    if parca.scheme not in ("http", "https") or not parca.hostname:
        return False
    beklenen = str(host or "").strip().lower()
    if not beklenen:
        return False
    netloc = (parca.netloc or "").lower()
    hostname = parca.hostname.lower()
    host_adi = beklenen.split(":")[0]
    return netloc == beklenen or hostname == host_adi


def limit_mesaji(user_dk: int, user_gun: int, kiraci_dk: int, kiraci_gun: int) -> str:
    if int(user_dk) >= USER_DK:
        return _DK_USER
    if int(kiraci_dk) >= KIRACI_DK:
        return _DK_KIRACI
    if int(user_gun) >= USER_GUN:
        return _GUN_USER
    if int(kiraci_gun) >= KIRACI_GUN:
        return _GUN_KIRACI
    return ""


def node_yolu_acik() -> bool:
    if not ofisbir_istegi():
        return False
    from routes.whatsapp_routes import _wa_tenant_id

    return _wa_tenant_id() == "default"


def _gun_basi(simdi: datetime) -> datetime:
    ist = simdi.astimezone(IST)
    bas = ist.replace(hour=0, minute=0, second=0, microsecond=0)
    return bas.astimezone(timezone.utc)


def _musteri(mid: int):
    return fetch_one("SELECT id FROM customers WHERE id = %s", (int(mid),))


def _sayilar(uid: int, dk_basi: datetime, gun_basi: datetime):
    row = fetch_one(
        """
        SELECT
            COUNT(*) FILTER (WHERE user_id = %s AND created_at >= %s) AS user_dk,
            COUNT(*) FILTER (WHERE user_id = %s AND created_at >= %s) AS user_gun,
            COUNT(*) FILTER (WHERE created_at >= %s) AS kiraci_dk,
            COUNT(*) FILTER (WHERE created_at >= %s) AS kiraci_gun
        FROM sozlesme_whatsapp_gonderim
        WHERE created_at >= %s
        """,
        (int(uid), dk_basi, int(uid), gun_basi, dk_basi, gun_basi, gun_basi),
    ) or {}
    return (
        int(row.get("user_dk") or 0),
        int(row.get("user_gun") or 0),
        int(row.get("kiraci_dk") or 0),
        int(row.get("kiraci_gun") or 0),
    )


def _bul(uid: int, anahtar: str):
    return fetch_one(
        """
        SELECT id, durum, COALESCE(neden, '') AS neden
        FROM sozlesme_whatsapp_gonderim
        WHERE user_id = %s AND anahtar = %s
        """,
        (int(uid), anahtar),
    )


def _son_gonderim(uid: int, metin_h: str, tel_h: str, sinir: datetime):
    return fetch_one(
        """
        SELECT id, durum, created_at
        FROM sozlesme_whatsapp_gonderim
        WHERE user_id = %s AND metin_hash = %s AND telefon_hash = %s
          AND durum IN ('gonderiliyor', 'kuyrukta', 'gonderildi', 'belirsiz')
          AND created_at >= %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(uid), metin_h, tel_h, sinir),
    )


def _ekle(uid, mid, buton, durum, maske, tel_h, uzunluk, metin_h, anahtar):
    return execute_returning(
        """
        INSERT INTO sozlesme_whatsapp_gonderim (
            user_id, musteri_id, buton, durum, telefon_maske, telefon_hash,
            metin_uzunluk, metin_hash, anahtar
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (anahtar) DO NOTHING
        RETURNING id
        """,
        (int(uid), int(mid), buton, durum, maske, tel_h, int(uzunluk), metin_h, anahtar),
    )


def _durum_yaz(rid: int, durum: str, neden: str = "") -> None:
    execute(
        """
        UPDATE sozlesme_whatsapp_gonderim
        SET durum = %s, neden = NULLIF(%s, '')
        WHERE id = %s AND durum = 'gonderiliyor'
        """,
        (durum, _neden_kodu(neden), int(rid)),
    )


def dene_bir_kez(cagri, ag_hatalari):
    """Zaman aşımı ve bağlantı hatasında çağrıyı bir kez yineler."""
    son = None
    for _deneme in (1, 2):
        try:
            return cagri()
        except ag_hatalari as exc:
            son = exc
    raise son


def _durum_coz(data) -> dict:
    if not isinstance(data, dict):
        return {"hazir": False, "neden": "ulasilamadi"}
    if bool(data.get("bagli")) and bool(data.get("hazir")):
        return {"hazir": True, "neden": ""}
    if data.get("neden") == "qr" or data.get("qr_bekliyor"):
        return {"hazir": False, "neden": "qr"}
    neden = str(data.get("neden") or "bagli_degil")
    if neden not in _GERI_METIN:
        neden = "bagli_degil"
    return {"hazir": False, "neden": neden}


def _yanit_durum(r) -> dict:
    if getattr(r, "status_code", 0) != 200:
        return {"hazir": False, "neden": "ulasilamadi"}
    try:
        data = r.json() if r.content else {}
    except Exception:
        return {"hazir": False, "neden": "ulasilamadi"}
    return _durum_coz(data)


def _ag_sinifi():
    import requests

    return (requests.exceptions.Timeout, requests.exceptions.ConnectionError)


def _durum_varsayilan() -> dict:
    import requests
    from routes.whatsapp_routes import _wa_internal_headers, _wa_url

    def cagri():
        return requests.get(_wa_url("durum-bak"), headers=_wa_internal_headers(), timeout=(3, 5))

    try:
        return _yanit_durum(dene_bir_kez(cagri, _ag_sinifi()))
    except Exception:
        return {"hazir": False, "neden": "ulasilamadi"}


_NUMARA_YENILE = frozenset({"hazir_degil", "hata", "http"})


def _oturum_log(kaynak: str, bilgi: dict | None) -> None:
    b = bilgi or {}
    logger.info(
        "sozlesme_wa_geri_oturum kaynak=%s tur=%s sure_ms=%s durum_ms=%s tekrar=%s tekrar_ms=%s",
        kaynak,
        b.get("tur") or "-",
        int(b.get("sure_ms") or 0),
        int(b.get("durum_ms") or 0),
        b.get("tekrar") or "-",
        int(b.get("tekrar_ms") or 0),
    )


def _durum_uyan_varsayilan() -> None:
    import requests
    from routes.whatsapp_routes import _wa_internal_headers, _wa_url

    def cagri():
        return requests.get(_wa_url("durum"), headers=_wa_internal_headers(), timeout=(3, 22))

    try:
        dene_bir_kez(cagri, _ag_sinifi())
    except Exception:
        logger.info("sozlesme_wa_durum tur=ulasilamadi")


def _numara_bir_ve_yenile(norm: str, numara_fn, durum_fn, yenile: bool):
    fn = numara_fn or _numara_dene
    t0 = time.perf_counter()
    kayit, neden = fn(norm)
    sure1 = int((time.perf_counter() - t0) * 1000)
    bilgi = {"tur": neden or "-", "sure_ms": sure1, "durum_ms": 0, "tekrar": "-", "tekrar_ms": 0}
    if not yenile or kayit in ("kayitli", "yok") or neden not in _NUMARA_YENILE:
        return kayit, neden, bilgi
    logger.info("sozlesme_wa_numara tur=%s sure_ms=%s", neden or "-", sure1)
    t1 = time.perf_counter()
    try:
        (durum_fn or _durum_uyan_varsayilan)()
    except Exception:
        logger.info("sozlesme_wa_durum tur=hata")
    bilgi["durum_ms"] = int((time.perf_counter() - t1) * 1000)
    t2 = time.perf_counter()
    kayit2, neden2 = fn(norm)
    bilgi["tekrar"] = kayit2
    bilgi["tekrar_ms"] = int((time.perf_counter() - t2) * 1000)
    bilgi["tur"] = neden2 or bilgi["tur"]
    logger.info(
        "sozlesme_wa_numara_tekrar tur=%s sure_ms=%s durum_ms=%s sonuc=%s",
        neden2 or "-",
        bilgi["tekrar_ms"],
        bilgi["durum_ms"],
        kayit2,
    )
    return kayit2, neden2, bilgi


def _uyandir_varsayilan() -> dict:
    import requests
    from routes.whatsapp_routes import _wa_internal_headers, _wa_url

    def cagri():
        return requests.post(
            _wa_url("uyandir"),
            json={"bekle_ms": 20000},
            headers=_wa_internal_headers(),
            timeout=(3, 22),
        )

    try:
        return _yanit_durum(dene_bir_kez(cagri, _ag_sinifi()))
    except Exception:
        return {"hazir": False, "neden": "ulasilamadi"}


def _numara_dene(telefon: str):
    from routes.erp_notlar_routes import _wa_numara_kayitli

    son = ("hata", "ulasilamadi")
    for _deneme in (1, 2):
        kayit, neden = _wa_numara_kayitli(telefon)
        if kayit != "hata" or neden not in ("baglanti", "yanit_yok"):
            return kayit, neden
        son = (kayit, neden)
    return son


def _bagli_paket(raw) -> dict:
    if isinstance(raw, bool):
        return {"hazir": raw, "neden": "" if raw else "bagli_degil"}
    if isinstance(raw, dict):
        if raw.get("hazir"):
            return {"hazir": True, "neden": ""}
        neden = str(raw.get("neden") or "bagli_degil")
        if neden not in _GERI_METIN:
            neden = "bagli_degil"
        return {"hazir": False, "neden": neden}
    return {"hazir": False, "neden": "bagli_degil"}


def _kuyruk_liste(telefon: str, mesaj: str, anahtar: str, ek=None) -> list:
    oge = {"telefon": telefon, "mesaj": mesaj, "anahtar": anahtar}
    if ek:
        oge["ek"] = {"mime": ek.get("mime"), "veri": ek.get("veri"), "ad": ek.get("ad")}
    return [oge]


def _kuyruk_log(http_kod: int, neden: str) -> None:
    logger.info("sozlesme_wa_kuyruk http=%s kod=%s", int(http_kod), (_neden_kodu(neden) or "-")[:32])


def _kuyruk_varsayilan(telefon: str, mesaj: str, anahtar: str, ek=None) -> dict:
    import requests
    from routes.whatsapp_routes import _wa_internal_headers, _wa_url

    try:
        r = requests.post(
            _wa_url("kuyruk-toplu-ekle"),
            json={"liste": _kuyruk_liste(telefon, mesaj, anahtar, ek)},
            headers=_wa_internal_headers(),
            timeout=(3, 12),
        )
    except requests.exceptions.Timeout:
        _kuyruk_log(0, "yanit_yok")
        return {"durum": "belirsiz", "neden": "yanit_yok"}
    except Exception:
        _kuyruk_log(0, "ulasilamadi")
        return {"durum": "hata", "neden": "ulasilamadi"}
    try:
        body = r.json() if r.content else {}
    except Exception:
        body = {}
    paket = _kuyruk_oku(int(r.status_code), body)
    if int(r.status_code) >= 400 or paket.get("durum") in ("hata", "belirsiz"):
        _kuyruk_log(int(r.status_code), str(paket.get("neden") or ""))
    return paket


def _kuyruk_oku(status: int, body) -> dict:
    if int(status) == 413:
        return {"durum": "hata", "neden": "ek_cok_buyuk"}
    if int(status) >= 400 or not isinstance(body, dict) or body.get("ok") is False:
        neden = ""
        if isinstance(body, dict):
            neden = _neden_kodu(body.get("code") or body.get("kod"))
        if not neden and int(status) == 503:
            neden = "oturum_yok"
        elif not neden and int(status) >= 500:
            neden = "ulasilamadi"
        elif not neden and int(status) >= 400:
            neden = "http"
        return {"durum": "hata", "neden": neden}
    oge = body.get("oge") or []
    if not oge or not isinstance(oge[0], dict):
        return {"durum": "kuyrukta", "neden": ""}
    durum = str(oge[0].get("durum") or "")
    if durum == "gonderildi":
        return {"durum": "gonderildi", "neden": ""}
    if durum == "basarisiz":
        return {"durum": "hata", "neden": _neden_kodu(oge[0].get("kod") or oge[0].get("code"))}
    if durum == "belirsiz":
        return {"durum": "belirsiz", "neden": ""}
    return {"durum": "kuyrukta", "neden": ""}


def _kuyruk_sonuc(body) -> str:
    return str(_kuyruk_oku(200, body).get("durum") or "hata")


def _arkaplan_ayir(sonuc) -> tuple[str, str]:
    if isinstance(sonuc, dict):
        return str(sonuc.get("durum") or "hata"), _neden_kodu(sonuc.get("neden"))
    return str(sonuc or "hata"), ""


def _arkaplan_yaz(rid: int, sonuc) -> None:
    kod, neden = _arkaplan_ayir(sonuc)
    if kod == "gonderildi":
        _durum_yaz(rid, "gonderildi", neden)
    elif kod in ("ok", "kuyrukta"):
        _durum_yaz(rid, "kuyrukta", neden)
    elif kod == "belirsiz":
        _durum_yaz(rid, "belirsiz", neden)
    else:
        _durum_yaz(rid, "basarisiz", neden)


def _durum_govde(row: dict, cakisma: bool) -> dict:
    durum = str((row or {}).get("durum") or "")
    neden = _neden_kodu((row or {}).get("neden"))
    govde = {
        "ok": durum != "basarisiz",
        "durum": durum,
        "mesaj": durum_mesaji(durum, cakisma, neden),
    }
    if cakisma:
        govde["cakisma"] = True
    if durum == "basarisiz":
        govde["yine"] = neden not in _YINE_KAPALI
    return govde


def sozlesme_wa_durum(uid: int, deneme: str) -> tuple[dict, int]:
    kim = deneme_gecerli(deneme)
    if not kim:
        return {"ok": False, "mesaj": _ORIGIN}, 400
    ensure_tablo()
    row = _bul(int(uid), deneme_anahtar(kim))
    if not row:
        return {"ok": False, "durum": "yok", "mesaj": _KAYIT_YOK}, 404
    return _durum_govde(row, False), 200


def sozlesme_wa_isle(
    uid: int,
    data: dict,
    origin: str,
    host: str,
    *,
    simdi: datetime | None = None,
    bagli_fn=None,
    numara_fn=None,
    kuyruk_fn=None,
    uyandir_fn=None,
    durum_fn=None,
) -> tuple[dict, int]:
    if not origin_uygun(origin, host):
        return {"ok": False, "mesaj": _ORIGIN}, 403
    buton = str((data or {}).get("buton") or "").strip()
    if buton not in ("ust", "makbuz"):
        return {"ok": False, "mesaj": "İstek reddedildi"}, 400
    try:
        mid = int((data or {}).get("musteri_id") or 0)
    except (TypeError, ValueError):
        mid = 0
    if mid <= 0:
        return {"ok": False, "mesaj": _MUSTERI}, 400
    if not _musteri(mid):
        return {"ok": False, "mesaj": _YOK}, 404
    metin = str((data or {}).get("mesaj") or "").strip()
    if not metin:
        return {"ok": False, "mesaj": _BOS}, 400
    if len(metin) > MESAJ_LIMIT:
        return {"ok": False, "mesaj": _UZUN}, 400
    norm = normalize_wa_telefon((data or {}).get("telefon"))
    if not norm:
        return {"ok": False, "mesaj": _TEL}, 400
    kim = deneme_gecerli((data or {}).get("deneme"))
    if not kim:
        return {"ok": False, "mesaj": _ORIGIN}, 400

    ensure_tablo()
    an = simdi or datetime.now(timezone.utc)
    if an.tzinfo is None:
        an = an.replace(tzinfo=timezone.utc)
    user_dk, user_gun, kiraci_dk, kiraci_gun = _sayilar(int(uid), an - timedelta(minutes=1), _gun_basi(an))
    lim = limit_mesaji(user_dk, user_gun, kiraci_dk, kiraci_gun)
    if lim:
        return {"ok": False, "mesaj": lim}, 429

    maske = telefon_maske(norm)
    tel_h = telefon_ozet(norm)
    metin_h = ozet_hash(metin)
    onay = bool((data or {}).get("onay"))
    anahtar = deneme_anahtar(kim)
    var_olan = _bul(int(uid), anahtar)
    if var_olan:
        return _durum_govde(var_olan, True), 200

    def _kaydet(durum: str, anahtar_deger: str):
        return _ekle(int(uid), mid, buton, durum, maske, tel_h, len(metin), metin_h, anahtar_deger[:120])

    def _tekil(on_ek: str) -> str:
        return (on_ek + ":" + metin_h[:8] + ":" + tel_h[:8] + ":" + str(int(uid)) + ":" + str(int(an.timestamp() * 1000)))[:120]

    def _geri(neden, bilgi=None):
        kod = "geri_" + str(neden or "bagli_degil")
        _kaydet(kod, _tekil("szwa:geri"))
        logger.info("sozlesme_wa_geri neden=%s adet=1", neden)
        if neden == "oturum":
            _oturum_log("metin", bilgi)
        mesaj = _GERI_METIN.get(neden) or _GERI_METIN["bagli_degil"]
        if neden == "qr":
            return {"ok": False, "qr": True, "geri_dus": False, "neden": neden, "mesaj": mesaj}, 200
        govde = {"ok": True, "geri_dus": True, "neden": neden, "mesaj": mesaj}
        if neden == "oturum":
            govde["web_elle"] = True
        return govde, 200

    if not node_yolu_acik():
        return _geri("kiraci")

    uyandi = False
    if bagli_fn is not None:
        paket = _bagli_paket(bagli_fn())
        if not paket["hazir"] and uyandir_fn is not None:
            paket = _bagli_paket(uyandir_fn())
            uyandi = True
    else:
        paket = _durum_varsayilan()
        if not paket["hazir"] and paket["neden"] != "ulasilamadi":
            paket = _uyandir_varsayilan()
            uyandi = True
    if not paket["hazir"]:
        return _geri(paket["neden"] or "bagli_degil")

    if not onay:
        son = _son_gonderim(int(uid), metin_h, tel_h, an - timedelta(seconds=TEKRAR_SANIYE))
        if son:
            return {"ok": False, "tekrar": True, "durum": str(son.get("durum") or ""), "mesaj": _YINE}, 409

    kayit, _neden, bilgi = _numara_bir_ve_yenile(norm, numara_fn, durum_fn, not uyandi)
    if kayit == "yok":
        _kaydet("basarisiz", _tekil("szwa:yok"))
        return {"ok": False, "kayitli": False, "mesaj": _KAYITSIZ}, 400
    if kayit != "kayitli":
        neden_k = "ulasilamadi" if _neden in ("baglanti", "yanit_yok", "ulasilamadi") else "oturum"
        return _geri(neden_k, bilgi if neden_k == "oturum" else None)

    row = _kaydet("gonderiliyor", anahtar)
    if not row:
        bulunan = _bul(int(uid), anahtar)
        if bulunan:
            return _durum_govde(bulunan, True), 200
        return {"ok": False, "cakisma": True, "mesaj": _ONCEDEN}, 200
    rid = int(row["id"])
    kuyruk = kuyruk_fn or _kuyruk_varsayilan

    def _calis():
        try:
            sonuc = kuyruk(norm, metin, anahtar[:120])
        except Exception:
            logger.info("sozlesme wa arkaplan hata")
            sonuc = "belirsiz"
        _arkaplan_yaz(rid, sonuc)

    threading.Thread(target=_calis, daemon=True, name=f"szwa-{rid}").start()
    return {"ok": True, "durum": "gonderiliyor", "mesaj": _SUREN, "deneme": kim}, 200


def _tutar_yazi(val) -> str:
    try:
        x = float(val or 0)
    except (TypeError, ValueError):
        x = 0.0
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _tarih_yazi(v) -> str:
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%d.%m.%Y")
        except Exception:
            return ""
    s = str(v or "").strip()
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        return s[8:10] + "." + s[5:7] + "." + s[0:4]
    return s[:10]


def tahsilat_metin(no, tutar, tarih, pdf_ek: bool) -> str:
    govde = f"Makbuz no {no}. Tutar {_tutar_yazi(tutar)} TL. Tarih {_tarih_yazi(tarih)}."
    if pdf_ek:
        return govde + " Makbuz PDF ekte."
    return govde


def _dosya_adi(makbuz_no) -> str:
    s = str(makbuz_no or "").strip()
    if not _MAKBUZ_NO_RE.match(s):
        return ""
    return s + ".pdf"


def _tahsilat_getir(tid: int):
    return fetch_one(
        """
        SELECT t.id, t.makbuz_no, t.tutar, t.odeme_turu, t.tahsilat_tarihi, t.aciklama,
               t.created_at, t.fatura_id, t.tahsil_eden, t.cek_detay, t.havale_banka,
               COALESCE(t.customer_id, t.musteri_id) AS musteri_id,
               c.name AS musteri_adi
        FROM tahsilatlar t
        LEFT JOIN customers c ON COALESCE(t.customer_id, t.musteri_id) = c.id
        WHERE t.id = %s
        """,
        (int(tid),),
    )


def _makbuz_son(uid: int, tahsilat_id: int):
    return fetch_one(
        """
        SELECT id, durum
        FROM sozlesme_whatsapp_gonderim
        WHERE user_id = %s AND tahsilat_id = %s
          AND durum IN ('gonderiliyor', 'kuyrukta', 'gonderildi', 'belirsiz')
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(uid), int(tahsilat_id)),
    )


def _ekle_tahsilat(uid, mid, durum, maske, tel_h, uzunluk, metin_h, anahtar, tahsilat_id, makbuz_no):
    return execute_returning(
        """
        INSERT INTO sozlesme_whatsapp_gonderim (
            user_id, musteri_id, buton, durum, telefon_maske, telefon_hash,
            metin_uzunluk, metin_hash, anahtar, tahsilat_id, makbuz_no
        ) VALUES (%s, %s, 'tahsilat', %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (anahtar) DO NOTHING
        RETURNING id
        """,
        (
            int(uid),
            int(mid),
            durum,
            maske,
            tel_h,
            int(uzunluk),
            metin_h,
            anahtar,
            int(tahsilat_id),
            str(makbuz_no or "")[:40],
        ),
    )


def _pdf_gercek(row):
    from routes.faturalar_routes import build_makbuz_pdf

    return build_makbuz_pdf(row, (row or {}).get("musteri_adi") or "")


def _pdf_bayt(row, pdf_fn=None) -> bytes:
    fn = pdf_fn or _pdf_gercek
    raw = fn(row)
    if isinstance(raw, bytearray):
        raw = bytes(raw)
    if not isinstance(raw, bytes) or not raw.startswith(b"%PDF"):
        return b""
    if len(raw) < 5 or len(raw) > PDF_BAYT_LIMIT:
        return b""
    return raw


def _makbuz_indir(tid: int) -> str:
    return f"/faturalar/tahsilat-pdf/{int(tid)}?indir=1"


def tahsilat_wa_hazir(tid, *, getir_fn=None, node_fn=None) -> tuple[dict, int]:
    try:
        tid = int(tid or 0)
    except (TypeError, ValueError):
        tid = 0
    if tid <= 0:
        return {"ok": False, "mesaj": _TAHSILAT_YOK}, 400
    row = (getir_fn or _tahsilat_getir)(tid)
    if not row:
        return {"ok": False, "mesaj": _TAHSILAT_YOK}, 404
    ek = bool((node_fn or node_yolu_acik)())
    return {
        "ok": True,
        "pdf_ek": ek,
        "geri_dus": False,
        "makbuz_no": str(row.get("makbuz_no") or ""),
        "mesaj": tahsilat_metin(row.get("makbuz_no"), row.get("tutar"), row.get("tahsilat_tarihi"), ek),
        "indir": _makbuz_indir(tid),
    }, 200


def tahsilat_wa_isle(
    uid: int,
    data: dict,
    origin: str,
    host: str,
    *,
    simdi: datetime | None = None,
    bagli_fn=None,
    numara_fn=None,
    kuyruk_fn=None,
    uyandir_fn=None,
    durum_fn=None,
    getir_fn=None,
    pdf_fn=None,
    node_fn=None,
) -> tuple[dict, int]:
    if not origin_uygun(origin, host):
        return {"ok": False, "mesaj": _ORIGIN, "geri_dus": False}, 403
    try:
        tid = int((data or {}).get("tahsilat_id") or 0)
    except (TypeError, ValueError):
        tid = 0
    if tid <= 0:
        return {"ok": False, "mesaj": _TAHSILAT_YOK, "geri_dus": False}, 400
    kim = deneme_gecerli((data or {}).get("deneme"))
    if not kim:
        return {"ok": False, "mesaj": _ORIGIN, "geri_dus": False}, 400
    row = (getir_fn or _tahsilat_getir)(tid)
    if not row:
        return {"ok": False, "mesaj": _TAHSILAT_YOK, "geri_dus": False}, 404
    try:
        kayit_mid = int(row.get("musteri_id") or 0)
    except (TypeError, ValueError):
        kayit_mid = 0
    if kayit_mid <= 0:
        return {"ok": False, "mesaj": _TAHSILAT_YOK, "geri_dus": False}, 404
    try:
        istenen = int((data or {}).get("musteri_id") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "mesaj": _ORIGIN, "geri_dus": False}, 403
    if istenen != kayit_mid:
        return {"ok": False, "mesaj": _ORIGIN, "geri_dus": False}, 403
    istem_no = str((data or {}).get("makbuz_no") or "").strip()
    if istem_no and istem_no != str(row.get("makbuz_no") or "").strip():
        return {"ok": False, "mesaj": _ORIGIN, "geri_dus": False}, 403
    metin = str((data or {}).get("mesaj") or "").strip()
    if not metin:
        return {"ok": False, "mesaj": _BOS, "geri_dus": False}, 400
    if len(metin) > MESAJ_LIMIT:
        return {"ok": False, "mesaj": _UZUN, "geri_dus": False}, 400
    norm = normalize_wa_telefon((data or {}).get("telefon"))
    if not norm:
        return {"ok": False, "mesaj": _TEL, "geri_dus": False}, 400

    ensure_tablo()
    an = simdi or datetime.now(timezone.utc)
    if an.tzinfo is None:
        an = an.replace(tzinfo=timezone.utc)
    user_dk, user_gun, kiraci_dk, kiraci_gun = _sayilar(int(uid), an - timedelta(minutes=1), _gun_basi(an))
    lim = limit_mesaji(user_dk, user_gun, kiraci_dk, kiraci_gun)
    if lim:
        return {"ok": False, "mesaj": lim, "geri_dus": False}, 429

    maske = telefon_maske(norm)
    tel_h = telefon_ozet(norm)
    metin_h = ozet_hash(metin)
    anahtar = deneme_anahtar(kim)
    var_olan = _bul(int(uid), anahtar)
    if var_olan:
        govde = _durum_govde(var_olan, True)
        govde["geri_dus"] = False
        return govde, 200
    if not bool((data or {}).get("onay")):
        son = _makbuz_son(int(uid), tid)
        if son:
            return {
                "ok": False,
                "tekrar": True,
                "geri_dus": False,
                "durum": str(son.get("durum") or ""),
                "mesaj": _MAKBUZ_YINE,
            }, 409

    def _tekil(on_ek: str) -> str:
        ozet = ozet_hash(str(tid) + ":" + str(int(an.timestamp() * 1000)))
        return (on_ek + ":" + ozet[:16])[:120]

    def _geri_makbuz(neden: str, bilgi=None):
        neden_k = neden if neden in _GERI_METIN else "bagli_degil"
        if neden_k == "oturum":
            _oturum_log("makbuz", bilgi)
        try:
            _ekle_tahsilat(
                int(uid),
                kayit_mid,
                ("geri_" + neden_k)[:40],
                maske,
                tel_h,
                len(metin),
                metin_h,
                _tekil("szwa:geri"),
                tid,
                row.get("makbuz_no"),
            )
        except Exception:
            logger.info("tahsilat_wa geri kayit yok")
        mesaj = _GERI_METIN.get(neden_k) or _GERI_METIN["bagli_degil"]
        qr = neden_k == "qr"
        govde = {
            "ok": not qr,
            "geri_dus": not qr,
            "qr": qr,
            "neden": neden_k,
            "mesaj": mesaj,
            "indir": _makbuz_indir(tid),
        }
        if neden_k == "oturum":
            govde["web_elle"] = True
        return govde, 200

    acik = node_yolu_acik if node_fn is None else node_fn
    if not acik():
        return _geri_makbuz("kiraci")

    uyandi = False
    if bagli_fn is not None:
        paket = _bagli_paket(bagli_fn())
        if not paket["hazir"] and uyandir_fn is not None:
            paket = _bagli_paket(uyandir_fn())
            uyandi = True
    else:
        paket = _durum_varsayilan()
        if not paket["hazir"] and paket["neden"] != "ulasilamadi":
            paket = _uyandir_varsayilan()
            uyandi = True
    if not paket["hazir"]:
        return _geri_makbuz(paket["neden"] or "bagli_degil")

    kayit, neden_n, bilgi = _numara_bir_ve_yenile(norm, numara_fn, durum_fn, not uyandi)
    if kayit == "yok":
        try:
            _ekle_tahsilat(
                int(uid), kayit_mid, "basarisiz", maske, tel_h, len(metin), metin_h,
                _tekil("szwa:yok"), tid, row.get("makbuz_no"),
            )
        except Exception:
            logger.info("tahsilat_wa kayitsiz kayit yok")
        return {"ok": False, "kayitli": False, "geri_dus": False, "mesaj": _KAYITSIZ}, 400
    if kayit != "kayitli":
        neden_k = "ulasilamadi" if neden_n in ("baglanti", "yanit_yok", "ulasilamadi") else "oturum"
        return _geri_makbuz(neden_k, bilgi if neden_k == "oturum" else None)

    raw = _pdf_bayt(row, pdf_fn)
    ad = _dosya_adi(row.get("makbuz_no"))
    if not raw or not ad:
        return {"ok": False, "mesaj": _PDF_OLMADI, "geri_dus": False}, 400
    ek = {"mime": "application/pdf", "veri": base64.b64encode(raw).decode("ascii"), "ad": ad}
    raw = b""

    try:
        kayit_row = _ekle_tahsilat(
            int(uid), kayit_mid, "gonderiliyor", maske, tel_h, len(metin), metin_h,
            anahtar, tid, row.get("makbuz_no"),
        )
    except Exception:
        ek.clear()
        logger.info("tahsilat_wa kayit yok")
        return {"ok": False, "mesaj": _PDF_OLMADI, "geri_dus": False}, 503
    if not kayit_row:
        ek.clear()
        bulunan = _bul(int(uid), anahtar)
        if bulunan:
            govde = _durum_govde(bulunan, True)
            govde["geri_dus"] = False
            return govde, 200
        return {"ok": False, "cakisma": True, "geri_dus": False, "mesaj": _ONCEDEN}, 200
    rid = int(kayit_row["id"])
    kuyruk = kuyruk_fn or _kuyruk_varsayilan

    def _calis():
        paket = ek
        sonuc = "belirsiz"
        try:
            sonuc = kuyruk(norm, metin, anahtar[:120], paket)
        except Exception:
            logger.info("tahsilat wa arkaplan hata")
            sonuc = "belirsiz"
        finally:
            if isinstance(paket, dict):
                paket.clear()
        _arkaplan_yaz(rid, sonuc)

    threading.Thread(target=_calis, daemon=True, name=f"szwa-t-{rid}").start()
    return {"ok": True, "durum": "gonderiliyor", "mesaj": _SUREN, "deneme": kim, "geri_dus": False}, 200
