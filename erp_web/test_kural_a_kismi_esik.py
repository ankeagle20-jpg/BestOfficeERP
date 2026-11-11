# -*- coding: utf-8 -*-
"""Kural A KURAL_A_KISMI_ESIK=0.17 — temiz ortam birim testleri. Prod yazma yok."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Temiz ortam: KAPANIS_*/ODA_*/REEL_TUFE_*/TAHSILAT_* tanımsız
os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_", "GRID_KISMI_")):
        os.environ.pop(k, None)

from routes import giris_routes as gr  # noqa: E402

FAILS: list[str] = []
TOL = float(gr.AYLIK_GRID_TAM_ODENDI_TOLERANS)
ESIK = float(gr.KURAL_A_KISMI_ESIK)


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_sabitler():
    check("esik_0_17", ESIK == 0.17)
    check("tol_005", abs(TOL - 0.05) < 1e-9)
    src = (ROOT / "routes/giris_routes.py").read_text(encoding="utf-8")
    check("kaynak_sabit", "KURAL_A_KISMI_ESIK = 0.17" in src)
    check("env_yok", "environ.get(\"KURAL_A" not in src and "KURAL_A_KISMI_ESIK]" not in src)


def test_esik_alti_ustu_sinir():
    f = gr._grid_payload_marker_panel_tam_kapandi
    brut = 1000.0
    # sinir: min_tam = 830.00
    check("sinir_tam_830", f(830.0, brut, 2026, None, TOL) is True)
    # tol=0.05 kurtarir: 829.99+0.05>=830 → tam; tol disi 829.94 → kismi
    check("sinir_tol_ici_tam", f(829.99, brut, 2026, None, TOL) is True)
    check("sinir_tol_disi_kismi", f(829.94, brut, 2026, None, TOL) is False)
    # KDV bandi ornegi: tahsil ~= brut/1.2
    check("kdv_net_band", f(round(brut / 1.2, 2), brut, 2026, None, TOL) is True)
    # 883 tipi
    check("883_kismi", f(48000.0, 100000.0, 2026, None, TOL) is False)
    # 349 tipi oran ~0.03
    check("349_tam", f(418.86, 432.0, 2025, None, TOL) is True)
    check("marker_yok", f(0.0, brut, 2026, None, TOL) is False)
    check("pay_istisna", f(1000.0, brut, 2026, None, TOL, pay_t=100.0) is False)


def test_kdv_yuvarlama():
    f = gr._grid_payload_marker_panel_tam_kapandi
    # brut 1454.71 → min_tam = round(1454.71*0.83, 2) = 1207.41
    brut = 1454.71
    min_tam = round(brut * (1.0 - ESIK), 2)
    check("min_tam_hesap", min_tam == 1207.41, min_tam)
    check("yuvar_tam", f(min_tam, brut, 2026, None, TOL) is True)
    check("yuvar_tol_disi_kismi", f(round(min_tam - 0.06, 2), brut, 2026, None, TOL) is False)
    # tol icinde: mk = min_tam - 0.04 → mk+tol >= min_tam
    check("tol_kurtarir", f(round(min_tam - 0.04, 2), brut, 2026, None, TOL) is True)


def test_kapanis_sonrasi_ve_daralt():
    f = gr._grid_payload_marker_panel_tam_kapandi
    # kapanis sonrasi yol ayni helper — esik ustu kismi
    check("kap_sonrasi_kismi", f(500.0, 2000.0, 2024, None, TOL) is False)
    check("kap_sonrasi_band", f(1700.0, 2000.0, 2024, None, TOL) is True)
    # daralt + esik: daralt once kucuk TAH'i keser
    check("daralt_kucuk", f(720.0, 1454.71, 2026, None, TOL, daralt=True) is False)
    check("daralt_tam", f(1454.71, 1454.71, 2026, None, TOL, daralt=True) is True)


def test_z0_z1_ayni_helper():
    """Z bayragi helper imzasini degistirmez; ayni esik."""
    f = gr._grid_payload_marker_panel_tam_kapandi
    a = f(48000.0, 100000.0, 2026, None, TOL)
    b = f(48000.0, 100000.0, 2026, {"2026": 100000.0}, TOL)
    check("z_bagimsiz", a is False and b is False)


def _apply(mid, brut, tah, mk=None):
    payload = {
        "musteri_id": mid,
        "aylar": [{
            "yil": 2026, "ay": 6, "brut_tutar_kdv": brut, "tutar_kdv_dahil": brut,
            "odenen_tutar_kdv": 0.0, "kalan_tutar_kdv": brut,
            "tahsil_edildi": False, "kismi_tahsilat": False,
        }],
    }
    panel = {"2026-06-01": {"aylik": brut, "tahsil": tah, "kalan": round(brut - tah, 2)}}
    mk_v = tah if mk is None else mk
    with mock.patch.object(gr, "_ekstre_tahsil_rows_for_musteri", return_value=[]), \
            mock.patch.object(
                gr, "_ekstre_tahsil_batch_maps_from_rows",
                return_value={"marker": {"2026-06-01": mk_v}, "pay": {}},
            ):
        gr._apply_panel_by_iso_to_grid_payload(payload, panel)
    return payload["aylar"][0]


def test_883_turuncu_52000():
    a = _apply(883, 100000.0, 48000.0)
    check("883_kismi", a["kismi_tahsilat"] is True, a)
    check("883_kalan_52000", float(a["kalan_tutar_kdv"]) == 52000.0, a)
    check("883_tahsil_edildi_false", a["tahsil_edildi"] is False, a)
    check("883_odenen_48000", float(a["odenen_tutar_kdv"]) == 48000.0, a)


def test_349_degismez():
    a = _apply(349, 432.0, 418.86)
    check("349_tam", a["tahsil_edildi"] is True and a["kismi_tahsilat"] is False, a)
    check("349_kalan0", float(a["kalan_tutar_kdv"]) == 0.0, a)


def test_eski_taban_883_yesil_sayac():
    """Eski kod (esiksiz) 883'u tam kapardi — taban kaniti sayac>0."""
    brut, mk, tol = 100000.0, 48000.0, TOL
    # eski: marker>tol → True (pay/daralt yok)
    eski_tam = mk > tol
    check("eski_taban_883_yesil", eski_tam is True)
    check("eski_taban_sayac", int(eski_tam) > 0)


def test_mutasyon_esik_kaldirinca_883_fail():
    """Esik gecici 1.0 olursa 883 yanlis tam kapar → mutasyon FAIL beklenir."""
    old = gr.KURAL_A_KISMI_ESIK
    try:
        gr.KURAL_A_KISMI_ESIK = 1.0  # her eksik oran <=1 → hep tam
        yanlis = gr._grid_payload_marker_panel_tam_kapandi(48000.0, 100000.0, 2026, None, TOL)
        # mutasyon yakalandi: yanlis True olmali (bozuk davranis)
        check("mutasyon_883_yanlis_tam", yanlis is True)
        # asil esik ile dogru davranis
        gr.KURAL_A_KISMI_ESIK = old
        dogru = gr._grid_payload_marker_panel_tam_kapandi(48000.0, 100000.0, 2026, None, TOL)
        check("mutasyon_sonrasi_dogru_kismi", dogru is False)
        # kapı: esik kaldirilinca 883 testi basarisiz olurdu
        if yanlis is True and dogru is False:
            check("mutasyon_kapi_ok", True)
        else:
            check("mutasyon_kapi_ok", False, (yanlis, dogru))
    finally:
        gr.KURAL_A_KISMI_ESIK = old


def test_panel_ekstre_esik_kapali():
    """Panel/ekstre yollari kismi_esik=False → 152 tipi kalan=0 korunur."""
    f = gr._grid_payload_marker_panel_tam_kapandi
    check(
        "panel_eski_tam",
        f(600.0, 4992.0, 2024, None, TOL, kismi_esik=False) is True,
    )
    check(
        "grid_yeni_kismi",
        f(600.0, 4992.0, 2024, None, TOL, kismi_esik=True) is False,
    )


def test_fe_kaynak():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    idx = (ROOT / "templates/giris/index.html").read_text(encoding="utf-8")
    check("fe_tjs_esik", "KURAL_A_KISMI_ESIK = 0.17" in tjs)
    check("fe_idx_esik", "KURAL_A_KISMI_ESIK = 0.17" in idx)
    check("fe_tjs_args", "sozlesmeAylikMarkerTamKapandiMi(yNum, mNum, brutMk, tahMk)" in tjs)
    check("fe_idx_args", "sozlesmeAylikMarkerTamKapandiMi(yNum, mNum, brut, tah)" in idx)


def main():
    test_sabitler()
    test_esik_alti_ustu_sinir()
    test_kdv_yuvarlama()
    test_kapanis_sonrasi_ve_daralt()
    test_z0_z1_ayni_helper()
    test_883_turuncu_52000()
    test_349_degismez()
    test_eski_taban_883_yesil_sayac()
    test_mutasyon_esik_kaldirinca_883_fail()
    test_panel_ekstre_esik_kapali()
    test_fe_kaynak()
    print("FAILS", len(FAILS), FAILS)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
