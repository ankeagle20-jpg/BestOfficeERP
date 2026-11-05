# -*- coding: utf-8 -*-
"""Müşteriler raporu «Günler» süzgeci. Canlıya yazmaz. PII yok."""
from __future__ import annotations

import inspect
import os
import sys
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


def _satir(mid, gun, ay=1, borc=10.0, durum="Aktif", giris=None, soz_ay=None):
    g = int(gun) if gun is not None else 0
    a = int(soz_ay if soz_ay is not None else ay)
    if giris is None and g >= 1:
        giris = "2025-%02d-%02d" % (a, min(g, 28) if a == 2 else g)
    return {
        "musteri_id": mid,
        "firma_adi": "K%d" % mid,
        "giris_tarihi": giris or "",
        "aylik_tutar": 100.0,
        "toplam_borc": borc,
        "durum_etiket": durum,
        "hizmet_turu": "Sanal",
        "liste_ara_blob": "",
        "grup2": "",
        "hazir_ofis_oda_no": None,
        "sozlesme_gun": g,
        "sozlesme_ay": a if 1 <= a <= 12 else 0,
    }


FIKSTUR = [
    _satir(1, 1, 3, 10),
    _satir(2, 15, 3, 20),
    _satir(3, 15, 4, 5),
    _satir(4, 31, 1, 8),
    _satir(5, 0, 1, 7, giris=""),  # gün yok
    _satir(6, 15, 3, 0.0),  # borçsuz
    _satir(7, 28, 2, 12),
]


def _suz(**kw):
    from routes.faturalar_routes import _firma_ozet_client_filtre_uygula

    base = dict(
        q="",
        grup2=[],
        hizmet=[],
        durum="",
        oda=None,
        yillar=None,
        sadece_borclu=False,
        gunler=None,
        aylar=None,
    )
    base.update(kw)
    return _firma_ozet_client_filtre_uygula(FIKSTUR, **base)


def test_parse_ve_sql():
    from routes.faturalar_routes import (
        _FIRMA_OZET_GIRIS_TARIHI_SQL,
        _fatura_rapor_giris_gunleri_parse,
        _firma_ozet_dataset_from_request,
    )

    check("parse yok", _fatura_rapor_giris_gunleri_parse(None) is None)
    check("parse bos", _fatura_rapor_giris_gunleri_parse("  ") is None)
    check("parse sirali", _fatura_rapor_giris_gunleri_parse("31,1,1,15") == [1, 15, 31])
    check("parse gecersiz", _fatura_rapor_giris_gunleri_parse("0,32,abc") is None)
    src = inspect.getsource(_firma_ozet_dataset_from_request)
    check(
        "sql day ayni ifade",
        "EXTRACT(DAY FROM ({_FIRMA_OZET_GIRIS_TARIHI_SQL})" in src
        and "_fatura_rapor_giris_gunleri_parse" in src
        and "giri_gunler" in src,
    )
    check("ay da ayni ifade", "EXTRACT(MONTH FROM ({_FIRMA_OZET_GIRIS_TARIHI_SQL})" in src)


def test_client_filtre():
    hepsi = _suz()
    check("e bos ayni", [r["musteri_id"] for r in hepsi] == [1, 2, 3, 4, 5, 6, 7])

    tek = _suz(gunler=[15])
    check("a tek gun", [r["musteri_id"] for r in tek] == [2, 3, 6], [r["musteri_id"] for r in tek])

    cok = _suz(gunler=[1, 31])
    check("b or gunler", [r["musteri_id"] for r in cok] == [1, 4], [r["musteri_id"] for r in cok])

    and_ay = _suz(gunler=[15], aylar=[3])
    check("c and aylar", [r["musteri_id"] for r in and_ay] == [2, 6], [r["musteri_id"] for r in and_ay])

    and_borc = _suz(gunler=[15], sadece_borclu=True)
    check("d and borclu", [r["musteri_id"] for r in and_borc] == [2, 3], [r["musteri_id"] for r in and_borc])

    check("e bos tekrar", [r["musteri_id"] for r in _suz(gunler=None)] == [1, 2, 3, 4, 5, 6, 7])
    check("bos liste hic", _suz(gunler=[]) == [])

    # gün seçiliyken sozlesme_gun=0 gizlenir
    check("gecersiz gun gizlenir", all(r["musteri_id"] != 5 for r in _suz(gunler=[1, 15, 31])))


def test_gun_sutunu_ayni_alan():
    from routes.faturalar_routes import _firma_ozet_row_to_satir_item

    # 31. gün
    it31 = _firma_ozet_row_to_satir_item(
        {"id": 101, "firma_adi": "A", "kyc_soz_bas": date(2024, 1, 31), "giris_raw": None},
        pasifleri_dahil=True,
        grid_ozet_map={},
    )
    check("f gun 31", it31.get("sozlesme_gun") == 31, it31.get("sozlesme_gun"))

    # ayın son günü (şubat olmayan)
    it30 = _firma_ozet_row_to_satir_item(
        {"id": 102, "firma_adi": "B", "kyc_soz_bas": "2024-04-30", "giris_raw": None},
        pasifleri_dahil=True,
        grid_ozet_map={},
    )
    check("f gun 30", it30.get("sozlesme_gun") == 30, it30.get("sozlesme_gun"))

    # sozlesme_tarihi boş + rent_start (giris_raw) yedek
    it_yedek = _firma_ozet_row_to_satir_item(
        {"id": 103, "firma_adi": "C", "kyc_soz_bas": None, "giris_raw": date(2025, 7, 19)},
        pasifleri_dahil=True,
        grid_ozet_map={},
    )
    check("f yedek rent_start gun", it_yedek.get("sozlesme_gun") == 19, it_yedek.get("sozlesme_gun"))

    # filtre aynı sozlesme_gun alanından
    from routes.faturalar_routes import _firma_ozet_client_filtre_uygula

    rows = [it31, it30, it_yedek]
    f19 = _firma_ozet_client_filtre_uygula(
        rows, q="", grup2=[], hizmet=[], durum="", oda=None, gunler=[19]
    )
    check("f filtre=sutun", [r["musteri_id"] for r in f19] == [103])
    f31 = _firma_ozet_client_filtre_uygula(
        rows, q="", grup2=[], hizmet=[], durum="", oda=None, gunler=[31]
    )
    check("f filtre 31", [r["musteri_id"] for r in f31] == [101])


def test_sayac_ve_excel():
    from routes.faturalar_routes import _firma_ozet_client_filtre_uygula, _firma_ozet_recalc_ozet

    filt = _firma_ozet_client_filtre_uygula(
        FIKSTUR, q="", grup2=[], hizmet=[], durum="", oda=None, gunler=[15]
    )
    aylik, borc, n = _firma_ozet_recalc_ozet(filt)
    check("g sayac n", n == 3, n)
    check("g toplam tutar", abs(aylik - 300.0) < 0.01, aylik)
    check("g toplam borc", abs(borc - 25.0) < 0.01, borc)

    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    a = html.find("function faturaRaporExcelAktar")
    b = html.find("\nfunction ", a + 10)
    excel_fn = html[a:b]
    check("h excel param", "giri_gunler" in excel_fn or "GirisGunQuerySuffix" in excel_fn)

    src = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    check("h excel client gunler", "gunler=giris_gunler_filtre" in src)


def test_ui_kalip():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "_t.js").read_text(encoding="utf-8")
    check("ui wrap", 'id="fatura_rapor_giris_gun_wrap"' in html and 'id="fatura_rapor_giris_gun_btn"' in html)
    check("ui aylar yakin", html.find("fatura_rapor_giris_ay_wrap") < html.find("fatura_rapor_giris_gun_wrap") < html.find("fatura_rapor_giris_yil_wrap"))
    check("ui js oku", "function faturaRaporGirisGunlerOku" in js and "giri_gunler=" in js)
    check("ui tumu pill", 'data-giris-gun="0"' in js and "Günler (" in js)
    check("ui liste data-fr-gun", "data-fr-gun-n" in html[html.find("function faturaRaporListeAraUygula") : html.find("function faturaRaporListeAraTemizle")])




def test_gun_panel_ac_kapa():
    """Günler panel: HTML'de tanımlı (Giriş _t.js yüklemez); id eşleşmesi; aç/kapa mantığı."""
    import subprocess
    import tempfile

    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "_t.js").read_text(encoding="utf-8")

    check("panel btn onclick", 'id="fatura_rapor_giris_gun_btn"' in html and "faturaRaporGirisGunPanelToggle(event)" in html)
    check("panel pop id", 'id="fatura_rapor_giris_gun_pop"' in html and 'id="fatura_rapor_giris_gun_row"' in html)
    check("panel wrap id", 'id="fatura_rapor_giris_gun_wrap"' in html)
    check("toggle html tanimli", "function faturaRaporGirisGunPanelToggle" in html)
    check("render html tanimli", "function faturaRaporGirisGunPanelRender" in html)
    check("tumu html tanimli", "function faturaRaporGirisGunTumu" in html)
    check("oku/yaz html", "function faturaRaporGirisGunlerOku" in html and "function faturaRaporGirisGunlerYaz" in html)
    check("toggle tjs yedek", "function faturaRaporGirisGunPanelToggle" in js)

    a = html.find("function faturaRaporGirisAyPanelToggle")
    b = html.find("\nfunction ", a + 10)
    check("ay acinca gun kapat", "faturaRaporGirisGunPanelKapat" in html[a:b])

    y = html.find("function faturaRaporGirisYilPanelToggle")
    yb = html.find("\nfunction ", y + 10)
    check("yil acinca gun kapat", "faturaRaporGirisGunPanelKapat" in html[y:yb])

    check(
        "disari tik gun wrap",
        "fatura_rapor_giris_gun_wrap" in html and "faturaRaporGirisGunPanelKapat" in html,
    )

    start = html.find("window.__faturaRaporGirisGunler")
    if start < 0:
        start = html.find("function faturaRaporGirisGunlerOku")
    end2 = html.find("function faturaRaporGirisGunTumu", start)
    end2 = html.find("\nif (typeof window.__faturaRaporGirisYillar", end2)
    if end2 < 0:
        end2 = html.find("\nfunction faturaRaporGirisYillarOku", start)
    block = html[start:end2]
    node = (
        """
global.window = global;
var popHidden = true;
var btnExpanded = 'false';
var rowBuilt = null;
var rowHtml = '';
global.document = {
  getElementById: function (id) {
    if (id === 'fatura_rapor_giris_gun_pop') {
      return {
        get hidden() { return popHidden; },
        set hidden(v) { popHidden = !!v; },
        style: {},
        setAttribute: function () {}
      };
    }
    if (id === 'fatura_rapor_giris_gun_btn') {
      return {
        classList: { toggle: function () {} },
        innerHTML: '',
        title: '',
        setAttribute: function (k, v) { if (k === 'aria-expanded') btnExpanded = String(v); },
        getBoundingClientRect: function () { return { left: 10, top: 10, width: 80, height: 28 }; }
      };
    }
    if (id === 'fatura_rapor_giris_gun_row') {
      return {
        dataset: rowBuilt || (rowBuilt = {}),
        get innerHTML() { return rowHtml; },
        set innerHTML(v) { rowHtml = v; },
        addEventListener: function () {},
        contains: function () { return true; },
        querySelectorAll: function () { return []; }
      };
    }
    if (id === 'fatura_rapor_gorunum') return { value: 'detay' };
    return null;
  },
  documentElement: { clientWidth: 1200 }
};
global.requestAnimationFrame = function (fn) { fn(); };
"""
        + block
        + """
var opened = false;
try {
  faturaRaporGirisGunPanelToggle({ stopPropagation: function () {} });
  opened = (popHidden === false && btnExpanded === 'true');
} catch (e) {
  console.log('FAIL node toggle throw :: ' + e);
  process.exitCode = 1;
}
console.log((opened ? 'ok ' : 'FAIL ') + 'node panel acilir');
if (!opened) process.exitCode = 1;
if (rowHtml.indexOf('data-giris-gun=\"31\"') < 0) {
  console.log('FAIL node 1-31 pills');
  process.exitCode = 1;
} else {
  console.log('ok node 1-31 pills');
}
faturaRaporGirisGunPanelToggle({ stopPropagation: function () {} });
console.log((popHidden === true ? 'ok ' : 'FAIL ') + 'node panel kapanir');
if (popHidden !== true) process.exitCode = 1;
"""
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(node)
        path = f.name
    try:
        r = subprocess.run(["node", path], capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        check("node panel harness", False, "node yok")
        return
    out = (r.stdout or "") + (r.stderr or "")
    print(out.strip())
    check("node panel harness exit", r.returncode == 0, out[-240:])
    check("node acilir satiri", "ok node panel acilir" in out)
    check("node 1-31 satiri", "ok node 1-31 pills" in out)
    check("node kapanir satiri", "ok node panel kapanir" in out)


if __name__ == "__main__":
    test_parse_ve_sql()
    test_client_filtre()
    test_gun_sutunu_ayni_alan()
    test_sayac_ve_excel()
    test_ui_kalip()
    test_gun_panel_ac_kapa()
    if FAILS:
        print("FAILED:", ", ".join(FAILS))
        sys.exit(1)
    print("ALL_OK test_musteriler_gun_filtre")
