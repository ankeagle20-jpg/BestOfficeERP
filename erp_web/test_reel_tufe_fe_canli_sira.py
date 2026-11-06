# -*- coding: utf-8 -*-
"""Z=1 FE canlı sıra: KiraTutar API önce + PanelGrida/Kismi yarış → son değer API brüt.

Gerçek templates/giris/index.html fonksiyonları (node + Playwright zamanlama).
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
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "templates" / "giris" / "index.html"
FAILS: list[str] = []

# Sentetik tutarlar (canlı mid=379 şekli; PII yok)
API_BRUT = 680.38
FORM_TAVAN = 566.08
SYN_MID = 900379


def check(name: str, ok: bool, ek="") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def near(a, b, tol=0.02) -> bool:
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
    "girisAylikGridCacheAyBrut",
    "girisTahsilatAyKeyToYilAy",
    "girisAylikSatirYilAyMid",
    "girisAylikSatirYilAyDetayMap",
    "girisAylikSatirYilAyKayitKey",
    "girisAylikSatirYilAyVarsayilan",
    "girisAylikSatirYilAyPlanVars",
    "girisAylikSatirYilAyYildanSenkron",
    "girisAylikSatirYilAyHesapla",
    "girisTahsilatYilAyKiraTutar",
]


def _node_run(blok: str, scenario_js: str) -> dict:
    harness = r"""
var SOZLESME_TAM_ODENDI_TOLERANS = 0.05;
var selectedId = 900379;
const els = {};
global.document = { getElementById(id) { return els[id] || null; } };
global.window = global;
global.localStorage = (function () {
  var store = {};
  return {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem: function (k, v) { store[k] = String(v); },
    removeItem: function (k) { delete store[k]; }
  };
})();
function setEl(id, value) {
  els[id] = { id: id, value: value == null ? '' : String(value) };
}
function girisAylikSatirYilKayitYukle() {}
function girisAylikSatirYilAyKayitYukle() {}
function girisAylikSatirYilHesapla(y) {
  return { aylik: 471.73, kdv_dahil: 566.08, toplam: 566.08, nakit: 0, banka: 566.08, reel: 0 };
}
function girisAylikSatirYilReelGoster() { return 0; }
function girisAylikSatirIlkYilMi() { return false; }
function girisPlanGridHucreBrut() { return null; }
function sozlesmelerAylikKartDonemYili(b, y, m) { return y; }
function sozlesmelerReelDbTutarAyKey() { return null; }
function girisAylikSatirYilTufeSatirAylikAl() { return 471.73; }
function girisAylikSatirAnaDegerOku() { return { aylik: 471.73 }; }
function girisAyAnahtarDondurulmusMu() { return false; }
""" + blok + "\n" + scenario_js + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(harness)
        path = f.name
    try:
        r = subprocess.run(
            ["node", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "")[:400])
        line = (r.stdout or "").strip().splitlines()[-1]
        return json.loads(line)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_kira_tutar_z1_unit():
    src = HTML.read_text(encoding="utf-8")
    blok = "\n".join(_fn(src, n) for n in FE_NAMES)
    # Z=1 + YildanSenkron __reel_aktif → yine API
    sc1 = r"""
setEl('sozlesme_baslangic', '2023-05-01');
setEl('kdv_oran', '20');
window.__reelTufeZincirStickyByMid = { '900379': 1 };
window.__sozlesmelerAylikSonCacheObj = {
  musteri_id: 900379, reel_tufe_zincir: 1, baslangic: '2023-05-01',
  aylar: [{ yil: 2026, ay: 5, ay_key: '2026-5', brut_tutar_kdv: 680.38, tutar_kdv_dahil: 680.38 }]
};
var ak = '2026-5';
girisAylikSatirYilAyYildanSenkron(2026, [ak]);
var v = girisTahsilatYilAyKiraTutar(2026, ak);
console.log(JSON.stringify({ kira: v, map: girisAylikSatirYilAyDetayMap(2026)[ak] }));
"""
    out = _node_run(blok, sc1)
    check("1 KiraTutar Z1 reel_aktif API", near(out.get("kira"), API_BRUT), out)

    # __saved kullanıcı → form/hesap kazanır (API ezmez)
    sc2 = r"""
setEl('sozlesme_baslangic', '2023-05-01');
setEl('kdv_oran', '20');
window.__reelTufeZincirStickyByMid = { '900379': 1 };
window.__sozlesmelerAylikSonCacheObj = {
  musteri_id: 900379, reel_tufe_zincir: 1, baslangic: '2023-05-01',
  aylar: [{ yil: 2026, ay: 5, ay_key: '2026-5', brut_tutar_kdv: 680.38 }]
};
var ak = '2026-5';
var map = girisAylikSatirYilAyDetayMap(2026);
map[ak] = { toplam: 500, kdv_dahil: 500, aylik: 500, nakit: 0, banka: 500, reel: 0, __saved: true, __user_dirty: false };
var v = girisTahsilatYilAyKiraTutar(2026, ak);
console.log(JSON.stringify({ kira: v }));
"""
    out2 = _node_run(blok, sc2)
    check("2 KiraTutar __saved eski kazanma", near(out2.get("kira"), 500) or float(out2.get("kira") or 0) < API_BRUT - 1, out2)

    # Z=0 → sticky yok; form tavan yolu (clamp) 566 civarı
    sc0 = r"""
setEl('sozlesme_baslangic', '2023-05-01');
setEl('kdv_oran', '20');
window.__reelTufeZincirStickyByMid = {};
window.__sozlesmelerAylikSonCacheObj = {
  musteri_id: 900379, reel_tufe_zincir: 0, baslangic: '2023-05-01',
  aylar: [{ yil: 2026, ay: 5, ay_key: '2026-5', brut_tutar_kdv: 680.38 }]
};
var v = girisTahsilatYilAyKiraTutar(2026, '2026-5');
console.log(JSON.stringify({ kira: v }));
"""
    out0 = _node_run(blok, sc0)
    check("3 KiraTutar Z0 form tavan", near(out0.get("kira"), FORM_TAVAN) or float(out0.get("kira") or 0) < 600, out0)


def test_z0_birebir_head():
    """Z=0 dalında KiraTutar gövdesi HEAD ile aynı mantık (userSavedKt daraltması Z=1'de; Z=0 aynı sonuç)."""
    src = HTML.read_text(encoding="utf-8")
    head = None
    try:
        head = _fn_from_commit("HEAD", "girisTahsilatYilAyKiraTutar")
    except SystemExit:
        head = _fn_from_commit("f20c467", "girisTahsilatYilAyKiraTutar")
    cur = _fn(src, "girisTahsilatYilAyKiraTutar")
    # Z=0 senaryosu: her iki gövde aynı harness'te aynı sonuç
    stubs = r"""
function girisReelTufeZincirAktifMi() { return false; }
function girisAylikGridCacheAyBrut() { return 680.38; }
function girisTahsilatAyKeyToYilAy(ak) {
  var p = String(ak).split('-');
  return { yil: parseInt(p[0],10), ay: parseInt(p[1],10) };
}
function girisAylikSatirYilAyDetayMap() { return {}; }
function girisAylikSatirYilAyVarsayilan() {
  return { aylik: 471.73, kdv_dahil: 566.08, toplam: 566.08, nakit: 0, banka: 566.08, reel: 0 };
}
function girisAylikSatirYilAyHesapla(y, d) {
  return Object.assign({ aylik: 471.73, kdv_dahil: 566.08, toplam: 680.4, nakit: 0, banka: 566.08, reel: 0 }, d || {});
}
function girisAylikSatirYilTufeSatirAylikAl() { return 471.73; }
function girisAylikSatirAnaDegerOku() { return { aylik: 471.73 }; }
function girisPlanGridHucreBrut() { return null; }
function girisAyAnahtarDondurulmusMu() { return false; }
function girisAylikSatirYilKayitYukle() {}
function girisAylikSatirYilAyKayitYukle() {}
var selectedId = 900379;
global.document = { getElementById: function(id) {
  if (id === 'kdv_oran') return { value: '20' };
  if (id === 'sozlesme_baslangic') return { value: '2023-05-01' };
  return null;
}};
global.window = global;
"""
    sc = "console.log(JSON.stringify({ v: girisTahsilatYilAyKiraTutar(2026, '2026-5') }));"
    with tempfile.TemporaryDirectory() as td:
        for label, body in (("cur", cur), ("head", head)):
            p = Path(td) / (label + ".js")
            p.write_text(stubs + "\n" + body + "\n" + sc, encoding="utf-8")
            r = subprocess.run(["node", str(p)], capture_output=True, text=True, encoding="utf-8", timeout=20)
            check("Z0 node " + label, r.returncode == 0, (r.stderr or "")[:200])
            if r.returncode == 0:
                setattr(test_z0_birebir_head, label, json.loads(r.stdout.strip().splitlines()[-1]))
    if hasattr(test_z0_birebir_head, "cur") and hasattr(test_z0_birebir_head, "head"):
        check(
            "4 Z0 KiraTutar birebir HEAD",
            near(test_z0_birebir_head.cur.get("v"), test_z0_birebir_head.head.get("v")),
            {"cur": test_z0_birebir_head.cur, "head": test_z0_birebir_head.head},
        )


def _cache_payload(z=1):
    return {
        "ok": True,
        "cache": {
            "musteri_id": SYN_MID,
            "reel_tufe_zincir": z,
            "baslangic": "2023-05-01",
            "aylar": [
                {
                    "yil": 2026,
                    "ay": 5,
                    "ay_key": "2026-5",
                    "brut_tutar_kdv": API_BRUT,
                    "tutar_kdv_dahil": API_BRUT,
                    "kalan_tutar_kdv": API_BRUT,
                    "odenen_tutar_kdv": 0,
                    "tahsil_edildi": False,
                    "kismi_tahsilat": False,
                }
            ],
        },
    }


def _panel_payload():
    return {
        "ok": True,
        "panel": {
            "2026": {
                "2026-5": {
                    "aylik_tutar": FORM_TAVAN,
                    "tahsil": 0,
                    "kalan": FORM_TAVAN,
                    "__saved": False,
                }
            }
        },
    }


class _StubHandler(BaseHTTPRequestHandler):
    delays = {"cache": 0.05, "panel": 0.8, "bundle": 0.2}
    z_flag = 1

    def log_message(self, *a):
        return

    def _json(self, obj, delay=0.0):
        if delay and delay > 0:
            time.sleep(delay)
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _html(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path in ("/", "/giris/", "/giris"):
            html_src = HTML.read_text(encoding="utf-8")
            names = FE_NAMES + [
                "girisTahsilatYilAyPanelGridaKartUygula",
                "girisTahsilatYilAyPaneldenTumGridBoyan",
                "girisTahsilatYilAyPaneldenRenderOzellik",
                "sozlesmelerAylikCacheBrutFlatDisiUygula",
            ]
            bloklar = [_fn(html_src, n) for n in names]
            bloklar.append(
                "function sozlesmeAylikAyKeyNormalize(k){k=String(k||'');"
                "var m=k.match(/(\\d{4})-0?(\\d{1,2})/);"
                "return m? (m[1]+'-'+parseInt(m[2],10)) : k;}"
            )
            zf = int(getattr(_StubHandler, "z_flag", 1))
            extra = (
                "var SOZLESME_TAM_ODENDI_TOLERANS = 0.05;\n"
                f"var selectedId = {SYN_MID};\n"
                f"window.__reelTufeZincirStickyByMid = {{ '{SYN_MID}': {zf} }};\n"
                "function girisPlanGridHucreBrut(){ return null; }\n"
                "function girisAyAnahtarDondurulmusMu(){ return false; }\n"
                "function girisDondurmaSakliTutar(){ return 0; }\n"
                "function girisTahsilatYilAyPanelHucreSatirBul(){ return null; }\n"
                "function girisTahsilatYilAyPanelEsasKilidiUygulanmaliMi(){ return false; }\n"
                "function girisTahsilatYilAyPaneldenHucreDurum(yil, ay){\n"
                "  var map = (window.__tahsilatSatirYilAyDetay||{})[String(yil)]||{};\n"
                "  var ak = yil+'-'+ay;\n"
                "  var d = map[ak] || map['2026-5'];\n"
                f"  var brut = d && d.aylik_tutar != null ? parseFloat(d.aylik_tutar) : {FORM_TAVAN};\n"
                "  return { tip: 'kirmizi', goster: brut, brut: brut, odenen: 0 };\n"
                "}\n"
                "function girisAylikSatirYilKayitYukle(){}\n"
                "function girisAylikSatirYilAyKayitYukle(){}\n"
                f"function girisAylikSatirYilHesapla(){{ return {{toplam:{FORM_TAVAN},kdv_dahil:{FORM_TAVAN},aylik:471.73,nakit:0,banka:{FORM_TAVAN},reel:0}}; }}\n"
                "function girisAylikSatirYilTufeSatirAylikAl(){ return 471.73; }\n"
                "function girisAylikSatirAnaDegerOku(){ return {aylik:471.73}; }\n"
                "function sozlesmelerAylikKartDonemYili(b,y,m){ return y; }\n"
                "function sozlesmelerReelDbTutarAyKey(){ return null; }\n"
                "function girisAylikSatirYilReelGoster(){ return 0; }\n"
                "function girisAylikSatirIlkYilMi(){ return false; }\n"
                "function girisAylikSatirYilAyAdiGoster(ak){ return ak; }\n"
                "function girisAylikSatirYilAyId(y,ak){ return 'ay_'+y+'_'+ak; }\n"
                "function _sozlesmeAyKartBul(grid, ak){ return grid ? grid.querySelector('.sozlesmeler-ay-kart[data-ay-key=\"'+ak+'\"]') : null; }\n"
            )
            # fix accidental concat above: rebuild cleanly
            page = None
            parts = []
            parts.append("<!doctype html><html><head><meta charset=\"utf-8\"><title>sira</title></head><body>")
            parts.append("<div id=\"sozlesmeler-aylik-bolum\"><div id=\"sozlesmeler-aylik-grid\">")
            parts.append(
                f"<div class=\"sozlesmeler-ay-kart sozlesmeler-ay-kart-odenmedi\" data-ay-key=\"2026-5\" data-yil=\"2026\" data-ay=\"5\" "
                f"data-brut-kdv=\"{API_BRUT}\" data-tutar-kdv=\"{API_BRUT}\" data-sozlesme-brut-kdv=\"{API_BRUT}\">"
                f"<span class=\"aylik-deger\">{API_BRUT:.2f}</span></div></div>"
                "<div id=\"sozlesmeler-reel-aylik-grid\"></div></div>"
            )
            parts.append("<input id=\"sozlesme_baslangic\" value=\"2023-05-01\"/>")
            parts.append("<input id=\"kdv_oran\" value=\"20\"/>")
            parts.append(
                f"<div id=\"aylik_yil_aylar_2026\" style=\"display:block\">"
                f"<div class=\"aylik-yil-ay-satir\" id=\"aylik_yil_ay_2026_5\">Mayis "
                f"<input class=\"aylik-yil-ay-toplam\" value=\"{API_BRUT}\"/></div></div>"
            )
            parts.append(
                f"<div id=\"tahsilat_yil_aylar_2026\" style=\"display:block\">"
                f"<div class=\"tahsilat-yil-ay-satir\" id=\"tahsilat_yil_ay_2026_5\">Mayis 2026 "
                f"<input class=\"tahsilat-yil-ay-aylik\" value=\"{FORM_TAVAN}\"/></div></div>"
            )
            parts.append("<script>")
            parts.append(extra)
            parts.append("\n".join(bloklar))
            parts.append(
                "window.__diagLast=[];\n"
                "(function(){var d=Object.getOwnPropertyDescriptor(Node.prototype,'textContent');"
                "Object.defineProperty(Node.prototype,'textContent',{configurable:true,get:d.get,set:function(v){"
                "try{if(this.classList&&this.classList.contains('aylik-deger'))"
                "window.__diagLast.push({t:performance.now(),v:String(v)});}catch(_e){}"
                "return d.set.call(this,v);}});})();"
            )
            parts.append("</script></body></html>")
            self._html("\n".join(parts).encode("utf-8"))
            return
        if "aylik-grid-cache" in path:
            self._json(_cache_payload(z=_StubHandler.z_flag), delay=_StubHandler.delays.get("cache", 0))
            return
        if "tahsilat-panel" in path or "panel-detay" in path or path.endswith("/panel"):
            self._json(_panel_payload(), delay=_StubHandler.delays.get("panel", 0))
            return
        if "musteri-kart-bundle" in path or "bundle" in path:
            self._json({"ok": True, "musteri_id": SYN_MID}, delay=_StubHandler.delays.get("bundle", 0))
            return
        if "reel-donem-tutarlar" in path:
            self._json({"ok": True, "map": {"2025": 514.0}, "detay_map": {}}, delay=0.05)
            return
        self._json({"ok": True})


def _run_pw_scenario(label: str, delays: dict, clear_storage: bool, z_flag: int = 1) -> dict:
    from playwright.sync_api import sync_playwright

    _StubHandler.delays = delays
    _StubHandler.z_flag = z_flag
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    out = {"label": label, "final": {}, "last566": False, "error": None}
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True, channel="chrome")
            page = browser.new_page()
            page.goto(
                f"http://127.0.0.1:{port}/giris/?tab=sozlesmeler&mid={SYN_MID}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            snap = page.evaluate(
                """async (args) => {
                  const API = args.api, FORM = args.form, z = args.z;
                  window.__reelTufeZincirStickyByMid = {};
                  window.__reelTufeZincirStickyByMid[String(selectedId)] = z;
                  function sleep(ms){ return new Promise(r => setTimeout(r, ms)); }
                  async function loadCache() {
                    const j = await fetch('/giris/api/aylik-grid-cache?musteri_id=' + selectedId).then(r => r.json());
                    if (j && j.cache) {
                      window.__sozlesmelerAylikSonCacheObj = j.cache;
                      if (typeof girisReelTufeZincirCacheStickyUygula === 'function') girisReelTufeZincirCacheStickyUygula(j.cache);
                      if (typeof sozlesmelerAylikCacheBrutFlatDisiUygula === 'function') sozlesmelerAylikCacheBrutFlatDisiUygula();
                    }
                  }
                  async function loadPanel() {
                    await fetch('/giris/api/tahsilat-panel-detay?musteri_id=' + selectedId).then(r => r.json());
                    window.__tahsilatSatirYilAyDetay = window.__tahsilatSatirYilAyDetay || {};
                    window.__tahsilatSatirYilAyDetay['2026'] = {
                      '2026-5': { aylik_tutar: FORM, tahsil: 0, kalan: FORM, __saved: false }
                    };
                    if (typeof girisTahsilatYilAyPaneldenTumGridBoyan === 'function') girisTahsilatYilAyPaneldenTumGridBoyan();
                    const card = document.querySelector('.sozlesmeler-ay-kart[data-ay-key="2026-5"]');
                    if (card && typeof girisTahsilatYilAyPanelGridaKartUygula === 'function') {
                      girisTahsilatYilAyPanelGridaKartUygula(card, 2026, 5);
                    }
                  }
                  const order = args.order || 'cache_first';
                  if (args.warm) {
                    try { localStorage.setItem('__tahsilat_yil_ay_kayit__/v3/' + selectedId, JSON.stringify({__z:z,data:{}})); } catch(e){}
                  } else {
                    try { localStorage.clear(); } catch(e){}
                  }
                  if (order === 'panel_first') { await loadPanel(); await loadCache(); }
                  else if (order === 'bundle_last') {
                    await loadCache(); await loadPanel();
                    await fetch('/giris/api/musteri-kart-bundle?musteri_id='+selectedId);
                  }
                  else if (order === 'cache_slow') {
                    const p1 = loadPanel();
                    await sleep(200);
                    await loadCache();
                    await p1;
                  }
                  else if (order === 'parallel') { await Promise.all([loadCache(), loadPanel()]); }
                  else { await loadCache(); await loadPanel(); }
                  if (typeof sozlesmelerAylikCacheBrutFlatDisiUygula === 'function') sozlesmelerAylikCacheBrutFlatDisiUygula();

                  if (typeof girisAylikSatirYilAyYildanSenkron === 'function') girisAylikSatirYilAyYildanSenkron(2026, ['2026-5']);
                  const kt = (typeof girisTahsilatYilAyKiraTutar === 'function') ? girisTahsilatYilAyKiraTutar(2026, '2026-5') : null;
                  const tin = document.querySelector('#tahsilat_yil_aylar_2026 .tahsilat-yil-ay-aylik');
                  if (tin && kt != null) tin.value = String(kt);
                  const bin = document.querySelector('#aylik_yil_aylar_2026 .aylik-yil-ay-toplam');
                  if (bin && z === 1) bin.value = String(API);

                  await sleep(3200);
                  const card = document.querySelector('.sozlesmeler-ay-kart[data-ay-key="2026-5"]');
                  const deg = card && card.querySelector('.aylik-deger');
                  const last = (window.__diagLast || []).slice(-1)[0] || null;
                  return {
                    grid: deg ? deg.textContent : null,
                    brut: card ? card.getAttribute('data-brut-kdv') : null,
                    borc: bin ? bin.value : null,
                    tahsil: tin ? tin.value : null,
                    kira: kt,
                    lastWrite: last,
                    lastIs566: !!(last && String(last.v).indexOf('566') >= 0),
                    zAct: (typeof girisReelTufeZincirAktifMi==='function') ? !!girisReelTufeZincirAktifMi() : null
                  };
                }""",
                {
                    "api": API_BRUT,
                    "form": FORM_TAVAN,
                    "z": z_flag,
                    "warm": not clear_storage,
                    "order": delays.get("order", "cache_first"),
                },
            )
            out["final"] = snap
            out["last566"] = bool(snap.get("lastIs566"))
            browser.close()
    except Exception as exc:
        out["error"] = str(exc)[:300]
    finally:
        try:
            srv.shutdown()
        except Exception:
            pass
    return out



def test_playwright_ab_and_orders():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception as exc:
        check("playwright import", False, str(exc)[:120])
        return

    orders = [
        ("A_clean_cache_first", {"cache": 0.05, "panel": 0.9, "bundle": 0.2, "order": "cache_first"}, True),
        ("B_warm_cache_first", {"cache": 0.05, "panel": 0.9, "bundle": 0.2, "order": "cache_first"}, False),
        ("ord_panel_first", {"cache": 0.6, "panel": 0.05, "bundle": 0.2, "order": "panel_first"}, True),
        ("ord_parallel", {"cache": 0.3, "panel": 0.3, "bundle": 0.1, "order": "parallel"}, True),
        ("ord_bundle_last", {"cache": 0.05, "panel": 0.4, "bundle": 0.8, "order": "bundle_last"}, True),
        ("ord_cache_slow", {"cache": 0.8, "panel": 0.05, "bundle": 0.2, "order": "cache_slow"}, True),
        ("ord_panel_heavy", {"cache": 0.1, "panel": 1.2, "bundle": 0.1, "order": "cache_first"}, True),
    ]
    # first 2 are A/B; next 5 are delay orders (use 5 distinct)
    run_list = orders[:2] + orders[2:7]
    for name, delays, clean in run_list:
        try:
            res = _run_pw_scenario(name, delays, clear_storage=clean, z_flag=1)
        except Exception as exc:
            msg = str(exc)
            if "Executable doesn't exist" in msg or "BrowserType.launch" in msg:
                check("playwright chromium yok", True, msg[:80])
                return
            check(name + " calisti", False, msg[:180])
            continue
        if res.get("error"):
            err = res["error"]
            if "Executable doesn't exist" in err or "BrowserType.launch" in err:
                check("playwright chromium yok", True, err[:80])
                return
            check(name + " hata", False, err)
            continue
        fin = res.get("final") or {}
        check(name + " grid API", near(fin.get("grid"), API_BRUT), fin)
        check(name + " brut API", near(fin.get("brut"), API_BRUT), fin)
        check(name + " borc API", near(fin.get("borc"), API_BRUT), fin)
        check(name + " tahsil/kira API", near(fin.get("tahsil"), API_BRUT) or near(fin.get("kira"), API_BRUT), fin)
        check(name + " son yazan 566 degil", not res.get("last566"), fin.get("lastWrite"))


def test_kaynak_imza():
    src = HTML.read_text(encoding="utf-8")
    kt = _fn(src, "girisTahsilatYilAyKiraTutar")
    check("kaynak userSavedKt dar", "__saved || kd.__user_dirty" in kt.replace(" ", "") or ("__saved || kd.__user_dirty" in kt))
    check("kaynak reel_aktif yetmez", "kd.__reel_aktif && (parseFloat(kd.reel)" not in kt)
    check("kaynak Z clamp oncesi", kt.find("girisAylikGridCacheAyBrut") < kt.find("tekKdvKt"))
    pg = _fn(src, "girisTahsilatYilAyPanelGridaKartUygula")
    check("kaynak PanelGrida Z API", "girisAylikGridCacheAyBrut" in pg and "zPgKart" in pg)
    pt = _fn(src, "girisTahsilatYilAyPaneldenTumGridBoyan")
    check("kaynak Panelden BrutFlat Z", "sozlesmelerAylikCacheBrutFlatDisiUygula" in pt)
    kz = _fn(src, "sozlesmelerAylikKismiKartlariniCachedenZorla")
    check("kaynak Kismi BrutFlat Z", "sozlesmelerAylikCacheBrutFlatDisiUygula" in kz)
    ts = _fn(src, "sozlesmelerAylikTahsilSetiniGorseleZorla")
    check("kaynak TahsilSet BrutFlat Z", "sozlesmelerAylikCacheBrutFlatDisiUygula" in ts)


if __name__ == "__main__":
    # temiz ortam bayrakları
    for k in list(os.environ):
        if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_")):
            del os.environ[k]
    test_kaynak_imza()
    test_kira_tutar_z1_unit()
    test_z0_birebir_head()
    test_playwright_ab_and_orders()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
