# -*- coding: utf-8 -*-
"""Borç+Tahsil kapısı. Canlı veritabanına yazmaz, mevcut satırlara dokunmaz."""
import os
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parent
FAILS = []
_BAYRAK = "TUFE_BORC_TAHSIL_ENABLED"


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _ham(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def test_varsayilan_kapali_ve_red():
    from routes import giris_routes as gr
    import db as dbmod

    eski_ortam = os.environ.pop(_BAYRAK, None)
    yaz = []
    eski_ex = dbmod.execute
    eski_gr = gr.execute
    dbmod.execute = lambda *a, **k: yaz.append("db")
    gr.execute = lambda *a, **k: yaz.append("gr")
    app = Flask(__name__)
    try:
        check("varsayilan kapali", gr.tufe_borc_tahsil_acik() is False)
        os.environ[_BAYRAK] = "0"
        check("sifir kapali", gr.tufe_borc_tahsil_acik() is False)
        os.environ[_BAYRAK] = "1"
        check("acik bayrak", gr.tufe_borc_tahsil_acik() is True)
        os.environ.pop(_BAYRAK, None)
        check("geri kapali", gr.tufe_borc_tahsil_acik() is False)
        with app.test_request_context(
            "/giris/api/tufe-borclandir-nakit-tahsil-toplu",
            method="POST",
            json={"dry_run": False, "musteri_ids": [1]},
        ):
            resp = _ham(gr.api_tufe_borclandir_nakit_tahsil_toplu)()
        body, code = (resp[0].get_json(), resp[1]) if isinstance(resp, tuple) else (resp.get_json(), resp.status_code)
        check(
            "sunucu reddeder yazmaz",
            code == 403 and body.get("mesaj") == "Borç+Tahsil kapalı." and not yaz,
            (code, body, yaz),
        )
    finally:
        dbmod.execute = eski_ex
        gr.execute = eski_gr
        if eski_ortam is None:
            os.environ.pop(_BAYRAK, None)
        else:
            os.environ[_BAYRAK] = eski_ortam


def test_govde_ve_buton():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    fn = src.split("def api_tufe_borclandir_nakit_tahsil_toplu", 1)[1].split("\n@bp.route", 1)[0]
    kapi = fn.find("tufe_borc_tahsil_acik")
    ensure = fn.find("ensure_faturalar_amount_columns")
    check("kapi yazmadan once", 0 <= kapi < ensure)
    check("sentetik isaret duruyor", "|BTUFRT|" in fn and "tufe_otomasyon" in fn)
    check("kapi mevcut satiri silmez", "DELETE FROM tahsilatlar" not in fn[:kapi + 80])
    finans = (ROOT / "templates" / "faturalar" / "finans.html").read_text(encoding="utf-8")
    giris = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("finans buton kosulu", "{% if tufe_borc_tahsil_acik %}" in finans and 'id="tufe_borc_tahsil_btn"' in finans)
    check("giris buton kosulu", "{% if tufe_borc_tahsil_acik %}" in giris and 'id="tufe_borc_tahsil_btn"' in giris)
    fatura = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    giris_src = src
    check("sayfa bayragi gecirir", "tufe_borc_tahsil_acik=tufe_acik" in fatura and "tufe_borc_tahsil_acik=tufe_borc_tahsil_acik()" in giris_src)


if __name__ == "__main__":
    test_varsayilan_kapali_ve_red()
    test_govde_ve_buton()
    print("tufe_borc_tahsil_kapi %s" % ("ok" if not FAILS else "FAIL " + ",".join(FAILS)))
    raise SystemExit(1 if FAILS else 0)
