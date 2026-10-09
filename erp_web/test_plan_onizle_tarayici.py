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

            # --- ay seçici: min = bu ay (tarayıcı yerel tarihi), eski değer bu aydan önceyse bu aya çekilir ---
            bu_ay = sayfa.evaluate("(() => { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0'); })()")
            check("ay secici min bu ay", sayfa.evaluate("document.getElementById('plan_gecerlilik').min") == bu_ay)
            check("ay secici varsayilan bu ay", sayfa.evaluate("document.getElementById('plan_gecerlilik').value") == bu_ay)

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

            _doldur(sayfa, ay="2020-01")
            _onizle(sayfa)
            hata_kontrol("400 gecmis ay modalda (sunucu mesaji)", "Geçmiş aya plan girilemez")
            check(
                "gecmis ay mesaji tam",
                sayfa.evaluate("document.getElementById('plan_hata').textContent") == api.MSG_GECMIS_AY,
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
