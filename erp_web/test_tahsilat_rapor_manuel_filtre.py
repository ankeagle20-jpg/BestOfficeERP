# -*- coding: utf-8 -*-
"""Giriş tahsilat raporu Manuel filtresi. Canlıya yazmaz. PII yok."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


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


def _row(tid, kay, te, ac, tutar=100.0, td=None):
    return {
        "id": tid,
        "fatura_id": None,
        "makbuz_no": str(tid),
        "tutar": tutar,
        "odeme_turu": "nakit",
        "tahsilat_tarihi": td or date(2026, 10, 15),
        "aciklama": ac,
        "tahsil_eden": te,
        "kaynak": kay,
        "cari_id": 1,
        "musteri_adi": "X",
        "rapor_tarihi": td or date(2026, 10, 15),
    }


def test_sinif_yardimcilari():
    from routes.faturalar_routes import (
        _tahsilat_rapor_grid_esitle_satiri,
        _tahsilat_rapor_manuel_belirsiz,
    )

    check(
        "grid_toplu haric",
        _tahsilat_rapor_grid_esitle_satiri(
            _row(1, "grid_toplu", None, "Ekim 2026 Tahsilat N|AYLIK_TAH|2026-10-01|")
        )
        is True,
    )
    check(
        "esitle pattern haric",
        _tahsilat_rapor_grid_esitle_satiri(
            _row(2, "", None, "Ekim 2026 Tahsilat N|AYLIK_TAH|2026-10-01|")
        )
        is True,
    )
    check(
        "manuel_makbuz dahil",
        _tahsilat_rapor_grid_esitle_satiri(_row(3, "manuel_makbuz", "Ali", "peşin")) is False,
    )
    check(
        "banka dahil",
        _tahsilat_rapor_grid_esitle_satiri(_row(4, "banka_import", None, "havale")) is False,
    )
    check(
        "odeme_linki dahil",
        _tahsilat_rapor_grid_esitle_satiri(_row(5, "odeme_linki", None, "link")) is False,
    )
    check(
        "manuel marker pattern yine dahil (kaynak)",
        _tahsilat_rapor_grid_esitle_satiri(
            _row(6, "manuel_makbuz", None, "Ekim 2026 Tahsilat N|AYLIK_TAH|2026-10-01|")
        )
        is False,
    )
    check(
        "belirsiz bos",
        _tahsilat_rapor_manuel_belirsiz(_row(7, "", "X", "serbest metin")) is True,
    )
    check(
        "belirsiz degil makbuz",
        _tahsilat_rapor_manuel_belirsiz(_row(8, "manuel_makbuz", None, "x")) is False,
    )


def test_api_manuel1_ve0():
    from flask_login import login_user
    import routes.faturalar_routes as fr

    app, user = _auth_app()
    rows = [
        _row(10, "manuel_makbuz", "A", "nakit", 50),
        _row(11, "banka_import", None, "havale", 60),
        _row(12, "odeme_linki", None, "olnk", 70),
        _row(13, "grid_toplu", None, "Ekim 2026 Tahsilat N|AYLIK_TAH|2026-10-01|", 80),
        _row(14, "", None, "Ekim 2026 Tahsilat N|AYLIK_TAH|2026-10-01|", 90),
        _row(15, "", "Kimse", "eski kayit belirsiz", 40),
        _row(16, "manuel_makbuz", None, "x", 100, date(2026, 10, 1)),
        _row(17, "manuel_makbuz", None, "y", 110, date(2026, 10, 30)),
    ]

    def fake_fetch(sql, params=None):
        bas, bit = params[-2], params[-1]
        out = []
        for r in rows:
            if bas <= r["tahsilat_tarihi"] <= bit:
                out.append(dict(r))
        return out

    with app.test_request_context(
        "/faturalar/api/tahsilat-raporu?baslangic=2026-10-01&bitis=2026-10-30&manuel=1"
    ):
        login_user(user)
        with patch.object(fr, "fetch_all", side_effect=fake_fetch), patch.object(
            fr, "_ensure_tahsil_eden_column", return_value=None
        ), patch.object(fr, "_opt_customer_id", return_value=0):
            data = _unwrap(fr.api_tahsilat_raporu)().get_json()

    ids = [int(x["id"]) for x in data.get("items") or []]
    check("m1 manuel var", 10 in ids and 11 in ids and 12 in ids, ids)
    check("m1 grid yok", 13 not in ids and 14 not in ids, ids)
    check("m1 belirsiz dahil", 15 in ids, ids)
    check("m1 sinir 1/30", 16 in ids and 17 in ids, ids)
    check("m1 flag", data.get("manuel") is True)
    check("m1 belirsiz meta", data.get("belirsiz_adet") == 1 and 15 in (data.get("belirsiz_ids") or []))
    check(
        "m1 toplam",
        abs(float(data.get("toplam") or 0) - (50 + 60 + 70 + 40 + 100 + 110)) < 0.01,
        data.get("toplam"),
    )

    with app.test_request_context(
        "/faturalar/api/tahsilat-raporu?baslangic=2026-10-01&bitis=2026-10-30&manuel=0"
    ):
        login_user(user)
        with patch.object(fr, "fetch_all", side_effect=fake_fetch), patch.object(
            fr, "_ensure_tahsil_eden_column", return_value=None
        ), patch.object(fr, "_opt_customer_id", return_value=0):
            data0 = _unwrap(fr.api_tahsilat_raporu)().get_json()
    ids0 = [int(x["id"]) for x in data0.get("items") or []]
    check("m0 tumu", set(ids0) == {10, 11, 12, 13, 14, 15, 16, 17}, ids0)
    check("m0 flag", data0.get("manuel") is False)

    with app.test_request_context(
        "/faturalar/api/tahsilat-raporu?baslangic=2026-10-01&bitis=2026-10-30"
    ):
        login_user(user)
        with patch.object(fr, "fetch_all", side_effect=fake_fetch), patch.object(
            fr, "_ensure_tahsil_eden_column", return_value=None
        ), patch.object(fr, "_opt_customer_id", return_value=0):
            data_yok = _unwrap(fr.api_tahsilat_raporu)().get_json()
    check("param yok = tumu", data_yok.get("adet") == 8 and data_yok.get("manuel") is False)


def test_ui_varsayilan_isaretli():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("giris id", 'id="giris_tahsil_rapor_manuel"' in html)
    check("giris checked", 'id="giris_tahsil_rapor_manuel" checked' in html)
    check("soz id", 'id="sozlesme_tahsil_rapor_manuel"' in html)
    check("soz checked", 'id="sozlesme_tahsil_rapor_manuel" checked' in html)
    check("title", "Aylık grid düzeltmelerini hariç tutar" in html)
    check("js manuel=1", "manuel=1" in html and "manuelId" in html)
    # Faturalar sekmesine dokunulmadı
    tab = (ROOT / "templates" / "faturalar" / "tahsilatlar_tab.html").read_text(encoding="utf-8")
    check("faturalar dokunulmadi", "tahsil_rapor_manuel" not in tab and "giris_tahsil_rapor_manuel" not in tab)


if __name__ == "__main__":
    test_sinif_yardimcilari()
    test_api_manuel1_ve0()
    test_ui_varsayilan_isaretli()
    if FAILS:
        print("FAILS", FAILS)
        raise SystemExit(1)
    print("PASS")
