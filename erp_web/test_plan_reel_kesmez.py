# -*- coding: utf-8 -*-
"""Plan zinciri: plan blogundan sonraki reel yillar zinciri kesmez (Secenek B).

- 867 fixture: kayit sonrasi zincir (cache build yolu) beklenen degerleri uretir.
- Onizleme == kayit sonrasi zincir (ilk planda da reel yuklenir); fark varsa uyari satiri.
- compute_rev 31: yalniz planli kartlarin rev 30 cache'i gecersiz, plansiz kartlar etkilenmez.
Canli DB'ye gitmez (okuma yardimcilari yamali)."""
import copy
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


from flask import Flask  # noqa: E402

import sozlesme_plan_api as api  # noqa: E402
from routes import giris_routes as gr  # noqa: E402
from test_sozlesme_plan import _kyc_grid  # noqa: E402

MID = 867
TUFE = {2024: {8: 40.0}, 2025: {8: 33.0}, 2026: {8: 31.9}, 2027: {8: 31.9}}
KYC = _kyc_grid(aylik_kira=300)
REEL = {2023: 300.0, 2024: 1094.18, 2025: 1454.71, 2026: 1918.76}
PLAN = [{"gecerlilik_ay": "2026-02-01", "yeni_net": 500, "kdv_oran": 20, "yeni_brut": 600, "id": 1}]
FATURALI = ["2026-03", "2026-04"]
BELGE = {"2026-03": 1454.71, "2026-04": 1454.71}
DONEM_26 = [(2026, m) for m in range(8, 13)] + [(2027, m) for m in range(1, 8)]


def hucre(p):
    return {(a["yil"], a["ay"]): a["tutar_kdv_dahil"] for a in p["aylar"]}


def _yama(planlar, reel_db):
    eski = {
        "paket": gr._plan_paket_yukle,
        "manual": gr._musteri_reel_donem_manual_dict_from_db,
        "kyc": gr._musteri_kyc_grup_for_aylik_grid,
        "tufe": gr._tufe_map_by_year_month_cached,
        "rows": gr._ekstre_tahsil_rows_for_musteri,
        "batch": gr._ekstre_tahsil_batch_maps_from_rows,
        "tmap": gr._aylik_tahsil_tutar_map,
        "acik": gr._aylik_grid_acik_tutar_ay_keys_normalized,
        "fetch_all": gr.fetch_all,
    }
    gr._plan_paket_yukle = lambda mids: {
        int(m): {"planlar": list(planlar), "faturali": list(FATURALI), "belge": dict(BELGE)} for m in mids
    }
    gr._musteri_reel_donem_manual_dict_from_db = lambda mid: dict(reel_db)
    gr._musteri_kyc_grup_for_aylik_grid = lambda mid: dict(KYC)
    gr._tufe_map_by_year_month_cached = lambda: TUFE
    gr._ekstre_tahsil_rows_for_musteri = lambda mid: []
    gr._ekstre_tahsil_batch_maps_from_rows = lambda rows: {"marker": {}, "pay": {}}
    gr._aylik_tahsil_tutar_map = lambda mid: {}
    gr._aylik_grid_acik_tutar_ay_keys_normalized = lambda mid: set()

    def fetch_all(sql, params=None):
        if "musteri_reel_donem_tutar" in str(sql):
            return [{"musteri_id": MID, "donem_yil": y, "tutar_kdv_dahil": t} for y, t in reel_db.items()]
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
    gr.fetch_all = eski["fetch_all"]
    gr._plan_kutu_sifirla()


def _kayit_sonrasi_payload():
    """resync/cache-build yolu: planli kart, reel haritasi ve kilitli aylar ile."""
    return gr._build_aylik_grid_cache_payload(
        MID, TUFE, dict(KYC), dict(REEL), planlar=list(PLAN), faturali_aylar=list(FATURALI), fatura_belge=dict(BELGE)
    )


def test_867_kayit_sonrasi_zincir():
    eski = _yama(PLAN, REEL)
    try:
        h = hucre(_kayit_sonrasi_payload())
        check("867 Agu 2025 - Oca 2026 = 1454,71 (plan oncesi)", all(abs(h[k] - 1454.71) < 0.011 for k in [(2025, 8), (2025, 12), (2026, 1)]))
        check("867 Subat 2026 = 600", abs(h[(2026, 2)] - 600) < 0.011)
        check("867 Mart/Nisan 2026 = 1454,71 (K0 kilitli)", abs(h[(2026, 3)] - 1454.71) < 0.011 and abs(h[(2026, 4)] - 1454.71) < 0.011)
        check("867 Mayis-Temmuz 2026 = 600", all(abs(h[(2026, m)] - 600) < 0.011 for m in (5, 6, 7)))
        check("867 Agu 2026 - Tem 2027 = 791,40", all(abs(h[k] - 791.40) < 0.011 for k in DONEM_26), [h[k] for k in DONEM_26])
        check("867 Agu 2027 = 1043,86", abs(h[(2027, 8)] - 1043.86) < 0.011 and abs(h[(2027, 12)] - 1043.86) < 0.011)
        check("867 1918,76 plan sonrasi hicbir aya yazilmaz", all(abs(v - 1918.76) > 0.011 for (y, m), v in h.items() if (y, m) >= (2026, 2)))
        # Reel haritasinda 2026 olmadan da ayni (reel plan sonrasi etkisiz)
        eski_reel = dict(REEL)
        eski_reel.pop(2026)
        p2 = gr._build_aylik_grid_cache_payload(
            MID, TUFE, dict(KYC), eski_reel, planlar=list(PLAN), faturali_aylar=list(FATURALI), fatura_belge=dict(BELGE)
        )
        h2 = hucre(p2)
        check("867 reel(2026) olsa da olmasa da plan sonrasi ayni", all(abs(h2[k] - h[k]) < 0.011 for k in DONEM_26 + [(2027, 8)]))
    finally:
        _geri(eski)


def test_plansiz_cache_degismedi():
    """Plansiz karttaki reel davranisi ayni: reel yili kazanir."""
    eski = _yama([], REEL)
    try:
        p = gr._build_aylik_grid_cache_payload(MID, TUFE, dict(KYC), dict(REEL), planlar=[], faturali_aylar=[], fatura_belge={})
        h = hucre(p)
        check("plansiz: reel 2026 donemi 1918,76", all(abs(h[k] - 1918.76) < 0.011 for k in DONEM_26))
        check("plansiz: plan_var yok", "plan_var" not in p)
    finally:
        _geri(eski)


def _istek(app, govde):
    with app.test_request_context(f"/giris/api/musteri/{MID}/plan/onizleme", method="POST", json=govde):
        yanit = api.plan_onizleme_yanit(MID)
    if isinstance(yanit, tuple):
        return yanit[0].get_json(), yanit[1]
    return yanit.get_json(), yanit.status_code


def _onizleme_kur():
    kayit = {k: getattr(api, k) for k in ("_musteri_ve_kyc", "_kilitler", "_kilit_k3_aylari", "_tahsil_haritasi", "_bugun")}
    api._musteri_ve_kyc = lambda mid: ({"id": int(mid), "durum": "aktif"}, dict(KYC))
    api._kilitler = lambda mid: []
    api._kilit_k3_aylari = lambda mid: []
    api._tahsil_haritasi = lambda mid: {}
    api._bugun = lambda: date(2026, 2, 10)
    os.environ["PLAN_DEGISTIR_ENABLED"] = "1"
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = str(MID)
    return kayit


def _onizleme_geri(kayit):
    for k, v in kayit.items():
        setattr(api, k, v)
    os.environ.pop("PLAN_DEGISTIR_ENABLED", None)
    os.environ.pop("PLAN_DEGISTIR_MUSTERI_IDS", None)


GOVDE = {"gecerlilik_ay": "2026-02-01", "yeni_net": 500, "kdv_oran": 20, "odeme": "banka", "yeni_brut": 600}


def test_onizleme_reel_haritasi_ilk_planda_yuklenir():
    eski = _yama([], REEL)
    try:
        check("planli_reel_haritasi: planin olmadigi kartta varsayilan bos", gr._planli_reel_haritasi([MID]) == {})
        gr._plan_kutu_sifirla()
        check("planli_reel_haritasi(tum=True): plansiz kartta da reel", (gr._planli_reel_haritasi([MID], tum=True) or {}).get(MID) == REEL)
        gr._plan_kutu_sifirla()
        zincir, mevcut, _k = api._onizleme_zinciri(MID, dict(KYC))
        check("onizleme zinciri: ilk planda reel yuklu", zincir and zincir.get("reel") == REEL and mevcut == [], (zincir or {}).get("reel"))
    finally:
        _geri(eski)


def test_onizleme_ile_kayit_ayni():
    app = Flask(__name__)
    # 1) kayit oncesi: plan yok, ilk onizleme
    eski = _yama([], REEL)
    kayit = _onizleme_kur()
    try:
        veri, kod = _istek(app, dict(GOVDE))
        check("onizleme 200", kod == 200 and veri.get("ok") is True, (kod, veri))
        onizleme = {a["ay"]: a for a in veri["aylar"]}
    finally:
        _onizleme_geri(kayit)
        _geri(eski)
    # 2) kayit sonrasi: plan yazilmis, cache build yolu
    eski = _yama(PLAN, REEL)
    try:
        h = hucre(_kayit_sonrasi_payload())
    finally:
        _geri(eski)
    farklar = []
    for ay, a in onizleme.items():
        y, m = int(ay[:4]), int(ay[5:7])
        if (y, m) not in h:
            continue
        if abs(float(a["yeni_brut"]) - float(h[(y, m)])) > 0.011:
            farklar.append((ay, a["yeni_brut"], h[(y, m)]))
    check("onizleme 24 ay == kayit sonrasi zincir (birebir)", len(onizleme) == 24 and not farklar, farklar)
    check("onizleme Subat 2026 = 600, Mart = 1454,71", onizleme["2026-02"]["yeni_brut"] == 600 and abs(onizleme["2026-03"]["yeni_brut"] - 1454.71) < 0.011)
    check("onizleme Agu 2026 = 791,40", abs(onizleme["2026-08"]["yeni_brut"] - 791.40) < 0.011 and abs(onizleme["2027-01"]["yeni_brut"] - 791.40) < 0.011)

    # 3) uyari satiri: reel kayitlari plan tarafindan yok sayilacak
    eski = _yama([], REEL)
    kayit = _onizleme_kur()
    try:
        veri, kod = _istek(app, dict(GOVDE))
        uy = [u for u in veri.get("uyarilar") or [] if u.get("kod") == "reel_yok_sayilacak"]
        beklenen = "Şu dönem reel kayıtları plan tarafından yok sayılacak: 2026: 1918,76 → 791,40."
        check("onizleme uyari satiri (2026: 1918,76 -> 791,40)", len(uy) == 1 and uy[0].get("mesaj") == beklenen, uy)
        check("onizleme yok_sayilan_reel alani", [(r["yil"], r["reel_brut"], r["plan_brut"]) for r in veri.get("yok_sayilan_reel") or []] == [(2026, 1918.76, 791.4)], veri.get("yok_sayilan_reel"))
    finally:
        _onizleme_geri(kayit)
        _geri(eski)
    # 4) reel plan zincirine esitse uyari yok; reel hic yoksa uyari yok
    for ad, reel_db in (("reel esit", {**REEL, 2026: 791.40}), ("reel yok", {})):
        eski = _yama([], reel_db)
        kayit = _onizleme_kur()
        try:
            veri, kod = _istek(app, dict(GOVDE))
            check("onizleme " + ad + ": uyari yok", kod == 200 and not [u for u in veri.get("uyarilar") or [] if u.get("kod") == "reel_yok_sayilacak"] and not veri.get("yok_sayilan_reel"), veri.get("uyarilar"))
        finally:
            _onizleme_geri(kayit)
            _geri(eski)


def test_compute_rev_plansiz_gecersiz_olmaz():
    check("rev 31 = 31", gr.AYLIK_GRID_COMPUTE_REV == 31 and gr.AYLIK_GRID_ONCEKI_REV == 30)
    uy = gr._aylik_grid_rev_uyumlu
    check("guncel rev uyumlu", uy({"compute_rev": 31}) and uy({"compute_rev": 31, "plan_var": True}))
    check("rev 30 plansiz uyumlu (gereksiz gecersiz olmaz)", uy({"compute_rev": 30}) and uy({"compute_rev": 30, "plan_var": False}))
    check("rev 30 planli gecersiz (yeniden hesaplanir)", not uy({"compute_rev": 30, "plan_var": True}))
    check("rev 29 ve eskiler gecersiz", not uy({"compute_rev": 29}) and not uy({"compute_rev": 0}) and not uy({}) and not uy(None))
    check("bozuk rev gecersiz", not uy({"compute_rev": "x"}))
    # freshness K parmak izi: rev 30 plansiz payload == 31 payload
    eski = _yama([], REEL)
    try:
        p31 = gr._aylik_grid_compute(MID, dict(KYC), TUFE, plan_katmani=False)
        p30 = copy.deepcopy(p31)
        p30["compute_rev"] = 30
        p30p = copy.deepcopy(p30)
        p30p["plan_var"] = True
        k31 = gr._aylik_grid_freshness_k_from_payload(p31)
        check("freshness K: rev 30 plansiz = rev 31", gr._aylik_grid_freshness_k_from_payload(p30) == k31)
        check("freshness K: rev 30 planli farkli", gr._aylik_grid_freshness_k_from_payload(p30p) != k31)
        check("yeni payload rev 31 uretir", p31.get("compute_rev") == 31)
    finally:
        _geri(eski)


def main():
    test_867_kayit_sonrasi_zincir()
    test_plansiz_cache_degismedi()
    test_onizleme_reel_haritasi_ilk_planda_yuklenir()
    test_onizleme_ile_kayit_ayni()
    test_compute_rev_plansiz_gecersiz_olmaz()
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("plan_reel_kesmez ok")


if __name__ == "__main__":
    main()
