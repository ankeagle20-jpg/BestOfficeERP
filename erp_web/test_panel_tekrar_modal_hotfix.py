# -*- coding: utf-8 -*-
"""HOTFIX: Borçlandır modal CSS/inline/görünürlük fallback + sonuç mesajı."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:180]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_css_blok():
    i = HTML.find("<style>")
    j = HTML.find("</style>")
    stil = HTML[i:j] if i >= 0 and j > i else ""
    check("css marker stilde", "panel-tekrar-modal-css" in stil)
    check("css overlay stilde", ".panel-tekrar-modal-overlay" in stil and "position:fixed" in stil)
    check("css z-index", "z-index:10050" in stil or "z-index: 10050" in stil)
    check("css kutu", ".panel-tekrar-modal-kutu" in stil)
    check("inline stil fn", "function panelTekrarModalInlineStil" in HTML)
    check("gorunur fn", "function panelTekrarModalGorunurMu" in HTML)
    check("sonuc fn", "function panelTekrarSonucGoster" in HTML)
    check("disari kapatmaz", "KAPATMAZ" in HTML)
    # eski substring-only alert kalkmış
    check("substring alert yok", "indexOf('Başka kaynaklı')" not in HTML)
    check("tahsil confirm duruyor", "tahsil-et" in HTML and "confirm(parcalarT.join" in HTML)


def test_node_fallback_ve_sonuc():
    node = shutil.which("node")
    if not node:
        check("node var", False, "node yok")
        return
    bas = HTML.find("function panelTekrarModalListeHtml")
    son = HTML.find("function panelTekrarGonder")
    helpers = HTML[bas:son]
    betik = helpers + r"""
var alerts = [];
var confirms = [];
var alert = function(m){ alerts.push(String(m)); };
var confirm = function(m){ confirms.push(String(m)); return true; };
var rAF = function(fn){ fn(); };
var requestAnimationFrame = rAF;

// --- 1) görünmez → confirm fallback ---
var lastWrap = null;
var document = {
  body: { appendChild: function(el){ lastWrap = el; } },
  getElementById: function(){ return null; },
  createElement: function(){
    return {
      id:'', className:'', style:{}, innerHTML:'',
      setAttribute: function(){},
      querySelector: function(sel){
        if (sel.indexOf('tamam') >= 0) return { disabled:false, addEventListener:function(){}, getBoundingClientRect:function(){return {left:0,top:0,width:0,height:0};}, contains:function(){return false;} };
        if (sel.indexOf('iptal') >= 0) return { addEventListener:function(){} };
        return null;
      },
      addEventListener: function(){},
      remove: function(){ lastWrap=null; },
      getBoundingClientRect: function(){ return {left:0,top:0,width:0,height:0}; },
      contains: function(){ return false; }
    };
  },
  addEventListener: function(){},
  removeEventListener: function(){},
  elementFromPoint: function(){ return null; }
};
var window = { getComputedStyle: function(){ return { position: 'static' }; } };
panelTekrarModalAc({
  soru: '12 ay?',
  j: { ok:true, onay_gerekli:false, mesaj:'DEĞİŞMEYECEK 12 ay', silinecek_grid:[], silinecek_eski:[], degismeyecek:[{yil:2025,ay:7,neden:'baska_kaynak'}] }
}).then(function(k){
  if (!k || !k.ok || !k.fallback) { console.log('FAIL_FALLBACK', k); process.exit(2); }
  if (confirms.length !== 1) { console.log('FAIL_CONFIRM_N', confirms.length); process.exit(3); }
  if (String(confirms[0]).indexOf('DEĞİŞMEYECEK') < 0) { console.log('FAIL_CONFIRM_TXT'); process.exit(4); }

  // --- 2) görünür modal: Tamam ---
  confirms = [];
  var tamamHandler = null;
  var btn = {
    disabled: false,
    addEventListener: function(ev, fn){ if (ev==='click') tamamHandler = fn; },
    getBoundingClientRect: function(){ return {left:100,top:100,width:80,height:30}; },
    contains: function(el){ return el===btn; }
  };
  var wrapEl = {
    id:'panelTekrarModal', className:'', style:{}, innerHTML:'',
    setAttribute: function(){},
    querySelector: function(sel){
      if (sel.indexOf('tamam') >= 0) return btn;
      if (sel.indexOf('iptal') >= 0) return { addEventListener:function(){} };
      return null;
    },
    addEventListener: function(){},
    remove: function(){},
    getBoundingClientRect: function(){ return {left:0,top:0,width:400,height:300}; },
    contains: function(el){ return el===btn || el===wrapEl; }
  };
  document.createElement = function(){ return wrapEl; };
  document.body.appendChild = function(el){ lastWrap = el; };
  document.elementFromPoint = function(){ return btn; };
  window.getComputedStyle = function(){ return { position: 'fixed' }; };
  panelTekrarModalAc({
    soru: 'ok',
    j: { mesaj:'görünür', silinecek_grid:[], silinecek_eski:[], degismeyecek:[] }
  }).then(function(k2){
    if (confirms.length !== 0) { console.log('FAIL_NO_FALLBACK_WHEN_VISIBLE', confirms); process.exit(5); }
    if (!k2 || !k2.ok || k2.fallback) { console.log('FAIL_VISIBLE', k2); process.exit(6); }

    // --- 3) sonuç mesajı her ok ---
    alerts = [];
    panelTekrarSonucGoster({ ok:true, mesaj:'12 satır atlandı. Başka kaynaklı...' });
    panelTekrarSonucGoster({ ok:true, olusturulan:[{}], atlanan:[{neden:'baska_kaynak'}], tahsil_silinen:[] });
    panelTekrarSonucGoster({ ok:false, iptal:true, mesaj:'gizle' });
    if (alerts.length !== 2) { console.log('FAIL_ALERTS', alerts); process.exit(7); }
    if (alerts[0].indexOf('atlandı') < 0) { console.log('FAIL_A0', alerts[0]); process.exit(8); }
    if (alerts[1].indexOf('atlandı') < 0 && alerts[1].indexOf('oluştur') < 0) { console.log('FAIL_A1', alerts[1]); process.exit(9); }

    // --- 4) inline stil set ---
    var el = { style: {} };
    panelTekrarModalInlineStil(el);
    if (el.style.position !== 'fixed' || String(el.style.zIndex) !== '10050') {
      console.log('FAIL_INLINE', el.style); process.exit(10);
    }
    console.log('NODE_OK');
    process.exit(0);
  });
  if (tamamHandler) tamamHandler();
  else { console.log('NO_TAMAM'); process.exit(11); }
});
"""
    r = subprocess.run([node, "-e", betik], capture_output=True, text=True)
    check("node gorunurluk+fallback+sonuc", r.returncode == 0, (r.stderr or r.stdout or "")[:200])
    check("node stdout OK", "NODE_OK" in (r.stdout or ""), r.stdout[-200:] if r.stdout else "")


def test_playwright_opsiyonel():
    """Yerelde playwright/chromium varsa sahte HTML ile Tamam görünürlüğünü doğrula."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        check("playwright atlandi", True, "kurulu degil — opsiyonel")
        return
    css_i = HTML.find("/* panel-tekrar-modal-css")
    css_j = HTML.find("</style>")
    css = HTML[css_i:css_j] if css_i >= 0 else ""
    bas = HTML.find("function panelTekrarModalListeHtml")
    son = HTML.find("function panelTekrarGonder")
    js = HTML[bas:son]
    page_html = f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>
<body style="margin:0;min-height:200vh;background:#eee">
<script>
{js}
window.__karar = null;
panelTekrarModalAc({{
  soru: 'SAHTE 12 ay test (PII yok)',
  j: {{
    ok: true, onay_gerekli: false, panel_n: 0,
    mesaj: 'Hiçbir tahsilat silinmeyecek. DEĞİŞMEYECEK 2 ay: 2025-07 (baska_kaynak), 2025-08 (baska_kaynak).',
    silinecek_grid: [], silinecek_eski: [],
    degismeyecek: [{{yil:2025,ay:7,neden:'baska_kaynak'}},{{yil:2025,ay:8,neden:'baska_kaynak'}}]
  }}
}}).then(function(k){{ window.__karar = k; }});
</script></body></html>"""
    shot = ROOT / "_tmp_modal_hotfix_shot.png"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True, channel="chrome")
            page = browser.new_page(viewport={"width": 900, "height": 700})
            page.set_content(page_html, wait_until="domcontentloaded")
            page.wait_for_timeout(200)
            btn = page.locator(".panel-tekrar-modal-tamam")
            visible = btn.is_visible()
            box = btn.bounding_box() if visible else None
            page.screenshot(path=str(shot), full_page=False)
            browser.close()
        check("playwright Tamam visible", visible and box and box.get("width", 0) > 10, box)
        check("playwright screenshot", shot.exists() and shot.stat().st_size > 1000)
    except Exception as exc:
        msg = str(exc)
        if "Executable doesn't exist" in msg or "BrowserType.launch" in msg:
            check("playwright atlandi", True, "chromium yok — opsiyonel")
        else:
            check("playwright calisti", False, msg[:160])


if __name__ == "__main__":
    test_css_blok()
    test_node_fallback_ve_sonuc()
    test_playwright_opsiyonel()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
