# -*- coding: utf-8 -*-
"""Kısmi ödemeli ay: efektif brüt reel'den değil ana yoldan (plan > brüt).

867 fixture: plan brüt 949,68; reel 2026 = 1918,76; AYLIK_PAY/marker
469,84 kısmi ödeme. Beklenen: grid kalan 479,84 (turuncu/kısmi), panel
aynı, ekstre borç 949,68. Canlı DB'ye yazma yok (tüm okumalar yamalı).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)
os.environ.pop("GRID_KISMI_GOSTER_ENABLED", None)
os.environ.pop("GRID_KISMI_GOSTER_MUSTERI_IDS", None)

from routes import giris_routes as gr  # noqa: E402
from test_sozlesme_plan import _kyc_grid  # noqa: E402

FAILS = []
MID = 867
TUFE = {2024: {8: 40.0}, 2025: {8: 33.0}, 2026: {8: 31.9}, 2027: {8: 31.9}}
KYC = _kyc_grid(aylik_kira=300)
REEL = {2023: 300.0, 2024: 1094.18, 2025: 1454.71, 2026: 1918.76}
PLAN = [{"gecerlilik_ay": "2026-01-01", "yeni_net": 600, "kdv_oran": 20, "yeni_brut": 720.0, "id": 1}]
# Ağustos, ödemenin kendisiyle kilide (K2) dönmüş gibi: normal plan
# haritasında yok; kilitsiz zincirde plan brüt 949,68.
FATURALI = ["2026-08"]
BELGE = {}


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _yama(batch, tahsil_map=None):
    eski = {
        "paket": gr._plan_paket_yukle,
        "manual": gr._musteri_reel_donem_manual_dict_from_db,
        "kyc": gr._musteri_kyc_grup_for_aylik_grid,
        "tufe": gr._tufe_map_by_year_month_cached,
        "rows": gr._ekstre_tahsil_rows_for_musteri,
        "batch": gr._ekstre_tahsil_batch_maps_from_rows,
        "tmap": gr._aylik_tahsil_tutar_map,
        "acik": gr._aylik_grid_acik_tutar_ay_keys_normalized,
        "panel_load": gr._load_musteri_panel_by_iso,
        "fetch_all": gr.fetch_all,
    }
    gr._plan_paket_yukle = lambda mids: {
        int(m): {"planlar": list(PLAN), "faturali": list(FATURALI), "belge": dict(BELGE)}
        for m in mids
    }
    gr._musteri_reel_donem_manual_dict_from_db = lambda mid: dict(REEL)
    gr._musteri_kyc_grup_for_aylik_grid = lambda mid: dict(KYC)
    gr._tufe_map_by_year_month_cached = lambda: TUFE
    gr._ekstre_tahsil_rows_for_musteri = lambda mid: []
    gr._ekstre_tahsil_batch_maps_from_rows = lambda rows: batch
    gr._aylik_tahsil_tutar_map = lambda mid, **kw: dict(tahsil_map or {})
    gr._aylik_grid_acik_tutar_ay_keys_normalized = lambda mid: set()
    gr._load_musteri_panel_by_iso = lambda mid: {}

    def fetch_all(sql, params=None):
        if "musteri_reel_donem_tutar" in str(sql):
            return [
                {"musteri_id": MID, "donem_yil": y, "tutar_kdv_dahil": t}
                for y, t in REEL.items()
            ]
        return []

    gr.fetch_all = fetch_all
    gr._plan_kutu_sifirla()
    return eski


def _geri(eski):
    gr._plan_paket_yukle = eski["paket"]
    gr._musteri_reel_donem_manual_dict_from_db = eski["manual"]
    gr._musteri_kyc_grup_for_aylik_grid = eski["kyc"]
    gr._tufe_map_by_year_month_cached = eski["tufe"]
    gr._ekstre_tahsil_rows_for_musteri = eski["rows"]
    gr._ekstre_tahsil_batch_maps_from_rows = eski["batch"]
    gr._aylik_tahsil_tutar_map = eski["tmap"]
    gr._aylik_grid_acik_tutar_ay_keys_normalized = eski["acik"]
    gr._load_musteri_panel_by_iso = eski["panel_load"]
    gr.fetch_all = eski["fetch_all"]
    gr._plan_kutu_sifirla()


def _build(batch, planlar=PLAN, faturali=FATURALI, reel=REEL):
    eski = _yama(batch, {"2026-08-01": 469.84})
    try:
        return gr._build_aylik_grid_cache_payload(
            MID, TUFE, dict(KYC), dict(reel),
            planlar=list(planlar), faturali_aylar=list(faturali), fatura_belge=dict(BELGE),
        )
    finally:
        _geri(eski)


def _ay(payload, y, m):
    for a in payload.get("aylar") or []:
        if a.get("yil") == y and a.get("ay") == m:
            return a
    return {}


KISMI_BATCH = {"marker": {"2026-08-01": 469.84}, "pay": {"2026-08-01": 469.84}}


def test_birim():
    f = gr._kismi_efektif_brut
    check("birim plan kazanir", f(1918.76, 949.68) == 949.68)
    check("birim plansiz brut (reel overlay) kalir", f(1918.76, None) == 1918.76)
    check("birim reel=plan ayni", f(949.68, 949.68) == 949.68)
    check("birim plan sifirsa brut", f(500.0, 0.0) == 500.0)


def test_867_kismi_grid():
    p = _build(KISMI_BATCH)
    a = _ay(p, 2026, 8)
    check("867 grid brut 949,68", a.get("brut_tutar_kdv") == 949.68, a)
    check("867 grid odenen 469,84", a.get("odenen_tutar_kdv") == 469.84, a)
    check("867 grid kalan 479,84", a.get("kalan_tutar_kdv") == 479.84, a)
    check("867 grid kismi turuncu", a.get("kismi_tahsilat") is True and a.get("tahsil_edildi") is False, a)
    s = _ay(p, 2026, 9)
    check("odemesiz Eylul 949,68 kalan tam", s.get("brut_tutar_kdv") == 949.68 and s.get("kalan_tutar_kdv") == 949.68 and s.get("kismi_tahsilat") is False, s)


def test_867_panel_ayni():
    p = _build(KISMI_BATCH)
    eski = _yama(KISMI_BATCH, {"2026-08-01": 469.84})
    try:
        by_iso = gr._panel_by_iso_from_tahsil_map(MID, p)
    finally:
        _geri(eski)
    r = by_iso.get("2026-08-01") or {}
    check("panel aylik 949,68", r.get("aylik") == 949.68, r)
    check("panel tahsil 469,84", r.get("tahsil") == 469.84, r)
    check("panel kalan 479,84", r.get("kalan") == 479.84, r)


def test_867_ekstre_borc():
    reel_ay = {"2026-8": 1918.76}
    borc_planli = gr._ekstre_ay_borcu(
        2026, 8, None, {"2026-08-01": 1918.76}, None, {}, reel_ay, 8, TUFE, 0,
        {"2026-08": 949.68},
    )
    check("ekstre borc plan 949,68 (reel ezmez)", borc_planli == 949.68, borc_planli)
    borc_plansiz = gr._ekstre_ay_borcu(
        2026, 8, None, {"2026-08-01": 949.68}, None, {}, reel_ay, 8, TUFE, 0, {},
    )
    check("ekstre plansiz reel kazanir 1918,76", borc_plansiz == 1918.76, borc_plansiz)


def test_tam_odenmis():
    batch = {"marker": {"2026-09-01": 949.68}, "pay": {}}
    p = _build(batch)
    a = _ay(p, 2026, 9)
    check("tam ay kalan 0 edildi", a.get("kalan_tutar_kdv") == 0.0 and a.get("tahsil_edildi") is True and a.get("kismi_tahsilat") is False, a)


def test_plansiz_reel_kazanir():
    p = _build(KISMI_BATCH, planlar=[], faturali=[])
    a = _ay(p, 2026, 8)
    check("plansiz kismi brut reel 1918,76", a.get("brut_tutar_kdv") == 1918.76, a)
    check("plansiz kismi kalan 1448,92", a.get("kalan_tutar_kdv") == 1448.92, a)


def test_reel_esit_plan():
    reel2 = dict(REEL)
    reel2[2026] = 949.68
    p = _build(KISMI_BATCH, reel=reel2)
    a = _ay(p, 2026, 8)
    check("reel=plan kalan 479,84", a.get("kalan_tutar_kdv") == 479.84 and a.get("brut_tutar_kdv") == 949.68, a)


def test_bayraktan_bagimsiz():
    for env in (None, "1"):
        eski_env = os.environ.get("GRID_KISMI_GOSTER_ENABLED")
        try:
            if env is None:
                os.environ.pop("GRID_KISMI_GOSTER_ENABLED", None)
            else:
                os.environ["GRID_KISMI_GOSTER_ENABLED"] = env
                os.environ["GRID_KISMI_GOSTER_MUSTERI_IDS"] = "867"
            p = _build(KISMI_BATCH)
            a = _ay(p, 2026, 8)
            check(f"bayrak={env} kalan 479,84", a.get("kalan_tutar_kdv") == 479.84, a)
        finally:
            os.environ.pop("GRID_KISMI_GOSTER_ENABLED", None)
            os.environ.pop("GRID_KISMI_GOSTER_MUSTERI_IDS", None)
            if eski_env is not None:
                os.environ["GRID_KISMI_GOSTER_ENABLED"] = eski_env


def main():
    test_birim()
    test_867_kismi_grid()
    test_867_panel_ayni()
    test_867_ekstre_borc()
    test_tam_odenmis()
    test_plansiz_reel_kazanir()
    test_reel_esit_plan()
    test_bayraktan_bagimsiz()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
