# -*- coding: utf-8 -*-
"""Kira borç ufku: pasif + kapanış + N → son borç ayı.

N = customers.kapanis_sonrasi_borc_ay
  0 → yalnız kapanış ayı
  1..12 → kapanış ayı + N ek ay
  None/boş → Hepsi:
    KAPANIS_HEPSI_SOZLESME_YILI!=1 → cap yok (None; eski)
    =1 → çıktığı ayı içeren sözleşme yılının sonuna (giriş ayından 12'şer)

Dönüş: son borç ayının 1. günü (dahil). None = cap yok.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Mapping

_LOG = logging.getLogger(__name__)
_ENV_HEPSI_SOZ = "KAPANIS_HEPSI_SOZLESME_YILI"


def hepsi_sozlesme_yili_aktif() -> bool:
    """Yalnız tam '1' açık; başka her şey kapalı."""
    return str(os.environ.get(_ENV_HEPSI_SOZ) or "").strip() == "1"


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


def _giris_tarihi(musteri_row: Mapping[str, Any]) -> date | None:
    """Grid ile aynı öncelik: sozlesme_tarihi → rent_start_date."""
    return _coerce_date(musteri_row.get("sozlesme_tarihi")) or _coerce_date(
        musteri_row.get("rent_start_date")
    )


def hepsi_sozlesme_yili_son_borc_ayi(giris: date, kap: date) -> date:
    """Çıktığı ayı içeren sözleşme yılının son ayının 1'i.

    m = (kap_ayı − giriş_ayı) ay farkı (aynı ay → 0).
    son = giriş_ayı + 12*(m//12 + 1) − 1 ay.
    Örn. giriş 2023-08, kap 2025-09 → m=25 → 2026-07.
    """
    g = date(giris.year, giris.month, 1)
    k = date(kap.year, kap.month, 1)
    m = (k.year - g.year) * 12 + (k.month - g.month)
    if m < 0:
        m = 0
    return _add_months(g, 12 * (m // 12 + 1) - 1)


def kira_son_borc_ayi(musteri_row: Mapping[str, Any] | None) -> date | None:
    """Pasif + kapanış → son borç ayı (dahil); cap yoksa None.

    N dolu: kapanış ayı + N.
    N NULL + bayrak kapalı: None.
    N NULL + bayrak açık + giriş var: sözleşme yılı sonu.
    N NULL + bayrak açık + giriş yok: None + log.
    """
    src = dict(musteri_row or {})
    durum = str(src.get("durum") or src.get("musteri_durum") or "").strip().lower()
    if durum != "pasif":
        return None
    kap = _coerce_date(src.get("kapanis_tarihi"))
    if not kap:
        return None
    n = _parse_n(src.get("kapanis_sonrasi_borc_ay"))
    if n is not None:
        return _add_months(date(kap.year, kap.month, 1), n)
    # Hepsi (N NULL)
    if not hepsi_sozlesme_yili_aktif():
        return None
    giris = _giris_tarihi(src)
    if not giris:
        mid = src.get("id") or src.get("musteri_id") or "?"
        _LOG.warning(
            "kira_son_borc_ayi Hepsi+bayrak: giriş tarihi yok mid=%s (cap yok)",
            mid,
        )
        return None
    return hepsi_sozlesme_yili_son_borc_ayi(giris, kap)


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
    """Cache/parmak izi: N dolu → rakam; Hepsi → 'H' (eski) veya 'H12' (bayrak açık)."""
    n = _parse_n(raw)
    if n is not None:
        return str(n)
    return "H12" if hepsi_sozlesme_yili_aktif() else "H"
