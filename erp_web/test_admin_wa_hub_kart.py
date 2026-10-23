# -*- coding: utf-8 -*-
"""Yönetim hub WhatsApp Sağlık kartı. Canlı yazma yok. Çıktıda kimlik yok."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)
os.environ["BESTOFFICE_HTTP_FILE_LOG"] = "0"

FAILS: list[str] = []


def check(name, ok):
    print(("ok " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def test_kart_kaynak():
    html = (ROOT / "templates" / "admin" / "yonetim.html").read_text(encoding="utf-8")
    i = html.find('id="hub-wa-saglik"')
    kart = html[html.rfind("<a ", 0, i): html.find("</a>", i) + 4] if i >= 0 else ""
    check("kart var", "WhatsApp Sağlık" in kart and "hub-card" in kart)
    check("kart aciklama", "oturumu kurtarma ve bekleyenleri yönetme" in kart)
    check("kart hedef", 'href="/whatsapp/saglik"' in kart and "Panele Git" in kart)
    check("kart etiket", "wa_servis_etiket" in kart and "Varsayılan servis" in kart)
    check("rozet yer", 'id="wa-saglik-rozet"' in kart and "bilinmiyor" in kart)
    hizli = html[html.find("Ek Yönetim Araçları"):]
    check("hizli sira", hizli.find("Kullanıcı Yönetimi") < hizli.find("Küresel Müşteri Portföyü")
          < hizli.find("Mükerrer Müşteri Arşivi") < hizli.find("Ana Dashboard"))
    once = html.find("Küresel Müşteri Portföyü")
    check("kart sonda", once >= 0 and once < i)
    sayfa = (ROOT / "templates" / "whatsapp" / "saglik.html").read_text(encoding="utf-8")
    check("sayfa baslik", "WhatsApp Sağlık — {{ wa_servis_etiket" in sayfa)
    check("sayfa govde", "wa_servis_etiket or 'Varsayılan servis'" in sayfa)
    ayar = (ROOT / "templates" / "ayarlar" / "index.html").read_text(encoding="utf-8")
    check("ayarlar link", 'href="/whatsapp/saglik">WhatsApp Sağlık</a>' in ayar)
    auth = (ROOT / "auth.py").read_text(encoding="utf-8")
    check("menu duruyor", '"id": "whatsapp", "label": "WhatsApp Sağlık"' in auth)
    js = (ROOT / "static" / "js" / "admin-wa-rozet.js").read_text(encoding="utf-8")
    check("rozet zaman", "3000" in js and "ctrl.abort" in js and 'boya("bilinmiyor")' in js)
    check("rozet uc", "/whatsapp/api/saglik-rozet" in js)
    hub = (ROOT / "routes" / "admin_routes.py").read_text(encoding="utf-8")
    govde = hub[hub.find("def yonetim_hub"): hub.find("def index")]
    check("hub blok yok", "requests" not in govde and "saglik-rozet" not in govde)


def test_rozet_mantik():
    import wa_saglik as ws

    check("yesil", ws.rozet_seviye("ok", "hazir", 0) == "yesil")
    check("qr sari", ws.rozet_seviye("ok", "qr", 0) == "sari")
    check("baglaniyor sari", ws.rozet_seviye("ok", "baglaniyor", 0) == "sari")
    check("kuyruk sari", ws.rozet_seviye("ok", "hazir", 2) == "sari")
    check("kopuk kirmizi", ws.rozet_seviye("baglanti_yok", None, 0) == "kirmizi")
    check("zaman kirmizi", ws.rozet_seviye("zaman_asimi", None, 0) == "kirmizi")
    check("bilinmiyor", ws.rozet_seviye("garip", None, 0) == "bilinmiyor")
    src = (ROOT / "wa_saglik.py").read_text(encoding="utf-8")
    fn = src[src.find("def rozet_takili_sayisi"): src.find("def rozet_seviye")]
    check("tek sayim", "COUNT(*)" in fn and "statement_timeout = '1000ms'" in fn
          and "saglik_sayaclari" not in fn)
    rota = (ROOT / "routes" / "whatsapp_routes.py").read_text(encoding="utf-8")
    govde = rota[rota.find("def api_wa_saglik_rozet"): rota.find("def api_wa_saglik_ozet")]
    check("kapali sayac yok", "saglik_sayaclari" not in govde and "timeout=(1, 1.5)" in govde)


ROL = {"v": "personel"}


def _istemci():
    from flask import Flask
    from auth import User, login_manager
    from routes import whatsapp_routes

    @login_manager.user_loader
    def _load(_uid):
        return User(id=1, username="t", full_name="T", role=ROL["v"],
                    aktif_mi=True, security_stamp="test-stamp")

    app = Flask(__name__)
    app.secret_key = "test"
    login_manager.init_app(app)

    @app.route("/")
    def index():
        return "home"

    app.register_blueprint(whatsapp_routes.bp)
    client = app.test_client()
    with client.session_transaction() as s:
        s["_user_id"] = "1"
        s["_fresh"] = True
    return client


def test_yetki_ve_rozet():
    import routes.whatsapp_routes as wr
    import wa_saglik as ws

    auth = (ROOT / "auth.py").read_text(encoding="utf-8")
    kap = auth[auth.find("def yetki_gerekli"): auth.find("def sifre_degistir")]
    check("ayni rol alani", '"admin"' in kap and "platform" not in kap)
    check("admin bypass", "| {\"admin\"}" in kap or '| {"admin"}' in kap)

    def durum_ok(timeout=(2, 3)):
        return "ok", "hazir", None

    def durum_kopuk(timeout=(2, 3)):
        return "zaman_asimi", None, None

    def durum_patlar(timeout=(2, 3)):
        raise RuntimeError("kopuk")

    cagri = {"sayac": 0, "eski": 0, "zaman": None}

    def sayac_sifir():
        cagri["sayac"] += 1
        return 0

    def sayac_yok():
        cagri["sayac"] += 1
        return None

    def eski_sayac():
        cagri["eski"] += 1
        return {"yasli_takili": 9}

    def durum_zaman(timeout=(2, 3)):
        cagri["zaman"] = timeout
        return "zaman_asimi", None, None

    def durum_qr(timeout=(2, 3)):
        return "ok", "qr", None

    eski_durum = wr._saglik_servis_durumu
    eski_takili = ws.rozet_takili_sayisi
    eski_tam = ws.saglik_sayaclari
    try:
        wr._rozet_onbellek_temizle()
        wr._saglik_servis_durumu = durum_ok
        ws.rozet_takili_sayisi = sayac_sifir
        ws.saglik_sayaclari = eski_sayac
        client = _istemci()
        ROL["v"] = "personel"
        r = client.get("/whatsapp/api/saglik-rozet")
        check("personel 403", r.status_code == 403)
        r = client.get("/whatsapp/api/saglik-ozet")
        check("personel ozet 403", r.status_code == 403)
        ROL["v"] = "admin"
        wr._rozet_onbellek_temizle()
        cagri["sayac"] = 0
        r = client.get("/whatsapp/api/saglik-rozet")
        g = r.get_json(silent=True) or {}
        check("platform admin yesil", r.status_code == 200 and g.get("seviye") == "yesil"
              and set(g) == {"ok", "seviye", "etiket"} and g.get("etiket") == "Varsayılan servis")
        r2 = client.get("/whatsapp/api/saglik-rozet")
        check("onbellek", cagri["sayac"] == 1 and (r2.get_json(silent=True) or {}).get("seviye") == "yesil")
        wr._rozet_onbellek_temizle()
        wr._saglik_servis_durumu = durum_zaman
        cagri["sayac"] = 0
        cagri["eski"] = 0
        r = client.get("/whatsapp/api/saglik-rozet")
        g = r.get_json(silent=True) or {}
        check("zaman asimi kirmizi", r.status_code == 200 and g.get("seviye") == "kirmizi"
              and cagri["zaman"] == (1, 1.5) and cagri["sayac"] == 0 and cagri["eski"] == 0)
        wr._rozet_onbellek_temizle()
        wr._saglik_servis_durumu = durum_patlar
        r = client.get("/whatsapp/api/saglik-rozet")
        g = r.get_json(silent=True) or {}
        check("hata bilinmiyor", r.status_code == 200 and g.get("seviye") == "bilinmiyor")
        wr._rozet_onbellek_temizle()
        wr._saglik_servis_durumu = durum_qr
        cagri["sayac"] = 0
        r = client.get("/whatsapp/api/saglik-rozet")
        g = r.get_json(silent=True) or {}
        check("qr sayacsiz sari", r.status_code == 200 and g.get("seviye") == "sari" and cagri["sayac"] == 0)
        wr._rozet_onbellek_temizle()
        wr._saglik_servis_durumu = durum_ok
        ws.rozet_takili_sayisi = sayac_yok
        r = client.get("/whatsapp/api/saglik-rozet")
        g = r.get_json(silent=True) or {}
        check("sayac hatasi yesil", r.status_code == 200 and g.get("seviye") == "yesil"
              and set(g) == {"ok", "seviye", "etiket"})

        def sahte_sayfa(_ad, **kw):
            return kw.get("wa_servis_etiket") or ""

        eski_render = wr.render_template
        wr.render_template = sahte_sayfa
        try:
            r = client.get("/whatsapp/saglik")
            check("admin sayfa", r.status_code == 200 and "Varsayılan servis" in (r.get_data(as_text=True) or ""))
            ROL["v"] = "personel"
            r = client.get("/whatsapp/saglik")
            check("personel sayfa yok", r.status_code != 200)
        finally:
            wr.render_template = eski_render
    finally:
        wr._saglik_servis_durumu = eski_durum
        wr._rozet_onbellek_temizle()
        ws.rozet_takili_sayisi = eski_takili
        ws.saglik_sayaclari = eski_tam


def test_js():
    r = subprocess.run(
        ["node", "--check", str(ROOT / "static" / "js" / "admin-wa-rozet.js")],
        capture_output=True, text=True,
    )
    check("node check", r.returncode == 0)


if __name__ == "__main__":
    test_kart_kaynak()
    test_rozet_mantik()
    test_yetki_ve_rozet()
    test_js()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
