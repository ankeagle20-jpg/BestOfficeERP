# -*- coding: utf-8 -*-
"""Kart Kaydet ve POST /giris/api/musteri/<id>/durum ortak yazma alanları."""
from __future__ import annotations

from datetime import date, datetime


def parse_kapanis_tarihi(s):
    """Formdan gelen kapanış tarihi (YYYY-MM-DD veya GG.AA.YYYY)."""
    if not s:
        return None
    s = str(s).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def tarih_tr(d: date | None) -> str:
    """GG.AA.YYYY; None → boş."""
    if not isinstance(d, date):
        return ""
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def resolve_giris_tarihi(sozlesme_tarihi=None, rent_start_date=None) -> date | None:
    """Proje kuralı: sozlesme_tarihi → yoksa rent_start_date (kira_kapanis_cap._giris_tarihi)."""
    from kira_kapanis_cap import _giris_tarihi

    return _giris_tarihi(
        {"sozlesme_tarihi": sozlesme_tarihi, "rent_start_date": rent_start_date}
    )


def dogrula_kapanis_giris(kap, giris) -> str | None:
    """kapanis < giris → Türkçe hata; kap boş veya giris boş → None (doğrulama yok / eşit OK)."""
    if not isinstance(kap, date):
        return None
    if not isinstance(giris, date):
        return None
    if kap < giris:
        return (
            "Kapanış tarihi, giriş tarihinden önce olamaz (giriş: %s)"
            % tarih_tr(giris)
        )
    return None


def musteri_giris_tarihi_db(musteri_id) -> date | None:
    """customers.rent_start_date + son musteri_kyc.sozlesme_tarihi → giriş."""
    try:
        mid = int(musteri_id)
    except (TypeError, ValueError):
        return None
    if mid <= 0:
        return None
    try:
        from db import fetch_one

        row = fetch_one(
            """
            SELECT c.rent_start_date::date AS rent_start,
                   mk.sozlesme_tarihi::date AS sozlesme_tarihi
            FROM customers c
            LEFT JOIN LATERAL (
                SELECT sozlesme_tarihi
                FROM musteri_kyc
                WHERE musteri_id = c.id
                ORDER BY id DESC
                LIMIT 1
            ) mk ON TRUE
            WHERE c.id = %s
            """,
            (mid,),
        )
    except Exception:
        return None
    if not row:
        return None
    return resolve_giris_tarihi(
        row.get("sozlesme_tarihi"), row.get("rent_start")
    )


def musteri_kapanis_tarihi_db(musteri_id) -> date | None:
    """customers.kapanis_tarihi (salt okuma)."""
    try:
        mid = int(musteri_id)
    except (TypeError, ValueError):
        return None
    if mid <= 0:
        return None
    try:
        from db import fetch_one

        row = fetch_one(
            "SELECT kapanis_tarihi::date AS kap FROM customers WHERE id = %s",
            (mid,),
        )
    except Exception:
        return None
    kap = (row or {}).get("kap")
    return kap if isinstance(kap, date) else parse_kapanis_tarihi(kap)


def normalize_musteri_durum_kapanis(data):
    """durum: aktif|pasif; pasif değilse kapanis_tarihi None."""
    dr = (data.get("durum") or "aktif").strip().lower()
    if dr not in ("aktif", "pasif"):
        dr = "aktif"
    kap = parse_kapanis_tarihi(data.get("kapanis_tarihi")) if dr == "pasif" else None
    return dr, kap


def normalize_kapanis_sonrasi_borc_ay(data, durum: str | None = None):
    """Pasif: kapanış sonrası ek ay (0=sadece kapanış, 1..=kapanış+N), boş/None=Hepsi."""
    dr = (durum or data.get("durum") or "aktif").strip().lower()
    if dr != "pasif":
        return None
    raw = data.get("kapanis_sonrasi_borc_ay")
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        val = int(s)
    except (TypeError, ValueError):
        return None
    return val if 0 <= val <= 12 else None


def musteri_durum_yaz_alanlari(data, *, kati: bool = False, oda_bosalt: bool = True):
    """durum + kapanış + is_active; oda_bosalt=True ise pasifte oda_sil.

    kati=True: durum aktif|pasif değilse None (400).
    kati=False: geçersiz durum aktif sayılır (kart kaydet varsayılanı).
    oda_bosalt=True: /durum (pill/toplu) — pasifte oda NULL.
    oda_bosalt=False: /giris/kaydet — oda kolonuna dokunma (oda_sil False).
    Pasifte boş kapanış → bugün. Aktifte kapanış alanları None, is_active True.
    """
    payload = dict(data or {})
    raw = (payload.get("durum") or "").strip().lower()
    if raw not in ("aktif", "pasif"):
        if kati:
            return None
        raw = "aktif"
    payload["durum"] = raw
    if raw == "pasif" and not str(payload.get("kapanis_tarihi") or "").strip():
        payload["kapanis_tarihi"] = date.today().isoformat()
    dr, kap = normalize_musteri_durum_kapanis(payload)
    kap_borc = normalize_kapanis_sonrasi_borc_ay(payload, dr)
    return {
        "durum": dr,
        "kapanis_tarihi": kap,
        "kapanis_sonrasi_borc_ay": kap_borc,
        "is_active": dr == "aktif",
        "oda_sil": bool(oda_bosalt) and dr == "pasif",
    }


def musteri_durum_yaz_json(alanlar) -> dict:
    """API cevabı: tarih ISO, yoksa None."""
    if not alanlar:
        return {}
    kap = alanlar.get("kapanis_tarihi")
    return {
        "durum": alanlar["durum"],
        "kapanis_tarihi": kap.isoformat() if kap else None,
        "kapanis_sonrasi_borc_ay": alanlar.get("kapanis_sonrasi_borc_ay"),
        "is_active": bool(alanlar.get("is_active")),
    }
