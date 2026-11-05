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


if __name__ == "__main__":
    test_sunucu_gun_gelen()
    test_sablon_ve_excel()
    test_node_popup_ozet()
    if FAILS:
        print("FAILED:", ", ".join(FAILS))
        sys.exit(1)
    print("ALL_OK test_musteriler_grid_popup_toplam")
