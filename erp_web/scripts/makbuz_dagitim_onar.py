# -*- coding: utf-8 -*-
"""manuel_makbuz |AYLIK_PAY| onarımı — kronolojik tam FIFO (allowlist yok).

Varsayılan: DRY-RUN (yazmaz).
--apply yalnız ALLOW_PROD_WRITE tanımlıysa ve --mid verilmişse yazar.
Kapsam dışı: grid_toplu, eşitle/GRID_TAH_PATTERN, banka_import, odeme_linki.
PII yazılmaz (yalnız mid, makbuz id/no, tarih, tutar, ay).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_PAY_RE = re.compile(
    r"\|AYLIK_PAY\|([0-9]{4}-[0-9]{2}-[0-9]{2})=([0-9]+(?:\.[0-9]+)?)"
)
_TAH_RE = re.compile(r"\|AYLIK_TAH\|([0-9]{4}-[0-9]{2}-[0-9]{2})\|")
_KAPSAM_DISI_KAYNAK = frozenset({"grid_toplu", "banka_import", "odeme_linki"})


def _prod_write_ok() -> bool:
    return (os.environ.get("ALLOW_PROD_WRITE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _pay_list(aciklama: str | None) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for m in _PAY_RE.finditer(str(aciklama or "")):
        try:
            out.append((m.group(1), round(float(m.group(2)), 2)))
        except (TypeError, ValueError):
            continue
    return out


def _pay_sum(pays) -> float:
    return round(sum(float(p) for _, p in (pays or [])), 2)


def _j(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, default=str))


def _is_esitle_grid(kaynak: str, aciklama: str, tahsil_eden) -> bool:
    kay = str(kaynak or "").strip().lower()
    if kay in _KAPSAM_DISI_KAYNAK or kay == "manuel_makbuz":
        return False
    te_bos = tahsil_eden is None or str(tahsil_eden).strip() == ""
    if not te_bos:
        return False
    try:
        from routes.giris_routes import GRID_TAH_PATTERN
    except Exception:
        return False
    return bool(GRID_TAH_PATTERN.match(str(aciklama or "").strip()))


def _classify(row: dict) -> str:
    kay = str(row.get("kaynak") or "").strip().lower()
    if kay == "manuel_makbuz":
        return "manuel_makbuz"
    if kay in _KAPSAM_DISI_KAYNAK:
        return kay or "kapsam_disi"
    if _is_esitle_grid(kay, row.get("aciklama") or "", row.get("tahsil_eden")):
        return "esitle_grid_pattern"
    return kay or "kapsam_disi"


def load_tahsilatlar(mid: int, fetch_all) -> list[dict]:
    rows = fetch_all(
        """
        SELECT id,
               COALESCE(musteri_id, customer_id) AS mid,
               makbuz_no,
               ROUND(COALESCE(tutar, 0)::numeric, 2) AS tutar,
               LEFT(tahsilat_tarihi::text, 10) AS tarih,
               COALESCE(aciklama, '') AS aciklama,
               COALESCE(kaynak, '') AS kaynak,
               odeme_turu,
               tahsil_eden
        FROM tahsilatlar
        WHERE COALESCE(musteri_id, customer_id) = %s
          AND COALESCE(tutar, 0) > 0
        ORDER BY LEFT(tahsilat_tarihi::text, 10) NULLS LAST, id
        """,
        (int(mid),),
    ) or []
    out = []
    for r in rows:
        item = dict(r)
        item["id"] = int(item["id"])
        item["mid"] = int(item["mid"])
        item["tutar"] = round(float(item.get("tutar") or 0), 2)
        item["tarih"] = str(item.get("tarih") or "")[:10]
        item["cls"] = _classify(item)
        out.append(item)
    return out


def _has_marker(aciklama: str | None) -> bool:
    ac = str(aciklama or "")
    return bool(_PAY_RE.search(ac) or _TAH_RE.search(ac))


def _pay_map(pays) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for iso, p in pays or []:
        out[str(iso)[:10]] = round(out[str(iso)[:10]] + float(p), 2)
    return dict(out)


def _durum(tahsil: float, kalan: float, brut: float) -> str:
    if tahsil <= 0.01:
        return "borclu" if brut > 0.01 else "bos"
    if kalan <= 0.05:
        return "odendi"
    return "kismi"


def _kurus_gurultusu_mu(plans: list[dict]) -> bool:
    """Yalnız ≤1,00 TL ay farkı varsa True (126 tipi). Değişen yoksa False."""
    any_chg = False
    for p in plans or []:
        if not p.get("degisti"):
            continue
        any_chg = True
        a = _pay_map(p.get("eski_pay"))
        b = _pay_map(p.get("yeni_pay"))
        for iso in set(a) | set(b):
            if abs(a.get(iso, 0.0) - b.get(iso, 0.0)) > 1.00 + 1e-9:
                return False
    return any_chg


def _simulate_tahsil_map(brut_by: dict[str, float], rows_eff: list[dict]) -> dict[str, float]:
    """Panel `_aylik_tahsil_tutar_map` iki geçişi (marker'lı → markersiz oldest-open)."""
    remaining = {str(k)[:10]: round(float(v), 2) for k, v in (brut_by or {}).items() if float(v) > 0}
    marked: list[dict] = []
    unmarked: list[dict] = []
    for r in rows_eff or []:
        try:
            tut = round(float(r.get("tutar") or 0), 2)
        except (TypeError, ValueError):
            continue
        if tut <= 0:
            continue
        ac = str(r.get("aciklama") or "")
        if _has_marker(ac):
            marked.append(r)
        else:
            unmarked.append(r)

    out: dict[str, float] = defaultdict(float)

    def _alloc_oldest(rem_tutar: float) -> list[tuple[str, float]]:
        rem = round(float(rem_tutar or 0), 2)
        if rem <= 0 or not remaining:
            return []
        al: list[tuple[str, float]] = []
        for iso in sorted(remaining):
            if rem <= 0.004:
                break
            acik = round(float(remaining.get(iso) or 0), 2)
            if acik <= 0:
                continue
            pay = round(min(acik, rem), 2)
            if pay <= 0:
                continue
            al.append((iso, pay))
            remaining[iso] = round(acik - pay, 2)
            rem = round(rem - pay, 2)
        return al

    def _yaz(iso: str, pv: float) -> None:
        iso = str(iso)[:10]
        pv = round(float(pv or 0), 2)
        if pv <= 0:
            return
        out[iso] = round(out[iso] + pv, 2)
        if iso in remaining:
            remaining[iso] = round(max(float(remaining.get(iso) or 0) - pv, 0), 2)

    marked.sort(key=lambda r: (str(r.get("tarih") or "")[:10], int(r.get("id") or 0)))
    unmarked.sort(key=lambda r: (str(r.get("tarih") or "")[:10], int(r.get("id") or 0)))

    for r in marked:
        ac = str(r.get("aciklama") or "")
        tut = round(float(r.get("tutar") or 0), 2)
        pays = _pay_list(ac)
        if pays:
            for iso, pv in pays:
                _yaz(iso, pv)
            continue
        tahs = _TAH_RE.findall(ac)
        if tahs:
            n = len(tahs)
            cents = int(round(tut * 100))
            if cents <= 0 or n <= 0:
                continue
            base, rem = cents // n, cents % n
            for i, iso in enumerate(tahs):
                share = (base + (1 if i < rem else 0)) / 100.0
                _yaz(iso, share)
            continue
        for iso, pv in _alloc_oldest(tut):
            out[iso] = round(out[iso] + pv, 2)

    for r in unmarked:
        tut = round(float(r.get("tutar") or 0), 2)
        al = _alloc_oldest(tut)
        if al:
            for iso, pv in al:
                out[iso] = round(out[iso] + pv, 2)
            continue
        tah = str(r.get("tarih") or "")[:10]
        if len(tah) == 10:
            iso = f"{tah[:7]}-01"
            _yaz(iso, tut)
    return dict(out)


def _panel_cells_from_map(brut_by: dict[str, float], tahsil_map: dict[str, float]) -> dict[str, dict]:
    out = {}
    for iso, brut in sorted((brut_by or {}).items()):
        b = round(float(brut or 0), 2)
        t = round(float((tahsil_map or {}).get(iso) or 0), 2)
        if b > 0 and t > b:
            t = b
        k = round(max(b - t, 0), 2) if b > 0 else 0.0
        out[iso] = {"aylik": b, "tahsil": t, "kalan": k, "durum": _durum(t, k, b)}
    return out


def _risk_analiz(panel_once: dict, panel_bek: dict, bugun: date) -> dict:
    """(a) ödendi→borçlu/kısmi (b) vadesi gelmiş borç artışı >0.50 (c) tek ay >1000."""
    riskler = []
    for iso in sorted(set(panel_once or {}) | set(panel_bek or {})):
        o = (panel_once or {}).get(iso) or {}
        b = (panel_bek or {}).get(iso) or {}
        try:
            ot = round(float(o.get("tahsil") or 0), 2)
            ok_ = round(float(o.get("kalan") or 0), 2)
            oa = round(float(o.get("aylik") or 0), 2)
            bt = round(float(b.get("tahsil") or 0), 2)
            bk = round(float(b.get("kalan") or 0), 2)
            ba = round(float(b.get("aylik") or oa), 2)
        except (TypeError, ValueError):
            continue
        st0 = o.get("durum") or _durum(ot, ok_, oa)
        st1 = b.get("durum") or _durum(bt, bk, ba)
        if st0 == "odendi" and st1 in ("borclu", "kismi"):
            riskler.append({"tip": "odendi_gerileme", "ay": iso, "st0": st0, "st1": st1})
        dt = round(bt - ot, 2)
        dk = round(bk - ok_, 2)
        if abs(dt) > 1000 or abs(dk) > 1000:
            riskler.append({"tip": "ay_gt1000", "ay": iso, "dt": dt, "dk": dk})
    b0 = _borc_ozet(panel_once, bugun)
    b1 = _borc_ozet(panel_bek, bugun)
    artis = round(
        float(b1.get("vadesi_gelmis_kalan") or 0) - float(b0.get("vadesi_gelmis_kalan") or 0),
        2,
    )
    if artis > 0.50:
        riskler.append(
            {
                "tip": "borc_artisi",
                "delta": artis,
                "once": b0.get("vadesi_gelmis_kalan"),
                "sonra": b1.get("vadesi_gelmis_kalan"),
            }
        )
    return {
        "riskli": bool(riskler),
        "riskler": riskler,
        "borc_once": b0,
        "borc_sonra": b1,
    }


def hesapla_plan(
    mid: int,
    rows: list[dict],
    *,
    borc: dict | None = None,
) -> dict:
    """manuel_makbuz tam FIFO; markersiz kapsam-dışı kendi tarihinde SABİT tüketilir (yazılmaz)."""
    from ay_fifo import acik_ay_dagit
    from routes.faturalar_routes import (
        _gorunen_borc_satirlari,
        yeniden_hesapla_makbuz_dagitim_aciklama,
    )

    mid = int(mid)
    manuel = [r for r in rows if r.get("cls") == "manuel_makbuz"]
    diger = [r for r in rows if r.get("cls") != "manuel_makbuz"]
    if borc is None:
        borc_map = _gorunen_borc_satirlari(mid)
    else:
        borc_map = dict(borc)

    sirali = sorted(
        rows,
        key=lambda r: (str(r.get("tarih") or "")[:10], int(r.get("id") or 0)),
    )
    # Kronolojik: markersiz kapsam-dışı → kendi odeme tarihinde sanal PAY (DB'ye yazılmaz).
    # Marker'lı eşitle/grid olduğu gibi. Manuel → tam_fifo yeniden hesap.
    onceki_sabit: list[dict] = []
    batch_manuel: list[dict] = []
    plans: list[dict] = []

    for r in sirali:
        tid = int(r["id"])
        tah = str(r.get("tarih") or "")[:10]
        tutar = round(float(r.get("tutar") or 0), 2)
        ac = r.get("aciklama") or ""
        cls = r.get("cls")

        if cls != "manuel_makbuz":
            if _has_marker(ac):
                onceki_sabit.append(
                    {"id": tid, "tarih": tah, "tutar": tutar, "aciklama": ac}
                )
            else:
                # banka_import / odeme_linki / markersiz diğer — sabit FIFO tüketim
                onceki_now = list(onceki_sabit) + list(batch_manuel)
                sanal_pay = acik_ay_dagit(
                    borc_map,
                    tutar,
                    tah,
                    onceki=onceki_now,
                    sozlesme_basi=None,
                )
                onceki_sabit.append(
                    {
                        "id": tid,
                        "tarih": tah,
                        "tutar": tutar,
                        "pay": list(sanal_pay or []),
                        "aciklama": "",
                    }
                )
            continue

        eski_pay = _pay_list(ac)
        onceki = list(onceki_sabit) + list(batch_manuel)
        yeni_ac = yeniden_hesapla_makbuz_dagitim_aciklama(
            mid,
            tutar=tutar,
            tahsilat_tarihi=tah,
            aciklama_eski=ac,
            haric_tahsilat_id=tid,
            kaynak="manuel_makbuz",
            odeme_turu=r.get("odeme_turu"),
            tam_fifo=True,
            onceki_tahsilatlar=onceki,
            borc_satirlari=borc_map,
        )
        if yeni_ac is None:
            return {
                "ok": False,
                "neden": "yeniden_hesapla_none_acik_ay_yok",
                "id": tid,
                "tutar": tutar,
                "tarih": tah,
                "eski_pay": eski_pay,
                "plans": plans,
                "not": "onceki markersiz/sabit tuketim sonrasi acik ay kalmamis olabilir",
            }
        yeni_pay = _pay_list(yeni_ac)
        yeni_sum = _pay_sum(yeni_pay)
        if abs(yeni_sum - tutar) > 0.011:
            return {
                "ok": False,
                "neden": "marker_toplam_esit_degil",
                "id": tid,
                "tutar": tutar,
                "yeni_sum": yeni_sum,
                "yeni_pay": yeni_pay,
                "plans": plans,
            }
        degisti = list(eski_pay) != list(yeni_pay)
        plans.append(
            {
                "id": tid,
                "no": r.get("makbuz_no"),
                "tarih": tah,
                "tutar": tutar,
                "eski_pay": eski_pay,
                "yeni_pay": yeni_pay,
                "eski_sum": _pay_sum(eski_pay),
                "yeni_sum": yeni_sum,
                "degisti": degisti,
                "eski_aciklama": ac,
                "yeni_aciklama": yeni_ac,
            }
        )
        batch_manuel.append(
            {"id": tid, "tarih": tah, "tutar": tutar, "aciklama": yeni_ac}
        )

    return {
        "ok": True,
        "neden": None,
        "mid": mid,
        "plans": plans,
        "n_manuel": len(manuel),
        "n_diger": len(diger),
        "n_degisti": sum(1 for p in plans if p["degisti"]),
        "update_ids": [p["id"] for p in plans if p["degisti"]],
        "diger_ids": [int(r["id"]) for r in diger],
        "borc": {k: round(float(v), 2) for k, v in sorted((borc_map or {}).items())},
    }


def degerlendir_plan(
    plan: dict,
    rows: list[dict],
    snap: dict,
    bugun: date,
) -> dict:
    """ÖNEMSİZ / RİSKLİ / UYGUN + panel önce/sonra (iki geçiş simülasyonu)."""
    brut = _brut_by_iso(snap.get("payload") or {}, snap.get("by_iso") or {})
    # Mevcut panel hücreleri (DB) — durum ekle
    panel_once = {}
    for iso, cell in (snap.get("by_iso") or {}).items():
        try:
            a = round(float((cell or {}).get("aylik") or brut.get(iso) or 0), 2)
            t = round(float((cell or {}).get("tahsil") or 0), 2)
            k = round(float((cell or {}).get("kalan") or 0), 2)
        except (TypeError, ValueError):
            continue
        panel_once[str(iso)[:10]] = {
            "aylik": a,
            "tahsil": t,
            "kalan": k,
            "durum": _durum(t, k, a),
        }
    for iso, b in brut.items():
        if iso not in panel_once:
            panel_once[iso] = {
                "aylik": b,
                "tahsil": 0.0,
                "kalan": b,
                "durum": _durum(0.0, b, b),
            }

    # Efektif satırlar: manuel yeni aciklama, diger aynı (markersiz dokunulmaz)
    by_yeni = {int(p["id"]): p.get("yeni_aciklama") for p in (plan.get("plans") or [])}
    rows_eff = []
    for r in rows:
        item = dict(r)
        tid = int(r["id"])
        if tid in by_yeni and by_yeni[tid] is not None:
            item["aciklama"] = by_yeni[tid]
        rows_eff.append(item)

    tahsil_bek = _simulate_tahsil_map(brut, rows_eff)
    panel_bek = _panel_cells_from_map(brut, tahsil_bek)

    kurus = _kurus_gurultusu_mu(plan.get("plans") or [])
    risk = _risk_analiz(panel_once, panel_bek, bugun)

    n_raw = int(plan.get("n_degisti") or 0)
    update_ids = list(plan.get("update_ids") or [])
    if kurus:
        hukum = "ONEMSIZ"
        gerekce = "yalniz <=1.00 TL kurus tasmasi; uygulama gerekmez"
        n_update = 0
        update_ids = []
        # degisti bayraklarını rapor için düş
        for p in plan.get("plans") or []:
            p["degisti"] = False
        plan["n_degisti"] = 0
        plan["update_ids"] = []
    elif risk.get("riskli"):
        hukum = "RISKLI"
        gerekce = ",".join(sorted({x.get("tip") for x in (risk.get("riskler") or [])}))
        n_update = n_raw
    elif n_raw == 0:
        hukum = "UYGUN"
        gerekce = "degisiklik yok (idempotent)"
        n_update = 0
    else:
        hukum = "UYGUN"
        gerekce = "risk yok; marker onarimi uygulanabilir"
        n_update = n_raw

    fark_aylar = []
    for iso in sorted(set(panel_once) | set(panel_bek)):
        o = panel_once.get(iso) or {}
        b = panel_bek.get(iso) or {}
        if abs(float(o.get("tahsil") or 0) - float(b.get("tahsil") or 0)) > 0.05 or abs(
            float(o.get("kalan") or 0) - float(b.get("kalan") or 0)
        ) > 0.05:
            fark_aylar.append(
                {
                    "ay": iso,
                    "once_t": o.get("tahsil"),
                    "once_k": o.get("kalan"),
                    "once_st": o.get("durum"),
                    "bek_t": b.get("tahsil"),
                    "bek_k": b.get("kalan"),
                    "bek_st": b.get("durum"),
                }
            )

    return {
        "hukum": hukum,
        "gerekce": gerekce,
        "n_update": n_update,
        "update_ids": update_ids,
        "kurus": kurus,
        "risk": risk,
        "panel_once_borc": risk.get("borc_once"),
        "panel_bek_borc": risk.get("borc_sonra"),
        "fark_aylar": fark_aylar,
        "panel_bek": panel_bek,
    }


def _panel_snapshot(mid: int, fetch_one) -> dict:
    prow = fetch_one(
        "SELECT by_iso, updated_at FROM musteri_tahsilat_panel_detay WHERE musteri_id=%s",
        (int(mid),),
    )
    by_iso = {}
    if prow and prow.get("by_iso"):
        raw = prow["by_iso"]
        by_iso = json.loads(raw) if isinstance(raw, str) else (raw or {})
    grow = fetch_one(
        "SELECT payload, updated_at FROM musteri_aylik_grid_cache WHERE musteri_id=%s",
        (int(mid),),
    )
    payload = {}
    if grow and grow.get("payload"):
        raw = grow["payload"]
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return {
        "by_iso": by_iso or {},
        "payload": payload or {},
        "panel_updated": (prow or {}).get("updated_at"),
        "grid_updated": (grow or {}).get("updated_at"),
    }


def _brut_by_iso(payload: dict, by_iso: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for a in (payload or {}).get("aylar") or []:
        if not isinstance(a, dict):
            continue
        try:
            iso = date(int(a["yil"]), int(a["ay"]), 1).isoformat()
            brut = round(
                float(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil") or 0), 2
            )
        except Exception:
            continue
        if brut > 0:
            out[iso] = brut
    for iso, cell in (by_iso or {}).items():
        if iso in out:
            continue
        try:
            pa = round(float((cell or {}).get("aylik") or 0), 2)
        except (TypeError, ValueError):
            pa = 0.0
        if pa > 0:
            out[str(iso)[:10]] = pa
    return out


def _beklenen_panel(
    plans: list[dict],
    diger: list[dict],
    brut_by: dict[str, float],
    *,
    yil: int = 2026,
    ay_bas: int = 4,
    ay_bit: int = 12,
) -> dict[str, dict]:
    """Kural A: TAH-only (PAY yok) ay → tahsil=brut. PAY toplamı aksi halde."""
    pay_tot: dict[str, float] = defaultdict(float)
    tah_only: set[str] = set()
    for r in diger:
        ac = str(r.get("aciklama") or "")
        pays = _pay_list(ac)
        tahs = _TAH_RE.findall(ac)
        if pays:
            for iso, p in pays:
                pay_tot[iso] = round(pay_tot[iso] + p, 2)
        elif tahs:
            for iso in tahs:
                tah_only.add(iso)
    for pl in plans:
        for iso, p in pl.get("yeni_pay") or []:
            pay_tot[iso] = round(pay_tot[iso] + float(p), 2)
    out = {}
    for m in range(ay_bas, ay_bit + 1):
        iso = f"{yil}-{m:02d}-01"
        brut = round(float(brut_by.get(iso) or 0), 2)
        if iso in tah_only and iso not in pay_tot:
            tahsil = brut
        else:
            tahsil = round(min(pay_tot.get(iso, 0.0), brut), 2) if brut > 0 else round(
                pay_tot.get(iso, 0.0), 2
            )
        kalan = round(max(brut - tahsil, 0), 2) if brut > 0 else 0.0
        if tahsil <= 0.01:
            durum = "borclu" if brut > 0.01 else "bos"
        elif kalan <= 0.05:
            durum = "odendi"
        else:
            durum = "kismi"
        out[iso] = {
            "aylik": brut,
            "tahsil": tahsil,
            "kalan": kalan,
            "durum": durum,
        }
    return out


def _borc_ozet(by_iso_cells: dict, bugun: date) -> dict:
    kalan = 0.0
    gec = 0
    ay_basi_bugun = bugun.replace(day=1)
    for iso, cell in sorted((by_iso_cells or {}).items()):
        try:
            d = date(int(iso[:4]), int(iso[5:7]), 1)
        except Exception:
            continue
        if d > ay_basi_bugun:
            continue
        try:
            k = round(float((cell or {}).get("kalan") or 0), 2)
        except (TypeError, ValueError):
            k = 0.0
        if k > 0.05:
            kalan = round(kalan + k, 2)
            gec += 1
    return {"vadesi_gelmis_kalan": kalan, "geciken_ay": gec, "bugun": bugun.isoformat()}


def _yedek_yolu(mid: int) -> Path:
    gun = datetime.now().strftime("%Y%m%d")
    desktop = Path.home() / "Desktop"
    return desktop / f"yedek_mid{int(mid)}_marker_{gun}.json"


def _yedek_al(mid: int, rows: list[dict], snap: dict, yol: Path) -> Path:
    govde = {
        "mid": int(mid),
        "alindi": datetime.now().isoformat(timespec="seconds"),
        "tahsilatlar": [
            {
                "id": int(r["id"]),
                "makbuz_no": r.get("makbuz_no"),
                "tarih": r.get("tarih"),
                "tutar": r.get("tutar"),
                "kaynak": r.get("kaynak"),
                "cls": r.get("cls"),
                "aciklama": r.get("aciklama"),
            }
            for r in rows
        ],
        "musteri_tahsilat_panel_detay": {"by_iso": snap.get("by_iso")},
        "musteri_aylik_grid_cache": {"payload": snap.get("payload")},
    }
    yol.write_text(json.dumps(govde, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    return yol


def _audit(mid: int, plans: list[dict], execute) -> None:
    from audit_log import audit_yaz

    ozet = []
    for p in plans:
        if not p.get("degisti"):
            continue
        ozet.append(
            {
                "id": p["id"],
                "eski_pay": p.get("eski_pay"),
                "yeni_pay": p.get("yeni_pay"),
            }
        )
    audit_yaz(
        execute,
        olay="makbuz_dagitim_onar",
        varlik="tahsilatlar",
        kayit_id=int(mid),
        musteri_id=int(mid),
        kullanici="makbuz_dagitim_onar",
        anlik={"mid": int(mid), "n": len(ozet), "ids": [x["id"] for x in ozet]},
        yeni={"ozet": ozet},
    )


def _dogrula_sonrasi(mid: int, plan: dict, bugun: date, fetch_all, fetch_one) -> dict:
    rows = load_tahsilatlar(mid, fetch_all)
    by_id = {int(r["id"]): r for r in rows}
    uyusmaz = []
    for p in plan.get("plans") or []:
        r = by_id.get(int(p["id"]))
        if not r:
            uyusmaz.append({"id": p["id"], "neden": "satir_yok"})
            continue
        got = _pay_list(r.get("aciklama"))
        if got != list(p.get("yeni_pay") or []):
            uyusmaz.append(
                {"id": p["id"], "neden": "marker", "beklenen": p.get("yeni_pay"), "gercek": got}
            )
        if abs(_pay_sum(got) - float(p["tutar"])) > 0.011:
            uyusmaz.append(
                {
                    "id": p["id"],
                    "neden": "toplam",
                    "tutar": p["tutar"],
                    "pay_sum": _pay_sum(got),
                }
            )
    # kapsam dışı değişmedi mi — yedek ile kıyas script tarafında; burada marker sum
    snap = _panel_snapshot(mid, fetch_one)
    brut = _brut_by_iso(snap["payload"], snap["by_iso"])
    bek = _beklenen_panel(
        plan["plans"],
        [r for r in rows if r.get("cls") != "manuel_makbuz"],
        brut,
    )
    panel_fark = []
    for iso, cell in sorted(bek.items()):
        cur = snap["by_iso"].get(iso) or {}
        try:
            ct = round(float(cur.get("tahsil") or 0), 2)
            ck = round(float(cur.get("kalan") or 0), 2)
        except (TypeError, ValueError):
            ct, ck = 0.0, 0.0
        if abs(ct - cell["tahsil"]) > 0.05 or abs(ck - cell["kalan"]) > 0.05:
            panel_fark.append(
                {
                    "ay": iso,
                    "bek_tahsil": cell["tahsil"],
                    "bek_kalan": cell["kalan"],
                    "ger_tahsil": ct,
                    "ger_kalan": ck,
                }
            )
    borc = _borc_ozet(bek, bugun)
    return {
        "ok": not uyusmaz and not panel_fark,
        "uyusmaz_marker": uyusmaz,
        "panel_fark": panel_fark,
        "borc": borc,
        "beklenen_panel": bek,
        "panel_by_iso": {
            iso: {
                "tahsil": round(float((snap["by_iso"].get(iso) or {}).get("tahsil") or 0), 2),
                "kalan": round(float((snap["by_iso"].get(iso) or {}).get("kalan") or 0), 2),
            }
            for iso in sorted(bek)
        },
    }


def _print_dry_run(
    mid: int, plan: dict, rows: list[dict], snap: dict, bugun: date, deg: dict
) -> None:
    print("=== KAPSAM_DISI ===", plan.get("n_diger"))
    for r in rows:
        if r.get("cls") == "manuel_makbuz":
            continue
        _j(
            {
                "id": r["id"],
                "no": r.get("makbuz_no"),
                "td": r.get("tarih"),
                "T": r.get("tutar"),
                "cls": r.get("cls"),
                "pays": _pay_list(r.get("aciklama")),
                "markersiz": not _has_marker(r.get("aciklama")),
            }
        )
    print("=== MANUEL eski vs yeni ===")
    for p in plan.get("plans") or []:
        _j(
            {
                "id": p["id"],
                "no": p.get("no"),
                "td": p["tarih"],
                "T": p["tutar"],
                "eski": p["eski_pay"],
                "yeni": p["yeni_pay"],
                "T_eq_yeni": abs(p["tutar"] - p["yeni_sum"]) < 0.011,
                "degisti": p["degisti"],
            }
        )
    print("=== PANEL FARK (yalniz degisen) ===")
    for row in deg.get("fark_aylar") or []:
        _j(row)
    print("=== BORC ===")
    _j(
        {
            "mevcut": deg.get("panel_once_borc"),
            "beklenen": deg.get("panel_bek_borc"),
        }
    )
    print("=== HUKUM ===")
    _j(
        {
            "hukum": deg.get("hukum"),
            "gerekce": deg.get("gerekce"),
            "n_update": deg.get("n_update"),
            "ids": deg.get("update_ids"),
            "riskler": (deg.get("risk") or {}).get("riskler"),
        }
    )
    if deg.get("hukum") == "ONEMSIZ":
        print("ONEMSIZ (yuvarlama), uygulama gerekmez")
    elif deg.get("hukum") == "RISKLI":
        print("RISKLI")
    print("=== UPDATE PLANI ===")
    _j(
        {
            "mid": mid,
            "n_update": deg.get("n_update"),
            "ids": deg.get("update_ids"),
            "tahsilat_degisti": f"tahsilat_degisti({mid})",
            "yedek": str(_yedek_yolu(mid)),
        }
    )


def _hedef_872_uyum(plan: dict) -> dict:
    hedef = {
        5274: [("2026-07-01", 51799.00)],
        5525: [("2026-07-01", 0.80), ("2026-08-01", 51799.00)],
        5584: [("2026-08-01", 0.80), ("2026-09-01", 51799.00)],
        6063: [("2026-09-01", 0.80), ("2026-10-01", 51799.00)],
    }
    sonuc = []
    hepsi = True
    for p in plan.get("plans") or []:
        tid = int(p["id"])
        if tid not in hedef:
            continue
        ok = list(p.get("yeni_pay") or []) == hedef[tid]
        if not ok:
            hepsi = False
        sonuc.append(
            {"id": tid, "beklenen": hedef[tid], "hesaplanan": p.get("yeni_pay"), "uyum": ok}
        )
    # Kasım spill yok
    kasim = False
    for p in plan.get("plans") or []:
        for iso, _ in p.get("yeni_pay") or []:
            if str(iso).startswith("2026-11"):
                kasim = True
    return {"hepsi_uyum": hepsi and not kasim, "kasim_tasima": kasim, "satirlar": sonuc}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="manuel_makbuz dağıtım onarımı (tam FIFO)")
    ap.add_argument("--mid", type=int, required=True, help="tek musteri_id (zorunlu)")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="yaz; yalnız ALLOW_PROD_WRITE + --mid ile",
    )
    ap.add_argument(
        "--risk-onay",
        action="store_true",
        help="RİSKLİ hükümde apply'a bilinçli izin (varsayılan reddedilir)",
    )
    ap.add_argument(
        "--bugun",
        default="2026-10-30",
        help="vadesi gelmiş borç hesabı için tarih YYYY-MM-DD",
    )
    args = ap.parse_args(argv)

    mid = int(args.mid)
    if mid <= 0:
        print("HATA mid gecersiz")
        return 2

    if args.apply and not _prod_write_ok():
        print("HATA: --apply icin ALLOW_PROD_WRITE gerekli")
        return 2

    # Dry-run'da prod write kapalı kalsın
    if not args.apply:
        os.environ.pop("ALLOW_PROD_WRITE", None)

    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
    from flask import Flask

    app = Flask("makbuz_dagitim_onar")
    app.app_context().push()
    from db import execute, fetch_all, fetch_one
    import db as dbmod

    bugun = date.fromisoformat(str(args.bugun)[:10])

    if not args.apply:
        with dbmod.db() as conn:
            conn.cursor().execute("SET LOCAL transaction_read_only = on")

    rows = load_tahsilatlar(mid, fetch_all)
    plan = hesapla_plan(mid, rows)
    if not plan.get("ok"):
        print(
            "DUR",
            plan.get("neden"),
            {
                k: plan.get(k)
                for k in ("id", "tutar", "tarih", "eski_pay", "yeni_sum", "not")
            },
        )
        return 1

    snap = _panel_snapshot(mid, fetch_one)
    deg = degerlendir_plan(plan, rows, snap, bugun)
    print("=== DRY-RUN mid=%s apply=%s ===" % (mid, bool(args.apply)))
    _print_dry_run(mid, plan, rows, snap, bugun, deg)

    if mid == 872:
        print("=== HEDEF mid=872 ===")
        _j(_hedef_872_uyum(plan))

    if not args.apply:
        print(
            "DRY-RUN tamam (yazilmadi). hukum=%s n_update=%s"
            % (deg.get("hukum"), deg.get("n_update"))
        )
        return 0

    if deg.get("hukum") == "ONEMSIZ" or int(deg.get("n_update") or 0) == 0:
        print("APPLY atlandi: n_update=0 / ONEMSIZ")
        return 0

    if deg.get("hukum") == "RISKLI" and not args.risk_onay:
        print("HATA: RISKLI — --apply reddedildi ( --risk-onay gerekli )")
        return 3

    # APPLY — plan.yeni_aciklama (kronolojik sabit onceki ile üretilmiş)
    yol = _yedek_yolu(mid)
    _yedek_al(mid, rows, snap, yol)
    print("YEDEK", str(yol))

    from routes.faturalar_routes import makbuz_aciklama_sadece_guncelle
    from routes.giris_routes import tahsilat_degisti

    yazilan = 0
    for p in plan["plans"]:
        if int(p["id"]) not in set(deg.get("update_ids") or []):
            continue
        if list(p.get("eski_pay") or []) == list(p.get("yeni_pay") or []):
            continue
        tid = int(p["id"])
        yeni = p.get("yeni_aciklama") or ""
        if abs(_pay_sum(_pay_list(yeni)) - float(p["tutar"])) > 0.011:
            print("DUR marker_toplam id=%s" % tid)
            return 1
        if not makbuz_aciklama_sadece_guncelle(tid, yeni):
            print("DUR update_fail id=%s" % tid)
            return 1
        yazilan += 1

    _audit(mid, [p for p in plan["plans"] if int(p["id"]) in set(deg.get("update_ids") or [])], execute)
    tahsilat_degisti(mid)

    dog = _dogrula_sonrasi(mid, plan, bugun, fetch_all, fetch_one)
    print("=== DOGRULAMA ===")
    _j(
        {
            "ok": dog.get("ok"),
            "uyusmaz_marker": dog.get("uyusmaz_marker"),
            "panel_fark": dog.get("panel_fark"),
            "borc": dog.get("borc"),
        }
    )
    if not dog.get("ok"):
        print("DUR dogrulama uyusmazligi (self-repair yok)")
        return 1
    print("APPLY OK mid=%s n_update=%s" % (mid, yazilan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
