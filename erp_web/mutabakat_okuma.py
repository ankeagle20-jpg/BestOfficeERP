# -*- coding: utf-8 -*-
"""Fazlalık taşıma (waterfall) — yalnızca OKUMA katmanı.

Varsayılan KAPALI:
  MUTABAKAT_OKUMA_ENABLED=0
  MUTABAKAT_OKUMA_MUSTERI_IDS=   (boş = kimse)

G3 kuralı: yalnız kira>0 aylardaki aşırı yazım sonraki kira>0 aya taşınır.
Kira=0 / dondurma / grid yok aylara düşen tutar TAŞINMAZ → eşleşmemiş kova.
Korunum: Σ dağıtılan + Σ eşleşmemiş + avans = Σ map.

Yazma yollarına (tahsilatlar, panel save, makbuz, borçlandır) dokunulmaz.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any


TOL = 0.05


def mutabakat_okuma_enabled() -> bool:
    return str(os.environ.get("MUTABAKAT_OKUMA_ENABLED") or "").strip() in (
        "1",
        "true",
        "TRUE",
        "yes",
    )


def mutabakat_okuma_allowlist() -> set[int]:
    raw = str(os.environ.get("MUTABAKAT_OKUMA_MUSTERI_IDS") or "").strip()
    out: set[int] = set()
    if not raw:
        return out
    for p in raw.replace(";", ",").split(","):
        p = p.strip()
        if not p:
            continue
        try:
            out.add(int(p))
        except ValueError:
            continue
    return out


def mutabakat_okuma_aktif(musteri_id: int | None) -> bool:
    """Bayrak açık + mid allowlist'te. Allowlist boşken kimse etkilenmez."""
    if not mutabakat_okuma_enabled():
        return False
    try:
        mid = int(musteri_id or 0)
    except (TypeError, ValueError):
        return False
    if mid <= 0:
        return False
    al = mutabakat_okuma_allowlist()
    if not al:
        return False
    return mid in al


def waterfall_tasima(
    map_by_iso: dict | None,
    brut_by_iso: dict | None,
    *,
    skip_ym: set | None = None,
) -> dict[str, Any]:
    """Kronolojik tavan+taşıma (G3 daraltılmış).

    - kira>0 ve skip değil: tahsil = min(ham+carry, brut); fazlalık carry.
    - kira=0 / dondurma / skip: ham → eşleşmemiş (taşınmaz); carry bu aya girmez.
    - Ufuk sonu carry = avans (yalnız kira>0 aşımından).

    Korunum: Σ dağıtılan + Σ eşleşmemiş + avans == Σ map.
    """
    mp = {}
    for k, v in (map_by_iso or {}).items():
        iso = _norm_iso(k)
        if not iso:
            continue
        try:
            fv = round(float(v or 0), 2)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            mp[iso] = round(mp.get(iso, 0.0) + fv, 2)

    br = {}
    for k, v in (brut_by_iso or {}).items():
        iso = _norm_iso(k)
        if not iso:
            continue
        try:
            fv = round(float(v or 0), 2)
        except (TypeError, ValueError):
            continue
        br[iso] = fv

    skip = set()
    for s in skip_ym or ():
        s = str(s or "").strip()
        if len(s) >= 7 and s[4] == "-":
            skip.add(s[:7])

    isos = sorted(set(mp) | set(br))
    dagitim: dict[str, float] = {}
    eslesmemis_by_iso: dict[str, float] = {}
    carry = 0.0
    sum_map = round(sum(mp.values()), 2)

    for iso in isos:
        ym = iso[:7]
        brut = round(float(br.get(iso) or 0), 2)
        ham = round(float(mp.get(iso) or 0), 2)
        if ym in skip or brut <= TOL:
            # G3: kira=0 / dondurma — ham eşleşmemişte kalır, taşınmaz
            if ham > TOL:
                eslesmemis_by_iso[iso] = round(
                    eslesmemis_by_iso.get(iso, 0.0) + ham, 2
                )
            # carry olduğu gibi devam (önceki kira>0 aşımı)
            continue
        available = round(carry + ham, 2)
        take = round(min(available, brut), 2)
        if take > TOL:
            dagitim[iso] = take
        carry = round(max(available - take, 0), 2)

    avans = round(carry, 2)
    sum_dag = round(sum(dagitim.values()), 2)
    sum_es = round(sum(eslesmemis_by_iso.values()), 2)
    return {
        "dagitim": dagitim,
        "eslesmemis": eslesmemis_by_iso,
        "eslesmemis_toplam": sum_es,
        "avans": avans,
        "sum_map": sum_map,
        "sum_dagitim": sum_dag,
        "korunum_ok": abs(sum_dag + sum_es + avans - sum_map) <= max(TOL, 0.02),
    }


def waterfall_tasima_g2_legacy(
    map_by_iso: dict | None,
    brut_by_iso: dict | None,
    *,
    skip_ym: set | None = None,
) -> dict[str, Any]:
    """G2 davranışı (kira=0 ham'ı carry'e sokar) — yalnızca ayrıştırma/ölçüm."""
    mp = {}
    for k, v in (map_by_iso or {}).items():
        iso = _norm_iso(k)
        if not iso:
            continue
        try:
            fv = round(float(v or 0), 2)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            mp[iso] = round(mp.get(iso, 0.0) + fv, 2)
    br = {}
    for k, v in (brut_by_iso or {}).items():
        iso = _norm_iso(k)
        if not iso:
            continue
        try:
            fv = round(float(v or 0), 2)
        except (TypeError, ValueError):
            continue
        br[iso] = fv
    skip = set()
    for s in skip_ym or ():
        s = str(s or "").strip()
        if len(s) >= 7 and s[4] == "-":
            skip.add(s[:7])
    isos = sorted(set(mp) | set(br))
    dagitim: dict[str, float] = {}
    carry = 0.0
    sum_map = round(sum(mp.values()), 2)
    for iso in isos:
        ym = iso[:7]
        brut = round(float(br.get(iso) or 0), 2)
        ham = round(float(mp.get(iso) or 0), 2)
        available = round(carry + ham, 2)
        if ym in skip or brut <= TOL:
            carry = available
            continue
        take = round(min(available, brut), 2)
        if take > TOL:
            dagitim[iso] = take
        carry = round(max(available - take, 0), 2)
    avans = round(carry, 2)
    sum_dag = round(sum(dagitim.values()), 2)
    return {
        "dagitim": dagitim,
        "avans": avans,
        "sum_map": sum_map,
        "sum_dagitim": sum_dag,
        "korunum_ok": abs(sum_dag + avans - sum_map) <= max(TOL, 0.02),
    }


def okuma_tahsil_map(
    musteri_id: int | None,
    map_by_iso: dict | None,
    brut_by_iso: dict | None,
    *,
    skip_ym: set | None = None,
) -> dict[str, float]:
    """Bayrak kapalı veya allowlist dışı → ham map kopyası. Açıksa waterfall dağıtım."""
    ham = {}
    for k, v in (map_by_iso or {}).items():
        iso = _norm_iso(k)
        if not iso:
            continue
        try:
            fv = round(float(v or 0), 2)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            ham[iso] = round(ham.get(iso, 0.0) + fv, 2)
    if not mutabakat_okuma_aktif(musteri_id):
        return ham
    r = waterfall_tasima(ham, brut_by_iso, skip_ym=skip_ym)
    return dict(r.get("dagitim") or {})


def okuma_waterfall_full(
    musteri_id: int | None,
    map_by_iso: dict | None,
    brut_by_iso: dict | None,
    *,
    skip_ym: set | None = None,
) -> dict[str, Any]:
    """Bayrak açıksa tam waterfall sonucu (eşleşmemiş dahil); kapalıysa ham + boş kova."""
    ham = {}
    for k, v in (map_by_iso or {}).items():
        iso = _norm_iso(k)
        if not iso:
            continue
        try:
            fv = round(float(v or 0), 2)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            ham[iso] = round(ham.get(iso, 0.0) + fv, 2)
    if not mutabakat_okuma_aktif(musteri_id):
        return {
            "dagitim": dict(ham),
            "eslesmemis": {},
            "eslesmemis_toplam": 0.0,
            "avans": 0.0,
            "sum_map": round(sum(ham.values()), 2),
            "sum_dagitim": round(sum(ham.values()), 2),
            "korunum_ok": True,
            "aktif": False,
        }
    r = waterfall_tasima(ham, brut_by_iso, skip_ym=skip_ym)
    r["aktif"] = True
    return r


ETIKET_ONCEKI_SOZLESME = "önceki sözleşme dönemi"
ETIKET_ESLESMEMIS = "eşleşmemiş"


def _parse_sozlesme_bas(sozlesme_baslangic) -> date | None:
    if sozlesme_baslangic is None:
        return None
    if isinstance(sozlesme_baslangic, datetime):
        return sozlesme_baslangic.date()
    if isinstance(sozlesme_baslangic, date):
        return sozlesme_baslangic
    s = str(sozlesme_baslangic).strip()[:10]
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
        except ValueError:
            return None
    if len(s) >= 7 and s[4] == "-":
        try:
            return date(int(s[0:4]), int(s[5:7]), 1)
        except ValueError:
            return None
    return None


def etiket_eslesmemis_ay(iso: str, sozlesme_baslangic) -> str:
    """Ay iso sözleşme başlangıcından önceyse önceki dönem; aksi halde eşleşmemiş."""
    iso_n = _norm_iso(iso)
    if not iso_n:
        return ETIKET_ESLESMEMIS
    bas = _parse_sozlesme_bas(sozlesme_baslangic)
    if bas is None:
        return ETIKET_ESLESMEMIS
    try:
        ay_d = date(int(iso_n[0:4]), int(iso_n[5:7]), 1)
    except ValueError:
        return ETIKET_ESLESMEMIS
    if ay_d < bas:
        return ETIKET_ONCEKI_SOZLESME
    return ETIKET_ESLESMEMIS


def sinifla_eslesmemis_rapor(
    eslesmemis_by_iso: dict | None,
    sozlesme_baslangic=None,
    *,
    mid: int | None = None,
) -> list[dict[str, Any]]:
    """Eşleşmemiş kovayı etiketlere böl (hesap değişmez; yalnız gösterim).

    Dönüş: [{etiket, mid?, toplam_tl, ay_aralik, ay_adet, by_iso}, ...]
    Boş girdi → [].
    """
    by_etiket: dict[str, dict[str, float]] = {}
    for k, v in (eslesmemis_by_iso or {}).items():
        iso = _norm_iso(k)
        if not iso:
            continue
        try:
            fv = round(float(v or 0), 2)
        except (TypeError, ValueError):
            continue
        if fv <= TOL:
            continue
        et = etiket_eslesmemis_ay(iso, sozlesme_baslangic)
        bucket = by_etiket.setdefault(et, {})
        bucket[iso] = round(bucket.get(iso, 0.0) + fv, 2)

    # Önceki dönem önce, sonra eşleşmemiş
    order = (ETIKET_ONCEKI_SOZLESME, ETIKET_ESLESMEMIS)
    out: list[dict[str, Any]] = []
    for et in order:
        bucket = by_etiket.get(et) or {}
        if not bucket:
            continue
        aylar = sorted(bucket.keys())
        row = {
            "etiket": et,
            "toplam_tl": round(sum(bucket.values()), 2),
            "ay_aralik": "%s..%s" % (aylar[0][:7], aylar[-1][:7]),
            "ay_adet": len(aylar),
            "by_iso": dict(bucket),
        }
        if mid is not None:
            try:
                row["mid"] = int(mid)
            except (TypeError, ValueError):
                pass
        out.append(row)
    # beklenmeyen etiket varsa ekle
    for et, bucket in by_etiket.items():
        if et in order:
            continue
        aylar = sorted(bucket.keys())
        row = {
            "etiket": et,
            "toplam_tl": round(sum(bucket.values()), 2),
            "ay_aralik": "%s..%s" % (aylar[0][:7], aylar[-1][:7]) if aylar else "-",
            "ay_adet": len(aylar),
            "by_iso": dict(bucket),
        }
        if mid is not None:
            try:
                row["mid"] = int(mid)
            except (TypeError, ValueError):
                pass
        out.append(row)
    return out


def brut_by_iso_from_payload(payload: dict | None) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(payload, dict):
        return out
    for a in payload.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        try:
            iso = date(int(a["yil"]), int(a["ay"]), 1).isoformat()
            bv = round(float(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil") or 0), 2)
        except (TypeError, ValueError, KeyError):
            continue
        out[iso] = bv
    return out


def apply_waterfall_to_grid_payload_copy(
    payload: dict | None,
    dagitim: dict | None,
    *,
    tol: float = TOL,
) -> dict | None:
    """Bellekte odenen/tahsil_edildi güncelle — yazmaz."""
    if not isinstance(payload, dict):
        return payload
    import copy

    pl = copy.deepcopy(payload)
    dag = dagitim or {}
    for a in pl.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        try:
            iso = date(int(a["yil"]), int(a["ay"]), 1).isoformat()
            brut = round(float(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil") or 0), 2)
        except (TypeError, ValueError, KeyError):
            continue
        od = round(float(dag.get(iso) or 0), 2)
        if brut <= tol:
            continue
        kalan = round(max(brut - od, 0), 2)
        if od >= brut - tol:
            kalan = 0.0
            od = brut
        a["odenen_tutar_kdv"] = od
        a["kalan_tutar_kdv"] = kalan
        a["tahsil_edildi"] = kalan <= tol and od > tol
        a["kismi_tahsilat"] = od > tol and kalan > tol
        if kalan <= tol and od > tol:
            a["acik_aylik_borc_faturasi"] = False
        elif od <= tol and kalan > tol:
            a["acik_aylik_borc_faturasi"] = True
    pl["_mutabakat_okuma_waterfall"] = True
    return pl


def _norm_iso(k) -> str | None:
    s = str(k or "").strip()[:10]
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return date(int(s[0:4]), int(s[5:7]), 1).isoformat()
        except ValueError:
            return None
    if len(s) >= 7 and s[4] == "-":
        try:
            return date(int(s[0:4]), int(s[5:7]), 1).isoformat()
        except ValueError:
            return None
    return None
