# -*- coding: utf-8 -*-
"""Plan geçmişi arayüzü: İptal düğmesi kilidi, "Açık plan yok." (404) akışı, aynı aya yeni plan sonrası liste.

Tarayıcı -> GERÇEK Flask rotası (sahte DB) -> arayüz. Canlı veritabanına gitmez. Playwright (msedge) yoksa atlanır."""
import sys
from datetime import date, datetime
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
from test_plan_onizle_tarayici import BASE, _doldur, _flask_kur, _modal_ac, _onizle, _yonlendir  # noqa: E402

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _satir(i, ay, net, brut):
    return {
        "id": i, "musteri_id": 7, "gecerlilik_ay": ay, "yeni_net": net, "kdv_oran": 20,
        "yeni_brut": brut, "nakit_tutar": None, "banka_tutar": net, "olusturan": "test",
        "created_at": None, "iptal_at": None, "iptal_eden": None,
    }


def _bolum(sayfa):
    return sayfa.evaluate("(document.getElementById('plan_gecmis_bolum') || {textContent: ''}).textContent")


def _liste_bekle(sayfa, ifade="document.querySelectorAll('#plan_gecmis_bolum button').length >= 1"):
    sayfa.evaluate("planKapisiniYenile(7)")
    sayfa.wait_for_function("() => " + ifade, timeout=5000)


def senaryolar(sayfa, app, gorunen, diyalog):
    tx = app.test_tx
    api._resync = lambda mid: True
    api._bugun = lambda: date(2026, 10, 9)
    eski_fetch = dbmod.fetch_all
    dbmod.fetch_all = lambda *a, **k: [dict(s) for s in tx.bellek.satirlar if int(s["musteri_id"]) == 7]
    try:
        # ---------- 1) Eski liste + başka yerden iptal edilmiş plan -> 404 "Açık plan yok." ----------
        tx.bellek.satirlar.append(_satir(41, date(2026, 12, 1), 1000, 1200))
        sayfa.evaluate("selectedId = 7")
        _liste_bekle(sayfa)
        for s in tx.bellek.satirlar:  # başka sekme / otomatik iptal planı kapattı; ekrandaki liste eski
            if s["id"] == 41:
                s["iptal_at"], s["iptal_eden"] = datetime(2026, 10, 9, 12, 0, 0), "baska-sekme"
        n0 = len(gorunen)
        d0 = len(diyalog)
        sayfa.evaluate("document.querySelector('#plan_gecmis_bolum button').click()")
        sayfa.wait_for_function("() => { var b = document.getElementById('plan_gecmis_bolum'); return b && b.textContent.indexOf('zaten') >= 0; }", timeout=5000)
        iptal = [g for g in gorunen[n0:] if g[0] == "POST" and g[1].endswith("/plan/41/iptal")]
        liste_get = [g for g in gorunen[n0:] if g[0] == "GET" and g[1] == "/giris/api/musteri/7/plan"]
        metin = _bolum(sayfa)
        check("404 'Acik plan yok': alert yok", len(diyalog) == d0, diyalog[d0:])
        check("404 'Acik plan yok': tek iptal istegi, ardindan liste sunucudan yenilendi", len(iptal) == 1 and len(liste_get) >= 1, (len(iptal), len(liste_get)))
        check("404: mesaj 'plan zaten iptal edilmis, liste guncellendi' gosterilir", "plan zaten iptal edilmiş, liste güncellendi" in metin.lower(), metin)
        check("404: yenilenen listede aktif plan / Iptal dugmesi yok", sayfa.locator("#plan_gecmis_bolum button").count() == 0 and "iptal" in metin and "aktif" not in metin.replace("Plan geçmişi", ""), metin)
        check("404: iptal kilidi acildi", sayfa.evaluate("!window.__planIptalDevam") is True)

        # ---------- 2) Çift tıklama tek istek, istek boyunca düğme kilitli ----------
        tx.bellek.satirlar.append(_satir(42, date(2026, 12, 1), 1000, 1200))
        sayfa.evaluate("selectedId = 7")
        _liste_bekle(sayfa)
        n0 = len(gorunen)
        durum = sayfa.evaluate(
            """() => {
                const b = document.querySelector('#plan_gecmis_bolum button');
                b.click(); const k1 = b.disabled; b.click(); b.click();
                return {kilitli: k1, devam: window.__planIptalDevam === true};
            }"""
        )
        sayfa.wait_for_function("() => !window.__planIptalDevam", timeout=5000)
        sayfa.wait_for_timeout(300)
        iptal = [g for g in gorunen[n0:] if g[0] == "POST" and g[1].endswith("/plan/42/iptal")]
        satir42 = [s for s in tx.bellek.satirlar if s["id"] == 42][0]
        check("cift tiklama: tek iptal istegi", len(iptal) == 1, len(iptal))
        check("cift tiklama: istek boyunca dugme kilitli", durum["kilitli"] is True and durum["devam"] is True, durum)
        check("cift tiklama: plan iptal edildi, 'zaten iptal' mesaji yok", satir42["iptal_at"] is not None and "zaten" not in _bolum(sayfa).lower(), _bolum(sayfa))

        # ---------- 3) Aynı aya yeni plan (otomatik iptal) -> geçmiş listesi sunucu cevabına göre ----------
        tx.bellek.satirlar.append(_satir(43, date(2027, 6, 1), 1800, 2160))
        eski_zincir = api._onizleme_zinciri

        def zincir(mid, kyc):
            z, _m, k = eski_zincir(mid, kyc)
            acik = [dict(s) for s in tx.bellek.satirlar if s["iptal_at"] is None and int(s["musteri_id"]) == int(mid)]
            return z, acik, k

        api._onizleme_zinciri = zincir
        try:
            _liste_bekle(sayfa)
            ilk = _bolum(sayfa)
            check("yeni plan oncesi: 2027-06 net 1800 aktif gorunur", "2027-06" in ilk and "net 1800" in ilk, ilk)
            sayfa.select_option("#musteri_durum", "plan")
            sayfa.wait_for_selector("#plan_degistir_modal", state="visible", timeout=3000)
            _doldur(sayfa, ay="2027-06", net="2000")
            _onizle(sayfa)
            n0 = len(gorunen)
            sayfa.click("#plan_kaydet")
            sayfa.wait_for_function("() => { var b = document.getElementById('plan_gecmis_bolum'); return b && b.textContent.indexOf('net 2000') >= 0; }", timeout=6000)
            post = [g for g in gorunen[n0:] if g[0] == "POST" and g[1] == "/giris/api/musteri/7/plan"]
            get_ = [g for g in gorunen[n0:] if g[0] == "GET" and g[1] == "/giris/api/musteri/7/plan"]
            satirlar = sayfa.evaluate(
                "[...document.querySelectorAll('#plan_gecmis_bolum > div')].slice(1).map(d => d.textContent)"
            )
            eski43 = [s for s in tx.bellek.satirlar if s["id"] == 43][0]
            yeni = [s for s in tx.bellek.satirlar if s["gecerlilik_ay"] == date(2027, 6, 1) and s["iptal_at"] is None]
            check("yeni plan: tek POST, ardindan liste sunucudan cekildi", len(post) == 1 and len(get_) >= 1, (len(post), len(get_)))
            check("yeni plan: sunucuda eski iptal, yeni aktif", eski43["iptal_at"] is not None and len(yeni) == 1 and float(yeni[0]["yeni_net"]) == 2000)
            iptalli = [t for t in satirlar if "2027-06" in t and "net 1800" in t and "iptal" in t]
            aktifli = [t for t in satirlar if "2027-06" in t and "net 2000" in t and "aktif" in t]
            check("yeni plan: gecmis listesi sunucuya gore (1800 iptal + 2000 aktif)", len(iptalli) == 1 and len(aktifli) == 1, satirlar)
            check("yeni plan: eski 1800 plani artik 'aktif' degil", not [t for t in satirlar if "net 1800" in t and "aktif" in t], satirlar)
        finally:
            api._onizleme_zinciri = eski_zincir
    finally:
        dbmod.fetch_all = eski_fetch
        api._bugun = lambda: date(2024, 10, 9)


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
            sayfa.evaluate("selectedId = 7")
            senaryolar(sayfa, app, gorunen, diyalog)
            check("hic alert cikmadi", diyalog == [], diyalog)
        finally:
            tarayici.close()
    if FAILS:
        print("PLAN_IPTAL_UI_FAIL", FAILS)
        return 1
    print("plan_iptal_ui ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
