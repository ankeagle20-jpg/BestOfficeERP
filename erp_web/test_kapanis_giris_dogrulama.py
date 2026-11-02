# -*- coding: utf-8 -*-
"""kapanis_tarihi < giris tarihi doğrulaması."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek="") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_1_kapanis_once_red():
    from musteri_durum_yaz import dogrula_kapanis_giris

    msg = dogrula_kapanis_giris(date(2021, 6, 17), date(2021, 6, 25)) or ""
    check("1_red", bool(msg))
    check("1_tr_tarih", "25.06.2021" in msg)
    check("1_metin", "Kapanış tarihi, giriş tarihinden önce olamaz" in msg)


def test_2_esit_kabul():
    from musteri_durum_yaz import dogrula_kapanis_giris

    check("2_esit", dogrula_kapanis_giris(date(2022, 1, 1), date(2022, 1, 1)) is None)
    check("2_sonra", dogrula_kapanis_giris(date(2022, 2, 1), date(2022, 1, 1)) is None)


def test_3_kapanis_bos_kabul():
    from musteri_durum_yaz import dogrula_kapanis_giris

    check("3_none", dogrula_kapanis_giris(None, date(2022, 1, 1)) is None)
    check("3_giris_yok", dogrula_kapanis_giris(date(2022, 1, 1), None) is None)


def test_4_giris_duzenleme_red():
    """Giriş ileri alınırsa mevcut kapanış girişten önce kalır → red (KYC yolu sim)."""
    from musteri_durum_yaz import dogrula_kapanis_giris, resolve_giris_tarihi

    kap = date(2024, 5, 9)
    soz_yeni = date(2024, 9, 5)
    giris = resolve_giris_tarihi(soz_yeni, date(2024, 1, 1))
    check("4_giris_soz", giris == soz_yeni)
    check("4_red", dogrula_kapanis_giris(kap, giris) is not None)


def test_5_rent_start_esas():
    from musteri_durum_yaz import resolve_giris_tarihi, dogrula_kapanis_giris

    g = resolve_giris_tarihi(None, date(2022, 12, 12))
    check("5_rent", g == date(2022, 12, 12))
    check(
        "5_red",
        dogrula_kapanis_giris(date(2021, 2, 19), g) is not None,
    )
    g2 = resolve_giris_tarihi(date(2020, 1, 1), date(2022, 12, 12))
    check("5_soz_oncelik", g2 == date(2020, 1, 1))


def test_6_aktife_alma_bozulmaz():
    """Aktif yazımında kapanış None → doğrulama yok; durum alanları temizler."""
    from musteri_durum_yaz import (
        dogrula_kapanis_giris,
        musteri_durum_yaz_alanlari,
    )

    alanlar = musteri_durum_yaz_alanlari(
        {"durum": "aktif", "kapanis_tarihi": "2021-01-01"},
        kati=True,
        oda_bosalt=True,
    )
    check("6_kap_none", alanlar["kapanis_tarihi"] is None)
    check("6_aktif", alanlar["durum"] == "aktif")
    check(
        "6_dogrula_yok",
        dogrula_kapanis_giris(alanlar["kapanis_tarihi"], date(2025, 1, 1)) is None,
    )

    # API: durum aktif → dogrula çağrılsa bile kap None
    from routes import giris_routes as gr

    with mock.patch.object(gr, "musteri_giris_tarihi_db", return_value=date(2025, 8, 1)), mock.patch.object(
        gr, "fetch_one", return_value={"id": 1}
    ), mock.patch.object(gr, "execute", return_value=1), mock.patch.object(
        gr, "ensure_customers_durum"
    ), mock.patch.object(gr, "ensure_customers_is_active"), mock.patch.object(
        gr, "ensure_customers_kapanis_sonrasi_borc_ay"
    ), mock.patch.object(gr, "ensure_customers_hazir_ofis_oda"), mock.patch.object(
        gr, "kapanis_borc_ufku_degisti"
    ), mock.patch.object(gr, "giris_gerekli", lambda f: f), mock.patch(
        "flask_login.utils._get_user", return_value=mock.Mock(is_authenticated=True, id=1)
    ):
        client = gr.bp
        # doğrudan fonksiyon
        from flask import Flask

        app = Flask("t")
        app.secret_key = "t"
        app.register_blueprint(gr.bp, url_prefix="/giris")
        with app.test_request_context(
            "/giris/api/musteri/1/durum",
            method="POST",
            json={"durum": "aktif"},
        ):
            resp = gr.api_musteri_durum_guncelle(1)
            data = resp.get_json() if hasattr(resp, "get_json") else resp[0].get_json()
            status = resp[1] if isinstance(resp, tuple) else getattr(resp, "status_code", 200)
            if isinstance(resp, tuple):
                status = resp[1]
                data = resp[0].get_json()
            else:
                status = resp.status_code
                data = resp.get_json()
    check("6_api_ok", status == 200 and data.get("ok") is True, (status, data))


def test_7_okuma_bozulmaz():
    """Mevcut hatalı kayıt: dogrula yalnız yazmada; okuma yardımcıları tarihleri olduğu gibi döner."""
    from musteri_durum_yaz import musteri_giris_tarihi_db, musteri_kapanis_tarihi_db

    with mock.patch("db.fetch_one") as fo:
        fo.side_effect = [
            {"rent_start": date(2022, 12, 12), "sozlesme_tarihi": None},
            {"kap": date(2021, 2, 19)},
        ]
        g = musteri_giris_tarihi_db(339)
        k = musteri_kapanis_tarihi_db(339)
    check("7_giris", g == date(2022, 12, 12))
    check("7_kap", k == date(2021, 2, 19))
    check("7_once", k < g)


def test_api_pasif_red():
    from routes import giris_routes as gr
    from flask import Flask
    from flask_login import LoginManager

    app = Flask("t2")
    app.secret_key = "t"
    LoginManager(app)
    app.register_blueprint(gr.bp, url_prefix="/giris")
    fn = getattr(gr.api_musteri_durum_guncelle, "__wrapped__", gr.api_musteri_durum_guncelle)
    with mock.patch.object(
        gr, "musteri_giris_tarihi_db", return_value=date(2022, 12, 12)
    ), mock.patch.object(gr, "fetch_one", return_value={"id": 339}), mock.patch.object(
        gr, "execute", return_value=1
    ), mock.patch.object(gr, "ensure_customers_durum"), mock.patch.object(
        gr, "ensure_customers_is_active"
    ), mock.patch.object(gr, "ensure_customers_kapanis_sonrasi_borc_ay"), mock.patch.object(
        gr, "ensure_customers_hazir_ofis_oda"
    ):
        with app.test_request_context(
            "/giris/api/musteri/339/durum",
            method="POST",
            json={"durum": "pasif", "kapanis_tarihi": "2021-02-19"},
        ):
            resp = fn(339)
            if isinstance(resp, tuple):
                status, data = resp[1], resp[0].get_json()
            else:
                status, data = resp.status_code, resp.get_json()
    check("api_red_400", status == 400, status)
    check("api_red_mesaj", "Giriş" in (data.get("mesaj") or "") or "giriş" in (data.get("mesaj") or ""))


if __name__ == "__main__":
    test_1_kapanis_once_red()
    test_2_esit_kabul()
    test_3_kapanis_bos_kabul()
    test_4_giris_duzenleme_red()
    test_5_rent_start_esas()
    test_6_aktife_alma_bozulmaz()
    test_7_okuma_bozulmaz()
    test_api_pasif_red()
    if FAILS:
        print("FAIL", FAILS)
        raise SystemExit(1)
    print("ALL_OK")
