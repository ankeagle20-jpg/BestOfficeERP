# -*- coding: utf-8 -*-
"""Büyük sayfa borç: kısmi ay = fo_cache kalan. Canlı yazma yok. PII yok."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:280]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def test_kismi_kalan_helper():
    from routes import giris_routes as gr

    pl = {
        "aylar": [
            {
                "ay_key": "2024-4",
                "yil": 2024,
                "ay": 4,
                "tutar_kdv_dahil": 420.0,
                "brut_tutar_kdv": 420.0,
                "odenen_tutar_kdv": 240.0,
                "kalan_tutar_kdv": 180.0,
                "tahsil_edildi": False,
                "kismi_tahsilat": True,
            },
            {
                "ay_key": "2024-5",
                "yil": 2024,
                "ay": 5,
                "tutar_kdv_dahil": 420.0,
                "brut_tutar_kdv": 420.0,
                "odenen_tutar_kdv": 0.0,
                "kalan_tutar_kdv": 420.0,
                "tahsil_edildi": False,
                "kismi_tahsilat": False,
            },
            {
                "ay_key": "2024-3",
                "yil": 2024,
                "ay": 3,
                "tutar_kdv_dahil": 420.0,
                "brut_tutar_kdv": 420.0,
                "odenen_tutar_kdv": 420.0,
                "kalan_tutar_kdv": 0.0,
                "tahsil_edildi": True,
                "kismi_tahsilat": False,
            },
        ]
    }
    m = gr._firma_ozet_kismi_kalan_by_ay_from_payload(pl)
    check("kismi map tek ay", m == {"2024-4": 180.0}, str(m))


def test_buyuk_esit_kucuk_sentetik():
    """firma_ozet_toplam_borc_ve_geciken_ay kısmi ayda kalan kullanır = fo_cache."""
    from routes import giris_routes as gr

    payload = {
        "aylar": [
            {
                "ay_key": "2024-1",
                "yil": 2024,
                "ay": 1,
                "tutar_kdv_dahil": 1000.0,
                "brut_tutar_kdv": 1000.0,
                "odenen_tutar_kdv": 1000.0,
                "kalan_tutar_kdv": 0.0,
                "tahsil_edildi": True,
                "kismi_tahsilat": False,
            },
            {
                "ay_key": "2024-2",
                "yil": 2024,
                "ay": 2,
                "tutar_kdv_dahil": 1000.0,
                "brut_tutar_kdv": 1000.0,
                "odenen_tutar_kdv": 400.0,
                "kalan_tutar_kdv": 600.0,
                "tahsil_edildi": False,
                "kismi_tahsilat": True,
            },
            {
                "ay_key": "2024-3",
                "yil": 2024,
                "ay": 3,
                "tutar_kdv_dahil": 1000.0,
                "brut_tutar_kdv": 1000.0,
                "odenen_tutar_kdv": 0.0,
                "kalan_tutar_kdv": 1000.0,
                "tahsil_edildi": False,
                "kismi_tahsilat": False,
            },
        ]
    }
    kyc = {
        "sozlesme_tarihi": date(2024, 1, 15),
        "kira_artis_tarihi": date(2024, 1, 15),
        "sozlesme_bitis": date(2026, 12, 31),
        "aylik_kira": 833.33,
        "kdv_oran": 20,
        "kira_nakit": False,
        "kapanis_tarihi": None,
        "kapanis_sonrasi_borc_ay": None,
        "musteri_durum": "aktif",
    }

    def fake_read(mid, mem_ttl=12.0):
        return payload

    def fake_hucre(mid, y, m, *a, **k):
        return 1000.0

    with patch.object(gr, "_read_aylik_grid_cache_payload", side_effect=fake_read), \
         patch.object(gr, "firma_ozet_aylik_grid_hucre_kdv_dahil", side_effect=fake_hucre), \
         patch.object(gr, "_reel_ay_key_tutar_map_musteri", return_value={}), \
         patch.object(gr, "_build_aylik_grid_cache_payload", return_value=payload), \
         patch.object(gr, "_firma_ozet_cache_payload_usable", return_value=True), \
         patch.object(gr, "_firma_ozet_payload_is_placeholder_dominated", return_value=False):
        fo_c = gr._firma_ozet_ozet_from_grid_cache_payload(payload, 2024, 3, kyc)
        tb, ga = gr.firma_ozet_toplam_borc_ve_geciken_ay(
            90001, 2024, 3, {}, kyc, {}
        )
    check("fo_cache 1600", fo_c is not None and abs(float(fo_c["toplam_borc"]) - 1600.0) < 0.02, str(fo_c))
    check("buyuk=1600", abs(tb - 1600.0) < 0.02, str(tb))
    check("buyuk ga=2", ga == 2, str(ga))
    check(
        "buyuk=fo_cache",
        fo_c is not None and abs(tb - float(fo_c["toplam_borc"])) < 0.02,
        str((tb, fo_c)),
    )


def test_326_buyuk_esit_kucuk_canli():
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
    from routes import giris_routes as gr

    mid = 326
    kyc = gr._musteri_kyc_grup_for_aylik_grid(mid)
    pl = gr._read_aylik_grid_cache_payload(mid)
    bug = date.today()
    fo_c = gr._firma_ozet_ozet_from_grid_cache_payload(pl, bug.year, bug.month, kyc)
    if not fo_c:
        check("326 fo_cache var", False)
        return
    tb, ga = gr.firma_ozet_toplam_borc_ve_geciken_ay(
        mid,
        bug.year,
        bug.month,
        gr._tufe_map_by_year_month_cached(),
        kyc,
        gr._musteri_reel_donem_manual_dict_from_db(mid),
    )
    check(
        "326 buyuk=fo_cache",
        abs(float(tb) - float(fo_c["toplam_borc"])) < 0.05,
        f"{tb} vs {fo_c['toplam_borc']}",
    )
    check(
        "326 ga esit",
        int(ga) == int(fo_c.get("geciken_ay") or 0),
        f"{ga} vs {fo_c.get('geciken_ay')}",
    )
    # Kısmi ay içeriyor
    kismi = gr._firma_ozet_kismi_kalan_by_ay_from_payload(pl)
    check("326 kismi ay var", len(kismi) >= 1, str(kismi))


if __name__ == "__main__":
    test_kismi_kalan_helper()
    test_buyuk_esit_kucuk_sentetik()
    test_326_buyuk_esit_kucuk_canli()
    if FAILS:
        print("FAILS", FAILS)
        sys.exit(1)
    print("CASE firma_ozet kismi ay buyuk sayfa ok")
