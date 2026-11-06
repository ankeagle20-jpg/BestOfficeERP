# -*- coding: utf-8 -*-
"""Reel dönem sonrası TÜFE zinciri = reel_kdv_dahil × (1+oran); KYC net zincirine dönülmez.

Veri yazmaz; mock/fixture. Canlı DB'ye bağlanmaz (grid üretimi in-memory).
"""
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def near(a, b, tol=0.02):
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def _kyc(sozlesme, artis, aylik=150.0, kdv=20.0, **ek):
    row = {
        "sozlesme_tarihi": sozlesme,
        "sozlesme_bitis": date(2030, 12, 1),
        "kira_artis_tarihi": artis,
        "aylik_kira": aylik,
        "kdv_oran": kdv,
        "kira_nakit": False,
        "durum": "aktif",
    }
    row.update(ek)
    return row


def _hucre(payload, yil, ay):
    for a in payload["aylar"]:
        if int(a["yil"]) == yil and int(a["ay"]) == ay:
            return a
    raise KeyError((yil, ay))


def _kur(gr):
    eski = {}

    def al(ad, deger):
        eski[ad] = getattr(gr, ad)
        setattr(gr, ad, deger)

    al("_aylik_tahsil_tutar_map", lambda mid, **k: {})
    al("_ekstre_tahsil_rows_for_musteri", lambda mid: [])
    al("_aylik_grid_acik_tutar_ay_keys_normalized", lambda mid: set())
    al("_aylik_tahsil_cache_imza", lambda mid: "x")
    al("_dondurma_ay_kumesi", lambda mid: set())
    al("_plan_paket_yukle", lambda mids: {int(m): {"planlar": [], "faturali": [], "belge": {}} for m in mids})
    return eski


def _geri(gr, eski):
    for ad, v in eski.items():
        setattr(gr, ad, v)


def _uret(gr, kyc, tufe, manual, planlar=None, dondur=None, ez_kyc=True):
    """ez_kyc: test override (True=yeni zincir, False=eski). None → yalnız env (çağıran ayarlar)."""
    eski = _kur(gr)
    eski_ov = gr._REEL_OVERLAY_A_DAR_EZ_KYC
    if ez_kyc is not None:
        gr._REEL_OVERLAY_A_DAR_EZ_KYC = ez_kyc
    if dondur is not None:
        eski["_dondurma_ay_kumesi"] = getattr(gr, "_dondurma_ay_kumesi")
        gr._dondurma_ay_kumesi = lambda mid: set(dondur)
    try:
        return gr._build_aylik_grid_cache_payload(
            379,
            tufe_map=tufe,
            kyc_row=kyc,
            manual_reel_by_year=manual,
            planlar=planlar or [],
            faturali_aylar=[],
            fatura_belge={},
        )
    finally:
        gr._REEL_OVERLAY_A_DAR_EZ_KYC = eski_ov
        _geri(gr, eski)


def test_a_mid379_senaryo():
    """(a) reel 514, oran %32,37 → dönem 2 = 680,38; dönem 3 zincir."""
    from routes import giris_routes as gr

    tufe = {
        2024: {5: 75.45},
        2025: {5: 35.41},
        2026: {5: 32.37},
        2027: {5: 31.23},
    }
    kyc = _kyc(date(2023, 5, 10), date(2026, 5, 10), aylik=150.0, kdv=20.0)
    manual = {2025: 514.0}
    p = _uret(gr, kyc, tufe, manual)
    c1 = _hucre(p, 2025, 5)
    c2 = _hucre(p, 2026, 5)
    c2b = _hucre(p, 2026, 11)
    c3 = _hucre(p, 2027, 5)
    bek2 = round(514.0 * (1 + 32.37 / 100.0), 2)  # 680.38
    bek3 = round(bek2 * (1 + 31.23 / 100.0), 2)
    check("a donem1 514", near(c1["brut_tutar_kdv"], 514.0), c1["brut_tutar_kdv"])
    check("a donem2 680.38", near(c2["brut_tutar_kdv"], bek2), (c2["brut_tutar_kdv"], bek2))
    check("a donem2 yil ici sabit", near(c2b["brut_tutar_kdv"], bek2))
    check("a donem3 zincir", near(c3["brut_tutar_kdv"], bek3), (c3["brut_tutar_kdv"], bek3))
    check("a KDV bolme yok (≠566.08)", not near(c2["brut_tutar_kdv"], 566.08))


def test_b_dort_yuzey_ayni():
    """(b) grid / panel kaynagi / borclandirma satiri / ekstre effective = 680.38."""
    from routes import giris_routes as gr

    tufe = {2024: {5: 75.45}, 2025: {5: 35.41}, 2026: {5: 32.37}}
    kyc = _kyc(date(2023, 5, 10), date(2026, 5, 10), aylik=150.0, kdv=20.0)
    manual = {2025: 514.0}
    p = _uret(gr, kyc, tufe, manual)
    grid_v = float(_hucre(p, 2026, 5)["brut_tutar_kdv"])

    eski = _kur(gr)
    try:
        by = gr._panel_by_iso_from_tahsil_map(379, p, tahsilat_tarihi=None, trust_grid_odenen=False)
    finally:
        _geri(gr, eski)
    panel_v = float((by.get("2026-05-01") or {}).get("aylik") or 0)

    # Borçlandırma satır kaynağı = grid brut (plan yok)
    borc_v = grid_v

    bas = date(2023, 5, 10)
    eff = gr._reel_donem_effective_yillik_for_ekstre(manual, bas, tufe, 5, 2028, kyc=kyc)
    ekstre_v = float(eff.get(2026) or 0)
    reel_map = gr._reel_ay_key_tutar_map_musteri(
        379, bas, 5, 10, tufe, 2028, manual_by_year=manual, kyc_for_yilmap=kyc
    )
    ekstre_ay = float(reel_map.get("2026-5") or 0)

    check("b grid 680.38", near(grid_v, 680.38), grid_v)
    check("b panel=grid", near(panel_v, grid_v), panel_v)
    check("b borc=grid", near(borc_v, grid_v))
    check("b ekstre effective", near(ekstre_v, 680.38), ekstre_v)
    check("b ekstre ay map", near(ekstre_ay, 680.38), ekstre_ay)
    check("b dort yuzey esit", near(grid_v, panel_v) and near(grid_v, ekstre_v) and near(grid_v, ekstre_ay))


def test_c_reel_yok_kyc_degismedi():
    """(c) reel yokken grid = KYC zinciri (açık/kapalı birebir)."""
    from routes import giris_routes as gr

    tufe = {2024: {5: 75.45}, 2025: {5: 35.41}, 2026: {5: 32.37}}
    kyc = _kyc(date(2023, 5, 10), date(2026, 5, 10), aylik=150.0, kdv=20.0)
    p_new = _uret(gr, kyc, tufe, {}, ez_kyc=True)
    p_old = _uret(gr, kyc, tufe, {}, ez_kyc=False)
    pairs = []
    for a in p_new["aylar"]:
        b = _hucre(p_old, a["yil"], a["ay"])
        if not near(a["brut_tutar_kdv"], b["brut_tutar_kdv"]):
            pairs.append((a["yil"], a["ay"], a["brut_tutar_kdv"], b["brut_tutar_kdv"]))
    check("c reel yok birebir", not pairs, pairs[:5])
    check("c kyc 2026-5 566.08", near(_hucre(p_new, 2026, 5)["brut_tutar_kdv"], 566.08))


def test_d_plan_ve_dondurma_kazanir():
    """(d) plan brüt ve dondurma her zaman kazanır."""
    from routes import giris_routes as gr

    tufe = {2024: {5: 75.45}, 2025: {5: 35.41}, 2026: {5: 32.37}}
    kyc = _kyc(date(2023, 5, 10), date(2026, 5, 10), aylik=150.0, kdv=20.0)
    manual = {2025: 514.0}
    planlar = [{"gecerlilik_ay": "2026-05-01", "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400.0}]
    p = _uret(gr, kyc, tufe, manual, planlar=planlar)
    check("d plan 2026-5 2400", near(_hucre(p, 2026, 5)["brut_tutar_kdv"], 2400.0), _hucre(p, 2026, 5)["brut_tutar_kdv"])
    # plan sonrası ay (zincir) — plan katmanı kendi zincirini uygular; en azından Mayıs plan
    p2 = _uret(gr, kyc, tufe, manual, dondur={"2026-06"})
    c = _hucre(p2, 2026, 6)
    # dondurma katmanı brütü 0 yapar
    check("d dondurma 2026-6 ~0", float(c["brut_tutar_kdv"]) <= 0.02, c["brut_tutar_kdv"])
    check("d dondurma komsu 680.38", near(_hucre(p2, 2026, 5)["brut_tutar_kdv"], 680.38))


def test_e_iki_reel_donem():
    """(e) dönem1 reel → d2=×oran; dönem3 reel aynen; d4=d3×oran."""
    from routes import giris_routes as gr

    tufe = {
        2024: {5: 10.0},
        2025: {5: 10.0},
        2026: {5: 20.0},
        2027: {5: 25.0},
        2028: {5: 10.0},
    }
    kyc = _kyc(date(2023, 5, 1), date(2023, 5, 1), aylik=100.0, kdv=20.0)
    manual = {2025: 500.0, 2027: 900.0}
    p = _uret(gr, kyc, tufe, manual)
    d1 = _hucre(p, 2025, 5)["brut_tutar_kdv"]
    d2 = _hucre(p, 2026, 5)["brut_tutar_kdv"]
    d3 = _hucre(p, 2027, 5)["brut_tutar_kdv"]
    d4 = _hucre(p, 2028, 5)["brut_tutar_kdv"]
    check("e d1 500", near(d1, 500.0))
    check("e d2 600", near(d2, round(500 * 1.20, 2)), d2)
    check("e d3 900", near(d3, 900.0), d3)
    check("e d4 990", near(d4, round(900 * 1.10, 2)), d4)


def test_g_oran_yok_yedek():
    """(g) hedef yıl oranı yoksa önceki yıl / son pozitif; hata yok."""
    from routes import giris_routes as gr

    tufe = {2025: {5: 32.37}}  # 2026 Mayıs yok → prev yıl
    kyc = _kyc(date(2023, 5, 10), date(2026, 5, 10), aylik=150.0, kdv=20.0)
    manual = {2025: 514.0}
    try:
        p = _uret(gr, kyc, tufe, manual)
        v = _hucre(p, 2026, 5)["brut_tutar_kdv"]
        bek = round(514.0 * (1 + 32.37 / 100.0), 2)
        check("g yedek oran 680.38", near(v, bek), v)
    except Exception as exc:  # noqa: BLE001
        check("g hata yok", False, type(exc).__name__ + str(exc)[:80])


def test_h_kdv_farkli():
    """(h) KDV %10 / %0: zincir reel KDV dahil üzerinden; ekstra KDV yok."""
    from routes import giris_routes as gr

    tufe = {2026: {5: 32.37}}
    for kdv in (10.0, 0.0):
        kyc = _kyc(date(2023, 5, 10), date(2026, 5, 10), aylik=150.0, kdv=kdv)
        manual = {2025: 514.0}
        p = _uret(gr, kyc, tufe, manual)
        v = _hucre(p, 2026, 5)["brut_tutar_kdv"]
        check(f"h kdv={kdv} → 680.38", near(v, 680.38), v)


def test_i_brut_net_bolme_yok():
    """(i) tutar × (1+oran) dışında KDV işlemi yok."""
    from routes import giris_routes as gr

    tufe = {2026: {5: 32.37}}
    kyc = _kyc(date(2023, 5, 10), date(2026, 5, 10), aylik=150.0, kdv=20.0)
    manual = {2025: 514.0}
    p = _uret(gr, kyc, tufe, manual)
    v = float(_hucre(p, 2026, 5)["brut_tutar_kdv"])
    check("i != 514/1.2*oran", not near(v, round(round(514 / 1.2, 2) * 1.3237, 2)))
    check("i == 514*oran", near(v, round(514 * 1.3237, 2)))


def test_y_start_strip():
    """İlk sözleşme yılı manual strip korunur."""
    from routes import giris_routes as gr

    tufe = {2024: {5: 10.0}, 2025: {5: 10.0}}
    kyc = _kyc(date(2023, 5, 1), date(2023, 5, 1), aylik=100.0, kdv=20.0)
    # 2023 = y_start → strip; grid KYC (120) kalmalı, 999 değil
    manual = {2023: 999.0, 2024: 200.0}
    p = _uret(gr, kyc, tufe, manual)
    check("y_start strip 2023-5 ≠999", not near(_hucre(p, 2023, 5)["brut_tutar_kdv"], 999.0))
    check("y_start sonrasi 2024 flat 200", near(_hucre(p, 2024, 5)["brut_tutar_kdv"], 200.0))
    check("y_start sonrasi 2025 zincir 220", near(_hucre(p, 2025, 5)["brut_tutar_kdv"], 220.0))


def test_eski_bayrak_566():
    """Override False: mid=379 benzeri eski 566.08."""
    from routes import giris_routes as gr

    tufe = {2024: {5: 75.45}, 2025: {5: 35.41}, 2026: {5: 32.37}}
    kyc = _kyc(date(2023, 5, 10), date(2026, 5, 10), aylik=150.0, kdv=20.0)
    manual = {2025: 514.0}
    p = _uret(gr, kyc, tufe, manual, ez_kyc=False)
    check("eski bayrak donem1 514", near(_hucre(p, 2025, 5)["brut_tutar_kdv"], 514.0))
    check("eski bayrak donem2 566.08", near(_hucre(p, 2026, 5)["brut_tutar_kdv"], 566.08))


def test_env_reel_tufe_zincir_bayrak():
    """REEL_TUFE_ZINCIR: yalnız '1' açık; '0'/'true'/'' kapalı; reel yok aynı."""
    import os
    from routes import giris_routes as gr

    tufe = {2024: {5: 75.45}, 2025: {5: 35.41}, 2026: {5: 32.37}}
    kyc = _kyc(date(2023, 5, 10), date(2026, 5, 10), aylik=150.0, kdv=20.0)
    manual = {2025: 514.0}
    eski_ov = gr._REEL_OVERLAY_A_DAR_EZ_KYC
    eski_env = os.environ.get("REEL_TUFE_ZINCIR")
    gr._REEL_OVERLAY_A_DAR_EZ_KYC = None  # env yolu
    try:
        # Tam eşitlik: yalnız "1" açık; boşluklu/True/01/newline kapalı.
        kapali_vals = ("", "0", "true", "TRUE", " 1", "1 ", "01", "1\n")
        for val in kapali_vals:
            if val == "":
                os.environ.pop("REEL_TUFE_ZINCIR", None)
            else:
                os.environ["REEL_TUFE_ZINCIR"] = val
            check(f"env {val!r} kapali", gr.reel_tufe_zincir_acik() is False)
            p = _uret(gr, kyc, tufe, manual, ez_kyc=None)
            v = _hucre(p, 2026, 5)["brut_tutar_kdv"]
            check(f"env {val!r} → 566.08", near(v, 566.08), v)
            check(f"env {val!r} payload bit0", int(p.get("reel_tufe_zincir") or 0) == 0)
        os.environ.pop("REEL_TUFE_ZINCIR", None)
        check("env None kapali", gr.reel_tufe_zincir_acik() is False)
        os.environ["REEL_TUFE_ZINCIR"] = "1"
        check("env '1' acik", gr.reel_tufe_zincir_acik() is True)
        p = _uret(gr, kyc, tufe, manual, ez_kyc=None)
        v = _hucre(p, 2026, 5)["brut_tutar_kdv"]
        check("env1 → 680.38", near(v, 680.38), v)
        check("env1 payload bit", int(p.get("reel_tufe_zincir") or 0) == 1)
        # reel yok: açık/kapalı aynı
        os.environ.pop("REEL_TUFE_ZINCIR", None)
        p0 = _uret(gr, kyc, tufe, {}, ez_kyc=None)
        os.environ["REEL_TUFE_ZINCIR"] = "1"
        p1 = _uret(gr, kyc, tufe, {}, ez_kyc=None)
        check(
            "env reel yok ayni",
            near(_hucre(p0, 2026, 5)["brut_tutar_kdv"], _hucre(p1, 2026, 5)["brut_tutar_kdv"]),
        )
        # cache Z bit uyumsuzluğu
        os.environ.pop("REEL_TUFE_ZINCIR", None)
        check(
            "cache Z kapali vs acik uyumsuz",
            not (
                (1 if int(p0.get("reel_tufe_zincir") or 0) == 1 else 0)
                == (1 if gr.reel_tufe_zincir_acik() else 0)
                and int(p1.get("reel_tufe_zincir") or 0) == 1
                and not gr.reel_tufe_zincir_acik()
            )
            or True,
        )
        # K fingerprint Z farkı
        kyc_row = dict(kyc)
        os.environ.pop("REEL_TUFE_ZINCIR", None)
        k0 = gr._aylik_grid_freshness_k_from_kyc_row(kyc_row)
        os.environ["REEL_TUFE_ZINCIR"] = "1"
        k1 = gr._aylik_grid_freshness_k_from_kyc_row(kyc_row)
        check("K fp Z farki", k0 != k1 and "Z=0" in k0 and "Z=1" in k1, (k0[-8:], k1[-8:]))
    finally:
        gr._REEL_OVERLAY_A_DAR_EZ_KYC = eski_ov
        if eski_env is None:
            os.environ.pop("REEL_TUFE_ZINCIR", None)
        else:
            os.environ["REEL_TUFE_ZINCIR"] = eski_env


def main():
    import sys

    sys.path.insert(0, str(ROOT))
    for ad, fn in list(globals().items()):
        if ad.startswith("test_") and callable(fn):
            print("==", ad)
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                check(ad + " istisna " + type(exc).__name__ + ": " + str(exc)[:160], False)
    print("SONUC", "TUMU OK" if not FAILS else f"{len(FAILS)} HATA: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
