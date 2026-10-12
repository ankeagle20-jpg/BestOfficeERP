# -*- coding: utf-8 -*-
"""Otomatik fatura kapısı. Canlı veritabanına yazmaz."""
import os
from datetime import date
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parent
FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _ham(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class _Yaz:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(str(sql))

    def execute_returning(self, sql, params=None):
        self.sql.append(str(sql))
        return {"id": 1}

    def fetch_one(self, sql, params=None):
        s = str(sql)
        if "FROM customers" in s:
            return {"id": 867, "name": "Test"}
        return None

    def fetch_all(self, sql, params=None):
        if "FROM customers" in str(sql):
            return [{"id": 867}]
        return []


def _ayarla(fr, gr, yaz, enabled, dondurma=None):
    fr.ensure_auto_invoice_tables = lambda: None
    fr._auto_inv_settings = lambda: {
        "enabled": enabled,
        "run_day": 1,
        "run_hour": 9,
        "send_gib": False,
        "auto_sms_code": "",
    }
    fr.execute = yaz.execute
    fr.execute_returning = yaz.execute_returning
    fr.fetch_one = yaz.fetch_one
    fr.fetch_all = yaz.fetch_all
    gr.fetch_one = lambda *a, **k: None
    gr.fetch_all = lambda *a, **k: []
    gr._load_max_aylik_tah_iso_by_musteri = lambda **k: {}
    gr._pesin_borclandirma_horizon_for_musteri = lambda mid, **k: date(2026, 3, 1)
    gr._plan_paket_yukle = lambda ids: None
    gr._planli_reel_haritasi = lambda ids: {}
    gr._add_months = lambda d, n: date(d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1, 1)
    gr._dondurma_ay_kumesi = lambda mid: set(dondurma or [])
    fr._auto_month_amount_resolved = lambda *a, **k: 100.0


def _geri(fr, gr, eski):
    for ad, fn in eski["fr"].items():
        setattr(fr, ad, fn)
    for ad, fn in eski["gr"].items():
        setattr(gr, ad, fn)


def _fatura_yazildi(sqls):
    return any("INSERT INTO faturalar" in s for s in sqls)


def test_kapali_uc_tetik_yazmaz():
    from routes import faturalar_routes as fr
    from routes import giris_routes as gr

    yaz = _Yaz()
    eski = {
        "fr": {ad: getattr(fr, ad) for ad in (
            "ensure_auto_invoice_tables", "_auto_inv_settings", "execute", "execute_returning",
            "fetch_one", "fetch_all", "_auto_month_amount_resolved",
        )},
        "gr": {ad: getattr(gr, ad) for ad in (
            "fetch_one", "fetch_all", "_load_max_aylik_tah_iso_by_musteri",
            "_pesin_borclandirma_horizon_for_musteri", "_plan_paket_yukle",
            "_planli_reel_haritasi", "_add_months", "_dondurma_ay_kumesi",
        )},
    }
    _ayarla(fr, gr, yaz, False)
    app = Flask(__name__)
    app.secret_key = "kapi"
    try:
        for force in (False, True):
            yaz.sql.clear()
            out = fr.run_auto_invoice_cycle(force=force, run_date=date(2026, 3, 1))
            check(
                "dongu force=%s kapali" % force,
                out.get("skipped") and out.get("mesaj") == "Otomatik fatura kapalı." and not yaz.sql and not _fatura_yazildi(yaz.sql),
                out,
            )
        eski_getenv = os.getenv
        os.getenv = lambda k, d=None: "" if k == "CRON_TOKEN" else eski_getenv(k, d)
        try:
            with app.test_request_context("/faturalar/cron/auto-fatura"):
                resp = _ham(fr.cron_auto_fatura)()
            body = resp.get_json()
            check("cron kapali", body.get("mesaj") == "Otomatik fatura kapalı." and not yaz.sql, body)
        finally:
            os.getenv = eski_getenv
        with app.test_request_context("/faturalar/api/auto-fatura/run", method="POST", json={"force": True}):
            resp = _ham(fr.api_auto_fatura_run)()
        body = resp.get_json()
        check("manuel kosu force kapali", body.get("mesaj") == "Otomatik fatura kapalı." and not _fatura_yazildi(yaz.sql), body)
        yaz.sql.clear()
        with app.test_request_context(
            "/faturalar/api/auto-fatura/secili-olustur",
            method="POST",
            json={"musteri_ids": [867], "yil": 2026, "ay": "Mart"},
        ):
            resp = _ham(fr.api_auto_fatura_secili_olustur)()
        if isinstance(resp, tuple):
            body, code = resp[0].get_json(), resp[1]
        else:
            body, code = resp.get_json(), resp.status_code
        check(
            "secili olustur kapali",
            code == 403 and body.get("mesaj") == "Otomatik fatura kapalı." and not _fatura_yazildi(yaz.sql),
            (code, body),
        )
    finally:
        _geri(fr, gr, eski)


def test_acik_davranis_ayni():
    from routes import faturalar_routes as fr
    from routes import giris_routes as gr

    yaz = _Yaz()
    eski = {
        "fr": {ad: getattr(fr, ad) for ad in (
            "ensure_auto_invoice_tables", "_auto_inv_settings", "execute", "execute_returning",
            "fetch_one", "fetch_all", "_auto_month_amount_resolved",
        )},
        "gr": {ad: getattr(gr, ad) for ad in (
            "fetch_one", "fetch_all", "_load_max_aylik_tah_iso_by_musteri",
            "_pesin_borclandirma_horizon_for_musteri", "_plan_paket_yukle",
            "_planli_reel_haritasi", "_add_months", "_dondurma_ay_kumesi",
        )},
    }
    _ayarla(fr, gr, yaz, True, dondurma={"2026-03"})
    try:
        yaz.sql.clear()
        out = fr.run_auto_invoice_cycle(force=False, run_date=date(2026, 3, 15))
        check(
            "acikken gun degil yazmaz",
            out.get("skipped") and "planlanan gün" in str(out.get("mesaj") or "") and not _fatura_yazildi(yaz.sql),
            out,
        )
        yaz.sql.clear()
        out = fr.run_auto_invoice_cycle(force=True, run_date=date(2026, 3, 15))
        check(
            "acik force dondurma fatura yazmaz",
            out.get("ok") and not out.get("skipped") and not _fatura_yazildi(yaz.sql),
            (out, [s[:40] for s in yaz.sql]),
        )
        # Geçerli kayıt varsa ikinci satır yok (mevcut create guard).
        def var_oto(sql, params=None):
            if "AUTO_INV" in str(sql) or "LIKE" in str(sql):
                return {"id": 140, "fatura_no": "DAHILI-2026-0140"}
            return yaz.fetch_one(sql, params)
        fr.fetch_one = var_oto
        gr._dondurma_ay_kumesi = lambda mid: set()
        created = []
        fr.execute_returning = lambda sql, params=None: created.append(str(sql)) or {"id": 9}
        tek = fr._auto_invoice_create_for_customer(867, date(2026, 4, 1))
        check(
            "acik gecerli kayit varsa ikinci yok",
            tek.get("status") == "exists" and not any("INSERT INTO faturalar" in s for s in created),
            tek,
        )
    finally:
        _geri(fr, gr, eski)


def test_varsayilan_false_ve_arayuz():
    db = (ROOT / "db.py").read_text(encoding="utf-8")
    blok = db.split("def ensure_auto_invoice_tables", 1)[1].split("def ", 1)[0]
    check("sema varsayilan FALSE", "enabled BOOLEAN DEFAULT FALSE" in blok and "VALUES (FALSE, 1, 9, FALSE)" in blok)
    check("ensure TRUE yazmiyor", "VALUES (TRUE" not in blok and "enabled=TRUE" not in blok.upper().replace(" ", ""))
    finans = (ROOT / "templates" / "faturalar" / "finans.html").read_text(encoding="utf-8")
    tab = (ROOT / "templates" / "faturalar" / "faturalar_tab.html").read_text(encoding="utf-8")
    giris = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("simdi calistir kapali baslar", 'id="auto_run_btn"' in finans and "disabled" in finans.split('id="auto_run_btn"', 1)[1][:80])
    check("ayar kutusu duruyor", "Otomatik aktif" in finans and "auto_send_gib" in finans and "autoSaveSettings" in finans)
    check("finans secili gizli", 'id="fatura_rapor_secili_otomatik_fatura_btn" hidden' in finans)
    check("faturalar secili gizli", 'id="aday_olustur_btn" hidden' in tab)
    check("giris secili gizli", 'id="fatura_rapor_secili_otomatik_fatura_btn" hidden' in giris)
    ayar = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    check("ayar kaydi duruyor", "def api_auto_fatura_settings" in ayar and "UPDATE auto_invoice_settings" in ayar)


if __name__ == "__main__":
    test_kapali_uc_tetik_yazmaz()
    test_acik_davranis_ayni()
    test_varsayilan_false_ve_arayuz()
    print("auto_fatura_kapi %s" % ("ok" if not FAILS else "FAIL " + ",".join(FAILS)))
    raise SystemExit(1 if FAILS else 0)
