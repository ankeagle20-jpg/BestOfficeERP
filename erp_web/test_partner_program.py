# -*- coding: utf-8 -*-
"""Ortak programı birim testleri. Canlı veritabanına yazmaz."""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from partner_program import (
    ayar_sec,
    hold_engel,
    komisyon_satirlari,
    net_matrah,
    onerilen_kademe,
    parent_uygun,
    payload_dogrula,
    tekil_ekle,
    tur_belirle,
    PartnerKuralError,
)

ROOT = Path(__file__).resolve().parent
FAIL = []


def check(ad, kosul, detay=""):
    if kosul:
        print("ok", ad)
    else:
        FAIL.append(ad)
        print("FAIL", ad, detay)


def _payload(**ustu):
    govde = {
        "satis_pencere_ay": 12,
        "bekleme_gun": 30,
        "tavan_komisyon_pct": 50,
        "dist_satis_pct": 10,
        "dist_yenileme_pct": 5,
        "matrah_kdv_orani_pct": 20,
        "matrah_kdv_onaylandi": True,
        "kademeler": [
            {"kod": "starter", "ad": "Başlangıç", "esik_ciro_try": 0, "satis_pct": 25, "yenileme_pct": 8},
            {"kod": "silver", "ad": "Gümüş", "esik_ciro_try": 100000, "satis_pct": 30, "yenileme_pct": 10},
            {"kod": "gold", "ad": "Altın", "esik_ciro_try": 300000, "satis_pct": 35, "yenileme_pct": 12},
            {"kod": "platinum", "ad": "Platin", "esik_ciro_try": 750000, "satis_pct": 40, "yenileme_pct": 15},
        ],
    }
    govde.update(ustu)
    return payload_dogrula(govde)


def test_altyapi():
    db = (ROOT / "db.py").read_text(encoding="utf-8")
    fn = db.split("def ensure_partner_program_tables", 1)[1].split("\ndef clear_all_customers", 1)[0]
    for ad in (
        "partner_accounts",
        "partner_program_settings",
        "partner_codes",
        "partner_code_redemptions",
        "partner_commission_entries",
    ):
        check(ad + " public", f"CREATE TABLE IF NOT EXISTS public.{ad}" in fn)
        check(ad + " strip", ad in (ROOT / "tenant_provisioning.py").read_text(encoding="utf-8"))
    check("ensure once bagimsiz", "_run_ensure_ddl_once(" not in fn)
    check("fatura ddl yok", "ALTER TABLE public.platform_tenant_invoices" not in fn)
    from tenant_provisioning import transform_public_dump

    dump = (
        "\n--\n-- Name: partner_accounts; Type: TABLE; Schema: public; Owner: -\n--\n\n"
        "CREATE TABLE public.partner_accounts (\n    id integer\n);\n"
        "\n--\n-- Name: customers; Type: TABLE; Schema: public; Owner: -\n--\n\n"
        "CREATE TABLE public.customers (\n    id integer\n);\n"
    )
    out = transform_public_dump(dump, "tenant_ornek")
    check("kiraci semasinda yok", "partner_accounts" not in out and "tenant_ornek.customers" in out)

    from flask import Flask, g
    from flask_login import LoginManager, login_user
    from auth import User
    from routes.admin_partner_routes import bp

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "t"
    lm = LoginManager(app)

    @lm.user_loader
    def _yukle(_uid):
        return None
    app.register_blueprint(bp, url_prefix="/admin")
    bayrak = {"sema": None}

    @app.before_request
    def _sema():
        if bayrak["sema"]:
            g.tenant_schema = bayrak["sema"]

    c = app.test_client()
    check("anonim 403", c.get("/admin/api/ortaklar").status_code == 403)
    with app.test_request_context("/admin/api/ortaklar"):
        login_user(User(3, "p", "P", "personel"))
    # login_user oturumu test client'a session ile taşır
    with c.session_transaction() as sess:
        sess.clear()
    with app.test_request_context():
        login_user(User(3, "p", "P", "personel"))
        from flask import session
        # Flask-Login session key
    # Doğrudan guard çağrısı
    from routes.admin_partner_routes import platform_partner_admin

    @platform_partner_admin
    def _icer():
        return "ok"

    with app.test_request_context("/admin/api/ortaklar"):
        login_user(User(4, "p", "P", "personel"))
        kod = _icer()
        check("rol 403", getattr(kod, "status_code", None) == 403 or (isinstance(kod, tuple) and kod[1] == 403))
    with app.test_request_context("/admin/ortaklar"):
        g.tenant_schema = "tenant_ornek"
        login_user(User(1, "a", "A", "admin"))
        kod = _icer()
        check("kiraci 403", isinstance(kod, tuple) and kod[1] == 403)


def test_derinlik():
    parent_uygun("distributor", None)
    parent_uygun("bayi", None)
    parent_uygun("bayi", "distributor")
    check("derinlik izin", True)
    for cagri in (
        lambda: parent_uygun("distributor", "distributor"),
        lambda: parent_uygun("bayi", "bayi"),
        lambda: parent_uygun("bayi", "diger"),
    ):
        try:
            cagri()
            check("derinlik ret", False)
        except PartnerKuralError:
            check("derinlik ret", True)


def test_pencere_matrah_oran_surum_hold():
    bag = datetime(2026, 1, 15, tzinfo=timezone.utc)
    check("ici satis", tur_belirle(bag, datetime(2026, 6, 1, tzinfo=timezone.utc), 12) == "satis")
    check("sinir yenileme", tur_belirle(bag, datetime(2027, 1, 15, tzinfo=timezone.utc), 12) == "yenileme")
    check("yukseltme satis", tur_belirle(bag, datetime(2026, 11, 1, tzinfo=timezone.utc), 12) == "satis")
    check("net 1200", net_matrah("1200", 20) == Decimal("1000.00"))
    check("net sifir oran", net_matrah("1200", 0) == Decimal("1200.00"))
    kapali = _payload(matrah_kdv_onaylandi=False)
    bos = komisyon_satirlari(
        payment_id=1, invoice_id=1, tenant_id=1,
        tahsil_at=bag, brut="1200", bagli_at=bag,
        partner_id=7, partner_tur="bayi", partner_kademe="silver",
        parent_id=3, payload=kapali, ayar_id=1,
    )
    check("onaysiz satir yok", bos == [])
    acik = _payload()
    satir = komisyon_satirlari(
        payment_id=1, invoice_id=1, tenant_id=1,
        tahsil_at=bag, brut="1200", bagli_at=bag,
        partner_id=7, partner_tur="bayi", partner_kademe="silver",
        parent_id=3, payload=acik, ayar_id=4,
    )
    roller = {s["rol"]: s for s in satir}
    check("gumus bayi", roller["bayi"]["tutar"] == Decimal("300.00") and roller["bayi"]["partner_id"] == 7)
    check("gumus dist", roller["distributor"]["tutar"] == Decimal("100.00") and roller["distributor"]["partner_id"] == 3)
    check("tavan yok", roller["bayi"]["tavan_uygulandi"] is False)
    dogrudan = komisyon_satirlari(
        payment_id=2, invoice_id=1, tenant_id=1,
        tahsil_at=bag, brut="1200", bagli_at=bag,
        partner_id=7, partner_tur="bayi", partner_kademe="silver",
        parent_id=None, payload=acik, ayar_id=4,
    )
    check(
        "dogrudan iki rol",
        len(dogrudan) == 2 and all(s["partner_id"] == 7 for s in dogrudan),
    )
    asan = _payload(dist_satis_pct=20)
    asan_satir = komisyon_satirlari(
        payment_id=3, invoice_id=1, tenant_id=1,
        tahsil_at=bag, brut="1200", bagli_at=bag,
        partner_id=7, partner_tur="bayi", partner_kademe="platinum",
        parent_id=3, payload=asan, ayar_id=4,
    )
    toplam = sum(s["tutar"] for s in asan_satir)
    check("tavan 500", toplam == Decimal("500.00") and asan_satir[0]["tavan_uygulandi"] is True)
    check("onerilen silver", onerilen_kademe(150000, acik["kademeler"]) == "silver")
    eski = {"id": 1, "valid_from": bag, "valid_to": None, "payload": {"matrah_kdv_orani_pct": 20}}
    yeni_from = datetime(2027, 1, 1, tzinfo=timezone.utc)
    yeni = {"id": 2, "valid_from": yeni_from, "valid_to": None, "payload": {"matrah_kdv_orani_pct": 10}}
    kopya = dict(eski["payload"])
    check("eski surum", ayar_sec([eski, yeni], datetime(2026, 6, 1, tzinfo=timezone.utc))["id"] == 1)
    check("yeni surum", ayar_sec([eski, yeni], datetime(2027, 2, 1, tzinfo=timezone.utc))["id"] == 2)
    check("eski payload durur", eski["payload"] == kopya)
    simdi = datetime(2026, 2, 1, tzinfo=timezone.utc)
    hold = simdi + timedelta(days=10)
    check("hold uyari", hold_engel(hold, simdi, False) is not None)
    check("hold zorla", hold_engel(hold, simdi, True) is None)
    depo = {}
    check("ilk yazim", tekil_ekle(depo, satir) == 2)
    check("ikinci yazim yok", tekil_ekle(depo, satir) == 0 and len(depo) == 2)
    check("hesap kaynakta gomulu oran yok", "100000" not in inspect.getsource(komisyon_satirlari))


def test_kod():
    import db
    import routes.signup_routes as sr
    import partner_program as pp

    src = inspect.getsource(sr.api_signup)
    i_kod = src.find("kodu_cozumle")
    i_res = src.find("reserve_tenant_slug")
    check("kod slugdan once", 0 < i_kod < i_res)
    check("gecersiz slug oncesi", src.find('"gecersiz"') < i_res)
    check("motor partner yok", "partner" not in inspect.getsource(sr._compute_purchase_bill))
    check("metadata kod yok", "partner_kod" not in inspect.getsource(sr._create_purchase_invoice))
    html = (ROOT / "templates" / "signup" / "signup.html").read_text(encoding="utf-8")
    check("alan var", "Ortak/bayi kodu" in html and "if (partnerKod) body.partner_kod" in html)
    pp.ensure_partner_tables_once = lambda: None
    state = {"rows": []}
    orig_f, orig_e = db.fetch_one, db.execute_returning

    def fetch_one(sql, params=()):
        duz = sql.replace(" ", "")
        if "WHEREtenant_id=%s" in duz:
            for r in state["rows"]:
                if r.get("tenant_id") == params[0]:
                    return {"id": r["id"], "partner_id": r["partner_id"]}
        if "WHEREinvoice_id=%s" in duz:
            for r in state["rows"]:
                if r.get("invoice_id") == params[0]:
                    return {"id": r["id"], "partner_id": r["partner_id"]}
        return None

    def execute_returning(sql, params=()):
        row = {
            "id": len(state["rows"]) + 1,
            "tenant_id": params[0],
            "invoice_id": params[1],
            "partner_id": params[3],
        }
        state["rows"].append(row)
        return row

    db.fetch_one = fetch_one
    db.execute_returning = execute_returning
    try:
        ilk = pp.bag_olustur(partner_id=1, code_id=2, tenant_id=9)
        ikinci = pp.bag_olustur(partner_id=8, code_id=3, tenant_id=9)
        check("ilk kod kazanir", ilk["ilk"] is False and ikinci["ilk"] is True and len(state["rows"]) == 1)
    finally:
        db.fetch_one, db.execute_returning = orig_f, orig_e
    try:
        pp.bag_degistir(tenant_id=1, kod="ABC", gerekce="y")
        check("gerekce zorunlu", False)
    except PartnerKuralError:
        check("gerekce zorunlu", True)
    from partner_program import kod_durum

    simdi = datetime(2026, 6, 1, tzinfo=timezone.utc)
    check(
        "suresi dolmus",
        bool(
            kod_durum(
                {
                    "aktif": True,
                    "partner_durum": "aktif",
                    "gecerlilik_bas": datetime(2020, 1, 1, tzinfo=timezone.utc),
                    "gecerlilik_bit": datetime(2021, 1, 1, tzinfo=timezone.utc),
                },
                simdi,
            )
        ),
    )


def test_kanca():
    import partner_program as pp

    def patla(_pid):
        raise RuntimeError("yazim")

    eski = pp.komisyon_isle
    pp.komisyon_isle = patla
    try:
        pp.komisyon_kancasi(5)
        check("kanca yutar", True)
    except Exception as e:
        check("kanca yutar", False, str(e))
    finally:
        pp.komisyon_isle = eski
    paytr = (ROOT / "routes" / "billing_paytr_routes.py").read_text(encoding="utf-8")
    kayit = paytr.split("def _record_paytr_paid", 1)[1].split("\ndef ", 1)[0]
    cb = paytr.split("def paytr_callback", 1)[1]
    check("kayit icinde kanca yok", "komisyon" not in kayit)
    check("kanca kayittan sonra", cb.find("_record_paytr_paid") < cb.find("komisyon_kancasi"))
    elle = (ROOT / "routes" / "admin_billing_routes.py").read_text(encoding="utf-8")
    fn = elle.split("def api_payments_create", 1)[1].split("\ndef ", 1)[0]
    check(
        "elle tahsilat kanca",
        "komisyon_kancasi" in fn
        and fn.find("INSERT INTO public.platform_tenant_payments") < fn.find("komisyon_kancasi"),
    )
    check("tara ucu", "eksik_komisyonlari_tara" in (ROOT / "routes" / "admin_partner_routes.py").read_text(encoding="utf-8"))


def main():
    test_altyapi()
    test_derinlik()
    test_pencere_matrah_oran_surum_hold()
    test_kod()
    test_kanca()
    if FAIL:
        raise SystemExit("FAIL " + ",".join(FAIL))
    print("partner_program ok")


if __name__ == "__main__":
    main()
