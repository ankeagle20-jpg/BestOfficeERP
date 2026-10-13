# -*- coding: utf-8 -*-
"""Müşteri çoğalt: yerel Postgres + gerçek giriş sayfası. Canlı veritabanına yazmaz."""
import json
import os
import sys
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
if os.environ.get("ALLOW_PROD_WRITE") == "1":
    print("FAIL ALLOW_PROD_WRITE")
    raise SystemExit(1)

import psycopg2
from flask import Flask, g
from flask_login import LoginManager, login_user
from jinja2 import ChoiceLoader, DictLoader
from psycopg2 import sql as psql
from psycopg2.extras import RealDictCursor
from werkzeug.routing.exceptions import BuildError

import db as dbmod
import routes.giris_routes as gr
from auth import User, login_manager
from routes.giris_routes import bp

DSN = os.environ.get("BOE_COGALT_DSN", "host=127.0.0.1 port=54329 dbname=postgres user=postgres")
MID = 867
FAILS = []
GIZLI = {
    "tax_number", "phone", "email", "ev_adres", "notes", "vergi_no", "yetkili_tcno",
    "yetkili_tel", "yetkili_tel2", "yetkili_email", "yetkili_ikametgah",
}


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def _fark_adlari(once, sonra):
    adlar = []
    for k in once:
        if once.get(k) != sonra.get(k):
            adlar.append(k if k not in GIZLI else "(gizli)")
    return adlar


def main():
    dbmod.get_conn = lambda: (_ for _ in ()).throw(RuntimeError("canli baglanti yok"))
    dbmod.ensure_musteri_kyc_uyruk_column = lambda: None
    try:
        conn = psycopg2.connect(DSN)
    except Exception as exc:
        print("FAIL yerel pg yok", type(exc).__name__)
        raise SystemExit(1)
    conn.cursor_factory = RealDictCursor
    aktif = {"sema": "public"}

    def _sema():
        try:
            from flask import has_request_context

            if has_request_context():
                ad = getattr(g, "tenant_schema", None)
                if ad:
                    return ad
        except Exception:
            pass
        return aktif["sema"]

    @contextmanager
    def local_db():
        hedef = _sema() or "public"
        if hedef not in ("public", "tenant_cogalt_b"):
            raise RuntimeError("sema")
        cur = conn.cursor()
        cur.execute(psql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(psql.Identifier(hedef)))
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def local_fetch_one(metin, params=()):
        with local_db() as c:
            cur = c.cursor()
            cur.execute(metin, params or ())
            row = cur.fetchone()
            return dict(row) if row else None

    def local_fetch_all(metin, params=()):
        with local_db() as c:
            cur = c.cursor()
            cur.execute(metin, params or ())
            return [dict(r) for r in cur.fetchall()]

    def local_execute(metin, params=()):
        with local_db() as c:
            cur = c.cursor()
            cur.execute(metin, params or ())
            return cur.rowcount

    gr.fetch_one = local_fetch_one
    gr.fetch_all = local_fetch_all
    gr.execute = local_execute
    gr.get_db = local_db
    gr._giris_cogalt_schema_ready = True
    gr._giris_kaydet_schema_ready = True
    gr._upsert_aylik_grid_cache = lambda *a, **k: None

    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS tenant_cogalt_b CASCADE")
    cur.execute("CREATE SCHEMA tenant_cogalt_b")
    for sema in ("public", "tenant_cogalt_b"):
        cur.execute(psql.SQL("DROP TABLE IF EXISTS {}.sozlesme_plan_dondurma CASCADE").format(psql.Identifier(sema)))
        cur.execute(psql.SQL("DROP TABLE IF EXISTS {}.sozlesme_plan_degisiklik CASCADE").format(psql.Identifier(sema)))
        cur.execute(psql.SQL("DROP TABLE IF EXISTS {}.tahsilatlar CASCADE").format(psql.Identifier(sema)))
        cur.execute(psql.SQL("DROP TABLE IF EXISTS {}.faturalar CASCADE").format(psql.Identifier(sema)))
        cur.execute(psql.SQL("DROP TABLE IF EXISTS {}.musteri_kyc CASCADE").format(psql.Identifier(sema)))
        cur.execute(psql.SQL("DROP TABLE IF EXISTS {}.customers CASCADE").format(psql.Identifier(sema)))
        cur.execute(
            psql.SQL(
                """
                CREATE TABLE {}.customers (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    musteri_adi TEXT,
                    musteri_no INTEGER UNIQUE,
                    tax_number TEXT,
                    phone TEXT,
                    email TEXT,
                    address TEXT,
                    ev_adres TEXT,
                    notes TEXT,
                    durum TEXT,
                    kapanis_tarihi DATE,
                    kapanis_sonrasi_borc_ay INTEGER,
                    is_active BOOLEAN,
                    bizim_hesap BOOLEAN,
                    grup2_secimleri TEXT[],
                    calisma_sekli TEXT,
                    hazir_ofis_oda_no INTEGER,
                    office_code TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            ).format(psql.Identifier(sema))
        )
        cur.execute(
            psql.SQL(
                """
                CREATE TABLE {}.musteri_kyc (
                    id SERIAL PRIMARY KEY,
                    musteri_id INTEGER,
                    musteri_adi TEXT,
                    sirket_unvani TEXT,
                    unvan TEXT,
                    hizmet_turu TEXT,
                    aylik_kira NUMERIC(12,2),
                    yillik_kira NUMERIC(12,2),
                    kdv_oran NUMERIC(8,2),
                    kira_suresi_ay INTEGER,
                    kira_artis_tarihi DATE,
                    sozlesme_tarihi DATE,
                    sozlesme_bitis DATE,
                    kira_nakit BOOLEAN,
                    kira_banka BOOLEAN,
                    kira_nakit_tutar NUMERIC(14,2),
                    kira_banka_tutar NUMERIC(14,2),
                    duzenli_fatura TEXT,
                    odeme_duzeni TEXT,
                    odeme_duzeni_manuel TEXT,
                    sube_merkez TEXT,
                    yeni_adres TEXT,
                    faaliyet_konusu TEXT,
                    nace_kodu TEXT,
                    uyruk TEXT,
                    vergi_no TEXT,
                    vergi_dairesi TEXT,
                    yetkili_adsoyad TEXT,
                    yetkili_tcno TEXT,
                    yetkili_tel TEXT,
                    yetkili_email TEXT,
                    yetkili_tel2 TEXT,
                    yetkili_ikametgah TEXT,
                    email TEXT,
                    sozlesme_no TEXT,
                    hazir_ofis_oda_no INTEGER,
                    notlar TEXT,
                    mersis_no TEXT,
                    ticaret_sicil_no TEXT
                )
                """
            ).format(psql.Identifier(sema))
        )
        for tablo in ("faturalar", "tahsilatlar", "sozlesme_plan_degisiklik", "sozlesme_plan_dondurma"):
            cur.execute(
                psql.SQL("CREATE TABLE {}.{} (id SERIAL PRIMARY KEY, musteri_id INTEGER, iz TEXT)").format(
                    psql.Identifier(sema), psql.Identifier(tablo)
                )
            )
    conn.commit()

    def ekle(sema, ad):
        aktif["sema"] = sema
        with local_db() as c:
            k = c.cursor()
            k.execute(
                """
                INSERT INTO customers (
                    id, name, musteri_adi, musteri_no, tax_number, phone, email, address, ev_adres, notes,
                    durum, is_active, calisma_sekli, hazir_ofis_oda_no, office_code, bizim_hesap
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'aktif', TRUE, 'cari', 210, 'OFIS', FALSE
                )
                """,
                (MID, ad, ad + " kisa", MID, "1000000001", "5550000001", "kaynak@ornek.test", "Is adresi", "Ev adresi", "not kaynak"),
            )
            k.execute(
                """
                INSERT INTO musteri_kyc (
                    musteri_id, musteri_adi, sirket_unvani, hizmet_turu, aylik_kira, kdv_oran, kira_suresi_ay,
                    sozlesme_tarihi, sube_merkez, yeni_adres, faaliyet_konusu, uyruk,
                    vergi_no, vergi_dairesi, yetkili_adsoyad, yetkili_tcno, yetkili_tel, yetkili_tel2,
                    yetkili_email, yetkili_ikametgah, email, sozlesme_no, hazir_ofis_oda_no, notlar, mersis_no, ticaret_sicil_no
                ) VALUES (
                    %s, %s, %s, 'Hazir Ofis', 3600, 20, 12,
                    DATE '2024-01-01', 'Merkez', 'Is adresi', 'Danismanlik', 'TC',
                    %s, 'Test Daire', 'Yetkili Ad', %s, %s, '5550000009',
                    %s, 'Ev adresi', %s, 'SOZ-1', 210, 'kyc not', '0000000000000001', 'TS-1'
                )
                """,
                (MID, ad, ad, "1000000001", "10000000146", "5550000001", "kaynak@ornek.test", "sirket@ornek.test"),
            )
            for tablo in ("faturalar", "tahsilatlar", "sozlesme_plan_degisiklik", "sozlesme_plan_dondurma"):
                k.execute("INSERT INTO %s (musteri_id, iz) VALUES (%%s, 'kaynak')" % tablo, (MID,))
            k.execute("SELECT setval('customers_id_seq', %s, true)", (MID,))
        conn.commit()

    ekle("public", "Kaynak Unvan")
    ekle("tenant_cogalt_b", "Kiraci Unvan")

    app = Flask(__name__)
    app.secret_key = "cogalt-yerel"
    LoginManager().init_app(app)

    def yanit(fn):
        rv = fn()
        if isinstance(rv, tuple):
            resp, kod = rv
            govde = resp.get_json(silent=True) or {}
            return govde, kod
        return (rv.get_json(silent=True) or {}), rv.status_code

    def cagir(sema, yol, govde, fn):
        aktif["sema"] = sema or "public"
        with app.test_request_context(yol, method="POST", json=govde):
            login_user(User(3, "deneme", "Deneme", "admin"))
            if sema and sema != "public":
                g.tenant_schema = sema
            return yanit(fn)

    def satir(sema, mid):
        aktif["sema"] = sema
        return local_fetch_one("SELECT * FROM customers WHERE id = %s", (mid,))

    def cocuk(sema, mid):
        aktif["sema"] = sema
        toplam = 0
        for tablo in ("faturalar", "tahsilatlar", "sozlesme_plan_degisiklik", "sozlesme_plan_dondurma"):
            row = local_fetch_one("SELECT COUNT(*) AS n FROM %s WHERE musteri_id = %%s" % tablo, (mid,))
            toplam += int(row["n"])
        return toplam

    def vergi_indeks(kur=True):
        """Yalnızca çakışma denemesi. Uygulama şemasında bu indeks yok."""
        aktif["sema"] = "public"
        with local_db() as c:
            k = c.cursor()
            if kur:
                k.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_pub_tax ON customers (tax_number) "
                    "WHERE tax_number IS NOT NULL AND tax_number <> ''"
                )
            else:
                k.execute("DROP INDEX IF EXISTS uq_pub_tax")
        conn.commit()

    def dolu_esit(a, b, kolonlar):
        return all(
            (a or {}).get(k) == (b or {}).get(k) and (a or {}).get(k) not in (None, "")
            for k in kolonlar
        )

    def hepsi_bos(satir_row, kolonlar):
        return all((satir_row or {}).get(k) in (None, "") for k in kolonlar)

    once = satir("public", MID)
    govde, kod = cagir("public", "/giris/api/musteri-cogalt", {"kaynak_id": MID}, gr.api_musteri_cogalt)
    yeni = int(govde.get("id") or 0)
    check("cogalt 200 ayri id", kod == 200 and yeni and yeni != MID, kod)
    sonra = satir("public", MID)
    check("kaynak satir ayni", once == sonra, _fark_adlari(once, sonra))
    kopya = satir("public", yeni)
    kyc_kaynak = local_fetch_one("SELECT * FROM musteri_kyc WHERE musteri_id = %s", (MID,))
    kyc = local_fetch_one("SELECT * FROM musteri_kyc WHERE musteri_id = %s", (yeni,))
    check(
        "kisi alanlari kopyali",
        dolu_esit(kopya, once, ("name", "musteri_adi", "tax_number", "phone", "email", "ev_adres", "address"))
        and dolu_esit(
            kyc,
            kyc_kaynak,
            (
                "vergi_no",
                "vergi_dairesi",
                "yetkili_adsoyad",
                "yetkili_tcno",
                "yetkili_tel",
                "yetkili_tel2",
                "yetkili_email",
                "yetkili_ikametgah",
                "email",
                "sirket_unvani",
            ),
        )
        and kopya.get("calisma_sekli") == "cari"
        and kyc.get("hizmet_turu") == "Hazir Ofis"
        and str(kyc.get("aylik_kira")) in ("3600", "3600.00")
        and int(kopya.get("musteri_no") or 0) == yeni,
    )
    check(
        "sirket alanlari bos",
        hepsi_bos(kopya, ("notes", "hazir_ofis_oda_no", "office_code"))
        and hepsi_bos(kyc, ("sozlesme_no", "hazir_ofis_oda_no", "notlar", "mersis_no", "ticaret_sicil_no")),
    )
    check("cocuk tablolar kopyalanmadi", cocuk("public", yeni) == 0 and cocuk("public", MID) == 4, (cocuk("public", yeni), cocuk("public", MID)))

    kaydet_govde = {
        "id": yeni,
        "name": "Yeni Unvan",
        "musteri_adi": "Yeni Kisa",
        "tax_number": "2000000002",
        "yetkili_tc": "10000000146",
        "phone": "5550000002",
        "email": "yeni@ornek.test",
        "address": "Yeni adres",
        "ev_adres": "Yeni ev",
        "notes": "",
        "durum": "aktif",
        "calisma_sekli": "cari",
    }
    g2, k2 = cagir("public", "/giris/kaydet", kaydet_govde, gr.kaydet)
    check("kaydet yeni karta", k2 == 200 and g2.get("ok") is True and int(g2.get("id") or yeni) == yeni, k2)
    yeni_son = satir("public", yeni)
    kaynak_son = satir("public", MID)
    check("yeni unvan kalici", (yeni_son or {}).get("name") == "Yeni Unvan" and (yeni_son or {}).get("address") == "Yeni adres")
    check("kaynak kaydetten sonra ayni", kaynak_son == once, _fark_adlari(once, kaynak_son) if kaynak_son and once else "yok")

    vergi_indeks(True)
    cakisma = dict(kaydet_govde)
    cakisma["tax_number"] = once.get("tax_number")
    g3, k3 = cagir("public", "/giris/kaydet", cakisma, gr.kaydet)
    check("vergi cakismasi 409", k3 == 409 and g3.get("ok") is False and "vergi" in (g3.get("mesaj") or "").lower(), k3)
    check("cakisma kaynagi bozmadi", satir("public", MID) == once)
    vergi_indeks(False)

    public_ids = [r["id"] for r in local_fetch_all("SELECT id FROM customers ORDER BY id")]
    g4, k4 = cagir("tenant_cogalt_b", "/giris/api/musteri-cogalt", {"kaynak_id": MID}, gr.api_musteri_cogalt)
    kiraci_yeni = int(g4.get("id") or 0)
    check("kiraci cogalt 200", k4 == 200 and kiraci_yeni and kiraci_yeni != MID, k4)
    aktif["sema"] = "tenant_cogalt_b"
    kiraci_satir = satir("tenant_cogalt_b", kiraci_yeni)
    kiraci_kaynak = satir("tenant_cogalt_b", MID)
    check(
        "kiraci kisi kopyali",
        kiraci_satir
        and kiraci_satir.get("name") == "Kiraci Unvan"
        and dolu_esit(kiraci_satir, kiraci_kaynak, ("tax_number", "phone", "email"))
        and hepsi_bos(kiraci_satir, ("hazir_ofis_oda_no", "office_code", "notes")),
    )
    aktif["sema"] = "public"
    public_ids_2 = [r["id"] for r in local_fetch_all("SELECT id FROM customers ORDER BY id")]
    check("kiraci public e sizmaz", public_ids_2 == public_ids, public_ids_2)
    aktif["sema"] = "tenant_cogalt_b"
    kiraci_ayni_id = satir("tenant_cogalt_b", yeni)
    check(
        "public kaydi kiraci semasinda degil",
        kiraci_ayni_id is None or kiraci_ayni_id.get("name") != "Yeni Unvan",
    )
    aktif["sema"] = "public"

    ui(app, local_fetch_one, vergi_indeks)
    vergi_indeks(True)
    once_blok = satir("public", MID)
    n_blok = local_fetch_one("SELECT COUNT(*) AS n FROM customers")
    g5, k5 = cagir("public", "/giris/api/musteri-cogalt", {"kaynak_id": MID}, gr.api_musteri_cogalt)
    n_blok_2 = local_fetch_one("SELECT COUNT(*) AS n FROM customers")
    mesaj5 = (g5.get("mesaj") or "").lower()
    check(
        "tekil indeks cogalt 409",
        k5 == 409 and "vergi" in mesaj5 and "kopyalanamad" in mesaj5 and int((n_blok or {}).get("n") or 0) == int((n_blok_2 or {}).get("n") or -1),
        k5,
    )
    check("tekil indeks kaynak ayni", satir("public", MID) == once_blok, _fark_adlari(once_blok, satir("public", MID) or {}))
    conn.close()
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("musteri_cogalt ok")


def ui(app, fetch_one, vergi_indeks=None):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("SKIP playwright yok")
        check("playwright", False, "yok")
        return
    app.register_blueprint(bp, url_prefix="/giris")
    login_manager.init_app(app)
    _uf = app.jinja_env.globals.get("url_for")

    def uf(endpoint, **values):
        try:
            if _uf:
                return _uf(endpoint, **values)
        except BuildError:
            pass
        return "/yok/" + str(endpoint)

    app.jinja_env.globals["url_for"] = uf
    app.jinja_env.globals["has_module"] = lambda k: True
    app.jinja_env.globals["is_ledger_only"] = lambda *a, **k: False
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    import auth as authmod

    class _Kullanici:
        is_authenticated = True
        is_active = True
        is_anonymous = False
        id = 3
        username = "deneme"

        def get_id(self):
            return "3"

    authmod.current_user = _Kullanici()
    app.jinja_env.loader = ChoiceLoader([DictLoader({"giris/index.html": html}), app.jinja_env.loader])
    client = app.test_client()
    yazilan = []

    def musteri_json(mid):
        row = fetch_one("SELECT * FROM customers WHERE id = %s", (mid,))
        kyc = fetch_one("SELECT * FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1", (mid,))
        if not row:
            return {"ok": False}, 404
        m = {
            "ok": True,
            "musteri": {
                "id": row["id"],
                "musteri_no": row.get("musteri_no"),
                "name": row.get("name") or "",
                "musteri_adi": row.get("musteri_adi") or "",
                "tax_number": row.get("tax_number") or "",
                "phone": row.get("phone") or "",
                "email": row.get("email") or "",
                "address": row.get("address") or "",
                "ev_adres": row.get("ev_adres") or "",
                "notes": row.get("notes") or "",
                "calisma_sekli": row.get("calisma_sekli") or "sirali",
                "hizmet_turu": (kyc or {}).get("hizmet_turu") or "",
                "vergi_dairesi": (kyc or {}).get("vergi_dairesi") or "",
                "yetkili_ad": (kyc or {}).get("yetkili_adsoyad") or "",
                "yetkili_tc": (kyc or {}).get("yetkili_tcno") or "",
                "sube_merkez": (kyc or {}).get("sube_merkez") or "Merkez",
                "aylik_kira": str((kyc or {}).get("aylik_kira") or ""),
                "guncel_kira_bedeli": str((kyc or {}).get("aylik_kira") or ""),
                "sozlesme_baslangic": "2024-01-01",
            },
        }
        return m, 200

    def isle(route, request):
        u = request.url.split("plan.test")[-1]
        yol = u.split("?")[0]
        if yol == "/giris/api/musteri-cogalt" and request.method == "POST":
            govde = json.loads(request.post_data or "{}")
            with app.test_request_context(yol, method="POST", json=govde):
                login_user(User(3, "deneme", "Deneme", "admin"))
                paket = gr.api_musteri_cogalt()
            resp = paket[0] if isinstance(paket, tuple) else paket
            kod = paket[1] if isinstance(paket, tuple) else 200
            route.fulfill(status=kod, content_type="application/json", body=resp.get_data(as_text=True))
            return
        if yol == "/giris/kaydet" and request.method == "POST":
            govde = json.loads(request.post_data or "{}")
            yazilan.append(govde)
            with app.test_request_context(yol, method="POST", json=govde):
                login_user(User(3, "deneme", "Deneme", "admin"))
                paket = gr.kaydet()
            resp = paket[0] if isinstance(paket, tuple) else paket
            kod = paket[1] if isinstance(paket, tuple) else 200
            route.fulfill(status=kod, content_type="application/json", body=resp.get_data(as_text=True))
            return
        if yol.startswith("/giris/api/musteri/") and request.method == "GET" and yol[len("/giris/api/musteri/") :].isdigit():
            mid = int(yol.rsplit("/", 1)[-1])
            m, kod = musteri_json(mid)
            route.fulfill(status=kod, content_type="application/json", body=json.dumps(m))
            return
        if yol.startswith("/musteriler/api/kyc/kaydet"):
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))
            return
        if yol.startswith("/giris/api/"):
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "liste": [], "musteriler": [], "map": {}, "aylar": []}))
            return
        if yol.startswith("/static/"):
            hedef = ROOT / yol[len("/static/") :]
            if hedef.is_file():
                route.fulfill(status=200, body=hedef.read_bytes())
                return
        r = client.get(u)
        route.fulfill(status=r.status_code, content_type=r.content_type or "text/html", body=r.get_data())

    with sync_playwright() as pw:
        try:
            tarayici = pw.chromium.launch(channel="msedge", headless=True)
        except Exception as exc:
            print("SKIP tarayici", type(exc).__name__)
            check("playwright tarayici", False)
            return
        s = tarayici.new_page(viewport={"width": 1400, "height": 900})
        diyalog = []
        s.on("dialog", lambda d: (diyalog.append(d.message[:80]), d.accept()))
        s.route("**/*", isle)
        s.goto("http://plan.test/giris/", wait_until="load")
        s.wait_for_function("typeof girisMusteriCogalt === 'function' && typeof selectMusteri === 'function'", timeout=20000)
        if "function switchTab" in html:
            s.evaluate("switchTab(6)")
        s.evaluate("selectMusteri(%d)" % MID)
        s.wait_for_function("document.getElementById('musteri-id').value === '%d'" % MID, timeout=8000)
        kaynak_ad = s.evaluate("document.getElementById('name').value")
        kaynak_form = s.evaluate(
            """() => ({
                tax: document.getElementById('tax_number').value,
                phone: document.getElementById('phone').value,
                email: document.getElementById('email').value,
                ev: document.getElementById('ev_adres').value,
                yetkili: (document.getElementById('yetkili_ad') || {}).value || '',
                daire: (document.getElementById('vergi_dairesi') || {}).value || ''
            })"""
        )
        s.evaluate("document.querySelector('.giris-btn-cogalt').click()")
        s.wait_for_function(
            "() => { const v = document.getElementById('musteri-id').value; return v && v !== '%d'; }" % MID,
            timeout=8000,
        )
        yeni_ui = s.evaluate("document.getElementById('musteri-id').value")
        form_kopya = s.evaluate(
            """() => ({
                tax: document.getElementById('tax_number').value,
                phone: document.getElementById('phone').value,
                email: document.getElementById('email').value,
                ev: document.getElementById('ev_adres').value,
                yetkili: (document.getElementById('yetkili_ad') || {}).value || '',
                daire: (document.getElementById('vergi_dairesi') || {}).value || '',
                mersis: (document.getElementById('mersis') || {}).value || '',
                ticaret: (document.getElementById('ticaret_sicil') || {}).value || '',
                oda: (document.getElementById('hazir_ofis_oda_no') || {}).value || ''
            })"""
        )
        uyari = s.evaluate(
            """() => {
                const el = document.getElementById('musteri-cogalt-uyari');
                if (!el) return {var: false, yazi: false};
                const st = getComputedStyle(el);
                const yazi = el.textContent || '';
                return {
                    var: st.display !== 'none' && st.visibility !== 'hidden',
                    yazi: yazi.indexOf('çoğaltıldı') >= 0 && yazi.indexOf('vergi') >= 0
                };
            }"""
        )
        check("ui yeni id", yeni_ui and yeni_ui != str(MID), yeni_ui)
        check(
            "ui kisi kopyali",
            form_kopya.get("tax") == kaynak_form.get("tax")
            and form_kopya.get("tax")
            and form_kopya.get("phone") == kaynak_form.get("phone")
            and form_kopya.get("email") == kaynak_form.get("email")
            and form_kopya.get("ev") == kaynak_form.get("ev")
            and form_kopya.get("yetkili") == kaynak_form.get("yetkili")
            and form_kopya.get("daire") == kaynak_form.get("daire")
            and form_kopya.get("yetkili")
            and form_kopya.get("daire"),
        )
        check(
            "ui sirket bos",
            form_kopya.get("mersis") == "" and form_kopya.get("ticaret") == "" and form_kopya.get("oda") == "",
        )
        check("ui uyari gorunur", uyari.get("var") and uyari.get("yazi"))
        s.evaluate(
            """(ad) => {
                document.getElementById('name').value = ad;
                document.getElementById('musteri_adi').value = ad;
                document.getElementById('tax_number').value = '2000000003';
                document.getElementById('vergi_dairesi').value = 'Test';
                document.getElementById('yetkili_ad').value = 'Yetkili';
                document.getElementById('yetkili_tc').value = '10000000146';
                document.getElementById('phone').value = '5550000003';
                document.getElementById('email').value = 'yeni2@ornek.test';
                document.getElementById('address').value = 'Yeni adres 2';
                document.getElementById('ev_adres').value = 'Ev 2';
                const sube = document.getElementById('sube_merkez');
                if (sube) sube.value = 'Merkez';
                const h = document.getElementById('hizmet_turu');
                if (h && !h.value && h.options.length > 1) h.selectedIndex = 1;
                const kira = document.getElementById('aylik_kira');
                if (kira && !kira.value) kira.value = '3600';
                if (typeof girisYetkililerDogrula === 'function') {
                    window.girisYetkililerDogrula = function () { return { ok: true }; };
                }
            }""",
            "Yeni Unvan UI",
        )
        s.evaluate("document.getElementById('musteri-kaydet-btn').click()")
        s.wait_for_function(
            """() => {
                const el = document.getElementById('musteri-cogalt-uyari');
                return el && getComputedStyle(el).display === 'none';
            }""",
            timeout=8000,
        )
        check("ui uyari kaydet sonrasi yok", True)
        check("ui kaydet govdesi yeni id", yazilan and str(yazilan[-1].get("id")) == str(yeni_ui), (yazilan[-1].get("id") if yazilan else None, yeni_ui))
        check("ui kaydet unvani yeni", yazilan and yazilan[-1].get("name") == "Yeni Unvan UI", yazilan[-1].get("name") if yazilan else None)
        check("ui kaynak unvani govdede degil", yazilan and yazilan[-1].get("name") != kaynak_ad)
        if vergi_indeks:
            vergi_indeks(True)
        s.evaluate("document.getElementById('tax_number').value = %s" % json.dumps(kaynak_form.get("tax") or ""))
        s.evaluate("document.getElementById('musteri-kaydet-btn').click()")
        s.wait_for_function(
            """() => {
                const el = document.getElementById('musteri-kaydet-durum');
                if (!el || el.style.display === 'none') return false;
                return (el.textContent || '').toLowerCase().indexOf('vergi') >= 0;
            }""",
            timeout=8000,
        )
        kutu = s.evaluate(
            """() => {
                const el = document.getElementById('musteri-kaydet-durum');
                const st = getComputedStyle(el);
                return {display: st.display, renk: st.borderTopColor || ''};
            }"""
        )
        check("ui cakisma kirmizi kutu", kutu.get("display") == "block" and "229" in (kutu.get("renk") or ""))
        check("ui cakisma formu ayni id", s.evaluate("document.getElementById('musteri-id').value") == str(yeni_ui))
        kaynak_son = fetch_one("SELECT name FROM customers WHERE id = %s", (MID,))
        check("ui cakisma kaynagi ayni", (kaynak_son or {}).get("name") == "Kaynak Unvan")
        s.evaluate("selectMusteri(%s)" % yeni_ui)
        s.wait_for_function("document.getElementById('name').value === 'Yeni Unvan UI'", timeout=8000)
        check("ui yeniden ac unvan kalici", s.evaluate("document.getElementById('name').value") == "Yeni Unvan UI")
        check("ui kaynak id formu terk etti", s.evaluate("document.getElementById('musteri-id').value") == str(yeni_ui))
        s.evaluate("selectMusteri(%d)" % MID)
        s.wait_for_function(
            "document.getElementById('musteri-id').value === '%d' && document.getElementById('name').value === 'Kaynak Unvan'" % MID,
            timeout=8000,
        )
        s.evaluate(
            """() => {
                document.getElementById('tax_number').value = '1000000001';
                document.getElementById('vergi_dairesi').value = 'Test';
                document.getElementById('yetkili_ad').value = 'Yetkili';
                document.getElementById('yetkili_tc').value = '10000000146';
                document.getElementById('phone').value = '5550000001';
                document.getElementById('email').value = 'kaynak@ornek.test';
                document.getElementById('address').value = 'Is adresi';
                document.getElementById('ev_adres').value = 'Ev adresi';
                const sube = document.getElementById('sube_merkez');
                if (sube) sube.value = 'Merkez';
                const h = document.getElementById('hizmet_turu');
                if (h && !h.value && h.options.length > 1) h.selectedIndex = 1;
                const kira = document.getElementById('aylik_kira');
                if (kira && !kira.value) kira.value = '3600';
                window.girisYetkililerDogrula = function () { return { ok: true }; };
            }"""
        )
        onceki = len(yazilan)
        s.fill("#name", "Normal Kart Unvan")
        s.click("#musteri-kaydet-btn")
        s.wait_for_timeout(800)
        normal = yazilan[-1] if len(yazilan) > onceki else {}
        check(
            "normal kart govde yeni unvan",
            normal.get("name") == "Normal Kart Unvan" and str(normal.get("id")) == str(MID),
            normal.get("name"),
        )
        s.evaluate("selectMusteri(%d)" % MID)
        s.wait_for_function("document.getElementById('name').value === 'Normal Kart Unvan'", timeout=8000)
        check("normal kart yeniden ac", s.evaluate("document.getElementById('name').value") == "Normal Kart Unvan")
        tarayici.close()


if __name__ == "__main__":
    main()
