# -*- coding: utf-8 -*-
"""Tahsilat raporu tarih filtresi: varsayılan = tahsilat_tarihi.
Canlıya yazmaz. PII yok (yalnız id / tarih / tutar / sayı).
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def _row(tid, ttarih, tutar, aciklama):
    return {
        "id": tid,
        "fatura_id": None,
        "makbuz_no": str(tid),
        "tutar": tutar,
        "odeme_turu": "nakit",
        "tahsilat_tarihi": ttarih,
        "aciklama": aciklama,
        "tahsil_eden": "",
        "cari_id": 1,
        "musteri_id": 1,
        "customer_id": 1,
        "musteri_adi": "X",
        "rapor_tarihi": ttarih,
    }


def _unwrap(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _auth_app():
    from flask import Flask
    from flask_login import LoginManager, UserMixin

    app = Flask(__name__)
    app.secret_key = "test"
    lm = LoginManager(app)

    class U(UserMixin):
        id = 1

        @property
        def is_authenticated(self):
            return True

    @lm.user_loader
    def load(_):
        return U()

    return app, U()


def test_api_varsayilan_tahsilat_tarihi():
    from flask_login import login_user
    import routes.faturalar_routes as fr

    app, user = _auth_app()
    captured = {}

    def fake_fetch(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        # SQL'e göre sahte satırlar döndürme — test SQL koşullarını doğrular
        return []

    with app.test_request_context(
        "/faturalar/api/tahsilat-raporu?baslangic=2026-10-01&bitis=2026-10-30"
    ):
        login_user(user)
        with patch.object(fr, "fetch_all", side_effect=fake_fetch), patch.object(
            fr, "_ensure_tahsil_eden_column", return_value=None
        ), patch.object(fr, "_opt_customer_id", return_value=0):
            resp = _unwrap(fr.api_tahsilat_raporu)()
            data = resp.get_json()

    check("api ok", data.get("ok") is True)
    check("date_mode tahsilat", data.get("date_mode") == "tahsilat_tarihi", data.get("date_mode"))
    sql = captured.get("sql") or ""
    check("sql tahsilat_tarihi", "t.tahsilat_tarihi::date >=" in sql)
    check("sql marker OR yok", "regexp_matches" not in sql)
    check("params 2 tarih", list(captured.get("params") or []) == [date(2026, 10, 1), date(2026, 10, 30)])


def test_api_donem_senaryo1():
    from flask_login import login_user
    import routes.faturalar_routes as fr

    app, user = _auth_app()
    captured = {}

    def fake_fetch(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with app.test_request_context(
        "/faturalar/api/tahsilat-raporu?baslangic=2026-10-01&bitis=2026-10-30&senaryo=1"
    ):
        login_user(user)
        with patch.object(fr, "fetch_all", side_effect=fake_fetch), patch.object(
            fr, "_ensure_tahsil_eden_column", return_value=None
        ), patch.object(fr, "_opt_customer_id", return_value=0):
            resp = _unwrap(fr.api_tahsilat_raporu)()
            data = resp.get_json()

    check("donem mode", data.get("date_mode") == "donem_kapsama")
    check("donem sql marker", "regexp_matches" in (captured.get("sql") or ""))


def test_api_sinir_ve_filtre_mantik():
    """Temmuz peşin Ekim aralığında yok; 01 ve 30 Ekim var; tutar makbuz tutarı."""
    from flask_login import login_user
    import routes.faturalar_routes as fr

    app, user = _auth_app()
    # 12 ay peşin Temmuz'da, marker'larda Ekim var
    peşin = (
        "|AYLIK_TAH|2026-07-01| |AYLIK_TAH|2026-08-01| |AYLIK_TAH|2026-09-01| "
        "|AYLIK_TAH|2026-10-01| |AYLIK_TAH|2026-11-01|"
    )
    rows_db = [
        _row(100, date(2026, 7, 15), 12000.0, peşin),
        _row(101, date(2026, 10, 1), 500.0, "|AYLIK_TAH|2026-10-01|"),
        _row(102, date(2026, 10, 30), 600.0, "nakit"),
        _row(103, date(2026, 9, 15), 700.0, "|AYLIK_TAH|2026-10-01|"),  # Eylül makbuz, Ekim marker
    ]

    def fake_fetch(sql, params=None):
        # Simüle et: yalnız tahsilat_tarihi filtresi (varsayılan SQL)
        bas, bit = params[0], params[1]
        out = []
        for r in rows_db:
            td = r["tahsilat_tarihi"]
            if bas <= td <= bit:
                out.append(dict(r))
        return out

    with app.test_request_context(
        "/faturalar/api/tahsilat-raporu?baslangic=2026-10-01&bitis=2026-10-30"
    ):
        login_user(user)
        with patch.object(fr, "fetch_all", side_effect=fake_fetch), patch.object(
            fr, "_ensure_tahsil_eden_column", return_value=None
        ), patch.object(fr, "_opt_customer_id", return_value=0):
            data = _unwrap(fr.api_tahsilat_raporu)().get_json()

    ids = [int(x["id"]) for x in data.get("items") or []]
    check("temmuz peşin yok", 100 not in ids, ids)
    check("eylul marker yok", 103 not in ids, ids)
    check("1 eki var", 101 in ids, ids)
    check("30 eki var", 102 in ids, ids)
    check("toplam makbuz", abs(float(data.get("toplam") or 0) - 1100.0) < 0.01, data.get("toplam"))
    check("adet 2", data.get("adet") == 2, data.get("adet"))


def test_dataset_varsayilan_senaryo0():
    from flask import Flask
    import routes.faturalar_routes as fr

    app = Flask(__name__)
    captured = {}

    def fake_fetch(sql, params=None):
        captured.setdefault("sqls", []).append(sql)
        captured.setdefault("params", []).append(params)
        return []

    with app.test_request_context(
        "/faturalar/tahsilatlar?baslangic=2026-10-01&bitis=2026-10-30"
    ):
        with patch.object(fr, "fetch_all", side_effect=fake_fetch):
            ds = fr._tahsilat_rapor_dataset_from_request()

    check("dataset mode tahsilat", ds.get("date_mode") == "tahsilat_tarihi", ds.get("date_mode"))
    check("dataset senaryo 0", str(ds.get("senaryo")) == "0", ds.get("senaryo"))
    sql0 = (captured.get("sqls") or [""])[0]
    check("dataset sql tarih", "t.tahsilat_tarihi::date >=" in sql0)
    check("dataset sql donem yok", "generate_series" not in sql0 and "AYLIK_TAH" not in sql0)


def test_dataset_senaryo1_donem():
    from flask import Flask
    import routes.faturalar_routes as fr

    app = Flask(__name__)
    with app.test_request_context(
        "/x?baslangic=2026-10-01&bitis=2026-10-30&senaryo=1"
    ):
        with patch.object(fr, "fetch_all", return_value=[]):
            ds = fr._tahsilat_rapor_dataset_from_request()
    check("senaryo1 donem", ds.get("date_mode") == "donem_kapsama")


def test_ui_etiket_ve_varsayilan():
    html = (ROOT / "templates" / "faturalar" / "tahsilatlar_tab.html").read_text(encoding="utf-8")
    check("etiket doneme gore", "Döneme göre (Yıllık)" in html)
    check("checked sadece senaryo1", "{% if senaryo is defined and senaryo != '0' %}checked{% endif %}" in html)
    check("js varsayilan kapali", "senaryoParam === '1'" in html)
    src = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    check("py default senaryo 0", 'request.args.get("senaryo", "0")' in src)
    giris = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("giris aciklama tahsilat", "Tahsilat tarihine göre" in giris)


if __name__ == "__main__":
    test_api_varsayilan_tahsilat_tarihi()
    test_api_donem_senaryo1()
    test_api_sinir_ve_filtre_mantik()
    test_dataset_varsayilan_senaryo0()
    test_dataset_senaryo1_donem()
    test_ui_etiket_ve_varsayilan()
    if FAILS:
        print("FAILS", FAILS)
        raise SystemExit(1)
    print("PASS")
