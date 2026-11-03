# -*- coding: utf-8 -*-
"""KAPANIS_N_KAPANIS_AYI_DAHIL: N=k → son = kap+(k−1). Prod yazma yok."""
from __future__ import annotations

import os
from datetime import date
from importlib import reload
from pathlib import Path

os.environ.pop("ALLOW_PROD_WRITE", None)
os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
os.environ.pop("KAPANIS_N_KAPANIS_AYI_DAHIL", None)
os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)

ROOT = Path(__file__).resolve().parent
FAILS: list[str] = []


def check(name: str, ok: bool, ek="") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _base(kap: date, n, giris: date | None = None, *, iptal=False):
    r = {
        "durum": "pasif",
        "kapanis_tarihi": kap,
        "kapanis_sonrasi_borc_ay": n,
        "iptal": iptal,
    }
    if giris is not None:
        r["rent_start_date"] = giris
        r["sozlesme_tarihi"] = giris
    return r


def _reload_kc():
    import kira_kapanis_cap as kc

    kc._warned_dahil_sans_n0 = False  # type: ignore[attr-defined]
    return reload(kc)


def test_a_bayrak_kapali():
    os.environ.pop("KAPANIS_N_KAPANIS_AYI_DAHIL", None)
    os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
    os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
    kc = _reload_kc()
    kap = date(2024, 9, 9)
    check("a_flag_off", kc.n_kapanis_ayi_dahil_aktif() is False)
    check("a_eff_off", kc.n_kapanis_ayi_dahil_efektif() is False)
    # Eski Aday B: N=k → kap+k
    check("a_n1", kc.kira_son_borc_ayi(_base(kap, 1)) == date(2024, 10, 1))
    check("a_n2", kc.kira_son_borc_ayi(_base(kap, 2)) == date(2024, 11, 1))
    check("a_n12", kc.kira_son_borc_ayi(_base(kap, 12)) == date(2025, 9, 1))
    check("a_fp1", kc.kapanis_ek_fingerprint(1) == "1")
    check("a_fp12", kc.kapanis_ek_fingerprint(12) == "12")
    check("a_fp0", kc.kapanis_ek_fingerprint(0) == "0")
    check("a_fp_h", kc.kapanis_ek_fingerprint(None) == "H")


def test_b_bayrak_acik():
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    os.environ["KAPANIS_N_KAPANIS_AYI_DAHIL"] = "1"
    os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
    kc = _reload_kc()
    check("b_eff_on", kc.n_kapanis_ayi_dahil_efektif() is True)
    kap = date(2024, 9, 9)
    # N=1 → yalnız kapanış ayı
    check("b_n1", kc.kira_son_borc_ayi(_base(kap, 1)) == date(2024, 9, 1), kc.kira_son_borc_ayi(_base(kap, 1)))
    check("b_n2", kc.kira_son_borc_ayi(_base(kap, 2)) == date(2024, 10, 1))
    check("b_n12", kc.kira_son_borc_ayi(_base(kap, 12)) == date(2025, 8, 1))
    # N=0 → kap−1 (N0)
    check("b_n0", kc.kira_son_borc_ayi(_base(kap, 0)) == date(2024, 8, 1))
    # NULL Hepsi (hepsi bayrak kapalı)
    check("b_null", kc.kira_son_borc_ayi(_base(kap, None)) is None)
    # Hepsi B2
    os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
    kc = _reload_kc()
    giris = date(2022, 7, 7)
    son_h = kc.kira_son_borc_ayi(_base(date(2024, 9, 9), None, giris))
    check("b_hepsi", son_h == date(2025, 6, 1), son_h)
    # iptal: giriş−1
    son_i = kc.kira_son_borc_ayi(_base(kap, 3, giris, iptal=True))
    check("b_iptal", son_i == date(2022, 6, 1), son_i)
    check("b_fp_iptal", kc.kapanis_ek_fingerprint(3, iptal=True) == "IPTAL")
    os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
    kc = _reload_kc()


def test_c_pre_kap():
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    os.environ["KAPANIS_N_KAPANIS_AYI_DAHIL"] = "1"
    kc = _reload_kc()
    r = _base(date(2025, 9, 30), 1)
    for y, m in ((2025, 8), (2025, 7), (2024, 12)):
        check("c_pre_%d_%02d" % (y, m), kc.ay_kira_borc_dahil_mi(r, y, m) is True)
    check("c_kap_in_n1", kc.ay_kira_borc_dahil_mi(r, 2025, 9) is True)
    check("c_after_out", kc.ay_kira_borc_dahil_mi(r, 2025, 10) is False)


def test_d_fingerprint():
    os.environ.pop("KAPANIS_N_KAPANIS_AYI_DAHIL", None)
    os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
    kc = _reload_kc()
    fp1_off = kc.kapanis_ek_fingerprint(1)
    fp0_off = kc.kapanis_ek_fingerprint(0)
    fp_h_off = kc.kapanis_ek_fingerprint(None)
    check("d_off_1", fp1_off == "1")
    check("d_off_0", fp0_off == "0")
    check("d_off_h", fp_h_off == "H")

    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    os.environ["KAPANIS_N_KAPANIS_AYI_DAHIL"] = "1"
    kc = _reload_kc()
    check("d_on_1d", kc.kapanis_ek_fingerprint(1) == "1d")
    check("d_on_12d", kc.kapanis_ek_fingerprint(12) == "12d")
    check("d_on_diff", kc.kapanis_ek_fingerprint(1) != fp1_off)
    check("d_on_0x_same_n0", kc.kapanis_ek_fingerprint(0) == "0x")
    check("d_on_h_same", kc.kapanis_ek_fingerprint(None) == "H")


def test_e_ay_dahil():
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    os.environ["KAPANIS_N_KAPANIS_AYI_DAHIL"] = "1"
    kc = _reload_kc()
    kap = date(2024, 9, 15)
    r2 = _base(kap, 2)
    check("e_excl", kc.kira_borc_cap_exclusive(r2) == date(2024, 11, 1))
    check("e_sep", kc.ay_kira_borc_dahil_mi(r2, 2024, 9) is True)
    check("e_oct", kc.ay_kira_borc_dahil_mi(r2, 2024, 10) is True)
    check("e_nov", kc.ay_kira_borc_dahil_mi(r2, 2024, 11) is False)


def test_f_fe_sablon():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    check("f_nDahil", "nDahil" in tjs and "KAPANIS_N_KAPANIS_AYI_DAHIL" in tjs)
    check("f_delta_n1", "n0Haric && nDahil && ekAy >= 1" in tjs)
    check("f_delta_n0", "n0Haric && ekAy === 0" in tjs and "delta = -1" in tjs)
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("f_window", "window.KAPANIS_N_KAPANIS_AYI_DAHIL" in html)
    check("f_jinja", "kapanis_n_kapanis_ayi_dahil" in html)
    check("f_label_1", "yalnız kapanış ayı" in html)
    check("f_label_2", "kapanış ayı + 1 ay" in html)
    check("f_label_12", "kapanış ayı + 11 ay" in html)
    check('f_opt_vals', all(('value="%d"' % i) in html for i in range(0, 13)))
    check('f_opt_hepsi', 'value=""' in html and "Hepsi" in html)
    gr = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check(
        "f_route",
        "kapanis_n_kapanis_ayi_dahil=n_kapanis_ayi_dahil_efektif()" in gr,
    )


def test_g_dahil_n0_yok_etkisiz():
    os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
    os.environ["KAPANIS_N_KAPANIS_AYI_DAHIL"] = "1"
    kc = _reload_kc()
    check("g_aktif_ham", kc.n_kapanis_ayi_dahil_aktif() is True)
    check("g_eff_false", kc.n_kapanis_ayi_dahil_efektif() is False)
    kap = date(2024, 9, 9)
    check("g_n1_eski", kc.kira_son_borc_ayi(_base(kap, 1)) == date(2024, 10, 1))
    check("g_fp1_eski", kc.kapanis_ek_fingerprint(1) == "1")


def main():
    try:
        test_a_bayrak_kapali()
        test_b_bayrak_acik()
        test_c_pre_kap()
        test_d_fingerprint()
        test_e_ay_dahil()
        test_f_fe_sablon()
        test_g_dahil_n0_yok_etkisiz()
    finally:
        os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
        os.environ.pop("KAPANIS_N_KAPANIS_AYI_DAHIL", None)
        os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("ALL_OK test_kapanis_n_dahil")


if __name__ == "__main__":
    main()
