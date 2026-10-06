"""Sözleşme ödeme linki. Bayrak varsayılan kapalı. Satın Al callback'inden bağımsız."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from db import db, execute, execute_returning, fetch_all, fetch_one
from db import _tenant_schema_for_request

logger = logging.getLogger(__name__)

OID_PREFIX = "OLNK"
NEUTRAL = "Bu ödeme bağlantısı kullanılamıyor."
_DEFAULT_PUBLIC_BASE = "https://payafin.com"
SILINEMEZ = "Kart ödemesi, silinemez"
_HAZIR = False
_HITS: dict[str, list[float]] = {}
_HITS_LOCK = threading.Lock()
IST = ZoneInfo("Europe/Istanbul")
_WA_MESAJ_LIMIT = 1000
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,128}")
_VARSAYILAN_IMZA = "Ofisbir Ofis ve Danışmanlık Hizmetleri A.Ş."


def bayrak_acik() -> bool:
    """ODEME_LINKI_ENABLED yoksa veya kapalıysa False. PAYTR_CALLBACK_APPLY okunmaz."""
    v = (os.environ.get("ODEME_LINKI_ENABLED") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def ofisbir_istegi() -> bool:
    schema = _tenant_schema_for_request()
    if schema not in (None, "", "public"):
        return False
    try:
        from flask import g, has_request_context

        if has_request_context():
            slug = getattr(g, "tenant_slug", None)
            if slug not in (None, "", "public"):
                return False
    except Exception:
        return False
    return True


def ozellik_acik() -> bool:
    return bayrak_acik() and ofisbir_istegi()


def public_odeme_base() -> str:
    """Ödeme linki ve PayTR dönüş adresinin kökü.

    İstek host'u kullanılmaz. Varsayılan https://payafin.com.
    ODEME_LINKI_PUBLIC_BASE ile kök değiştirilebilir.
    """
    raw = (os.environ.get("ODEME_LINKI_PUBLIC_BASE") or _DEFAULT_PUBLIC_BASE).strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except Exception:
        return _DEFAULT_PUBLIC_BASE
    if parts.scheme not in ("https", "http") or not parts.netloc:
        return _DEFAULT_PUBLIC_BASE
    return f"{parts.scheme}://{parts.netloc}"


def ensure_tablolar() -> None:
    global _HAZIR
    if _HAZIR:
        return
    execute(
        """
        CREATE TABLE IF NOT EXISTS public.odeme_linkleri (
            id BIGSERIAL PRIMARY KEY,
            token_hash TEXT NOT NULL,
            merchant_oid TEXT NOT NULL,
            musteri_id INTEGER NOT NULL,
            sozlesme_id INTEGER,
            tutar_kurus INTEGER NOT NULL,
            aciklama TEXT NOT NULL DEFAULT '',
            durum TEXT NOT NULL DEFAULT 'bekliyor',
            olusturan_kullanici_id INTEGER,
            expires_at TIMESTAMPTZ NOT NULL,
            paytr_init_at TIMESTAMPTZ,
            paytr_token TEXT,
            paid_at TIMESTAMPTZ,
            tahsilat_id INTEGER,
            makbuz_no TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT odeme_linkleri_durum_chk
                CHECK (durum IN ('bekliyor', 'odendi', 'suresi_doldu', 'iptal', 'basarisiz')),
            CONSTRAINT odeme_linkleri_tutar_chk CHECK (tutar_kurus > 0)
        )
        """
    )
    execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS odeme_linkleri_token_hash_uidx
        ON public.odeme_linkleri (token_hash)
        """
    )
    execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS odeme_linkleri_merchant_oid_uidx
        ON public.odeme_linkleri (merchant_oid)
        """
    )
    execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS odeme_linkleri_tahsilat_id_uidx
        ON public.odeme_linkleri (tahsilat_id)
        WHERE tahsilat_id IS NOT NULL
        """
    )
    execute(
        """
        CREATE INDEX IF NOT EXISTS odeme_linkleri_musteri_idx
        ON public.odeme_linkleri (musteri_id, created_at DESC)
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS public.odeme_link_olaylari (
            id BIGSERIAL PRIMARY KEY,
            link_id BIGINT NOT NULL REFERENCES public.odeme_linkleri (id) ON DELETE CASCADE,
            olay TEXT NOT NULL,
            merchant_oid TEXT,
            gelen_tutar_kurus TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    execute(
        """
        CREATE INDEX IF NOT EXISTS odeme_link_olaylari_link_idx
        ON public.odeme_link_olaylari (link_id, created_at)
        """
    )
    for stmt in (
        "ALTER TABLE public.odeme_linkleri ADD COLUMN IF NOT EXISTS whatsapp_durum TEXT",
        "ALTER TABLE public.odeme_linkleri ADD COLUMN IF NOT EXISTS whatsapp_sayac INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE public.odeme_linkleri ADD COLUMN IF NOT EXISTS whatsapp_deneme INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE public.odeme_linkleri ADD COLUMN IF NOT EXISTS whatsapp_son_deneme_at TIMESTAMPTZ",
        "ALTER TABLE public.odeme_linkleri ADD COLUMN IF NOT EXISTS whatsapp_telefon TEXT",
        "ALTER TABLE public.odeme_linkleri ADD COLUMN IF NOT EXISTS whatsapp_mesaj TEXT",
    ):
        try:
            execute(stmt)
        except Exception:
            logger.info("odeme linki wa kolon")
    _HAZIR = True


def normalize_wa_telefon(raw) -> str:
    """+90, 00 ve baştaki 0 biçimini ülke kodlu rakama çevirir. Geçersizse boş."""
    s = re.sub(r"\D", "", str(raw or ""))
    if s.startswith("00"):
        s = s[2:]
    if s.startswith("0") and len(s) == 11:
        s = "90" + s[1:]
    elif len(s) == 10 and s.startswith("5"):
        s = "90" + s
    if not re.fullmatch(r"[1-9]\d{10,14}", s or ""):
        return ""
    return s


def wa_kisi_secenekleri(yetkililer=None, kyc=None, kart_telefon="", kart_yetkili="") -> list[dict]:
    """Yetkili adı ve telefonu. Birincil önce, aynı numara bir kez. Ad yoksa ad boş."""
    adaylar: list[tuple] = []

    def ekle(oncelik, sira, ad, tel):
        numara = str(tel or "").strip()
        if not numara:
            return
        adaylar.append((oncelik, sira, len(adaylar), str(ad or "").strip(), numara))

    for row in yetkililer or []:
        if not isinstance(row, dict):
            continue
        ad = row.get("ad_soyad") or row.get("ad") or ""
        try:
            sira = int(row.get("sira") or 0)
        except (TypeError, ValueError):
            sira = 0
        oncelik = 0 if row.get("birincil") else 1
        ekle(oncelik, sira, ad, row.get("tel"))
        ekle(oncelik, sira, ad, row.get("tel2"))
    if not adaylar and isinstance(kyc, dict):
        ad = kyc.get("yetkili_adsoyad") or ""
        ekle(0, 0, ad, kyc.get("yetkili_tel"))
        ekle(0, 0, ad, kyc.get("yetkili_tel2"))
    if not adaylar:
        ad = ""
        if isinstance(kyc, dict):
            ad = str(kyc.get("yetkili_adsoyad") or "").strip()
        if not ad:
            for row in yetkililer or []:
                if isinstance(row, dict) and row.get("birincil") and str(row.get("ad_soyad") or "").strip():
                    ad = str(row.get("ad_soyad") or "").strip()
                    break
        if not ad:
            for row in yetkililer or []:
                if isinstance(row, dict) and str(row.get("ad_soyad") or "").strip():
                    ad = str(row.get("ad_soyad") or "").strip()
                    break
        if not ad:
            ad = str(kart_yetkili or "").strip()
        ekle(0, 0, ad, kart_telefon)
    else:
        ekle(2, 0, str(kart_yetkili or "").strip(), kart_telefon)
    adaylar.sort(key=lambda x: (x[0], x[1], x[2]))
    gorulen = set()
    out = []
    for _oncelik, _sira, _i, ad, tel in adaylar:
        anahtar = normalize_wa_telefon(tel) or re.sub(r"\D", "", tel)
        if not anahtar or anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        out.append({"ad": ad, "telefon": tel})
    return out


def whatsapp_etiket(durum: str) -> str:
    kod = str(durum or "").strip()
    return {
        "gonderildi": "Gönderildi",
        "gonderiliyor": "Gönderiliyor",
        "basarisiz": "Gönderilemedi",
        "basarisiz_kalici": "Gönderilemedi",
        "belirsiz": "Gönderim doğrulanamadı, WhatsApp'ı kontrol edin",
    }.get(kod, "")


def _tarih_ist(value) -> str:
    if not isinstance(value, datetime):
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST).strftime("%d.%m.%Y %H:%M")


def ofis_imzasi() -> str:
    try:
        from firma_profil import firma_profil_oku

        unvan = str((firma_profil_oku() or {}).get("unvan") or "").strip()
        if unvan:
            return unvan[:160]
    except Exception:
        logger.info("odeme linki imza")
    return _VARSAYILAN_IMZA


def kanonik_odeme_url(token: str, token_hash_kayit: str) -> str:
    """İstemci adresini kullanmaz. Token özeti kayıtla eşleşirse payafin kökünden kurar."""
    ham = str(token or "").strip()
    if not _TOKEN_RE.fullmatch(ham):
        return ""
    kayit = str(token_hash_kayit or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", kayit):
        return ""
    if not hmac.compare_digest(token_hash(ham), kayit):
        return ""
    return public_odeme_base() + "/odeme/" + ham


def wa_varsayilan_mesaj(ad, aciklama, tutar, url, expires_at, imza) -> str:
    isim = str(ad or "").strip() or "Müşteri"
    acik = str(aciklama or "").strip() or "Ödeme"
    imza_metin = str(imza or "").strip() or _VARSAYILAN_IMZA
    return (
        f"Sayın {isim},\n"
        f"{acik}\n"
        f"Tutar: {tutar} TL\n"
        f"Ödeme linki: {url}\n"
        f"Son geçerlilik: {_tarih_ist(expires_at)}\n"
        f"{imza_metin}"
    )


def wa_mesaj_dogrula(mesaj: str, kanonik_url: str) -> str:
    """Boşsa uygun. Aksi halde kısa hata metni. Başka URL kabul edilmez."""
    metin = str(mesaj or "").strip()
    url = str(kanonik_url or "").strip()
    if not url or url not in metin:
        return "Mesajda ödeme linki olmalı"
    if len(metin) > _WA_MESAJ_LIMIT:
        return "Mesaj çok uzun"
    for bulunan in _URL_RE.findall(metin):
        if bulunan.rstrip(".,)") != url:
            return "Mesajda yalnız ödeme linki olabilir"
    return ""


def link_wa_gonderilebilir(durum: str, expires_at, now: datetime | None = None) -> bool:
    if str(durum or "") != "bekliyor":
        return False
    if not isinstance(expires_at, datetime):
        return False
    an = now or datetime.now(timezone.utc)
    exp = expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if an.tzinfo is None:
        an = an.replace(tzinfo=timezone.utc)
    return exp >= an


def wa_tekrar_onay_gerekli(durum: str) -> bool:
    return str(durum or "") in ("gonderildi", "belirsiz")


def wa_sonraki_basarisiz(deneme, gecerli: bool) -> tuple[str, int]:
    yeni = int(deneme or 0) + 1
    if yeni >= 5 or not gecerli:
        return "basarisiz_kalici", yeni
    return "basarisiz", yeni


def token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def yeni_merchant_oid() -> str:
    oid = OID_PREFIX + secrets.token_hex(8)
    if not oid.isalnum() or oid.startswith("INV"):
        raise RuntimeError("merchant_oid formati")
    return oid


def kurus_from_tutar(raw) -> int:
    try:
        dec = Decimal(str(raw).replace(",", ".").strip())
    except Exception as exc:
        raise ValueError("tutar") from exc
    kurus = int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if kurus <= 0:
        raise ValueError("tutar")
    return kurus


def tutar_tl(kurus: int) -> str:
    return f"{(Decimal(int(kurus)) / Decimal(100)).quantize(Decimal('0.01'))}"


def mask_ad(name: str) -> str:
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return "Müşteri"
    out = []
    for part in parts[:3]:
        out.append(part[0] + "***")
    return " ".join(out)


def hiz_siniri(ip: str, limit: int = 30, pencere: int = 600) -> bool:
    now = time.time()
    key = str(ip or "")[:64] or "-"
    with _HITS_LOCK:
        arr = [t for t in _HITS.get(key, []) if now - t < pencere]
        if len(arr) >= limit:
            _HITS[key] = arr
            return False
        arr.append(now)
        _HITS[key] = arr
        return True


def istemci_ip() -> str:
    try:
        from flask import request

        xff = (request.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            return (xff.split(",")[0].strip() or "127.0.0.1")[:39]
        return (request.remote_addr or "127.0.0.1")[:39]
    except Exception:
        return "127.0.0.1"


def ekstre_kalan(musteri_id: int) -> float:
    from routes.giris_routes import (
        _cari_ekstre_hareketler,
        _cari_ekstre_varsayilan_bas_bit,
        _musteri_reel_donem_manual_dict_from_db,
    )

    bas, bit = _cari_ekstre_varsayilan_bas_bit(int(musteri_id))
    hareketler = _cari_ekstre_hareketler(
        int(musteri_id),
        bas,
        bit,
        0,
        use_reel_cells=True,
        tahsilat_borca_hizala=True,
        kira_nakit_ekstre=False,
        reel_client_override=_musteri_reel_donem_manual_dict_from_db(int(musteri_id)),
        panel_tahsil_by_iso=None,
    )
    if not hareketler:
        return 0.0
    try:
        return round(float(hareketler[-1].get("bakiye") or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _olay(cur, link_id: int, olay: str, merchant_oid: str, gelen: str) -> None:
    cur.execute(
        """
        INSERT INTO public.odeme_link_olaylari (link_id, olay, merchant_oid, gelen_tutar_kurus)
        VALUES (%s, %s, %s, %s)
        """,
        (int(link_id), str(olay)[:40], str(merchant_oid or "")[:64], str(gelen or "")[:32]),
    )


def _tahsilat_yaz(cur, link: dict) -> tuple[int, str]:
    from routes.faturalar_routes import _tahsilat_icin_makbuz_no_sec_cursor

    cur.execute("SELECT pg_advisory_xact_lock(hashtext('tahsilat_makbuz_no_alloc')::bigint)")
    makbuz_no = _tahsilat_icin_makbuz_no_sec_cursor(cur, None)
    tutar = float(tutar_tl(int(link["tutar_kurus"])))
    aciklama = str(link.get("aciklama") or "").strip()[:500] or "Ödeme linki"
    cur.execute(
        """
        INSERT INTO tahsilatlar (
            musteri_id, customer_id, fatura_id, tutar, odeme_turu,
            tahsilat_tarihi, aciklama, makbuz_no, kaynak
        ) VALUES (%s, %s, NULL, %s, 'kredi_karti', CURRENT_DATE, %s, %s, 'odeme_linki')
        RETURNING id
        """,
        (int(link["musteri_id"]), int(link["musteri_id"]), tutar, aciklama, makbuz_no),
    )
    row = cur.fetchone()
    if not row or not row.get("id"):
        raise RuntimeError("tahsilat yazilamadi")
    return int(row["id"]), str(makbuz_no)


def _pdf_ve_cache(musteri_id: int, tahsilat_id: int) -> None:
    try:
        from flask import current_app

        app_pdf = current_app._get_current_object()
    except Exception:
        app_pdf = None

    def _pdf():
        try:
            from routes.faturalar_routes import UPLOAD_MUSTERI_DOSYALARI, build_makbuz_pdf
            import os

            row = fetch_one(
                """
                SELECT id, fatura_id, makbuz_no, tutar, odeme_turu, tahsilat_tarihi,
                       aciklama, created_at
                FROM tahsilatlar WHERE id = %s
                """,
                (int(tahsilat_id),),
            )
            if not row:
                return
            cust = fetch_one("SELECT name FROM customers WHERE id = %s", (int(musteri_id),))
            ad = (cust or {}).get("name") or "Müşteri"
            os.makedirs(UPLOAD_MUSTERI_DOSYALARI, exist_ok=True)
            fn = f"Tahsilat_{row.get('makbuz_no')}_{int(tahsilat_id)}.pdf"
            path = os.path.join(UPLOAD_MUSTERI_DOSYALARI, fn)
            from db import fetch_all

            hesaplar = fetch_all(
                "SELECT banka_adi, hesap_adi, iban FROM banka_hesaplar "
                "WHERE COALESCE(is_active::int, 1) = 1 AND (iban IS NOT NULL AND iban != '') "
                "ORDER BY banka_adi"
            )
            pdf = build_makbuz_pdf(dict(row), ad, None, banka_hesaplar=hesaplar)
            with open(path, "wb") as fh:
                fh.write(pdf)
        except Exception:
            logger.exception("odeme linki makbuz pdf")

    if app_pdf:
        def _run():
            with app_pdf.app_context():
                _pdf()

        threading.Thread(target=_run, daemon=True).start()
    else:
        _pdf()
    try:
        from routes.giris_routes import _cari_ekstre_cache_invalidate_musteri

        _cari_ekstre_cache_invalidate_musteri(int(musteri_id))
    except Exception:
        logger.exception("odeme linki ekstre cache")


def callback_olnk(merchant_oid: str, status: str, total_amount: str) -> None:
    """Hash doğrulanmış OLNK bildirimi. Bayraktan bağımsız: para varsa yazar."""
    ensure_tablolar()
    yazilan = None
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM public.odeme_linkleri
            WHERE merchant_oid = %s
            FOR UPDATE
            """,
            (merchant_oid,),
        )
        link = cur.fetchone()
        if not link:
            return
        lid = int(link["id"])
        durum = str(link.get("durum") or "")
        if durum == "odendi" or link.get("tahsilat_id"):
            _olay(cur, lid, "mukerrer", merchant_oid, total_amount)
            return
        if str(total_amount or "").strip() != str(int(link["tutar_kurus"])):
            _olay(cur, lid, "tutar_uyusmazligi", merchant_oid, total_amount)
            return
        if str(status or "").strip().lower() != "success":
            _olay(cur, lid, "callback_hata", merchant_oid, total_amount)
            return
        if durum not in ("bekliyor", "suresi_doldu", "iptal"):
            _olay(cur, lid, "callback_hata", merchant_oid, total_amount)
            return
        exp = link.get("expires_at")
        now = datetime.now(timezone.utc)
        if exp is not None and getattr(exp, "tzinfo", None) is None:
            exp = exp.replace(tzinfo=timezone.utc)
        gec = durum == "suresi_doldu" or (exp is not None and exp < now)
        isaret = None
        if durum == "iptal":
            isaret = "iptal_sonrasi_odeme"
        elif gec:
            isaret = "gec_odeme"
        tahsilat_id, makbuz_no = _tahsilat_yaz(cur, link)
        cur.execute(
            """
            UPDATE public.odeme_linkleri
            SET durum = 'odendi',
                paid_at = NOW(),
                tahsilat_id = %s,
                makbuz_no = %s,
                updated_at = NOW()
            WHERE id = %s
              AND durum IN ('bekliyor', 'suresi_doldu', 'iptal')
              AND tahsilat_id IS NULL
            """,
            (tahsilat_id, makbuz_no, lid),
        )
        if cur.rowcount != 1:
            raise RuntimeError("odeme linki guncellenemedi")
        _olay(cur, lid, "callback_basari", merchant_oid, total_amount)
        if isaret:
            _olay(cur, lid, isaret, merchant_oid, total_amount)
        yazilan = (int(link["musteri_id"]), tahsilat_id)
    if yazilan:
        _pdf_ve_cache(yazilan[0], yazilan[1])


def link_olustur(
    musteri_id: int,
    tutar_kurus: int,
    aciklama: str,
    gun: int,
    kullanici_id: int | None,
    sozlesme_id: int | None = None,
) -> tuple[str, dict]:
    ensure_tablolar()
    if gun < 1 or gun > 30:
        raise ValueError("gun")
    token = secrets.token_urlsafe(32)
    oid = yeni_merchant_oid()
    exp = datetime.now(timezone.utc) + timedelta(days=int(gun))
    row = None
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO public.odeme_linkleri (
                token_hash, merchant_oid, musteri_id, sozlesme_id, tutar_kurus,
                aciklama, durum, olusturan_kullanici_id, expires_at
            ) VALUES (%s, %s, %s, %s, %s, %s, 'bekliyor', %s, %s)
            RETURNING id, merchant_oid, tutar_kurus, aciklama, durum, expires_at, created_at
            """,
            (
                token_hash(token),
                oid,
                int(musteri_id),
                int(sozlesme_id) if sozlesme_id else None,
                int(tutar_kurus),
                str(aciklama or "").strip()[:500],
                int(kullanici_id) if kullanici_id else None,
                exp,
            ),
        )
        row = cur.fetchone()
        _olay(cur, int(row["id"]), "olusturuldu", oid, str(int(tutar_kurus)))
    return token, dict(row)


def tahsilat_kaynak(tahsilat_id: int) -> str:
    row = fetch_one(
        "SELECT COALESCE(kaynak, '') AS kaynak FROM tahsilatlar WHERE id = %s",
        (int(tahsilat_id),),
    )
    return str((row or {}).get("kaynak") or "")


def _wa_olay(link_id: int, olay: str, kod: str = "") -> None:
    """Olay kaydı. Telefon, mesaj ve token yazılmaz."""
    execute(
        """
        INSERT INTO public.odeme_link_olaylari (link_id, olay, merchant_oid, gelen_tutar_kurus)
        VALUES (%s, %s, NULL, %s)
        """,
        (int(link_id), str(olay)[:40], str(kod or "")[:32] or None),
    )


def whatsapp_sahiplen(link_id: int, telefon: str, mesaj: str, onay: bool):
    """Atomik sahiplenme. Dönüş satırı ya da None. Sayacı yalnız yeni gönderimde artırır."""
    return execute_returning(
        """
        UPDATE public.odeme_linkleri
        SET whatsapp_durum = 'gonderiliyor',
            whatsapp_son_deneme_at = NOW(),
            whatsapp_telefon = %s,
            whatsapp_mesaj = %s,
            whatsapp_sayac = CASE
                WHEN COALESCE(whatsapp_durum, '') IN ('gonderildi', 'belirsiz')
                THEN COALESCE(whatsapp_sayac, 0) + 1
                ELSE COALESCE(whatsapp_sayac, 0)
            END,
            updated_at = NOW()
        WHERE id = %s
          AND durum = 'bekliyor'
          AND expires_at > NOW()
          AND (
            COALESCE(whatsapp_durum, '') IN ('', 'basarisiz')
            OR (
              %s
              AND COALESCE(whatsapp_durum, '') IN ('gonderildi', 'belirsiz')
            )
            OR (
              COALESCE(whatsapp_durum, '') = 'gonderiliyor'
              AND whatsapp_son_deneme_at IS NOT NULL
              AND whatsapp_son_deneme_at < NOW() - INTERVAL '3 minutes'
            )
          )
        RETURNING id, whatsapp_sayac, whatsapp_deneme, whatsapp_telefon, whatsapp_mesaj,
                  expires_at, durum
        """,
        (telefon, mesaj, int(link_id), bool(onay)),
    )


def whatsapp_sonuc_yaz(link_id: int, sayac: int, durum: str, deneme: int | None, kod: str) -> None:
    olay = "whatsapp_gonderildi" if durum == "gonderildi" else "whatsapp_basarisiz"
    n = execute(
        """
        UPDATE public.odeme_linkleri
        SET whatsapp_durum = %s,
            whatsapp_deneme = COALESCE(%s, whatsapp_deneme),
            updated_at = NOW()
        WHERE id = %s
          AND whatsapp_durum = 'gonderiliyor'
          AND whatsapp_sayac = %s
        """,
        (durum, deneme, int(link_id), int(sayac)),
    )
    if n:
        _wa_olay(link_id, olay, "" if durum == "gonderildi" else kod)


def whatsapp_gonder_isle(link_id: int, sayac: int) -> str:
    row = fetch_one(
        """
        SELECT whatsapp_telefon, whatsapp_mesaj, whatsapp_deneme, whatsapp_sayac,
               expires_at, durum
        FROM public.odeme_linkleri
        WHERE id = %s
        """,
        (int(link_id),),
    )
    if not row or int(row.get("whatsapp_sayac") or 0) != int(sayac):
        return "atlandi"
    tel = str(row.get("whatsapp_telefon") or "").strip()
    mesaj = str(row.get("whatsapp_mesaj") or "").strip()
    if not tel or not mesaj:
        durum, yeni = wa_sonraki_basarisiz(row.get("whatsapp_deneme"), False)
        whatsapp_sonuc_yaz(link_id, sayac, durum, yeni, "telefon_yok")
        return durum
    from routes.erp_notlar_routes import _wa_node_gonder

    sonuc, tur = _wa_node_gonder(
        int(link_id), tel, mesaj, "public", f"olnk:{int(link_id)}:{int(sayac)}"
    )
    gecerli = link_wa_gonderilebilir(str(row.get("durum") or ""), row.get("expires_at"))
    if sonuc == "ok":
        whatsapp_sonuc_yaz(link_id, sayac, "gonderildi", None, "")
        return "gonderildi"
    if sonuc == "belirsiz":
        whatsapp_sonuc_yaz(link_id, sayac, "belirsiz", None, "yanit_yok")
        return "belirsiz"
    durum, yeni = wa_sonraki_basarisiz(row.get("whatsapp_deneme"), gecerli)
    whatsapp_sonuc_yaz(link_id, sayac, durum, yeni, tur or "http")
    return durum


def whatsapp_yeniden_dene() -> None:
    """Takılı gönderimi toparlar, yalnız başarısız denemeyi yineler."""
    if not bayrak_acik() or not ofisbir_istegi():
        return
    ensure_tablolar()
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE public.odeme_linkleri
            SET whatsapp_deneme = COALESCE(whatsapp_deneme, 0) + 1,
                whatsapp_durum = CASE
                    WHEN COALESCE(whatsapp_deneme, 0) + 1 >= 5
                      OR expires_at <= NOW()
                      OR durum <> 'bekliyor'
                    THEN 'basarisiz_kalici'
                    ELSE 'basarisiz'
                END,
                updated_at = NOW()
            WHERE whatsapp_durum = 'gonderiliyor'
              AND whatsapp_son_deneme_at IS NOT NULL
              AND whatsapp_son_deneme_at < NOW() - INTERVAL '3 minutes'
            RETURNING id
            """
        )
        takili = [int(r["id"]) for r in (cur.fetchall() or []) if r.get("id")]
    for lid in takili:
        _wa_olay(lid, "whatsapp_basarisiz", "surec_kesildi")
    rows = fetch_all(
        """
        SELECT id, whatsapp_telefon, whatsapp_mesaj
        FROM public.odeme_linkleri
        WHERE whatsapp_durum = 'basarisiz'
          AND COALESCE(whatsapp_deneme, 0) < 5
          AND durum = 'bekliyor'
          AND expires_at > NOW()
          AND (
            whatsapp_son_deneme_at IS NULL
            OR whatsapp_son_deneme_at <= NOW() - INTERVAL '2 minutes'
          )
        ORDER BY id
        LIMIT 10
        """
    )
    for row in rows or []:
        if not str(row.get("whatsapp_telefon") or "").strip():
            continue
        owned = whatsapp_sahiplen(
            int(row["id"]),
            str(row.get("whatsapp_telefon") or ""),
            str(row.get("whatsapp_mesaj") or ""),
            False,
        )
        if not owned:
            continue
        try:
            whatsapp_gonder_isle(int(owned["id"]), int(owned["whatsapp_sayac"]))
        except Exception as e:
            logger.info("odeme linki wa yeniden link_id=%s hata=%s", int(row["id"]), type(e).__name__)
