# -*- coding: utf-8 -*-
"""Ilk yil borclandirma satiri: karttan gosterim, reel API yok. Ag yok."""
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
    "girisAylikSatirYilKayitKey",
    "girisAylikSatirYilKayitYaz",
    "girisAylikSatirYilDomOku",
    "girisAylikSatirYilKaydetTutarAl",
    "girisAylikSatirYilDbKaydetTek",
    "girisAylikSatirYilDbSilTek",
    "girisAylikSatirYilKaydet",
    "girisAylikSatirYilTumunuKaydet",
]


def main():
    html = HTML.read_text(encoding="utf-8")
    check("lf", "\r\n" not in html)
    check("bilgi metni", html.count("İlk yıl sözleşme tutarından") >= 2)
    check("ilk yil kayitli rozeti yok", "var kayitli = ilkYilSatir ? false" in html)
    check("reel sil ilk yilda yok", "!ilkYilSatir && kayitli && reelG > 0" in html)
    routes = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("backend ilk yil reddi duruyor", "İlk sözleşme yılı için reel tutar girilemez." in routes)
    check("backend grid ortmesi duruyor", "int(k) != y_start" in routes)
    blok = "\n".join(_fn(html, n) for n in NAMES)
    harness = r"""
const calls = [];
const store = {};
global.localStorage = {
  setItem(k, v) { store[k] = String(v); },
  getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
  removeItem(k) { delete store[k]; }
};
global.selectedId = 867;
global.window = global;
const els = {};
global.document = { getElementById(id) { return els[id] || null; } };
function setEl(id, value, checked) {
  els[id] = { id: id, value: value == null ? '' : String(value), checked: !!checked };
}
global.girisFetchAgKopmasinaKarsi = function (url, opt) {
  calls.push({ url: String(url), method: (opt && opt.method) || 'GET', body: opt && opt.body });
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve({ ok: true }); } });
};
function girisAylikSatirYilPanelYilListesi() { return global.__yillar.slice(); }
function girisAylikSatirYilDbSonrasi() { return Promise.resolve(); }
""" + blok + r"""
function kartKur(opts) {
  setEl('sozlesme_baslangic', opts.bas || '2023-08-01');
  setEl('aylik_kira', opts.aylik);
  setEl('kdv_oran', opts.kdv == null ? '20' : String(opts.kdv));
  setEl('kira_nakit', '', !!opts.nakit);
  setEl('kira_banka', '', !!opts.banka);
  setEl('kira_nakit_tutar', opts.nakitTutar == null ? '' : String(opts.nakitTutar));
  setEl('kira_banka_tutar', opts.bankaTutar == null ? '' : String(opts.bankaTutar));
  window.__aylikSatirYilDetay = {};
}
function yilKur(y, row) {
  window.__aylikSatirYilDetay[String(y)] = Object.assign({
    aylik: 0, nakit: 0, reel: 0, __reel_aktif: false, __manual_saved: false
  }, row || {});
}
const out = [];
function satir(ad, h, bek) {
  const alan = ['aylik', 'kdv_dahil', 'toplam', 'nakit', 'banka', 'reel'];
  let ok = h.__manual_saved === false && h.__reel_aktif === false && h.__ilk_yil_kart === true;
  for (let i = 0; i < alan.length; i++) {
    if (Math.abs((h[alan[i]] || 0) - bek[i]) > 0.001) ok = false;
  }
  out.push({ ad: ad, ok: ok, h: { aylik: h.aylik, kdv: h.kdv_dahil, toplam: h.toplam, nakit: h.nakit, banka: h.banka, reel: h.reel, kayit: h.__manual_saved } });
}

kartKur({ aylik: 600, banka: true, bankaTutar: 600, bas: '2023-08-01' });
yilKur(2023, { reel: 300, __reel_aktif: true, __manual_saved: true });
satir('banka reel var kismi', girisAylikSatirYilHesapla(2023), [600, 720, 720, 0, 720, 720]);

kartKur({ aylik: 600, banka: true, bankaTutar: 600, bas: '2023-01-01' });
yilKur(2023, { reel: 300, __reel_aktif: true, __manual_saved: true });
satir('banka reel var ocak', girisAylikSatirYilHesapla(2023), [600, 720, 720, 0, 720, 720]);

kartKur({ aylik: 600, banka: true, bankaTutar: 600, bas: '2023-08-01' });
yilKur(2023, {});
satir('banka reel yok', girisAylikSatirYilHesapla(2023), [600, 720, 720, 0, 720, 720]);

kartKur({ aylik: 500, nakit: true, nakitTutar: 500, bas: '2024-03-01' });
yilKur(2024, { reel: 900, __reel_aktif: true, __manual_saved: true });
satir('nakit', girisAylikSatirYilHesapla(2024), [500, 0, 500, 500, 0, 500]);

kartKur({ aylik: 600, nakit: true, banka: true, nakitTutar: 200, bankaTutar: 400, bas: '2022-06-01' });
yilKur(2022, { reel: 50, __reel_aktif: true, __manual_saved: true });
satir('karma', girisAylikSatirYilHesapla(2022), [600, 480, 680, 200, 480, 680]);

kartKur({ aylik: 600, banka: true, bankaTutar: 600, kdv: 0, bas: '2023-08-01' });
yilKur(2023, { reel: 300, __reel_aktif: true, __manual_saved: true });
satir('kdv sifir', girisAylikSatirYilHesapla(2023), [600, 600, 600, 0, 600, 600]);

kartKur({ aylik: 600, banka: true, bankaTutar: 600, bas: '2023-08-01' });
yilKur(2024, { reel: 1094.18, nakit: 0, __reel_aktif: true, __manual_saved: true });
const h2 = girisAylikSatirYilHesapla(2024);
out.push({
  ad: 'ikinci yil reel duruyor',
  ok: Math.abs(h2.aylik - 911.82) < 0.001 && Math.abs(h2.kdv_dahil - 1094.18) < 0.001
    && Math.abs(h2.toplam - 1094.18) < 0.001 && Math.abs(h2.banka - 1094.18) < 0.001
    && h2.nakit === 0 && h2.__manual_saved === true && h2.__reel_aktif === true
    && Math.abs(h2.reel - 1094.18) < 0.001,
  h: { aylik: h2.aylik, kdv: h2.kdv_dahil, toplam: h2.toplam, banka: h2.banka, reel: h2.reel, kayit: h2.__manual_saved }
});

kartKur({ aylik: 600, banka: true, bankaTutar: 600, bas: '2023-08-01' });
window.__musteriReelDonemDbMap = { '2023': 300, '2024': 1094.18 };
out.push({ ad: 'goster kart', ok: girisAylikSatirYilReelGoster(2023) === 720 && girisAylikSatirYilReelGoster(2024) === 1094.18 });

global.__yillar = [2023, 2024];
yilKur(2023, { reel: 300, __reel_aktif: true, __manual_saved: true });
yilKur(2024, { reel: 1094.18, nakit: 0, __reel_aktif: true, __manual_saved: true });
calls.length = 0;
girisAylikSatirYilTumunuKaydet(null);
girisAylikSatirYilKaydet(2023);
girisAylikSatirYilDbKaydetTek(2023);
girisAylikSatirYilDbSilTek(2023);
setTimeout(function () {
  const ilk = calls.filter(function (c) { return String(c.body || '').indexOf('"donem_yil":2023') >= 0 || String(c.body || '').indexOf('"donem_yil": 2023') >= 0; });
  const ikinci = calls.filter(function (c) { return String(c.url).indexOf('/giris/api/reel-donem-tutar') >= 0 && String(c.body || '').indexOf('2024') >= 0; });
  out.push({ ad: 'tumunu ilk yil istek yok', ok: ilk.length === 0 && ikinci.length === 1 && calls.length === 1 });
  console.log(JSON.stringify(out));
}, 30);
"""
    js = ROOT / "_tmp_ilk_yil_satir_test.js"
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
