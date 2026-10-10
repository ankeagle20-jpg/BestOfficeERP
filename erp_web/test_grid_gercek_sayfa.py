# -*- coding: utf-8 -*-
"""GERCEK giris/index.html (Flask sablonu) + sentetik API yanitlari ile aylik grid olcumu.

867 benzeri kart: reel DB var (2024 donemi 1500), plan 2024-11'den baslar (2400), plan_var cache sunucu uretimi.
Kart sec -> 3 tur yenileme -> gecikmeli setTimeout turu; her kart yazimi (data-tutar-kdv, data-brut-kdv,
.aylik-deger metni) setAttribute/textContent sarmalayicisiyla yazici fonksiyon adiyla kaydedilir.
Canli DB'ye ve .env'e gitmez.
"""
import copy
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    print("SKIP playwright yok")
    raise SystemExit(0)

import auth  # noqa: E402
import test_plan_kalici as K  # noqa: E402
from routes import giris_routes as gr  # noqa: E402
from test_plan_degistir import _app  # noqa: E402
from test_plan_onizle_tarayici import _auth_yamala  # noqa: E402

BASE = "http://grid.test"
MID = 867
FAILS = []
JS_ESKI = os.environ.get("INDEX_HTML_YOL")  # ayirt edicilik denemesi: eski index.html


ETIKET = [""]


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + ETIKET[0] + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


INIT = r"""
(() => {
  window.__yaz = [];
  const T0 = performance.now();
  function yazici() {
    const s = (new Error().stack || '').split('\n').slice(2, 10).map(x => { const m = x.match(/at (?:async )?([^ (]+)(?: \(.*?:(\d+):\d+\))?/) || []; const l = (x.match(/:(\d+):\d+\)?$/) || [])[1]; return (m[1] || '?') + (l ? '@' + l : ''); });
    return s.filter(x => !/^(kayit|HTMLDivElement|Object\.defineProperty|Element\.)/.test(x)).slice(0, 5).join('<');
  }
  function kart(el) { return el && el.closest ? el.closest('.sozlesmeler-ay-kart[data-ay-key]') : null; }
  function kayit(c, tur, deger) {
    const g = c.closest('#sozlesmeler-reel-aylik-grid') ? 'reel' : (c.closest('#sozlesmeler-aylik-grid') ? 'normal' : '?');
    window.__yaz.push({t: Math.round(performance.now() - T0), grid: g, ay: c.getAttribute('data-ay-key'), tur, deger: String(deger), kim: yazici()});
  }
  const sa = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (n, v) {
    if (n === 'data-tutar-kdv' || n === 'data-brut-kdv') { const c = this.classList && this.classList.contains('sozlesmeler-ay-kart') ? this : null; if (c && c.getAttribute('data-ay-key')) kayit(c, n, v); }
    return sa.apply(this, arguments);
  };
  const d = Object.getOwnPropertyDescriptor(Node.prototype, 'textContent');
  Object.defineProperty(Node.prototype, 'textContent', {
    configurable: true, get: d.get,
    set: function (v) {
      if (this.classList && this.classList.contains('aylik-deger')) { const c = kart(this); if (c) kayit(c, 'metin', v); }
      return d.set.call(this, v);
    }
  });
})();
"""


# Cok yilli reel harita: istemci reel yil boyayicilari plan aylarini ezmeye calisir
REEL_EK = {"2025": 1800.0, "2026": 2000.0, "2027": 2200.0}
for _y, _v in REEL_EK.items():
    K.MANUEL[int(_y)] = _v


def _bundle(payload):
    return {
        "ok": True,
        "musteri": {"ok": True, "musteri": {
            "id": MID, "name": "Test 867", "durum": "aktif", "sozlesme_tarihi": "2023-08-01", "sozlesme_baslangic": "2023-08-01",
            "sozlesme_bitis": "2030-08-01", "kira_artis_tarihi": "2023-08-01", "aylik_kira": 1000, "ilk_kira_bedeli": 1000,
            "guncel_kira_bedeli": 1000, "kdv_oran": 20, "kira_nakit": False, "kira_banka": False}},
        "tahsil_durum": {"ok": True, "aylar": []},
        "grid": {"ok": True, "cache": payload},
        "reel": {"ok": True, "map": dict({"2024": 1500.0}, **REEL_EK), "detay_map": {}},
    }


def _payload(mismatch=False):
    eski = K._yama(K.PLAN)
    try:
        p = K._kayitli_plan_payload()
        p = json.loads(json.dumps(p, default=str))
        p["musteri_id"] = MID
        if not mismatch:
            p["kira_banka_tutar"] = 1000.0  # form (banka kirasi net 1000) ile uyumlu: gercek 867 akisi cache render yoluna girer
    finally:
        K._geri(eski)
    return p


def _kartlar(sayfa):
    return sayfa.evaluate(
        """() => { const o = {}; ['sozlesmeler-aylik-grid','sozlesmeler-reel-aylik-grid'].forEach(g => {
            const r = {}; document.querySelectorAll('#' + g + ' .sozlesmeler-ay-kart[data-ay-key]').forEach(c => {
              const d = c.querySelector('.aylik-deger');
              r[c.getAttribute('data-ay-key')] = {metin: d ? d.textContent : null, tutar: c.getAttribute('data-tutar-kdv'), brut: c.getAttribute('data-brut-kdv')}; });
            o[g] = r; }); return o; }"""
    )


def kosu(mismatch, html_yol, etiket):
    """Bir senaryoyu kosar; (basarisiz_kontroller, plan_disi_yazim_sayisi) doner."""
    global FAILS
    FAILS = []
    ETIKET[0] = etiket + ": "
    payload = _payload(mismatch)
    plan_ay = {(a["yil"], a["ay"]): a["tutar_kdv_dahil"] for a in payload["aylar"]}
    check("sentetik payload plan_var ve 2024-10=1500, 2024-11=2400", payload.get("plan_var") is True and plan_ay[(2024, 10)] == 1500 and plan_ay[(2024, 11)] == 2400)
    bundle = _bundle(payload)
    app = _app()
    app.logger.disabled = True
    _auth_yamala()
    from werkzeug.routing.exceptions import BuildError
    _uf = app.jinja_env.globals["url_for"]

    def url_for_toleransli(endpoint, **values):  # test uygulamasinda kayitli olmayan blueprint'ler (auth.*, vb.)
        try:
            return _uf(endpoint, **values)
        except BuildError:
            return "/yok/" + str(endpoint)

    app.jinja_env.globals["url_for"] = url_for_toleransli
    app.jinja_env.globals["has_module"] = lambda k: True  # app.py context_processor karsiligi
    app.jinja_env.globals["is_ledger_only"] = lambda *a, **k: False
    if html_yol:  # eski/alternatif index.html: sablon yukleyicisine bindir, route aynen calissin
        import jinja2
        app.jinja_env.loader = jinja2.ChoiceLoader([jinja2.DictLoader({"giris/index.html": Path(html_yol).read_text(encoding="utf-8")}), app.jinja_env.loader])
    client = app.test_client()
    istekler = []

    def isle(route, request):
        yol = request.url[len(BASE):]
        if yol.startswith("/giris/api/musteri-kart-bundle"):
            istekler.append("bundle")
            route.fulfill(status=200, content_type="application/json", body=json.dumps(bundle))
            return
        if yol.startswith("/giris/api/aylik-grid-cache"):
            istekler.append("grid-cache")
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "cache": payload}))
            return
        if yol.startswith("/giris/api/aylik-tahsil-durum"):
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "aylar": []}))
            return
        if yol.startswith("/giris/api/reel-donem-tutarlar"):
            route.fulfill(status=200, content_type="application/json", body=json.dumps(bundle["reel"]))
            return
        if yol.startswith("/giris/api/musteri/%d" % MID) and "/" not in yol[len("/giris/api/musteri/%d" % MID):].split("?")[0]:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(bundle["musteri"]))
            return
        if yol.startswith("/giris/api/"):
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "liste": [], "musteriler": [], "map": {}, "aylar": []}))
            return
        r = client.get(yol)
        route.fulfill(status=r.status_code, content_type=r.content_type or "text/html", body=r.get_data())

    beklenen = {}
    for (y, m), v in plan_ay.items():
        beklenen["%d-%d" % (y, m)] = v

    with sync_playwright() as pw:
        try:
            tarayici = pw.chromium.launch(channel="msedge", headless=True)
        except Exception as exc:  # pragma: no cover
            print("SKIP tarayici yok:", str(exc)[:80])
            return None, 0
        s = tarayici.new_page(viewport={"width": 1400, "height": 900})
        hatalar = []
        s.on("pageerror", lambda e: hatalar.append(str(e)[:120]))
        s.add_init_script(INIT)
        s.route("**/*", isle)
        s.goto(BASE + "/giris/", wait_until="load")
        s.wait_for_function("typeof selectMusteri === 'function'", timeout=15000)
        s.evaluate("selectMusteri(%d)" % MID)
        s.wait_for_function("document.querySelectorAll('#sozlesmeler-aylik-grid .sozlesmeler-ay-kart').length > 20", timeout=15000)
        s.wait_for_timeout(1500)
        def dogru(grid_id):
            k = _kartlar(s)[grid_id]
            yanlis = {a: (k[a]["metin"], beklenen.get(a)) for a in k if a in beklenen and beklenen[a] is not None
                      and k[a]["metin"] not in ("%.2f" % beklenen[a], "%.2f" % round(beklenen[a], 2))}
            return yanlis, len(k)

        for gid in ("sozlesmeler-aylik-grid", "sozlesmeler-reel-aylik-grid"):
            yanlis, n = dogru(gid)
            check("gercek sayfa: ilk yukleme sonrasi %s plan rakamlarinda (%d kart)" % (gid.split("-")[1], n), n > 20 and not yanlis, dict(list(yanlis.items())[:5]))

        # 3 tur yenileme (gercek fonksiyonlar) + gecikmeli tur
        for tur in (1, 2, 3):
            s.evaluate("""() => { try { sozlesmelerReelDbGridVePanelSonBoya(); } catch (e) {} try { sozlesmelerAylikNormGridKartTutarlariniYenile(); } catch (e) {}
                try { sozlesmelerReelKayitliTumYillariGridaUygula(); } catch (e) {} try { sozlesmelerReelDbSonBoyaSira(window.__sozlesmelerAylikSonCacheObj); } catch (e) {} }""")
            s.wait_for_timeout(250)
            for gid in ("sozlesmeler-aylik-grid", "sozlesmeler-reel-aylik-grid"):
                yanlis, n = dogru(gid)
                check("gercek sayfa: %d. yenileme sonrasi %s plan rakamlarinda" % (tur, gid.split("-")[1]), not yanlis, dict(list(yanlis.items())[:5]))
        s.evaluate("""() => { try { sozlesmelerReelDbGridVePanelSonBoya(); } catch (e) {} }""")
        s.wait_for_timeout(1200)  # setTimeout turu (120/350 ms gecikmeli duzeltmeler)
        for gid in ("sozlesmeler-aylik-grid", "sozlesmeler-reel-aylik-grid"):
            yanlis, n = dogru(gid)
            check("gercek sayfa: gecikmeli tur sonrasi %s plan rakamlarinda" % gid.split("-")[1], not yanlis, dict(list(yanlis.items())[:5]))

        # yazici raporu: plan ayi (2024-11+) icin plan disi deger yazanlar
        yaz = s.evaluate("window.__yaz")
        kotu = {}
        for y in yaz:
            ay = y["ay"]
            if ay not in beklenen or beklenen[ay] is None:
                continue
            yy, mm = [int(x) for x in ay.split("-")]
            if (yy, mm) < (2024, 11):
                continue
            if y["tur"] == "metin" and y["deger"] not in ("%.2f" % beklenen[ay], "0.00", "-"):
                kotu.setdefault(y["kim"], 0)
                kotu[y["kim"]] += 1
        print("PLAN AYINA PLAN-DISI METIN YAZANLAR:", json.dumps(kotu, ensure_ascii=False))
        print("TOPLAM YAZIM:", len(yaz), "sayfa hatalari:", hatalar[:3])
        if os.environ.get("YAZ_DOK"):
            Path(os.environ["YAZ_DOK"]).write_text(json.dumps(yaz, ensure_ascii=False, indent=0), encoding="utf-8")
        tarayici.close()
    return list(FAILS), sum(kotu.values())


def _eski_index(dosya):
    import subprocess
    cikti = subprocess.run(["git", "show", "4bd3e11:erp_web/templates/giris/index.html"], cwd=str(ROOT.parent), capture_output=True)
    if cikti.returncode != 0:
        return None
    Path(dosya).write_bytes(cikti.stdout)
    return dosya


def main():
    import tempfile
    sonuc = 0
    for ad, mismatch in (("A cache uyumlu", False), ("B cache formla uyumsuz", True)):
        fails, kotu = kosu(mismatch, None, "YENI " + ad)
        if fails is None:
            return 0
        print("YENI %s: basarisiz=%d plan_disi_yazim=%d" % (ad, len(fails), kotu))
        if fails or kotu:
            sonuc = 1
    # eski index.html (4bd3e11): senaryolardan en az biri FAIL olmali (testin ayirt ediciligi)
    eski = _eski_index(os.path.join(tempfile.gettempdir(), "_index_4bd3e11.html"))
    if eski:
        kotu_eski = 0
        fail_eski = 0
        for ad, mismatch in (("A", False), ("B", True)):
            f, k = kosu(mismatch, eski, "ESKI " + ad)
            fail_eski += len(f or [])
            kotu_eski += k
        print("ESKI index: basarisiz=%d plan_disi_yazim=%d" % (fail_eski, kotu_eski))
        if not (fail_eski > 0 and kotu_eski > 0):
            print("FAIL eski index ile test FAIL etmeli")
            sonuc = 1
    print("grid_gercek_sayfa " + ("FAIL" if sonuc else "ok"))
    return sonuc


if __name__ == "__main__":
    raise SystemExit(main())
