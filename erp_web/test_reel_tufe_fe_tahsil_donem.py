# -*- coding: utf-8 -*-
"""Z=1 tahsilat paneli: dönem yılı (artış ayı→12 ay); Z=0 takvim yılı birebir.

Gerçek templates/giris/index.html fonksiyonları (node + Playwright).
Prod yazmaz; sentetik mid/tutar; PII yok. Commit yok.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "templates" / "giris" / "index.html"
HEAD = "6e40d45"
FAILS: list[str] = []
SYN_MID = 900379
API_2025 = 514.0
API_2026 = 680.38


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


FE = [
    "girisReelTufeZincirStickyBag",
    "girisReelTufeZincirStickyMidCoz",
    "girisReelTufeZincirCacheStickyUygula",
    "girisReelTufeZincirAktifMi",
    "girisReelTufeZincirApiZ",
    "sozlesmelerReelDonemAnchor",
    "sozlesmeReelDonemAyKeys",
    "sozlesmelerAylikKartDonemYili",
    "girisTahsilatYilAyKeysFiltered",
    "girisTahsilatAyKeyToYilAy",
    "girisTahsilatYilAyIsoOneri",
    "girisTahsilatYilAyId",
]


def _extract(src: str, names: list[str]) -> str:
    parts = []
    for n in names:
        try:
            parts.append(_fn(src, n))
        except SystemExit:
            if n.startswith("girisReelTufe"):
                continue
            raise
    return "\n".join(parts)


def _prelude(bas: str = "2023-05-10", z: int = 1) -> str:
    return f"""
var SOZLESME_TAM_ODENDI_TOLERANS = 0.05;
var selectedId = {SYN_MID};
const els = {{}};
function _el(id, val) {{
  els[id] = {{ id: id, value: val == null ? '' : String(val), checked: false, style: {{}},
    getAttribute: function(){{return null;}}, setAttribute: function(){{}},
    querySelector: function(){{return null;}}, querySelectorAll: function(){{return [];}} }};
  return els[id];
}}
global.document = {{ getElementById: function(id){{ return els[id]||null; }}, addEventListener: function(){{}} }};
global.window = global;
global.localStorage = {{ getItem: function(){{return null;}}, setItem: function(){{}}, removeItem: function(){{}} }};
function parseTarihStr(s) {{
  var m = String(s||'').match(/(\\d{{4}})-(\\d{{1,2}})-(\\d{{1,2}})/);
  return m ? new Date(+m[1], +m[2]-1, +m[3]) : null;
}}
function sozlesmeAylikAyKeyFromYilAy(y, a) {{ return y + '-' + a; }}
function girisPanelAyKeysCapFiltre(keys) {{ return keys; }}
_el('sozlesme_baslangic', '{bas}');
_el('sozlesme_bitis', '');
_el('kdv_oran', '20');
window.__sozlesmelerAylikSonCacheObj = {{
  musteri_id: {SYN_MID},
  reel_tufe_zincir: {z},
  baslangic: '{bas}',
  aylar: (function(){{
    var out = [];
    // 2025 dönem: May25-Apr26 = 514; 2026 dönem: May26-Apr27 = 680.38
    function pushRange(y0, m0, n, brut) {{
      var y=y0, m=m0;
      for (var i=0;i<n;i++) {{
        out.push({{ yil:y, ay:m, ay_key: y+'-'+m, brut_tutar_kdv: brut, tutar_kdv_dahil: brut }});
        m++; if (m>12) {{ m=1; y++; }}
      }}
    }}
    pushRange(2025,5,12,{API_2025});
    pushRange(2026,5,12,{API_2026});
    return out;
  }})()
}};
"""


def _node(blok: str, scenario: str, bas: str = "2023-05-10", z: int = 1) -> dict:
    harness = _prelude(bas, z) + "\n" + blok + "\n" + scenario + "\n"
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


def test_a_z1_period_keys() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE)
    scenario = r"""
var k26 = girisTahsilatYilAyKeysFiltered(2026);
var k25 = girisTahsilatYilAyKeysFiltered(2025);
// d2-ii: 2025 döneminde Oca–Nis 2026 tutarları API 514
function brutOf(ak) {
  var ya = girisTahsilatAyKeyToYilAy(ak);
  var sco = window.__sozlesmelerAylikSonCacheObj;
  var hit = null;
  (sco.aylar || []).forEach(function (a) {
    if (+a.yil === ya.yil && +a.ay === ya.ay) hit = a.brut_tutar_kdv != null ? a.brut_tutar_kdv : a.tutar_kdv_dahil;
  });
  return hit;
}
var ocaNis25 = (k25 || []).filter(function (ak) {
  var ya = girisTahsilatAyKeyToYilAy(ak);
  return ya.yil === 2026 && ya.ay >= 1 && ya.ay <= 4;
}).map(function (ak) { return { ak: ak, brut: brutOf(ak) }; });
console.log(JSON.stringify({
  k26: k26, k25: k25, n26: k26.length, n25: k25.length, ocaNis25: ocaNis25
}));
"""
    out = _node(blok, scenario, z=1)
    k26, k25 = out["k26"], out["k25"]
    check("a 2026 12 ay", out["n26"] == 12, out)
    check("a 2026 first May", k26[0] == "2026-5" or k26[0] == "2026-05", k26[:2])
    # sozlesmeReelDonemAyKeys uses m+1 without pad: 2026-5
    check("a 2026 last Apr27", k26[-1] in ("2027-4", "2027-04"), k26[-1])
    check("a 2026 no Jan26", "2026-1" not in k26 and "2026-01" not in k26, k26)
    check("a 2025 has Jan-Apr26", any(x in k25 for x in ("2026-1", "2026-01", "2026-4", "2026-04")), k25)
    check("a 2025 starts May25", k25[0] in ("2025-5", "2025-05"), k25[0])
    oca = out.get("ocaNis25") or []
    check("a/d2-ii Oca-Nis26 in 2025", len(oca) == 4, oca)
    check(
        "a/d2-ii Oca-Nis26 brut 514",
        len(oca) == 4 and all(near(x.get("brut"), API_2025) for x in oca),
        oca,
    )


def test_b_year_nav_bounds() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE)
    scenario = r"""
// d2-ii: önceki=2025 (May25–Nis26), sonraki=2027 (May27+)
var k26 = girisTahsilatYilAyKeysFiltered(2026);
var k27 = girisTahsilatYilAyKeysFiltered(2027);
var k25 = girisTahsilatYilAyKeysFiltered(2025);
var k23 = girisTahsilatYilAyKeysFiltered(2023);
console.log(JSON.stringify({
  k26n: k26.length, k27n: k27.length, k25n: k25.length, k23n: k23.length,
  k27first: k27[0] || null, k25first: k25[0] || null, k25last: k25[k25.length-1] || null,
  k23first: k23[0] || null
}));
"""
    out = _node(blok, scenario, z=1)
    check("b 2027 dönem keys", out["k27n"] == 12 and str(out.get("k27first") or "").startswith("2027-"), out)
    check("b 2023 dönem keys", out["k23n"] == 12 and str(out.get("k23first") or "").startswith("2023-"), out)
    check("b/d2-ii prev 2025 May-Apr", out["k25n"] == 12 and str(out.get("k25first") or "").startswith("2025-5") and str(out.get("k25last") or "").startswith("2026-4"), out)


def test_c_kayit_iso_keys() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE)
    scenario = r"""
var k26 = girisTahsilatYilAyKeysFiltered(2026);
// May 2026 row + Jan 2027 row
var may = k26.find(function(k){ return k === '2026-5' || k === '2026-05'; });
var oca = k26.find(function(k){ return k === '2027-1' || k === '2027-01'; });
var yaMay = girisTahsilatAyKeyToYilAy(may);
var yaOca = girisTahsilatAyKeyToYilAy(oca);
var idMay = girisTahsilatYilAyId(2026, may);
var idOca = girisTahsilatYilAyId(2026, oca);
console.log(JSON.stringify({
  may: may, oca: oca, yaMay: yaMay, yaOca: yaOca, idMay: idMay, idOca: idOca
}));
"""
    out = _node(blok, scenario, z=1)
    check("c May key", out.get("may") in ("2026-5", "2026-05"), out)
    check("c Oca27 key", out.get("oca") in ("2027-1", "2027-01"), out)
    check("c May yil/ay", out["yaMay"]["yil"] == 2026 and out["yaMay"]["ay"] == 5, out)
    check("c Oca yil/ay", out["yaOca"]["yil"] == 2027 and out["yaOca"]["ay"] == 1, out)
    check("c id contains ayKey", "2026_5" in out["idMay"] or "2026_05" in out["idMay"], out)
    check("c Oca id 2027_1", "2027_1" in out["idOca"] or "2027_01" in out["idOca"], out)


def test_d_kalan() -> None:
    # Kalan = aylik - tahsil is in Hesapla path; unit-check formula via extracted Kalan if available
    src = HTML.read_text(encoding="utf-8", errors="replace")
    try:
        kalan_fn = _fn(src, "girisTahsilatYilAyKalanHesapla")
    except SystemExit:
        check("d kalan fn", False)
        return
    blok = kalan_fn
    scenario = r"""
function girisTahsilatYilAyKalanHesapla(d) {
""" + ""  # already have full fn in blok
    # re-extract properly
    blok = _fn(src, "girisTahsilatYilAyKalanHesapla")
    scenario = r"""
var d1 = { aylik_tutar: 680.38, tahsil: 0 };
var d2 = { aylik_tutar: 680.38, tahsil: 100 };
var k1 = girisTahsilatYilAyKalanHesapla(d1);
var k2 = girisTahsilatYilAyKalanHesapla(d2);
console.log(JSON.stringify({ k1: k1, k2: k2 }));
"""
    harness = "var SOZLESME_TAM_ODENDI_TOLERANS=0.05;\n" + blok + "\n" + scenario
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(harness)
        path = f.name
    try:
        r = subprocess.run(["node", path], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        out = json.loads([ln for ln in r.stdout.splitlines() if ln.strip()][-1])
    finally:
        os.unlink(path)
    check("d kalan full", near(out["k1"], API_2026), out)
    check("d kalan partial", near(out["k2"], API_2026 - 100), out)


def test_e_other_artis_ay() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE)
    # artış 3
    out3 = _node(
        blok,
        "var k=girisTahsilatYilAyKeysFiltered(2026); console.log(JSON.stringify({k:k,n:k.length,f:k[0],l:k[k.length-1]}));",
        bas="2020-03-15",
        z=1,
    )
    check("e artis3 first", str(out3.get("f") or "").startswith("2026-3"), out3)
    check("e artis3 last", str(out3.get("l") or "").startswith("2027-2"), out3)
    check("e artis3 n12", out3.get("n") == 12, out3)
    # artış 9
    out9 = _node(
        blok,
        "var k=girisTahsilatYilAyKeysFiltered(2026); console.log(JSON.stringify({k:k,f:k[0],l:k[k.length-1]}));",
        bas="2019-09-01",
        z=1,
    )
    check("e artis9 first", str(out9.get("f") or "").startswith("2026-9"), out9)
    check("e artis9 last", str(out9.get("l") or "").startswith("2027-8"), out9)
    # artış 1 → calendar = period
    out1z = _node(
        blok,
        "var k=girisTahsilatYilAyKeysFiltered(2026); console.log(JSON.stringify({k:k,f:k[0],l:k[k.length-1]}));",
        bas="2020-01-01",
        z=1,
    )
    out1z0 = _node(
        blok,
        "var k=girisTahsilatYilAyKeysFiltered(2026); console.log(JSON.stringify({k:k,f:k[0],l:k[k.length-1]}));",
        bas="2020-01-01",
        z=0,
    )
    check("e artis1 Z1=Jan-Dec", out1z.get("f") in ("2026-1", "2026-01") and str(out1z.get("l") or "").startswith("2026-12"), out1z)
    check("e artis1 Z1==Z0 keys", out1z.get("k") == out1z0.get("k"), {"z1": out1z.get("k"), "z0": out1z0.get("k")})


def test_f_z0_birebir_head() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    cur = _fn(src, "girisTahsilatYilAyKeysFiltered")
    head = _fn_from_commit(HEAD, "girisTahsilatYilAyKeysFiltered")
    # Need deps for both
    deps = [
        "girisReelTufeZincirStickyBag",
        "girisReelTufeZincirStickyMidCoz",
        "girisReelTufeZincirCacheStickyUygula",
        "girisReelTufeZincirAktifMi",
        "girisReelTufeZincirApiZ",
        "sozlesmelerReelDonemAnchor",
        "sozlesmeReelDonemAyKeys",
        "girisTahsilatYilAyIsoOneri",
    ]
    head_src = subprocess.run(
        ["git", "show", f"{HEAD}:erp_web/templates/giris/index.html"],
        cwd=str(ROOT.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    ).stdout
    blok_cur = _extract(src, deps + ["girisTahsilatYilAyKeysFiltered"])
    blok_head = _extract(head_src, deps + ["girisTahsilatYilAyKeysFiltered"])
    # For HEAD, AktifMi exists but Z=0 path shouldn't call period-first; same calendar
    scenario = r"""
var k = girisTahsilatYilAyKeysFiltered(2026);
// d2-v: satırlar (=keys), yıl listesi proxy (unique yil from keys), CSV satır sırası (=keys join)
var yillar = [];
(k || []).forEach(function (ak) {
  var ya = (typeof girisTahsilatAyKeyToYilAy === 'function') ? girisTahsilatAyKeyToYilAy(ak) : null;
  var y = ya ? ya.yil : parseInt(String(ak).split('-')[0], 10);
  if (yillar.indexOf(y) < 0) yillar.push(y);
});
var csv = (k || []).join(',');
console.log(JSON.stringify({ k: k, yillar: yillar, csv: csv }));
"""
    # AyKeyToYilAy for year list / CSV proxies
    if "girisTahsilatAyKeyToYilAy" not in blok_cur:
        blok_cur = _extract(src, deps + ["girisTahsilatYilAyKeysFiltered", "girisTahsilatAyKeyToYilAy"])
        blok_head = _extract(head_src, deps + ["girisTahsilatYilAyKeysFiltered", "girisTahsilatAyKeyToYilAy"])
    out_cur = _node(blok_cur, scenario, z=0)
    out_head = _node(blok_head, scenario, z=0)
    check("f Z0 keys birebir", out_cur.get("k") == out_head.get("k"), {"cur": out_cur.get("k"), "head": out_head.get("k")})
    check("f/d2-v Z0 yillar birebir", out_cur.get("yillar") == out_head.get("yillar"), {"cur": out_cur.get("yillar"), "head": out_head.get("yillar")})
    check("f/d2-v Z0 csv birebir", out_cur.get("csv") == out_head.get("csv"), {"cur": out_cur.get("csv"), "head": out_head.get("csv")})
    # Z=0 should be calendar year from sco.aylar (May-Dec 2026 only in our synthetic if filter yy===2026)
    # Our synthetic aylar has May26-Apr27 for 680 and May25-Apr26 for 514.
    # Calendar filter yy===2026 → Jan-Dec 2026 months present = May-Dec 2026 (8 months) + nothing Jan-Apr from 514 set... 
    # Jan-Apr 2026 are in 514 pushRange starting May 2025: months 9-12 of that range = Jan-Apr 2026 with yil=2026.
    # So Z=0 calendar 2026 = Jan-Dec 2026 (12 months mixed 514/680).
    check("f Z0 is calendar-ish", len(out_cur.get("k") or []) >= 8, out_cur)


def test_g_playwright_delayed() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        check("g playwright import", False, "yok")
        return
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE)
    # d2-i: warm localStorage'da eski TAKVİM-YILI donemYil=2026 map (Oca–Ara anahtarları)
    eski_takvim_map = json.dumps(
        {
            "2026-1": {"tahsil": 10, "tarih": "2026-01-15"},
            "2026-2": {"tahsil": 10, "tarih": "2026-02-15"},
            "2026-3": {"tahsil": 10, "tarih": "2026-03-15"},
            "2026-4": {"tahsil": 10, "tarih": "2026-04-15"},
            "2026-5": {"tahsil": 10, "tarih": "2026-05-15"},
            "2026-6": {"tahsil": 10, "tarih": "2026-06-15"},
            "2026-7": {"tahsil": 10, "tarih": "2026-07-15"},
            "2026-8": {"tahsil": 10, "tarih": "2026-08-15"},
            "2026-9": {"tahsil": 10, "tarih": "2026-09-15"},
            "2026-10": {"tahsil": 10, "tarih": "2026-10-15"},
            "2026-11": {"tahsil": 10, "tarih": "2026-11-15"},
            "2026-12": {"tahsil": 10, "tarih": "2026-12-15"},
        }
    )
    page_html = f"""<!DOCTYPE html><html><body>
<input id="sozlesme_baslangic" value="2023-05-10">
<input id="sozlesme_bitis" value="">
<script>
var SOZLESME_TAM_ODENDI_TOLERANS=0.05;
var selectedId={SYN_MID};
function parseTarihStr(s){{var m=String(s||'').match(/(\\d{{4}})-(\\d{{1,2}})-(\\d{{1,2}})/);return m?new Date(+m[1],+m[2]-1,+m[3]):null;}}
function sozlesmeAylikAyKeyFromYilAy(y,a){{return y+'-'+a;}}
function girisPanelAyKeysCapFiltre(k){{return k;}}
{blok}
function seedEskiTakvimMap(){{
  // Eski UI: donemYil=takvim yılı → localStorage + bellek map Oca–Ara 2026
  var map = {eski_takvim_map};
  try {{
    localStorage.setItem('giris_tahsilat_yil_ay_detay_' + selectedId + '_2026', JSON.stringify(map));
    localStorage.setItem('warm_td', '1');
  }} catch (e) {{}}
  window.__tahsilatSatirYilAyDetay = window.__tahsilatSatirYilAyDetay || {{}};
  window.__tahsilatSatirYilAyDetay[2026] = map;
  window.__tahsilatSatirYilAyDetay['2026'] = map;
}}
function applyDelayed(ms){{
  return new Promise(function(resolve){{
    setTimeout(function(){{
      window.__sozlesmelerAylikSonCacheObj={{
        musteri_id:{SYN_MID}, reel_tufe_zincir:1, baslangic:'2023-05-10',
        aylar:(function(){{
          var o=[];
          function pushRange(y0,m0,n,brut){{
            var y=y0,m=m0;
            for(var i=0;i<n;i++){{ o.push({{yil:y,ay:m,brut_tutar_kdv:brut}}); m++; if(m>12){{m=1;y++;}} }}
          }}
          pushRange(2025,5,12,{API_2025});
          pushRange(2026,5,12,{API_2026});
          return o;
        }})()
      }};
      var k=girisTahsilatYilAyKeysFiltered(2026);
      var hasJan = (k||[]).some(function(x){{ return x==='2026-1'||x==='2026-01'; }});
      var hasApr = (k||[]).some(function(x){{ return x==='2026-4'||x==='2026-04'; }});
      resolve({{ n:k.length, first:k[0], last:k[k.length-1], k:k, hasJan:hasJan, hasApr:hasApr }});
    }}, ms);
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
            page1 = browser.new_page()
            page1.goto(url, wait_until="domcontentloaded")
            a = page1.evaluate("applyDelayed(80)")
            # d2-i warm: eski takvim-yılı kayıt map'i seed + KeysFiltered yine May–Nis
            page2 = browser.new_page()
            page2.goto(url, wait_until="domcontentloaded")
            page2.evaluate("seedEskiTakvimMap()")
            b = page2.evaluate("applyDelayed(140)")
            browser.close()
    finally:
        httpd.shutdown()
    check("g playwright kosustu", a is not None and b is not None)
    for label, data in (("temiz", a or {}), ("warm", b or {})):
        check(f"g {label} n12", data.get("n") == 12, data)
        check(f"g {label} May-Apr", str(data.get("first") or "").startswith("2026-5") and str(data.get("last") or "").startswith("2027-4"), data)
        check(f"g/d2-i {label} no Oca-Nis26", (not data.get("hasJan")) and (not data.get("hasApr")), data)


def main() -> int:
    for k in list(os.environ):
        if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_")) or k == "ALLOW_PROD_WRITE":
            del os.environ[k]
    test_a_z1_period_keys()
    test_b_year_nav_bounds()
    test_c_kayit_iso_keys()
    test_d_kalan()
    test_e_other_artis_ay()
    test_f_z0_birebir_head()
    test_g_playwright_delayed()
    print("TUMU GECTI" if not FAILS else "FAIL", FAILS)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
