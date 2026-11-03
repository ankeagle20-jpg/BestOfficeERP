# -*- coding: utf-8 -*-
"""Müşteri durum=İptal: yazma, cap, tahsilat koruması, UI/GET. Canlı yazma yok."""
from __future__ import annotations

import importlib
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
    print(
        ("ok " if ok else "FAIL ")
        + name
        + ((" :: " + str(ek)[:280]) if (ek and not ok) else "")
    )
    if not ok:
        FAILS.append(name)


def test_a_yaz_alanlari_iptal():
    from musteri_durum_yaz import (
        iptal_icin_kapanis_giris_ayarla,
        musteri_durum_yaz_alanlari,
        musteri_durum_yaz_json,
    )

    a = musteri_durum_yaz_alanlari(
        {"durum": "iptal", "kapanis_tarihi": "2025-01-01", "kapanis_sonrasi_borc_ay": 3},
        kati=True,
    )
    check("a_durum_pasif", a["durum"] == "pasif")
    check("a_iptal_true", a["iptal"] is True)
    check("a_N0", a["kapanis_sonrasi_borc_ay"] == 0)
    check("a_ui_iptal", a["ui_durum"] == "iptal")
    check("a_is_active_false", a["is_active"] is False)
    err = iptal_icin_kapanis_giris_ayarla(a, date(2023, 4, 10))
    check("a_kap_ayarla_ok", err is None and a["kapanis_tarihi"] == date(2023, 4, 10))
    j = musteri_durum_yaz_json(a)
    check("a_json_durum_iptal", j["durum"] == "iptal" and j["iptal"] is True)

    err2 = iptal_icin_kapanis_giris_ayarla(dict(a), None)
    check("a_giris_yok_hata", isinstance(err2, str) and "giriş" in err2.lower())

    aktif = musteri_durum_yaz_alanlari({"durum": "aktif"}, kati=True)
    check("a_aktif_iptal_false", aktif["iptal"] is False and aktif["durum"] == "aktif")
    pasif = musteri_durum_yaz_alanlari(
        {"durum": "pasif", "kapanis_tarihi": "2024-04-09", "kapanis_sonrasi_borc_ay": 0},
        kati=True,
    )
    check("a_pasif_iptal_false", pasif["iptal"] is False and pasif["durum"] == "pasif")


def test_b_tahsilat_korumasi_400():
    from routes import giris_routes as gr
    from flask import Flask
    from flask_login import LoginManager

    app = Flask("iptal_tah")
    app.secret_key = "t"
    LoginManager(app)
    app.register_blueprint(gr.bp, url_prefix="/giris")
    fn = getattr(gr.api_musteri_durum_guncelle, "__wrapped__", gr.api_musteri_durum_guncelle)
    exec_called = {"n": 0}

    def _exec(*_a, **_k):
        exec_called["n"] += 1
        return 1

    with mock.patch.object(gr, "musteri_tahsilat_var_mi", return_value=True), mock.patch.object(
        gr, "execute", side_effect=_exec
    ):
        with app.test_request_context(
            "/giris/api/musteri/101137/durum",
            method="POST",
            json={"durum": "iptal"},
        ):
            resp = fn(101137)
            if isinstance(resp, tuple):
                status, data = resp[1], resp[0].get_json()
            else:
                status, data = resp.status_code, resp.get_json()
    check("b_status_400", status == 400, (status, data))
    check(
        "b_mesaj",
        "tahsilat" in str((data or {}).get("mesaj") or "").lower(),
        data,
    )
    check("b_update_yok", exec_called["n"] == 0)


def test_c_kira_son_borc_ayi_iptal():
    prev_h = os.environ.get("KAPANIS_HEPSI_SOZLESME_YILI")
    prev_n = os.environ.get("KAPANIS_N0_KAPANIS_AYI_HARIC")
    try:
        os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
        os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
        import kira_kapanis_cap as kc

        importlib.reload(kc)
        from kira_kapanis_cap import (
            ay_kira_borc_dahil_mi,
            kapanis_ek_fingerprint,
            kira_son_borc_ayi,
        )

        base = {
            "durum": "pasif",
            "iptal": True,
            "kapanis_tarihi": date(2023, 4, 10),
            "kapanis_sonrasi_borc_ay": 0,
            "sozlesme_tarihi": date(2023, 4, 10),
            "rent_start_date": date(2023, 4, 10),
        }
        son = kira_son_borc_ayi(base)
        check("c_son_flag_off", son == date(2023, 3, 1), son)
        check("c_giris_ay_haric", ay_kira_borc_dahil_mi(base, 2023, 4) is False)
        check("c_onceki_dahil", ay_kira_borc_dahil_mi(base, 2023, 3) is True)
        check("c_fp_iptal", kapanis_ek_fingerprint(0, iptal=True) == "IPTAL")

        os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
        os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
        importlib.reload(kc)
        from kira_kapanis_cap import (
            ay_kira_borc_dahil_mi as ay2,
            kapanis_ek_fingerprint as fp2,
            kira_son_borc_ayi as son2,
        )

        base2 = dict(base, kapanis_sonrasi_borc_ay=None)
        s2 = son2(base2)
        check("c_son_flag_on", s2 == date(2023, 3, 1), s2)
        check("c_borc_yok_flag_on", ay2(base2, 2023, 4) is False)
        check("c_fp_iptal_flag_on", fp2(None, iptal=True) == "IPTAL")
        # bayraklı N=0 normal (iptal yok) değişmesin — Aday A: son = kap-1 ay
        normal = {
            "durum": "pasif",
            "iptal": False,
            "kapanis_tarihi": date(2024, 4, 9),
            "kapanis_sonrasi_borc_ay": 0,
            "sozlesme_tarihi": date(2023, 4, 10),
        }
        check("c_n0_normal_flag_on", son2(normal) == date(2024, 3, 1), son2(normal))
    finally:
        if prev_h is None:
            os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
        else:
            os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = prev_h
        if prev_n is None:
            os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
        else:
            os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = prev_n
        import kira_kapanis_cap as kc

        importlib.reload(kc)


def test_d_cap_fingerprint_iptal_degil():
    """İptal olmayan: Hepsi / N=0 / N=1..12 birebir (bayrak kapalı)."""
    os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
    os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
    import kira_kapanis_cap as kc

    importlib.reload(kc)
    from kira_kapanis_cap import kapanis_ek_fingerprint, kira_son_borc_ayi

    r = {
        "durum": "pasif",
        "iptal": False,
        "kapanis_tarihi": date(2025, 9, 30),
        "kapanis_sonrasi_borc_ay": 0,
    }
    check("d_n0", kira_son_borc_ayi(r) == date(2025, 9, 1))
    check("d_fp0", kapanis_ek_fingerprint(0) == "0")
    check("d_fp0_iptal_arg_false", kapanis_ek_fingerprint(0, iptal=False) == "0")
    r3 = dict(r, kapanis_sonrasi_borc_ay=3)
    check("d_n3", kira_son_borc_ayi(r3) == date(2025, 12, 1))
    check("d_fp3", kapanis_ek_fingerprint(3) == "3")
    r_h = dict(r, kapanis_sonrasi_borc_ay=None)
    check("d_hepsi_none", kira_son_borc_ayi(r_h) is None)
    check("d_fp_h", kapanis_ek_fingerprint(None) == "H")
    for n in (1, 6, 12):
        rn = dict(r, kapanis_sonrasi_borc_ay=n)
        expect = date(2025, 9, 1)
        # +n months
        from kira_kapanis_cap import _add_months

        expect = _add_months(date(2025, 9, 1), n)
        check("d_n%d" % n, kira_son_borc_ayi(rn) == expect, kira_son_borc_ayi(rn))


def test_e_f_donus_aktif_pasif():
    from routes import giris_routes as gr
    from flask import Flask
    from flask_login import LoginManager

    app = Flask("iptal_don")
    app.secret_key = "t"
    LoginManager(app)
    app.register_blueprint(gr.bp, url_prefix="/giris")
    fn = getattr(gr.api_musteri_durum_guncelle, "__wrapped__", gr.api_musteri_durum_guncelle)
    captured = {}

    def _exec(sql, params=None):
        captured["params"] = params
        return 1

    common = dict(
        musteri_giris_tarihi_db=mock.Mock(return_value=date(2023, 4, 10)),
        fetch_one=mock.Mock(return_value={"id": 99}),
        execute=mock.Mock(side_effect=_exec),
        ensure_customers_durum=mock.Mock(),
        ensure_customers_is_active=mock.Mock(),
        ensure_customers_kapanis_sonrasi_borc_ay=mock.Mock(),
        ensure_customers_iptal=mock.Mock(),
        ensure_customers_hazir_ofis_oda=mock.Mock(),
        kapanis_borc_ufku_degisti=mock.Mock(return_value=True),
        musteri_tahsilat_var_mi=mock.Mock(return_value=False),
        _iptal_hayalet_grid_temizle=mock.Mock(),
    )

    with mock.patch.multiple(gr, **common):
        with app.test_request_context(
            "/giris/api/musteri/99/durum",
            method="POST",
            json={"durum": "iptal"},
        ):
            resp = fn(99)
            status = resp[1] if isinstance(resp, tuple) else resp.status_code
            data = resp[0].get_json() if isinstance(resp, tuple) else resp.get_json()
    params = captured.get("params") or ()
    check("e_iptal_status", status == 200 and data.get("ok"), (status, data))
    check("e_iptal_db_pasif", params[0] == "pasif", params)
    check("e_iptal_flag", params[4] is True, params)
    check("e_iptal_kap_giris", params[1] == date(2023, 4, 10), params)
    check("e_iptal_N0", params[2] == 0, params)
    check("e_json_ui", data.get("durum") == "iptal" and data.get("iptal") is True, data)
    check("e_hayalet", common["_iptal_hayalet_grid_temizle"].called)

    captured.clear()
    with mock.patch.multiple(gr, **common):
        with app.test_request_context(
            "/giris/api/musteri/99/durum",
            method="POST",
            json={"durum": "aktif"},
        ):
            resp = fn(99)
            status = resp[1] if isinstance(resp, tuple) else resp.status_code
            data = resp[0].get_json() if isinstance(resp, tuple) else resp.get_json()
    params = captured.get("params") or ()
    check("e_aktif_status", status == 200, (status, data))
    check("e_aktif_iptal_false", params[4] is False, params)
    check("e_aktif_durum", params[0] == "aktif", params)

    captured.clear()
    with mock.patch.multiple(gr, **common):
        with app.test_request_context(
            "/giris/api/musteri/99/durum",
            method="POST",
            json={
                "durum": "pasif",
                "kapanis_tarihi": "2024-04-09",
                "kapanis_sonrasi_borc_ay": 0,
            },
        ):
            resp = fn(99)
            status = resp[1] if isinstance(resp, tuple) else resp.status_code
            data = resp[0].get_json() if isinstance(resp, tuple) else resp.get_json()
    params = captured.get("params") or ()
    check("f_pasif_status", status == 200, (status, data))
    check("f_pasif_iptal_false", params[4] is False, params)
    check("f_pasif_durum", params[0] == "pasif", params)
    check("f_json_not_iptal", data.get("durum") == "pasif" and data.get("iptal") is False, data)


def test_g_ekstre_panel_cap_sifir():
    """İptal müşteri: borç penceresi boş → ay dahil değil (ekstre/panel 0 mantığı)."""
    os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
    os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
    import kira_kapanis_cap as kc

    importlib.reload(kc)
    from kira_kapanis_cap import ay_kira_borc_dahil_mi, kira_son_borc_ayi

    row = {
        "durum": "pasif",
        "iptal": True,
        "sozlesme_tarihi": date(2026, 8, 8),
        "rent_start_date": date(2026, 8, 8),
        "kapanis_tarihi": date(2026, 8, 8),
        "kapanis_sonrasi_borc_ay": 0,
    }
    son = kira_son_borc_ayi(row)
    check("g_son_onceki", son == date(2026, 7, 1), son)
    check("g_giris_ay_0", ay_kira_borc_dahil_mi(row, 2026, 8) is False)
    check("g_sonrasi_0", ay_kira_borc_dahil_mi(row, 2026, 9) is False)


def test_h_hayalet_force_ve_schema():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check(
        "h_force_bypass",
        "force_placeholder_collapse=True" in src
        and "_iptal_hayalet_grid_temizle" in src,
    )
    db = (ROOT / "db.py").read_text(encoding="utf-8")
    check(
        "h_ensure_iptal",
        "def ensure_customers_iptal" in db
        and "ADD COLUMN IF NOT EXISTS iptal BOOLEAN" in db
        and '_run_ensure_ddl_once("customers.iptal"' in db
        and "ensure_customers_iptal()" in db[db.find("def init_schema") : db.find("def init_schema") + 2500],
    )
    check(
        "h_select_fallback",
        "def customers_iptal_select_sql" in db and "FALSE AS iptal" in db,
    )
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("h_option_iptal", 'option value="iptal"' in html)
    check("h_uyari", "borç oluşmaz" in html)
    check("h_rozet_css", "sirket-durum-etiket--iptal" in html)
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    check("h_tjs_sync", "v === 'iptal'" in tjs or "raw === 'iptal'" in tjs)
    check("h_tjs_rozet", "girisApiMusteriIptalMi" in tjs)
    pd = (ROOT / "static" / "js" / "plan_degistir.js").read_text(encoding="utf-8")
    check("h_plan_iptal", 'secilen === "iptal"' in pd)


def test_i_get_detay_iptal():
    """GET detay serialize: iptal bayrağı → durum=iptal."""
    from kira_kapanis_cap import musteri_iptal_mi

    check("i_false0", musteri_iptal_mi(0) is False)
    check("i_false_false", musteri_iptal_mi(False) is False)
    check("i_false_none", musteri_iptal_mi(None) is False)
    check("i_true1", musteri_iptal_mi(1) is True)
    check("i_true", musteri_iptal_mi(True) is True)
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("i_detay_serialize", 'out["durum"] = "iptal"' in src)
    check("i_liste_iptal_col", "COALESCE(iptal, FALSE) AS iptal" in src)


def main():
    test_a_yaz_alanlari_iptal()
    test_b_tahsilat_korumasi_400()
    test_c_kira_son_borc_ayi_iptal()
    test_d_cap_fingerprint_iptal_degil()
    test_e_f_donus_aktif_pasif()
    test_g_ekstre_panel_cap_sifir()
    test_h_hayalet_force_ve_schema()
    test_i_get_detay_iptal()
    print("---")
    if FAILS:
        print("FAILS:", len(FAILS), FAILS)
        raise SystemExit(1)
    print("ALL_OK test_musteri_iptal_durumu")


if __name__ == "__main__":
    main()
