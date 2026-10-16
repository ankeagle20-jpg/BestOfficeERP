# -*- coding: utf-8 -*-
"""Ekstre ekran varsayılan dönem: aktif/pasif + pasif sonrası uyarı + elle koruma (kaynak)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _patch_kyc(gr, kyc):
    return mock.patch.object(gr, "_musteri_kyc_grup_for_aylik_grid", return_value=dict(kyc))


def test_pasif_kapanis_ayi_dahil():
    """117-benzeri: pasif ay 2023-08 → 2023-01-01 … 2023-08-31."""
    from routes import giris_routes as gr

    kyc = {
        "durum": "pasif",
        "kapanis_tarihi": date(2023, 8, 15),
        "sozlesme_tarihi": date(2022, 12, 7),
        "sozlesme_bitis": date(2025, 1, 1),
        "aylik_kira": 2300.0,
    }
    with _patch_kyc(gr, kyc), mock.patch.object(
        gr, "_ekstre_son_tahsil_marker_ayi", return_value=None
    ), mock.patch.object(gr, "_ekstre_pasif_ay_yedek", return_value=None):
        bas, bit = gr._cari_ekstre_ekran_varsayilan_bas_bit(117, bugun=date(2026, 10, 16))
    check("pasif_bas", bas == date(2023, 1, 1), bas)
    check("pasif_bit", bit == date(2023, 8, 31), bit)


def test_aktif_son_marker_ufuk():
    """9-benzeri: son marker 2027-03 → 2026-01-01 … 2027-03-31 (bugün 2026-10)."""
    from routes import giris_routes as gr

    kyc = {"durum": "aktif", "sozlesme_tarihi": date(2020, 1, 1), "aylik_kira": 1000.0}
    with _patch_kyc(gr, kyc), mock.patch.object(
        gr, "_ekstre_son_tahsil_marker_ayi", return_value=date(2027, 3, 1)
    ):
        bas, bit = gr._cari_ekstre_ekran_varsayilan_bas_bit(9, bugun=date(2026, 10, 16))
    check("aktif_bas", bas == date(2026, 1, 1), bas)
    check("aktif_bit", bit == date(2027, 3, 31), bit)


def test_aktif_odemesiz_bugun_ayi():
    from routes import giris_routes as gr

    kyc = {"durum": "aktif", "sozlesme_tarihi": date(2024, 5, 1), "aylik_kira": 500.0}
    with _patch_kyc(gr, kyc), mock.patch.object(
        gr, "_ekstre_son_tahsil_marker_ayi", return_value=None
    ):
        bas, bit = gr._cari_ekstre_ekran_varsayilan_bas_bit(99, bugun=date(2026, 10, 16))
    check("odemesiz_bas", bas == date(2026, 1, 1), bas)
    check("odemesiz_bit", bit == date(2026, 10, 31), bit)


def test_marker_tavan_12_ay():
    """12+ ay ileri marker tavana kesilir (bugün+12 ay sonu)."""
    from routes import giris_routes as gr

    kyc = {"durum": "aktif", "aylik_kira": 1.0}
    with _patch_kyc(gr, kyc), mock.patch.object(
        gr, "_ekstre_son_tahsil_marker_ayi", return_value=date(2030, 6, 1)
    ):
        bas, bit = gr._cari_ekstre_ekran_varsayilan_bas_bit(1, bugun=date(2026, 10, 16))
    # tavan = 2026-10 + 12 ay = 2027-10 ay sonu
    check("tavan_bit", bit == date(2027, 10, 31), bit)
    check("tavan_bas", bas == date(2026, 1, 1), bas)


def test_pasif_sonrasi_uyari():
    from routes import giris_routes as gr

    kyc = {"durum": "pasif", "kapanis_tarihi": date(2023, 8, 15)}
    tahsil = [
        {
            "id": 1,
            "tutar": 100.0,
            "aciklama": "X|AYLIK_TAH|2023-09-01|",
            "tahsilat_tarihi": date(2023, 9, 5),
            "kaynak": "",
        }
    ]
    with _patch_kyc(gr, kyc), mock.patch.object(
        gr, "_ekstre_tahsil_rows_for_musteri", return_value=tahsil
    ), mock.patch.object(gr, "fetch_one", return_value=None):
        ok_var = gr._ekstre_pasif_sonrasi_kayit_var(117, kyc)
    check("pasif_sonrasi_var", ok_var is True)

    tahsil2 = [
        {
            "id": 2,
            "tutar": 100.0,
            "aciklama": "X|AYLIK_TAH|2023-08-01|",
            "tahsilat_tarihi": date(2023, 8, 10),
            "kaynak": "",
        }
    ]
    with _patch_kyc(gr, kyc), mock.patch.object(
        gr, "_ekstre_tahsil_rows_for_musteri", return_value=tahsil2
    ), mock.patch.object(gr, "fetch_one", return_value=None):
        ok_yok = gr._ekstre_pasif_sonrasi_kayit_var(117, kyc)
    check("pasif_ayi_dahil_uyari_yok", ok_yok is False)


def test_api_eski_varsayilan_dokunulmadi():
    """Excel/API boş-tarih yedeği eski fonksiyonda kalsın (imza/doc)."""
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check(
        "ekran_fn_ayri",
        "def _cari_ekstre_ekran_varsayilan_bas_bit" in src,
    )
    check(
        "eski_fn_duruyor",
        "def _cari_ekstre_varsayilan_bas_bit" in src,
    )
    # build_payload hâlâ eski yedeği kullanır
    i = src.find("def _cari_ekstre_build_payload_from_request")
    body = src[i : i + 1200]
    check("payload_eski_fn", "_cari_ekstre_varsayilan_bas_bit(" in body)
    check("payload_ekran_fn_yok", "_cari_ekstre_ekran_varsayilan_bas_bit(" not in body)
    # musteri GET ekran fn
    check("musteri_ekran", "ekstre_varsayilan_bas" in src and "_cari_ekstre_ekran_varsayilan_bas_bit" in src)


def test_fe_elle_koruma_ve_uyari():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("fe_uyari_fn", "function girisCariEkstrePasifSonrasiUyariGoster" in html)
    check("fe_uyari_el", "cari_ekstre_pasif_sonrasi_uyari" in html)
    check("fe_uyari_metin", "Pasife alma sonrası kayıt var; dönemi genişletin." in html)
    check("fe_dokundu_reset", "__cariEkstreBitisKullaniciDokundu = false" in html)
    check(
        "fe_bas_manuel",
        "onFormAlan('cari_ekstre_baslangic', { inputToo: true, ekstreBitisManuel: true })" in html,
    )
    # Elle dokununca otomatik varsayılan iptal
    check("fe_dokundu_gate", "__cariEkstreBitisKullaniciDokundu" in html and "otomatikVarsayilan = false" in html)


def test_devreden_fn_degismedi():
    """Devreden bakiye yardımcıları imza olarak duruyor (davranış bu pakette değişmez)."""
    from routes import giris_routes as gr

    check("dev_fifo", callable(gr._ekstre_devreden_satir_toplamlari))
    check("dev_legacy", callable(gr._ekstre_devreden_toplamlari))


if __name__ == "__main__":
    for fn in (
        test_pasif_kapanis_ayi_dahil,
        test_aktif_son_marker_ufuk,
        test_aktif_odemesiz_bugun_ayi,
        test_marker_tavan_12_ay,
        test_pasif_sonrasi_uyari,
        test_api_eski_varsayilan_dokunulmadi,
        test_fe_elle_koruma_ve_uyari,
        test_devreden_fn_degismedi,
    ):
        print("---", fn.__name__)
        fn()
    if FAILS:
        print("FAILED:", FAILS)
        sys.exit(1)
    print("ALL_OK")
