# -*- coding: utf-8 -*-
"""Müşteriler raporu «Sadece borçlular» istemci süzgeci. Canlıya yazmaz. PII yok."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def _satir(mid, borc, durum="Aktif", grup2=""):
    return {
        "musteri_id": mid,
        "firma_adi": "K%d" % mid,
        "giris_tarihi": "2025-01-01",
        "aylik_tutar": 100,
        "toplam_borc": borc,
        "durum_etiket": durum,
        "hizmet_turu": "Sanal",
        "liste_ara_blob": "",
        "grup2": grup2,
        "hazir_ofis_oda_no": None,
    }


FIKSTUR = [
    _satir(1, 10.0),           # borçlu
    _satir(2, 0.01),           # borçlu (0.01 > 0.005)
    _satir(3, 0.004),          # gizli
    _satir(4, 0.0),            # gizli
    _satir(5, -5.0),           # alacaklı / gizli
    _satir(6, 0.005),          # sınır: > değil, gizli
    _satir(7, 1.0, "Pasif"),   # borçlu pasif
    _satir(8, 2.0, "Aktif", "Ofis"),
]


def test_python_filtre():
    from routes.faturalar_routes import _firma_ozet_client_filtre_uygula

    def suz(**kw):
        base = dict(q="", grup2=[], hizmet=[], durum="", oda=None, yillar=None, sadece_borclu=False)
        base.update(kw)
        return _firma_ozet_client_filtre_uygula(FIKSTUR, **base)

    hepsi = suz()
    check("kapali tumu", [r["musteri_id"] for r in hepsi] == [1, 2, 3, 4, 5, 6, 7, 8])

    borc = suz(sadece_borclu=True)
    check("acik borclu", [r["musteri_id"] for r in borc] == [1, 2, 7, 8], [r["musteri_id"] for r in borc])
    check("0.004 gizli", all(r["musteri_id"] != 3 for r in borc))
    check("0 ve negatif gizli", all(r["musteri_id"] not in (4, 5, 6) for r in borc))
    check("0.01 gorunur", any(r["musteri_id"] == 2 for r in borc))

    and_pasif = suz(sadece_borclu=True, durum="pasif")
    check("and durum pasif", [r["musteri_id"] for r in and_pasif] == [7])

    and_g2 = suz(sadece_borclu=True, grup2=["Ofis"])
    check("and grup2", [r["musteri_id"] for r in and_g2] == [8])

    check("kapali geri", [r["musteri_id"] for r in suz(sadece_borclu=False)] == [1, 2, 3, 4, 5, 6, 7, 8])


def test_html_ve_js():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("checkbox id", 'id="fatura_rapor_sadece_borclu"' in html and "Sadece borçlular" in html)
    i_bh = html.find('id="fatura_rapor_bizim_hesap"')
    i_sb = html.find('id="fatura_rapor_sadece_borclu"')
    check("bizim saginda", i_bh > 0 and i_sb > i_bh and (i_sb - i_bh) < 600)

    a = html.find("function faturaRaporListeAraUygula")
    b = html.find("function faturaRaporListeAraTemizle", a)
    if b < 0:
        b = html.find("\nfunction ", a + 10)
    fn = html[a:b]
    check("liste borc kosulu", "fatura_rapor_sadece_borclu" in fn and "data-fr-borc-n" in fn and "0.005" in fn)
    check("liste display AND", "tr.style.display = ok ? '' : 'none'" in fn)

    a = html.find("function faturaRaporExcelAktar")
    b = html.find("\nfunction ", a + 10)
    check("excel param", "sadece_borclu=1" in html[a:b])

    a = html.find("initFaturaRaporSadeceBorcluGiris")
    check("init istemci", a > 0 and "faturaRaporListeAraUygula" in html[a : a + 450] and "faturaRaporYukle" not in html[a : a + 450])

    src = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    check("py param", "sadece_borclu" in src and "client_sadece_borclu" in src)


def test_node_liste_ara():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    a = html.find("function faturaRaporListeAraUygula")
    b = html.find("function faturaRaporListeAraTemizle", a)
    if b < 0:
        b = html.find("\nfunction ", a + 10)
    fn = html[a:b]
    # stub dependents that ListeAra calls at end
    js = r"""
global.document = null;
global.window = global;
function faturaRaporFirmaGrup2SatirUygun() { return true; }
function faturaRaporHizmetFiltreSatirUygun() { return true; }
function faturaRaporFirmaFiltreGrup2DiziOku() { return []; }
function faturaRaporFirmaFiltreHizmetDiziOku() { return []; }
function faturaRaporGirisAylarOku() { return []; }
function faturaRaporGirisYillarOku() { return null; }
function faturaRaporOdaFiltreOku() { return null; }
function faturaRaporFirmaListeHepsiniSecStateGuncelle() {}
function faturaRaporFirmaUpdateThFilterMarkers() {}
function faturaRaporFiltreBosSatirGuncelle() {}
function faturaRaporFirmaUstDurumGuncelle() {}
function faturaRaporFirmaSayfalamaUI() {}
function faturaRaporFiltreAktifRozetGuncelle() {}
function faturaRaporFirmaOzetAltGuncelle() {}
""" + fn + r"""
function mkTr(borc, display) {
  return {
    style: { display: display || '' },
    getAttribute: function (k) {
      if (k === 'data-fr-borc-n') return String(borc);
      return '';
    },
    querySelectorAll: function () { return []; }
  };
}
var rows = [mkTr(10), mkTr(0.01), mkTr(0.004), mkTr(0), mkTr(-1), mkTr(0.005)];
var sb = { checked: false };
var gor = { value: 'firma_ozet' };
var tablo = { dataset: { faturaRaporFirma: '1' } };
var tbody = {
  querySelector: function () { return null; },
  querySelectorAll: function () { return rows; }
};
global.document = {
  getElementById: function (id) {
    if (id === 'fatura_rapor_tablo') return tablo;
    if (id === 'fatura_rapor_tbody') return tbody;
    if (id === 'fatura_rapor_liste_ara') return { value: '' };
    if (id === 'fatura_rapor_gorunum') return gor;
    if (id === 'fatura_rapor_sadece_borclu') return sb;
    return null;
  }
};
function say(ad, ok, ek) {
  console.log((ok ? 'ok ' : 'FAIL ') + ad + (ok ? '' : (' :: ' + ek)));
  if (!ok) process.exitCode = 1;
}
faturaRaporListeAraUygula();
say('kapali hepsi gorunur', rows.every(function (r) { return r.style.display === ''; }));
sb.checked = true;
faturaRaporListeAraUygula();
say('acik 10 ve 0.01', rows[0].style.display === '' && rows[1].style.display === '');
say('acik 0.004/0/neg/0.005 gizli',
  rows[2].style.display === 'none' && rows[3].style.display === 'none'
  && rows[4].style.display === 'none' && rows[5].style.display === 'none');
sb.checked = false;
faturaRaporListeAraUygula();
say('kapaninca geri', rows.every(function (r) { return r.style.display === ''; }));
"""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(js)
        path = f.name
    try:
        r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        sys.stdout.write(r.stdout or "")
        if r.stderr:
            sys.stdout.write(r.stderr)
        check("node exit", r.returncode == 0, r.stderr or r.stdout)
        for line in (r.stdout or "").splitlines():
            if line.startswith("FAIL "):
                FAILS.append(line[5:].split(" ::", 1)[0])
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    test_python_filtre()
    test_html_ve_js()
    test_node_liste_ara()
    if FAILS:
        print("FAILS", FAILS)
        raise SystemExit(1)
    print("PASS")
