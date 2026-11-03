# -*- coding: utf-8 -*-
"""İptal müşteri: aylık grid boş + tahsilat/fatura 400. Canlı yazma yok."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)
os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek="") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_a_grid_api_bos():
    from routes import giris_routes as gr
    from flask import Flask
    from flask_login import LoginManager

    app = Flask("iptal_grid")
    app.secret_key = "t"
    LoginManager(app)
    app.register_blueprint(gr.bp, url_prefix="/giris")
    fn = getattr(gr.api_aylik_grid_cache, "__wrapped__", gr.api_aylik_grid_cache)

    with mock.patch.object(gr, "_musteri_sozlesme_iptal_mi", return_value=True), mock.patch.object(
        gr, "_ensure_aylik_grid_cache_table"
    ), mock.patch.object(gr, "_aylik_grid_mem_set"):
        with app.test_request_context("/giris/api/aylik-grid-cache?musteri_id=435"):
            resp = fn()
            data = resp.get_json() if hasattr(resp, "get_json") else resp[0].get_json()
            status = resp.status_code if hasattr(resp, "status_code") else 200
    check("a_status", status == 200 and data.get("ok") is True, (status, data))
    cache = (data or {}).get("cache") or {}
    check("a_iptal_flag", data.get("iptal") is True and cache.get("iptal") is True, data)
    check("a_aylar_bos", cache.get("aylar") == [], cache.get("aylar"))
    check("a_mesaj", "iptal" in str(cache.get("bos_neden") or "").lower() or "borç" in str(cache.get("iptal_mesaj") or "").lower(), cache)


def test_b_tahsilat_fatura_400():
    from routes import giris_routes as gr
    from flask import Flask
    from flask_login import LoginManager

    app = Flask("iptal_ops")
    app.secret_key = "t"
    LoginManager(app)
    app.register_blueprint(gr.bp, url_prefix="/giris")

    for name, fn0, path, body in (
        (
            "tahsil",
            getattr(gr.api_aylik_tutarlardan_tahsil_et, "__wrapped__", gr.api_aylik_tutarlardan_tahsil_et),
            "/giris/api/aylik-tutarlardan-tahsil-et",
            {"musteri_id": 435, "satirlar": [{"yil": 2026, "ay": 8, "tutar_kdv_dahil": 100}]},
        ),
        (
            "borc",
            getattr(gr.api_aylik_tutarlardan_borclandir, "__wrapped__", gr.api_aylik_tutarlardan_borclandir),
            "/giris/api/aylik-tutarlardan-borclandir",
            {"musteri_id": 435, "satirlar": [{"yil": 2026, "ay": 8, "tutar_kdv_dahil": 100}]},
        ),
    ):
        with mock.patch.object(gr, "_musteri_sozlesme_iptal_mi", return_value=True):
            with app.test_request_context(path, method="POST", json=body):
                resp = fn0()
                if isinstance(resp, tuple):
                    status, data = resp[1], resp[0].get_json()
                else:
                    status, data = resp.status_code, resp.get_json()
        check(f"b_{name}_400", status == 400, (status, data))
        mes = str((data or {}).get("mesaj") or "")
        check(
            f"b_{name}_mesaj",
            ("iptal" in mes.casefold()) or ("İptal" in mes) or ("tahsilat/fatura" in mes),
            data,
        )


def test_c_pasif_iptal_degil_cap():
    """Pasif + N=0 + bayrak: grid core ay_sayisi > 0 (iptal kısa yolu yok)."""
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    import importlib
    import kira_kapanis_cap as kc

    importlib.reload(kc)
    from routes import giris_routes as gr

    importlib.reload(gr)
    kyc = {
        "durum": "pasif",
        "iptal": False,
        "sozlesme_tarihi": date(2023, 4, 10),
        "sozlesme_bitis": date(2024, 4, 10),
        "kapanis_tarihi": date(2024, 4, 9),
        "kapanis_sonrasi_borc_ay": 0,
        "aylik_kira": 1000,
        "kira_suresi_ay": 12,
    }
    core = gr._aylik_grid_contract_core(kyc, {})
    check("c_core", core is not None and int(core.get("ay_sayisi") or 0) > 0, core and core.get("ay_sayisi"))
    # iptal ise 0
    kyc2 = dict(kyc, iptal=True, sozlesme_bitis=date(2027, 8, 8), kapanis_tarihi=date(2026, 8, 8), sozlesme_tarihi=date(2026, 8, 8))
    core2 = gr._aylik_grid_contract_core(kyc2, {})
    check("c_iptal_0", core2 is not None and int(core2.get("ay_sayisi") or 0) == 0, core2)
    os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
    importlib.reload(kc)
    importlib.reload(gr)


def test_d_disk_dolu_olsa_bile_api_bos():
    from routes import giris_routes as gr
    from flask import Flask
    from flask_login import LoginManager

    app = Flask("iptal_disk")
    app.secret_key = "t"
    LoginManager(app)
    app.register_blueprint(gr.bp, url_prefix="/giris")
    fn = getattr(gr.api_aylik_grid_cache, "__wrapped__", gr.api_aylik_grid_cache)
    ghost = {"musteri_id": 435, "aylar": [{"yil": 2026, "ay": 8, "tutar_kdv_dahil": 1440}] * 11}

    with mock.patch.object(gr, "_musteri_sozlesme_iptal_mi", return_value=True), mock.patch.object(
        gr, "_ensure_aylik_grid_cache_table"
    ), mock.patch.object(gr, "_aylik_grid_mem_set"), mock.patch.object(
        gr, "fetch_one", return_value={"payload": __import__("json").dumps(ghost)}
    ):
        with app.test_request_context("/giris/api/aylik-grid-cache?musteri_id=435"):
            resp = fn()
            data = resp.get_json()
    cache = (data or {}).get("cache") or {}
    check("d_bos", cache.get("aylar") == [], cache)
    check("d_iptal", cache.get("iptal") is True)


def test_e_fe_kontroller():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    for label, src in (("tjs", tjs), ("html", html)):
        check(f"e_{label}_helper", "function girisFormMusteriIptalMi" in src)
        check(f"e_{label}_bos_goster", "sozlesmelerAylikIptalBosGoster" in src)
        check(f"e_{label}_mesaj", "aylık borç oluşmaz" in src)
        check(f"e_{label}_btn_gizle", "btn-aylik-fatura-alt" in src and "sozlesmelerAylikIptalUiUygula" in src)
        check(f"e_{label}_efektif_iptal", "durum === 'iptal'" in src)
        check(f"e_{label}_guncelle_early", "sozlesmelerAylikIptalBosGoster" in src[src.find("function sozlesmelerAylikGuncelle") : src.find("function sozlesmelerAylikGuncelle") + 500])
    src_py = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("e_api_early", "_musteri_sozlesme_iptal_mi(musteri_id)" in src_py and "bos_neden" in src_py)
    check("e_guard_tahsil", "api_aylik_tutarlardan_tahsil_et" in src_py and "_iptal_musteri_islem_red_json" in src_py)


def main():
    test_a_grid_api_bos()
    test_b_tahsilat_fatura_400()
    test_c_pasif_iptal_degil_cap()
    test_d_disk_dolu_olsa_bile_api_bos()
    test_e_fe_kontroller()
    print("---")
    if FAILS:
        print("FAILS", FAILS)
        raise SystemExit(1)
    print("ALL_OK test_iptal_grid_bos")


if __name__ == "__main__":
    main()
