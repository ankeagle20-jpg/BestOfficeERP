"""Tahsilat ay dağıtımı: ekstre borç satırı ve tarih sırası.

Aylık grid önbelleği ve ekrandaki yıl filtresi bu hesaba girmez.
İleri tarihli tahsilat, daha eski bir ödemenin açık ayını düşürmez.
"""
from __future__ import annotations

from datetime import date, datetime

TOL = 0.01


def ay_basi(raw) -> str | None:
    s = str(raw or "").strip()[:10]
    try:
        dd = datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None
    return date(dd.year, dd.month, 1).isoformat()


def borc_haritasi(satirlar) -> dict[str, float]:
    """Ay başı → borç tutarı. Aynı ayın satırları toplanır."""
    out: dict[str, float] = {}
    if isinstance(satirlar, dict):
        items = satirlar.items()
    else:
        items = []
        for it in satirlar or []:
            if isinstance(it, dict):
                items.append((it.get("iso") or it.get("tarih"), it.get("tutar") or it.get("borc")))
            elif isinstance(it, (list, tuple)) and len(it) >= 2:
                items.append((it[0], it[1]))
    for raw_iso, raw_tutar in items:
        iso = ay_basi(raw_iso)
        if not iso:
            continue
        try:
            tutar = round(float(raw_tutar or 0), 2)
        except (TypeError, ValueError):
            continue
        if tutar <= 0:
            continue
        out[iso] = round(out.get(iso, 0.0) + tutar, 2)
    return out


def _onceki_tutarlar(onceki, odeme_tarihi: str) -> list[float]:
    """Tarihi ödemeden sonra olanlar düşer. Kalanlar tarih ve sıra ile uygulanır."""
    prepared = []
    for i, item in enumerate(onceki or []):
        if isinstance(item, (int, float)):
            try:
                tutar = round(float(item), 2)
            except (TypeError, ValueError):
                continue
            prepared.append((odeme_tarihi, i, tutar))
            continue
        if not isinstance(item, dict):
            continue
        tarih = str(item.get("tarih") or odeme_tarihi)[:10]
        if len(tarih) != 10:
            tarih = odeme_tarihi
        if tarih > odeme_tarihi:
            continue
        try:
            sira = int(item.get("id"))
        except (TypeError, ValueError):
            sira = i
        try:
            tutar = round(float(item.get("tutar") or 0), 2)
        except (TypeError, ValueError):
            continue
        if tutar <= 0:
            continue
        prepared.append((tarih, sira, tutar))
    prepared.sort(key=lambda row: (row[0], row[1]))
    return [tutar for _tarih, _sira, tutar in prepared]


def _uygula(need: dict[str, float], tutar: float, odeme_tarihi: str, allowlist) -> list[tuple[str, float]]:
    try:
        rem = round(float(tutar or 0), 2)
    except (TypeError, ValueError):
        return []
    if rem <= TOL:
        return []
    months = sorted(need)
    if allowlist is not None:
        phases = [[iso for iso in months if iso in allowlist]]
    else:
        phases = [
            [iso for iso in months if iso <= odeme_tarihi],
            [iso for iso in months if iso > odeme_tarihi],
        ]
    out: list[tuple[str, float]] = []
    for phase in phases:
        for iso in phase:
            if rem <= TOL:
                break
            acik = round(float(need.get(iso) or 0), 2)
            if acik <= TOL:
                continue
            pay = round(min(acik, rem), 2)
            if pay <= TOL:
                continue
            need[iso] = round(acik - pay, 2)
            rem = round(rem - pay, 2)
            out.append((iso, pay))
        if rem <= TOL:
            break
    return out


def acik_ay_dagit(
    borc,
    tutar: float,
    odeme_tarihi: str,
    onceki=None,
    allowlist=None,
) -> list[tuple[str, float]]:
    """Açık tutar = ay borcu − daha önceki tahsilatların kapattığı tutar.

    Öncekiler tutar olarak, ödeme tarihine kadar ve tarih sırasında uygulanır.
    İşaret metni okunmaz. Vadesi gelmiş aylar bitmeden sonraki aya yazılmaz.
    allowlist verilirse yalnız o aylar, tarih sırasında doldurulur.
    """
    iso_odeme = ay_basi(odeme_tarihi) and str(odeme_tarihi)[:10]
    if not iso_odeme or len(str(odeme_tarihi).strip()) < 10:
        return []
    odeme = str(odeme_tarihi).strip()[:10]
    need = borc_haritasi(borc)
    allow = None
    if allowlist is not None:
        allow = set()
        for raw in allowlist:
            iso = ay_basi(raw)
            if iso:
                allow.add(iso)
    for eski in _onceki_tutarlar(onceki, odeme):
        _uygula(need, eski, odeme, None)
    return _uygula(need, tutar, odeme, allow)


def dagit_seri(borc, tahsilatlar) -> list[list[tuple[str, float]]]:
    """Tarih sırasındaki her tahsilat için dağılım. İleri tarih, öncekini etkilemez."""
    need = borc_haritasi(borc)
    sirali = []
    for i, item in enumerate(tahsilatlar or []):
        if not isinstance(item, dict):
            continue
        tarih = str(item.get("tarih") or "")[:10]
        if len(tarih) != 10:
            continue
        try:
            tutar = round(float(item.get("tutar") or 0), 2)
        except (TypeError, ValueError):
            continue
        if tutar <= 0:
            continue
        try:
            sira = int(item.get("id"))
        except (TypeError, ValueError):
            sira = i
        sirali.append((tarih, sira, i, tutar))
    sirali.sort(key=lambda row: (row[0], row[1], row[2]))
    sonuc = [[] for _ in (tahsilatlar or [])]
    for tarih, _sira, idx, tutar in sirali:
        sonuc[idx] = _uygula(need, tutar, tarih, None)
    return sonuc
