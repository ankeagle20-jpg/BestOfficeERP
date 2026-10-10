# -*- coding: utf-8 -*-
"""Planli kartta grid / Detay Tablosu / borclandirma plan brutunu korur (yerel, DB ve prod yok).

1) Sunucu: /api/aylik-tutarlardan-borclandir planli kartta bayat istemci tutarini plan brutune cevirir.
2) Tarayici (Playwright): 867 benzeri sentetik kart (reel DB var, plan Eki'de baslar):
   grid boyama ve Detay Tablosu 1., 2., 3. yenilemeden sonra da Agu-Eyl onceki rakam, Eki+ plan bruti.
index.html'deki GERCEK fonksiyonlar regex ile cikarilip sentetik sayfada calistirilir."""
import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


from flask import Flask  # noqa: E402

from routes import giris_routes as gr  # noqa: E402

MID = 867
PLAN = [{"gecerlilik_ay": "2026-10-01", "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400}]
PLAN_PAYLOAD = {
    "plan_var": True,
    "aylar": [
        {"yil": 2026, "ay": 8, "tutar_kdv_dahil": 1918.76},
        {"yil": 2026, "ay": 9, "tutar_kdv_dahil": 1918.76},
        {"yil": 2026, "ay": 10, "tutar_kdv_dahil": 2400.0},
        {"yil": 2027, "ay": 7, "tutar_kdv_dahil": 2400.0},
        {"yil": 2027, "ay": 8, "tutar_kdv_dahil": 3165.6},
    ],
}


def test_sunucu_borclandir():
    eski = {n: getattr(gr, n) for n in (
        "fetch_one", "fetch_all", "execute", "ensure_faturalar_amount_columns", "_plan_paket_yukle",
        "_read_aylik_grid_cache_payload", "sync_musteri_panel_borclu_from_satirlar",
        "_invalidate_aylik_grid_payload_mem", "_defer_aylik_grid_cache_rebuild")}
    import fatura_belge_no
    eski_no = fatura_belge_no.next_dahili_no
    yazilan, senkron = [], []
    sayac = {"n": 0}

    def no():
        sayac["n"] += 1
        return "D-%d" % sayac["n"]

    fatura_belge_no.next_dahili_no = no
    gr.ensure_faturalar_amount_columns = lambda: None
    gr.fetch_one = lambda sql, params=None: {"id": 1, "name": "Test"} if "FROM customers" in sql else (
        {"id": 99} if "WHERE fatura_no" in sql else None)
    gr.fetch_all = lambda sql, params=None: []
    gr.execute = lambda sql, params=None: yazilan.append(params) if "INSERT INTO faturalar" in sql else None
    gr.sync_musteri_panel_borclu_from_satirlar = lambda mid, satirlar: senkron.append(copy.deepcopy(satirlar)) or {}
    gr._invalidate_aylik_grid_payload_mem = lambda mid: None
    gr._defer_aylik_grid_cache_rebuild = lambda mid: None
    app = Flask(__name__)
    f = gr.api_aylik_tutarlardan_borclandir.__wrapped__
    # bayat istemci: plan oncesi Agu (dogru), Eki ve 2027-08 eski rakam
    istemci = [
        {"yil": 2026, "ay": 8, "tutar_kdv_dahil": 1918.76},
        {"yil": 2026, "ay": 10, "tutar_kdv_dahil": 1918.76},
        {"yil": 2027, "ay": 8, "tutar_kdv_dahil": 1700.0},
    ]

    def cagir(plan_var):
        gr._plan_paket_yukle = lambda mids: {int(m): {"planlar": PLAN if plan_var else [], "faturali": [], "belge": {}} for m in mids}
        gr._read_aylik_grid_cache_payload = lambda mid: copy.deepcopy(PLAN_PAYLOAD) if plan_var else {"aylar": PLAN_PAYLOAD["aylar"]}
        yazilan.clear()
        senkron.clear()
        with app.test_request_context("/x", method="POST", json={"musteri_id": MID, "kira_nakit": False, "satirlar": copy.deepcopy(istemci)}):
            yanit = f()
        return [float(p[5]) for p in yazilan], [x["tutar_kdv_dahil"] for x in (senkron[0] if senkron else [])], yanit

    try:
        fat, pan, yanit = cagir(True)
        check("planli: fatura toplami plan brutu (plan oncesi ay istemci degerinde)", fat == [1918.76, 2400.0, 3165.6], fat)
        check("planli: panel borclu senkronu da plan brutu", pan == [1918.76, 2400.0, 3165.6], pan)
        check("planli: yanit ok", yanit.get_json().get("ok") is True)
        fat, pan, _y = cagir(False)
        check("plansiz: istemci tutarlari AYNEN", fat == [1918.76, 1918.76, 1700.0] and pan == [1918.76, 1918.76, 1700.0], (fat, pan))
        # cache okunamazsa (None) veya hata: istemci tutari aynen
        gr._plan_paket_yukle = lambda mids: {int(m): {"planlar": PLAN, "faturali": [], "belge": {}} for m in mids}
        gr._read_aylik_grid_cache_payload = lambda mid: None
        check("planli ama cache yok: yardimci bos, istemci aynen", gr._borc_plan_brut_haritasi(MID) == {})
        gr._read_aylik_grid_cache_payload = lambda mid: (_ for _ in ()).throw(RuntimeError("x"))
        check("okuma hatasi: yardimci bos", gr._borc_plan_brut_haritasi(MID) == {})
    finally:
        for n, v in eski.items():
            setattr(gr, n, v)
        fatura_belge_no.next_dahili_no = eski_no


def _fn(html, ad):
    m = re.search(r"^function " + ad + r"\(.*?^\}", html, flags=re.S | re.M)
    return m.group(0) if m else ""  # eski kodda olmayan yardimci: atla (ayirt edicilik denemesi)


def test_tarayici():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("SKIP playwright yok")
        return
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8", errors="replace")
    adlar = [
        "girisPlanGridHucreBrut", "sozlesmelerAylikCacheBrutFlatDisiUygula", "_sozlesmelerAylikTekGridKartTutarlariniYenile",
        "sozlesmelerAylikNormGridKartTutarlariniYenile", "sozlesmelerAylikCacheReelDbEsasla", "_sozlesmelerAylikCacheAyTahsilDokunmaMi",
        "girisAylikSatirYilAyPlanVars", "girisAylikSatirYilAyYildanSenkron", "girisAylikSatirYilAyPanelHtml",
        "girisAylikSatirYilAyHesapla", "girisAylikSatirYilAyVarsayilan", "girisAylikSatirYilAyId",
    ]
    kod = "\n".join(_fn(html, a) for a in adlar)
    aylar = [(2026, 8), (2026, 9)] + [(2026, m) for m in (10, 11, 12)] + [(2027, m) for m in range(1, 9)]
    kartlar = "".join(
        '<div class="sozlesmeler-ay-kart" data-ay-key="%d-%d" data-yil="%d" data-ay="%d"><span class="aylik-deger">0</span></div>' % (y, m, y, m)
        for y, m in aylar
    )
    sayfa = """<html><body>
    <input id="sozlesme_baslangic" value="2025-08-01"><input id="kdv_oran" value="20">
    <div id="sozlesmeler-aylik-grid">%s</div><div id="sozlesmeler-reel-aylik-grid">%s</div>
    <script>
    var SOZLESME_TAM_ODENDI_TOLERANS = 0.05;
    %s
    var FLAT = {}; ['2026-8','2026-9','2026-10','2026-11','2026-12','2027-1','2027-2','2027-3','2027-4','2027-5','2027-6','2027-7'].forEach(function (k) { FLAT[k] = 1918.76; });
    window.__musteriReelDonemTutarlari = { 2026: 1918.76 };
    var sozlesmeTahsilEdilenAyAnahtarlari = new Set();
    function sozlesmeAylikAyKeyNormalize(k) { return String(k); }
    function parseTarihStr(s) { return new Date(s); }
    function girisAylikSatirYilKayitYukle() {}
    function sozlesmelerAylikKartDonemYili(b, y, m) { return y; }
    function sozlesmelerReelDbAyKeyFlatMap() { return FLAT; }
    function girisTahsilatYilAyPanelGridaKartUygula() { return false; }
    function sozlesmelerReelDbTutarAyKey(b, ak) { return FLAT[ak] != null ? FLAT[ak] : null; }
    var DETAY = {};
    function girisAylikSatirYilAyDetayMap(y) { DETAY[y] = DETAY[y] || {}; return DETAY[y]; }
    function girisAylikSatirYilHesapla(y) { return { aylik: 1599.0, kdv_dahil: 1918.76, toplam: 1918.76, nakit: 0, banka: 1918.76, reel: 1918.76 }; }
    function girisAylikSatirYilReelGoster() { return 1918.76; }
    function girisAylikSatirIlkYilMi() { return false; }
    function girisAylikSatirYilAyAdiGoster(k) { return k; }
    function girisTahsilatAyKeyToYilAy(k) { var a = String(k).split('-'); return { yil: parseInt(a[0], 10), ay: parseInt(a[1], 10) }; }
    function ayKeys() { var o = []; for (var m = 8; m <= 12; m++) o.push('2026-' + m); for (var m2 = 1; m2 <= 7; m2++) o.push('2027-' + m2); return o; }
    function gridDegerleri(gid) { var o = {}; document.querySelectorAll('#' + gid + ' .sozlesmeler-ay-kart').forEach(function (c) { o[c.getAttribute('data-ay-key')] = c.querySelector('.aylik-deger').textContent; }); return o; }
    function detayToplamlar() {
        var keys = ayKeys(); girisAylikSatirYilAyYildanSenkron(2026, keys);
        var h = document.createElement('div'); h.innerHTML = girisAylikSatirYilAyPanelHtml(2026, keys);
        var o = {}; h.querySelectorAll('.aylik-yil-ay-satir[id]').forEach(function (r) { o[r.id.replace('aylik_yil_ay_2026_', '')] = r.querySelector('.aylik-yil-ay-toplam').value; });
        return o;
    }
    function yenile() {
        sozlesmelerAylikCacheReelDbEsasla(window.__sozlesmelerAylikSonCacheObj);
        sozlesmelerAylikNormGridKartTutarlariniYenile();
        sozlesmelerAylikCacheBrutFlatDisiUygula();
    }
    </script></body></html>""" % (kartlar, kartlar, kod)

    def cache(plan_var):
        o = {"baslangic": "2025-08-01", "aylar": []}
        if plan_var:
            o["plan_var"] = True
        for y, m in aylar:
            t = 1918.76 if (y, m) < (2026, 10) else (3165.6 if (y, m) == (2027, 8) else 2400.0)
            if not plan_var:
                t = 1918.76
            o["aylar"].append({"yil": y, "ay": m, "ay_key": "%d-%d" % (y, m), "tutar_kdv_dahil": t, "brut_tutar_kdv": t,
                               "odenen_tutar_kdv": 0, "kalan_tutar_kdv": t, "tahsil_edildi": False, "kismi_tahsilat": False})
        return o

    with sync_playwright() as pw:
        try:
            tarayici = pw.chromium.launch(channel="msedge")
        except Exception:
            tarayici = pw.chromium.launch()
        s = tarayici.new_page()
        s.on("pageerror", lambda e: print("PAGEERROR", e))
        s.on("console", lambda m: None)
        s.set_content(sayfa)
        s.evaluate("c => { window.__sozlesmelerAylikSonCacheObj = c; }", cache(True))
        beklenen = {}
        for y, m in aylar:
            beklenen["%d-%d" % (y, m)] = "1918.76" if (y, m) < (2026, 10) else ("3165.60" if (y, m) == (2027, 8) else "2400.00")
        for tur in (1, 2, 3):
            s.evaluate("yenile()")
            for gid in ("sozlesmeler-aylik-grid", "sozlesmeler-reel-aylik-grid"):
                g = s.evaluate("gridDegerleri('%s')" % gid)
                check("planli grid %s %d. yenileme: Agu-Eyl onceki, Eki+ plan bruti" % (gid.split('-')[1], tur), g == beklenen, {k: (g[k], beklenen[k]) for k in g if g[k] != beklenen[k]})
            d = s.evaluate("detayToplamlar()")
            bd = {("%d_%d" % (y, m)): ("1918.76" if (y, m) < (2026, 10) else ("3165.6" if (y, m) == (2027, 8) else "2400")) for y, m in aylar if (y, m) >= (2026, 8)}
            bd = {k: v for k, v in bd.items() if k in d}
            check("planli Detay Tablosu %d. yenileme: Agu-Eyl onceki, Eki+ plan bruti" % tur, d == bd, {k: (d.get(k), bd[k]) for k in bd if d.get(k) != bd[k]})
        # plansiz kart: eski yol AYNEN (reel haritasi tum aylara)
        s.evaluate("c => { window.__sozlesmelerAylikSonCacheObj = c; DETAY = {}; }", cache(False))
        s.evaluate("yenile()")
        g = s.evaluate("gridDegerleri('sozlesmeler-aylik-grid')")
        check("plansiz grid: reel haritasi aynen (tum aylar 1918.76)", all(g[k] == "1918.76" for k in g if k != "2027-8") , g)
        d = s.evaluate("detayToplamlar()")
        check("plansiz Detay: yil degeri tum aylara", all(v == "1918.76" for v in d.values()), d)
        tarayici.close()


if __name__ == "__main__":
    test_sunucu_borclandir()
    test_tarayici()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
