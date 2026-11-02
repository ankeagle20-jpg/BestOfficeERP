# -*- coding: utf-8 -*-
"""Aday A: KAPANIS_N0_KAPANIS_AYI_HARIC — N=0 kapanış ayı borç dışı.

Prod yazma yok. Bayrak kapalıyken eski davranış birebir.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

os.environ.pop("ALLOW_PROD_WRITE", None)
os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)

ROOT = Path(__file__).resolve().parent
FAILS: list[str] = []


def check(name: str, ok: bool, ek="") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _base(kap: date, n, giris: date | None = None):
    r = {
        "durum": "pasif",
        "kapanis_tarihi": kap,
        "kapanis_sonrasi_borc_ay": n,
    }
    if giris is not None:
        r["rent_start_date"] = giris
        r["sozlesme_tarihi"] = giris
    return r


def test_a_bayrak_kapali():
    os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
    from importlib import reload
    import kira_kapanis_cap as kc

    reload(kc)
    kap = date(2024, 9, 9)
    r0 = _base(kap, 0)
    check("a_flag_off", kc.n0_kapanis_ayi_haric_aktif() is False)
    check("a_n0_son", kc.kira_son_borc_ayi(r0) == date(2024, 9, 1), kc.kira_son_borc_ayi(r0))
    check("a_n0_dahil_kap", kc.ay_kira_borc_dahil_mi(r0, 2024, 9) is True)
    check("a_n0_fp", kc.kapanis_ek_fingerprint(0) == "0")
    check("a_n1", kc.kira_son_borc_ayi(_base(kap, 1)) == date(2024, 10, 1))
    check("a_n3", kc.kira_son_borc_ayi(_base(kap, 3)) == date(2024, 12, 1))
    check("a_n12", kc.kira_son_borc_ayi(_base(kap, 12)) == date(2025, 9, 1))
    check("a_null", kc.kira_son_borc_ayi(_base(kap, None)) is None)
    check("a_fp1", kc.kapanis_ek_fingerprint(1) == "1")
    check("a_fp_h", kc.kapanis_ek_fingerprint(None) == "H")


def test_b_bayrak_acik():
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    from importlib import reload
    import kira_kapanis_cap as kc

    reload(kc)
    check("b_flag_on", kc.n0_kapanis_ayi_haric_aktif() is True)
    # 2024-09-09 → son 2024-08
    r0 = _base(date(2024, 9, 9), 0)
    check("b_n0_son_aug", kc.kira_son_borc_ayi(r0) == date(2024, 8, 1), kc.kira_son_borc_ayi(r0))
    check("b_n0_excl_sep", kc.kira_borc_cap_exclusive(r0) == date(2024, 9, 1))
    check("b_n0_kap_false", kc.ay_kira_borc_dahil_mi(r0, 2024, 9) is False)
    check("b_n0_aug_true", kc.ay_kira_borc_dahil_mi(r0, 2024, 8) is True)
    # 2025-09-30 → 2025-08
    r326 = _base(date(2025, 9, 30), 0)
    check("b_326_son", kc.kira_son_borc_ayi(r326) == date(2025, 8, 1), kc.kira_son_borc_ayi(r326))
    check("b_326_sep_false", kc.ay_kira_borc_dahil_mi(r326, 2025, 9) is False)
    # N=1..12 aynı
    kap = date(2024, 9, 9)
    check("b_n1_same", kc.kira_son_borc_ayi(_base(kap, 1)) == date(2024, 10, 1))
    check("b_n3_same", kc.kira_son_borc_ayi(_base(kap, 3)) == date(2024, 12, 1))
    check("b_n12_same", kc.kira_son_borc_ayi(_base(kap, 12)) == date(2025, 9, 1))
    # NULL Hepsi (bayrak hepsi kapalı)
    check("b_null_same", kc.kira_son_borc_ayi(_base(kap, None)) is None)
    # Hepsi B2 open — aynı
    os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
    reload(kc)
    giris = date(2022, 7, 7)
    son_h = kc.kira_son_borc_ayi(_base(date(2024, 9, 9), None, giris))
    check("b_hepsi_b2", son_h == date(2025, 6, 1), son_h)
    # N=0 hâlâ kap-1 (n0 bayrak + hepsi bayrak)
    check(
        "b_n0_with_hepsi",
        kc.kira_son_borc_ayi(_base(date(2024, 9, 9), 0, giris)) == date(2024, 8, 1),
    )
    os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
    reload(kc)


def test_c_pre_kap_kirpilmaz():
    """Kapanış öncesi aylar dahil (son = kap-1 iken)."""
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    from importlib import reload
    import kira_kapanis_cap as kc

    reload(kc)
    r = _base(date(2025, 9, 30), 0)
    for y, m in ((2025, 8), (2025, 7), (2024, 12), (2023, 8)):
        check("c_pre_%d_%02d" % (y, m), kc.ay_kira_borc_dahil_mi(r, y, m) is True)
    check("c_kap_out", kc.ay_kira_borc_dahil_mi(r, 2025, 9) is False)
    check("c_after_out", kc.ay_kira_borc_dahil_mi(r, 2025, 10) is False)


def test_d_fingerprint():
    from importlib import reload
    import kira_kapanis_cap as kc

    os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
    reload(kc)
    fp_off = kc.kapanis_ek_fingerprint(0)
    check("d_off_0", fp_off == "0")
    check("d_off_1", kc.kapanis_ek_fingerprint(1) == "1")
    check("d_off_h", kc.kapanis_ek_fingerprint(None) == "H")

    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    reload(kc)
    fp_on = kc.kapanis_ek_fingerprint(0)
    check("d_on_0x", fp_on == "0x")
    check("d_on_diff", fp_on != fp_off)
    check("d_on_1_same", kc.kapanis_ek_fingerprint(1) == "1")
    check("d_on_h_same", kc.kapanis_ek_fingerprint(None) == "H")


def test_e_f_g_senaryo_kalan():
    """326: 10796.40 − 848.58 = 9947.82; 371: 708 → 0 (kapanış ayı çıkınca)."""
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    from importlib import reload
    import kira_kapanis_cap as kc

    reload(kc)
    # 326 benzeri
    r326 = _base(date(2025, 9, 30), 0)
    check("f_son", kc.kira_son_borc_ayi(r326) == date(2025, 8, 1))
    kalan_once = 10796.40
    eylul = 848.58
    kalan_sonra = round(kalan_once - eylul, 2)
    check("f_kalan", kalan_sonra == 9947.82, kalan_sonra)
    check("f_ay_false", kc.ay_kira_borc_dahil_mi(r326, 2025, 9) is False)
    # 371 benzeri
    r371 = _base(date(2024, 9, 9), 0)
    check("g_son", kc.kira_son_borc_ayi(r371) == date(2024, 8, 1))
    check("g_eylul_false", kc.ay_kira_borc_dahil_mi(r371, 2024, 9) is False)
    check("g_kalan0", round(708.0 - 708.0, 2) == 0.0)


def test_fe_ve_sablon():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    check("fe_n0Haric", "n0Haric" in tjs and "KAPANIS_N0_KAPANIS_AYI_HARIC" in tjs)
    check("fe_delta", "delta = (n0Haric && ekAy === 0) ? -1 : ekAy" in tjs)
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("html_window_flag", "window.KAPANIS_N0_KAPANIS_AYI_HARIC" in html)
    check("html_jinja_flag", "kapanis_n0_kapanis_ayi_haric" in html)
    check("html_label_on", "kapanış ayı borç sayılmaz" in html)
    check("html_label_off", "sadece kapanış ayı" in html)
    check('html_opt_0', 'value="0"' in html)
    check("html_fn_delta", "n0Haric" in html and "delta + 1" in html)
    gr = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check(
        "route_pass",
        "kapanis_n0_kapanis_ayi_haric=n0_kapanis_ayi_haric_aktif()" in gr,
    )


def main():
    try:
        test_a_bayrak_kapali()
        test_b_bayrak_acik()
        test_c_pre_kap_kirpilmaz()
        test_d_fingerprint()
        test_e_f_g_senaryo_kalan()
        test_fe_ve_sablon()
    finally:
        os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
        os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("ALL_OK test_kapanis_n0_haric")


if __name__ == "__main__":
    main()
