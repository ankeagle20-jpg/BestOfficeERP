# -*- coding: utf-8 -*-
"""Kısmi ödemeli ay: efektif brüt (daraltılmış kural).

Yalnız plan brüt VARSA plan uygulanır (867: kalan 479,84, brüt 949,68,
ekstre borç 949,68). Plansız ayda eski davranış: reel kap — grid, reel
yazan ekstreyle tutarlı kalır (363-benzeri: reel 4.398 kısmi → brüt
4.398). Panel 'aylik' plansız ayı ezmez. DB'ye yazma yok (okumalar yamalı).
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


def _build(batch, planlar=PLAN, faturali=FATURALI, reel=REEL, tahsil_map=None):
    eski = _yama(batch, tahsil_map if tahsil_map is not None else {"2026-08-01": 469.84})
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
    check("birim plan reel'i de ezer", f(1918.76, 949.68, reel_tutar=1918.76) == 949.68)
    check("birim plansiz reel kap (eski davranis)", f(1102.10, None, reel_tutar=1125.49) == 1125.49)
    check("birim plansiz brut (reel overlay) kalir", f(1918.76, None) == 1918.76)
    check("birim plansiz reelsiz brut kalir", f(1102.10, None, reel_tutar=0.0) == 1102.10)
    check("birim reel=plan ayni", f(949.68, 949.68) == 949.68)
    check("birim plan sifirsa reel kap", f(500.0, 0.0, reel_tutar=600.0) == 600.0)
    check("birim plan+reel sifirsa brut", f(500.0, 0.0) == 500.0)


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


def test_363_benzeri_plansiz():
    # 363 benzeri: plansız, reel 2026 = 4.398, kısmi ödeme 481.
    # Eski davranış: brüt 4.398 kalır; ekstre de reel (4.398) yazar → tutarlı.
    reel363 = {2023: 300.0, 2024: 1094.18, 2025: 1454.71, 2026: 4398.0}
    batch = {"marker": {"2026-08-01": 481.0}, "pay": {"2026-08-01": 481.0}}
    p = _build(batch, planlar=[], faturali=[], reel=reel363, tahsil_map={"2026-08-01": 481.0})
    a = _ay(p, 2026, 8)
    check("363 plansiz brut reel 4398", a.get("brut_tutar_kdv") == 4398.0, a)
    check("363 plansiz kalan 3917", a.get("kalan_tutar_kdv") == 3917.0, a)
    borc = gr._ekstre_ay_borcu(
        2026, 8, None, {}, None, {}, {"2026-8": 4398.0}, 8, TUFE, 0, {},
    )
    check("363 ekstre borc reel 4398", borc == 4398.0, borc)
    check("363 grid brut = ekstre borc", a.get("brut_tutar_kdv") == borc, (a, borc))


def test_zenginlestir_plansiz_reel_kap():
    # Cache yolu: payload brütü TÜFE hesabı (1102,10), reel 1125,49 →
    # plansızda eski davranış: kap reel, brüt de reel'e çekilir.
    payload = {
        "musteri_id": 0, "artis_ay": 8,
        "aylar": [{"yil": 2026, "ay": 11, "brut_tutar_kdv": 1102.10,
                   "tutar_kdv_dahil": 1102.10, "odenen_tutar_kdv": 0}],
    }
    batch = {"marker": {"2026-11-01": 2.02}, "pay": {"2026-11-01": 2.02}}
    gr._ekstre_payload_odenen_zenginlestir(payload, {}, batch, manual_reel_by_year={2026: 1125.49})
    a = payload["aylar"][0]
    check("zenginlestir plansiz brut reel 1125,49", a.get("brut_tutar_kdv") == 1125.49, a)
    check("zenginlestir plansiz kalan 1123,47", a.get("kalan_tutar_kdv") == 1123.47, a)


def _apply_calis(payload, panel, plan_dict):
    eski_rows = gr._ekstre_tahsil_rows_for_musteri
    eski_batch = gr._ekstre_tahsil_batch_maps_from_rows
    eski_plan = gr._plan_brutleri_for_mid
    gr._ekstre_tahsil_rows_for_musteri = lambda mid: []
    gr._ekstre_tahsil_batch_maps_from_rows = lambda rows: {
        "marker": {"2026-08-01": 481.0}, "pay": {"2026-08-01": 481.0},
    }
    gr._plan_brutleri_for_mid = lambda *a, **k: dict(plan_dict)
    try:
        gr._apply_panel_by_iso_to_grid_payload(
            payload, panel, tahsil_map={"2026-08-01": 481.0}
        )
    finally:
        gr._ekstre_tahsil_rows_for_musteri = eski_rows
        gr._ekstre_tahsil_batch_maps_from_rows = eski_batch
        gr._plan_brutleri_for_mid = eski_plan
    return payload["aylar"][0]


def test_apply_plansiz_panel_ezmez():
    # Panel ana-yol 'aylik' (1307,26) plansız ayda grid brütünü (reel
    # 4.398) EZMEZ — C'nin daraltılan davranışı buydu.
    payload = {
        "musteri_id": 363, "artis_ay": 8,
        "aylar": [{"yil": 2026, "ay": 8, "brut_tutar_kdv": 4398.0,
                   "tutar_kdv_dahil": 4398.0, "odenen_tutar_kdv": 0}],
    }
    panel = {"2026-08-01": {"aylik": 1307.26, "tahsil": 481.0, "kalan": 826.26}}
    a = _apply_calis(payload, panel, {})
    check("apply plansiz brut reel 4398 kalir", a.get("brut_tutar_kdv") == 4398.0, a)
    check("apply plansiz kalan 3917", a.get("kalan_tutar_kdv") == 3917.0, a)


def test_apply_planli_plan_uygular():
    payload = {
        "musteri_id": 867, "artis_ay": 8,
        "aylar": [{"yil": 2026, "ay": 8, "brut_tutar_kdv": 1918.76,
                   "tutar_kdv_dahil": 1918.76, "odenen_tutar_kdv": 0}],
    }
    panel = {"2026-08-01": {"aylik": 1918.76, "tahsil": 469.84, "kalan": 1448.92}}
    eski_rows = gr._ekstre_tahsil_rows_for_musteri
    eski_batch = gr._ekstre_tahsil_batch_maps_from_rows
    eski_plan = gr._plan_brutleri_for_mid
    gr._ekstre_tahsil_rows_for_musteri = lambda mid: []
    gr._ekstre_tahsil_batch_maps_from_rows = lambda rows: {
        "marker": {"2026-08-01": 469.84}, "pay": {"2026-08-01": 469.84},
    }
    gr._plan_brutleri_for_mid = lambda *a, **k: {"2026-08": 949.68}
    try:
        gr._apply_panel_by_iso_to_grid_payload(
            payload, panel, tahsil_map={"2026-08-01": 469.84}
        )
    finally:
        gr._ekstre_tahsil_rows_for_musteri = eski_rows
        gr._ekstre_tahsil_batch_maps_from_rows = eski_batch
        gr._plan_brutleri_for_mid = eski_plan
    a = payload["aylar"][0]
    check("apply planli brut plan 949,68", a.get("brut_tutar_kdv") == 949.68, a)
    check("apply planli kalan 479,84", a.get("kalan_tutar_kdv") == 479.84, a)


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
    test_363_benzeri_plansiz()
    test_zenginlestir_plansiz_reel_kap()
    test_apply_plansiz_panel_ezmez()
    test_apply_planli_plan_uygular()
    test_reel_esit_plan()
    test_bayraktan_bagimsiz()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
