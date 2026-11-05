# -*- coding: utf-8 -*-
"""Grid popup Toplam Borç / Günü Gelen Borç. Prod yazmaz. PII yok."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)
for _k in list(os.environ):
    if _k.startswith("KAPANIS_") or _k.startswith("ODA_"):
        os.environ.pop(_k, None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def _html() -> str:
    return (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")


def _satir(mid, borc, sg, ref_ay, ga=None):
    return {
        "musteri_id": mid,
        "toplam_borc": borc,
        "borc_hesaplanamadi": False,
        "sozlesme_gun": sg,
        "borc_ref_ay": ref_ay,
        "geciken_ay": ga if ga is not None else (2 if borc and ref_ay else 1),
    }


def test_sunucu_gun_gelen():
    from routes import faturalar_routes as fr

    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 5)

    # (a) bugün 5, sg 18, ref>0
    rows = [_satir(1, 300.0, 18, 100.0, ga=3)]
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows, aktif=False)
    check("a ham", rows[0]["toplam_borc_ham"] == 300.0)
    check("a gun_gelen", rows[0]["toplam_borc_gun_gelen"] == 200.0, rows[0])
    check("a ay_haric true", rows[0]["gun_bazli_ay_haric"] is True)
    check("a borc degismedi bayrak kapali", rows[0]["toplam_borc"] == 300.0)
    check("a geciken ayni", rows[0]["geciken_ay"] == 3)

    # (g) bayrak açık: gun_gelen aynı, ham sabit, borc düşer
    rows_g = [_satir(10, 300.0, 18, 100.0, ga=3)]
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows_g, aktif=True)
    check("g ham sabit", rows_g[0]["toplam_borc_ham"] == 300.0)
    check("g gun_gelen ayni", rows_g[0]["toplam_borc_gun_gelen"] == 200.0)
    check("g ay_haric true", rows_g[0]["gun_bazli_ay_haric"] is True)
    check("g borc dustu", rows_g[0]["toplam_borc"] == 200.0)

    # (b) bugün >= sg
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 18)
    rows_b = [_satir(2, 300.0, 18, 100.0)]
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows_b, aktif=False)
    check(
        "b ayni",
        rows_b[0]["toplam_borc_ham"] == 300.0
        and rows_b[0]["toplam_borc_gun_gelen"] == 300.0,
        rows_b[0],
    )
    check("b ay_haric false", rows_b[0]["gun_bazli_ay_haric"] is False)

    # (c) ref_ay=0
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 5)
    rows_c = [_satir(3, 300.0, 18, 0.0, ga=2)]
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows_c, aktif=False)
    check("c ayni", rows_c[0]["toplam_borc_gun_gelen"] == 300.0)
    check("c ay_haric false", rows_c[0]["gun_bazli_ay_haric"] is False)
    check("c geciken", rows_c[0]["geciken_ay"] == 2)

    # (d) ref None
    rows_d = [_satir(4, 300.0, 18, 50.0)]
    del rows_d[0]["borc_ref_ay"]
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows_d, aktif=False)
    check("d ayni", rows_d[0]["toplam_borc_gun_gelen"] == 300.0)

    # (e) ref > toplam
    rows_e = [_satir(5, 40.0, 18, 100.0, ga=1)]
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows_e, aktif=False)
    check("e sifir", rows_e[0]["toplam_borc_gun_gelen"] == 0.0)
    check("e negatif yok", rows_e[0]["toplam_borc_gun_gelen"] >= 0)

    # (f) sg 31 / kasım 30 / şubat
    check("f etkin kasim", fr._firma_ozet_gun_bazli_etkin_gun(31, 2025, 11) == 30)
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 29)
    rows_f = [_satir(6, 250.0, 31, 50.0)]
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows_f, aktif=False)
    check("f 29 dusuldu", rows_f[0]["toplam_borc_gun_gelen"] == 200.0)
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 30)
    rows_f2 = [_satir(7, 250.0, 31, 50.0)]
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows_f2, aktif=False)
    check("f 30 ayni", rows_f2[0]["toplam_borc_gun_gelen"] == 250.0)
    check("f subat28", fr._firma_ozet_gun_bazli_etkin_gun(31, 2025, 2) == 28)
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 2, 27)
    rows_sf = [_satir(8, 100.0, 31, 20.0)]
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows_sf, aktif=False)
    check("f subat27 dus", rows_sf[0]["toplam_borc_gun_gelen"] == 80.0)

    # helper reuse (no copy of drop math inline beyond call)
    import inspect

    hsrc = inspect.getsource(fr._firma_ozet_toplam_borc_gun_gelen_hesapla)
    check("reuse uygula_satir", "_firma_ozet_gun_bazli_borc_uygula_satir" in hsrc)

    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = None


def test_sablon_ve_excel():
    html = _html()
    check("ozet fn", "function faturaRaporGridModalOzetHtml" in html)
    check("label toplam", "Toplam Borç" in html)
    check("label gunu gelen", "Günü Gelen Borç" in html)
    check("not bulunamadi", "Sözleşme günü bulunamadı" in html)
    check("not dahil degil", "bu ay henüz dahil değil" in html)
    check("attr gun gelen", "data-fr-gun-gelen-n" in html)
    check("attr soz gun", "data-fr-soz-gun" in html)
    check("attr yaz grid-borc", "data-fr-grid-borc-n=" in html and "esc(gridHamN)" in html)
    check("attr yaz gun-gelen", "data-fr-gun-gelen-n=" in html and "esc(gridGelenN)" in html)
    check("attr yaz soz-gun", "data-fr-soz-gun=" in html and "esc(sozGunN)" in html)
    check("attr yaz soz-tarih", "data-fr-soz-tarih=" in html and "esc(sozTarN)" in html)
    check("attr yaz gun-ay-haric", "data-fr-gun-ay-haric=" in html and "esc(ayHaricN)" in html)
    check("attr toFixed", "gridHamN = gridHamNum.toFixed(2)" in html)
    check("attr btn textContent parse yok", "parseFloat(btn && btn.textContent)" not in html)
    check("aylar tr const", "FATURA_RAPOR_AYLAR_TR" in html and "Kasım" in html)
    check("etiket fn", "function faturaRaporGridModalOzetEtiket" in html)
    check("ay adi iso slice", "iso.slice(5, 7)" in html and "new Date(" not in html[html.find("function faturaRaporGridModalAyAdi"):html.find("function faturaRaporGridModalSozTarihTr")])
    check("popup ay filtre fn", "function faturaRaporGridModalBugunAyIdx" in html and "function faturaRaporGridModalAyIdx" in html and "Henüz borçlanan ay yok." in html)
    check("popup filtre sozlesmeler yok", "sozlesmelerAylikCacheRender" not in html[html.find("function faturaRaporGridModalBugunAyIdx"):html.find("function faturaRaporGridModalAc")])
    check("popup filtre metin <= yok", "k <= bugunAy" not in html[html.find("function faturaRaporGridModalAyIdx"):html.find("function faturaRaporGridModalAc")])
    check("istanbul plant", "data.istanbul_bugun" in html)
    check("src _t yok", "_t.js" not in html[:8000])
    # PII: labels/notes must not contain sample customer names patterns in ozet fn
    ozet = html[
        html.find("var FATURA_RAPOR_AYLAR_TR") : html.find(
            "function faturaRaporGridModalCiz"
        )
    ]
    check("j pii firma yok", "firmaAdi" not in ozet)
    check("j telefon yok", "telefon" not in ozet.lower())
    src = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    check("py sozlesme_tarihi", '"sozlesme_tarihi": giris_iso' in src)
    check("py gun_bazli_ay_haric", 'it["gun_bazli_ay_haric"]' in src)
    wb = src[src.find("def _firma_ozet_workbook_bytes") : src.find("def _firma_ozet_workbook_bytes") + 2500]
    check("excel Grid yok", '"Grid"' not in wb and "gun_gelen" not in wb)
    check("excel 14 Toplam borç", '"Toplam borç"' in wb)


def test_node_popup_ozet():
    html = _html()
    start = html.find("function faturaRaporGridModalKapat")
    end = html.find("function faturaRaporFirmaKartaGit(ev, mid) {", start)
    fns = html[start:end]
    check("extract", "faturaRaporGridModalOzetHtml" in fns)

    node = (
        r"""
global.window = global;
global.document = {
  getElementById: function (id) {
    if (id === 'fatura_rapor_grid_modal_body') {
      if (!global.__b) {
        global.__b = { _i: '' };
        Object.defineProperty(global.__b, 'innerHTML', {
          get: function () { return this._i; },
          set: function (v) { this._i = String(v == null ? '' : v); }
        });
      }
      return global.__b;
    }
    if (id === 'fatura_rapor_grid_modal_title') {
      if (!global.__t) global.__t = { textContent: '' };
      return global.__t;
    }
    if (id === 'fatura_rapor_grid_modal') {
      if (!global.__m) {
        global.__m = { _h: true, onclick: null };
        Object.defineProperty(global.__m, 'hidden', {
          get: function () { return this._h; },
          set: function (v) { this._h = !!v; }
        });
      }
      return global.__m;
    }
    return null;
  },
  addEventListener: function () {},
  removeEventListener: function () {}
};
global.girisFetchJsonCached = function () { return new Promise(function () {}); };
"""
        + fns
        + r"""
function say(ad, ok, ek) {
  console.log((ok ? 'ok ' : 'FAIL ') + ad + (ok ? '' : (' :: ' + (ek || ''))));
  if (!ok) process.exitCode = 1;
}
function temiz(s) {
  s = String(s == null ? '' : s);
  return s.indexOf('NaN') < 0 && s.indexOf('undefined') < 0 && s.indexOf('null') < 0;
}
function degerler(html) {
  var out = [];
  var re = /class="deger">([^<]*)<\/div>/g;
  var m;
  while ((m = re.exec(String(html || '')))) out.push(String(m[1] || '').replace(/\s*₺\s*$/, '').trim());
  return out;
}
var body = document.getElementById('fatura_rapor_grid_modal_body');

// (h) Toplam Borç == ham (Grid sütunu) — tam dolu meta (f referans)
faturaRaporGridModalCiz({ aylar: [] }, 'X', { toplamBorc: 1234.5, gunuGelen: 1100, sozlesmeGun: 18, bugunIso: '2025-11-05' });
var h = body.innerHTML || '';
say('h toplam label', h.indexOf('Toplam Borç') >= 0);
say('h ham tutar', h.indexOf('1.234,50') >= 0 || h.indexOf('1234,50') >= 0);
say('h gunu gelen label', h.indexOf('Günü Gelen Borç') >= 0);
say('h not dahil degil', h.indexOf('henüz dahil değil') >= 0 || h.indexOf('dahil değil') >= 0);
var hRef = h;

// ayni gun
faturaRaporGridModalCiz({ aylar: [{ yil: 2025, ay: 1, tutar_kdv_dahil: 10, tahsil_edildi: true, kalan_tutar_kdv: 0 }] }, 'Y', {
  toplamBorc: 200, gunuGelen: 200, sozlesmeGun: 5, bugunIso: '2025-11-05'
});
h = body.innerHTML || '';
say('b not ayni', h.indexOf('iki tutar aynı') >= 0);
say('e kart+ozet', h.indexOf('sozlesmeler-ay-kart') >= 0 && h.indexOf('Toplam Borç') >= 0);

// (i) iptal 0,00
faturaRaporGridModalCiz({ iptal: true, aylar: [], iptal_mesaj: 'Sözleşme iptal edildi: aylık borç oluşmaz.' }, 'Z', {
  toplamBorc: 999, gunuGelen: 888, sozlesmeGun: 18, bugunIso: '2025-11-05'
});
h = body.innerHTML || '';
say('i iptal msg', h.indexOf('iptal') >= 0 || h.indexOf('İptal') >= 0 || h.indexOf('Sözleşme iptal') >= 0);
say('i sifir', h.indexOf('0,00') >= 0);

// (j) PII yok in notes
var ozet = faturaRaporGridModalOzetHtml({ toplamBorc: 1, gunuGelen: 1, sozlesmeGun: '', bugunIso: '2025-11-05' });
say('j bulunamadi', ozet.indexOf('Sözleşme günü bulunamadı') >= 0);
say('j telefon yok', ozet.toLowerCase().indexOf('telefon') < 0);
say('j tc yok', ozet.toLowerCase().indexOf('tc') < 0 || ozet.indexOf('Sözleşme') >= 0);

// ---- Eski/eksik yanıt (a–e) + tam dolu değişmedi (f) ----
try {
  // (a) gun_gelen + istanbul_bugun tamamen YOK (eski önbellek)
  var aOzet = faturaRaporGridModalOzetHtml({ toplamBorc: 500, sozlesmeGun: 18 });
  var aDv = degerler(aOzet);
  say('a no throw', true);
  say('a temiz', temiz(aOzet));
  say('a gunu=toplam', aDv.length >= 2 && aDv[0] === aDv[1] && (aDv[0].indexOf('500') >= 0));
  say('a not yedek', aOzet.indexOf('bulunamadı') >= 0 || aOzet.indexOf('bilinmiyor') >= 0 || aOzet.indexOf('aynı') >= 0);
  faturaRaporGridModalCiz({ aylar: [] }, 'A', { toplamBorc: 500, sozlesmeGun: 18 });
  var aH = body.innerHTML || '';
  say('a ciz temiz', temiz(aH));
  say('a ciz gunu=toplam', (function () {
    var d = degerler(aH); return d.length >= 2 && d[0] === d[1];
  })());
} catch (eA) {
  say('a no throw', false, String(eA && eA.message || eA));
}

try {
  // (b) yalnız istanbul_bugun YOK, gun_gelen var
  var bOzet = faturaRaporGridModalOzetHtml({
    toplamBorc: 300, gunuGelen: 200, sozlesmeGun: 18
  });
  var bDv = degerler(bOzet);
  say('b no throw', true);
  say('b temiz', temiz(bOzet));
  say('b ham', bDv[0] && bDv[0].indexOf('300') >= 0);
  say('b gelen', bDv[1] && bDv[1].indexOf('200') >= 0);
  say('b not bilinmiyor', bOzet.indexOf('bilinmiyor') >= 0 || bOzet.indexOf('bulunamadı') >= 0);
} catch (eB) {
  say('b no throw', false, String(eB && eB.message || eB));
}

try {
  // (c) yalnız gun_gelen YOK, istanbul_bugun var
  var cOzet = faturaRaporGridModalOzetHtml({
    toplamBorc: 450, sozlesmeGun: 18, bugunIso: '2025-11-05'
  });
  var cDv = degerler(cOzet);
  say('c no throw', true);
  say('c temiz', temiz(cOzet));
  say('c gunu=toplam', cDv.length >= 2 && cDv[0] === cDv[1] && (cDv[0].indexOf('450') >= 0));
  say('c not dahil', cOzet.indexOf('dahil değil') >= 0 || cOzet.indexOf('aynı') >= 0);
} catch (eC) {
  say('c no throw', false, String(eC && eC.message || eC));
}

try {
  // (d) ham YOK / null / '' / NaN → Fmt 0,00
  var dCases = [undefined, null, '', NaN];
  var dOk = true;
  var dMsg = '';
  for (var di = 0; di < dCases.length; di++) {
    var dMeta = { sozlesmeGun: 10, bugunIso: '2025-11-05' };
    if (dCases[di] !== undefined) dMeta.toplamBorc = dCases[di];
    // gunuGelen de yok → ham yedeği
    var dO = faturaRaporGridModalOzetHtml(dMeta);
    var dDv = degerler(dO);
    if (!temiz(dO) || !dDv[0] || dDv[0].indexOf('0,00') < 0 || dDv[0] !== dDv[1]) {
      dOk = false;
      dMsg = 'case=' + String(dCases[di]) + ' html=' + dO.slice(0, 160);
      break;
    }
  }
  say('d no throw', true);
  say('d sifir yedek', dOk, dMsg);
  say('d temiz', dOk && temiz(faturaRaporGridModalOzetHtml({})));
} catch (eD) {
  say('d no throw', false, String(eD && eD.message || eD));
}

try {
  // (e) sayısal olmayan / Infinity
  var e1 = faturaRaporGridModalOzetHtml({
    toplamBorc: 'abc', gunuGelen: Infinity, sozlesmeGun: 5, bugunIso: '2025-11-05'
  });
  var e2 = faturaRaporGridModalOzetHtml({
    toplamBorc: Infinity, gunuGelen: 'xyz', sozlesmeGun: 5, bugunIso: '2025-11-05'
  });
  var eDv = degerler(e1);
  say('e no throw', true);
  say('e1 temiz', temiz(e1));
  say('e2 temiz', temiz(e2));
  say('e1 sifir', eDv[0] && eDv[0].indexOf('0,00') >= 0 && eDv[0] === eDv[1]);
  say('e2 sifir', (function () {
    var d = degerler(e2); return d[0] && d[0].indexOf('0,00') >= 0 && d[0] === d[1];
  })());
} catch (eE) {
  say('e no throw', false, String(eE && eE.message || eE));
}

try {
  // (f) tam dolu meta sonucu değişmedi
  faturaRaporGridModalCiz({ aylar: [] }, 'X', {
    toplamBorc: 1234.5, gunuGelen: 1100, sozlesmeGun: 18, bugunIso: '2025-11-05'
  });
  var fH = body.innerHTML || '';
  say('f ayni html', fH === hRef);
  say('f ham tutar', fH.indexOf('1.234,50') >= 0 || fH.indexOf('1234,50') >= 0);
  say('f gelen tutar', fH.indexOf('1.100,00') >= 0 || fH.indexOf('1100,00') >= 0);
  say('f not dahil', fH.indexOf('henüz dahil değil') >= 0 || fH.indexOf('dahil değil') >= 0);
  say('f temiz', temiz(fH));
} catch (eF) {
  say('f ayni html', false, String(eF && eF.message || eF));
}
"""
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(node)
        path = f.name
    try:
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=20)
        except FileNotFoundError:
            check("node", False, "node yok")
            return
        out = (r.stdout or "") + (r.stderr or "")
        print(out.strip())
        check("node exit", r.returncode == 0, out[-300:])
        for needle in [
            "ok h toplam label",
            "ok h ham tutar",
            "ok h gunu gelen label",
            "ok i iptal msg",
            "ok i sifir",
            "ok j bulunamadi",
            "ok a no throw",
            "ok a temiz",
            "ok a gunu=toplam",
            "ok a not yedek",
            "ok a ciz temiz",
            "ok a ciz gunu=toplam",
            "ok b no throw",
            "ok b temiz",
            "ok b ham",
            "ok b gelen",
            "ok c no throw",
            "ok c temiz",
            "ok c gunu=toplam",
            "ok d no throw",
            "ok d sifir yedek",
            "ok d temiz",
            "ok e no throw",
            "ok e1 temiz",
            "ok e2 temiz",
            "ok e1 sifir",
            "ok e2 sifir",
            "ok f ayni html",
            "ok f ham tutar",
            "ok f gelen tutar",
            "ok f not dahil",
            "ok f temiz",
        ]:
            check(needle, needle in out)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_tutar_kurus_ve_buyuk():
    """Kuruşlu / büyük tutar: payload → attr → popup Fmt (tr-TR metin parse yok)."""
    from routes import faturalar_routes as fr

    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 18)  # gun geldi → gun_gelen=ham
    cases = [83.0, 83412.57, 1234567.89, 0.05, 999999999.99]
    for i, amt in enumerate(cases):
        rows = [_satir(100 + i, amt, 5, 0.0, ga=1)]
        fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows, aktif=False)
        ham = rows[0]["toplam_borc_ham"]
        gg = rows[0]["toplam_borc_gun_gelen"]
        check(f"k{i} ham tip float", isinstance(ham, float))
        check(f"k{i} ham aynen", ham == round(float(amt), 2), ham)
        check(f"k{i} gun_gelen aynen", gg == round(float(amt), 2), gg)
        check(f"k{i} round2", ham == round(ham, 2) and gg == round(gg, 2))

    # düşümlü: ham kuruşlu kalır, gun_gelen = ham - ref (round 2)
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 5)
    rows_d = [_satir(200, 83412.57, 18, 100.07, ga=2)]
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows_d, aktif=False)
    check("k dus ham", rows_d[0]["toplam_borc_ham"] == 83412.57)
    check("k dus gelen", rows_d[0]["toplam_borc_gun_gelen"] == 83312.50, rows_d[0])
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = None

    html = _html()
    start = html.find("function faturaRaporGridModalKapat")
    end = html.find("function faturaRaporFirmaKartaGit(ev, mid) {", start)
    fns = html[start:end]
    node = (
        r"""
global.window = global;
global.document = {
  getElementById: function (id) {
    if (id === 'fatura_rapor_grid_modal_body') {
      if (!global.__b) {
        global.__b = { _i: '' };
        Object.defineProperty(global.__b, 'innerHTML', {
          get: function () { return this._i; },
          set: function (v) { this._i = String(v == null ? '' : v); }
        });
      }
      return global.__b;
    }
    if (id === 'fatura_rapor_grid_modal_title') {
      if (!global.__t) global.__t = { textContent: '' };
      return global.__t;
    }
    if (id === 'fatura_rapor_grid_modal') {
      if (!global.__m) {
        global.__m = { _h: true, onclick: null };
        Object.defineProperty(global.__m, 'hidden', {
          get: function () { return this._h; },
          set: function (v) { this._h = !!v; }
        });
      }
      return global.__m;
    }
    return null;
  },
  addEventListener: function () {},
  removeEventListener: function () {}
};
global.girisFetchJsonCached = function () { return new Promise(function () {}); };
"""
        + fns
        + r"""
function say(ad, ok, ek) {
  console.log((ok ? 'ok ' : 'FAIL ') + ad + (ok ? '' : (' :: ' + (ek || ''))));
  if (!ok) process.exitCode = 1;
}
function temiz(s) {
  s = String(s == null ? '' : s);
  return s.indexOf('NaN') < 0 && s.indexOf('undefined') < 0 && s.indexOf('null') < 0;
}
function degerler(html) {
  var out = [];
  var re = /class="deger">([^<]*)<\/div>/g;
  var m;
  while ((m = re.exec(String(html || '')))) out.push(String(m[1] || '').replace(/\s*₺\s*$/, '').trim());
  return out;
}
function attrN(raw) {
  var n = parseFloat(raw);
  if (!isFinite(n)) n = 0;
  n = Math.round(n * 100) / 100;
  return n.toFixed(2);
}
function trFmt(n) {
  return Number(n).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function metaFromAttr(hamAttr, gelAttr) {
  var hamN = parseFloat(hamAttr);
  var gelN = parseFloat(gelAttr);
  if (!isFinite(hamN)) hamN = 0;
  if (!isFinite(gelN)) gelN = hamN;
  return { toplamBorc: hamN, gunuGelen: gelN, sozlesmeGun: 18, bugunIso: '2025-11-05' };
}

var cases = [83, 83412.57, 1234567.89, 0.05, 999999999.99];
var expectedTr = ['83,00', '83.412,57', '1.234.567,89', '0,05', '999.999.999,99'];
var body = document.getElementById('fatura_rapor_grid_modal_body');
var attrs = [];
for (var i = 0; i < cases.length; i++) {
  var a = attrN(cases[i]);
  attrs.push(a);
  say('attr nokta ' + i, /^\d+(\.\d{2})?$/.test(a) && a.indexOf(',') < 0, a);
  say('attr deger ' + i, Math.abs(parseFloat(a) - cases[i]) < 0.001, a);
  // tr-TR metin parseFloat KIRPAR — bu yol kullanılmamalı
  var trBad = parseFloat(trFmt(cases[i]));
  say('tr parse kirpar ' + i, Math.abs(trBad - cases[i]) > 0.5 || cases[i] < 100, String(trBad));
  var ozet = faturaRaporGridModalOzetHtml(metaFromAttr(a, a));
  var dv = degerler(ozet);
  say('popup tr ' + i, dv[0] === expectedTr[i] && dv[1] === expectedTr[i], dv.join('|'));
  say('popup temiz ' + i, temiz(ozet));
  // Grid hücre Fmt == popup Toplam
  say('hucre=popup ' + i, trFmt(parseFloat(a)) === dv[0]);
}

// sıralama data-fr-grid-borc-n sayısal
var sorted = attrs.slice().sort(function (x, y) { return parseFloat(x) - parseFloat(y); });
var expectOrder = ['0.05', '83.00', '83412.57', '1234567.89', '999999999.99'];
say('sort sayisal', sorted.join(',') === expectOrder.join(','), sorted.join(','));

// eksik alan yedek bozulmadı
var miss = faturaRaporGridModalOzetHtml({ toplamBorc: 500, sozlesmeGun: 18 });
var md = degerler(miss);
say('miss temiz', temiz(miss));
say('miss gunu=toplam', md[0] === md[1] && md[0].indexOf('500') >= 0);

// Ac yolu: attr üzerinden (tr text yok)
faturaRaporGridModalCiz({ aylar: [] }, 'T', metaFromAttr('2191036.33', '1278032.73'));
var h = body.innerHTML || '';
say('buyuk toplam', h.indexOf('2.191.036,33') >= 0, h.slice(0, 220));
say('buyuk gelen', h.indexOf('1.278.032,73') >= 0);
say('buyuk 83 degil', h.indexOf('>83,00') < 0 && h.indexOf('>2,19') < 0);
say('buyuk temiz', temiz(h));
"""
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(node)
        path = f.name
    try:
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=20)
        except FileNotFoundError:
            check("node kurus", False, "node yok")
            return
        out = (r.stdout or "") + (r.stderr or "")
        print(out.strip())
        check("node kurus exit", r.returncode == 0, out[-400:])
        for needle in [
            "ok attr nokta 1",
            "ok attr deger 1",
            "ok popup tr 1",
            "ok hucre=popup 1",
            "ok popup tr 2",
            "ok sort sayisal",
            "ok miss temiz",
            "ok miss gunu=toplam",
            "ok buyuk toplam",
            "ok buyuk gelen",
            "ok buyuk 83 degil",
        ]:
            check(needle, needle in out)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _js_brace_extract(src: str, start_marker: str, *, end_before: str | None = None) -> str:
    """Kaynaktan JS fonksiyon/ifade gövdesini al (brace-match; regex literal güvenli).

    end_before verilirse (ör. sonraki ifade), o markere kadar kes — iç içe /regex/
    yüzünden bozulan uzun gövdeler için.
    """
    i = src.find(start_marker)
    if i < 0:
        raise ValueError("marker yok: " + start_marker[:60])
    if end_before:
        e = src.find(end_before, i + len(start_marker))
        if e < 0:
            raise ValueError("end_before yok: " + end_before[:60])
        return src[i:e].rstrip()
    brace = src.find("{", i)
    if brace < 0:
        raise ValueError("brace yok: " + start_marker[:60])
    depth = 0
    in_s = None  # ', ", `
    esc = False
    j = brace
    while j < len(src):
        ch = src[j]
        if in_s:
            if esc:
                esc = False
                j += 1
                continue
            if ch == "\\":
                esc = True
                j += 1
                continue
            if ch == in_s:
                in_s = None
            j += 1
            continue
        # /regex/ — tırnak içeren karakter sınıflarını bozmasın
        if ch == "/" and j > 0:
            prev = src[j - 1]
            if prev in (
                "(",
                "=",
                ",",
                ":",
                "!",
                "&",
                "|",
                "?",
                "+",
                "-",
                "*",
                "~",
                "{",
                "[",
                ";",
                " ",
                "\t",
                "\n",
                "\r",
            ) or prev.isalpha() is False and prev not in ("/", "\\"):
                # division vs regex: önceki token operatör/ayırıcıysa regex
                if prev not in (")", "]", "}", "\"", "'", "`") and not prev.isalnum() and prev != "_":
                    j += 1
                    while j < len(src):
                        c2 = src[j]
                        if c2 == "\\":
                            j += 2
                            continue
                        if c2 == "[":
                            j += 1
                            while j < len(src) and src[j] != "]":
                                if src[j] == "\\":
                                    j += 2
                                    continue
                                j += 1
                            j += 1
                            continue
                        if c2 == "/":
                            j += 1
                            while j < len(src) and src[j].isalpha():
                                j += 1
                            break
                        if c2 == "\n":
                            break
                        j += 1
                    continue
        if ch in ("'", '"', "`"):
            in_s = ch
            j += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = j + 1
                if end < len(src) and src[end] == ";":
                    end += 1
                return src[i:end]
        j += 1
    raise ValueError("brace kapanmadi: " + start_marker[:60])


def test_gercek_satir_uretimi_popup_tutar():
    """index.html buildFirmaRowsHtml gerçek çıktısından attr oku → popup tam tutar."""
    html = _html()
    try:
        # Regex-içeren uzun gövde: bir sonraki kullanıma kadar güvenli kes
        build_src = _js_brace_extract(
            html,
            "var buildFirmaRowsHtml = function",
            end_before="tbody.innerHTML = buildFirmaRowsHtml",
        )
    except ValueError as e:
        check("extract buildFirmaRowsHtml", False, str(e))
        return
    check(
        "extract buildFirmaRowsHtml",
        "data-fr-grid-borc-n" in build_src
        and "toFixed(2)" in build_src
        and "Kaydet" not in build_src,
    )

    # fmtF hemen üstte — satır üretimine gerekli, tutarı yalnızca gösterimde biçimler
    fmt_i = html.rfind("var fmtF = function", 0, html.find("var buildFirmaRowsHtml = function"))
    check("extract fmtF", fmt_i >= 0)
    try:
        fmt_src = _js_brace_extract(html[fmt_i:], "var fmtF = function")
    except ValueError as e:
        check("extract fmtF body", False, str(e))
        return

    # popup fonksiyonları (Fmt + ay etiketleri + OzetHtml + Ciz)
    try:
        fmt_popup = _js_brace_extract(html, "function faturaRaporGridModalFmt")
        ozet_ciz = _js_brace_extract(html, "function faturaRaporGridModalCiz")
        ay_i = html.find("var FATURA_RAPOR_AYLAR_TR")
        if ay_i < 0:
            raise ValueError("FATURA_RAPOR_AYLAR_TR yok")
        ozet_blk = html[ay_i : html.find("function faturaRaporGridModalCiz", ay_i)]
    except ValueError as e:
        check("extract popup fn", False, str(e))
        return
    check("extract ozet", "function faturaRaporGridModalOzetHtml" in ozet_blk)

    node = (
        r"""
global.window = global;
global.document = {
  getElementById: function (id) {
    if (id === 'fatura_rapor_grid_modal_body') {
      if (!global.__b) {
        global.__b = { _i: '' };
        Object.defineProperty(global.__b, 'innerHTML', {
          get: function () { return this._i; },
          set: function (v) { this._i = String(v == null ? '' : v); }
        });
      }
      return global.__b;
    }
    if (id === 'fatura_rapor_grid_modal_title') {
      if (!global.__t) global.__t = { textContent: '' };
      return global.__t;
    }
    return null;
  },
  addEventListener: function () {},
  removeEventListener: function () {}
};
function say(ad, ok, ek) {
  console.log((ok ? 'ok ' : 'FAIL ') + ad + (ok ? '' : (' :: ' + (ek || ''))));
  if (!ok) process.exitCode = 1;
}
function temiz(s) {
  s = String(s == null ? '' : s);
  return s.indexOf('NaN') < 0 && s.indexOf('undefined') < 0 && s.indexOf('null') < 0;
}
function degerler(html) {
  var out = [];
  var re = /class="deger">([^<]*)<\/div>/g;
  var m;
  while ((m = re.exec(String(html || '')))) out.push(String(m[1] || '').replace(/\s*₺\s*$/, '').trim());
  return out;
}
function attrOf(trHtml, name) {
  var re = new RegExp(name + '="([^"]*)"');
  var m = String(trHtml || '').match(re);
  return m ? m[1] : null;
}
function metaFromTr(trHtml) {
  var hamN = parseFloat(attrOf(trHtml, 'data-fr-grid-borc-n'));
  var gelN = parseFloat(attrOf(trHtml, 'data-fr-gun-gelen-n'));
  if (!isFinite(hamN)) hamN = 0;
  if (!isFinite(gelN)) gelN = hamN;
  var har = attrOf(trHtml, 'data-fr-gun-ay-haric');
  return {
    toplamBorc: hamN,
    gunuGelen: gelN,
    sozlesmeGun: attrOf(trHtml, 'data-fr-soz-gun') || '',
    sozlesmeTarihi: attrOf(trHtml, 'data-fr-soz-tarih') || '',
    gunBazliAyHaric: (har === '1' || har === 'true'),
    bugunIso: '2025-11-05'
  };
}
"""
        + fmt_popup
        + "\n"
        + ozet_blk
        + "\n"
        + ozet_ciz
        + "\n"
        # satır üretimi bağımlılıkları (tutarı değiştirmez)
        + "var ordRows = [0, 1, 2, 3, 4, 5, 6, 7, 8, 16, 9, 10, 11, 12, 13, 14, 15];\n"
        + fmt_src
        + ";\n"
        + build_src
        + ";\n"
        + r"""
var cases = [
  { ham: 83.00, gelen: 83.00, trHam: '83,00', trGelen: '83,00', attrHam: '83.00', attrGelen: '83.00' },
  { ham: 83412.57, gelen: 82000.01, trHam: '83.412,57', trGelen: '82.000,01', attrHam: '83412.57', attrGelen: '82000.01' },
  { ham: 1234567.89, gelen: 1230000.00, trHam: '1.234.567,89', trGelen: '1.230.000,00', attrHam: '1234567.89', attrGelen: '1230000.00' },
  { ham: 0.05, gelen: 0.00, trHam: '0,05', trGelen: '0,00', attrHam: '0.05', attrGelen: '0.00' },
  { ham: 999999999.99, gelen: 999999999.99, trHam: '999.999.999,99', trGelen: '999.999.999,99', attrHam: '999999999.99', attrGelen: '999999999.99' }
];
var rows = cases.map(function (c, i) {
  return {
    musteri_id: 1000 + i,
    firma_adi: 'F' + i,
    giris_tarihi: '2025-01-01',
    aylik_tutar: 100,
    toplam_borc: c.ham,
    toplam_borc_ham: c.ham,
    toplam_borc_gun_gelen: c.gelen,
    borc_hesaplanamadi: false,
    sozlesme_gun: 18,
    sozlesme_ay: 1,
    sozlesme_ay_adi: 'Ocak',
    geciken_ay: 1,
    ilk_kira_bedeli: null,
    guncel_kira_bedeli: null,
    hizmet_turu: '—',
    grup2: '—',
    grup2_secimleri: [],
    durum_etiket: 'Aktif',
    kapanis_tarihi: '',
    liste_ara_blob: 'F' + i
  };
});
say('build fn', typeof buildFirmaRowsHtml === 'function');
var htmlAll = buildFirmaRowsHtml(rows);
say('build html tr', htmlAll.indexOf('<tr') >= 0 && htmlAll.indexOf('data-fr-grid-borc-n=') >= 0);
var trParts = htmlAll.split('</tr>').filter(function (p) { return p.indexOf('<tr') >= 0; }).map(function (p) { return p + '</tr>'; });
say('build tr count', trParts.length === cases.length, String(trParts.length));

var attrsHam = [];
for (var i = 0; i < cases.length; i++) {
  var tr = trParts[i] || '';
  var aHam = attrOf(tr, 'data-fr-grid-borc-n');
  var aGel = attrOf(tr, 'data-fr-gun-gelen-n');
  var aSoz = attrOf(tr, 'data-fr-soz-gun');
  attrsHam.push(aHam);
  // (a) noktalı ham, virgül/binlik yok
  say('a attr ham ' + i, aHam === cases[i].attrHam, aHam);
  say('a attr gelen ' + i, aGel === cases[i].attrGelen, aGel);
  say('a attr soz ' + i, aSoz === '18', aSoz);
  say('a virgul yok ' + i, aHam && aHam.indexOf(',') < 0 && aHam.indexOf('.') >= 0 || aHam === '83.00' || aHam === '0.05' || aHam === '0.00');
  // (b) popup tr-TR tam tutar
  var ozet = faturaRaporGridModalOzetHtml(metaFromTr(tr));
  var dv = degerler(ozet);
  say('b popup ham ' + i, dv[0] === cases[i].trHam, (dv[0] || '') + '|' + cases[i].trHam);
  say('b popup gelen ' + i, dv[1] === cases[i].trGelen, (dv[1] || '') + '|' + cases[i].trGelen);
  say('b kirpma yok ' + i, ozet.indexOf('>2,19') < 0 && !(cases[i].ham > 100 && dv[0] === '83,00'));
  say('b temiz ' + i, temiz(ozet));

  // (c) düğme tr-TR metni bilerek bozulsa bile attr'den okur
  var trBroken = tr.replace(/>[^<]*83\.412,57[^<]*</, '>KIRPIK_TEXT_83.412,57<')
    .replace(/>[^<]*1\.234\.567,89[^<]*</, '>KIRPIK_TEXT_1.234.567,89<')
    .replace(/>[^<]*999\.999\.999,99[^<]*</, '>KIRPIK_TEXT_999.999.999,99<')
    .replace(/>[^<]*83,00[^<]*</, '>KIRPIK_TEXT_83,00<')
    .replace(/>[^<]*0,05[^<]*</, '>KIRPIK_TEXT_0,05<');
  // ayrıca klasik kırpma kanıtı: textContent parseFloat
  var btnMatch = tr.match(/fatura-rapor-firma-grid-btn[^>]*>([^<]+)</);
  var btnTxt = btnMatch ? btnMatch[1] : '';
  var brokenParse = parseFloat(btnTxt);
  if (cases[i].ham >= 1000) {
    say('c eski kirpma kaniti ' + i, isFinite(brokenParse) && Math.abs(brokenParse - cases[i].ham) > 1, String(brokenParse));
  } else {
    say('c eski kirpma kaniti ' + i, true);
  }
  var ozetC = faturaRaporGridModalOzetHtml(metaFromTr(trBroken));
  var dvC = degerler(ozetC);
  say('c bozulsa bile ham ' + i, dvC[0] === cases[i].trHam, dvC[0]);
  say('c bozulsa bile gelen ' + i, dvC[1] === cases[i].trGelen, dvC[1]);
}

// (d) sıralama data-fr-grid-borc-n sayısal
var order = attrsHam.slice().map(function (a, idx) { return { a: a, i: idx }; })
  .sort(function (x, y) { return parseFloat(x.a) - parseFloat(y.a); })
  .map(function (x) { return x.i; });
say('d sort', order.join(',') === '3,0,1,2,4', order.join(','));

// (e) NEGATİF: attr silinince güvenli yedek; kırpılmış tutar / NaN yok; eski textContent yolu kırpardı
var trBig = trParts[1]; // 83412.57
var trNoAttr = trBig.replace(/ data-fr-grid-borc-n="[^"]*"/, '').replace(/ data-fr-gun-gelen-n="[^"]*"/, '');
say('e attr silindi', trNoAttr.indexOf('data-fr-grid-borc-n') < 0);
var metaE = metaFromTr(trNoAttr); // Ac ile aynı: yoksa 0
var ozetE = faturaRaporGridModalOzetHtml(metaE);
var dvE = degerler(ozetE);
say('e yedek sifir', dvE[0] === '0,00' && dvE[1] === '0,00', dvE.join('|'));
say('e NaN yok', temiz(ozetE));
say('e kirpilmis 83 yok', dvE[0] !== '83,41' && dvE[0] !== '83,00' && ozetE.indexOf('83.412') < 0);
// eski davranış kanıtı: textContent parseFloat kırpardı → test bunu yakalar
var btnBig = (trBig.match(/fatura-rapor-firma-grid-btn[^>]*>([^<]+)</) || [])[1] || '';
var eskiKirpik = parseFloat(btnBig);
say('e eski yol kirpar', Math.abs(eskiKirpik - 83.412) < 0.001, String(eskiKirpik));
say('e yeni yol kirpmaz', Math.abs(metaE.toplamBorc - 0) < 0.001 && Math.abs(metaE.toplamBorc - eskiKirpik) > 1);
"""
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(node)
        path = f.name
    try:
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            check("node gercek satir", False, "node yok")
            return
        out = (r.stdout or "") + (r.stderr or "")
        print(out.strip())
        check("node gercek satir exit", r.returncode == 0, out[-500:])
        for needle in [
            "ok extract buildFirmaRowsHtml",  # python-side already printed; node:
            "ok build fn",
            "ok build html tr",
            "ok build tr count",
            "ok a attr ham 1",
            "ok a attr gelen 1",
            "ok a attr soz 1",
            "ok b popup ham 1",
            "ok b popup gelen 1",
            "ok b popup ham 2",
            "ok b popup ham 4",
            "ok c bozulsa bile ham 1",
            "ok c eski kirpma kaniti 1",
            "ok d sort",
            "ok e attr silindi",
            "ok e yedek sifir",
            "ok e NaN yok",
            "ok e kirpilmis 83 yok",
            "ok e eski yol kirpar",
            "ok e yeni yol kirpmaz",
        ]:
            if needle.startswith("ok extract "):
                continue
            check(needle, needle in out)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_ozet_ay_etiket_gercek_satir():
    """buildFirmaRowsHtml → attr → popup ay etiketi / sözleşme tarihi (istanbul_bugun)."""
    html = _html()
    try:
        build_src = _js_brace_extract(
            html,
            "var buildFirmaRowsHtml = function",
            end_before="tbody.innerHTML = buildFirmaRowsHtml",
        )
        fmt_i = html.rfind("var fmtF = function", 0, html.find("var buildFirmaRowsHtml = function"))
        fmt_src = _js_brace_extract(html[fmt_i:], "var fmtF = function")
        # AyAdi..OzetHtml bloğu (Ciz öncesi)
        blk_start = html.find("var FATURA_RAPOR_AYLAR_TR")
        blk_end = html.find("function faturaRaporGridModalCiz", blk_start)
        ozet_blk = html[blk_start:blk_end]
    except ValueError as e:
        check("etiket extract", False, str(e))
        return
    check("etiket extract build", "data-fr-soz-tarih" in build_src and "data-fr-gun-ay-haric" in build_src)
    check("etiket extract ozet", "faturaRaporGridModalOzetEtiket" in ozet_blk)

    node = (
        r"""
global.window = global;
function say(ad, ok, ek) {
  console.log((ok ? 'ok ' : 'FAIL ') + ad + (ok ? '' : (' :: ' + (ek || ''))));
  if (!ok) process.exitCode = 1;
}
function temiz(s) {
  s = String(s == null ? '' : s);
  return s.indexOf('NaN') < 0 && s.indexOf('undefined') < 0 && s.indexOf('null') < 0;
}
function attrOf(trHtml, name) {
  var re = new RegExp(name + '="([^"]*)"');
  var m = String(trHtml || '').match(re);
  return m ? m[1] : null;
}
function metaFromTr(trHtml, bugunIso) {
  var hamN = parseFloat(attrOf(trHtml, 'data-fr-grid-borc-n'));
  var gelN = parseFloat(attrOf(trHtml, 'data-fr-gun-gelen-n'));
  if (!isFinite(hamN)) hamN = 0;
  if (!isFinite(gelN)) gelN = hamN;
  var har = attrOf(trHtml, 'data-fr-gun-ay-haric');
  return {
    toplamBorc: hamN,
    gunuGelen: gelN,
    sozlesmeGun: attrOf(trHtml, 'data-fr-soz-gun') || '',
    sozlesmeTarihi: attrOf(trHtml, 'data-fr-soz-tarih') || '',
    gunBazliAyHaric: (har === '1' || har === 'true'),
    bugunIso: bugunIso || ''
  };
}
function etiketler(html) {
  var out = [];
  var re = /class="etiket">([^<]*)<\/span>/g;
  var m;
  while ((m = re.exec(String(html || '')))) out.push(m[1]);
  return out;
}
function tarihler(html) {
  var out = [];
  var re = /class="tarih">([^<]*)<\/span>/g;
  var m;
  while ((m = re.exec(String(html || '')))) out.push(m[1]);
  return out;
}
"""
        + "function faturaRaporGridModalFmt(n){var x=parseFloat(n);if(!isFinite(x))x=0;return x.toLocaleString('tr-TR',{minimumFractionDigits:2,maximumFractionDigits:2});}\n"
        + ozet_blk
        + "\nvar ordRows = [0, 1, 2, 3, 4, 5, 6, 7, 8, 16, 9, 10, 11, 12, 13, 14, 15];\n"
        + fmt_src
        + ";\n"
        + build_src
        + ";\n"
        + r"""
var AYLAR = ['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];
// (a) 12 ay
for (var m = 1; m <= 12; m++) {
  var iso = '2026-' + (m < 10 ? '0' : '') + m + '-15';
  say('a ay ' + m, faturaRaporGridModalAyAdi(iso) === AYLAR[m - 1], faturaRaporGridModalAyAdi(iso));
}
// (b) Aralık→Ocak
say('b aralik', faturaRaporGridModalAyAdi('2026-12-31') === 'Aralık');
say('b ocak', faturaRaporGridModalAyAdi('2027-01-01') === 'Ocak');
// (g) tarih gg.aa.yyyy, Date yok
say('g tarih', faturaRaporGridModalSozTarihTr('2025-10-18') === '18.10.2025');
say('g bos', faturaRaporGridModalSozTarihTr('') === '' && faturaRaporGridModalSozTarihTr(null) === '');

function rowOf(i, ham, gelen, haric, sozTar) {
  return {
    musteri_id: 2000 + i,
    firma_adi: 'X' + i,
    giris_tarihi: sozTar || '',
    sozlesme_tarihi: sozTar || '',
    aylik_tutar: 100,
    toplam_borc: ham,
    toplam_borc_ham: ham,
    toplam_borc_gun_gelen: gelen,
    gun_bazli_ay_haric: !!haric,
    borc_hesaplanamadi: false,
    sozlesme_gun: 18,
    sozlesme_ay: 1,
    sozlesme_ay_adi: 'Ocak',
    geciken_ay: 1,
    ilk_kira_bedeli: null,
    guncel_kira_bedeli: null,
    hizmet_turu: '—',
    grup2: '—',
    grup2_secimleri: [],
    durum_etiket: 'Aktif',
    kapanis_tarihi: '',
    liste_ara_blob: 'X' + i
  };
}
var rows = [
  rowOf(0, 300, 200, true, '2025-10-18'),
  rowOf(1, 300, 300, false, '2024-03-05'),
  rowOf(2, 83412.57, 82000.01, true, ''),
  rowOf(3, 1234567.89, 1234567.89, false, '2020-01-01')
];
var htmlAll = buildFirmaRowsHtml(rows);
var trParts = htmlAll.split('</tr>').filter(function (p) { return p.indexOf('<tr') >= 0; }).map(function (p) { return p + '</tr>'; });
say('build count', trParts.length === 4);

// (c) haric=true → Toplam dahil, Günü Gelen hariç + tarih
var oC = faturaRaporGridModalOzetHtml(metaFromTr(trParts[0], '2026-11-05'));
var eC = etiketler(oC);
var tC = tarihler(oC);
say('c toplam dahil', eC[0] === 'Kasım dahil', eC.join('|'));
say('c gelen haric', eC[1] === 'Kasım hariç', eC.join('|'));
say('c tarih sag', tC[0] === '18.10.2025', tC.join('|'));
say('c tutar', oC.indexOf('300,00') >= 0 && oC.indexOf('200,00') >= 0);
say('c temiz', temiz(oC));

// (d) false → ikisi dahil
var oD = faturaRaporGridModalOzetHtml(metaFromTr(trParts[1], '2026-04-20'));
var eD = etiketler(oD);
say('d ikisi dahil', eD[0] === 'Nisan dahil' && eD[1] === 'Nisan dahil', eD.join('|'));
say('d tarih', tarihler(oD)[0] === '05.03.2024');

// (e) sözleşme tarihi yok → tarih span yok, etiket çalışır
var oE = faturaRaporGridModalOzetHtml(metaFromTr(trParts[2], '2026-08-10'));
say('e etiket', etiketler(oE)[0] === 'Ağustos dahil' && etiketler(oE)[1] === 'Ağustos hariç');
say('e tarih yok', tarihler(oE).length === 0, String(tarihler(oE)));
say('e attr tarih bos', attrOf(trParts[2], 'data-fr-soz-tarih') === '');

// (f) eksik alan / eski önbellek
var oF = faturaRaporGridModalOzetHtml({ toplamBorc: 50, gunuGelen: 40 });
var eF = etiketler(oF);
say('f yedek', eF[0] === 'Bu ay dahil' && eF[1] === 'Bu ay dahil', eF.join('|'));
say('f temiz', temiz(oF));
say('f tarih yok', tarihler(oF).length === 0);
var oF2 = faturaRaporGridModalOzetHtml({ toplamBorc: 10, gunuGelen: 10, gunBazliAyHaric: true });
say('f haric yedek', etiketler(oF2)[1] === 'Bu ay hariç');

// (h) PII yok
say('h telefon yok', oC.toLowerCase().indexOf('telefon') < 0);
say('h musteri etikette yok', eC.join(' ').indexOf('X0') < 0 && oC.indexOf('firma_adi') < 0);

// (i) tutarlar değişmedi (büyük)
var oI = faturaRaporGridModalOzetHtml(metaFromTr(trParts[3], '2026-02-01'));
say('i tutar buyuk', oI.indexOf('1.234.567,89') >= 0);
say('i dahil subat', etiketler(oI)[0] === 'Şubat dahil' && etiketler(oI)[1] === 'Şubat dahil');

// attr haric bayrağı gerçek satırdan
say('attr haric 0', attrOf(trParts[0], 'data-fr-gun-ay-haric') === '1');
say('attr haric 1', attrOf(trParts[1], 'data-fr-gun-ay-haric') === '0');
say('attr soz tarih', attrOf(trParts[0], 'data-fr-soz-tarih') === '2025-10-18');
"""
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(node)
        path = f.name
    try:
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            check("node etiket", False, "node yok")
            return
        out = (r.stdout or "") + (r.stderr or "")
        print(out.strip())
        check("node etiket exit", r.returncode == 0, out[-400:])
        for needle in [
            "ok a ay 1",
            "ok a ay 11",
            "ok a ay 12",
            "ok b aralik",
            "ok b ocak",
            "ok g tarih",
            "ok c toplam dahil",
            "ok c gelen haric",
            "ok c tarih sag",
            "ok d ikisi dahil",
            "ok e etiket",
            "ok e tarih yok",
            "ok f yedek",
            "ok f temiz",
            "ok f haric yedek",
            "ok h telefon yok",
            "ok i tutar buyuk",
            "ok attr haric 0",
            "ok attr soz tarih",
        ]:
            check(needle, needle in out)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_popup_ay_filtre_gelecek_gizle():
    """Gerçek faturaRaporGridModalCiz: bu aydan sonraki aylar gizlenir; özet görünenden."""
    html = _html()
    # Sözleşmeler yolu dokunulmamış
    check(
        "i sozlesmeler fn ayni isim",
        "function sozlesmelerAylikCacheRender(cacheObj, preferLiveTahsil)" in html,
    )
    try:
        fmt = _js_brace_extract(html, "function faturaRaporGridModalFmt")
        kart = _js_brace_extract(html, "function faturaRaporGridModalKartHtml")
        ay_i = html.find("var FATURA_RAPOR_AYLAR_TR")
        ozet_blk = html[ay_i : html.find("function faturaRaporGridModalAyIdx", ay_i)]
        # AyIdx + BugunAyIdx + Ciz
        filtre_i = html.find("function faturaRaporGridModalAyIdx")
        ciz_end = html.find("function faturaRaporGridModalAc", filtre_i)
        filtre_blk = html[filtre_i:ciz_end]
    except ValueError as e:
        check("filtre extract", False, str(e))
        return
    check("filtre extract ciz", "aylarVis" in filtre_blk and "Henüz borçlanan ay yok." in filtre_blk)
    check("filtre new Date yok", "new Date(" not in filtre_blk)

    node = (
        r"""
global.window = global;
global.document = {
  getElementById: function (id) {
    if (id === 'fatura_rapor_grid_modal_body') {
      if (!global.__b) {
        global.__b = { _i: '' };
        Object.defineProperty(global.__b, 'innerHTML', {
          get: function () { return this._i; },
          set: function (v) { this._i = String(v == null ? '' : v); }
        });
      }
      return global.__b;
    }
    if (id === 'fatura_rapor_grid_modal_title') {
      if (!global.__t) global.__t = { textContent: '' };
      return global.__t;
    }
    return null;
  },
  addEventListener: function () {},
  removeEventListener: function () {}
};
function say(ad, ok, ek) {
  console.log((ok ? 'ok ' : 'FAIL ') + ad + (ok ? '' : (' :: ' + (ek || ''))));
  if (!ok) process.exitCode = 1;
}
function temiz(s) {
  s = String(s == null ? '' : s);
  return s.indexOf('NaN') < 0 && s.indexOf('undefined') < 0 && s.indexOf('null') < 0;
}
function ayObj(y, m, tut, kalan) {
  return {
    yil: y, ay: m, ay_key: y + '-' + m,
    tutar_kdv_dahil: tut, kalan_tutar_kdv: (kalan != null ? kalan : tut),
    tahsil_edildi: false
  };
}
function meta(bugun) {
  return { toplamBorc: 100, gunuGelen: 80, sozlesmeGun: 18, bugunIso: bugun || '', sozlesmeTarihi: '2025-10-18', gunBazliAyHaric: true };
}
"""
        + fmt
        + "\n"
        + kart
        + "\n"
        + ozet_blk
        + "\n"
        + filtre_blk
        + "\n"
        + r"""
var body = document.getElementById('fatura_rapor_grid_modal_body');

// (a) 2026-11-05: 2025-12 … 2027-10 → son görünen 2026-11
var aylarA = [];
for (var y = 2025; y <= 2027; y++) {
  for (var m = 1; m <= 12; m++) {
    if (y === 2025 && m < 12) continue;
    if (y === 2027 && m > 10) continue;
    aylarA.push(ayObj(y, m, 1000, 500));
  }
}
faturaRaporGridModalCiz({ aylar: aylarA }, 'A', meta('2026-11-05'));
var hA = body.innerHTML || '';
say('a son ay 2026-11', hA.indexOf('data-ay-key="2026-11"') >= 0 || hA.indexOf('Kasım 2026') >= 0);
say('a 2026-12 yok', hA.indexOf('data-ay-key="2026-12"') < 0 && hA.indexOf('Aralık 2026') < 0);
say('a 2027-01 yok', hA.indexOf('data-ay-key="2027-1"') < 0 && hA.indexOf('data-ay-key="2027-01"') < 0 && hA.indexOf('Ocak 2027') < 0);
say('a 2025-12 var', hA.indexOf('data-ay-key="2025-12"') >= 0 || hA.indexOf('Aralık 2025') >= 0);
say('a temiz', temiz(hA));

// (b) bu ay görünür (sg gelmemiş olsa da — sadece görünürlük)
say('b bu ay var', hA.indexOf('Kasım 2026') >= 0 || hA.indexOf('data-ay-key="2026-11"') >= 0);

// (c) Aralık→Ocak
faturaRaporGridModalCiz({ aylar: [ayObj(2026, 12, 10, 10), ayObj(2027, 1, 20, 20), ayObj(2027, 2, 30, 30)] }, 'C', meta('2026-12-31'));
var hC1 = body.innerHTML || '';
say('c1 2026-12 var', hC1.indexOf('Aralık 2026') >= 0 || hC1.indexOf('data-ay-key="2026-12"') >= 0);
say('c1 2027-01 yok', hC1.indexOf('Ocak 2027') < 0 && hC1.indexOf('data-ay-key="2027-1"') < 0 && hC1.indexOf('data-ay-key="2027-01"') < 0);
faturaRaporGridModalCiz({ aylar: [ayObj(2026, 12, 10, 10), ayObj(2027, 1, 20, 20), ayObj(2027, 2, 30, 30)] }, 'C2', meta('2027-01-01'));
var hC2 = body.innerHTML || '';
say('c2 2027-01 var', hC2.indexOf('Ocak 2027') >= 0 || hC2.indexOf('data-ay-key="2027-1"') >= 0 || hC2.indexOf('data-ay-key="2027-01"') >= 0);
say('c2 2027-02 yok', hC2.indexOf('Şubat 2027') < 0 && hC2.indexOf('data-ay-key="2027-2"') < 0 && hC2.indexOf('data-ay-key="2027-02"') < 0);

// (d) özet yalnız görünenlerden: 2 ay görünür (100+200 plan), gelecek 999 gizlenir
faturaRaporGridModalCiz({
  aylar: [ayObj(2026, 10, 100, 40), ayObj(2026, 11, 200, 50), ayObj(2026, 12, 999, 999)]
}, 'D', meta('2026-11-15'));
var hD = body.innerHTML || '';
say('d aylar 2', hD.indexOf('Aylar: 2') >= 0, hD.match(/Aylar: \d+/) && hD.match(/Aylar: \d+/)[0]);
say('d plan 300', hD.indexOf('Plan: 300,00') >= 0 || hD.indexOf('Plan: 300.00') >= 0);
say('d kalan 90', hD.indexOf('Kalan: 90,00') >= 0 || hD.indexOf('Kalan: 90.00') >= 0);
say('d 999 yok', hD.indexOf('999') < 0 || (hD.indexOf('Aralık 2026') < 0));

// (e) kapanış sonrası aylar zaten yoksa korunur; gelecekte kapanış + gelecek aylar yine gizli
faturaRaporGridModalCiz({
  aylar: [ayObj(2026, 9, 10, 10), ayObj(2026, 10, 10, 10)]  // kapanış geçmiş simülasyonu: sonraki yok
}, 'E', meta('2026-11-05'));
var hE = body.innerHTML || '';
say('e kapanis gecmis', (hE.indexOf('Eylül 2026') >= 0 || hE.indexOf('data-ay-key="2026-9"') >= 0 || hE.indexOf('data-ay-key="2026-09"') >= 0)
  && hE.indexOf('Kasım 2026') < 0);
faturaRaporGridModalCiz({
  aylar: [ayObj(2026, 11, 10, 10), ayObj(2026, 12, 10, 10), ayObj(2027, 1, 10, 10)]
}, 'E2', meta('2026-11-05'));
var hE2 = body.innerHTML || '';
say('e kapanis gelecek yine filtre', hE2.indexOf('Kasım 2026') >= 0 && hE2.indexOf('Aralık 2026') < 0 && hE2.indexOf('Ocak 2027') < 0);

// (f) iptal
faturaRaporGridModalCiz({ iptal: true, aylar: [ayObj(2026, 12, 1, 1)], iptal_mesaj: 'Sözleşme iptal edildi: aylık borç oluşmaz.' }, 'F', meta('2026-11-05'));
var hF = body.innerHTML || '';
say('f iptal msg', hF.indexOf('iptal') >= 0 || hF.indexOf('İptal') >= 0 || hF.indexOf('Sözleşme iptal') >= 0);
say('f iptal kart yok', hF.indexOf('sozlesmeler-ay-kart') < 0);

// (g) tüm aylar gelecekte
faturaRaporGridModalCiz({ aylar: [ayObj(2027, 1, 10, 10), ayObj(2027, 2, 10, 10)] }, 'G', meta('2026-11-05'));
var hG = body.innerHTML || '';
say('g henuz mesaj', hG.indexOf('Henüz borçlanan ay yok.') >= 0);
say('g kart yok', hG.indexOf('sozlesmeler-ay-kart') < 0);
say('g ozet var', hG.indexOf('Toplam Borç') >= 0);

// (h) istanbul_bugun yok → süzgeç yok
faturaRaporGridModalCiz({ aylar: [ayObj(2026, 11, 10, 10), ayObj(2027, 5, 99, 99)] }, 'H', meta(''));
var hH = body.innerHTML || '';
say('h gelecek gorunur', hH.indexOf('Mayıs 2027') >= 0 || hH.indexOf('data-ay-key="2027-5"') >= 0 || hH.indexOf('data-ay-key="2027-05"') >= 0);
say('h temiz', temiz(hH));

// (j) alt alanlar değişmedi
say('j etiket', hA.indexOf('Kasım dahil') >= 0 || hA.indexOf('etiket') >= 0);
say('j toplam label', hA.indexOf('Toplam Borç') >= 0 && hA.indexOf('Günü Gelen Borç') >= 0);
"""
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(node)
        path = f.name
    try:
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            check("node ay filtre", False, "node yok")
            return
        out = (r.stdout or "") + (r.stderr or "")
        print(out.strip())
        check("node ay filtre exit", r.returncode == 0, out[-400:])
        for needle in [
            "ok a son ay 2026-11",
            "ok a 2026-12 yok",
            "ok a 2027-01 yok",
            "ok b bu ay var",
            "ok c1 2026-12 var",
            "ok c1 2027-01 yok",
            "ok c2 2027-01 var",
            "ok c2 2027-02 yok",
            "ok d aylar 2",
            "ok d plan 300",
            "ok d kalan 90",
            "ok e kapanis gecmis",
            "ok e kapanis gelecek yine filtre",
            "ok f iptal msg",
            "ok f iptal kart yok",
            "ok g henuz mesaj",
            "ok h gelecek gorunur",
            "ok h temiz",
            "ok j toplam label",
        ]:
            check(needle, needle in out)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    # (i) sozlesmelerAylikCacheRender gövdesinde gelecek ay popup filtresi yok
    srender = html[
        html.find("function sozlesmelerAylikCacheRender") : html.find(
            "function sozlesmelerAylikCacheRender"
        )
        + 8000
    ]
    check(
        "i sozlesmeler filtre yok",
        "Henüz borçlanan ay yok." not in srender
        and "faturaRaporGridModalBugunAyIdx" not in srender
        and "faturaRaporGridModalAyIdx" not in srender,
    )



def test_popup_ay_filtre_sayisal_idx():
    """Sayısal ay idx: yalnız ay_key / yalnız yil+ay / dolgusuz / karışık / bozuk."""
    html = _html()
    try:
        fmt = _js_brace_extract(html, "function faturaRaporGridModalFmt")
        kart = _js_brace_extract(html, "function faturaRaporGridModalKartHtml")
        ay_i = html.find("var FATURA_RAPOR_AYLAR_TR")
        ozet_blk = html[ay_i : html.find("function faturaRaporGridModalAyIdx", ay_i)]
        filtre_i = html.find("function faturaRaporGridModalAyIdx")
        ciz_end = html.find("function faturaRaporGridModalAc", filtre_i)
        filtre_blk = html[filtre_i:ciz_end]
    except ValueError as e:
        check("sayisal extract", False, str(e))
        return
    check("sayisal extract ciz", "aylarVis" in filtre_blk and "faturaRaporGridModalAyIdx" in filtre_blk)
    check("sayisal metin k<= yok", "k <= bugunAy" not in filtre_blk and "k < bugunAy" not in filtre_blk)
    check("sayisal idx <= var", "idx <= bugunIdx" in filtre_blk)
    check("sayisal new Date yok", "new Date(" not in filtre_blk)

    node = (
        r"""
global.window = global;
global.document = {
  getElementById: function (id) {
    if (id === 'fatura_rapor_grid_modal_body') {
      if (!global.__b) {
        global.__b = { _i: '' };
        Object.defineProperty(global.__b, 'innerHTML', {
          get: function () { return this._i; },
          set: function (v) { this._i = String(v == null ? '' : v); }
        });
      }
      return global.__b;
    }
    if (id === 'fatura_rapor_grid_modal_title') {
      if (!global.__t) global.__t = { textContent: '' };
      return global.__t;
    }
    return null;
  },
  addEventListener: function () {},
  removeEventListener: function () {}
};
function say(ad, ok, ek) {
  console.log((ok ? 'ok ' : 'FAIL ') + ad + (ok ? '' : (' :: ' + (ek || ''))));
  if (!ok) process.exitCode = 1;
}
function temiz(s) {
  s = String(s == null ? '' : s);
  return s.indexOf('NaN') < 0 && s.indexOf('undefined') < 0;
}
function meta(bugun) {
  return { toplamBorc: 100, gunuGelen: 80, sozlesmeGun: 18, bugunIso: bugun || '', sozlesmeTarihi: '2025-10-18', gunBazliAyHaric: true };
}
function hasAy(h, y, m) {
  var pad = (m < 10 ? '0' : '') + String(m);
  var k1 = 'data-ay-key="' + y + '-' + m + '"';
  var k2 = 'data-ay-key="' + y + '-' + pad + '"';
  return h.indexOf(k1) >= 0 || h.indexOf(k2) >= 0;
}
function noAy(h, y, m) {
  return !hasAy(h, y, m);
}
"""
        + fmt
        + "\n"
        + kart
        + "\n"
        + ozet_blk
        + "\n"
        + filtre_blk
        + "\n"
        + r"""
var body = document.getElementById('fatura_rapor_grid_modal_body');

// (a) YALNIZ ay_key (yil/ay YOK), bugün 2026-11-05
faturaRaporGridModalCiz({
  aylar: [
    { ay_key: '2026-11', tutar_kdv_dahil: 100, kalan_tutar_kdv: 100, tahsil_edildi: false },
    { ay_key: '2026-12', tutar_kdv_dahil: 200, kalan_tutar_kdv: 200, tahsil_edildi: false }
  ]
}, 'A', meta('2026-11-05'));
var hA = body.innerHTML || '';
say('a 2026-11 var', hasAy(hA, 2026, 11), hA.slice(0, 200));
say('a 2026-12 yok', noAy(hA, 2026, 12));
say('a temiz', temiz(hA));

// (b) YALNIZ yil+ay (ay_key YOK)
faturaRaporGridModalCiz({
  aylar: [
    { yil: 2026, ay: 11, tutar_kdv_dahil: 100, kalan_tutar_kdv: 100, tahsil_edildi: false },
    { yil: 2026, ay: 12, tutar_kdv_dahil: 200, kalan_tutar_kdv: 200, tahsil_edildi: false }
  ]
}, 'B', meta('2026-11-05'));
var hB = body.innerHTML || '';
say('b 2026-11 var', hasAy(hB, 2026, 11) || hB.indexOf('Kasım 2026') >= 0);
say('b 2026-12 yok', noAy(hB, 2026, 12) && hB.indexOf('Aralık 2026') < 0);
say('b temiz', temiz(hB));

// (c) DOLGUSUZ ay_key — metin karşılaştırmasında yanlış çıkacak durumlar
faturaRaporGridModalCiz({
  aylar: [
    { ay_key: '2026-9', tutar_kdv_dahil: 90, kalan_tutar_kdv: 90, tahsil_edildi: false },
    { ay_key: '2026-10', tutar_kdv_dahil: 100, kalan_tutar_kdv: 100, tahsil_edildi: false },
    { ay_key: '2026-11', tutar_kdv_dahil: 110, kalan_tutar_kdv: 110, tahsil_edildi: false },
    { ay_key: '2026-12', tutar_kdv_dahil: 120, kalan_tutar_kdv: 120, tahsil_edildi: false },
    { ay_key: '2027-5', tutar_kdv_dahil: 50, kalan_tutar_kdv: 50, tahsil_edildi: false }
  ]
}, 'C', meta('2026-11-05'));
var hC = body.innerHTML || '';
say('c 9 var', hasAy(hC, 2026, 9) || hC.indexOf('Eylül 2026') >= 0);
say('c 10 var', hasAy(hC, 2026, 10) || hC.indexOf('Ekim 2026') >= 0);
say('c 11 var', hasAy(hC, 2026, 11) || hC.indexOf('Kasım 2026') >= 0);
say('c 12 yok', noAy(hC, 2026, 12) && hC.indexOf('Aralık 2026') < 0);
say('c 2027-5 yok', noAy(hC, 2027, 5) && hC.indexOf('Mayıs 2027') < 0);
say('c aylar 3', hC.indexOf('Aylar: 3') >= 0, hC.match(/Aylar: \d+/) && hC.match(/Aylar: \d+/)[0]);
say('c temiz', temiz(hC));

// (d) dolgulu/dolgusuz karışık aynı sonuç
faturaRaporGridModalCiz({
  aylar: [
    { ay_key: '2026-09', tutar_kdv_dahil: 90, kalan_tutar_kdv: 90, tahsil_edildi: false },
    { ay_key: '2026-10', tutar_kdv_dahil: 100, kalan_tutar_kdv: 100, tahsil_edildi: false },
    { ay_key: '2026-11', tutar_kdv_dahil: 110, kalan_tutar_kdv: 110, tahsil_edildi: false },
    { ay_key: '2026-12', tutar_kdv_dahil: 120, kalan_tutar_kdv: 120, tahsil_edildi: false },
    { ay_key: '2027-05', tutar_kdv_dahil: 50, kalan_tutar_kdv: 50, tahsil_edildi: false }
  ]
}, 'D', meta('2026-11-05'));
var hD = body.innerHTML || '';
say('d 9 var', hasAy(hD, 2026, 9) || hD.indexOf('Eylül 2026') >= 0);
say('d 10 var', hasAy(hD, 2026, 10) || hD.indexOf('Ekim 2026') >= 0);
say('d 11 var', hasAy(hD, 2026, 11) || hD.indexOf('Kasım 2026') >= 0);
say('d 12 yok', noAy(hD, 2026, 12) && hD.indexOf('Aralık 2026') < 0);
say('d 2027-5 yok', noAy(hD, 2027, 5) && hD.indexOf('Mayıs 2027') < 0);
say('d aylar 3', hD.indexOf('Aylar: 3') >= 0);
say('d temiz', temiz(hD));

// (e) ay_key/yil/ay yok veya bozuk → gizlenmez, NaN/undefined yok
faturaRaporGridModalCiz({
  aylar: [
    { ay_key: 'abc', tutar_kdv_dahil: 77, kalan_tutar_kdv: 77, tahsil_edildi: false },
    { ay_key: '2026-12', tutar_kdv_dahil: 120, kalan_tutar_kdv: 120, tahsil_edildi: false }
  ]
}, 'E', meta('2026-11-05'));
var hE = body.innerHTML || '';
say('e bozuk gorunur', hE.indexOf('77') >= 0 || hE.indexOf('sozlesmeler-ay-kart') >= 0);
say('e 2026-12 yok', noAy(hE, 2026, 12) && hE.indexOf('Aralık 2026') < 0);
say('e temiz', temiz(hE));
say('e aylar 1', hE.indexOf('Aylar: 1') >= 0, hE.match(/Aylar: \d+/) && hE.match(/Aylar: \d+/)[0]);
"""
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(node)
        path = f.name
    try:
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            check("node sayisal idx", False, "node yok")
            return
        out = (r.stdout or "") + (r.stderr or "")
        print(out.strip())
        check("node sayisal idx exit", r.returncode == 0, out[-400:])
        for needle in [
            "ok a 2026-11 var",
            "ok a 2026-12 yok",
            "ok a temiz",
            "ok b 2026-11 var",
            "ok b 2026-12 yok",
            "ok b temiz",
            "ok c 9 var",
            "ok c 10 var",
            "ok c 11 var",
            "ok c 12 yok",
            "ok c 2027-5 yok",
            "ok c aylar 3",
            "ok c temiz",
            "ok d 9 var",
            "ok d 10 var",
            "ok d 11 var",
            "ok d 12 yok",
            "ok d 2027-5 yok",
            "ok d aylar 3",
            "ok d temiz",
            "ok e bozuk gorunur",
            "ok e 2026-12 yok",
            "ok e temiz",
            "ok e aylar 1",
        ]:
            check(needle, needle in out)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    test_sunucu_gun_gelen()
    test_sablon_ve_excel()
    test_node_popup_ozet()
    test_tutar_kurus_ve_buyuk()
    test_gercek_satir_uretimi_popup_tutar()
    test_ozet_ay_etiket_gercek_satir()
    test_popup_ay_filtre_gelecek_gizle()
    test_popup_ay_filtre_sayisal_idx()
    if FAILS:
        print("FAILED:", ", ".join(FAILS))
        sys.exit(1)
    print("ALL_OK test_musteriler_grid_popup_toplam")
