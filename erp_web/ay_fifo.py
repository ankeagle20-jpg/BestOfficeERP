"""Tahsilat ay dağıtımı.

Borç haritasını çağıran verir: ekrandaki aylık tutar.
Yıl filtresi girdi değildir.
İşaretli önceki tahsilat yalnız işaretli ayı kapatır; fazlası sonraki aya taşmaz.
İşaretsiz önceki tutar, ödeme tarihine kadar tarih sırasında uygulanır.
İleri tarihli tahsilat, daha eski bir ödemenin açık ayını düşürmez.
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime

TOL = 0.01
_PAY_RE = re.compile(r"\|AYLIK_PAY\|([0-9]{4}-[0-9]{2}-[0-9]{2})=([0-9]+(?:\.[0-9]+)?)")
_TAH_RE = re.compile(r"\|AYLIK_TAH\|([0-9]{4}-[0-9]{2}-[0-9]{2})\|")


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


def _pozitif_harita(src) -> dict[str, float]:
    out: dict[str, float] = {}
    if not src:
        return out
    items = src.items() if isinstance(src, dict) else src
    for raw_iso, raw_tutar in items:
        iso = ay_basi(raw_iso)
        if not iso:
            continue
        try:
            tutar = round(float(raw_tutar or 0), 2)
        except (TypeError, ValueError):
            continue
        if tutar <= TOL:
            continue
        out[iso] = tutar
    return out


def grid_brut_haritasi(payload) -> dict[str, float]:
    """Aylık grid hücresi: brüt, yoksa KDV dahil tutar."""
    if isinstance(payload, str):
        import json
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return {}
    aylar = payload if isinstance(payload, list) else (payload or {}).get("aylar") if isinstance(payload, dict) else []
    out: dict[str, float] = {}
    if not isinstance(aylar, list):
        return out
    for a in aylar:
        if not isinstance(a, dict):
            continue
        try:
            iso = ay_basi(f"{int(a.get('yil')):04d}-{int(a.get('ay')):02d}-01")
        except (TypeError, ValueError):
            continue
        if not iso:
            continue
        ham = a.get("brut_tutar_kdv")
        if ham is None:
            ham = a.get("tutar_kdv_dahil")
        try:
            tutar = round(float(ham or 0), 2)
        except (TypeError, ValueError):
            continue
        if tutar > TOL:
            out[iso] = tutar
    return out


def _ay_ekle(d: date, n: int) -> date:
    m0 = d.month - 1 + n
    y = d.year + m0 // 12
    m = m0 % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def reel_ay_haritasi(donemler, artis) -> dict[str, float]:
    """Yıllık reel tutarı, kira artış yıldönümünden başlayan 12 aya yayar."""
    if isinstance(artis, datetime):
        artis_gun = artis.date()
    elif isinstance(artis, date):
        artis_gun = artis
    else:
        try:
            artis_gun = datetime.strptime(str(artis or "")[:10], "%Y-%m-%d").date()
        except ValueError:
            return {}
    items = list(donemler.items()) if isinstance(donemler, dict) else list(donemler or [])
    sirali = []
    for raw_yil, raw_tutar in items:
        try:
            yil = int(raw_yil)
            tutar = round(float(raw_tutar or 0), 2)
        except (TypeError, ValueError):
            continue
        if tutar <= TOL or yil < 1990 or yil > 2100:
            continue
        sirali.append((yil, tutar))
    sirali.sort()
    out: dict[str, float] = {}
    for yil, tutar in sirali:
        bas = date(yil, artis_gun.month, min(artis_gun.day, monthrange(yil, artis_gun.month)[1]))
        for i in range(12):
            d = _ay_ekle(bas, i)
            out[date(d.year, d.month, 1).isoformat()] = tutar
    return out


def gorunen_borc_biles(
    grid=None, reel=None, fatura=None, sozlesme_basi=None
) -> tuple[dict[str, float], list[str]]:
    """Ekrandaki ay borcu. Hücre varsa o; yoksa reel; o da yoksa fatura yedeği.

    sozlesme_basi verilirse fatura yedeği yalnız o ay ve sonrası. Tarih yoksa
    fatura yedeği eskisi gibi her aya uygulanır.
    Dönüş: (borç haritası, fatura yedeğine düşen aylar).
    """
    gg = _pozitif_harita(grid)
    rr = _pozitif_harita(reel)
    ff = _pozitif_harita(fatura)
    taban = ay_basi(sozlesme_basi) if sozlesme_basi else None
    borc: dict[str, float] = {}
    yedek: list[str] = []
    for iso in sorted(set(gg) | set(rr) | set(ff)):
        if iso in gg:
            borc[iso] = gg[iso]
        elif iso in rr:
            borc[iso] = rr[iso]
        elif taban and iso < taban:
            continue
        else:
            borc[iso] = ff[iso]
            yedek.append(iso)
    return borc, yedek


def _uygula(
    need: dict[str, float],
    tutar: float,
    odeme_tarihi: str,
    allowlist,
    taban=None,
) -> list[tuple[str, float]]:
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
        if taban:
            months = [iso for iso in months if iso >= taban]
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


def _pay_listesi(raw) -> list[tuple[str, float]]:
    out = []
    for it in raw or []:
        if isinstance(it, dict):
            iso, amt = it.get("iso") or it.get("tarih"), it.get("tutar") if it.get("tutar") is not None else it.get("pay")
        elif isinstance(it, (list, tuple)) and len(it) >= 2:
            iso, amt = it[0], it[1]
        else:
            continue
        ay = ay_basi(iso)
        try:
            tutar = round(float(amt or 0), 2)
        except (TypeError, ValueError):
            continue
        if ay and tutar > TOL:
            out.append((ay, tutar))
    return out


def _isaret(item: dict):
    """İşaret varsa ('pay', [(ay, tutar)]) veya ('tah', [ay]). Yoksa None."""
    if "pay" in item and item.get("pay") is not None:
        return ("pay", _pay_listesi(item.get("pay")))
    ac = str(item.get("aciklama") or "")
    pays = []
    for iso, amt in _PAY_RE.findall(ac):
        ay = ay_basi(iso)
        try:
            tutar = round(float(amt), 2)
        except (TypeError, ValueError):
            continue
        if ay and tutar > TOL:
            pays.append((ay, tutar))
    if pays or "|AYLIK_PAY|" in ac:
        return ("pay", pays)
    tah = []
    for iso in _TAH_RE.findall(ac):
        ay = ay_basi(iso)
        if ay and ay not in tah:
            tah.append(ay)
    if tah:
        return ("tah", tah)
    return None


def _kapat_ay(need: dict[str, float], parcalar) -> list[tuple[str, float]]:
    """Tutarı yalnız yazılı aya uygular. Ayın açığını aşan kısım düşer."""
    out: list[tuple[str, float]] = []
    for iso, amt in parcalar or []:
        ay = ay_basi(iso)
        if not ay or ay not in need:
            continue
        try:
            istenen = round(float(amt or 0), 2)
        except (TypeError, ValueError):
            continue
        acik = round(float(need.get(ay) or 0), 2)
        pay = round(min(acik, istenen), 2)
        if pay <= TOL:
            continue
        need[ay] = round(acik - pay, 2)
        out.append((ay, pay))
    return out


def _onceki_kayitlar(onceki, odeme_tarihi: str) -> list[dict]:
    prepared = []
    for i, item in enumerate(onceki or []):
        if isinstance(item, (int, float)):
            try:
                tutar = round(float(item), 2)
            except (TypeError, ValueError):
                continue
            if tutar <= TOL:
                continue
            prepared.append({"tarih": odeme_tarihi, "sira": i, "tutar": tutar})
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
        if tutar <= TOL:
            continue
        kayit = {"tarih": tarih, "sira": sira, "tutar": tutar, "aciklama": item.get("aciklama") or ""}
        if "pay" in item:
            kayit["pay"] = item.get("pay")
        prepared.append(kayit)
    prepared.sort(key=lambda row: (row["tarih"], row["sira"]))
    return prepared


def acik_ay_dagit(
    borc,
    tutar: float,
    odeme_tarihi: str,
    onceki=None,
    allowlist=None,
    sozlesme_basi=None,
) -> list[tuple[str, float]]:
    """Açık tutar = ay borcu − daha önce o aya işaretlenmiş tahsilat.

    İşaretli önceki tahsilat yalnız kendi ayını kapatır; fazlası sonraki aya taşmaz.
    İşaretsiz önceki tutar tarih sırasında açık aylara uygulanır.
    Vadesi gelmiş aylar bitmeden sonraki aya yazılmaz.
    allowlist verilirse yalnız o aylar, açık tutar sırasında doldurulur.
    sozlesme_basi yalnız yeni işaretsiz tutarı sınırlar; önceki işaretler ve
    allowlist aynı kalır. Tarih yoksa sınır yoktur.
    """
    iso_odeme = ay_basi(odeme_tarihi) and str(odeme_tarihi)[:10]
    if not iso_odeme or len(str(odeme_tarihi).strip()) < 10:
        return []
    odeme = str(odeme_tarihi).strip()[:10]
    taban = ay_basi(sozlesme_basi) if sozlesme_basi else None
    need = borc_haritasi(borc)
    allow = None
    if allowlist is not None:
        allow = set()
        for raw in allowlist:
            iso = ay_basi(raw)
            if iso:
                allow.add(iso)
    for eski in _onceki_kayitlar(onceki, odeme):
        isaret = _isaret(eski)
        if isaret is None:
            _uygula(need, eski["tutar"], odeme, None)
        elif isaret[0] == "pay":
            _kapat_ay(need, isaret[1])
        elif len(isaret[1]) == 1:
            _kapat_ay(need, [(isaret[1][0], eski["tutar"])])
        else:
            _uygula(need, eski["tutar"], odeme, set(isaret[1]))
    if allow is not None:
        return _uygula(need, tutar, odeme, allow)
    return _uygula(need, tutar, odeme, None, taban=taban)


def dagit_seri(borc, tahsilatlar) -> list[list[tuple[str, float]]]:
    """Tarih sırasındaki her tahsilat için dağılım. İleri tarih, öncekini etkilemez.

    |AYLIK_PAY| olmayan |AYLIK_TAH| satırı yalnız işaretli ayı kapatır.
    Aynı tutar FIFO'ya ikinci kez girmez. |AYLIK_PAY| satırı yeniden hesap için FIFO'da kalır.
    """
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
        sirali.append((tarih, sira, i, tutar, item))
    sirali.sort(key=lambda row: (row[0], row[1], row[2]))
    sonuc = [[] for _ in (tahsilatlar or [])]
    for tarih, _sira, idx, tutar, item in sirali:
        isaret = _isaret(item)
        if isaret and isaret[0] == "tah":
            if len(isaret[1]) == 1:
                sonuc[idx] = _kapat_ay(need, [(isaret[1][0], tutar)])
            else:
                sonuc[idx] = _uygula(need, tutar, tarih, set(isaret[1]))
            continue
        sonuc[idx] = _uygula(need, tutar, tarih, None)
    return sonuc
