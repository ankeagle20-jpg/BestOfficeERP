# -*- coding: utf-8 -*-
"""Kira borç ufku: pasif + kapanış + N → son borç ayı.

N = customers.kapanis_sonrasi_borc_ay
  0 → yalnız kapanış ayı
  1..12 → kapanış ayı + N ek ay
  None/boş → Hepsi (cap yok; bu aşamada NULL anlamı değişmez)

Dönüş: son borç ayının 1. günü (dahil). None = cap yok.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping


def _coerce_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _add_months(d: date, n: int) -> date:
    y, m = d.year, d.month + int(n)
    while m > 12:
        m -= 12
        y += 1
    while m < 1:
        m += 12
        y -= 1
    return date(y, m, 1)


def _parse_n(raw) -> int | None:
    """Geçerli N (0..12) veya None (Hepsi / geçersiz)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        val = int(s)
    except (TypeError, ValueError):
        return None
    if 0 <= val <= 12:
        return val
    return None


def kira_son_borc_ayi(musteri_row: Mapping[str, Any] | None) -> date | None:
    """Pasif + kapanış dolu + N dolu → son borç ayının 1'i; aksi halde None.

    Örn. kap=2025-09-30, N=0 → 2025-09-01
         kap=2025-09-15, N=3 → 2025-12-01
    """
    src = dict(musteri_row or {})
    durum = str(src.get("durum") or src.get("musteri_durum") or "").strip().lower()
    if durum != "pasif":
        return None
    kap = _coerce_date(src.get("kapanis_tarihi"))
    if not kap:
        return None
    n = _parse_n(src.get("kapanis_sonrasi_borc_ay"))
    if n is None:
        return None
    return _add_months(date(kap.year, kap.month, 1), n)


def kira_borc_cap_exclusive(musteri_row: Mapping[str, Any] | None) -> date | None:
    """Son borç ayından sonraki ayın 1'i (dışlayıcı sınır). Cap yoksa None."""
    son = kira_son_borc_ayi(musteri_row)
    if son is None:
        return None
    return _add_months(son, 1)


def ay_kira_borc_dahil_mi(musteri_row: Mapping[str, Any] | None, yil: int, ay: int) -> bool:
    """Cap yoksa True; varsa ay (yıl, ay) son borç ayına kadar (dahil) True."""
    son = kira_son_borc_ayi(musteri_row)
    if son is None:
        return True
    try:
        cur = date(int(yil), int(ay), 1)
    except (TypeError, ValueError):
        return False
    return cur <= son


def kapanis_ek_fingerprint(raw) -> str:
    """Cache/parmak izi: None/boş → 'H' (Hepsi), 0..12 → rakam. NULL ile 0 ayrılır."""
    n = _parse_n(raw)
    return "H" if n is None else str(n)
