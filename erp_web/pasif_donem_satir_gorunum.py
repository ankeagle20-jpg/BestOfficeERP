# -*- coding: utf-8 -*-
"""Pasif/iptal: kapanis sonrasi bos donem/yil satiri gorunurluk kurali.

Yalniz gorunum. Borc hesabini / KAPANIS_HEPSI bayragini degistirmez.
Hepsi (N=NULL, bayrak kapali) icin de hepsi_sozlesme_yili_son_borc_ayi kullanilir.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from kira_kapanis_cap import (
    _add_months,
    _coerce_date,
    _giris_tarihi,
    _parse_n,
    hepsi_sozlesme_yili_son_borc_ayi,
    kira_son_borc_ayi,
    musteri_iptal_mi,
)

_TOL = 0.05


def artis_ayi(kyc: Mapping[str, Any] | None, giris: date | None = None) -> int:
    src = dict(kyc or {})
    ka = _coerce_date(src.get("kira_artis_tarihi"))
    if ka:
        return int(ka.month)
    g = giris or _giris_tarihi(src)
    if g:
        return int(g.month)
    return 1


def donem_baslangic(donem_yil: int, artis_ay: int) -> date:
    return date(int(donem_yil), int(artis_ay), 1)


def gorunum_son_borc_ayi(kyc: Mapping[str, Any] | None) -> date | None:
    """Pasif/iptal gorunum son borc ayi (dahil). Aktifte None.

    N dolu: kira_son_borc_ayi (canli N0 bayragi dahil).
    Hepsi (N NULL): bayrak kapali olsa da hepsi_sozlesme_yili_son_borc_ayi.
    """
    src = dict(kyc or {})
    durum = str(src.get("durum") or src.get("musteri_durum") or "").strip().lower()
    iptal = musteri_iptal_mi(src.get("iptal"))
    if iptal:
        return kira_son_borc_ayi(src)
    if durum != "pasif":
        return None
    son = kira_son_borc_ayi(src)
    if son is not None:
        return son
    kap = _coerce_date(src.get("kapanis_tarihi"))
    giris = _giris_tarihi(src)
    if kap and giris and _parse_n(src.get("kapanis_sonrasi_borc_ay")) is None:
        return hepsi_sozlesme_yili_son_borc_ayi(giris, kap)
    return None


def _f(v: Any) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def donem_yilinda_veri_var(
    donem_yil: int,
    artis_ay: int,
    *,
    panel_by_iso: Mapping[str, Any] | None = None,
    grid_payload: Mapping[str, Any] | None = None,
    reel_map: Mapping[Any, Any] | None = None,
    marker_by_iso: Mapping[str, Any] | None = None,
    pay_by_iso: Mapping[str, Any] | None = None,
    fatura_ym: Iterable[str] | None = None,
) -> bool:
    """Donem yilinda tahsil/odeme/fatura/marker/nakit-banka var mi.

    reel_map parametresi uyumluluk icin kalir; yalniz reel kaydi VERİ sayilmaz
    (pasif/iptal kapanis sonrasi donem satirini gostermeye yetmez).
    """
    bas = donem_baslangic(donem_yil, artis_ay)
    months = [_add_months(bas, i) for i in range(12)]
    _ = reel_map  # bilinçli: reel-only istisna kaldirildi
    fat_set = {str(x)[:7] for x in (fatura_ym or []) if x}
    for m in months:
        iso = m.isoformat()
        ym = iso[:7]
        if ym in fat_set or iso in fat_set:
            return True
        c = (panel_by_iso or {}).get(iso)
        if isinstance(c, dict) and _f(c.get("tahsil")) > _TOL:
            return True
        if _f((marker_by_iso or {}).get(iso)) > _TOL or _f((pay_by_iso or {}).get(iso)) > _TOL:
            return True
    for a in (grid_payload or {}).get("aylar") or []:
        try:
            cur = date(int(a["yil"]), int(a["ay"]), 1)
        except (TypeError, ValueError, KeyError):
            continue
        if cur < bas or cur > _add_months(bas, 11):
            continue
        if _f(a.get("odenen_tutar_kdv")) > _TOL or bool(a.get("tahsil_edildi")):
            return True
        if _f(a.get("nakit")) > _TOL or _f(a.get("banka")) > _TOL:
            return True
        if bool(a.get("acik_aylik_borc_faturasi")):
            return True
    return False


def donem_yil_satiri_gosterilsin(
    donem_yil: int,
    kyc: Mapping[str, Any] | None,
    *,
    panel_by_iso: Mapping[str, Any] | None = None,
    grid_payload: Mapping[str, Any] | None = None,
    reel_map: Mapping[Any, Any] | None = None,
    marker_by_iso: Mapping[str, Any] | None = None,
    pay_by_iso: Mapping[str, Any] | None = None,
    fatura_ym: Iterable[str] | None = None,
) -> bool:
    src = dict(kyc or {})
    durum = str(src.get("durum") or src.get("musteri_durum") or "").strip().lower()
    iptal = musteri_iptal_mi(src.get("iptal"))
    if (not iptal) and durum != "pasif":
        return True
    son = gorunum_son_borc_ayi(src)
    if son is None:
        return True
    giris = _giris_tarihi(src)
    artis = artis_ayi(src, giris)
    bas = donem_baslangic(int(donem_yil), artis)
    if bas <= son:
        return True
    return donem_yilinda_veri_var(
        int(donem_yil),
        artis,
        panel_by_iso=panel_by_iso,
        grid_payload=grid_payload,
        reel_map=reel_map,
        marker_by_iso=marker_by_iso,
        pay_by_iso=pay_by_iso,
        fatura_ym=fatura_ym,
    )


def filtre_donem_yillari(
    years: Sequence[int],
    kyc: Mapping[str, Any] | None,
    **data_kw: Any,
) -> list[int]:
    return [int(y) for y in years if donem_yil_satiri_gosterilsin(int(y), kyc, **data_kw)]
