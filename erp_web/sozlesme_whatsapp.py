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


def _son_gonderim(uid: int, metin_h: str, tel_h: str, sinir: datetime):
    return fetch_one(
        """
        SELECT id, durum, created_at
        FROM sozlesme_whatsapp_gonderim
        WHERE user_id = %s AND metin_hash = %s AND telefon_hash = %s
          AND durum IN ('gonderiliyor', 'gonderildi', 'belirsiz')
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


def _bagli_varsayilan() -> bool:
    import requests
    from routes.whatsapp_routes import _wa_internal_headers, _wa_url

    try:
        r = requests.get(_wa_url("durum-bak"), headers=_wa_internal_headers(), timeout=(3, 5))
    except Exception:
        return False
    if r.status_code != 200:
        return False
    try:
        data = r.json() if r.content else {}
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    return bool(data.get("bagli")) and bool(data.get("hazir"))


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
    if not isinstance(body, dict) or body.get("ok") is False:
        return "hata"
    return "ok"


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
    anahtar = "szwa:" + metin_h[:12] + ":" + tel_h[:12] + ":" + str(int(uid))
    if onay:
        anahtar = (anahtar + ":" + str(int(an.timestamp() * 1000)))[:120]

    def _kaydet(durum: str, anahtar_deger: str):
        return _ekle(int(uid), mid, buton, durum, maske, tel_h, len(metin), metin_h, anahtar_deger[:120])

    def _tekil(on_ek: str) -> str:
        return (on_ek + ":" + metin_h[:8] + ":" + tel_h[:8] + ":" + str(int(uid)) + ":" + str(int(an.timestamp() * 1000)))[:120]

    def _geri():
        _kaydet("geri_dus", _tekil("szwa:geri"))
        return {"ok": True, "geri_dus": True}, 200

    if not node_yolu_acik():
        return _geri()

    bagli = bagli_fn or _bagli_varsayilan
    if not bagli():
        return _geri()

    if not onay:
        son = _son_gonderim(int(uid), metin_h, tel_h, an - timedelta(seconds=TEKRAR_SANIYE))
        if son:
            if str(son.get("durum") or "") == "gonderiliyor":
                return {"ok": True, "durum": "gonderiliyor", "mesaj": _SUREN}, 200
            return {"ok": False, "tekrar": True, "mesaj": _YINE}, 409

    if numara_fn is None:
        from routes.erp_notlar_routes import _wa_numara_kayitli

        numara = _wa_numara_kayitli
    else:
        numara = numara_fn
    kayit, _neden = numara(norm)
    if kayit == "yok":
        _kaydet("basarisiz", _tekil("szwa:yok"))
        return {"ok": False, "kayitli": False, "mesaj": _KAYITSIZ}, 400
    if kayit != "kayitli":
        return _geri()

    row = _kaydet("gonderiliyor", anahtar)
    if not row:
        return {"ok": True, "durum": "gonderiliyor", "mesaj": _SUREN}, 200
    rid = int(row["id"])
    kuyruk = kuyruk_fn or _kuyruk_varsayilan

    def _calis():
        try:
            sonuc = kuyruk(norm, metin, anahtar[:120])
        except Exception:
            logger.info("sozlesme wa arkaplan hata")
            sonuc = "belirsiz"
        if sonuc == "ok":
            _durum_yaz(rid, "gonderildi")
        elif sonuc == "belirsiz":
            _durum_yaz(rid, "belirsiz")
        else:
            _durum_yaz(rid, "basarisiz")

    threading.Thread(target=_calis, daemon=True, name=f"szwa-{rid}").start()
    return {"ok": True, "durum": "gonderiliyor"}, 200
