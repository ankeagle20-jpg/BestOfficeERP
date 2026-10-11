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
