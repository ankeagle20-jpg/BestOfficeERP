# -*- coding: utf-8 -*-
"""Tarayıcı -> GERÇEK Flask rotası (sahte DB) -> arayüz. Canlı veritabanına/sunucuya gitmez.

Mock API'nin yakalayamadığı uyuşmazlıkları arar: müşteri kimliğinin okunması (`let selectedId`),
gönderilen gövde, route'un yanıtı, hata mesajlarının modalda gösterilmesi, modal yerleşimi.
Playwright (msedge) yoksa atlanır.
"""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    print("SKIP playwright yok")
    raise SystemExit(0)

import auth  # noqa: E402
import db as dbmod  # noqa: E402
import sozlesme_plan_api as api  # noqa: E402
from test_plan_degistir import Tx, _app, _kur  # noqa: E402

BASE = "http://plan.test"
JS_YOLU = Path(os.environ.get("PLAN_JS_YOL") or (ROOT / "static" / "js" / "plan_degistir.js"))
FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _stiller():
    bloklar = []
    for ad in ("layout.html", "giris/index.html"):  # sayfa layout.html'den türer
        t = (ROOT / "templates" / ad).read_text(encoding="utf-8", errors="replace")
        bloklar += re.findall(r"<style[^>]*>(.*?)</style>", t, flags=re.S)
    return "\n".join(re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", b, flags=re.S) for b in bloklar)


def _sayfa():
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><style>' + _stiller() + "</style></head><body>"
        '<select id="musteri_durum" onfocus="planDurumOdak()" onchange="musteriDurumSecildi(this.value)">'
        '<option value="aktif">Aktif</option><option value="pasif">Pasif</option></select>'
        '<input id="kdv_oran" value="20"><input id="kira_nakit" type="checkbox"><input id="kira_banka" type="checkbox" checked>'
        "<script>let selectedId = null;</script>"  # gerçek sayfadaki gibi: window'a eklenmez
        '<script src="/static/js/plan_degistir.js"></script>'
        "<script>window.__istek=[];"
        "function sozlesmelerAylikHizliYukle(){} function sozlesmelerAylikGuncelle(){}"
        "function girisTahsilatOzetGuncelle(){} function cariEkstreYukle(){}</script></body></html>"
    )


class Durum:
    giris = True
    ag_hatasi = False
    faturali = []


def _auth_yamala():
    class K:
        @property
        def is_authenticated(self):
            return Durum.giris

        is_active = True
        is_anonymous = False
        id = 1
        username = "test"

        def get_id(self):
            return "1"

    auth.current_user = K()


def _flask_kur():
    app = _app()
    app.logger.disabled = True
    tx = Tx()
    _kur(tx)
    app.test_tx = tx
    # 60 aylık zincir: 2027-09 geçerlilik ayı da zincir içinde kalsın
    eski = api._onizleme_zinciri

    def zincir(mid, kyc):
        z, mevcut, kutu = eski(mid, kyc)
        z["ay_sayisi"] = 60
        z["faturali_aylar"] = list(Durum.faturali)
        return z, mevcut, kutu

    api._onizleme_zinciri = zincir
    dbmod.fetch_all = lambda *a, **k: []
    dbmod.fetch_one = lambda *a, **k: None
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "7"
    _auth_yamala()
    return app


def _yonlendir(app, gorunen):
    client = app.test_client()

    def isle(route, request):
        url = request.url
        yol = url[len(BASE):]
        if yol in ("/", "/sayfa.html"):
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=_sayfa())
            return
        if yol.startswith("/static/js/plan_degistir.js"):
            route.fulfill(status=200, content_type="application/javascript; charset=utf-8", body=JS_YOLU.read_text(encoding="utf-8"))
            return
        if yol.startswith("/giris/api/"):
            gorunen.append((request.method, yol, request.post_data))
            if Durum.ag_hatasi and yol.endswith("/plan/onizleme"):
                route.abort()
                return
            r = client.open(
                yol,
                method=request.method,
                data=request.post_data_buffer if request.method != "GET" else None,
                content_type=request.headers.get("content-type"),
            )
            route.fulfill(status=r.status_code, content_type=r.content_type or "application/json", body=r.get_data())
            return
        route.fulfill(status=404, body="yok")

    return isle


def _modal_ac(page, mid):
    page.evaluate("selectedId = %d" % mid)  # kart yükleme akışı: let selectedId atanır, window.selectedId yok
    page.evaluate("planKapisiniYenile(%d)" % mid)
    page.wait_for_selector("#musteri_durum option[value='plan']", state="attached", timeout=5000)
    page.select_option("#musteri_durum", "plan")
    page.wait_for_selector("#plan_degistir_modal", state="visible", timeout=3000)


def _doldur(page, ay="2027-09", net="2000", kdv="20"):
    page.fill("#plan_gecerlilik", ay)
    page.fill("#plan_yeni_net", net)
    page.fill("#plan_kdv", kdv)


def _onizle(page):
    page.click("#plan_onizle_btn")
    page.wait_for_function(
        "() => { var b = document.getElementById('plan_onizle_btn'); return b && !b.disabled; }", timeout=5000
    )
    page.wait_for_timeout(100)


def _kutu_durum(sayfa):
    return sayfa.evaluate(
        """() => {
            const r = id => { const e = document.getElementById(id); if (!e) return null; const b = e.getBoundingClientRect(); return {t: b.top, b: b.bottom, l: b.left, r: b.right, h: b.height}; };
            const g = id => { const e = document.getElementById(id); return !!e && getComputedStyle(e).display !== 'none'; };
            return {
                kaydet_pasif: document.getElementById('plan_kaydet').disabled,
                ipucu: document.getElementById('plan_kaydet_ipucu').textContent,
                hazir: window.__planOnizlemeHazir,
                kilit_kutu: g('plan_kilit_kutu'), odemeli_kutu: g('plan_odemeli_kutu'),
                modal: r('plan_degistir_modal').h ? (function () { const m = document.querySelector('#plan_degistir_modal > div').getBoundingClientRect(); return {t: m.top, b: m.bottom}; })() : null,
                kaydet: r('plan_kaydet'), kilit_etiket: r('plan_kilit_etiket'), kilit_cb: r('plan_kilit_onay'), kilit_box: r('plan_kilit_kutu'),
                odemeli_etiket: r('plan_odemeli_etiket'), odemeli_cb: r('plan_odemeli_onay'), odemeli_box: r('plan_odemeli_kutu'),
            };
        }"""
    )


def _icinde(ic, dis):
    return ic and dis and ic["t"] >= dis["t"] - 1 and ic["b"] <= dis["b"] + 1


def _yeni_senaryolar(sayfa, app, gorunen, bu_ay):
    """867 benzeri tüm-faturalı kart, geçmiş aya plan + ödemeli ay onayı, geçmiş plan iptali."""
    tx = app.test_tx
    api._resync = lambda mid: True
    api._bugun = lambda: date.today()
    eski_kilit, eski_tahsil, eski_fa = api._kilitler, api._tahsil_haritasi, Durum.faturali

    def modal_ac():
        _modal_ac(sayfa, 7)

    def kaydet_istegi(n0):
        return [json.loads(g[2]) for g in gorunen[n0:] if g[0] == "POST" and g[1] == "/giris/api/musteri/7/plan"]

    # ---------- A) 867 benzeri: 2023-01..2027-08 tümü faturalı, geçerlilik = bu ay ----------
    aylar = [f"{2023 + i // 12}-{i % 12 + 1:02d}" for i in range(56)]
    api._kilitler = lambda mid: [{"ay": a, "fatura_tutari": 1200.0, "kaynak": "fatura_tarihi"} for a in aylar]
    Durum.faturali = list(aylar)
    api._tahsil_haritasi = lambda mid: {}
    sayfa.keyboard.press("Escape")
    sayfa.click("#plan_kapat")
    modal_ac()
    _doldur(sayfa, ay=bu_ay)
    _onizle(sayfa)
    d = _kutu_durum(sayfa)
    check("867: onizleme hazir, kilit kutusu gorunur", d["hazir"] is True and d["kilit_kutu"] is True, d)
    check("867: onay isaretlenmeden kaydet pasif + ipucu", d["kaydet_pasif"] and d["ipucu"] == "Kilitli ay onayını işaretleyin", d)
    check(
        "867: onay kutusu kirmizi kutu icinde ve ekranda gorunur",
        _icinde(d["kilit_cb"], d["kilit_box"]) and _icinde(d["kilit_cb"], d["modal"]) and d["kilit_cb"]["h"] >= 16,
        d,
    )
    check("867: kaydet dugmesi ekranda gorunur (sabit alt cubuk)", _icinde(d["kaydet"], d["modal"]), d)
    check("867: kilit kutusu kompakt", d["kilit_box"]["h"] < 160, d["kilit_box"])
    check(
        "867: gorunur degisiklik yok uyarisi bilgi amacli degil/engel degil",
        "Bilgi:" not in sayfa.evaluate("document.getElementById('plan_uyarilar').textContent"),
    )
    sayfa.click("#plan_kilit_onay")
    d = _kutu_durum(sayfa)
    check("867: onay isaretlenince kaydet aktif, ipucu bos", d["kaydet_pasif"] is False and d["ipucu"] == "", d)
    sayfa.click("#plan_kilit_onay")
    check("867: onay kalkinca kaydet tekrar pasif", _kutu_durum(sayfa)["kaydet_pasif"] is True)
    sayfa.click("#plan_kilit_onay")
    # form değişince eski önizleme geçersiz: kaydet kapanır, ipucu Önizle der
    sayfa.fill("#plan_yeni_net", "2100")
    d = _kutu_durum(sayfa)
    check("867: form degisince kaydet kapanir", d["kaydet_pasif"] and d["ipucu"] == "Önce Önizle'ye basın" and d["kilit_kutu"] is False, d)
    _onizle(sayfa)
    sayfa.click("#plan_kilit_onay")
    n0 = len(gorunen)
    sayfa.click("#plan_kaydet")
    sayfa.wait_for_function("() => { const m = document.getElementById('plan_degistir_modal'); return !m || m.style.display === 'none'; }", timeout=5000)
    ist = kaydet_istegi(n0)
    check(
        "867: kayit istegi onay_kilitli_aylar ile gitti ve kaydedildi",
        len(ist) == 1 and ist[0].get("onay_kilitli_aylar") is True and "onay_odemeli_aylar" not in ist[0]
        and any(str(s["gecerlilik_ay"])[:7] == bu_ay for s in tx.bellek.satirlar),
        ist,
    )

    # "görünür değişiklik yok" + kilit yok: bilgi, Kaydet engellenmez
    api._kilitler = lambda mid: []
    Durum.faturali = [f"{2027 + (8 + i) // 12}-{(8 + i) % 12 + 1:02d}" for i in range(40)]
    modal_ac()
    _doldur(sayfa, ay="2027-09", net="2000")
    _onizle(sayfa)
    d = _kutu_durum(sayfa)
    uy = sayfa.evaluate("document.getElementById('plan_uyarilar').textContent")
    check("gorunur degisiklik yok: bilgi var, kaydet engellenmedi", "Bilgi:" in uy and d["kaydet_pasif"] is False, (uy, d))
    Durum.faturali = []

    # ---------- B) geçmiş aya plan + ödemeli aylar ----------
    sayfa.click("#plan_kapat")
    api._bugun = lambda: date(2026, 10, 9)
    api._tahsil_haritasi = lambda mid: {"2026-03": 500.0, "2026-04": 99999.0, "2026-01": 700.0}
    modal_ac()
    _doldur(sayfa, ay="2026-03")
    _onizle(sayfa)
    d = _kutu_durum(sayfa)
    satir_sayisi = sayfa.locator("#plan_odemeli_tablo table tr").count()
    odm_metin = sayfa.evaluate("document.getElementById('plan_odemeli_kutu').textContent")
    check("odemeli: kutu gorunur, 2 ay + baslik satiri", d["odemeli_kutu"] is True and satir_sayisi == 3, (d, satir_sayisi))
    check("odemeli: fazla odeme kirmizi uyari", "Fazla ödeme" in odm_metin and "DİKKAT" in odm_metin, odm_metin)
    check("odemeli: onay kutusu gorunur ve etiket dogru", _icinde(d["odemeli_cb"], d["odemeli_box"]) and "Ödemesi olan geçmiş aylar etkilenecek, anladım" in odm_metin, d)
    check("odemeli: onaysiz kaydet pasif + ipucu", d["kaydet_pasif"] and d["ipucu"] == "Ödemeli ay onayını işaretleyin", d)
    sayfa.click("#plan_odemeli_onay")
    check("odemeli: onayla kaydet aktif", _kutu_durum(sayfa)["kaydet_pasif"] is False)
    n0 = len(gorunen)
    sayfa.click("#plan_kaydet")
    sayfa.wait_for_function("() => { const m = document.getElementById('plan_degistir_modal'); return !m || m.style.display === 'none'; }", timeout=5000)
    ist = kaydet_istegi(n0)
    check(
        "odemeli: istek onay_odemeli_aylar ile gitti, gecmis plan kaydedildi",
        len(ist) == 1 and ist[0].get("onay_odemeli_aylar") is True
        and any(str(s["gecerlilik_ay"])[:7] == "2026-03" for s in tx.bellek.satirlar),
        ist,
    )

    # ---------- C) geçmiş plan iptali (arayüzde İptal düğmesi var, geçmiş notu yok) ----------
    tx.bellek.satirlar.append(
        {
            "id": 21, "musteri_id": 7, "gecerlilik_ay": date(2024, 1, 1), "yeni_net": 2000, "kdv_oran": 20,
            "yeni_brut": 2400, "nakit_tutar": None, "banka_tutar": 2000, "olusturan": "test",
            "created_at": None, "iptal_at": None, "iptal_eden": None,
        }
    )
    eski_fetch = dbmod.fetch_all
    dbmod.fetch_all = lambda *a, **k: [dict(s) for s in tx.bellek.satirlar if int(s["musteri_id"]) == 7]
    try:
        sayfa.evaluate("planKapisiniYenile(7)")
        sayfa.wait_for_selector("#plan_gecmis_bolum button", state="attached", timeout=5000)
        bolum = sayfa.evaluate("document.getElementById('plan_gecmis_bolum').textContent")
        iptal_say = sayfa.locator("#plan_gecmis_bolum button").count()
        check("gecmis plan icin Iptal dugmesi var", iptal_say >= 1 and "Geçmiş aya ait plan iptal edilemez" not in bolum, (iptal_say, bolum))
        n0 = len(gorunen)
        sayfa.evaluate(
            "(() => { const b = [...document.querySelectorAll('#plan_gecmis_bolum > div')].find(x => x.textContent.indexOf('2024-01') >= 0); b.querySelector('button').click(); })()"
        )
        sayfa.wait_for_timeout(600)
        iptaller = [g for g in gorunen[n0:] if g[1].endswith("/plan/21/iptal")]
        satir21 = [s for s in tx.bellek.satirlar if s["id"] == 21][0]
        check("gecmis plan iptal istegi gitti ve iptal edildi", len(iptaller) == 1 and satir21["iptal_at"] is not None, iptaller)
    finally:
        dbmod.fetch_all = eski_fetch
        api._kilitler, api._tahsil_haritasi = eski_kilit, eski_tahsil
        Durum.faturali = eski_fa
        api._bugun = lambda: date(2024, 10, 9)
    # sonraki (hata) senaryoları için modal temiz açılabilsin
    modal_ac()
    _doldur(sayfa)


def main():
    app = _flask_kur()
    gorunen = []
    diyalog = []
    with sync_playwright() as p:
        try:
            tarayici = p.chromium.launch(channel="msedge", headless=True)
        except Exception as exc:  # pragma: no cover
            print("SKIP tarayici yok:", str(exc)[:80])
            return 0
        try:
            sayfa = tarayici.new_page(viewport={"width": 1280, "height": 900})
            sayfa.on("dialog", lambda d: (diyalog.append(d.message), d.accept()))
            sayfa.route("**/*", _yonlendir(app, gorunen))
            sayfa.goto(BASE + "/sayfa.html", wait_until="load")

            check("on kosul window.selectedId tanimsiz (let)", sayfa.evaluate("typeof window.selectedId") == "undefined")
            _modal_ac(sayfa, 7)

            # --- modal yerleşimi: etiketler alanın üstünde ayrı satırlarda ---
            yer = sayfa.evaluate(
                """() => ['plan_gecerlilik','plan_yeni_net','plan_kdv','plan_odeme'].map(id => {
                    const i = document.getElementById(id), l = i.previousElementSibling;
                    const bi = i.getBoundingClientRect(), bl = l.getBoundingClientRect();
                    return {id, lt: bl.top, lb: bl.bottom, ll: bl.left, it: bi.top, il: bi.left, iw: bi.width, ih: bi.height};
                })"""
            )
            ok = all(y["lb"] <= y["it"] + 1 and abs(y["ll"] - y["il"]) < 2 and y["iw"] > 100 and y["ih"] > 15 for y in yer)
            ok = ok and all(yer[k]["it"] < yer[k + 1]["lt"] for k in range(3))
            check("modal etiket ustte, alan altta, satirlar ayri", ok, yer)

            # --- ay seçici: min = sözleşme başlangıç ayı (geçmiş serbest), varsayılan bu ay ---
            bu_ay = sayfa.evaluate("(() => { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0'); })()")
            check("ay secici min sozlesme baslangic ayi", sayfa.evaluate("document.getElementById('plan_gecerlilik').min") == "2023-08")
            check("ay secici varsayilan bu ay", sayfa.evaluate("document.getElementById('plan_gecerlilik').value") == bu_ay)
            check(
                "kaydet baslangicta pasif ve ipucu var",
                sayfa.evaluate("document.getElementById('plan_kaydet').disabled")
                and sayfa.evaluate("document.getElementById('plan_kaydet_ipucu').textContent") == "Önce Önizle'ye basın",
            )

            # --- ana akış: gerçek route ---
            _doldur(sayfa)
            _onizle(sayfa)
            istek = [g for g in gorunen if g[1].endswith("/plan/onizleme")]
            check("onizle istegi gitti", len(istek) == 1 and istek[0][1] == "/giris/api/musteri/7/plan/onizleme", istek)
            govde = json.loads(istek[0][2]) if istek else {}
            check(
                "govde alanlari",
                govde == {"gecerlilik_ay": "2027-09", "yeni_net": 2000, "kdv_oran": 20, "odeme": "banka"},
                govde,
            )
            brut = sayfa.evaluate("document.getElementById('plan_brut').textContent")
            satir = sayfa.locator("#plan_onizleme table tr").count()
            kaydet_acik = sayfa.evaluate("!document.getElementById('plan_kaydet').disabled")
            hata = sayfa.evaluate("document.getElementById('plan_hata').style.display")
            check("brut 2400 ve tablo dolu", brut == "2400" and satir >= 2, (brut, satir))
            check("kaydet onizlemeden sonra aktif", kaydet_acik)
            check("hata kutusu gizli", hata == "none", hata)

            # --- görünür değişiklik yok uyarısı arayüzde (40 ay faturalı: pencerede fark yok) ---
            check("normal akista gorunur degisiklik uyarisi yok", "görünür bir değişiklik yaratmıyor" not in sayfa.evaluate("document.getElementById('plan_uyarilar').textContent"))
            Durum.faturali = [f"{2027 + (8 + i) // 12}-{(8 + i) % 12 + 1:02d}" for i in range(40)]  # 2027-09 ... 2030-12
            _onizle(sayfa)
            uyari = sayfa.evaluate("document.getElementById('plan_uyarilar').textContent")
            check(
                "gorunur degisiklik yok uyarisi arayuzde",
                "faturalı aylar nedeniyle şu an görünür bir değişiklik yaratmıyor; ilk değişen ay: 2031-01" in uyari
                and sayfa.evaluate("!document.getElementById('plan_kaydet').disabled"),
                uyari,
            )
            Durum.faturali = []
            _onizle(sayfa)
            check("uyari yeni onizlemede temizlenir", sayfa.evaluate("document.getElementById('plan_uyarilar').textContent") == "")

            # --- karma ödeme gövdesi ---
            sayfa.select_option("#plan_odeme", "karma")
            sayfa.fill("#plan_nakit", "500")
            sayfa.fill("#plan_banka", "1500")
            n0 = len(gorunen)
            _onizle(sayfa)
            g2 = json.loads(gorunen[n0][2])
            check("karma govde", g2.get("odeme") == "karma" and g2.get("nakit_tutar") == 500 and g2.get("banka_tutar") == 1500, g2)
            karma_brut = sayfa.evaluate("document.getElementById('plan_brut').textContent")
            karma_beklenen = api._kart_parca(2000.0, 20.0, False, 500.0 / 2000.0)["brut"] if hasattr(api, "_kart_parca") else None
            check(
                "karma onizleme basarili (sunucu brutu)",
                karma_beklenen is not None and abs(float(karma_brut) - float(karma_beklenen)) < 0.011,
                (karma_brut, karma_beklenen),
            )
            sayfa.select_option("#plan_odeme", "banka")

            # --- hata durumları: sessiz kalmamalı, kaydet pasif ---
            def hata_kontrol(ad, beklenen_parca):
                kutu = sayfa.evaluate("(() => { const k = document.getElementById('plan_hata'); return {d: k.style.display, t: k.textContent}; })()")
                pasif = sayfa.evaluate("document.getElementById('plan_kaydet').disabled")
                check(ad, kutu["d"] != "none" and beklenen_parca in kutu["t"] and pasif, (kutu, pasif))

            sayfa.fill("#plan_yeni_net", "")
            _onizle(sayfa)
            hata_kontrol("400 gecersiz tutar modalda", "pozitif")

            _doldur(sayfa, ay="2023-07")
            _onizle(sayfa)
            hata_kontrol("400 sozlesme oncesi modalda (sunucu mesaji)", "sözleşme başlangıcından önce")
            check(
                "sozlesme oncesi mesaji tam",
                sayfa.evaluate("document.getElementById('plan_hata').textContent") == "Geçerlilik ayı sözleşme başlangıcından önce olamaz.",
            )

            _doldur(sayfa)
            Durum.giris = False
            _onizle(sayfa)
            hata_kontrol("401 oturum yok modalda", "Oturum")
            Durum.giris = True

            Durum.ag_hatasi = True
            _onizle(sayfa)
            hata_kontrol("ag hatasi modalda", "bağlantı")
            Durum.ag_hatasi = False

            eski_hesap = api.plan_tutar_hesapla
            api.plan_tutar_hesapla = lambda d: (_ for _ in ()).throw(RuntimeError("test-500"))
            app.config["PROPAGATE_EXCEPTIONS"] = False
            _onizle(sayfa)
            api.plan_tutar_hesapla = eski_hesap
            hata_kontrol("500 modalda kod ile", "500")

            # kapı kapalı müşteri (404)
            sayfa.evaluate("selectedId = 8")
            _onizle(sayfa)
            hata_kontrol("404 kapali musteri modalda", "")
            kutu404 = sayfa.evaluate("document.getElementById('plan_hata').textContent")
            check("404 mesaji bos degil", len(kutu404.strip()) > 0, kutu404)
            sayfa.evaluate("selectedId = 7")

            # düzelince hata kutusu temizlenir
            _doldur(sayfa)
            _onizle(sayfa)
            check(
                "hata sonrasi basarili onizleme hatayi temizler",
                sayfa.evaluate("document.getElementById('plan_hata').style.display") == "none"
                and sayfa.evaluate("!document.getElementById('plan_kaydet').disabled"),
            )

            _yeni_senaryolar(sayfa, app, gorunen, bu_ay)

            # müşteri seçili değil
            sayfa.evaluate("selectedId = null")
            n1 = len(gorunen)
            _onizle(sayfa)
            check(
                "musteri yoksa istek yok, mesaj var",
                len(gorunen) == n1 and "müşteri" in sayfa.evaluate("document.getElementById('plan_hata').textContent"),
            )
            check("alert kullanilmadi", diyalog == [], diyalog)
        finally:
            tarayici.close()
    if FAILS:
        print("TARAYICI_ROTA_FAIL", FAILS)
        return 1
    print("plan_onizle_tarayici ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
