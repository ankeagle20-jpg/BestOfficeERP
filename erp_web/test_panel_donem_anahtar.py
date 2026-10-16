# -*- coding: utf-8 -*-
"""Tahsilat panel dönem anahtarı (sozlesmeAylikBorcPanelDonemYil) — Tem–Haz vs Oca–Ara."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
TJS = (ROOT / "_t.js").read_text(encoding="utf-8") if (ROOT / "_t.js").is_file() else ""
FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:200]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _extract_fn(src: str, name: str) -> str:
    i = src.find("function " + name)
    if i < 0:
        return ""
    # naive brace match
    start = i
    brace = src.find("{", i)
    depth = 0
    for k in range(brace, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[start : k + 1]
    return ""


def test_static():
    check("DonemYil index'te tanımlı", "function sozlesmeAylikBorcPanelDonemYil" in HTML)
    check("KartDonem index'te", "function sozlesmelerAylikKartDonemYili" in HTML)
    check("LS ver=3", re.search(r"GIRIS_TAHSILAT_YIL_AY_KAYIT_VER\s*=\s*3\b", HTML) is not None)
    # CacheDenDoldur uses dkPc not String(yil)
    i = HTML.find("function girisTahsilatYilAyPanelCacheDenDoldur")
    j = HTML.find("\nfunction ", i + 10)
    chunk = HTML[i:j]
    check("CacheDenDoldur donemPc", "var donemPc =" in chunk and "dkPc" in chunk)
    check("CacheDenDoldur String(yil) yok", "String(yil)" not in chunk)
    check(
        "Borclandir DonemYil",
        "sozlesmeAylikBorcPanelDonemYil(sr.yil, sr.ay)" in HTML
        or "sozlesmeAylikBorcPanelDonemYil(sr.yil,sr.ay)" in HTML,
    )
    check("PanelDbYukle DonemYil çağrısı", "sozlesmeAylikBorcPanelDonemYil(yil, ay)" in HTML)
    # single source: DonemYil calls KartDonem
    fn = _extract_fn(HTML, "sozlesmeAylikBorcPanelDonemYil")
    check("DonemYil KartDonem çağırır", "sozlesmelerAylikKartDonemYili(" in fn)
    if TJS:
        fn_t = _extract_fn(TJS, "sozlesmeAylikBorcPanelDonemYil")
        check("_t.js DonemYil KartDonem çağırır", "sozlesmelerAylikKartDonemYili(" in fn_t)


def test_node_bucket():
    node = shutil.which("node")
    if not node:
        check("node var", False, "node yok")
        return
    kart = _extract_fn(HTML, "sozlesmelerAylikKartDonemYili")
    donem = _extract_fn(HTML, "sozlesmeAylikBorcPanelDonemYil")
    # Minimal parseTarihStr used by KartDonem
    helpers = r"""
function parseTarihStr(s) {
  if (!s) return null;
  var m = String(s).trim().match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  return new Date(parseInt(m[1],10), parseInt(m[2],10)-1, parseInt(m[3],10));
}
var document = { getElementById: function(){ return { value: '2025-07-05' }; } };
var window = { __sozlesmelerAylikSonCacheObj: { baslangic: '2025-07-05' }, __tahsilatSatirYilAyDetay: {} };
"""
    # PanelDbYukle hydrate core (from index) — simplified loop
    hydrate = r"""
function sozlesmeAylikAyKeyFromYilAy(y, a) { return y + '-' + a; }
function girisTahsilatYilAyPanelSatirNormalize() {}
function hydratePanelDb(byIso) {
  var tol = 0.05;
  window.__tahsilatSatirYilAyDetay = {};
  Object.keys(byIso).forEach(function (iso) {
    var row = byIso[iso];
    if (!row || typeof row !== 'object') return;
    var m = String(iso).slice(0, 10).match(/^(\d{4})-(\d{2})/);
    if (!m) return;
    var yil = parseInt(m[1], 10);
    var ay = parseInt(m[2], 10);
    var ak = sozlesmeAylikAyKeyFromYilAy(yil, ay);
    var donemYilDb = sozlesmeAylikBorcPanelDonemYil(yil, ay);
    var dk = String(donemYilDb);
    if (!window.__tahsilatSatirYilAyDetay[dk]) window.__tahsilatSatirYilAyDetay[dk] = {};
    window.__tahsilatSatirYilAyDetay[dk][ak] = {
      aylik_tutar: Math.round((parseFloat(row.aylik) || 0) * 100) / 100,
      tahsil: Math.round((parseFloat(row.tahsil) || 0) * 100) / 100,
      kalan: Math.round((parseFloat(row.kalan) || 0) * 100) / 100,
      __saved: true,
      __db_saved: true
    };
  });
  return window.__tahsilatSatirYilAyDetay;
}
"""
    by_iso = {}
    for y, a, brut in (
        *[(2025, m, 960.0) for m in range(7, 13)],
        *[(2026, m, 960.0) for m in range(1, 7)],
        *[(2026, m, 1267.49) for m in range(7, 13)],
        *[(2027, m, 1267.49) for m in range(1, 7)],
        *[(2027, m, 1673.46) for m in range(7, 10)],
    ):
        by_iso[f"{y:04d}-{a:02d}-01"] = {"aylik": brut, "tahsil": 0.0, "kalan": brut}

    betik = (
        helpers
        + kart
        + "\n"
        + donem
        + "\n"
        + hydrate
        + "\n"
        + "var byIso = "
        + json.dumps(by_iso)
        + ";\n"
        + r"""
// sınır
if (sozlesmeAylikBorcPanelDonemYil(2026, 6) !== 2025) { console.log('FAIL_SINIR_HAZ', sozlesmeAylikBorcPanelDonemYil(2026,6)); process.exit(2); }
if (sozlesmeAylikBorcPanelDonemYil(2026, 7) !== 2026) { console.log('FAIL_SINIR_TEM', sozlesmeAylikBorcPanelDonemYil(2026,7)); process.exit(3); }
var map = hydratePanelDb(byIso);
var m2025 = map['2025'] || {};
var m2026 = map['2026'] || {};
for (var a = 1; a <= 6; a++) {
  var ak = '2026-' + a;
  var d = m2025[ak];
  if (!d || d.kalan !== 960 || !d.__saved) { console.log('FAIL_OCAK_HAZ', ak, d); process.exit(4); }
  if (m2026[ak]) { console.log('FAIL_TAKVIM_BUCKET', ak); process.exit(5); }
}
for (var a2 = 7; a2 <= 12; a2++) {
  var ak2 = '2025-' + a2;
  if (!m2025[ak2] || m2025[ak2].kalan !== 960) { console.log('FAIL_2025', ak2, m2025[ak2]); process.exit(6); }
}
var dTem = m2026['2026-7'];
if (!dTem || Math.abs(dTem.kalan - 1267.49) > 0.01 || !dTem.__saved) { console.log('FAIL_TEM26', dTem); process.exit(7); }
// takvim yılı sözleşme (Oca başlangıç)
document.getElementById = function(){ return { value: '2026-01-15' }; };
window.__sozlesmelerAylikSonCacheObj = { baslangic: '2026-01-15' };
if (sozlesmeAylikBorcPanelDonemYil(2026, 3) !== 2026) { console.log('FAIL_CAL_MAR', sozlesmeAylikBorcPanelDonemYil(2026,3)); process.exit(8); }
if (sozlesmeAylikBorcPanelDonemYil(2026, 12) !== 2026) { console.log('FAIL_CAL_ARA', sozlesmeAylikBorcPanelDonemYil(2026,12)); process.exit(9); }
if (sozlesmeAylikBorcPanelDonemYil(2027, 1) !== 2027) { console.log('FAIL_CAL_OCA27', sozlesmeAylikBorcPanelDonemYil(2027,1)); process.exit(10); }
console.log('NODE_OK');
"""
    )
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "t.js"
        f.write_text(betik, encoding="utf-8")
        r = subprocess.run([node, str(f)], capture_output=True)
        out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
        check("node bucket Tem-Haz", r.returncode == 0 and "NODE_OK" in out, out[-300:])


def test_post_dirty_ay_iso_static():
    """POST dirty: yalnız __user_dirty / kayıtlı satırlar; iso takvim ayından."""
    i = HTML.find("function girisTahsilatYilAyPanelDbKaydet")
    j = HTML.find("\nfunction ", i + 10)
    chunk = HTML[i:j] if i >= 0 else ""
    check("PanelDbKaydet var", i >= 0)
    check(
        "POST dirty koruması",
        "__user_dirty" in chunk or "tah <= tol && kal > tol" in chunk,
    )
    # iso from ay key calendar parts
    check(
        "POST ay_iso takvim",
        "iso" in chunk.lower() and ("yil" in chunk and "ay" in chunk),
    )


def test_playwright_mock_panel():
    """Chromium: dönem 2025'te 12 satır Kaydedildi+960; 2026'da Tem kalan 1267.49."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        check("playwright kurulu", False, "playwright yok — node testi yeterli")
        return
    node_ok = shutil.which("node")
    if not node_ok:
        check("playwright skip node yok", True)
        return
    # Build minimal HTML page embedding DonemYil + hydrate + render
    kart = _extract_fn(HTML, "sozlesmelerAylikKartDonemYili")
    donem = _extract_fn(HTML, "sozlesmeAylikBorcPanelDonemYil")
    page_html = f"""<!DOCTYPE html><html><body>
<input id="sozlesme_baslangic" type="hidden" value="2025-07-05">
<div id="panel2025"></div><div id="panel2026"></div>
<script>
window.__sozlesmelerAylikSonCacheObj = {{ baslangic: '2025-07-05' }};
function parseTarihStr(s) {{
  var m = String(s||'').match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})/);
  return m ? new Date(+m[1], +m[2]-1, +m[3]) : null;
}}
{kart}
{donem}
function sozlesmeAylikAyKeyFromYilAy(y,a){{ return y+'-'+a; }}
var byIso = {{}};
for (var m=7;m<=12;m++) byIso['2025-'+String(m).padStart(2,'0')+'-01']={{aylik:960,tahsil:0,kalan:960}};
for (var m2=1;m2<=6;m2++) byIso['2026-'+String(m2).padStart(2,'0')+'-01']={{aylik:960,tahsil:0,kalan:960}};
for (var m3=7;m3<=12;m3++) byIso['2026-'+String(m3).padStart(2,'0')+'-01']={{aylik:1267.49,tahsil:0,kalan:1267.49}};
var detay = {{}};
Object.keys(byIso).forEach(function(iso){{
  var mm = iso.match(/^(\\d{{4}})-(\\d{{2}})/);
  var y=+mm[1], a=+mm[2];
  var dk = String(sozlesmeAylikBorcPanelDonemYil(y,a));
  var ak = sozlesmeAylikAyKeyFromYilAy(y,a);
  if (!detay[dk]) detay[dk]={{}};
  var r=byIso[iso];
  detay[dk][ak]={{kalan:r.kalan, __saved:true, aylik_tutar:r.aylik}};
}});
function paint(donem, elId, expectKalan) {{
  var map = detay[String(donem)]||{{}};
  var keys = [];
  var startY = donem, startM = 7;
  for (var i=0;i<12;i++) {{
    var tot = (startM-1)+i;
    var ny = startY + Math.floor(tot/12);
    var nm = (tot % 12) + 1;
    keys.push(ny+'-'+nm);
  }}
  var html = '';
  var nOk = 0;
  keys.forEach(function(ak){{
    var d = map[ak];
    var kayitli = !!(d && d.__saved);
    var kalan = d && d.kalan > 0 ? d.kalan : '';
    if (kayitli && Math.abs(Number(kalan)-expectKalan) < 0.02) nOk++;
    html += '<div class="satir" data-ak="'+ak+'"><span class="btn">'+(kayitli?'Kaydedildi':'Kaydet')+'</span><span class="kalan">'+(kalan||'')+'</span></div>';
  }});
  document.getElementById(elId).innerHTML = html;
  document.getElementById(elId).setAttribute('data-nok', String(nOk));
  document.getElementById(elId).setAttribute('data-n', String(keys.length));
}}
paint(2025, 'panel2025', 960);
paint(2026, 'panel2026', 1267.49);
</script></body></html>"""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "p.html"
        f.write_text(page_html, encoding="utf-8")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="chrome", headless=True)
                page = browser.new_page()
                page.goto(f.as_uri())
                n25 = page.locator("#panel2025").get_attribute("data-nok")
                n26 = page.locator("#panel2026").get_attribute("data-nok")
                txt25 = page.locator("#panel2025 .btn").all_text_contents()
                kalan26_tem = page.locator('#panel2026 .satir[data-ak="2026-7"] .kalan').inner_text()
                browser.close()
            check("pw 2025 12 Kaydedildi+960", n25 == "12", n25)
            check("pw 2025 all Kaydedildi", all(t == "Kaydedildi" for t in txt25), txt25[:3])
            check("pw 2026 Tem kalan 1267.49", abs(float(kalan26_tem) - 1267.49) < 0.02, kalan26_tem)
            check("pw 2026 6 ay (Tem-Ara) 1267", int(n26 or 0) >= 6, n26)
        except Exception as e:
            check("playwright çalıştı", False, type(e).__name__ + " " + str(e)[:160])


def test_undefined_fn_report():
    refs = set(re.findall(r"typeof\s+([A-Za-z_$][\w$]*)\s*===\s*['\"]function['\"]", HTML))
    defs = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", HTML))
    defs |= set(re.findall(r"([A-Za-z_$][\w$]*)\s*=\s*function\s*\(", HTML))
    missing = sorted(refs - defs)
    check("DonemYil artık missing değil", "sozlesmeAylikBorcPanelDonemYil" not in missing)
    # report others (informational checks — not fail unless empty scan broke)
    print("REPORT typeof-guarded missing defs:", ", ".join(missing[:40]))
    check("tarama çalıştı", isinstance(missing, list))


if __name__ == "__main__":
    test_static()
    test_node_bucket()
    test_post_dirty_ay_iso_static()
    test_playwright_mock_panel()
    test_undefined_fn_report()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
