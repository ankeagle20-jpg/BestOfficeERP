"""Sözleşmeler WhatsApp gönderimi. Metin ve tam numara tabloya veya loga yazılmaz."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
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
_BELIRSIZ = "Sonuç belirsiz, telefondan kontrol edin"
_KAYIT_YOK = "Kayıt yok"
_DENEME_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_GERI_METIN = {
    "kiraci": "WhatsApp servisi bağlı değil, WhatsApp Web sayfası açılıyor",
    "bagli_degil": "WhatsApp servisi bağlı değil, WhatsApp Web sayfası açılıyor",
    "ulasilamadi": "Servise ulaşılamadı, WhatsApp Web sayfası açılıyor",
    "oturum": "Oturum açılamadı, WhatsApp Web sayfası açılıyor",
    "qr": "WhatsApp oturumu yenilenmeli (QR)",
}
_DK_USER = "Bu dakika içinde sizin gönderim sınırınız doldu. Bir dakika sonra tekrar deneyin."
_GUN_USER = "Bugünkü gönderim sınırınız doldu."
_DK_KIRACI = "Bu dakika içinde kiracı gönderim sınırı doldu. Bir dakika sonra tekrar deneyin."
_GUN_KIRACI = "Bugünkü kiracı gönderim sınırı doldu."


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
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
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


def durum_mesaji(durum: str, cakisma: bool) -> str:
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
        return _BASARISIZ
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
        SELECT id, durum
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


def _durum_yaz(rid: int, durum: str) -> None:
    execute(
        """
        UPDATE sozlesme_whatsapp_gonderim
        SET durum = %s
        WHERE id = %s AND durum = 'gonderiliyor'
        """,
        (durum, int(rid)),
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


def _kuyruk_varsayilan(telefon: str, mesaj: str, anahtar: str) -> str:
    import requests
    from routes.whatsapp_routes import _wa_internal_headers, _wa_url

    try:
        r = requests.post(
            _wa_url("kuyruk-toplu-ekle"),
            json={"liste": [{"telefon": telefon, "mesaj": mesaj, "anahtar": anahtar}]},
            headers=_wa_internal_headers(),
            timeout=(3, 12),
        )
    except requests.exceptions.Timeout:
        return "belirsiz"
    except Exception:
        return "hata"
    if r.status_code >= 400:
        return "hata"
    try:
        body = r.json() if r.content else {}
    except Exception:
        return "belirsiz"
    return _kuyruk_sonuc(body)


def _kuyruk_sonuc(body) -> str:
    if not isinstance(body, dict) or body.get("ok") is False:
        return "hata"
    oge = body.get("oge") or []
    if not oge or not isinstance(oge[0], dict):
        return "kuyrukta"
    durum = str(oge[0].get("durum") or "")
    if durum == "gonderildi":
        return "gonderildi"
    if durum == "basarisiz":
        return "hata"
    if durum == "belirsiz":
        return "belirsiz"
    return "kuyrukta"


def _durum_govde(row: dict, cakisma: bool) -> dict:
    durum = str((row or {}).get("durum") or "")
    govde = {
        "ok": durum != "basarisiz",
        "durum": durum,
        "mesaj": durum_mesaji(durum, cakisma),
    }
    if cakisma:
        govde["cakisma"] = True
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

    def _geri(neden):
        kod = "geri_" + str(neden or "bagli_degil")
        _kaydet(kod, _tekil("szwa:geri"))
        logger.info("sozlesme_wa_geri neden=%s adet=1", neden)
        mesaj = _GERI_METIN.get(neden) or _GERI_METIN["bagli_degil"]
        if neden == "qr":
            return {"ok": False, "qr": True, "geri_dus": False, "neden": neden, "mesaj": mesaj}, 200
        return {"ok": True, "geri_dus": True, "neden": neden, "mesaj": mesaj}, 200

    if not node_yolu_acik():
        return _geri("kiraci")

    if bagli_fn is not None:
        paket = _bagli_paket(bagli_fn())
        if not paket["hazir"] and uyandir_fn is not None:
            paket = _bagli_paket(uyandir_fn())
    else:
        paket = _durum_varsayilan()
        if not paket["hazir"] and paket["neden"] != "ulasilamadi":
            paket = _uyandir_varsayilan()
    if not paket["hazir"]:
        return _geri(paket["neden"] or "bagli_degil")

    if not onay:
        son = _son_gonderim(int(uid), metin_h, tel_h, an - timedelta(seconds=TEKRAR_SANIYE))
        if son:
            return {"ok": False, "tekrar": True, "durum": str(son.get("durum") or ""), "mesaj": _YINE}, 409

    if numara_fn is None:
        kayit, _neden = _numara_dene(norm)
    else:
        kayit, _neden = numara_fn(norm)
    if kayit == "yok":
        _kaydet("basarisiz", _tekil("szwa:yok"))
        return {"ok": False, "kayitli": False, "mesaj": _KAYITSIZ}, 400
    if kayit != "kayitli":
        neden_k = "ulasilamadi" if _neden in ("baglanti", "yanit_yok", "ulasilamadi") else "oturum"
        return _geri(neden_k)

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
        if sonuc == "gonderildi":
            _durum_yaz(rid, "gonderildi")
        elif sonuc in ("ok", "kuyrukta"):
            _durum_yaz(rid, "kuyrukta")
        elif sonuc == "belirsiz":
            _durum_yaz(rid, "belirsiz")
        else:
            _durum_yaz(rid, "basarisiz")

    threading.Thread(target=_calis, daemon=True, name=f"szwa-{rid}").start()
    return {"ok": True, "durum": "gonderiliyor", "mesaj": _SUREN, "deneme": kim}, 200
