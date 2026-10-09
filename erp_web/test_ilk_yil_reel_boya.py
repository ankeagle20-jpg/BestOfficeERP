# -*- coding: utf-8 -*-
"""Ilk yil reel tutari sayfada boyanmaz; tahsil/kalan kayitli degerde kalir. Ag yok."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "templates" / "giris" / "index.html"
FAILS = []


def check(name, ok):
    print(("ok " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def _fn(src, name):
    key = "function " + name + "("
    i = src.find(key)
    if i < 0:
        raise SystemExit("yok " + name)
    j = src.find("{", i)
    depth = 0
    k = j
    while k < len(src):
        c = src[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
        k += 1
    raise SystemExit("kapanmadi " + name)


NAMES = [
    "kiraNakitMi",
    "girisKiraKarmaPaylariOkuma",
    "parseTarihStr",
    "girisTrSayiParse",
    "girisAylikSatirAnaDegerOku",
    "girisAylikSatirIlkYilMi",
    "girisAylikSatirIlkYilKartHesap",
    "girisAylikSatirBaslangicYili",
    "girisAylikSatirYilHesapla",
    "girisAylikSatirYilReelGoster",
    "sozlesmeAylikAyKeyNormalize",
    "sozlesmeAylikAyKeyFromYilAy",
    "sozlesmelerReelDonemAnchor",
    "sozlesmeReelDonemAyKeys",
    "sozlesmelerReelDbBaslangicYili",
    "sozlesmelerReelDbIlkDonemYilMi",
    "sozlesmelerReelDbAyIlkDonemMi",
    "sozlesmelerReelDbAyKeyFlatMap",
    "sozlesmelerReelDbTutarAyKey",
    "sozlesmelerAylikCacheReelDbEsasla",
    "sozlesmelerReelTahsilPanelSenkron",
    "girisTahsilatYilAyDetayMap",
    "girisTahsilatAyKeyToYilAy",
    "sozlesmelerAylikKartDonemYili",
    "girisAylikSatirYilAyMid",
    "girisAylikSatirYilAyDetayMap",
    "girisAylikSatirYilAyVarsayilan",
    "girisAylikSatirYilAyHesapla",
    "girisTahsilatYilAyKiraTutar",
    "girisTahsilatYilAyPanelAylikCanon",
    "girisTahsilatYilAyPanelSatirNormalize",
    "girisPlanGridHucreBrut",
    "girisPlanYilToplam",
    "girisTahsilatYilAylikTek",
    "girisTahsilatYilBorcTek",
    "_sozlesmelerReelDonemAyKeysTam",
    "_sozlesmelerReelYilSecimiGridAnlikUygula",
    "sozlesmelerReelKayitliTumYillariGridaUygula",
]


def main():
    html = HTML.read_text(encoding="utf-8")
    check("lf", "\r\n" not in html)
    blok = "\n".join(_fn(html, n) for n in NAMES)
    harness = r"""
var SOZLESME_TAM_ODENDI_TOLERANS = 0.05;
var selectedId = 867;
const els = {};
global.document = { getElementById(id) { return els[id] || null; } };
global.window = global;
function setEl(id, value, checked) {
  els[id] = { id: id, value: value == null ? '' : String(value), checked: !!checked };
}
function _sozlesmeAyKartBul(grid, ayKey) {
  if (!grid || !grid.querySelector) return null;
  return grid.querySelector('[data-ay-key="' + ayKey + '"]');
}
function _sozlesmelerGridTakvimYilAyKeys() { return []; }
""" + blok + r"""
function near(a, b) { return Math.abs((parseFloat(a) || 0) - (parseFloat(b) || 0)) <= 0.02; }
function kartKur(bas) {
  setEl('sozlesme_baslangic', bas || '2023-08-01');
  setEl('aylik_kira', '600');
  setEl('kdv_oran', '20');
  setEl('kira_nakit', '', false);
  setEl('kira_banka', '', true);
  setEl('kira_nakit_tutar', '');
  setEl('kira_banka_tutar', '600');
  window.__aylikSatirYilDetay = {};
  window.__tahsilatSatirYilAyDetay = {};
  window.__reelDbAyKeyFlatSig = null;
  window.__reelDbAyKeyFlatMap = null;
  window.__musteriReelDonemTutarlari = {};
}
function flatKur(bas, map) {
  kartKur(bas);
  window.__musteriReelDonemTutarlari = map;
  return sozlesmelerReelDbAyKeyFlatMap(bas);
}
function ayBul(cache, y, m) {
  for (var i = 0; i < cache.aylar.length; i++) {
    if (cache.aylar[i].yil === y && cache.aylar[i].ay === m) return cache.aylar[i];
  }
  return null;
}
function satir(yil, ak, row) {
  var map = girisTahsilatYilAyDetayMap(yil);
  map[ak] = row;
}
function gridKur(pairs) {
  var cards = pairs.map(function (p) {
    var attrs = { 'data-ay-key': p[0], 'data-yil': String(p[1]), 'data-ay': String(p[2]), 'data-panel-esas': '0' };
    var deg = { textContent: p[3] };
    return {
      getAttribute: function (n) { return attrs[n] || ''; },
      setAttribute: function (n, v) { attrs[n] = String(v); },
      querySelector: function (sel) { return sel === '.aylik-deger' ? deg : null; },
      deg: deg
    };
  });
  return {
    querySelector: function (sel) {
      var m = /data-ay-key="([^"]+)"/.exec(sel || '');
      if (!m) return null;
      for (var i = 0; i < cards.length; i++) {
        if (cards[i].getAttribute('data-ay-key') === m[1]) return cards[i];
      }
      return null;
    },
    cards: cards
  };
}
const out = [];
function kayit(ad, ok, extra) { out.push({ ad: ad, ok: !!ok, h: extra || null }); }

kartKur('2023-08-01');
window.__aylikSatirYilDetay['2024'] = { reel: 1094.18, __reel_aktif: true, __manual_saved: true, nakit: 0 };
kayit('867 ana satir', near(girisTahsilatYilAylikTek(2023), 720) && near(girisTahsilatYilBorcTek(2023), 8640)
  && near(girisTahsilatYilAylikTek(2024), 1094.18) && near(girisTahsilatYilBorcTek(2024), 13130.16));

var flat867 = flatKur('2023-08-01', { '2023': 300, '2024': 1094.18 });
var ilkYok = ['2023-8','2023-9','2023-10','2023-11','2023-12','2024-1','2024-2','2024-3','2024-4','2024-5','2024-6','2024-7'].every(function (k) {
  return flat867[k] == null && sozlesmelerReelDbTutarAyKey('2023-08-01', k) == null;
});
kayit('867 ilk donem haritada yok', ilkYok && near(flat867['2024-8'], 1094.18) && near(flat867['2024-12'], 1094.18) && near(flat867['2025-7'], 1094.18));

var cache = {
  baslangic: '2023-08-01',
  aylar: [
    { yil: 2023, ay: 8, ay_key: '2023-8', brut_tutar_kdv: 720, odenen_tutar_kdv: 720, kalan_tutar_kdv: 0, tahsil_edildi: true },
    { yil: 2023, ay: 10, ay_key: '2023-10', brut_tutar_kdv: 720, odenen_tutar_kdv: 200, kalan_tutar_kdv: 520, kismi_tahsilat: true },
    { yil: 2024, ay: 1, ay_key: '2024-1', brut_tutar_kdv: 720, odenen_tutar_kdv: 720, kalan_tutar_kdv: 0, tahsil_edildi: true },
    { yil: 2024, ay: 7, ay_key: '2024-7', brut_tutar_kdv: 720, odenen_tutar_kdv: 720, kalan_tutar_kdv: 0, tahsil_edildi: true },
    { yil: 2024, ay: 8, ay_key: '2024-8', brut_tutar_kdv: 1094.18, odenen_tutar_kdv: 1094.18, kalan_tutar_kdv: 0, tahsil_edildi: true },
    { yil: 2024, ay: 10, ay_key: '2024-10', brut_tutar_kdv: 1094.18, odenen_tutar_kdv: 711.64, kalan_tutar_kdv: 382.54, kismi_tahsilat: true }
  ]
};
window.__musteriReelDonemTutarlari = { '2023': 300, '2024': 1094.18 };
window.__reelDbAyKeyFlatSig = null;
sozlesmelerAylikCacheReelDbEsasla(cache);
var a8 = ayBul(cache, 2023, 8);
var a10 = ayBul(cache, 2023, 10);
var a1 = ayBul(cache, 2024, 1);
var a7 = ayBul(cache, 2024, 7);
var a88 = ayBul(cache, 2024, 8);
var a1010 = ayBul(cache, 2024, 10);
kayit('867 grid esasla ilk donem', near(a8.brut_tutar_kdv, 720) && near(a8.odenen_tutar_kdv, 720) && near(a8.kalan_tutar_kdv, 0)
  && near(a10.brut_tutar_kdv, 720) && near(a10.odenen_tutar_kdv, 200) && near(a10.kalan_tutar_kdv, 520)
  && near(a1.brut_tutar_kdv, 720) && near(a7.brut_tutar_kdv, 720)
  && near(a88.brut_tutar_kdv, 1094.18) && near(a1010.odenen_tutar_kdv, 711.64) && near(a1010.kalan_tutar_kdv, 382.54));

satir(2023, '2023-8', { aylik_tutar: 720, tahsil: 720, kalan: 0, __reel_db_aylik: 300, __saved: true });
satir(2023, '2023-10', { aylik_tutar: 300, tahsil: 200, kalan: 520, __reel_db_aylik: 300, __saved: true });
satir(2024, '2024-8', { aylik_tutar: 1000, tahsil: 1000, kalan: 0, __saved: true });
satir(2024, '2024-10', { aylik_tutar: 1094.18, tahsil: 711.64, kalan: 382.54, __saved: true });
sozlesmelerReelTahsilPanelSenkron();
var p8 = girisTahsilatYilAyDetayMap(2023)['2023-8'];
var p10 = girisTahsilatYilAyDetayMap(2023)['2023-10'];
var p88 = girisTahsilatYilAyDetayMap(2024)['2024-8'];
var p1010 = girisTahsilatYilAyDetayMap(2024)['2024-10'];
kayit('867 senkron ilk yil dokunmaz', near(p8.aylik_tutar, 720) && near(p8.tahsil, 720) && near(p8.kalan, 0)
  && near(p10.tahsil, 200) && near(p10.kalan, 520));
kayit('867 senkron sonraki yil duruyor', near(p88.aylik_tutar, 1094.18) && near(p88.tahsil, 1094.18) && near(p88.kalan, 0)
  && near(p1010.tahsil, 711.64) && near(p1010.kalan, 382.54) && near(p1010.aylik_tutar, 1094.18));

girisTahsilatYilAyPanelSatirNormalize(p10, 2023, '2023-10');
girisTahsilatYilAyPanelSatirNormalize(p8, 2023, '2023-8');
kayit('867 panel aylik kart tahsil kalan ayni', near(p10.aylik_tutar, 720) && near(p10.tahsil, 200) && near(p10.kalan, 520)
  && p10.__reel_db_aylik == null && near(p8.aylik_tutar, 720) && near(p8.tahsil, 720) && near(p8.kalan, 0));

var g = gridKur([['2023-8', 2023, 8, '720.00'], ['2024-1', 2024, 1, '720.00'], ['2024-8', 2024, 8, '999.00']]);
els['sozlesmeler-aylik-grid'] = g;
els['sozlesmeler-reel-aylik-grid'] = g;
sozlesmelerReelKayitliTumYillariGridaUygula();
kayit('867 grid boya', g.cards[0].deg.textContent === '720.00' && g.cards[1].deg.textContent === '720.00' && g.cards[2].deg.textContent === '1094.18');

var flatEsit = flatKur('2023-08-01', { '2023': 720, '2024': 1094.18 });
kayit('reel karta esit yine atlanir', flatEsit['2023-8'] == null && flatEsit['2024-7'] == null && near(flatEsit['2024-8'], 1094.18));

var flatNet = flatKur('2023-08-01', { '2023': 600, '2024': 900 });
var cacheNet = { baslangic: '2023-08-01', aylar: [
  { yil: 2023, ay: 9, ay_key: '2023-9', brut_tutar_kdv: 720, odenen_tutar_kdv: 100, kalan_tutar_kdv: 620, kismi_tahsilat: true }
]};
sozlesmelerAylikCacheReelDbEsasla(cacheNet);
var n9 = ayBul(cacheNet, 2023, 9);
satir(2023, '2023-9', { aylik_tutar: 600, tahsil: 100, kalan: 620, __saved: true });
var pn = girisTahsilatYilAyDetayMap(2023)['2023-9'];
sozlesmelerReelTahsilPanelSenkron();
girisTahsilatYilAyPanelSatirNormalize(pn, 2023, '2023-9');
kayit('net reel sinifi kart kalir', flatNet['2023-9'] == null && near(n9.brut_tutar_kdv, 720) && near(n9.odenen_tutar_kdv, 100) && near(n9.kalan_tutar_kdv, 620)
  && near(pn.aylik_tutar, 720) && near(pn.tahsil, 100) && near(pn.kalan, 620));

var flatOcak = flatKur('2024-01-01', { '2024': 500, '2025': 880 });
kayit('ocak baslangic ilk yil atlanir', near(girisTahsilatYilAylikTek(2024), 720) && near(girisTahsilatYilBorcTek(2024), 8640)
  && flatOcak['2024-1'] == null && flatOcak['2024-12'] == null && near(flatOcak['2025-1'], 880) && near(flatOcak['2025-6'], 880));
satir(2024, '2024-3', { aylik_tutar: 500, tahsil: 80, kalan: 420, __reel_db_aylik: 500, __saved: true });
var po = girisTahsilatYilAyDetayMap(2024)['2024-3'];
sozlesmelerReelTahsilPanelSenkron();
girisTahsilatYilAyPanelSatirNormalize(po, 2024, '2024-3');
kayit('ocak kismi tahsil kalan ayni', near(po.aylik_tutar, 720) && near(po.tahsil, 80) && near(po.kalan, 420) && po.__reel_db_aylik == null);

console.log(JSON.stringify(out));
"""
    js = ROOT / "_tmp_ilk_yil_reel_boya.js"
    js.write_text(harness, encoding="utf-8", newline="\n")
    try:
        proc = subprocess.run(
            ["node", str(js)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        js.unlink(missing_ok=True)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
        check("node", False)
        return
    rows = json.loads(proc.stdout.strip().splitlines()[-1])
    for row in rows:
        check(row["ad"], bool(row["ok"]))
        if not row["ok"]:
            print(row.get("h"))


if __name__ == "__main__":
    main()
    if FAILS:
        print("FAIL", len(FAILS))
        sys.exit(1)
    print("ok tumu", "fail yok")
