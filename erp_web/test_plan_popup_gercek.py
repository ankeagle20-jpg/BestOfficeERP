# -*- coding: utf-8 -*-
"""Plan degistir POPUP'i: GERCEK giris/index.html + gercek Flask plan rotalari (sahte DB) uzerinde Playwright testi.

Kapsam: dropdown'dan popup, durum kaydina 'plan' gitmez, gecmis tek kopya (kart yukleme yollari tekrarlansa da),
buton her zaman gorunur, Onizle/Kaydet/ayni aya degistirme/Iptal/hata popup icinde, kirli formda ESC/dis tiklama onayi,
kapi kapali musteride buton+popup yok, musteri degisince eski popup/plan kalmaz, pasif musteri notu,
Grup/Grup 2 duzeni (rect karsilastirma) ve ekran goruntuleri (C:\\Users\\MSI\\plan_ui_ekran).
ESKI_REF ortam degiskeni (varsayilan d80e1c1 = popup oncesi): eski index+js ile ayni senaryo -> cift gecmis KUSURU yeniden uretilir.
Canli DB'ye ve .env'e gitmez."""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    print("SKIP playwright yok")
    raise SystemExit(0)

import db as dbmod  # noqa: E402
import sozlesme_plan_api as api  # noqa: E402
from test_grid_867_gercek import _musteri, fixture_diger  # noqa: E402
from test_plan_degistir import Tx, _app, _kur  # noqa: E402
from test_plan_onizle_tarayici import Durum, _auth_yamala  # noqa: E402

BASE = "http://popup.test"
EKRAN = Path(os.environ.get("PLAN_UI_EKRAN", r"C:\Users\MSI\plan_ui_ekran"))
ACIK, PASIF, KAPALI = 7, 9, 8  # plan kapisi: 7 ve 9 acik, 8 kapali
FAILS = []
ETIKET = [""]


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + ETIKET[0] + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(ETIKET[0] + name)


def _satir(i, mid, ay, net, brut):
    return {
        "id": i, "musteri_id": mid, "gecerlilik_ay": ay, "yeni_net": net, "kdv_oran": 20, "yeni_brut": brut,
        "nakit_tutar": None, "banka_tutar": net, "olusturan": "test", "created_at": None, "iptal_at": None, "iptal_eden": None,
    }


def _fx(mid):
    f = fixture_diger()
    m = _musteri(mid)
    if mid == PASIF:
        m["durum"] = "pasif"
        m["kapanis_tarihi"] = "2026-09-30"
    f["musteri"]["musteri"] = m
    f["grid"]["cache"]["musteri_id"] = mid
    return f


def _git_dosya(ref, yol, hedef):
    c = subprocess.run(["git", "show", "%s:%s" % (ref, yol)], cwd=str(ROOT.parent), capture_output=True)
    if c.returncode != 0:
        return None
    Path(hedef).write_bytes(c.stdout)
    return hedef


def rects(s):
    """form-grid icindeki id'li elemanlarin konumu (Grup/Grup 2 duzeni dahil)."""
    return s.evaluate(
        """() => { const o = {}; document.querySelectorAll('.form-grid [id]').forEach(e => {
            if (e.id === 'musteri_durum_satir') return;
            const r = e.getBoundingClientRect(); if (r.width || r.height) o[e.id] = [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; });
            return o; }"""
    )


def kosu(html_yol=None, js_yol=None, etiket="YENI", tam=True, ekran=False):
    """tam=False: yalniz cift gecmis senaryosu (eski surumde kusuru gostermek icin)."""
    ETIKET[0] = etiket + ": "
    import jinja2
    from werkzeug.routing.exceptions import BuildError

    tx = Tx()
    app = _app()
    app.logger.disabled = True
    _kur(tx)
    app.test_tx = tx
    api._resync = lambda mid: True
    api._bugun = lambda: date(2026, 10, 9)
    api._kilit_k3_aylari = lambda mid: []
    eski_zinc = api._onizleme_zinciri
    kyc0 = api._musteri_ve_kyc

    def zincir(mid, kyc):
        z, _m, k = eski_zinc(mid, kyc)
        z["ay_sayisi"] = 60
        z["faturali_aylar"] = list(Durum.faturali)
        acik = [dict(s) for s in tx.bellek.satirlar if s["iptal_at"] is None and int(s["musteri_id"]) == int(mid)]
        return z, acik, k

    api._onizleme_zinciri = zincir
    api._musteri_ve_kyc = lambda mid: (
        {"id": int(mid), "durum": "pasif" if int(mid) == PASIF else "aktif"},
        {"sozlesme_tarihi": date(2023, 8, 1), "aylik_kira": 1000, "kdv_oran": 20},
    )
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "%d,%d" % (ACIK, PASIF)
    os.environ["PLAN_DEGISTIR_ENABLED"] = "1"
    def plan_oku(sql, params=None):
        mid = int(params[0]) if params else None
        return [dict(x) for x in tx.bellek.satirlar if mid is None or int(x["musteri_id"]) == mid]

    dbmod.fetch_all = plan_oku
    dbmod.fetch_one = lambda *a, **k: None
    tx.bellek.satirlar.append(_satir(51, ACIK, date(2026, 12, 1), 1000, 1200))
    tx.bellek.satirlar.append(_satir(52, PASIF, date(2026, 12, 1), 900, 1080))
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
    if html_yol:
        app.jinja_env.loader = jinja2.ChoiceLoader(
            [jinja2.DictLoader({"giris/index.html": Path(html_yol).read_text(encoding="utf-8")}), app.jinja_env.loader]
        )
    client = app.test_client()
    yazilan = []  # GET olmayan istekler: (yontem, yol, govde)
    gorunen = []

    def js(route, govde, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(govde))

    def mid_of(yol):
        mm = re.search(r"musteri_id=(\d+)", yol) or re.search(r"/giris/api/musteri(?:-kart-bundle)?/(\d+)", yol)
        return int(mm.group(1)) if mm else ACIK

    def isle(route, request):
        u = request.url[len(BASE):]
        if u.startswith("/static/js/plan_degistir.js") and js_yol:
            route.fulfill(status=200, content_type="application/javascript; charset=utf-8", body=Path(js_yol).read_text(encoding="utf-8"))
            return
        if request.method != "GET" and u.startswith("/giris/kaydet"):
            yazilan.append((request.method, u, request.post_data))
            js(route, {"ok": True, "id": mid_of(request.post_data or "") if False else ACIK})
            return
        if re.match(r"^/giris/api/musteri/\d+/plan", u):
            gorunen.append((request.method, u, request.post_data))
            if request.method != "GET":
                yazilan.append((request.method, u, request.post_data))
            r = client.open(u, method=request.method, data=request.post_data_buffer if request.method != "GET" else None,
                            content_type=request.headers.get("content-type"))
            route.fulfill(status=r.status_code, content_type=r.content_type or "application/json", body=r.get_data())
            return
        mid = mid_of(u)
        f = _fx(mid)
        if u.startswith("/giris/api/musteri-kart-bundle"):
            js(route, {"ok": True, "musteri_id": mid, "musteri": f["musteri"], "tahsil_durum": f["tahsil_durum"], "grid": f["grid"], "reel": f["reel"]})
            return
        if u.startswith("/giris/api/aylik-grid-cache"):
            js(route, f["grid"])
            return
        if u.startswith("/giris/api/aylik-tahsil-durum"):
            js(route, f["tahsil_durum"])
            return
        if u.startswith("/giris/api/reel-donem-tutarlar"):
            js(route, f["reel"])
            return
        if u.startswith("/giris/api/tahsilat-panel-detay"):
            js(route, {"ok": True, "by_iso": {}})
            return
        if re.match(r"^/giris/api/musteri/\d+(\?.*)?$", u):
            js(route, f["musteri"])
            return
        if u.startswith("/giris/api/"):
            js(route, {"ok": True, "liste": [], "musteriler": [], "map": {}, "aylar": []})
            return
        r = client.get(u)
        route.fulfill(status=r.status_code, content_type=r.content_type or "text/html", body=r.get_data())

    def say(s, sel):
        return s.evaluate("document.querySelectorAll(%s).length" % json.dumps(sel))

    def gorunur(s, sel):
        return s.evaluate(
            "(() => { const e = document.querySelector(%s); if (!e) return false; const r = e.getBoundingClientRect(); const cs = getComputedStyle(e); "
            "return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0; })()" % json.dumps(sel)
        )

    def musteri_ac(s, mid, kapi_acik=True):
        s.evaluate("selectMusteri(%d)" % mid)
        if kapi_acik:
            s.wait_for_selector("#plan_gecmis_bolum", state="attached", timeout=15000)
            s.wait_for_function("document.querySelectorAll('#plan_gecmis_bolum button, #plan_gecmis_bolum div').length > 1", timeout=8000)
        else:
            s.wait_for_timeout(2500)

    def btn_tikla(s):
        s.evaluate("document.getElementById('plan_degistir_btn').scrollIntoView({block: 'center'})")
        s.click("#plan_degistir_btn")

    def popup_acik(s):
        return gorunur(s, "#plan_degistir_modal")

    diyalog = []
    with sync_playwright() as pw:
        try:
            tarayici = pw.chromium.launch(channel="msedge", headless=True)
        except Exception as exc:  # pragma: no cover
            print("SKIP tarayici yok:", str(exc)[:80])
            return None
        s = tarayici.new_page(viewport={"width": 1400, "height": 900})
        hatalar = []
        s.on("pageerror", lambda e: hatalar.append(str(e)[:160]))
        dlg = {"cevap": True}
        s.on("dialog", lambda d: (diyalog.append((d.type, d.message)), d.accept() if dlg["cevap"] else d.dismiss()))
        s.route("**/*", isle)
        s.goto(BASE + "/giris/", wait_until="load")
        s.wait_for_function("typeof selectMusteri === 'function'", timeout=20000)

        # ---------------- 1) Cift gecmis kok nedeni: kart yukleme yollari tekrar tekrar ----------------
        musteri_ac(s, ACIK)
        for _ in range(3):
            s.evaluate(
                """() => { planKapisiniYenile(%d); planKapisiniYenile(%d); setTimeout(() => planKapisiniYenile(%d), 5); }""" % (ACIK, ACIK, ACIK)
            )
        s.evaluate("selectMusteri(%d)" % ACIK)
        s.wait_for_timeout(1800)
        s.evaluate("() => { try { syncDurumUI('aktif', {}); } catch (e) {} planKapisiniYenile(%d); }" % ACIK)
        s.wait_for_timeout(1200)
        n_gec = say(s, "#plan_gecmis_bolum")
        n_baslik = s.evaluate("[...document.querySelectorAll('div')].filter(e => e.children.length >= 0 && e.firstElementChild === null && e.textContent.trim() === 'Plan geçmişi').length")
        check("plan gecmisi yalniz BIR kez (#plan_gecmis_bolum)", n_gec == 1, n_gec)
        check("'Plan geçmişi' basligi yalniz bir kez", n_baslik == 1, n_baslik)
        check("plan gecmisi form-grid icinde degil", say(s, ".form-grid #plan_gecmis_bolum") == 0 and say(s, "#plan_degistir_modal #plan_gecmis_bolum") == 1)
        check("popup, buton, 'plan' secenegi birer tane", say(s, "#plan_degistir_modal") == 1 and say(s, "#plan_degistir_btn") == 1 and say(s, "#musteri_durum option[value='plan']") == 1,
              (say(s, "#plan_degistir_modal"), say(s, "#plan_degistir_btn"), say(s, "#musteri_durum option[value='plan']")))
        if not tam:
            print("%s: sayfa hatalari %s" % (etiket, hatalar[:2]))
            tarayici.close()
            return list(FAILS)

        # ---------------- 2) Buton: durum seciciin SAGINDA, ayni grid hucresinde ----------------
        konum = s.evaluate(
            """() => { const sel = document.getElementById('musteri_durum').getBoundingClientRect(), b = document.getElementById('plan_degistir_btn').getBoundingClientRect(),
                 w = document.getElementById('musteri_durum_satir');
                 return {sel: [sel.left, sel.top, sel.right, sel.bottom], btn: [b.left, b.top, b.right, b.bottom], satir_ebeveyn: w && w.contains(document.getElementById('plan_degistir_btn')) && w.contains(document.getElementById('musteri_durum'))}; }"""
        )
        check("buton gorunur ve durum seciciin sagnda (ayni satir)", gorunur(s, "#plan_degistir_btn") and konum["btn"][0] >= konum["sel"][2] - 1 and abs(konum["btn"][1] - konum["sel"][1]) < 20 and konum["satir_ebeveyn"], konum)
        check("popup kapali iken gorunmez", not popup_acik(s))
        if ekran:
            EKRAN.mkdir(parents=True, exist_ok=True)
            s.locator("#musteri_durum").scroll_into_view_if_needed()
            s.screenshot(path=str(EKRAN / "1_popup_kapali_buton.png"))
        yeni_rects = rects(s)

        # ---------------- 3) Dropdown'dan 'Plan degistir': popup acilir, dropdown geri doner, durum kaydina 'plan' gitmez ----------------
        s.select_option("#musteri_durum", "plan")
        s.wait_for_selector("#plan_degistir_modal", state="visible", timeout=4000)
        check("dropdown 'Plan degistir' popup acti", popup_acik(s))
        check("dropdown degeri onceki duruma dondu (aktif)", s.evaluate("document.getElementById('musteri_durum').value") == "aktif")
        yazilan.clear()
        s.evaluate("""() => {
            const v = {name: 'Test Ltd', tax_number: '1234567890', vergi_dairesi: 'Merkez', yetkili_ad: 'Ali Veli', yetkili_tc: '10000000146', phone: '5321234567',
                       email: 'a@b.co', address: 'Adres 1', ev_adres: 'Ev 1', sube_merkez: 'merkez', aylik_kira: '600'};
            const h = document.getElementById('hizmet_turu');
            if (h && !String(h.value || '').trim()) { const o = document.createElement('option'); o.value = o.textContent = 'Sanal Ofis'; h.appendChild(o); h.value = 'Sanal Ofis'; }
            Object.keys(v).forEach(id => { const e = document.getElementById(id); if (e && !String(e.value || '').trim()) e.value = v[id]; });
            kaydet({ preventDefault() {} }); }""")
        s.wait_for_timeout(1500)
        kayit = [json.loads(b) for (m, u, b) in yazilan if u.startswith("/giris/kaydet") and b]
        check("durum kaydi gitti ve 'plan' degil (aktif)", len(kayit) >= 1 and all(k.get("durum") == "aktif" for k in kayit) and not any("plan" == str(k.get("durum")) for k in kayit), [k.get("durum") for k in kayit] or "kayit istegi yok")
        check("hicbir yazma istegi 'musteri_durum=plan' tasimiyor", not any('"durum": "plan"' in (b or "") or '"durum":"plan"' in (b or "") for (_m, _u, b) in yazilan))
        if ekran:
            s.screenshot(path=str(EKRAN / "2_popup_acik_masaustu.png"))
        icerik = s.evaluate(
            """() => { const m = document.getElementById('plan_degistir_modal'); const k = m.firstChild;
               return {gecmis: !!m.querySelector('#plan_gecmis_bolum'), form: !!m.querySelector('#plan_gecerlilik') && !!m.querySelector('#plan_yeni_net') && !!m.querySelector('#plan_kdv'),
                       onizle: !!m.querySelector('#plan_onizle_btn'), kaydet: !!m.querySelector('#plan_kaydet'), kapat: !!m.querySelector('#plan_kapat'), x: !!m.querySelector('#plan_x'),
                       onizleme: !!m.querySelector('#plan_onizleme'), uyari: !!m.querySelector('#plan_uyarilar'), kilit: !!m.querySelector('#plan_kilit_kutu'), hata: !!m.querySelector('#plan_hata'),
                       dikey_kaydirma: getComputedStyle(k).overflowY}; }"""
        )
        check("popup: gecmis + form + Onizle/Kaydet/Kapat/X + onizleme/uyari/kilit/hata tek yerde", all(v for kx, v in icerik.items() if kx != "dikey_kaydirma"), icerik)
        check("popup dikey kaydirmali", icerik["dikey_kaydirma"] in ("auto", "scroll"), icerik)

        # ---------------- 4) Onizle / Kaydet / ayni aya degistirme popup icinde ----------------
        s.fill("#plan_gecerlilik", "2026-12")
        s.fill("#plan_yeni_net", "1500")
        s.click("#plan_onizle_btn")
        s.wait_for_function("window.__planOnizlemeHazir === true", timeout=6000)
        uy = s.evaluate("document.getElementById('plan_uyarilar').textContent")
        tablo = s.evaluate("document.querySelectorAll('#plan_onizleme table tr').length")
        check("popup: onizleme tablosu + ayni ay iptal uyarisi", tablo >= 2 and "iptal edilip yenisi yazılacak" in uy, (tablo, uy))
        check("popup: kaydet onizlemeden sonra aktif", s.evaluate("!document.getElementById('plan_kaydet').disabled"))
        n0 = len(gorunen)
        s.click("#plan_kaydet")
        s.wait_for_function("() => { const m = document.getElementById('plan_degistir_modal'); return !m || m.style.display === 'none'; }", timeout=6000)
        s.wait_for_function("document.getElementById('plan_gecmis_bolum') && document.getElementById('plan_gecmis_bolum').textContent.indexOf('net 1500') >= 0", timeout=6000)
        post = [g for g in gorunen[n0:] if g[0] == "POST" and g[1].endswith("/plan")]
        eski51 = [x for x in tx.bellek.satirlar if x["id"] == 51][0]
        yeni = [x for x in tx.bellek.satirlar if x["musteri_id"] == ACIK and x["iptal_at"] is None]
        check("popup: kaydet tek POST; eski plan iptal, yeni aktif; popup kapandi", len(post) == 1 and eski51["iptal_at"] is not None and len(yeni) == 1 and float(yeni[0]["yeni_net"]) == 1500 and not popup_acik(s), (len(post), eski51["iptal_at"], len(yeni)))
        g = s.evaluate("document.getElementById('plan_gecmis_bolum').textContent")
        check("kayit sonrasi gecmis sunucuya gore: 1000 iptal + 1500 aktif, tek kopya", "net 1000" in g and "net 1500" in g and say(s, "#plan_gecmis_bolum") == 1, g)
        check("kayit sonrasi buton hala gorunur", gorunur(s, "#plan_degistir_btn"))

        # ---------------- 5) Buton -> popup yeniden acilir (gecmis + form orada) ----------------
        btn_tikla(s)
        s.wait_for_selector("#plan_degistir_modal", state="visible", timeout=4000)
        check("butona basinca popup yeniden acildi: gecmis ve bos form orada", popup_acik(s) and s.evaluate("document.getElementById('plan_yeni_net').value") == "" and "net 1500" in s.evaluate("document.getElementById('plan_gecmis_bolum').textContent"))
        check("buton basisinda dropdown degeri aktif kaldi", s.evaluate("document.getElementById('musteri_durum').value") == "aktif")

        # ---------------- 6) Iptal popup icinde ----------------
        n0 = len(gorunen)
        s.evaluate("document.querySelector('#plan_gecmis_bolum [data-plan-islem=\"kaldir\"]').click(); document.getElementById('plan_onay_evet').click();")
        try:
            s.wait_for_function(
                """() => document.querySelectorAll('#plan_gecmis_bolum [data-plan-islem="kaldir"], #plan_gecmis_bolum [data-plan-islem="duzenle"]').length === 0 && document.querySelectorAll('#plan_gecmis_bolum [data-plan-islem="sil"]').length >= 1""",
                timeout=6000,
            )
        except Exception:
            raise
        iptal = [x for x in gorunen[n0:] if x[0] == "POST" and "/iptal" in x[1]]
        check("popup icinde Iptal: tek istek, liste yenilendi, popup acik kaldi", len(iptal) == 1 and popup_acik(s) and say(s, "#plan_gecmis_bolum") == 1, (len(iptal), popup_acik(s)))
        s.click("#plan_kapat")
        check("Kapat dugmesi dogrudan kapatir (onay yok)", not popup_acik(s) and not [d for d in diyalog if d[0] == "confirm"], diyalog)

        # ---------------- 7) Hata gosterimi popup icinde (ornek: plan_tx 409) ----------------
        from sozlesme_plan_depo import PlanCakisma
        btn_tikla(s)
        s.wait_for_selector("#plan_degistir_modal", state="visible", timeout=4000)
        s.fill("#plan_gecerlilik", "2027-03")
        s.fill("#plan_yeni_net", "2000")
        s.click("#plan_onizle_btn")
        s.wait_for_function("window.__planOnizlemeHazir === true", timeout=6000)
        eski_tx = api.plan_tx

        def cakisma(fn):
            raise PlanCakisma("test")

        api.plan_tx = cakisma
        s.evaluate("(() => { const m = document.querySelector('#plan_degistir_modal > div'); m.scrollTop = m.scrollHeight; })()")
        s.click("#plan_kaydet")
        s.wait_for_function("() => !window.__planKaydediliyor", timeout=5000)
        s.wait_for_timeout(150)
        h = s.evaluate(
            """() => { const k = document.getElementById('plan_hata'), m = document.querySelector('#plan_degistir_modal > div'); const kb = k.getBoundingClientRect(), mb = m.getBoundingClientRect();
                return {gorunur: getComputedStyle(k).display !== 'none', metin: k.textContent, ekranda: kb.top >= mb.top - 1 && kb.bottom <= mb.bottom + 1,
                        ipucu: document.getElementById('plan_kaydet_ipucu').textContent, kaydet_pasif: document.getElementById('plan_kaydet').disabled}; }"""
        )
        api.plan_tx = eski_tx
        check("hata popup icinde gorunur ve ekranda; ipucu 'Hata:'; kaydet geri acildi", h["gorunur"] and "açık bir plan zaten var" in h["metin"] and h["ekranda"] and h["ipucu"].startswith("Hata: ") and h["kaydet_pasif"] is False and popup_acik(s), h)
        alt = s.evaluate(
            """() => { const a = document.getElementById('plan_alt').getBoundingClientRect(), m = document.querySelector('#plan_degistir_modal > div').getBoundingClientRect(); return a.bottom <= m.bottom + 1 && a.top >= m.top; }"""
        )
        check("sticky alt cubuk popup icinde gorunur", alt)

        # ---------------- 8) Kirli form: ESC / dis tiklama onay ister; temiz formda dogrudan ----------------
        diyalog.clear()
        dlg["cevap"] = False  # onay reddedilir -> popup acik kalir
        s.keyboard.press("Escape")
        s.wait_for_timeout(250)
        check("kirli form + ESC: onay soruldu, reddedilince popup acik", popup_acik(s) and len([d for d in diyalog if d[0] == "confirm"]) == 1, diyalog)
        s.mouse.click(1390, 450)  # popup disi (overlay)
        s.wait_for_timeout(250)
        check("kirli form + dis tiklama: onay soruldu, reddedilince popup acik", popup_acik(s) and len([d for d in diyalog if d[0] == "confirm"]) == 2, diyalog)
        dlg["cevap"] = True
        s.keyboard.press("Escape")
        s.wait_for_timeout(250)
        check("kirli form + ESC + onay: kapandi, form temizlendi", not popup_acik(s) and s.evaluate("document.getElementById('plan_yeni_net').value") == "")
        diyalog.clear()
        btn_tikla(s)
        s.wait_for_selector("#plan_degistir_modal", state="visible", timeout=4000)
        s.keyboard.press("Escape")
        s.wait_for_timeout(250)
        check("temiz form + ESC: onaysiz kapanir", not popup_acik(s) and diyalog == [], diyalog)
        btn_tikla(s)
        s.wait_for_selector("#plan_degistir_modal", state="visible", timeout=4000)
        s.mouse.click(1390, 450)
        s.wait_for_timeout(250)
        check("temiz form + dis tiklama: onaysiz kapanir", not popup_acik(s) and diyalog == [], diyalog)
        btn_tikla(s)
        s.wait_for_selector("#plan_degistir_modal", state="visible", timeout=4000)
        s.click("#plan_x")
        check("sag ust X kapatir", not popup_acik(s))
        # kutu icinde basla, disarida birak: kapanmamali
        btn_tikla(s)
        s.wait_for_selector("#plan_degistir_modal", state="visible", timeout=4000)
        kutu = s.locator("#plan_degistir_modal > div").bounding_box()
        s.mouse.move(kutu["x"] + 40, kutu["y"] + 60)
        s.mouse.down()
        s.mouse.move(1390, 450)
        s.mouse.up()
        s.wait_for_timeout(200)
        check("kutu icinde basip disarida birakmak popup'i kapatmaz", popup_acik(s))
        s.click("#plan_kapat")
        s.evaluate("() => { document.getElementById('plan_degistir_modal').style.display = 'none'; }")

        # ---------------- 9) Pasif musteri: buton ve gecmis var; yeni plan reddi popup'ta net ----------------
        musteri_ac(s, PASIF)
        check("pasif: buton gorunur", gorunur(s, "#plan_degistir_btn"))
        btn_tikla(s)
        s.wait_for_selector("#plan_degistir_modal", state="visible", timeout=4000)
        pn = s.evaluate("(() => { const e = document.getElementById('plan_pasif_not'); return {goster: getComputedStyle(e).display !== 'none', metin: e.textContent}; })()")
        check("pasif: popup'ta net not", pn["goster"] and "pasif" in pn["metin"].lower() and "kaydedilemez" in pn["metin"], pn)
        check("pasif: gecmis gorunur (net 900)", "net 900" in s.evaluate("document.getElementById('plan_gecmis_bolum').textContent"))
        check("pasif: dropdown degeri pasif kaldi", s.evaluate("document.getElementById('musteri_durum').value") == "pasif")
        s.fill("#plan_gecerlilik", "2027-04")
        s.fill("#plan_yeni_net", "800")
        n0 = len(yazilan)
        s.click("#plan_onizle_btn")
        s.wait_for_function("() => { const b = document.getElementById('plan_onizle_btn'); return b && !b.disabled; }", timeout=5000)
        s.wait_for_timeout(200)
        msg = s.evaluate("document.getElementById('plan_hata').textContent")
        check("pasif: yeni plan sunucuda reddedilir, mesaj popup'ta", "Pasif müşteriye plan eklenemez" in msg and not any(u.split("?")[0].endswith("/plan") for (_m, u, _b) in yazilan[n0:]), msg)
        if ekran:
            s.screenshot(path=str(EKRAN / "5_pasif_popup.png"))
        s.keyboard.press("Escape")  # kirli: confirm (dlg True)
        s.wait_for_timeout(250)
        check("pasif: popup kapandi, dropdown hala pasif", not popup_acik(s) and s.evaluate("document.getElementById('musteri_durum').value") == "pasif")

        # ---------------- 10) Musteri degisince eski popup/plan kalmaz ----------------
        musteri_ac(s, ACIK)
        btn_tikla(s)
        s.wait_for_selector("#plan_degistir_modal", state="visible", timeout=4000)
        s.evaluate("selectMusteri(%d)" % PASIF)
        s.wait_for_timeout(200)
        check("musteri degisince eski popup kapandi", not popup_acik(s))
        s.wait_for_function("document.querySelector('#plan_gecmis_bolum') && document.querySelector('#plan_gecmis_bolum').textContent.indexOf('net 900') >= 0", timeout=8000)
        gtxt = s.evaluate("document.getElementById('plan_gecmis_bolum').textContent")
        check("yeni musterinin plani gorunur, eskinin plani yok", "net 900" in gtxt and "net 1500" not in gtxt and "net 1000" not in gtxt and say(s, "#plan_gecmis_bolum") == 1, gtxt)

        # ---------------- 11) Kapisi kapali musteride buton ve popup yok ----------------
        musteri_ac(s, KAPALI, kapi_acik=False)
        check("kapi kapali: buton yok", say(s, "#plan_degistir_btn") == 0)
        check("kapi kapali: popup, gecmis ve 'plan' secenegi yok", say(s, "#plan_degistir_modal") == 0 and say(s, "#plan_gecmis_bolum") == 0 and say(s, "#musteri_durum option[value='plan']") == 0)
        kapali_rects = rects(s)
        musteri_ac(s, ACIK)
        check("kapi acik musteriye donunce buton geri geldi", gorunur(s, "#plan_degistir_btn") and say(s, "#plan_degistir_btn") == 1)

        # ---------------- 12) Grup / Grup 2 duzeni kaymaz ----------------
        acik_rects = rects(s)
        farklar = {k: (acik_rects.get(k), kapali_rects.get(k)) for k in set(acik_rects) | set(kapali_rects) if k not in ("plan_degistir_btn", "plan_dondur_btn") and acik_rects.get(k) != kapali_rects.get(k)}
        check("form-grid elemanlari konumu: plan kapisi acik == kapali (Grup/Grup 2 dahil)", not farklar, dict(list(farklar.items())[:6]))
        check("Grup / Grup 2 elemanlari bulundu", any("grup" in k for k in acik_rects), sorted(k for k in acik_rects if "grup" in k)[:6])
        if ekran:
            ac = s.locator(".form-grid").first
            ac.scroll_into_view_if_needed()
            ac.screenshot(path=str(EKRAN / "3_form_plan_acik_grup_duzeni.png"))
            musteri_ac(s, KAPALI, kapi_acik=False)
            s.locator(".form-grid").first.screenshot(path=str(EKRAN / "3_form_plan_kapali_grup_duzeni.png"))
            musteri_ac(s, ACIK)
            s.set_viewport_size({"width": 390, "height": 640})
            btn_tikla(s)
            s.wait_for_selector("#plan_degistir_modal", state="visible", timeout=4000)
            s.screenshot(path=str(EKRAN / "4_popup_dar_ekran.png"))
            tas = s.evaluate(
                """() => { const k = document.querySelector('#plan_degistir_modal > div').getBoundingClientRect(); return {sol: k.left >= 0, sag: k.right <= window.innerWidth, ust: k.top >= 0, alt: k.bottom <= window.innerHeight,
                    yatayTasma: document.querySelector('#plan_degistir_modal > div').scrollWidth > document.querySelector('#plan_degistir_modal > div').clientWidth + 1}; }"""
            )
            check("dar ekranda popup tasmaz (390x640)", tas["sol"] and tas["sag"] and tas["ust"] and tas["alt"] and not tas["yatayTasma"], tas)
            s.click("#plan_x")
            s.set_viewport_size({"width": 1400, "height": 900})
        print("%s: sayfa hatalari %s" % (etiket, hatalar[:3]))
        tarayici.close()
    return list(FAILS), yeni_rects, kapali_rects


def main():
    sonuc = 0
    ref = os.environ.get("ESKI_REF", "d80e1c1")
    tmp = tempfile.gettempdir()
    eski_html = _git_dosya(ref, "erp_web/templates/giris/index.html", os.path.join(tmp, "_pp_index_eski.html"))
    eski_js = _git_dosya(ref, "erp_web/static/js/plan_degistir.js", os.path.join(tmp, "_pp_plan_eski.js"))
    if eski_html and eski_js and not os.environ.get("SADECE_YENI"):
        f = kosu(eski_html, eski_js, "ESKI(%s)" % ref, tam=False)
        gec = [x for x in (f or []) if "yalniz BIR kez" in x or "yalniz bir kez" in x]
        print("ESKI: cift gecmis kusuru yeniden uretildi: %s" % ("EVET" if gec else "HAYIR"))
        if not gec:
            print("FAIL eski surumde cift gecmis kusuru uretilemedi")
            sonuc = 1
        FAILS.clear()
    r = kosu(None, None, "YENI", tam=True, ekran=True)
    if r is None:
        return 0
    fails = r[0]
    print("YENI: basarisiz=%d" % len(fails))
    if fails:
        sonuc = 1
    print("plan_popup_gercek " + ("FAIL" if sonuc else "ok"))
    return sonuc


if __name__ == "__main__":
    raise SystemExit(main())
