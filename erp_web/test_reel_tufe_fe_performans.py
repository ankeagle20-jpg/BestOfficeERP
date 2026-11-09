# -*- coding: utf-8 -*-
"""Z=1 FE performans: memo/önbellek + çıktı birebir (06e398b golden).

Sayı tabanlı eşikler (flaky ms yok). Prod yazmaz; PII yok; commit yok.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "templates" / "giris" / "index.html"
GOLDEN_COMMIT = "06e398b"
FAILS: list[str] = []

SYN_MID = 900280
BAS = "2023-12-01"  # artis_ay=12
AY_N = 60


def check(name: str, ok: bool, ek="") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def near(a, b, tol=0.051) -> bool:
    try:
        if a is None and b is None:
            return True
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return a == b


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


FE_CUR = [
    "girisAylikSatirYilApiDonemBrutMemoInvalidate",
    "girisAylikSatirYilApiDonemBrutDurumBoz",
    "girisAylikSatirYilApiDonemBrutAl",
    "girisAylikSatirYilDonemApiEzilebilirMi",
    "girisAylikSatirBaslangicYili",
    "girisAylikSatirIlkYilMi",
    "girisAylikSatirYilHesapla",
    "girisAylikSatirYilAyVarsayilan",
    "girisAylikSatirYilAyHesapla",
    "girisTahsilatYilAyKiraTutar",
    "sozlesmelerAylikKartDonemYili",
    "sozlesmeReelDonemAyKeys",
    "sozlesmelerReelDonemAnchor",
    "girisReelTufeZincirAktifMi",
    "girisReelTufeZincirApiZ",
    "girisReelTufeZincirStickyBag",
    "girisReelTufeZincirStickyMidCoz",
    "girisReelTufeZincirCacheStickyUygula",
]

FE_GOLDEN = [
    "girisAylikSatirYilApiDonemBrutAl",
    "girisAylikSatirYilDonemApiEzilebilirMi",
    "girisAylikSatirBaslangicYili",
    "girisAylikSatirIlkYilMi",
    "girisAylikSatirYilHesapla",
    "sozlesmelerAylikKartDonemYili",
    "sozlesmeReelDonemAyKeys",
    "sozlesmelerReelDonemAnchor",
    "girisReelTufeZincirAktifMi",
    "girisReelTufeZincirApiZ",
    "girisReelTufeZincirStickyBag",
    "girisReelTufeZincirStickyMidCoz",
    "girisReelTufeZincirCacheStickyUygula",
]


def _extract(src: str, names: list[str]) -> str:
    parts = []
    for n in names:
        try:
            parts.append(_fn(src, n))
        except SystemExit:
            if n in (
                "girisAylikSatirYilApiDonemBrutMemoInvalidate",
                "girisAylikSatirYilApiDonemBrutDurumBoz",
            ):
                continue
            raise
    return "\n".join(parts)


def _prelude() -> str:
    # build 60 months starting Dec 2023, flat periods then chained bruts
    aylar_js = []
    y, m = 2023, 12
    for i in range(AY_N):
        # dönem yılı buckets: first 12 flat 4800, next 12 flat 5760, rest 6912
        if i < 12:
            brut = 4800.0
        elif i < 24:
            brut = 5760.0
        else:
            brut = 6912.0
        aylar_js.append(
            "{"
            f"yil:{y},ay:{m},ay_key:'{y}-{m}',brut_tutar_kdv:{brut},tutar_kdv_dahil:{brut}"
            "}"
        )
        m += 1
        if m > 12:
            m = 1
            y += 1
    aylar_lit = "[" + ",".join(aylar_js) + "]"
    return rf"""
var SOZLESME_TAM_ODENDI_TOLERANS = 0.05;
var selectedId = {SYN_MID};
var REEL_TUFE_ZINCIR_MIDLER = [{SYN_MID}];
const els = {{}};
function _el(id, val) {{
  els[id] = {{
    id: id, value: val == null ? '' : String(val), checked: false, style: {{}},
    options: [],
    getAttribute: function () {{ return null; }},
    setAttribute: function () {{}},
    querySelector: function () {{ return null; }},
    querySelectorAll: function () {{ return []; }},
    addEventListener: function () {{}},
  }};
  return els[id];
}}
global.document = {{
  getElementById: function (id) {{ return els[id] || null; }},
  createElement: function () {{ return {{ style: {{}}, appendChild: function(){{}}, addEventListener: function(){{}} }}; }},
  addEventListener: function () {{}},
}};
global.window = global;
global.localStorage = (function () {{
  var store = {{}};
  return {{
    getItem: function (k) {{ return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; }},
    setItem: function (k, v) {{ store[k] = String(v); }},
    removeItem: function (k) {{ delete store[k]; }},
  }};
}})();
function parseTarihStr(s) {{
  if (!s) return null;
  var m = String(s).match(/(\d{{4}})-(\d{{1,2}})-(\d{{1,2}})/);
  if (!m) return null;
  return new Date(parseInt(m[1],10), parseInt(m[2],10)-1, parseInt(m[3],10));
}}
function sozlesmelerReelDonemAnchor(basStr) {{
  var d = parseTarihStr(basStr) || new Date(basStr);
  if (!d || isNaN(d.getTime())) return {{ month: 11, date: 1 }};
  return {{ month: d.getMonth(), date: d.getDate() }};
}}
function girisAylikSatirIlkYilKartHesap() {{
  return {{ aylik: 4000, nakit: 0, banka: 4800, kdv_dahil: 4800, toplam: 4800, reel: 4800 }};
}}
function girisAylikSatirYilReelGoster() {{ return 0; }}
function girisAylikSatirYilReelDonemDetay() {{ return null; }}
function girisAylikSatirYilMapKdvDahil() {{ return 0; }}
function girisPlanYilGecerliAylik() {{ return null; }}
function girisDondurmaYilToplam() {{ return null; }}
function girisAylikSatirYilKayitYukle() {{}}
function girisAylikSatirYilAyKayitYukle() {{}}
function girisAylikSatirAnaDegerOku() {{ return {{ aylik: 4000, nakit: 0, banka: 0 }}; }}
function girisAylikSatirTufeAylikMap() {{ return {{}}; }}
function girisAylikSatirYilAyDetayAl() {{ return null; }}
function girisTahsilatYilAyDetayAl() {{ return null; }}
function girisAylikSatirYilKdvTutarGoster(h) {{ return h && h.kdv_dahil; }}
function girisAylikSatirYilBankaTutarGoster(h) {{ return h && h.banka; }}
function girisAylikSatirYilAyPanelGuncelle() {{}}
function girisAylikSatirYilAylikBrutKdvFlagMi() {{ return false; }}
_el('kdv_oran', '20');
_el('sozlesme_baslangic', '{BAS}');
_el('aylik_kira', '4000');
_el('kira_nakit_tutar', '0');
_el('kira_banka_tutar', '4000');
var sel = _el('aylik_kira_yil_select', '');
sel.options = [
  {{value:'2023'}},{{value:'2024'}},{{value:'2025'}},{{value:'2026'}},{{value:'2027'}},{{value:'2028'}}
];
_el('aylik_kira_yil_panel', '');
els['aylik_kira_yil_panel'].innerHTML = '';
els['aylik_kira_yil_panel'].style = {{display:'block'}};
window.__aylikSatirYilDetay = {{}};
window.__aylikSatirYilAcik = {{}};
window.__musteriReelDonemDbMap = {{}};
window.__musteriReelDonemTutarlari = {{}};
window.__sozlesmelerAylikSonCacheObj = {{
  musteri_id: {SYN_MID},
  reel_tufe_zincir: 1,
  baslangic: '{BAS}',
  artis_ay: 12,
  compute_rev: 31,
  updated_at: '2026-11-07T00:00:00',
  freshness_fingerprint: 'SYN|Z=1|H12|A60',
  aylar: {aylar_lit}
}};
function girisReelTufeZincirAktifMi() {{ return true; }}
function girisReelTufeZincirApiZ() {{ return 1; }}
"""


def _wrap_counts_js() -> str:
    return r"""
window.__perfCnt = { Hesapla:0, ApiDonemBrutAl:0, parseTarihStr:0, PanelDoldur:0, PanelDoldurGovde:0 };
(function(){
  var _p = parseTarihStr;
  parseTarihStr = function(){ window.__perfCnt.parseTarihStr++; return _p.apply(this, arguments); };
  var _h = girisAylikSatirYilHesapla;
  girisAylikSatirYilHesapla = function(){ window.__perfCnt.Hesapla++; return _h.apply(this, arguments); };
  var _a = girisAylikSatirYilApiDonemBrutAl;
  girisAylikSatirYilApiDonemBrutAl = function(){ window.__perfCnt.ApiDonemBrutAl++; return _a.apply(this, arguments); };
  if (typeof girisAylikSatirYilPanelDoldur === 'function') {
    var _d = girisAylikSatirYilPanelDoldur;
    girisAylikSatirYilPanelDoldur = function(){ window.__perfCnt.PanelDoldur++; return _d.apply(this, arguments); };
  }
  if (typeof girisAylikSatirYilPanelDoldurGovde === 'function') {
    var _g = girisAylikSatirYilPanelDoldurGovde;
    girisAylikSatirYilPanelDoldurGovde = function(){ window.__perfCnt.PanelDoldurGovde++; return _g.apply(this, arguments); };
  }
})();
"""


def _node_run(blok: str, scenario_js: str) -> dict:
    harness = _prelude() + "\n" + blok + "\n" + scenario_js + "\n"
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
            timeout=90,
        )
        if r.returncode != 0:
            raise SystemExit("node fail: " + (r.stderr or r.stdout or "")[:600])
        line = [ln for ln in (r.stdout or "").splitlines() if ln.strip()][-1]
        return json.loads(line)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _snapshot_scenario() -> str:
    return r"""
var years = [2023,2024,2025,2026,2027,2028];
var rows = {};
years.forEach(function(y){
  if (!window.__aylikSatirYilDetay[String(y)]) {
    window.__aylikSatirYilDetay[String(y)] = { aylik: 4000, nakit: 0, __manual_aylik: false };
  }
  rows[String(y)] = girisAylikSatirYilHesapla(y);
});
var apis = {};
years.forEach(function(y){ apis[String(y)] = girisAylikSatirYilApiDonemBrutAl(y); });
console.log(JSON.stringify({ rows: rows, apis: apis }));
"""


def test_call_budgets() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE_CUR)
    # include PanelDoldur(+Govde) from live file
    for n in ("girisAylikSatirYilPanelDoldur", "girisAylikSatirYilPanelDoldurGovde"):
        try:
            blok += "\n" + _fn(src, n)
        except SystemExit:
            pass
    scenario = (
        _wrap_counts_js()
        + r"""
// PanelDoldur may need innerHTML assign stub
els['aylik_kira_yil_panel'].querySelectorAll = function(){ return []; };
girisAylikSatirYilPanelDoldur();
// ekstra Hesapla turu (gerçek sayfa tekrarları)
for (var t=0;t<5;t++){
  [2023,2024,2025,2026,2027,2028].forEach(function(y){ girisAylikSatirYilHesapla(y); });
}
console.log(JSON.stringify({ cnt: window.__perfCnt }));
"""
    )
    out = _node_run(blok, scenario)
    c = out["cnt"]
    check("perf Hesapla<=2000", c.get("Hesapla", 999999) <= 2000, c)
    check("perf ApiDonemBrutAl<=200", c.get("ApiDonemBrutAl", 999999) <= 200, c)
    check("perf parseTarihStr<=5000", c.get("parseTarihStr", 999999) <= 5000, c)
    check("perf Hesapla>0", int(c.get("Hesapla") or 0) > 0, c)
    check("perf ApiDonemBrutAl>0", int(c.get("ApiDonemBrutAl") or 0) > 0, c)
    check("perf parseTarihStr>0", int(c.get("parseTarihStr") or 0) > 0, c)


def test_golden_output_match() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    cur = _extract(src, FE_CUR)
    gold_parts = []
    gold_src = subprocess.run(
        ["git", "show", f"{GOLDEN_COMMIT}:erp_web/templates/giris/index.html"],
        cwd=str(ROOT.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    ).stdout
    for n in FE_GOLDEN:
        gold_parts.append(_fn(gold_src, n))
    gold = "\n".join(gold_parts)
    out_cur = _node_run(cur, _snapshot_scenario())
    out_gold = _node_run(gold, _snapshot_scenario())
    diff = 0
    for y in out_gold["rows"]:
        for k in ("aylik", "nakit", "banka", "kdv_dahil", "toplam", "reel"):
            a = (out_cur["rows"].get(y) or {}).get(k)
            b = (out_gold["rows"].get(y) or {}).get(k)
            if not near(a, b):
                diff += 1
                print("DIFF row", y, k, a, b)
        if not near(out_cur["apis"].get(y), out_gold["apis"].get(y)):
            diff += 1
            print("DIFF api", y, out_cur["apis"].get(y), out_gold["apis"].get(y))
    check("golden fark=0", diff == 0, diff)


def test_memo_invalidate_on_payload() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE_CUR)
    scenario = r"""
var a1 = girisAylikSatirYilApiDonemBrutAl(2024);
var a1b = girisAylikSatirYilApiDonemBrutAl(2024);
// payload değiş
window.__sozlesmelerAylikSonCacheObj = Object.assign({}, window.__sozlesmelerAylikSonCacheObj, {
  freshness_fingerprint: 'SYN|Z=1|H12|A60|v2',
  updated_at: '2026-11-07T01:00:00',
  aylar: window.__sozlesmelerAylikSonCacheObj.aylar.map(function(a,i){
    var o = Object.assign({}, a);
    if (i >= 12 && i < 24) { o.brut_tutar_kdv = 9999; o.tutar_kdv_dahil = 9999; }
    return o;
  })
});
if (typeof girisAylikSatirYilApiDonemBrutMemoInvalidate === 'function') {
  girisAylikSatirYilApiDonemBrutMemoInvalidate('test-payload');
}
var a2 = girisAylikSatirYilApiDonemBrutAl(2024);
console.log(JSON.stringify({ a1:a1, a1b:a1b, a2:a2 }));
"""
    out = _node_run(blok, scenario)
    check("memo same before", near(out["a1"], out["a1b"]), out)
    # dönem 2024 flat was 5760; after change months inconsistent or 9999 → null or new
    check("memo stale degil", out["a2"] != out["a1"], out)


def test_memo_invalidate_on_dirty() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE_CUR)
    scenario = r"""
var a1 = girisAylikSatirYilApiDonemBrutAl(2025);
if (typeof girisAylikSatirYilApiDonemBrutDurumBoz === 'function') {
  girisAylikSatirYilApiDonemBrutDurumBoz('elle');
}
var a2 = girisAylikSatirYilApiDonemBrutAl(2025);
// değer aynı payload → aynı sonuç, ama memo yeniden kurulmuş olmalı (tick)
var tick = window.__girisApiDonemBrutDurumTick || 0;
console.log(JSON.stringify({ a1:a1, a2:a2, tick:tick, memoVer: (window.__girisApiDonemBrutMemo||{}).ver || '' }));
"""
    out = _node_run(blok, scenario)
    check("dirty tick>0", (out.get("tick") or 0) >= 1, out)
    check("dirty sonrası değer aynı payload", near(out["a1"], out["a2"]), out)
    check("dirty ver tick içerir", str(out.get("tick")) in str(out.get("memoVer")), out)


def test_panel_reentrancy_coalesce() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE_CUR)
    for n in ("girisAylikSatirYilPanelDoldur", "girisAylikSatirYilPanelDoldurGovde"):
        blok += "\n" + _fn(src, n)
    scenario = (
        _wrap_counts_js()
        + r"""
els['aylik_kira_yil_panel'].querySelectorAll = function(){ return []; };
els['aylik_kira_yil_panel'].querySelector = function(){ return null; };
// iç içe: govde sırasında tekrar doldur iste
var orig = girisAylikSatirYilPanelDoldurGovde;
girisAylikSatirYilPanelDoldurGovde = function(){
  window.__perfCnt.PanelDoldurGovde++;
  if (!window.__nestedOnce) {
    window.__nestedOnce = true;
    girisAylikSatirYilPanelDoldur(); // busy → again=true
  }
  // mutate state that must survive coalesce
  window.__aylikSatirYilDetay['2026'] = { aylik: 1234, nakit: 0, __manual_aylik: true, __marker: 1 };
  return orig.apply(this, arguments);
};
girisAylikSatirYilPanelDoldur();
var marker = (window.__aylikSatirYilDetay['2026']||{}).__marker;
var manual = !!(window.__aylikSatirYilDetay['2026']||{}).__manual_aylik;
console.log(JSON.stringify({ cnt: window.__perfCnt, marker: marker, manual: manual, again: !!window.__girisAylikSatirYilPanelDoldurAgain, busy: !!window.__girisAylikSatirYilPanelDoldurBusy }));
"""
    )
    out = _node_run(blok, scenario)
    check("reentry govde>=2", (out["cnt"].get("PanelDoldurGovde") or 0) >= 2, out["cnt"])
    check("reentry son durum marker", out.get("marker") == 1, out)
    check("reentry busy false after", out.get("busy") is False, out)


def test_playwright_memo_smoke() -> None:
    """Chromium'da memo + coalesce gerçekten koşsun (atlanmaz)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        check("playwright import", False, "yok")
        return
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE_CUR)
    for n in ("girisAylikSatirYilPanelDoldur", "girisAylikSatirYilPanelDoldurGovde"):
        try:
            blok += "\n" + _fn(src, n)
        except SystemExit:
            pass
    page_html = f"""<!DOCTYPE html><html><body>
<input id="sozlesme_baslangic" value="{BAS}">
<input id="aylik_kira" value="1000">
<input id="kira_nakit" value="0">
<input id="kdv_oran" value="20">
<div id="aylik_kira_yil_panel"></div>
<script>
var SOZLESME_TAM_ODENDI_TOLERANS=0.05;
var selectedId={SYN_MID};
function parseTarihStr(s){{var m=String(s||'').match(/(\\d{{4}})-(\\d{{1,2}})-(\\d{{1,2}})/);return m?new Date(+m[1],+m[2]-1,+m[3]):null;}}
function sozlesmeAylikAyKeyFromYilAy(y,a){{return y+'-'+a;}}
function girisPanelAyKeysCapFiltre(k){{return k;}}
function sozlesmelerYillikKdvDahilMapSec(){{return {{}};}}
function sozlesmelerReelArtisAyAdi(){{return 'Aralık';}}
function kiraNakitMi(){{return false;}}
function girisReelTufeZincirAktifMi(){{return true;}}
function girisReelTufeZincirApiZ(){{return 1;}}
window.__sozlesmelerAylikSonCacheObj={{aylar:[], reel_tufe_zincir:1}};
window.__musteriReelDonemTutarlari={{}};
window.__aylikSatirYilDetay={{}};
{blok}
window.__pwPerf = {{}};
(function(){{
  var a1 = girisAylikSatirYilApiDonemBrutAl(2025);
  var a2 = girisAylikSatirYilApiDonemBrutAl(2025);
  girisAylikSatirYilApiDonemBrutDurumBoz('pw');
  var a3 = girisAylikSatirYilApiDonemBrutAl(2025);
  var nested = 0;
  if (typeof girisAylikSatirYilPanelDoldurGovde === 'function') {{
    var orig = girisAylikSatirYilPanelDoldurGovde;
    girisAylikSatirYilPanelDoldurGovde = function(){{
      nested++;
      if (nested === 1) girisAylikSatirYilPanelDoldur();
      return orig.apply(this, arguments);
    }};
    girisAylikSatirYilPanelDoldur();
  }}
  window.__pwPerf = {{
    memoSame: a1 === a2,
    afterDirty: a3,
    tick: window.__girisApiDonemBrutDurumTick || 0,
    nested: nested,
    busy: !!window.__girisAylikSatirYilPanelDoldurBusy
  }};
}})();
</script></body></html>"""
    ran = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(page_html, wait_until="domcontentloaded")
        out = page.evaluate("() => window.__pwPerf")
        browser.close()
        ran = True
    check("playwright kosustu", ran is True)
    check("pw memo same", bool(out and out.get("memoSame")), out)
    check("pw dirty tick>0", bool(out and (out.get("tick") or 0) >= 1), out)
    if out and (out.get("nested") or 0) > 0:
        check("pw coalesce nested>=2", (out.get("nested") or 0) >= 2, out)
        check("pw busy false", out.get("busy") is False, out)


def test_z0_birebir() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    cur = _extract(src, FE_CUR)
    gold_src = subprocess.run(
        ["git", "show", f"{GOLDEN_COMMIT}:erp_web/templates/giris/index.html"],
        cwd=str(ROOT.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    ).stdout
    gold = "\n".join(_fn(gold_src, n) for n in FE_GOLDEN)
    scenario = (
        _wrap_counts_js()
        + r"""
// Z=0
girisReelTufeZincirAktifMi = function(){ return false; };
girisReelTufeZincirApiZ = function(){ return 0; };
window.__sozlesmelerAylikSonCacheObj.reel_tufe_zincir = 0;
window.__aylikSatirYilDetay = {};
var years=[2024,2025,2026];
var rows={};
years.forEach(function(y){
  window.__aylikSatirYilDetay[String(y)] = { aylik: 4000, nakit: 0 };
  rows[String(y)] = girisAylikSatirYilHesapla(y);
});
console.log(JSON.stringify({ rows: rows, cnt: window.__perfCnt }));
"""
    )
    out_c = _node_run(cur, scenario)
    out_g = _node_run(gold, scenario)
    diff = 0
    for y in out_g["rows"]:
        for k in ("aylik", "nakit", "banka", "kdv_dahil", "toplam"):
            if not near((out_c["rows"][y] or {}).get(k), (out_g["rows"][y] or {}).get(k)):
                diff += 1
    check("z0 output fark=0", diff == 0, diff)
    # call counts roughly same order (both small)
    check("z0 Hesapla küçük", out_c["cnt"]["Hesapla"] <= 50, out_c["cnt"])



def test_full_page_nonvacuous_counts() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        check("fullpage playwright", False, "yok")
        return
    import socket, subprocess, time, urllib.request
    from pathlib import Path as _P
    root = ROOT
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = os.environ.copy(); env.pop("ALLOW_PROD_WRITE", None)
    main_env = _P(r"C:\\Users\\MSI\\Desktop\\Best ERP\\BestOfficeERP\\erp_web\\.env")
    if main_env.exists():
        for line in main_env.read_text(encoding="utf-8", errors="replace").splitlines():
            line=line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k,v=line.split("=",1); k=k.strip()
            if k in ("ALLOW_PROD_WRITE","REEL_TUFE_ZINCIR") or k.startswith(("KAPANIS_","ODA_")): continue
            if k.startswith("REEL_TUFE_") and k != "REEL_TUFE_ZINCIR_MIDLER": continue
            if k and k not in env: env[k]=v.strip().strip('"').strip("'")
    env["REEL_TUFE_ZINCIR_MIDLER"] = "379,349,380,392,381,390,280,111,395,409,402,325,78,215,396,153,387,133,437,145,131,372,172,184,73,376,365,53"
    env.pop("ALLOW_PROD_WRITE", None)
    code = f"""
import os,sys
os.environ.pop('ALLOW_PROD_WRITE', None)
sys.path.insert(0, r'{root.as_posix()}')
os.chdir(r'{root.as_posix()}')
from app import app
from auth import User
from flask_login import login_user, current_user
from db import fetch_one
_admin = fetch_one("SELECT id, username, role, security_stamp FROM public.users WHERE role='admin' AND COALESCE(is_active,true)=true ORDER BY id LIMIT 1")
@app.before_request
def _auto():
    try:
        a=current_user.is_authenticated
        if (a() if callable(a) else a): return None
    except Exception:
        pass
    try:
        u=User(id=_admin['id'], username=_admin['username'], full_name='p', role=_admin['role'], aktif_mi=True, security_stamp=_admin.get('security_stamp'))
        login_user(u, remember=False)
    except Exception:
        pass
    return None
app.run(host='127.0.0.1', port={port}, debug=False, use_reloader=False, threaded=True)
"""
    logp = root / "_perf_fullpage_flask.log"
    logf = open(logp, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=str(root), env=env, stdout=logf, stderr=subprocess.STDOUT, text=True)
    ready=False
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/giris/", timeout=2)
            ready=True; break
        except Exception:
            if proc.poll() is not None: break
            time.sleep(0.4)
    if not ready:
        check("fullpage flask boot", False, "yok")
        try: proc.terminate(); logf.close()
        except Exception: pass
        return
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
            page = browser.new_page()
            page.add_init_script(
                "window.__perfCnt={Hesapla:0,ApiDonemBrutAl:0,parseTarihStr:0,PanelDoldur:0,PanelDoldurGovde:0};"
                "document.addEventListener('DOMContentLoaded',function(){"
                "try{"
                "if(typeof parseTarihStr==='function'){var _p=parseTarihStr;parseTarihStr=function(){window.__perfCnt.parseTarihStr++;return _p.apply(this,arguments);};}"
                "if(typeof girisAylikSatirYilHesapla==='function'){var _h=girisAylikSatirYilHesapla;girisAylikSatirYilHesapla=function(){window.__perfCnt.Hesapla++;return _h.apply(this,arguments);};}"
                "if(typeof girisAylikSatirYilApiDonemBrutAl==='function'){var _a=girisAylikSatirYilApiDonemBrutAl;girisAylikSatirYilApiDonemBrutAl=function(){window.__perfCnt.ApiDonemBrutAl++;return _a.apply(this,arguments);};}"
                "}catch(_e){}"
                "});"
            )
            page.goto(f"http://127.0.0.1:{port}/giris/?tab=sozlesmeler&mid=392", wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(8000)
            page.evaluate("""() => {
              try {
                if (typeof girisAylikSatirYilApiDonemBrutMemoInvalidate === 'function') girisAylikSatirYilApiDonemBrutMemoInvalidate('test');
                if (typeof girisAylikSatirYilApiDonemBrutDurumBoz === 'function') girisAylikSatirYilApiDonemBrutDurumBoz('test');
                if (typeof girisAylikSatirYilPanelDoldur === 'function') girisAylikSatirYilPanelDoldur();
                if (typeof girisAylikSatirYilHesapla === 'function') [2024,2025,2026].forEach(function(y){ girisAylikSatirYilHesapla(y); });
                if (typeof girisAylikSatirYilApiDonemBrutAl === 'function') girisAylikSatirYilApiDonemBrutAl(2026);
                if (typeof parseTarihStr === 'function') parseTarihStr('2024-05-01');
              } catch (e) {}
            }""")
            page.wait_for_timeout(2000)
            cnt = page.evaluate("() => window.__perfCnt || {}")
            check("fullpage Hesapla>0", int((cnt or {}).get("Hesapla") or 0) > 0, cnt)
            check("fullpage ApiDonemBrutAl>0", int((cnt or {}).get("ApiDonemBrutAl") or 0) > 0, cnt)
            check("fullpage parseTarihStr>0", int((cnt or {}).get("parseTarihStr") or 0) > 0, cnt)
            browser.close()
    finally:
        try: proc.terminate(); proc.wait(timeout=8)
        except Exception:
            try: proc.kill()
            except Exception: pass
        try: logf.close()
        except Exception: pass



def test_carpan_heavy_kira_storm() -> None:
    """Ağır-tip sentetik: Z=1 KiraTutar fırtınasında Hesapla üst sınırı."""
    src = HTML.read_text(encoding="utf-8", errors="replace")
    need = [
        "girisAylikSatirYilApiDonemBrutMemoInvalidate",
        "girisAylikSatirYilApiDonemBrutDurumBoz",
        "girisAylikSatirYilApiDonemBrutAl",
        "girisAylikSatirYilDonemApiEzilebilirMi",
        "girisAylikSatirBaslangicYili",
        "girisAylikSatirIlkYilMi",
        "girisAylikSatirYilHesapla",
        "girisAylikSatirYilAyVarsayilan",
        "girisAylikSatirYilAyHesapla",
        "girisTahsilatYilAyKiraTutar",
    ]
    blok = "\n".join(_fn(src, n) for n in need)
    stubs = r"""
function girisTahsilatAyKeyToYilAy(ayKey){ var p=String(ayKey).split('-'); return {yil:parseInt(p[0],10), ay:parseInt(p[1],10)}; }
function girisPlanGridHucreBrut(){ return null; }
function girisAyAnahtarDondurulmusMu(){ return false; }
function girisAylikGridCacheAyBrut(y,a){ return 1500; }
function girisAylikSatirYilAyDetayMap(){ return {}; }
function girisAylikSatirYilTufeSatirAylikAl(){ return 1000; }
function girisReelTufeZincirAktifMi(){ return true; }
"""
    scenario = (
        stubs
        + _wrap_counts_js()
        + r"""
var keys=[];
for (var y=2023;y<=2027;y++) for (var a=1;a<=12;a++) keys.push(y+'-'+a);
// once: prove Hesapla path alive (no early-return dependency)
girisAylikSatirYilHesapla(2025);
var hAfterSeed = window.__perfCnt.Hesapla;
for (var r=0;r<8;r++) {
  for (var i=0;i<keys.length;i++) girisTahsilatYilAyKiraTutar(2024, keys[i]);
}
console.log(JSON.stringify({ cnt: window.__perfCnt, keys: keys.length, hAfterSeed: hAfterSeed, hDelta: window.__perfCnt.Hesapla - hAfterSeed }));
"""
    )
    out = _node_run(blok, scenario)
    c = out["cnt"]
    check("carpan Kira storm Hesapla<=400", c.get("Hesapla", 999999) <= 400, c)
    check("carpan seed Hesapla>0", int(out.get("hAfterSeed", 0)) > 0, out)
    check("carpan storm Hesapla delta<=50", int(out.get("hDelta", 999)) <= 50, out)
    check("carpan keys>0", int(out.get("keys") or 0) > 0, out)


def main() -> int:
    for k in list(os.environ):
        if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "ALLOW_PROD_WRITE")):
            os.environ.pop(k, None)
    print("=== test_reel_tufe_fe_performans ===")
    test_call_budgets()
    test_golden_output_match()
    test_memo_invalidate_on_payload()
    test_memo_invalidate_on_dirty()
    test_panel_reentrancy_coalesce()
    test_playwright_memo_smoke()
    test_full_page_nonvacuous_counts()
    test_z0_birebir()
    test_carpan_heavy_kira_storm()
    print("FAILS", len(FAILS), FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
