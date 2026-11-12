# -*- coding: utf-8 -*-
"""Tümü Borç/Kalan: bugüne kadar (aktif) / son borç ayı (pasif).

Temiz ortam. Prod yazma yok. PII yok.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_")):
        os.environ.pop(k, None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:200]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _extract(src: str, name: str) -> str:
    m = re.search(rf"function {re.escape(name)}\s*\(", src)
    if not m:
        return ""
    i = m.start()
    depth = 0
    started = False
    for j in range(i, len(src)):
        ch = src[j]
        if ch == "{":
            depth += 1
            started = True
        elif ch == "}":
            depth -= 1
            if started and depth == 0:
                return src[i : j + 1]
    return src[i : i + 8000]


def test_kaynak():
    idx = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    for label, src in (("idx", idx), ("tjs", tjs)):
        check(f"{label}_helper", "function girisTahsilatTumuBorcCapYm" in src)
        check(f"{label}_cap_call_ozet", "capBugune: true" in src)
        check(f"{label}_fn_opts", "function girisTahsilatAyAyEtkinToplamlar(yearsFilter, opts)" in src)
        body = _extract(src, "girisTahsilatAyAyEtkinToplamlar")
        check(f"{label}_cap_borc", "sumBorc - sumAlacak" in body or "sumBorc-sumAlacak" in body.replace(" ", ""))
        check(f"{label}_yil_uncapped_call", "girisTahsilatAyAyEtkinToplamlar([yy])" in src)
        check(f"{label}_literal_bsn", (chr(92) + "n") not in body)
        py = _extract(src, "girisTahsilatParaYaz")
        check(f"{label}_para_neg", "Math.abs(x) < 0.005" in py)
    # mirror sync
    check("ayna_helper", "function girisTahsilatTumuBorcCapYm" in idx and "function girisTahsilatTumuBorcCapYm" in tjs)


def test_node_unit():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    helper = _extract(tjs, "girisTahsilatTumuBorcCapYm")
    fn = _extract(tjs, "girisTahsilatAyAyEtkinToplamlar")
    # minimal stubs
    js = r"""
var window = global;
window.__sozlesmelerAylikSonCacheObj = {
  aylar: [
    {yil:2026, ay:10, brut_tutar_kdv:20000, odenen_tutar_kdv:20000, tahsil_edildi:true},
    {yil:2026, ay:11, brut_tutar_kdv:20000, odenen_tutar_kdv:0, tahsil_edildi:false},
    {yil:2026, ay:12, brut_tutar_kdv:20000, odenen_tutar_kdv:5000, tahsil_edildi:false},
    {yil:2027, ay:1, brut_tutar_kdv:26246, odenen_tutar_kdv:0, tahsil_edildi:false}
  ]
};
function girisTahsilatYilYilListesi(){ return {years:[2026,2027]}; }
function girisTahsilatYilAyKeysFiltered(yy){
  if(yy===2026) return ['2026-10','2026-11','2026-12'];
  if(yy===2027) return ['2027-1'];
  return [];
}
function girisTahsilatAyKeyToYilAy(ak){
  var p=String(ak).split('-'); return {yil:parseInt(p[0],10), ay:parseInt(p[1],10)};
}
function girisAylikGridCacheAyBrut(y,m){
  var a=window.__sozlesmelerAylikSonCacheObj.aylar.find(function(x){return x.yil===y&&x.ay===m;});
  return a ? a.brut_tutar_kdv : 0;
}
function girisTahsilatYilAyPanelGridKaynagi(yy, ak){
  var ya=girisTahsilatAyKeyToYilAy(ak);
  var a=window.__sozlesmelerAylikSonCacheObj.aylar.find(function(x){return x.yil===ya.yil&&x.ay===ya.ay;});
  if(!a) return null;
  var kalan=Math.max(0, a.brut_tutar_kdv - a.odenen_tutar_kdv);
  return {brut:a.brut_tutar_kdv, odenen:a.odenen_tutar_kdv, kalan:kalan};
}
document = { getElementById: function(){ return {value:'aktif'}; } };
""" + helper + "\n" + fn + r"""
// force today cap Nov 2026
girisTahsilatTumuBorcCapYm = function(){ return {y:2026, m:11}; };
var t = girisTahsilatAyAyEtkinToplamlar([2026,2027], {capBugune:true});
if (Math.abs(t.borc - 40000) > 0.01) { console.log('FAIL borc', t); process.exit(1); }
if (Math.abs(t.alacak - 25000) > 0.01) { console.log('FAIL alacak', t); process.exit(1); }
if (Math.abs(t.kalan - 15000) > 0.01) { console.log('FAIL kalan', t); process.exit(1); }
var y = girisTahsilatAyAyEtkinToplamlar([2026]);
if (!(y.borc > 40000)) { console.log('FAIL yil uncapped', y); process.exit(1); }
console.log('ok unit', JSON.stringify(t), JSON.stringify(y));
"""
    fd, path = tempfile.mkstemp(suffix=".js", prefix="tumu_borc_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(js)
        r = subprocess.run(["node", path], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        check("node_unit", r.returncode == 0, (r.stdout + r.stderr)[:180])
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_mutasyon():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    body = _extract(tjs, "girisTahsilatAyAyEtkinToplamlar")
    mut = body.replace("capBugune", "capBuguneX", 1)
    check("mutasyon_fark", mut != body)
    # if cap removed from ozet call, test would fail
    check("mutasyon_call_var", "capBugune: true" in tjs)


def test_playwright_135():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    os.environ.pop("ALLOW_PROD_WRITE", None)
    for k in list(os.environ):
        if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_")):
            os.environ.pop(k, None)
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    os.environ["TAHSILAT_DONEM_GORUNUM"] = "1"
    os.environ["REEL_TUFE_ZINCIR_MIDLER"] = (
        "379,349,380,392,381,390,280,111,395,409,402,325,78,215,396,153,"
        "387,133,437,145,131,372,172,184,73,376,365,53,872,269,135,457,"
        "386,352,357,154,400,348,417,397,354,239,164,360,152,186,221,440,874,"
        "107,128,70,403,167,101124"
    )

    import app as app_module
    from auth import User
    from flask_login import current_user, login_user
    from werkzeug.serving import make_server
    from playwright.sync_api import sync_playwright

    app = getattr(app_module, "app", None) or app_module.create_app()
    app.config["WTF_CSRF_ENABLED"] = False

    @app.before_request
    def _auto():
        try:
            a = current_user.is_authenticated
            if (a() if callable(a) else a):
                return None
        except Exception:
            pass
        try:
            login_user(User(1, "olcum", "Olcum", "admin"), remember=False)
        except Exception:
            pass
        return None

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(80):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/giris/", timeout=2)
            break
        except Exception:
            time.sleep(0.2)

    BEK = {"borc": 412000.0, "alacak": 304000.0, "kalan": 108000.0}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(f"http://127.0.0.1:{port}/giris/?musteri_id=135", wait_until="domcontentloaded", timeout=180000)
        page.wait_for_function("() => typeof girisTahsilatAyAyEtkinToplamlar === 'function'", timeout=120000)
        # force select mid if needed
        page.evaluate(
            """async () => {
              if (typeof selectedId !== 'undefined') window.selectedId = 135;
              if (typeof selectMusteri === 'function') { try { await selectMusteri(135); } catch(e) {} }
              for (let i=0;i<40;i++){
                if (window.__sozlesmelerAylikSonCacheObj && Array.isArray(window.__sozlesmelerAylikSonCacheObj.aylar) && window.__sozlesmelerAylikSonCacheObj.aylar.length>5) break;
                await new Promise(r=>setTimeout(r,500));
              }
              if (typeof girisTahsilatOzetGuncelle === 'function') girisTahsilatOzetGuncelle();
              if (typeof girisTahsilatYilPanelDoldur === 'function') girisTahsilatYilPanelDoldur();
            }"""
        )
        page.wait_for_timeout(2500)
        info = page.evaluate(
            """() => {
              const ay = girisTahsilatAyAyEtkinToplamlar(null, {capBugune:true}) || {};
              const yil = girisTahsilatAyAyEtkinToplamlar([2026]) || {};
              const borcEl = document.getElementById('tahsilat_toplam_borc');
              const alEl = document.getElementById('tahsilat_toplam_tahsil');
              const kalEl = document.getElementById('tahsilat_kalan');
              function parseTR(el){
                if(!el) return null;
                const s=String(el.value||'').replace(/\\./g,'').replace(',','.');
                const n=parseFloat(s); return isFinite(n)?n:null;
              }
              // TBT/maxLt proxy: cell count in year panel months
              let cell_n = 0;
              const panel = document.getElementById('tahsilat_yil_panel');
              if (panel) cell_n = panel.querySelectorAll('input').length;
              return {
                ay, yil,
                ui: {borc: parseTR(borcEl), alacak: parseTR(alEl), kalan: parseTR(kalEl)},
                cell_n,
                hasCap: typeof girisTahsilatTumuBorcCapYm === 'function',
                n_aylar: (window.__sozlesmelerAylikSonCacheObj && window.__sozlesmelerAylikSonCacheObj.aylar || []).length
              };
            }"""
        )
        check("pw_has_cap", bool(info.get("hasCap")))
        check("pw_cell_n", int(info.get("cell_n") or 0) > 0, str(info.get("cell_n")))
        ay = info.get("ay") or {}
        check("pw_135_borc", abs(float(ay.get("borc") or 0) - BEK["borc"]) < 1.0, str(ay.get("borc")))
        check("pw_135_alacak", abs(float(ay.get("alacak") or 0) - BEK["alacak"]) < 1.0, str(ay.get("alacak")))
        check("pw_135_kalan", abs(float(ay.get("kalan") or 0) - BEK["kalan"]) < 1.0, str(ay.get("kalan")))
        yil = info.get("yil") or {}
        # yıl satırı (cap yok): 2026 plan borcu Tümü-cap'ten farklı olabilir; gelecek ay dahil
        yil_borc = float(yil.get("borc") or 0)
        check("pw_yil_2026_uncapped", yil_borc > 0, str(yil_borc))
        yil_cap = page.evaluate(
            """() => {
              const t = girisTahsilatAyAyEtkinToplamlar([2026], {capBugune:true}) || {};
              return t.borc || 0;
            }"""
        )
        check("pw_yil_2026_cap_le", float(yil_cap) <= yil_borc + 0.05, f"cap={yil_cap} full={yil_borc}")
        # TABAN eski: cap'siz tum yillar > 412000
        taban = page.evaluate(
            """() => {
              const t = girisTahsilatAyAyEtkinToplamlar([2025,2026,2027]) || {};
              return t.borc || 0;
            }"""
        )
        check("taban_eski_gt", float(taban) > BEK["borc"] + 1000, str(taban))
        browser.close()
    try:
        srv.shutdown()
    except Exception:
        pass


def main():
    test_kaynak()
    test_node_unit()
    test_mutasyon()
    test_playwright_135()
    if FAILS:
        print("FAIL", len(FAILS))
        for a in FAILS:
            print(" -", a)
        raise SystemExit(1)
    print("test_tumu_borc_bugune_kadar PASS")


if __name__ == "__main__":
    main()
