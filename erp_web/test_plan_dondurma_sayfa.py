# -*- coding: utf-8 -*-
"""Gercek giris sayfasi: dondurulan ay 3 yenileme ve gecikmeli turda 0 kalir. Canli DB yok."""
import test_grid_867_gercek as t

AY = (2026, 11)
ANAHTAR = "2026-11"


def _panel(s):
    return s.evaluate(
        """() => {
            var det = window.__tahsilatSatirYilAyDetay || {};
            var yil = det['2026'] || {};
            var row = yil['2026-11'];
            if (!row) return null;
            return row.aylik_tutar;
        }"""
    )


def _ek(s, ad):
    kart = t._kartlar(s).get("sozlesmeler-aylik-grid", {}).get(ANAHTAR) or {}
    t.check(ad + " grid dondurma 0", kart.get("metin") == "0.00", kart)
    panel = _panel(s)
    t.check(ad + " tahsilat panel dondurma 0", panel == 0, panel)


ILK = r"""
(() => {
  window.__ilk = [];
  const ih = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
  function bak(root) {
    ['2026-2','2026-3','2026-4','2025-6','2026-5'].forEach((ay) => {
      const c = root.querySelector('.sozlesmeler-ay-kart[data-ay-key="' + ay + '"]');
      if (!c) return;
      const deg = c.querySelector('.aylik-deger');
      window.__ilk.push({ay: ay, sinif: c.className, metin: deg ? deg.textContent : ''});
    });
  }
  Object.defineProperty(Element.prototype, 'innerHTML', {
    configurable: true,
    get: ih.get,
    set: function (v) {
      const r = ih.set.call(this, v);
      if (this.id === 'sozlesmeler-aylik-grid') bak(this);
      return r;
    }
  });
})();
"""

SNAP = r"""() => {
  function kart(ay) {
    const c = document.querySelector('#sozlesmeler-aylik-grid .sozlesmeler-ay-kart[data-ay-key="' + ay + '"]');
    if (!c) return null;
    const deg = c.querySelector('.aylik-deger');
    return {metin: deg ? deg.textContent : '', sinif: c.className};
  }
  const grid = {};
  ['2026-2','2026-3','2026-4','2025-6','2026-5'].forEach((ay) => { grid[ay] = kart(ay); });
  let panel = {};
  try {
    if (typeof girisTahsilatYilPanelDoldur === 'function') girisTahsilatYilPanelDoldur();
    window.__tahsilatSatirYilAcik = window.__tahsilatSatirYilAcik || {};
    window.__tahsilatSatirYilAcik['2025'] = true;
    if (typeof girisTahsilatYilAyPanelGuncelle === 'function') girisTahsilatYilAyPanelGuncelle(2025, {sunucuPanelYuklendi: true});
  } catch (e) {}
  function panelOku(k) {
    var row = document.getElementById('tahsilat_yil_ay_2025_' + k);
    if (!row) {
      var bulunan = document.querySelector('[id$="_' + k + '"]');
      if (bulunan && bulunan.classList && bulunan.classList.contains('tahsilat-yil-ay-satir')) row = bulunan;
    }
    if (!row) return null;
    var aylik = row.querySelector('.tahsilat-yil-ay-aylik');
    var kalan = row.querySelector('.tahsilat-yil-ay-kalan');
    return {
      aylik: aylik ? aylik.value : '',
      kalan: kalan ? kalan.value : '',
      borc: row.classList.contains('is-borclandir'),
      id: row.id
    };
  }
  ['2026_2','2026_3','2026_4','2025_6'].forEach(function (k) { panel[k] = panelOku(k); });
  return {grid: grid, panel: panel};
}"""


def _kirmizi_sifir(kart, ad):
    sinif = (kart or {}).get("sinif") or ""
    t.check(
        ad + " 0.00 ve kirmizi",
        (kart or {}).get("metin") == "0.00"
        and "sozlesmeler-ay-kart-odenmedi" in sinif
        and "sozlesmeler-ay-kart-odendi" not in sinif,
        kart,
    )


def _yesil_kaldi(kart, ad):
    sinif = (kart or {}).get("sinif") or ""
    t.check(
        ad + " yesil kaldi",
        "sozlesmeler-ay-kart-odendi" in sinif and (kart or {}).get("metin") not in ("0.00", "0", "-", ""),
        kart,
    )


def zaman_cizelgesi():
    """Reel 1454,71 + tahsil seti dondurulan aylari icerse bile ilk HTML'den itibaren 0.00 kirmizi."""
    import json
    from playwright.sync_api import sync_playwright
    from werkzeug.routing.exceptions import BuildError

    eski_fx = t.fixture_867

    def fixture(senaryo="eski"):
        fx = eski_fx("eski")
        cache = fx["grid"]["cache"]
        cache["dondurma_var"] = True
        cache["dondurulan_aylar"] = ["2026-02", "2026-03", "2026-04"]
        for a in cache["aylar"]:
            if (a["yil"], a["ay"]) in ((2026, 2), (2026, 3), (2026, 4)):
                a["tutar_kdv_dahil"] = 1454.71
                a["brut_tutar_kdv"] = 1454.71
                a["odenen_tutar_kdv"] = 1454.71
                a["kalan_tutar_kdv"] = 0.0
                a["tahsil_edildi"] = True
        aylar = list(fx["tahsil_durum"].get("aylar") or [])
        for k in ("2026-2", "2026-3", "2026-4"):
            if k not in aylar:
                aylar.append(k)
        fx["tahsil_durum"]["aylar"] = aylar
        return fx

    t.fixture_867 = fixture
    fx = fixture()
    app = t._app()
    app.logger.disabled = True
    t._auth_yamala()
    _uf = app.jinja_env.globals["url_for"]

    def uf(endpoint, **values):
        try:
            return _uf(endpoint, **values)
        except BuildError:
            return "/yok/" + str(endpoint)

    app.jinja_env.globals["url_for"] = uf
    app.jinja_env.globals["has_module"] = lambda k: True
    app.jinja_env.globals["is_ledger_only"] = lambda *a, **k: False
    client = app.test_client()

    def js(route, govde, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(govde))

    def isle(route, request):
        u = request.url[len(t.BASE):]
        if u.startswith("/giris/api/musteri-kart-bundle"):
            js(route, {"ok": True, "musteri_id": 867, "musteri": fx["musteri"], "tahsil_durum": fx["tahsil_durum"], "grid": fx["grid"], "reel": fx["reel"]})
            return
        if u.startswith("/giris/api/aylik-grid-cache"):
            js(route, fx["grid"])
            return
        if u.startswith("/giris/api/aylik-tahsil-durum"):
            js(route, fx["tahsil_durum"])
            return
        if u.startswith("/giris/api/reel-donem-tutarlar"):
            js(route, fx["reel"])
            return
        if u.startswith("/giris/api/tahsilat-panel-detay"):
            js(route, {"ok": True, "by_iso": {}})
            return
        if u.startswith("/giris/api/musteri/") and "/" not in u[len("/giris/api/musteri/"):].split("?")[0]:
            js(route, fx["musteri"])
            return
        if u.startswith("/giris/api/"):
            js(route, {"ok": True, "liste": [], "musteriler": [], "map": {}, "aylar": []})
            return
        r = client.get(u)
        route.fulfill(status=r.status_code, content_type=r.content_type or "text/html", body=r.get_data())

    def ornek(ad, snap):
        for ay in ("2026-2", "2026-3", "2026-4"):
            _kirmizi_sifir((snap.get("grid") or {}).get(ay), ad + " " + ay)
        _yesil_kaldi((snap.get("grid") or {}).get("2025-6"), ad + " 2025-6")
        komsu = (snap.get("grid") or {}).get("2026-5") or {}
        t.check(ad + " 2026-5 dondurma yemedi", komsu.get("metin") not in ("0.00", "0", None) and "sozlesmeler-ay-kart-odendi" not in (komsu.get("sinif") or ""), komsu)
        for k in ("2026_2", "2026_3", "2026_4"):
            row = (snap.get("panel") or {}).get(k)
            t.check(
                ad + " panel " + k + " 0 ve borclandirma kapali",
                row and str(row.get("aylik")) in ("0", "0.00", "0.0") and row.get("borc") is False,
                row,
            )

    try:
        with sync_playwright() as pw:
            try:
                tar = pw.chromium.launch(channel="msedge", headless=True)
            except Exception as exc:
                print("SKIP tarayici", str(exc)[:80])
                return
            s = tar.new_page(viewport={"width": 1400, "height": 900})
            s.add_init_script(ILK)
            s.route("**/*", isle)
            s.goto(t.BASE + "/giris/", wait_until="load")
            s.wait_for_function("typeof selectMusteri === 'function'", timeout=20000)
            s.evaluate("selectMusteri(867)")
            s.wait_for_function("document.querySelectorAll('#sozlesmeler-aylik-grid .sozlesmeler-ay-kart').length > 20", timeout=20000)
            snap0 = s.evaluate(SNAP)
            ornek("ilk", snap0)
            ilk = s.evaluate("window.__ilk") or []
            for ay in ("2026-2", "2026-3", "2026-4"):
                kayit = next((x for x in ilk if x.get("ay") == ay), None)
                _kirmizi_sifir(kayit, "ilk HTML " + ay)
            yesil = next((x for x in ilk if x.get("ay") == "2025-6"), None)
            _yesil_kaldi(yesil, "ilk HTML 2025-6")
            s.wait_for_timeout(200)
            ornek("200ms", s.evaluate(SNAP))
            s.wait_for_timeout(800)
            ornek("1s", s.evaluate(SNAP))
            s.wait_for_timeout(2000)
            ornek("3s", s.evaluate(SNAP))
            s.evaluate("() => { try { sozlesmelerAylikTahsilSetiniGorseleZorla(); } catch (e) {} try { sozlesmelerReelDbGridVePanelSonBoya(); } catch (e) {} try { sozlesmelerAylikGuncelle(); } catch (e) {} }")
            s.wait_for_timeout(400)
            ornek("gecikmeli", s.evaluate(SNAP))
            tar.close()
    finally:
        t.fixture_867 = eski_fx


def main():
    zaman_cizelgesi()
    if t.FAILS:
        print("FAIL", len(t.FAILS))
        for ad in t.FAILS:
            print(" -", ad)
        raise SystemExit(1)
    eski_fx = t.fixture_867

    def fixture(senaryo="eski"):
        fx = eski_fx(senaryo)
        for a in fx["grid"]["cache"]["aylar"]:
            if (a["yil"], a["ay"]) == AY:
                a["tutar_kdv_dahil"] = 0.0
                a["brut_tutar_kdv"] = 0.0
                a["kalan_tutar_kdv"] = 0.0
                a["tahsil_edildi"] = False
                a["kismi_tahsilat"] = False
        fx["grid"]["cache"]["dondurma_var"] = True
        fx["grid"]["cache"]["dondurulan_aylar"] = ["2026-11"]
        return fx

    t.fixture_867 = fixture
    try:
        sonuc = t.kosu("bundle", None, "dondurma-sayfa", kart_degistir=False, senaryo="eski", ek_kontrol=_ek)
    finally:
        t.fixture_867 = eski_fx
    if sonuc[0] is None and not t.FAILS:
        print("SKIP sayfa")
        return
    if t.FAILS:
        print("FAIL", len(t.FAILS))
        for ad in t.FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("plan_dondurma_sayfa ok")


if __name__ == "__main__":
    main()
