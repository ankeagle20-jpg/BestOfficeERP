# -*- coding: utf-8 -*-
"""Plan dondur popup'i: gercek giris sayfasi, sahte API. Canli DB'ye yazmaz."""
import copy
import json
import os
import re
from datetime import date
from pathlib import Path

from flask import Flask
from jinja2 import ChoiceLoader, DictLoader
from werkzeug.routing.exceptions import BuildError
from playwright.sync_api import sync_playwright

import sozlesme_plan_api as api
from auth import login_manager
from routes.giris_routes import bp
from sozlesme_plan_dondurma import dondurma_onizleme
from test_grid_867_gercek import _musteri, fixture_867
from test_grid_gercek_sayfa import _kartlar
from test_plan_onizle_tarayici import _auth_yamala

ROOT = Path(__file__).resolve().parent
BASE = "http://dondurma.test"
EKRAN = Path(os.environ.get("PLAN_UI_EKRAN", r"C:\Users\MSI\plan_ui_ekran"))
MID = 867
AY = "2026-11"
FAILS = []

ZINCIR = {
    "sozlesme_tarihi": date(2023, 8, 1),
    "artis_tarihi": date(2023, 8, 1),
    "ay_sayisi": 60,
    "aylik_net": 1000,
    "kdv_oran": 20,
    "kira_nakit": False,
    "tufe": {},
}
SINIF = {
    "2026-12": {"sinif": "K1", "neden": "GİB"},
    "2027-01": {"sinif": "K2", "neden": "tahsilat"},
    "2027-02": {"sinif": "K0", "neden": "belirsiz"},
}


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _onizleme(bas="2026-11", n=4):
    y, m = int(str(bas)[:4]), int(str(bas)[5:7])
    return dondurma_onizleme(ZINCIR, [], date(y, m, 1), int(n), SINIF)


def _grid(fx, acik):
    g = copy.deepcopy(fx["grid"])
    if not acik:
        return g
    c = g["cache"]
    c["dondurma_var"] = True
    c["dondurulan_aylar"] = [AY]
    for a in c.get("aylar") or []:
        if (a.get("yil"), a.get("ay")) == (2026, 11):
            a["tutar_kdv_dahil"] = 0.0
            a["brut_tutar_kdv"] = 0.0
            a["kalan_tutar_kdv"] = 0.0
            a["tahsil_edildi"] = False
            a["kismi_tahsilat"] = False
    return g


def main():
    if os.environ.get("ALLOW_PROD_WRITE") == "1":
        print("FAIL ALLOW_PROD_WRITE acik")
        raise SystemExit(1)
    os.environ["PLAN_DEGISTIR_ENABLED"] = "1"
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = str(MID)
    fx = fixture_867("eski")
    fx["musteri"]["musteri"] = _musteri(MID)
    kayitlar = []
    seq = {"n": 1}
    app = Flask(__name__)
    app.secret_key = "dondurma-ui"
    login_manager.init_app(app)
    app.register_blueprint(bp, url_prefix="/giris")
    _auth_yamala()
    _uf = app.jinja_env.globals["url_for"]

    def uf(endpoint, **values):
        try:
            return _uf(endpoint, **values)
        except BuildError:
            return "/yok/" + str(endpoint)

    app.jinja_env.globals["url_for"] = uf
    app.jinja_env.globals["has_module"] = lambda k: True
    app.jinja_env.globals["is_ledger_only"] = lambda *a, **k: False
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    app.jinja_env.loader = ChoiceLoader([DictLoader({"giris/index.html": html}), app.jinja_env.loader])
    client = app.test_client()
    api._kapi_acik = lambda mid: int(mid) == MID

    def js(route, govde, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(govde, default=str))

    def acik_var():
        return any(not k.get("iptal_at") for k in kayitlar)

    def isle(route, request):
        u = request.url[len(BASE):]
        yol = u.split("?")[0]
        if "/plan/dondurma" in yol:
            if request.method == "GET":
                js(route, {"ok": True, "aktif": [k for k in kayitlar if not k.get("iptal_at")], "gecmis": list(kayitlar)})
                return
            govde = json.loads(request.post_data or "{}")
            if yol.endswith("/onizleme"):
                bas = str(govde.get("baslangic_ay") or "")[:7]
                if bas == "2099-01":
                    js(route, {"ok": False, "mesaj": "Bu aralik cakisiyor."}, 409)
                    return
                try:
                    n = int(govde.get("ay_sayisi") or 0)
                except (TypeError, ValueError):
                    n = 0
                if len(bas) < 7 or n < 1:
                    js(route, {"ok": False, "mesaj": "Aralık uyuşmuyor."}, 400)
                    return
                js(route, {"ok": True, "not": "Dondurma; grid, borçlandırma/tahsilat panelleri, ekstre ve ödeme tutarlarını 0 yapar. Cari kart özeti ve fatura satırları (borç kayıtları) eski tutarda kalır.", **_onizleme(bas, n)})
                return
            if yol.endswith("/iptal"):
                bulunan = None
                for k in kayitlar:
                    if str(k["id"]) in yol and not k.get("iptal_at"):
                        k["iptal_at"] = "2026-10-12T10:00:00"
                        k["iptal_eden"] = "test"
                        bulunan = k
                if not bulunan:
                    js(route, {"ok": False, "mesaj": "Açık dondurma yok."}, 404)
                    return
                js(route, {"ok": True, "dondurma": bulunan})
                return
            oniz = _onizleme()
            if str(govde.get("baslangic_ay") or "")[:7] != AY or int(govde.get("ay_sayisi") or 0) != 4:
                js(route, {"ok": False, "mesaj": "Aralık uyuşmuyor."}, 400)
                return
            satir = {
                "id": seq["n"],
                "musteri_id": MID,
                "baslangic_ay": "2026-11-01",
                "ay_sayisi": 4,
                "dondurulan_aylar": oniz["dondurulan_aylar"],
                "atlanan_aylar": oniz["atlanan_aylar"],
                "olusturan": "test",
                "created_at": "2026-10-12T10:00:00",
                "iptal_at": None,
                "iptal_eden": None,
            }
            seq["n"] += 1
            kayitlar.append(satir)
            js(route, {"ok": True, "dondurma": satir, "dondurulan_aylar": oniz["dondurulan_aylar"]})
            return
        if re.match(r"^/giris/api/musteri/(\d+)/plan$", yol):
            mid = int(re.match(r"^/giris/api/musteri/(\d+)/plan$", yol).group(1))
            if mid != MID:
                js(route, {"ok": False}, 404)
                return
            js(route, {"ok": True, "gecmis": [], "aktif": [], "sozlesme_baslangic": "2023-08"})
            return
        if yol.startswith("/giris/api/musteri-kart-bundle"):
            g = _grid(fx, acik_var())
            js(route, {"ok": True, "musteri_id": MID, "musteri": fx["musteri"], "tahsil_durum": fx["tahsil_durum"], "grid": g, "reel": fx["reel"]})
            return
        if yol.startswith("/giris/api/aylik-grid-cache"):
            js(route, _grid(fx, acik_var()))
            return
        if yol.startswith("/giris/api/aylik-tahsil-durum"):
            js(route, fx["tahsil_durum"])
            return
        if yol.startswith("/giris/api/reel-donem-tutarlar"):
            js(route, fx["reel"])
            return
        if yol.startswith("/giris/api/tahsilat-panel-detay"):
            js(route, {"ok": True, "by_iso": {}})
            return
        if re.match(r"^/giris/api/musteri/\d+$", yol):
            js(route, fx["musteri"])
            return
        if yol.startswith("/giris/api/"):
            js(route, {"ok": True, "liste": [], "musteriler": [], "map": {}, "aylar": []})
            return
        if yol.startswith("/static/js/plan_degistir.js"):
            route.fulfill(status=200, content_type="application/javascript; charset=utf-8", body=(ROOT / "static" / "js" / "plan_degistir.js").read_text(encoding="utf-8"))
            return
        r = client.get(u)
        route.fulfill(status=r.status_code, content_type=r.content_type or "text/html", body=r.get_data())

    EKRAN.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        try:
            tarayici = pw.chromium.launch(channel="msedge", headless=True)
        except Exception as exc:
            print("SKIP tarayici yok:", str(exc)[:80])
            return
        s = tarayici.new_page(viewport={"width": 1400, "height": 900})
        hatalar = []
        s.on("pageerror", lambda e: hatalar.append(str(e)[:200]))
        s.route("**/*", isle)
        s.goto(BASE + "/giris/", wait_until="load")
        s.wait_for_function("typeof selectMusteri === 'function'", timeout=20000)
        check("statik selectte dondur yok", "value=\"dondur\"" not in html and "js/plan_degistir.js', v=13" in html)
        s.evaluate("selectMusteri(%d)" % MID)
        s.wait_for_selector("#plan_dondur_btn", timeout=15000)
        for _ in range(3):
            s.evaluate("planKapisiniYenile(%d)" % MID)
        s.wait_for_timeout(800)
        check("dondurma gecmisi tek kap", s.evaluate("document.querySelectorAll('#dondurma_gecmis_bolum').length") == 1)
        check("plan dondur secenegi kapida", s.evaluate("document.querySelectorAll('#musteri_durum option[value=dondur]').length") == 1)
        once = (_kartlar(s).get("sozlesmeler-aylik-grid") or {}).get(AY) or {}
        once_metin = once.get("metin")
        check("grid baslangic tutari", once_metin not in (None, "", "0.00", "—"), once_metin)
        def gorunur_say(metin):
            return s.evaluate(
                """(metin) => [...document.querySelectorAll('#plan_degistir_modal button')].filter(b => (b.textContent || '').trim() === metin && b.offsetParent !== null).length""",
                metin,
            )

        def hata_gorunur():
            return s.evaluate(
                """() => {
                    const h = document.getElementById('dondurma_hata');
                    const kutu = document.querySelector('#plan_degistir_modal > div');
                    if (!h || !kutu || getComputedStyle(h).display === 'none' || !h.textContent) return false;
                    const hr = h.getBoundingClientRect();
                    const kr = kutu.getBoundingClientRect();
                    return hr.height > 0 && hr.top < kr.bottom && hr.bottom > kr.top;
                }"""
            )

        s.select_option("#musteri_durum", "dondur")
        s.wait_for_selector("#dondurma_bolum", state="visible", timeout=4000)
        durum = s.evaluate("document.getElementById('musteri_durum').value")
        check("dropdown dondur durumu degistirmez", durum == "aktif", durum)
        check("dondur basligi", s.evaluate("document.getElementById('plan_baslik_metin').textContent") == "Plan dondur")
        check("dondur modunda plan formu gizli", s.evaluate("getComputedStyle(document.getElementById('plan_form_bolum')).display") == "none")
        check("dondur modunda onizle tek", gorunur_say("Önizle") == 1, gorunur_say("Önizle"))
        check("dondur modunda kaydet tek", gorunur_say("Kaydet") == 1, gorunur_say("Kaydet"))
        check(
            "dondur modunda plan ipucu yok",
            s.evaluate("getComputedStyle(document.getElementById('plan_kaydet_ipucu')).display") == "none",
        )
        s.screenshot(path=str(EKRAN / "dondurma_dropdown_aktif.png"))
        s.evaluate("document.getElementById('plan_kapat').click()")
        s.click("#plan_degistir_btn")
        s.wait_for_function("getComputedStyle(document.getElementById('plan_form_bolum')).display !== 'none'", timeout=4000)
        check("plan basligi", s.evaluate("document.getElementById('plan_baslik_metin').textContent") == "Plan değiştir")
        check("plan modunda dondurma gizli", s.evaluate("getComputedStyle(document.getElementById('dondurma_bolum')).display") == "none")
        check("plan modunda onizle tek", gorunur_say("Önizle") == 1, gorunur_say("Önizle"))
        check("plan modunda kaydet tek", gorunur_say("Kaydet") == 1, gorunur_say("Kaydet"))
        check("gecmis kapsayici plan modunda da tek", s.evaluate("document.querySelectorAll('#dondurma_gecmis_bolum').length") == 1)
        s.screenshot(path=str(EKRAN / "plan_mod_ayri.png"))
        s.evaluate("document.getElementById('plan_kapat').click()")
        s.click("#plan_dondur_btn")
        s.wait_for_function("getComputedStyle(document.getElementById('dondurma_bolum')).display !== 'none'", timeout=4000)
        s.screenshot(path=str(EKRAN / "dondurma_mod_ayri.png"))
        s.evaluate(
            """() => {
                document.getElementById('dondurma_bas').value = '';
                document.getElementById('dondurma_sure').value = '';
                document.getElementById('dondurma_bas').dispatchEvent(new Event('input'));
                document.getElementById('dondurma_kaydet').click();
            }"""
        )
        s.wait_for_function("document.getElementById('dondurma_hata').textContent.indexOf('Başlangıç ayı') >= 0", timeout=4000)
        check("bos alan uyarisi gorunur", hata_gorunur() and "Başlangıç ayı" in s.evaluate("document.getElementById('dondurma_hata').textContent"))
        check("bos alan kayit yok", not kayitlar)
        s.evaluate(
            """() => {
                document.getElementById('dondurma_bas').value = '2026-12';
                document.getElementById('dondurma_sure').value = '1';
                document.getElementById('dondurma_bas').dispatchEvent(new Event('input'));
                document.getElementById('dondurma_kaydet').click();
            }"""
        )
        s.wait_for_function("document.getElementById('dondurma_hata').textContent.indexOf('Dondurulacak ay yok') >= 0", timeout=6000)
        check("tum aylar atlaninca uyari", hata_gorunur())
        check("tum aylar atlaninca kayit yok", not kayitlar)
        s.evaluate("document.getElementById('dondurma_kaydet').click()")
        s.wait_for_timeout(500)
        check("atlanan ayda ikinci kaydet de yazmaz", not kayitlar)
        s.evaluate(
            """() => {
                document.getElementById('dondurma_bas').value = '2099-01';
                document.getElementById('dondurma_sure').value = '1';
                document.getElementById('dondurma_bas').dispatchEvent(new Event('input'));
                document.getElementById('dondurma_kaydet').click();
            }"""
        )
        s.wait_for_function("document.getElementById('dondurma_hata').textContent.indexOf('cakisiyor') >= 0", timeout=6000)
        check("api hata popup icinde", hata_gorunur() and "cakisiyor" in s.evaluate("document.getElementById('dondurma_hata').textContent"))
        s.screenshot(path=str(EKRAN / "dondurma_kaydet_hata.png"))
        s.evaluate(
            """() => {
                document.getElementById('dondurma_bas').value = '2026-11';
                document.getElementById('dondurma_sure').value = '4';
                document.getElementById('dondurma_bas').dispatchEvent(new Event('input'));
                document.getElementById('dondurma_kaydet').click();
            }"""
        )
        s.wait_for_function("document.querySelectorAll('#dondurma_onizleme tr').length >= 4", timeout=6000)
        tablo = s.evaluate("document.getElementById('dondurma_onizleme').innerText")
        uyari = s.evaluate("document.getElementById('dondurma_uyari').innerText")
        check("onizleme dondurulacak ay", "2026-11" in tablo and "dondurulacak" in tablo, tablo)
        check("onizleme K1 K2 K0", "K1 GİB" in tablo and "K2 tahsilat" in tablo and "K0 belirsiz" in tablo, tablo)
        check("onizleme uyari gorunur", "K1 GİB" in uyari and s.evaluate("getComputedStyle(document.getElementById('dondurma_uyari')).display") != "none", uyari)
        not_metin = s.evaluate("document.getElementById('dondurma_not').textContent")
        check(
            "dondurma notu",
            "borçlandırma/tahsilat panelleri" in not_metin and "eski tutarda kalır" in not_metin,
        )
        s.screenshot(path=str(EKRAN / "dondurma_popup_onizleme.png"))
        check("onizlemesiz ilk kaydet yalniz onizler", not kayitlar)
        s.evaluate("document.getElementById('dondurma_kaydet').click()")
        s.wait_for_function(
            """() => {
                const k = document.querySelector('#sozlesmeler-aylik-grid [data-ay="2026-11"], #sozlesmeler-aylik-grid');
                return !!k;
            }""",
            timeout=4000,
        )
        def kart_metin():
            return ((_kartlar(s).get("sozlesmeler-aylik-grid") or {}).get(AY) or {}).get("metin")

        for _ in range(20):
            if kart_metin() == "0.00":
                break
            s.wait_for_timeout(400)
        check("grid dondurulan ay 0", kart_metin() == "0.00", kart_metin())
        s.screenshot(path=str(EKRAN / "dondurma_grid_sifir.png"))
        check("acik dondurma tek", len([k for k in kayitlar if not k.get("iptal_at")]) == 1)
        s.evaluate("document.querySelector('#dondurma_gecmis_bolum button').click()")
        for _ in range(20):
            if kart_metin() == once_metin and not acik_var():
                break
            s.wait_for_timeout(400)
        check("dondurma kaldirildi", not acik_var(), kayitlar)
        check("grid eski tutar", kart_metin() == once_metin, (kart_metin(), once_metin))
        s.screenshot(path=str(EKRAN / "dondurma_grid_geri.png"))
        gecmis = s.evaluate("document.getElementById('dondurma_gecmis_bolum').innerText")
        soluk = s.evaluate(
            """() => {
                const satirlar = [...document.querySelectorAll('#dondurma_gecmis_bolum > div')].filter(e => e.textContent.indexOf('iptal') >= 0);
                if (!satirlar.length) return '';
                return getComputedStyle(satirlar[0]).opacity;
            }"""
        )
        check("iptal gecmiste soluk", "iptal" in gecmis and soluk == "0.45", (gecmis, soluk))
        check("gecmis kapsayici hala tek", s.evaluate("document.querySelectorAll('#dondurma_gecmis_bolum').length") == 1)
        s.evaluate("document.getElementById('musteri_durum').value = 'pasif'; dondurmaPasifUygula();")
        check("pasif yeni dondurma kapali", s.evaluate("document.getElementById('plan_dondur_btn').disabled") is True)
        check("pasif gecmis durur", s.evaluate("!!document.getElementById('dondurma_gecmis_bolum')"))
        s.evaluate("document.getElementById('musteri_durum').value = 'aktif'; dondurmaPasifUygula();")
        check("aktif dondurma geri", s.evaluate("document.getElementById('plan_dondur_btn').disabled") is False)
        check("sayfa hatasi yok", not hatalar, hatalar[:2])
        tarayici.close()
    if acik_var():
        check("test sonu acik dondurma yok", False, kayitlar)
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("plan_dondurma_ui ok")


if __name__ == "__main__":
    main()
