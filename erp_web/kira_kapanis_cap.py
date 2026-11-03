# -*- coding: utf-8 -*-
"""Kira borç ufku: pasif + kapanış + N → son borç ayı.

N = customers.kapanis_sonrasi_borc_ay
  0 → varsayılan: yalnız kapanış ayı
      KAPANIS_N0_KAPANIS_AYI_HARIC=1 → kapanış ayı − 1 (kapanış ayı borç sayılmaz; Aday A)
  1..12 → varsayılan (Aday B eski): kapanış ayı + N
      KAPANIS_N0_KAPANIS_AYI_HARIC=1 ve KAPANIS_N_KAPANIS_AYI_DAHIL=1 →
      kapanış ayı + (N−1)  (N=kapanış ayı DAHİL kaç ay; N=1→yalnız kap)
  None/boş → Hepsi:
    KAPANIS_HEPSI_SOZLESME_YILI!=1 → cap yok (None; eski)
    =1 → çıktığı ayı içeren sözleşme yılının sonuna
      (giriş ayı 1..12 ile hizalı; sozlesme_bitis / kira_suresi_ay kullanılmaz)

KAPANIS_N_KAPANIS_AYI_DAHIL yalnız N0 bayrağı da açıkken anlamlı;
N0 kapalı + yeni bayrak açık → yeni bayrak etkisiz + log uyarısı.

Dönüş: son borç ayının 1. günü (dahil). None = cap yok.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Mapping

_LOG = logging.getLogger(__name__)
_ENV_HEPSI_SOZ = "KAPANIS_HEPSI_SOZLESME_YILI"
_ENV_N0_HARIC = "KAPANIS_N0_KAPANIS_AYI_HARIC"
_ENV_N_DAHIL = "KAPANIS_N_KAPANIS_AYI_DAHIL"
_warned_dahil_sans_n0 = False


def hepsi_sozlesme_yili_aktif() -> bool:
    """Yalnız tam '1' açık; başka her şey kapalı."""
    return str(os.environ.get(_ENV_HEPSI_SOZ) or "").strip() == "1"


def n0_kapanis_ayi_haric_aktif() -> bool:
    """Aday A: N=0 iken kapanış ayı borç dışı. Yalnız tam '1' açık."""
    return str(os.environ.get(_ENV_N0_HARIC) or "").strip() == "1"


def n_kapanis_ayi_dahil_aktif() -> bool:
    """Env ham: N=k → kap+(k−1). Yalnız tam '1' açık. N0 olmadan etkili değil."""
    return str(os.environ.get(_ENV_N_DAHIL) or "").strip() == "1"


def n_kapanis_ayi_dahil_efektif() -> bool:
    """Yeni semantik yalnız N0 da açıkken. Aksi halde etkisiz + tek sefer log."""
    global _warned_dahil_sans_n0
    if not n_kapanis_ayi_dahil_aktif():
        return False
    if not n0_kapanis_ayi_haric_aktif():
        if not _warned_dahil_sans_n0:
            _LOG.warning(
                "KAPANIS_N_KAPANIS_AYI_DAHIL=1 ama KAPANIS_N0_KAPANIS_AYI_HARIC kapalı "
                "— yeni bayrak etkisiz (N>=1 eski Aday B: kap+N)"
            )
            _warned_dahil_sans_n0 = True
        return False
    return True


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
    """Geçerli N (0..12) veya None (Hepsi / geçersiz). 0 korunur (falsy değil)."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = int(raw)
        if 0 <= val <= 12 and float(raw) == val:
            return val
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        val = int(s)
    except (TypeError, ValueError):
        try:
            f = float(s.replace(",", "."))
            val = int(f)
            if float(f) != float(val):
                return None
        except (TypeError, ValueError):
            return None
    if 0 <= val <= 12:
        return val
    return None


def _n_delta(n: int) -> int:
    """N → ay ofseti (kapanış ayı + delta = son borç ayı)."""
    if n0_kapanis_ayi_haric_aktif() and n == 0:
        return -1
    if n_kapanis_ayi_dahil_efektif() and n >= 1:
        return n - 1
    return n


def _giris_tarihi(musteri_row: Mapping[str, Any]) -> date | None:
    """Grid ile aynı öncelik: sozlesme_tarihi → rent_start_date."""
    return _coerce_date(musteri_row.get("sozlesme_tarihi")) or _coerce_date(
        musteri_row.get("rent_start_date")
    )


def _hepsi_sozlesme_yili_son_borc_ayi_eski(giris: date, kap: date) -> date:
    """Eski m-tabanlı formül (kanıt / karşılaştırma). m<0 → 0 zorlanır."""
    g = date(giris.year, giris.month, 1)
    k = date(kap.year, kap.month, 1)
    m = (k.year - g.year) * 12 + (k.month - g.month)
    if m < 0:
        m = 0
    return _add_months(g, 12 * (m // 12 + 1) - 1)


def hepsi_sozlesme_yili_son_borc_ayi(giris: date, kap: date) -> date:
    """Çıktığı ayı içeren sözleşme yılının son ayının 1'i.

    Anchor = giriş ayı (1..12). Yıl sonu ayı = anchor − 1 (Oca → Ara).
    Kapanış ayından (dahil) ileriye ilk karşılaşılan yıl-sonu ayı.
    Giriş yılı kullanılmaz → sozlesme_tarihi kapanıştan sonra (yenileme) olsa da doğru.
    m≥0 iken eski formülle birebir aynı; m<0 iken eski m=0 zorlardı (yanlış yıl).

    Örn. giriş Ağu → Tem; kap Eyl-2025 → Tem-2026; kap Tem-2025 → Tem-2025.
    """
    anchor = int(giris.month)
    if not 1 <= anchor <= 12:
        anchor = 1
    year_end_m = 12 if anchor == 1 else anchor - 1
    k = date(kap.year, kap.month, 1)
    if k.month == year_end_m:
        return k
    if k.month < year_end_m:
        delta = year_end_m - k.month
    else:
        delta = 12 - k.month + year_end_m
    return _add_months(k, delta)


def musteri_iptal_mi(v) -> bool:
    """customers.iptal / satır bayrağı — 0/false/None güvenli False."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        try:
            return int(v) == 1
        except (TypeError, ValueError):
            return False
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "on", "t")


def kira_son_borc_ayi(musteri_row: Mapping[str, Any] | None) -> date | None:
    """Pasif + kapanış → son borç ayı (dahil); cap yoksa None.

    iptal=TRUE: bayraklardan bağımsız giriş ayı − 1 (borç penceresi boş).
    N dolu: kapanış ayı + _n_delta(N).
    N NULL + bayrak kapalı: None.
    N NULL + bayrak açık + giriş var: sözleşme yılı sonu.
    N NULL + bayrak açık + giriş yok: None + log.
    """
    src = dict(musteri_row or {})
    durum = str(src.get("durum") or src.get("musteri_durum") or "").strip().lower()
    if durum != "pasif":
        return None
    # Sözleşme iptali: borç yok (giriş ayından önceki ay).
    if musteri_iptal_mi(src.get("iptal")):
        giris_i = _giris_tarihi(src)
        if not giris_i:
            return None
        return _add_months(date(giris_i.year, giris_i.month, 1), -1)
    kap = _coerce_date(src.get("kapanis_tarihi"))
    if not kap:
        return None
    n = _parse_n(src.get("kapanis_sonrasi_borc_ay"))
    if n is not None:
        return _add_months(date(kap.year, kap.month, 1), _n_delta(n))
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


def kapanis_ek_fingerprint(raw, iptal=None) -> str:
    """Cache/parmak izi: N dolu → rakam; Hepsi → 'H' (eski) veya 'H12' (bayrak açık).

    iptal=TRUE → 'IPTAL' (bayrak/N'den bağımsız; cache bayat sayılır).
    N=0 + KAPANIS_N0_KAPANIS_AYI_HARIC=1 → '0x' (eski '0'dan farklı → cache bayat).
    N=1..12 + N0 + KAPANIS_N_KAPANIS_AYI_DAHIL efektif → 'Nd' (eski 'N'den farklı).
    Bayrak(lar) kapalıyken N=0 → '0', N=1..12 → '1'..'12' (birebir eski).
    N=0 / Hepsi fingerprint yeni bayrakla DEĞİŞMEZ.
    """
    if musteri_iptal_mi(iptal):
        return "IPTAL"
    n = _parse_n(raw)
    if n is not None:
        if n == 0 and n0_kapanis_ayi_haric_aktif():
            return "0x"
        if n >= 1 and n_kapanis_ayi_dahil_efektif():
            return "%sd" % n
        return str(n)
    return "H12" if hepsi_sozlesme_yili_aktif() else "H"
