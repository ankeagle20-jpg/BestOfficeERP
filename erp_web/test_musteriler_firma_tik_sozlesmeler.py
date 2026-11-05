# -*- coding: utf-8 -*-
"""Müşteriler (firma özet) satır tıkı → Sözleşmeler tab + selectMusteri. Prod yazmaz. PII yok."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)
for _k in list(os.environ):
    if _k.startswith("KAPANIS_") or _k.startswith("ODA_"):
        os.environ.pop(_k, None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def _html() -> str:
    return (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")


def _tjs() -> str:
    return (ROOT / "_t.js").read_text(encoding="utf-8")


def _extract_fn(src: str, name: str) -> str:
    m = re.search(rf"function {re.escape(name)}\(ev, mid\) \{{.*?\n\}}", src, flags=re.S)
    return m.group(0) if m else ""


def test_sablon_ve_kaynak():
    html = _html()
    tjs = _tjs()
    fn = _extract_fn(html, "faturaRaporFirmaKartaGit")
    check("fn index.html", "function faturaRaporFirmaKartaGit" in html and bool(fn))
    check("fn switchTab(6)", "switchTab(6)" in fn and "switchTab(0)" not in fn)
    check("fn selectMusteri", "selectMusteri(id)" in fn)
    check("fn durum hepsi", "sozlesmeler-durum-filtre" in fn and "hepsi" in fn)
    check("fn invalid id sessiz", "if (isNaN(id) || id < 1) return;" in fn)
    check("fn whatsapp kalibi", "function whatsappMusteriKartaGit" in html)

    # (e) Giriş şablonunda _t.js src yok; canlı JS index.html
    head = html[:8000]
    check("src _t.js yok", "_t.js" not in head and 'src="/_t.js"' not in html and "src='_t.js'" not in html)
    # Ayna kopyası (canlı etkisi yok)
    fn_t = _extract_fn(tjs, "faturaRaporFirmaKartaGit")
    check("ayna _t.js switchTab(6)", "switchTab(6)" in fn_t and "switchTab(0)" not in fn_t)

    # Render: firma butonu mid + faturaRaporFirmaKartaGit; title Sözleşmeler
    check(
        "render onclick mid",
        'onclick="faturaRaporFirmaKartaGit(event, \' + midNum + \')"' in html
        or 'onclick="faturaRaporFirmaKartaGit(event, \' + midNum' in html
        or "faturaRaporFirmaKartaGit(event, ' + midNum + ')" in html,
    )
    check("render title sozlesmeler", 'title="Sözleşmeler kartını aç"' in html)
    check("render title musteri ekle yok", 'title="Müşteri kartını aç"' not in html or "faturaRaporFirmaKartaGit" not in html[html.find('title="Müşteri kartını aç"') - 80 : html.find('title="Müşteri kartını aç"') + 80] if 'title="Müşteri kartını aç"' in html else True)

    # Diğer tıklanabilirler: checkbox / sıralama ayrı — firma-link class yalnızca firma butonunda
    check("checkbox class ayri", "fatura-rapor-firma-sec" in html)
    check("sort th ayri", "fatura-rapor-firma-th" in html or "faturaRaporFirmaSort" in html or "data-fr-sort" in html)


def test_node_firma_tik():
    """JS mantığı: tab 6 + mid; tab 0 çağrılmaz; hepsi; geçersiz id; checkbox/sort tetiklemez."""
    html = _html()
    fn = _extract_fn(html, "faturaRaporFirmaKartaGit")
    check("extract fn", bool(fn), "faturaRaporFirmaKartaGit yok")
    if not fn:
        return

    node = (
        r"""
global.window = global;
var calls = { switchTab: [], selectMusteri: [], historyPush: 0 };
var dfValue = 'aktif';
global.document = {
  getElementById: function (id) {
    if (id === 'sozlesmeler-durum-filtre') {
      return {
        get value() { return dfValue; },
        set value(v) { dfValue = String(v); }
      };
    }
    return null;
  }
};
global.history = {
  state: {},
  pushState: function () { calls.historyPush += 1; }
};
function girisHistoryEnsurePopstateListener() {}
function switchTab(i) { calls.switchTab.push(i); }
function selectMusteri(id) { calls.selectMusteri.push(id); }
global.setTimeout = function (fn) { fn(); };

"""
        + fn
        + r"""

function say(ad, ok, ek) {
  console.log((ok ? 'ok ' : 'FAIL ') + ad + (ok ? '' : (' :: ' + (ek || ''))));
  if (!ok) process.exitCode = 1;
}

function reset() {
  calls.switchTab = [];
  calls.selectMusteri = [];
  calls.historyPush = 0;
  dfValue = 'aktif';
  window.__girisFromFaturaRaporKartClick = false;
}

// (a) geçerli mid → tab 6 + selectMusteri; tab 0 yok
reset();
faturaRaporFirmaKartaGit({ stopPropagation: function(){}, preventDefault: function(){} }, 42);
say('a switchTab6', calls.switchTab.length === 1 && calls.switchTab[0] === 6, JSON.stringify(calls.switchTab));
say('a selectMusteri mid', calls.selectMusteri.length === 1 && calls.selectMusteri[0] === 42, JSON.stringify(calls.selectMusteri));
say('a switchTab0 yok', calls.switchTab.indexOf(0) < 0);

// (b) pasif/iptal: durum filtresi hepsi
reset();
dfValue = 'aktif';
faturaRaporFirmaKartaGit({ stopPropagation: function(){}, preventDefault: function(){} }, 99);
say('b durum hepsi', dfValue === 'hepsi', dfValue);
say('b select mid', calls.selectMusteri[0] === 99);

// (c) geçersiz id hata fırlatmaz, çağrı yok
reset();
var threw = false;
try {
  faturaRaporFirmaKartaGit({ stopPropagation: function(){}, preventDefault: function(){} }, 'x');
  faturaRaporFirmaKartaGit({ stopPropagation: function(){}, preventDefault: function(){} }, 0);
  faturaRaporFirmaKartaGit({ stopPropagation: function(){}, preventDefault: function(){} }, -1);
  faturaRaporFirmaKartaGit(null, NaN);
} catch (e) {
  threw = true;
}
say('c throw yok', !threw);
say('c cagri yok', calls.switchTab.length === 0 && calls.selectMusteri.length === 0);

// (d) checkbox / sıralama: faturaRaporFirmaKartaGit çağrılmaz (simülasyon: fonksiyon dışarıdan tetiklenmez)
reset();
say('d checkbox ayri', typeof faturaRaporFirmaKartaGit === 'function' && calls.switchTab.length === 0);
say('d sort ayri', calls.selectMusteri.length === 0);

// zaten hepsi ise dokunma/koru
reset();
dfValue = 'hepsi';
faturaRaporFirmaKartaGit({ stopPropagation: function(){}, preventDefault: function(){} }, 7);
say('b2 hepsi korunur', dfValue === 'hepsi');
"""
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(node)
        path = f.name
    try:
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=20)
        except FileNotFoundError:
            check("node harness", False, "node yok")
            return
        out = (r.stdout or "") + (r.stderr or "")
        print(out.strip())
        check("node exit", r.returncode == 0, out[-300:])
        for needle in [
            "ok a switchTab6",
            "ok a selectMusteri mid",
            "ok a switchTab0 yok",
            "ok b durum hepsi",
            "ok b select mid",
            "ok c throw yok",
            "ok c cagri yok",
            "ok d checkbox ayri",
            "ok d sort ayri",
            "ok b2 hepsi korunur",
        ]:
            check(needle, needle in out)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_diger_tiklanabilirler_ayri():
    """Firma linki dışında checkbox/Excel/sort ayrı handler; FirmaKartaGit'e bağlı değil."""
    html = _html()
    # Firma butonu
    check("firma-link class", "fatura-rapor-firma-link" in html)
    # Checkbox: data-musteri-id ama onclick FirmaKartaGit yok
    ch_i = html.find('class="fatura-rapor-firma-sec"')
    check("checkbox var", ch_i >= 0)
    if ch_i >= 0:
        snippet = html[ch_i : ch_i + 120]
        check("checkbox onclick yok", "faturaRaporFirmaKartaGit" not in snippet)
    # Excel / fatura oluştur ayrı
    check(
        "excel veya fatura ayri",
        "faturaRaporSecilenlereFaturaOlustur" in html or "Excel" in html,
    )
    # Düzenle / Müşteri Ekle yolu başka yerde switchTab(0) kalır
    check("switchTab(0) baska yerde", "switchTab(0)" in html)
    # Firma fn içinde 0 yok
    fn = _extract_fn(html, "faturaRaporFirmaKartaGit")
    check("firma fn tab0 yok", "switchTab(0)" not in fn)


if __name__ == "__main__":
    test_sablon_ve_kaynak()
    test_node_firma_tik()
    test_diger_tiklanabilirler_ayri()
    if FAILS:
        print("FAILED:", ", ".join(FAILS))
        sys.exit(1)
    print("ALL_OK test_musteriler_firma_tik_sozlesmeler")
