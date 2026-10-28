# -*- coding: utf-8 -*-
"""Yıl tablosu eski sütun düzeni + kayıtlı hibrit nakiti. Ağ yok."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "templates" / "giris" / "index.html"
FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:400]) if (ek and not ok) else ""))
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
    "parseTarihStr",
    "girisTrSayiParse",
    "kiraNakitMi",
    "girisKiraKarmaPaylariOkuma",
    "girisAylikSatirAnaDegerOku",
    "girisAylikSatirIlkYilMi",
    "girisAylikSatirBaslangicYili",
    "girisAylikSatirYilHibritDetay",
    "girisAylikSatirYilHesapla",
    "girisAylikSatirYilKayitDbdenUygula",
    "girisAylikSatirYilKayitKey",
    "girisAylikSatirYilKayitYukle",
    "girisAylikSatirYilKaydetTutarAl",
    "girisAylikSatirYilDomOku",
    "girisAylikSatirYilDbKaydetTek",
    "_sozlesmelerReelHibritParseEl",
    "_sozlesmelerReelKdvOranOku",
    "sozlesmelerReelHibritKayitOnayMetni",
    "sozlesmelerReelHibritKdvGuncelle",
]


def main():
    html = HTML.read_text(encoding="utf-8")
    check("lf", "\r\n" not in html)
    blok = "\n".join(_fn(html, n) for n in NAMES)
    js_path = ROOT / "_tmp_reel_hibrit_panel.js"
    harness = r"""
var selectedId = 883;
const els = {};
global.document = { getElementById(id) { return els[id] || null; } };
global.window = global;
function setEl(id, value, checked) {
  els[id] = { id: id, value: value == null ? '' : String(value), checked: !!checked, querySelector() { return null; } };
}
var mem = {};
global.localStorage = {
  getItem(k) { return Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null; },
  setItem(k, v) { mem[k] = String(v); },
  removeItem(k) { delete mem[k]; }
};
var posts = [];
function girisFetchAgKopmasinaKarsi(url, opt) {
  posts.push(JSON.parse(opt.body));
  return Promise.resolve({ ok: true, status: 200, json() { return Promise.resolve({ ok: true }); } });
}
function near(a, b) { return Math.abs((parseFloat(a) || 0) - (parseFloat(b) || 0)) <= 0.02; }
""" + blok + r"""
function satir(y, row) {
  window.__aylikSatirYilDetay = window.__aylikSatirYilDetay || {};
  window.__aylikSatirYilDetay[String(y)] = row;
  return girisAylikSatirYilHesapla(y);
}
setEl('sozlesme_baslangic', '2022-03-01');
setEl('aylik_kira', '12500');
setEl('kdv_oran', '20');
setEl('kira_nakit', '', true);
setEl('kira_banka', '', true);
setEl('kira_nakit_tutar', '2500');
setEl('kira_banka_tutar', '10000');
var t1 = satir(2031, { aylik: 10000, nakit: 0 });
var t2 = satir(2032, { aylik: 10000, nakit: 5000 });
var t3 = satir(2033, { aylik: 600, nakit: 0 });
var t4 = satir(2034, { nakit: 5000, reel: 11000, __reel_aktif: true });
window.__musteriReelDonemDbMap = { '2025': 75250, '2026': 100000 };
window.__musteriReelDonemDetaylari = {
  '2025': { tip: 'dahil', hibrit_net: null, hibrit_banka: null, hibrit_toplam: null },
  '2026': { tip: 'hibrit', hibrit_net: 52000, hibrit_banka: 40000, hibrit_toplam: 92000 }
};
mem[girisAylikSatirYilKayitKey()] = JSON.stringify({
  _ts: Date.now(),
  data: {
    '2025': { nakit: 2500, banka: 0, aylik: 0, reel: 75250, reel_aktif: true },
    '2026': { nakit: 2500, banka: 0, aylik: 0, reel: 100000, reel_aktif: true }
  }
});
girisAylikSatirYilKayitYukle();
var h5 = girisAylikSatirYilHesapla(2026);
var h2025 = girisAylikSatirYilHesapla(2025);
setEl('sozlesmeler-reel-tutar-tip', 'hibrit');
setEl('sozlesmeler-reel-hibrit-net', '52000');
setEl('sozlesmeler-reel-hibrit-banka', '40000');
setEl('sozlesmeler-reel-hibrit-kdv', '');
setEl('sozlesmeler-reel-hibrit-banka-dahil', '');
setEl('sozlesmeler-reel-hibrit-odeme', '');
sozlesmelerReelHibritKdvGuncelle();
var out = {
  t1: t1, t2: t2, t3: t3, t4: t4, h5: h5,
  y2025nakit: h2025.nakit,
  bankaDahil: els['sozlesmeler-reel-hibrit-banka-dahil'].value,
  odeme: els['sozlesmeler-reel-hibrit-odeme'].value,
  onay: sozlesmelerReelHibritKayitOnayMetni(100000)
};
console.log(JSON.stringify(out));
"""
    js_path.write_text(harness, encoding="utf-8", newline="\n")
    try:
        chk = subprocess.run(["node", "--check", str(js_path)], capture_output=True, text=True, encoding="utf-8")
        check("node --check", chk.returncode == 0, chk.stderr)
        run = subprocess.run(["node", str(js_path)], capture_output=True, text=True, encoding="utf-8")
        check("node calisti", run.returncode == 0, run.stderr or run.stdout)
        data = json.loads(run.stdout.strip().splitlines()[-1]) if run.stdout.strip() else {}
        t1, t2, t3, t4, h5 = data.get("t1") or {}, data.get("t2") or {}, data.get("t3") or {}, data.get("t4") or {}, data.get("h5") or {}
        check("1 kdv banka toplam 12000", abs(t1.get("kdv_dahil") - 12000) < 0.02 and abs(t1.get("banka") - 12000) < 0.02 and abs(t1.get("toplam") - 12000) < 0.02, t1)
        check("2 banka kdv 6000 toplam 11000", abs(t2.get("banka") - 6000) < 0.02 and abs(t2.get("kdv_dahil") - 6000) < 0.02 and abs(t2.get("toplam") - 11000) < 0.02, t2)
        check("3 kdv banka toplam 720", abs(t3.get("kdv_dahil") - 720) < 0.02 and abs(t3.get("banka") - 720) < 0.02 and abs(t3.get("toplam") - 720) < 0.02, t3)
        reel3 = t3.get("reel") if t3.get("reel") else t3.get("toplam")
        check("3 reel 720", abs(float(reel3 or 0) - 720) < 0.02, t3)
        check("4 reel 11000", abs(t4.get("banka") - 6000) < 0.02 and abs(t4.get("kdv_dahil") - 6000) < 0.02 and abs(t4.get("aylik") - 10000) < 0.02 and abs(t4.get("toplam") - 11000) < 0.02, t4)
        check("5 nakit 52000", abs(h5.get("nakit") - 52000) < 0.02, h5)
        check("5 banka kdv 48000", abs(h5.get("banka") - 48000) < 0.02 and abs(h5.get("kdv_dahil") - 48000) < 0.02, h5)
        check("5 toplam 100000 aylik 92000", abs(h5.get("toplam") - 100000) < 0.02 and abs(h5.get("aylik") - 92000) < 0.02, h5)
        check("2025 dahil kart nakiti", abs(float(data.get("y2025nakit") or 0) - 2500) < 0.02, data)
        baslik = "<div>Yıl</div><div>Aylık</div><div>KDV Dahil</div><div>Toplam</div><div>Nakit</div><div>Banka</div><div>Reel</div>"
        check("6 basliklar", baslik in html and "Aylık (net)</div>" not in html)
        for cls in ("aylik-yil-aylik", "aylik-yil-kdv", "aylik-yil-toplam", "aylik-yil-nakit", "aylik-yil-banka", "aylik-yil-reel", "aylik-yil-reel-aktif"):
            check("6 id/class " + cls, cls in html)
        check("onizleme banka dahil", data.get("bankaDahil") == "48000", data.get("bankaDahil"))
        check("onizleme odeme", data.get("odeme") == "100000", data.get("odeme"))
        check("kayit onay metni", "Müşteri ödemesi" in str(data.get("onay") or "") and "100" in str(data.get("onay")), data.get("onay"))
    finally:
        js_path.unlink(missing_ok=True)
    if FAILS:
        print("FAILS", ",".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
