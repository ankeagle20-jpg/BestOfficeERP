# -*- coding: utf-8 -*-
"""Borçlandırma paneli hibrit yılı kayıtlı net/banka ile gösterir. Ağ yok."""
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
    "girisAylikSatirYilDbGovde",
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
setEl('sozlesme_baslangic', '2022-03-01');
setEl('aylik_kira', '12500');
setEl('kdv_oran', '20');
setEl('kira_nakit', '', true);
setEl('kira_banka', '', true);
setEl('kira_nakit_tutar', '2500');
setEl('kira_banka_tutar', '10000');
window.__musteriReelDonemDbMap = { '2025': 75250, '2026': 109600 };
window.__musteriReelDonemDetaylari = {
  '2025': { tip: 'dahil', hibrit_net: null, hibrit_banka: null, hibrit_toplam: null },
  '2026': { tip: 'hibrit', hibrit_net: 52000, hibrit_banka: 48000, hibrit_toplam: 100000 }
};
mem[girisAylikSatirYilKayitKey()] = JSON.stringify({
  _ts: Date.now(),
  data: {
    '2025': { nakit: 2500, banka: 0, aylik: 0, reel: 75250, reel_aktif: true },
    '2026': { nakit: 2500, banka: 0, aylik: 0, reel: 109600, reel_aktif: true }
  }
});
girisAylikSatirYilKayitYukle();
var y2026 = window.__aylikSatirYilDetay['2026'];
var y2025 = window.__aylikSatirYilDetay['2025'];
var h2026 = girisAylikSatirYilHesapla(2026);
var h2025 = girisAylikSatirYilHesapla(2025);
var govde = girisAylikSatirYilDbGovde(2026, 109600).govde;
setEl('sozlesmeler-reel-tutar-tip', 'hibrit');
setEl('sozlesmeler-reel-hibrit-net', '52000');
setEl('sozlesmeler-reel-hibrit-banka', '40000');
setEl('sozlesmeler-reel-hibrit-kdv', '');
setEl('sozlesmeler-reel-hibrit-banka-dahil', '');
setEl('sozlesmeler-reel-hibrit-odeme', '');
sozlesmelerReelHibritKdvGuncelle();
var out = {
  yuk2026nakit: y2026 && y2026.nakit,
  yuk2026banka: y2026 && y2026.banka,
  yuk2025nakit: y2025 && y2025.nakit,
  h2026nakit: h2026.nakit,
  h2026banka: h2026.banka,
  h2026toplam: h2026.toplam,
  h2026kdv: h2026.kdv_dahil,
  h2025nakit: h2025.nakit,
  govde: govde,
  bankaDahil: els['sozlesmeler-reel-hibrit-banka-dahil'].value,
  odeme: els['sozlesmeler-reel-hibrit-odeme'].value,
  onay: sozlesmelerReelHibritKayitOnayMetni(100000)
};
girisAylikSatirYilDbKaydetTek(2026).then(function (j) {
  out.atlandi = !!(j && j.atlandi);
  out.posts0 = posts.length;
  window.__aylikSatirYilDetay['2026'].__kullanici_degistirdi = true;
  window.__aylikSatirYilDetay['2026'].nakit = 50000;
  window.__aylikSatirYilDetay['2026'].banka = 50000;
  return girisAylikSatirYilDbKaydetTek(2026);
}).then(function () {
  out.posts1 = posts.length;
  out.son = posts[0] || null;
  window.__musteriReelDonemDbMap['2026'] = 100000;
  window.__musteriReelDonemDetaylari['2026'] = { tip: 'hibrit', hibrit_net: 52000, hibrit_banka: 40000, hibrit_toplam: 92000 };
  window.__aylikSatirYilDetay['2026'] = { nakit: 2500, banka: 0, reel: 100000, __reel_aktif: true };
  var hFix = girisAylikSatirYilHesapla(2026);
  out.fixNakit = hFix.nakit;
  out.fixBanka = hFix.banka;
  out.fixKdv = hFix.kdv_dahil;
  out.fixToplam = hFix.toplam;
  console.log(JSON.stringify(out));
}).catch(function (e) {
  out.hata = String(e && e.stack || e);
  console.log(JSON.stringify(out));
  process.exitCode = 1;
});
"""
    js_path.write_text(harness, encoding="utf-8", newline="\n")
    try:
        chk = subprocess.run(["node", "--check", str(js_path)], capture_output=True, text=True, encoding="utf-8")
        check("node --check", chk.returncode == 0, chk.stderr)
        run = subprocess.run(["node", str(js_path)], capture_output=True, text=True, encoding="utf-8")
        check("node calisti", run.returncode == 0, run.stderr or run.stdout)
        data = json.loads(run.stdout.strip().splitlines()[-1]) if run.stdout.strip() else {}
        check("2026 nakit 52000", abs(float(data.get("h2026nakit") or 0) - 52000) < 0.02, data)
        check("2026 banka 48000", abs(float(data.get("h2026banka") or 0) - 48000) < 0.02, data)
        check("2026 toplam 109600", abs(float(data.get("h2026toplam") or 0) - 109600) < 0.02, data)
        check("yukleme kart nakitini ezdi", abs(float(data.get("yuk2026nakit") or 0) - 52000) < 0.02, data)
        check("2025 dahil kart nakiti", abs(float(data.get("yuk2025nakit") or 0) - 2500) < 0.02, data)
        check("2025 hesap kart nakiti", abs(float(data.get("h2025nakit") or 0) - 2500) < 0.02, data)
        g = data.get("govde") or {}
        check("govde hibrit", g.get("giris_tip") == "hibrit" and g.get("hibrit_net") == 52000 and g.get("hibrit_banka") == 48000, g)
        check("degismeyen kaydet atlandi", data.get("atlandi") is True and data.get("posts0") == 0, data)
        son = data.get("son") or {}
        check(
            "degisen kaydet hibrit gonderir",
            data.get("posts1") == 1 and son.get("giris_tip") == "hibrit" and son.get("hibrit_net") == 50000,
            son,
        )
        check("dogru nakit 52000", abs(float(data.get("fixNakit") or 0) - 52000) < 0.02, data)
        check("dogru banka 40000", abs(float(data.get("fixBanka") or 0) - 40000) < 0.02, data)
        check("dogru kdv dahil 48000", abs(float(data.get("fixKdv") or 0) - 48000) < 0.02, data)
        check("dogru odeme 100000", abs(float(data.get("fixToplam") or 0) - 100000) < 0.02, data)
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
