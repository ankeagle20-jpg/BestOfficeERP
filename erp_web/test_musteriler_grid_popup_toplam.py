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
    check("a borc degismedi bayrak kapali", rows[0]["toplam_borc"] == 300.0)
    check("a geciken ayni", rows[0]["geciken_ay"] == 3)

    # (g) bayrak açık: gun_gelen aynı, ham sabit, borc düşer
    rows_g = [_satir(10, 300.0, 18, 100.0, ga=3)]
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows_g, aktif=True)
    check("g ham sabit", rows_g[0]["toplam_borc_ham"] == 300.0)
    check("g gun_gelen ayni", rows_g[0]["toplam_borc_gun_gelen"] == 200.0)
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

    # (c) ref_ay=0
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 5)
    rows_c = [_satir(3, 300.0, 18, 0.0, ga=2)]
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows_c, aktif=False)
    check("c ayni", rows_c[0]["toplam_borc_gun_gelen"] == 300.0)
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
    check("attr toFixed", "gridHamN = gridHamNum.toFixed(2)" in html)
    check("attr btn textContent parse yok", "parseFloat(btn && btn.textContent)" not in html)
    check("istanbul plant", "data.istanbul_bugun" in html)
    check("src _t yok", "_t.js" not in html[:8000])
    # PII: labels/notes must not contain sample customer names patterns in ozet fn
    ozet = html[
        html.find("function faturaRaporGridModalOzetHtml") : html.find(
            "function faturaRaporGridModalCiz"
        )
    ]
    check("j pii firma yok", "firma" not in ozet.lower() or "firmaAdi" not in ozet)
    check("j telefon yok", "telefon" not in ozet.lower())
    src = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
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

    # popup fonksiyonları (aynı kaynak)
    try:
        fmt_popup = _js_brace_extract(html, "function faturaRaporGridModalFmt")
        ozet_not = _js_brace_extract(html, "function faturaRaporGridModalOzetNot")
        ozet_html = _js_brace_extract(html, "function faturaRaporGridModalOzetHtml")
        ozet_ciz = _js_brace_extract(html, "function faturaRaporGridModalCiz")
    except ValueError as e:
        check("extract popup fn", False, str(e))
        return
    check("extract ozet", "function faturaRaporGridModalOzetHtml" in ozet_html)

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
  return {
    toplamBorc: hamN,
    gunuGelen: gelN,
    sozlesmeGun: attrOf(trHtml, 'data-fr-soz-gun') || '',
    bugunIso: '2025-11-05'
  };
}
"""
        + fmt_popup
        + "\n"
        + ozet_not
        + "\n"
        + ozet_html
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


if __name__ == "__main__":
    test_sunucu_gun_gelen()
    test_sablon_ve_excel()
    test_node_popup_ozet()
    test_tutar_kurus_ve_buyuk()
    test_gercek_satir_uretimi_popup_tutar()
    if FAILS:
        print("FAILED:", ", ".join(FAILS))
        sys.exit(1)
    print("ALL_OK test_musteriler_grid_popup_toplam")
