# -*- coding: utf-8 -*-
"""Müşteriler raporu Grid sütunu + salt okunur modal. Prod yazmaz. PII yok."""
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


def _tjs() -> str:
    return (ROOT / "_t.js").read_text(encoding="utf-8")


def test_ham_toplam_sunucu():
    from routes import faturalar_routes as fr

    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 5)
    rows = [
        {
            "musteri_id": 1,
            "toplam_borc": 300.0,
            "borc_hesaplanamadi": False,
            "sozlesme_gun": 18,
            "borc_ref_ay": 100.0,
            "geciken_ay": 3,
        },
        {
            "musteri_id": 2,
            "toplam_borc": 200.0,
            "borc_hesaplanamadi": False,
            "sozlesme_gun": 3,
            "borc_ref_ay": 50.0,
            "geciken_ay": 2,
        },
    ]
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows, aktif=True)
    check("a ham dusum oncesi", rows[0]["toplam_borc_ham"] == 300.0, rows[0])
    check("a borc dustu", rows[0]["toplam_borc"] == 200.0, rows[0].get("toplam_borc"))
    check("a2 ham=borc dahil", rows[1]["toplam_borc_ham"] == 200.0 and rows[1]["toplam_borc"] == 200.0)

    rows2 = [
        {
            "musteri_id": 3,
            "toplam_borc": 150.0,
            "borc_hesaplanamadi": False,
            "sozlesme_gun": 18,
            "borc_ref_ay": 50.0,
            "geciken_ay": 2,
        }
    ]
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows2, aktif=False)
    check("b kapali ham=borc", rows2[0]["toplam_borc_ham"] == 150.0 and rows2[0]["toplam_borc"] == 150.0)
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = None


def test_excel_degismedi():
    src = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    i = src.find("def _firma_ozet_workbook_bytes")
    chunk = src[i : i + 2800]
    check("excel Grid yok", '"Grid"' not in chunk)
    check("excel Toplam borç var", '"Toplam borç"' in chunk)
    headers = [
        "Giriş tarihi",
        "Firma ismi",
        "Vergi no",
        "Kimlik no",
        "Sözleşme aylık (KDV dahil)",
        "İlk kira",
        "Güncel",
        "Toplam borç",
        "Gün",
        "Ay",
        "Geciken ay",
        "Grup2",
        "Hizmet türü",
        "Durum",
    ]
    for h in headers:
        check("excel h " + h, f'"{h}"' in chunk)


def test_sablon():
    html = _html()
    check("col 16 th", 'data-fr-col-id="16"' in html and ">Grid</th>" in html)
    check("grid btn", "fatura-rapor-firma-grid-btn" in html)
    check("title gridi", "Aylık grid" in html and "faturaRaporGridModalAc" in html)
    check("modal markup", 'id="fatura_rapor_grid_modal"' in html)
    check("fn modal ac", "function faturaRaporGridModalAc" in html)
    check("fn modal ciz", "function faturaRaporGridModalCiz" in html)
    check("fn modal kapat", "function faturaRaporGridModalKapat" in html)
    check("salt okunur pointer", "pointer-events: none" in html)
    check("modal hidden display none", "fatura-rapor-grid-modal-backdrop[hidden]" in html)
    check("ham alan fe", "toplam_borc_ham" in html)
    check("src _t.js yok head", "_t.js" not in html[:8000])
    # Excel FE call unchanged path — workbook headers not from FE
    check("firma tik ayri", "faturaRaporFirmaKartaGit" in html)
    check("grid stopProp", "stopPropagation" in html[html.find("function faturaRaporGridModalAc") :][:500])
    # no edit buttons in modal render
    modal_fn = html[html.find("function faturaRaporGridModalCiz") : html.find("function faturaRaporGridModalAc")]
    check("modal borclandir yok", "sozlesmeAylikBorclandir" not in modal_fn and "tahsil" not in modal_fn.lower().split("kalan")[0] or "Tahsil Et" not in modal_fn)
    check("modal kaydet yok", "Kaydet" not in modal_fn and "Borçlandır" not in modal_fn)


def test_node_modal_ve_tik():
    html = _html()
    # extract modal functions
    start = html.find("/** Müşteriler raporu Grid sütunu:")
    if start < 0:
        start = html.find("function faturaRaporGridModalKapat")
    end = html.find("function faturaRaporFirmaKartaGit(ev, mid) {", start)
    fns = html[start:end]
    check("extract fns", "function faturaRaporGridModalAc" in fns)

    node = (
        r"""
global.window = global;
var calls = { firma: [], switchTab: [] };
global.document = {
  getElementById: function (id) {
    if (id === 'fatura_rapor_grid_modal') {
      if (!global.__frGridModalEl) {
        global.__frGridModalEl = { _h: true, onclick: null };
        Object.defineProperty(global.__frGridModalEl, 'hidden', {
          get: function () { return !!this._h; },
          set: function (v) { this._h = !!v; }
        });
      }
      return global.__frGridModalEl;
    }
    if (id === 'fatura_rapor_grid_modal_body') {
      if (!global.__frGridBodyEl) {
        global.__frGridBodyEl = { _i: '' };
        Object.defineProperty(global.__frGridBodyEl, 'innerHTML', {
          get: function () { return this._i || ''; },
          set: function (v) { this._i = String(v == null ? '' : v); }
        });
      }
      return global.__frGridBodyEl;
    }
    if (id === 'fatura_rapor_grid_modal_title') {
      if (!global.__frGridTitleEl) global.__frGridTitleEl = { textContent: '' };
      return global.__frGridTitleEl;
    }
    return null;
  },
  addEventListener: function () {},
  removeEventListener: function () {}
};
function faturaRaporFirmaKartaGit() { calls.firma.push([].slice.call(arguments)); }
function switchTab(i) { calls.switchTab.push(i); }
global.girisFetchJsonCached = function () {
  return new Promise(function () { /* unresolved — Ac async ezmesin */ });
};
"""
        + fns
        + r"""
function say(ad, ok, ek) {
  console.log((ok ? 'ok ' : 'FAIL ') + ad + (ok ? '' : (' :: ' + (ek || ''))));
  if (!ok) process.exitCode = 1;
}

var body = document.getElementById('fatura_rapor_grid_modal_body');

// (d) iptal mesaj
faturaRaporGridModalCiz({ iptal: true, aylar: [], iptal_mesaj: 'Sözleşme iptal edildi: aylık borç oluşmaz.' }, 'X');
say('d iptal msg', (body.innerHTML || '').indexOf('iptal') >= 0 || (body.innerHTML || '').toLowerCase().indexOf('iptal') >= 0 || (body.innerHTML || '').indexOf('Sözleşme') >= 0);

// (e) salt okunur — no edit buttons in rendered HTML
faturaRaporGridModalCiz({
  aylar: [
    { yil: 2025, ay: 1, ay_key: '2025-1', tutar_kdv_dahil: 100, tahsil_edildi: false, kalan_tutar_kdv: 100 },
    { yil: 2025, ay: 2, ay_key: '2025-2', tutar_kdv_dahil: 100, tahsil_edildi: true, kalan_tutar_kdv: 0 }
  ]
}, 'Y');
var h = body.innerHTML || '';
say('e grid kart', h.indexOf('sozlesmeler-ay-kart') >= 0);
say('e borclandir yok', h.indexOf('Borçlandır') < 0 && h.indexOf('sozlesmeAylikBorclandir') < 0);
say('e tahsil btn yok', h.indexOf('Tahsil Et') < 0 && h.indexOf('sozlesmeAylikTahsilEt') < 0);
say('e kaydet yok', h.indexOf('Kaydet') < 0);
say('e pointer none css kaynak', true);

// (c) grid click does not call firma / Sözleşmeler
calls.firma = [];
var stopped = false;
faturaRaporGridModalAc({
  stopPropagation: function () { stopped = true; },
  preventDefault: function () {},
  currentTarget: null
}, 55);
say('c stopPropagation', stopped === true);
say('c firma tik yok', calls.firma.length === 0);
say('c mid set', window.__faturaRaporGridModalMid === 55);
say('c switchTab yok', calls.switchTab.length === 0);

// invalid mid
var threw = false;
try { faturaRaporGridModalAc({ stopPropagation: function(){}, preventDefault: function(){} }, 'x'); } catch (e) { threw = true; }
say('c2 invalid throw yok', !threw);
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
            "ok d iptal msg",
            "ok e grid kart",
            "ok e borclandir yok",
            "ok e tahsil btn yok",
            "ok e kaydet yok",
            "ok c stopPropagation",
            "ok c firma tik yok",
            "ok c switchTab yok",
        ]:
            check(needle, needle in out)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_sort_ve_col_order():
    html = _html()
    check("sort 16", "colIndex === 16" in html and "data-fr-grid-borc-n" in html)
    check("default order 16", "8, 16, 9" in html or "[0, 1, 2, 3, 4, 5, 6, 7, 8, 16, 9" in html)
    check("migrate grid col", "migrateFirmaColOrderInsertGridCol" in html)


if __name__ == "__main__":
    test_ham_toplam_sunucu()
    test_excel_degismedi()
    test_sablon()
    test_sort_ve_col_order()
    test_node_modal_ve_tik()
    if FAILS:
        print("FAILED:", ", ".join(FAILS))
        sys.exit(1)
    print("ALL_OK test_musteriler_grid_sutunu")
