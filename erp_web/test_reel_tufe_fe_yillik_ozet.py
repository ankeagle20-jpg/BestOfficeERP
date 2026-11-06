# -*- coding: utf-8 -*-
"""Z=1 yıllık özet: API brüt → KDV Dahil/Aylık/Toplam/Banka (form TÜFE zinciri değil).

Gerçek templates/giris/index.html fonksiyonları (node + Playwright gecikmeli sıra).
Prod yazmaz; sentetik mid/tutar; PII yok. Commit yok.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "templates" / "giris" / "index.html"
HEAD = "bbebc17"
FAILS: list[str] = []

API_2025 = 514.0
API_2026 = 680.38
API_2027 = 900.62
NET_2026 = round(API_2026 / 1.2, 2)  # 566.98
SYN_MID = 900379
KDV = 20.0


def check(name: str, ok: bool, ek="") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def near(a, b, tol=0.05) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def _fn(src: str, name: str) -> str:
    key = "function " + name + "("
    i = src.find(key)
    if i < 0:
        raise SystemExit("yok " + name)
    j = src.find("{", i)
    depth = 0
    k = j
    in_s = None
    esc = False
    while k < len(src):
        c = src[k]
        if in_s:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == in_s:
                in_s = None
        else:
            if c in ("'", '"', "`"):
                in_s = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return src[i : k + 1]
        k += 1
    raise SystemExit("kapanmadi " + name)


def _fn_from_commit(commit: str, name: str) -> str:
    r = subprocess.run(
        ["git", "show", f"{commit}:erp_web/templates/giris/index.html"],
        cwd=str(ROOT.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    if r.returncode != 0:
        raise SystemExit("git show fail " + commit)
    return _fn(r.stdout, name)


FE_NAMES = [
    "girisReelTufeZincirStickyBag",
    "girisReelTufeZincirStickyMidCoz",
    "girisReelTufeZincirCacheStickyUygula",
    "girisReelTufeZincirAktifMi",
    "girisReelTufeZincirApiZ",
    "sozlesmelerAylikKartDonemYili",
    "sozlesmeReelDonemAyKeys",
    "sozlesmelerReelDonemAnchor",
    "girisAylikSatirYilApiDonemBrutAl",
    "girisAylikSatirYilDonemApiEzilebilirMi",
    "girisAylikSatirYilHesapla",
    "girisTahsilatYilAylikTek",
]


def _harness_prelude() -> str:
    return r"""
var SOZLESME_TAM_ODENDI_TOLERANS = 0.05;
var selectedId = 900379;
const els = {};
function _el(id, val) {
  els[id] = {
    id: id,
    value: val == null ? '' : String(val),
    checked: false,
    style: {},
    getAttribute: function () { return null; },
    setAttribute: function () {},
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
  };
  return els[id];
}
global.document = {
  getElementById: function (id) { return els[id] || null; },
  createElement: function () { return { style: {}, appendChild: function(){} }; },
  addEventListener: function () {},
};
global.window = global;
global.localStorage = (function () {
  var store = {};
  return {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem: function (k, v) { store[k] = String(v); },
    removeItem: function (k) { delete store[k]; },
  };
})();
function parseTarihStr(s) {
  if (!s) return null;
  var m = String(s).match(/(\d{4})-(\d{1,2})-(\d{1,2})/);
  if (!m) return null;
  return new Date(parseInt(m[1],10), parseInt(m[2],10)-1, parseInt(m[3],10));
}
function sozlesmelerReelDonemAnchor(basStr) {
  var d = parseTarihStr(basStr) || new Date(basStr);
  if (!d || isNaN(d.getTime())) return { month: 4, date: 1 };
  return { month: d.getMonth(), date: d.getDate() };
}
function girisAylikSatirBaslangicYili() { return 2023; }
function girisAylikSatirIlkYilMi(y) { return parseInt(y,10) === 2023; }
function girisAylikSatirIlkYilKartHesap() {
  return { aylik: 150, nakit: 0, banka: 180, kdv_dahil: 180, toplam: 180, reel: 180 };
}
function girisAylikSatirYilReelGoster() { return 0; }
function girisAylikSatirYilReelDonemDetay() { return null; }
function girisAylikSatirYilMapKdvDahil() { return 0; }
function girisPlanYilGecerliAylik() { return null; }
function girisDondurmaYilToplam() { return null; }
function girisPlanYilToplam() { return null; }
function girisAylikSatirYilKayitYukle() {}
function girisAylikSatirAnaDegerOku() { return { aylik: 150, nakit: 0, banka: 0 }; }
function girisAylikSatirTufeAylikMap() {
  // eski form zinciri (yanlış 2026/2027)
  return { 2023: 150, 2024: 263.18, 2025: 428.33, 2026: 471.73, 2027: 624.43 };
}
_el('kdv_oran', '20');
_el('sozlesme_baslangic', '2023-05-01');
_el('aylik_kira', '150');
window.__aylikSatirYilDetay = {};
window.__musteriReelDonemDbMap = { '2023': 180, '2025': 514 };
window.__musteriReelDonemTutarlari = { '2023': 180, '2025': 514 };
window.__sozlesmelerAylikSonCacheObj = {
  musteri_id: 900379,
  reel_tufe_zincir: 1,
  baslangic: '2023-05-01',
  aylar: [
    { yil: 2025, ay: 5, ay_key: '2025-5', brut_tutar_kdv: 514, tutar_kdv_dahil: 514 },
    { yil: 2025, ay: 6, ay_key: '2025-6', brut_tutar_kdv: 514, tutar_kdv_dahil: 514 },
    { yil: 2026, ay: 5, ay_key: '2026-5', brut_tutar_kdv: 680.38, tutar_kdv_dahil: 680.38 },
    { yil: 2026, ay: 6, ay_key: '2026-6', brut_tutar_kdv: 680.38, tutar_kdv_dahil: 680.38 },
    { yil: 2027, ay: 5, ay_key: '2027-5', brut_tutar_kdv: 900.62, tutar_kdv_dahil: 900.62 },
    { yil: 2027, ay: 6, ay_key: '2027-6', brut_tutar_kdv: 900.62, tutar_kdv_dahil: 900.62 }
  ]
};
"""


def _node_run(blok: str, scenario_js: str) -> dict:
    harness = _harness_prelude() + "\n" + blok + "\n" + scenario_js + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(harness)
        path = f.name
    try:
        r = subprocess.run(
            ["node", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if r.returncode != 0:
            raise SystemExit("node fail: " + (r.stderr or r.stdout or "")[:400])
        line = [ln for ln in (r.stdout or "").splitlines() if ln.strip()][-1]
        return json.loads(line)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _extract_blok(src: str, names: list[str]) -> str:
    parts = []
    for n in names:
        try:
            parts.append(_fn(src, n))
        except SystemExit:
            # optional helpers may be missing in HEAD
            if n in (
                "girisAylikSatirYilApiDonemBrutAl",
                "girisAylikSatirYilDonemApiEzilebilirMi",
            ):
                continue
            raise
    return "\n".join(parts)


def test_a_z1_rows() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract_blok(src, FE_NAMES)
    scenario = r"""
// 2025 reel satırı
window.__aylikSatirYilDetay['2025'] = { reel: 514, __reel_aktif: true, nakit: 0 };
var h25 = girisAylikSatirYilHesapla(2025);
// 2026/2027 API
window.__aylikSatirYilDetay['2026'] = { aylik: 471.73, nakit: 0, __manual_aylik: false };
window.__aylikSatirYilDetay['2027'] = { aylik: 624.43, nakit: 0, __manual_aylik: false };
var h26 = girisAylikSatirYilHesapla(2026);
var h27 = girisAylikSatirYilHesapla(2027);
var api26 = girisAylikSatirYilApiDonemBrutAl(2026);
var api27 = girisAylikSatirYilApiDonemBrutAl(2027);
var t26 = girisTahsilatYilAylikTek(2026);
var t27 = girisTahsilatYilAylikTek(2027);
console.log(JSON.stringify({
  h25: h25, h26: h26, h27: h27, api26: api26, api27: api27, t26: t26, t27: t27
}));
"""
    out = _node_run(blok, scenario)
    h26, h27 = out["h26"], out["h27"]
    check("a api26", near(out["api26"], API_2026), out.get("api26"))
    check("a api27", near(out["api27"], API_2027), out.get("api27"))
    check("a 2026 aylik net", near(h26.get("aylik"), NET_2026), h26)
    check("a 2026 kdv_dahil", near(h26.get("kdv_dahil"), API_2026), h26)
    check("a 2026 toplam", near(h26.get("toplam"), API_2026), h26)
    check("a 2026 banka", near(h26.get("banka"), API_2026), h26)
    check("a 2026 reel bos", not (h26.get("reel") or 0) > 0.01, h26)
    check("a 2027 kdv_dahil=api3", near(h27.get("kdv_dahil"), API_2027), h27)
    check("a 2027 toplam", near(h27.get("toplam"), API_2027), h27)
    check("a 2025 reel korunur", near(out["h25"].get("toplam"), API_2025), out["h25"])
    check("b tahsilat=borc 2026", near(out["t26"], h26.get("toplam")), out)
    check("b tahsilat=borc 2027", near(out["t27"], h27.get("toplam")), out)


def test_c_elle_koruma() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract_blok(src, FE_NAMES)
    scenario = r"""
window.__aylikSatirYilDetay['2026'] = {
  aylik: 471.73, nakit: 0, __manual_saved: true, __manual_aylik: true
};
var h = girisAylikSatirYilHesapla(2026);
var ez = girisAylikSatirYilDonemApiEzilebilirMi(2026);
console.log(JSON.stringify({ h: h, ez: ez }));
"""
    out = _node_run(blok, scenario)
    check("c ezilebilir false", out.get("ez") is False, out)
    # eski form zinciri kazanır (brut_kdv flag yok → net path: kdv~566)
    check("c eski kural aylik", near(out["h"].get("aylik"), 471.73), out["h"])


def test_d_nakit_iliski() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract_blok(src, FE_NAMES)
    scenario = r"""
window.__aylikSatirYilDetay['2026'] = { aylik: 471.73, nakit: 100, __manual_aylik: false };
var h = girisAylikSatirYilHesapla(2026);
console.log(JSON.stringify({ h: h }));
"""
    out = _node_run(blok, scenario)
    h = out["h"]
    # API net aylık; nakit 100 → banka = kdv_dahil = (aylik-nakit)*1.2? 
    # index Hesapla: bankaNet = aylik-nakit, banka = bankaNet*kdvMult, kdvDahil=banka, toplam=kdvDahil+nakit
    aylik = float(h.get("aylik") or 0)
    nakit = float(h.get("nakit") or 0)
    toplam = float(h.get("toplam") or 0)
    banka = float(h.get("banka") or 0)
    check("d nakit korunur", near(nakit, 100), h)
    check("d toplam = banka + nakit", near(toplam, banka + nakit), h)
    check("d aylik API net", near(aylik, NET_2026), h)


def test_e_z0_birebir_head() -> None:
    """Z=0: mevcut Hesapla çıktısı HEAD ile birebir (API ezmesi yok)."""
    src = HTML.read_text(encoding="utf-8", errors="replace")
    cur_hes = _fn(src, "girisAylikSatirYilHesapla")
    head_hes = _fn_from_commit(HEAD, "girisAylikSatirYilHesapla")
    # Z=0 senaryosu: zincir kapalı, API cache olsa bile eski aylik
    scenario_tmpl = r"""
window.__sozlesmelerAylikSonCacheObj.reel_tufe_zincir = 0;
window.__aylikSatirYilDetay['2026'] = { aylik: 471.73, nakit: 0 };
var h = girisAylikSatirYilHesapla(2026);
console.log(JSON.stringify({ h: h }));
"""
    # current: include new helpers + hesapla; Z=0 → ezilebilir false
    blok_cur = _extract_blok(src, FE_NAMES)
    out_cur = _node_run(blok_cur, scenario_tmpl)

    # HEAD: no new helpers; stub AktifMi false path via cache Z=0
    # Need KartDonemYili etc? HEAD Hesapla doesn't call API helpers.
    # Extract HEAD hesapla + minimal deps that HEAD hesapla needs
    head_src = subprocess.run(
        ["git", "show", f"{HEAD}:erp_web/templates/giris/index.html"],
        cwd=str(ROOT.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    ).stdout
    head_names = [
        "girisReelTufeZincirStickyBag",
        "girisReelTufeZincirStickyMidCoz",
        "girisReelTufeZincirCacheStickyUygula",
        "girisReelTufeZincirAktifMi",
        "girisReelTufeZincirApiZ",
        "girisAylikSatirYilHesapla",
        "girisTahsilatYilAylikTek",
    ]
    blok_head = _extract_blok(head_src, head_names)
    out_head = _node_run(blok_head, scenario_tmpl)
    hc, hh = out_cur["h"], out_head["h"]
    for k in ("aylik", "nakit", "banka", "kdv_dahil", "toplam", "reel"):
        check(
            f"e Z0 {k}",
            near(hc.get(k), hh.get(k)),
            {"cur": hc.get(k), "head": hh.get(k)},
        )


def test_f_playwright_delayed() -> None:
    """Temiz + warm; gecikmeli cache; yıllık özet son değer API."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        check("f playwright import", False, "yok")
        return

    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract_blok(src, FE_NAMES)
    page_html = f"""<!DOCTYPE html><html><body>
<input id="kdv_oran" value="20">
<input id="sozlesme_baslangic" value="2023-05-01">
<input id="aylik_kira" value="150">
<script>
var SOZLESME_TAM_ODENDI_TOLERANS = 0.05;
var selectedId = {SYN_MID};
function parseTarihStr(s) {{
  var m = String(s||'').match(/(\\d{{4}})-(\\d{{1,2}})-(\\d{{1,2}})/);
  return m ? new Date(+m[1], +m[2]-1, +m[3]) : null;
}}
function sozlesmelerReelDonemAnchor(basStr) {{
  var d = parseTarihStr(basStr); return d && !isNaN(d.getTime()) ? {{ month: d.getMonth(), date: d.getDate() }} : {{ month: 4, date: 1 }};
}}
function girisAylikSatirIlkYilMi(y) {{ return +y === 2023; }}
function girisAylikSatirIlkYilKartHesap() {{
  return {{ aylik: 150, nakit: 0, banka: 180, kdv_dahil: 180, toplam: 180, reel: 180 }};
}}
function girisAylikSatirYilReelGoster() {{ return 0; }}
function girisAylikSatirYilReelDonemDetay() {{ return null; }}
function girisAylikSatirYilMapKdvDahil() {{ return 0; }}
function girisPlanYilGecerliAylik() {{ return null; }}
function girisDondurmaYilToplam() {{ return null; }}
function girisPlanYilToplam() {{ return null; }}
function girisAylikSatirYilKayitYukle() {{}}
function girisAylikSatirBaslangicYili() {{ return 2023; }}
{blok}
window.__musteriReelDonemDbMap = {{ '2025': 514 }};
window.__musteriReelDonemTutarlari = {{ '2025': 514 }};
window.__aylikSatirYilDetay = {{}};
function applyCacheDelayed(delayMs) {{
  return new Promise(function(resolve) {{
    setTimeout(function() {{
      window.__sozlesmelerAylikSonCacheObj = {{
        musteri_id: {SYN_MID},
        reel_tufe_zincir: 1,
        baslangic: '2023-05-01',
        aylar: [
          {{ yil: 2025, ay: 5, ay_key: '2025-5', brut_tutar_kdv: {API_2025} }},
          {{ yil: 2026, ay: 5, ay_key: '2026-5', brut_tutar_kdv: {API_2026} }},
          {{ yil: 2026, ay: 6, ay_key: '2026-6', brut_tutar_kdv: {API_2026} }},
          {{ yil: 2027, ay: 5, ay_key: '2027-5', brut_tutar_kdv: {API_2027} }},
          {{ yil: 2027, ay: 6, ay_key: '2027-6', brut_tutar_kdv: {API_2027} }}
        ]
      }};
      window.__aylikSatirYilDetay['2026'] = {{ aylik: 471.73, nakit: 0 }};
      window.__aylikSatirYilDetay['2027'] = {{ aylik: 624.43, nakit: 0 }};
      window.__aylikSatirYilDetay['2025'] = {{ reel: 514, __reel_aktif: true, nakit: 0 }};
      var h26 = girisAylikSatirYilHesapla(2026);
      var h27 = girisAylikSatirYilHesapla(2027);
      var h25 = girisAylikSatirYilHesapla(2025);
      var t26 = girisTahsilatYilAylikTek(2026);
      resolve({{ h26: h26, h27: h27, h25: h25, t26: t26 }});
    }}, delayMs);
  }});
}}
</script></body></html>"""

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page_html.encode("utf-8"))

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    url = f"http://127.0.0.1:{port}/"
    a = b = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx1 = browser.new_context()
            page1 = ctx1.new_page()
            page1.goto(url, wait_until="domcontentloaded")
            a = page1.evaluate("applyCacheDelayed(80)")
            page1.evaluate("localStorage.setItem('warm_yillik', '1')")
            ctx1.close()
            ctx2 = browser.new_context()
            page2 = ctx2.new_page()
            page2.goto(url, wait_until="domcontentloaded")
            page2.evaluate("localStorage.setItem('warm_yillik', '1')")
            b = page2.evaluate("applyCacheDelayed(150)")
            browser.close()
    finally:
        httpd.shutdown()

    check("f playwright kosustu", a is not None and b is not None)
    for label, data in (("temiz", a or {}), ("warm", b or {})):
        h26 = data.get("h26") or {}
        h27 = data.get("h27") or {}
        h25 = data.get("h25") or {}
        check(f"f {label} 2026 kdv API", near(h26.get("kdv_dahil"), API_2026), h26)
        check(f"f {label} 2026 aylik net", near(h26.get("aylik"), NET_2026), h26)
        check(f"f {label} 2026 banka", near(h26.get("banka"), API_2026), h26)
        check(f"f {label} 2027 kdv", near(h27.get("kdv_dahil"), API_2027), h27)
        check(f"f {label} tahsilat=hesapla", near(data.get("t26"), h26.get("toplam")), data)
        check(f"f {label} 2025 reel", near(h25.get("toplam"), API_2025), h25)


def main() -> int:
    # no prod flags
    for k in list(os.environ):
        if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_")) or k == "ALLOW_PROD_WRITE":
            del os.environ[k]
    test_a_z1_rows()
    test_c_elle_koruma()
    test_d_nakit_iliski()
    test_e_z0_birebir_head()
    test_f_playwright_delayed()
    print("TUMU GECTI" if not FAILS else "FAIL", FAILS)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
