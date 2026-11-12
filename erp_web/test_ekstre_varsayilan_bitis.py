# -*- coding: utf-8 -*-
"""Ekstre varsayilan bitis: max(taban ay, odenen en son ay) ay sonu.

Temiz ortam birim testleri. Prod yazma yok.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_")):
        os.environ.pop(k, None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _patch_kyc(gr, kyc):
    return mock.patch.object(gr, "_musteri_kyc_grup_for_aylik_grid", return_value=dict(kyc))


def test_odenen_en_son_ay_kaynak():
    from routes import giris_routes as gr

    with mock.patch.object(gr, "_ekstre_son_tahsil_marker_ayi", return_value=date(2026, 6, 1)), mock.patch.object(
        gr, "_ekstre_son_tahsilat_hareket_tarihi", return_value=date(2026, 8, 15)
    ):
        ay = gr._ekstre_odenen_en_son_ay(1)
    check("odenen_max_tahsil_ayi", ay == date(2026, 8, 1), ay)

    with mock.patch.object(gr, "_ekstre_son_tahsil_marker_ayi", return_value=date(2027, 3, 1)), mock.patch.object(
        gr, "_ekstre_son_tahsilat_hareket_tarihi", return_value=date(2026, 1, 5)
    ):
        ay2 = gr._ekstre_odenen_en_son_ay(1)
    check("odenen_max_marker_ileri", ay2 == date(2027, 3, 1), ay2)


def test_aktif_guncel_ve_geride():
    from routes import giris_routes as gr

    kyc = {"durum": "aktif", "sozlesme_tarihi": date(2024, 1, 1)}
    # odeme geride → bit = icinde bulunulan ay sonu
    with _patch_kyc(gr, kyc), mock.patch.object(gr, "_ekstre_odenen_en_son_ay", return_value=date(2026, 5, 1)):
        _b, bit = gr._cari_ekstre_ekran_varsayilan_bas_bit(10, bugun=date(2026, 11, 12))
    check("aktif_geride_bit", bit == date(2026, 11, 30), bit)
    # odeme guncel ayni ay
    with _patch_kyc(gr, kyc), mock.patch.object(gr, "_ekstre_odenen_en_son_ay", return_value=date(2026, 11, 1)):
        _b2, bit2 = gr._cari_ekstre_ekran_varsayilan_bas_bit(10, bugun=date(2026, 11, 12))
    check("aktif_guncel_bit", bit2 == date(2026, 11, 30), bit2)


def test_aktif_ileri_pesin():
    from routes import giris_routes as gr

    kyc = {"durum": "aktif"}
    with _patch_kyc(gr, kyc), mock.patch.object(gr, "_ekstre_odenen_en_son_ay", return_value=date(2027, 2, 1)):
        _b, bit = gr._cari_ekstre_ekran_varsayilan_bas_bit(11, bugun=date(2026, 11, 12))
    check("aktif_pesin_bit", bit == date(2027, 2, 28), bit)


def test_pasif_odenen_uzat():
    """Pasif taban son borc; odenen daha ileriyse bit uzar (bas taban yilinda kalir)."""
    from routes import giris_routes as gr

    kyc = {
        "durum": "pasif",
        "kapanis_tarihi": date(2024, 12, 10),
        "kapanis_sonrasi_borc_ay": 0,
        "sozlesme_tarihi": date(2023, 1, 1),
    }
    with _patch_kyc(gr, kyc), mock.patch.object(
        gr, "_ekstre_odenen_en_son_ay", return_value=date(2025, 1, 1)
    ):
        bas, bit = gr._cari_ekstre_ekran_varsayilan_bas_bit(319, bugun=date(2026, 11, 12))
    check("pasif_bas_taban_yil", bas == date(2024, 1, 1), bas)
    check("pasif_bit_odenen", bit == date(2025, 1, 31), bit)


def test_pasif_hepsi_gorunum():
    from routes import giris_routes as gr
    from pasif_donem_satir_gorunum import gorunum_son_borc_ayi

    kyc = {
        "durum": "pasif",
        "kapanis_tarihi": date(2023, 6, 15),
        "kapanis_sonrasi_borc_ay": None,
        "sozlesme_tarihi": date(2022, 12, 1),
    }
    son = gorunum_son_borc_ayi(kyc)
    check("hepsi_son_var", isinstance(son, date), son)
    with _patch_kyc(gr, kyc), mock.patch.object(gr, "_ekstre_odenen_en_son_ay", return_value=None):
        _bas, bit = gr._cari_ekstre_ekran_varsayilan_bas_bit(317, bugun=date(2026, 11, 12))
    check("hepsi_bit_taban", bit == gr._ekstre_ay_sonu(son.year, son.month), bit)
    with _patch_kyc(gr, kyc), mock.patch.object(
        gr, "_ekstre_odenen_en_son_ay", return_value=date(2024, 11, 1)
    ):
        _bas2, bit2 = gr._cari_ekstre_ekran_varsayilan_bas_bit(317, bugun=date(2026, 11, 12))
    check("hepsi_bit_odenen", bit2 == date(2024, 11, 30), bit2)


def test_ay_sonu_uc():
    from routes import giris_routes as gr

    kyc = {"durum": "aktif"}
    # Aralik
    with _patch_kyc(gr, kyc), mock.patch.object(gr, "_ekstre_odenen_en_son_ay", return_value=None):
        _b, bit = gr._cari_ekstre_ekran_varsayilan_bas_bit(1, bugun=date(2026, 12, 5))
    check("ara_bit", bit == date(2026, 12, 31), bit)
    # Ocak
    with _patch_kyc(gr, kyc), mock.patch.object(gr, "_ekstre_odenen_en_son_ay", return_value=None):
        _b2, bit2 = gr._cari_ekstre_ekran_varsayilan_bas_bit(1, bugun=date(2027, 1, 3))
    check("oca_bit", bit2 == date(2027, 1, 31), bit2)
    # Subat 28
    with _patch_kyc(gr, kyc), mock.patch.object(gr, "_ekstre_odenen_en_son_ay", return_value=date(2026, 2, 1)):
        _b3, bit3 = gr._cari_ekstre_ekran_varsayilan_bas_bit(1, bugun=date(2026, 1, 10))
    check("sub28", bit3 == date(2026, 2, 28), bit3)
    # Subat 29 artik yil
    with _patch_kyc(gr, kyc), mock.patch.object(gr, "_ekstre_odenen_en_son_ay", return_value=date(2024, 2, 1)):
        _b4, bit4 = gr._cari_ekstre_ekran_varsayilan_bas_bit(1, bugun=date(2024, 1, 10))
    check("sub29", bit4 == date(2024, 2, 29), bit4)


def test_elle_ezilmez_fe():
    idx = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    for src, lab in ((idx, "idx"), (tjs, "tjs")):
        i = src.find("function girisCariEkstreVarsayilanTarihleriYaz")
        j = src.find("function ", i + 10)
        chunk = src[i:j] if i >= 0 and j > i else ""
        check(f"{lab}_elle_gate", "__cariEkstreBitisKullaniciDokundu" in chunk and "if (!elle)" in chunk)
        check(f"{lab}_reset_yok", "__cariEkstreBitisKullaniciDokundu = false" not in chunk)
        check(f"{lab}_literal_bs_n", (chr(92) + "n") not in chunk)


def test_api_yedek_dokunulmadi():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    i = src.find("def _cari_ekstre_build_payload_from_request")
    body = src[i : i + 1400]
    check("payload_eski", "_cari_ekstre_varsayilan_bas_bit(" in body)
    check("payload_ekran_yok", "_cari_ekstre_ekran_varsayilan_bas_bit(" not in body)
    check("odenen_fn", "def _ekstre_odenen_en_son_ay" in src)


def test_mutasyon():
    """Kural kaldirilinca (odenen max yok) test FAIL etmeli."""
    from routes import giris_routes as gr

    kyc = {
        "durum": "pasif",
        "kapanis_tarihi": date(2024, 12, 10),
        "kapanis_sonrasi_borc_ay": 0,
        "sozlesme_tarihi": date(2023, 1, 1),
    }
    with _patch_kyc(gr, kyc), mock.patch.object(
        gr, "_ekstre_odenen_en_son_ay", return_value=date(2025, 1, 1)
    ):
        _bas, bit_good = gr._cari_ekstre_ekran_varsayilan_bas_bit(319, bugun=date(2026, 11, 12))
    check("mut_iyi", bit_good == date(2025, 1, 31), bit_good)
    # mutasyon: odenen yok say → eski taban
    with _patch_kyc(gr, kyc), mock.patch.object(gr, "_ekstre_odenen_en_son_ay", return_value=None):
        _bas2, bit_bad = gr._cari_ekstre_ekran_varsayilan_bas_bit(319, bugun=date(2026, 11, 12))
    check("mut_kotu_taban", bit_bad == date(2024, 12, 31), bit_bad)
    check("mutasyon_kapi", bit_good != bit_bad and bit_good > bit_bad)


def main():
    test_odenen_en_son_ay_kaynak()
    test_aktif_guncel_ve_geride()
    test_aktif_ileri_pesin()
    test_pasif_odenen_uzat()
    test_pasif_hepsi_gorunum()
    test_ay_sonu_uc()
    test_elle_ezilmez_fe()
    test_api_yedek_dokunulmadi()
    test_mutasyon()
    if FAILS:
        print("FAIL", len(FAILS))
        for a in FAILS:
            print(" -", a)
        raise SystemExit(1)
    print("test_ekstre_varsayilan_bitis PASS")


if __name__ == "__main__":
    main()
