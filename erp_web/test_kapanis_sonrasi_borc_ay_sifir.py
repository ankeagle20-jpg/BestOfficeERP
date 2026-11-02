# -*- coding: utf-8 -*-
"""N=0 (kapanış sonrası borç ayı) ≠ Hepsi/NULL — yazma + okuma + cap.

Prod yazma yok; sahte DB / birim.
"""
from __future__ import annotations

import os
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

os.environ.pop("ALLOW_PROD_WRITE", None)

ROOT = Path(__file__).resolve().parent
FAILS: list[str] = []


def check(name: str, ok: bool, ek="") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_1_normalize_ve_yaz_alanlari():
    from musteri_durum_yaz import (
        musteri_durum_yaz_alanlari,
        musteri_durum_yaz_json,
        normalize_kapanis_sonrasi_borc_ay,
    )

    for raw in (0, "0", 0.0, Decimal(0)):
        check(
            "norm0_%s" % type(raw).__name__,
            normalize_kapanis_sonrasi_borc_ay(
                {"kapanis_sonrasi_borc_ay": raw, "durum": "pasif"}, "pasif"
            )
            == 0,
        )
    check(
        "norm_hepsi_bos",
        normalize_kapanis_sonrasi_borc_ay({"kapanis_sonrasi_borc_ay": "", "durum": "pasif"}, "pasif")
        is None,
    )
    check(
        "norm_hepsi_none",
        normalize_kapanis_sonrasi_borc_ay({"kapanis_sonrasi_borc_ay": None, "durum": "pasif"}, "pasif")
        is None,
    )
    for n in range(1, 13):
        check(
            "norm_%d" % n,
            normalize_kapanis_sonrasi_borc_ay(
                {"kapanis_sonrasi_borc_ay": n, "durum": "pasif"}, "pasif"
            )
            == n,
        )
    a0 = musteri_durum_yaz_alanlari(
        {"durum": "pasif", "kapanis_tarihi": "2024-09-09", "kapanis_sonrasi_borc_ay": 0},
        kati=True,
    )
    check("alan_0", a0["kapanis_sonrasi_borc_ay"] == 0)
    j0 = musteri_durum_yaz_json(a0)
    check("json_0_int", j0["kapanis_sonrasi_borc_ay"] == 0 and j0["kapanis_sonrasi_borc_ay"] is not None)
    a_h = musteri_durum_yaz_alanlari(
        {"durum": "pasif", "kapanis_tarihi": "2024-09-09", "kapanis_sonrasi_borc_ay": ""},
        kati=True,
    )
    check("alan_hepsi", a_h["kapanis_sonrasi_borc_ay"] is None)


def test_2_api_durum_0_sql_param():
    """POST /durum 0 gönderince execute parametresi 0 (NULL değil)."""
    from routes import giris_routes as gr
    from flask import Flask
    from flask_login import LoginManager

    app = Flask("n0")
    app.secret_key = "t"
    LoginManager(app)
    app.register_blueprint(gr.bp, url_prefix="/giris")
    fn = getattr(gr.api_musteri_durum_guncelle, "__wrapped__", gr.api_musteri_durum_guncelle)
    captured = {}

    def _exec(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    with mock.patch.object(gr, "musteri_giris_tarihi_db", return_value=date(2022, 7, 7)), mock.patch.object(
        gr, "fetch_one", return_value={"id": 371}
    ), mock.patch.object(gr, "execute", side_effect=_exec), mock.patch.object(
        gr, "ensure_customers_durum"
    ), mock.patch.object(gr, "ensure_customers_is_active"), mock.patch.object(
        gr, "ensure_customers_kapanis_sonrasi_borc_ay"
    ), mock.patch.object(gr, "ensure_customers_hazir_ofis_oda"), mock.patch.object(
        gr, "kapanis_borc_ufku_degisti", return_value=True
    ):
        with app.test_request_context(
            "/giris/api/musteri/371/durum",
            method="POST",
            json={
                "durum": "pasif",
                "kapanis_tarihi": "2024-09-09",
                "kapanis_sonrasi_borc_ay": 0,
            },
        ):
            resp = fn(371)
            if isinstance(resp, tuple):
                status, data = resp[1], resp[0].get_json()
            else:
                status, data = resp.status_code, resp.get_json()
    params = captured.get("params") or ()
    # durum, kap, N, is_active, oda_sil, mid
    check("api0_status", status == 200 and data.get("ok") is True, (status, data))
    check("api0_param_N", len(params) >= 3 and params[2] == 0, params)
    check("api0_json_N", data.get("kapanis_sonrasi_borc_ay") == 0, data)

    captured.clear()
    with mock.patch.object(gr, "musteri_giris_tarihi_db", return_value=date(2022, 7, 7)), mock.patch.object(
        gr, "fetch_one", return_value={"id": 371}
    ), mock.patch.object(gr, "execute", side_effect=_exec), mock.patch.object(
        gr, "ensure_customers_durum"
    ), mock.patch.object(gr, "ensure_customers_is_active"), mock.patch.object(
        gr, "ensure_customers_kapanis_sonrasi_borc_ay"
    ), mock.patch.object(gr, "ensure_customers_hazir_ofis_oda"), mock.patch.object(
        gr, "kapanis_borc_ufku_degisti", return_value=True
    ):
        with app.test_request_context(
            "/giris/api/musteri/371/durum",
            method="POST",
            json={"durum": "pasif", "kapanis_tarihi": "2024-09-09", "kapanis_sonrasi_borc_ay": ""},
        ):
            resp = fn(371)
            if isinstance(resp, tuple):
                status, data = resp[1], resp[0].get_json()
            else:
                status, data = resp.status_code, resp.get_json()
    params = captured.get("params") or ()
    check("api_hepsi_status", status == 200 and data.get("ok") is True, (status, data))
    check("api_hepsi_param_N", len(params) >= 3 and params[2] is None, params)
    check("api_hepsi_json_N", data.get("kapanis_sonrasi_borc_ay") is None, data)

    for n in (1, 6, 12):
        captured.clear()
        with mock.patch.object(
            gr, "musteri_giris_tarihi_db", return_value=date(2022, 7, 7)
        ), mock.patch.object(gr, "fetch_one", return_value={"id": 371}), mock.patch.object(
            gr, "execute", side_effect=_exec
        ), mock.patch.object(gr, "ensure_customers_durum"), mock.patch.object(
            gr, "ensure_customers_is_active"
        ), mock.patch.object(gr, "ensure_customers_kapanis_sonrasi_borc_ay"), mock.patch.object(
            gr, "ensure_customers_hazir_ofis_oda"
        ), mock.patch.object(gr, "kapanis_borc_ufku_degisti", return_value=True):
            with app.test_request_context(
                "/giris/api/musteri/371/durum",
                method="POST",
                json={
                    "durum": "pasif",
                    "kapanis_tarihi": "2024-09-09",
                    "kapanis_sonrasi_borc_ay": n,
                },
            ):
                resp = fn(371)
                if isinstance(resp, tuple):
                    status, data = resp[1], resp[0].get_json()
                else:
                    status, data = resp.status_code, resp.get_json()
        params = captured.get("params") or ()
        check("api_%d_param" % n, params[2] == n and data.get("kapanis_sonrasi_borc_ay") == n, params)


def test_3_serialize_okuma_0_hepsi():
    """api_musteri_detay yolu: DB 0 → '0'; NULL → '' (Hepsi)."""
    from routes.giris_routes import _musteri_serialize_val

    check("ser_0_int", _musteri_serialize_val(0) == "0")
    check("ser_0_dec", _musteri_serialize_val(Decimal(0)) == "0")
    check("ser_0_str", _musteri_serialize_val("0") == "0")
    check("ser_none", _musteri_serialize_val(None) == "")
    check("ser_bos", _musteri_serialize_val("") == "")
    check("ser_1", _musteri_serialize_val(1) == "1")
    check("ser_12", _musteri_serialize_val(12) == "12")
    check("ser_false_not_zero", _musteri_serialize_val(False) == "")
    # simüle detay out döngüsü
    row = {"kapanis_sonrasi_borc_ay": 0, "durum": "pasif", "kapanis_tarihi": date(2024, 9, 9)}
    out = {k: _musteri_serialize_val(v) for k, v in row.items()}
    check("detay_out_0", out["kapanis_sonrasi_borc_ay"] == "0")
    row_n = {"kapanis_sonrasi_borc_ay": None, "durum": "pasif"}
    out_n = {k: _musteri_serialize_val(v) for k, v in row_n.items()}
    check("detay_out_hepsi", out_n["kapanis_sonrasi_borc_ay"] == "")


def test_4_js_render_mantigi():
    """_t.js / musteri_durum.js: 0 → '0', null/'' → Hepsi ('')."""
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    mjs = (ROOT / "static" / "js" / "musteri_durum.js").read_text(encoding="utf-8")
    check(
        "js_deg_fn",
        "function kapanisSonrasiBorcAyDegeri(raw)" in tjs
        and "n >= 0 && n <= 12" in tjs,
    )
    check(
        "js_sync_0",
        re.search(
            r"aySel\.value\s*=\s*\(kapAy\s*!==\s*null\s*&&\s*kapAy\s*!==\s*undefined\)\s*\?\s*String\(kapAy\)\s*:\s*''",
            tjs,
        )
        is not None,
    )
    check(
        "js_kaydet_0",
        "kapBorcAy !== null && kapBorcAy !== undefined" in tjs,
    )
    check(
        "js_ay_yaz_0",
        'aySel.value = "0"' in mjs or "aySel.value = '0'" in mjs,
    )
    # dropdown option value=0 mevcut
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check('html_opt_0', 'value="0"' in html and "Hepsi" in html)
    check('html_opt_hepsi', re.search(r'<option value="">\s*Hepsi\s*</option>', html) is not None)


def test_5_tum_yazma_yollari():
    """durum / kaydet / kyc helper 0 kaybetmez; kaynak satırları doğrula."""
    from musteri_durum_yaz import musteri_durum_yaz_alanlari

    payload = {
        "durum": "pasif",
        "kapanis_tarihi": "2024-09-09",
        "kapanis_sonrasi_borc_ay": 0,
    }
    for kwargs in (
        {"kati": True, "oda_bosalt": True},
        {"kati": False, "oda_bosalt": False},
    ):
        a = musteri_durum_yaz_alanlari(payload, **kwargs)
        check("yol_%s" % kwargs["kati"], a["kapanis_sonrasi_borc_ay"] == 0)

    gr_src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    mr_src = (ROOT / "routes" / "musteri_routes.py").read_text(encoding="utf-8")
    check(
        "durum_ucu_helper",
        "alanlar = musteri_durum_yaz_alanlari(data, kati=True, oda_bosalt=True)" in gr_src,
    )
    check(
        "kaydet_helper",
        "alanlar = musteri_durum_yaz_alanlari(data, kati=False, oda_bosalt=False)" in gr_src,
    )
    check(
        "kyc_helper",
        "alanlar = musteri_durum_yaz_alanlari(data, kati=False, oda_bosalt=False)" in mr_src,
    )
    check(
        "kaydet_sql_N",
        "kapanis_sonrasi_borc_ay = %s" in gr_src and "kap_borc_ay" in gr_src,
    )
    check(
        "kyc_sql_N",
        "alanlar[\"kapanis_sonrasi_borc_ay\"]" in mr_src
        or "alanlar['kapanis_sonrasi_borc_ay']" in mr_src,
    )
    # serialize 0 koruması (kök neden düzeltmesi)
    check(
        "ser_fix_yorum",
        "0 korunur" in gr_src[gr_src.find("def _musteri_serialize_val") :][
            : gr_src.find("def _musteri_serialize_val") + 500
        ]
        or "v == 0" in gr_src[gr_src.find("def _musteri_serialize_val") : gr_src.find("def _musteri_serialize_val") + 600],
    )


def test_6_cap_n0_vs_hepsi():
    from kira_kapanis_cap import kira_son_borc_ayi, hepsi_sozlesme_yili_aktif

    kap = date(2024, 9, 9)
    giris = date(2022, 7, 7)
    base = {"durum": "pasif", "kapanis_tarihi": kap, "rent_start_date": giris, "sozlesme_tarihi": giris}
    son0 = kira_son_borc_ayi(dict(base, kapanis_sonrasi_borc_ay=0))
    check("cap_n0", son0 == date(2024, 9, 1), son0)

    os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
    try:
        check("flag_on", hepsi_sozlesme_yili_aktif() is True)
        son_h = kira_son_borc_ayi(dict(base, kapanis_sonrasi_borc_ay=None))
        # giriş Temmuz → yıl sonu Haziran; kap Eyl-2024 → Haz-2025
        check("cap_hepsi_b2", son_h == date(2025, 6, 1), son_h)
        # N=0 bayrak açıkken hâlâ kapanış ayı (Hepsi değil)
        son0b = kira_son_borc_ayi(dict(base, kapanis_sonrasi_borc_ay=0))
        check("cap_n0_flag_on", son0b == date(2024, 9, 1), son0b)
    finally:
        os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)

    os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
    son_null_off = kira_son_borc_ayi(dict(base, kapanis_sonrasi_borc_ay=None))
    check("cap_hepsi_flag_off", son_null_off is None, son_null_off)


def main():
    test_1_normalize_ve_yaz_alanlari()
    test_2_api_durum_0_sql_param()
    test_3_serialize_okuma_0_hepsi()
    test_4_js_render_mantigi()
    test_5_tum_yazma_yollari()
    test_6_cap_n0_vs_hepsi()
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("ALL_OK test_kapanis_sonrasi_borc_ay_sifir")


if __name__ == "__main__":
    main()
