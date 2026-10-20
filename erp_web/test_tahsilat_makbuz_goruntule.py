# -*- coding: utf-8 -*-
"""Manuel makbuz 'Makbuzu goruntule' butonu — yerel/fake. Canli yazma yok."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS: list[str] = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _html():
    return (ROOT / "templates/giris/index.html").read_text(encoding="utf-8")


def test_fe_kaynak():
    html = _html()
    check("fe buton giris", 'id="giris_tahsilat_makbuz_goruntule_btn"' in html
          and "tahsilatMakbuzGoruntuleAc('giris_tahsilat')" in html)
    check("fe buton sozlesme", 'id="sozlesme_tahsilat_makbuz_goruntule_btn"' in html
          and "tahsilatMakbuzGoruntuleAc('sozlesme_tahsilat')" in html)
    check("fe buton metni", "> Makbuzu görüntüle</button>" in html)
    check("fe eski buton metni yok", "> Makbuz oluştur</button>" not in html)
    check("fe hint guncel", "<strong>Makbuzu görüntüle:</strong>" in html
          and "<strong>Makbuz oluştur:</strong>" not in html)
    check("fe buton pasif baslangic",
          html.count('makbuz_goruntule_btn" disabled title="Önce kaydedin"') == 2)
    check("fe aria", html.count('aria-label="Makbuzu görüntüle"') == 2)
    check("fe fn hazirla", "function tahsilatMakbuzGoruntuleHazirla" in html)
    check("fe fn ac", "function tahsilatMakbuzGoruntuleAc" in html)
    check("fe fn sifirla", "function tahsilatMakbuzGoruntuleSifirla" in html)
    check("fe pdf route", "'/faturalar/tahsilat-pdf/' + k.tahsilat_id" in html)
    check("fe hook giris", "tahsilatMakbuzGoruntuleHazirla('giris_tahsilat', data.tahsilat_id" in html)
    check("fe hook sozlesme", "tahsilatMakbuzGoruntuleHazirla('sozlesme_tahsilat', data.tahsilat_id" in html)
    check("fe iptal sifirla giris", "tahsilatMakbuzGoruntuleSifirla('giris_tahsilat')" in html)
    check("fe iptal sifirla sozlesme", "tahsilatMakbuzGoruntuleSifirla('sozlesme_tahsilat')" in html)
    check("fe once kaydedin mesaji", "alert('Önce kaydedin.')" in html)
    # Onizle akisi (ayri Onizle butonu) korunuyor
    check("fe onizle korundu", "function girisTahsilatOnizle" in html
          and "function sozlesmeTahsilatOnizle" in html)
    # Kaydet cift tik korumasi mevcut
    check("fe inflight giris", "__girisTahsilKaydetInFlight" in html)
    check("fe inflight sozlesme", "__sozlesmeTahsilKaydetInFlight" in html)


def test_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        check("playwright atlandi", True, "kurulu degil")
        return
    html = _html()
    i = html.find("/* makbuz-goruntule-js */")
    j = html.find("/* /makbuz-goruntule-js */")
    js = html[i:j] if i >= 0 and j > i else ""
    check("pw js blok", bool(js))
    bi = html.find('id="giris_tahsilat_makbuz_goruntule_btn"')
    btn_g = html[html.rfind("<button", 0, bi):html.find("</button>", bi) + 9] if bi >= 0 else ""
    bj = html.find('id="sozlesme_tahsilat_makbuz_goruntule_btn"')
    btn_s = html[html.rfind("<button", 0, bj):html.find("</button>", bj) + 9] if bj >= 0 else ""
    check("pw butonlar", bool(btn_g and btn_s))
    page_html = f"""<!doctype html><html><head><meta charset="utf-8"></head>
<body>
<input type="hidden" id="giris_tahsilat_musteri_id" value="153">
<input type="hidden" id="sozlesme_tahsilat_musteri_id" value="153">
<input type="text" id="giris_tahsilat_tutar" value="">
{btn_g}
{btn_s}
<script>
var __open = [];
var __fetch = [];
window.open = function(u) {{ __open.push(String(u)); return null; }};
window.fetch = function(u) {{ __fetch.push(String(u)); return Promise.reject(new Error('beklenmez')); }};
{js}
</script></body></html>"""
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True, channel="chrome")
            page = browser.new_page()
            dialogs = []
            errors = []
            page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.set_content(page_html, wait_until="domcontentloaded")
            bg = page.locator("#giris_tahsilat_makbuz_goruntule_btn")
            bs = page.locator("#sozlesme_tahsilat_makbuz_goruntule_btn")
            check("pw baslangic pasif", bg.is_disabled() and bs.is_disabled())
            check("pw buton metni", "Makbuzu görüntüle" in bg.inner_text())
            # Kaydet oncesi tik (fonksiyon yolu): Once kaydedin, PDF yok
            page.evaluate("tahsilatMakbuzGoruntuleAc('giris_tahsilat')")
            page.wait_for_timeout(120)
            check("pw kayit oncesi mesaj", dialogs == ["Önce kaydedin."], dialogs)
            check("pw kayit oncesi pdf yok", page.evaluate("__open.length") == 0)
            dialogs.clear()
            # Kaydet basarisi simulasyonu: id+makbuz no bellekte
            page.evaluate("tahsilatMakbuzGoruntuleHazirla('giris_tahsilat', 123, '1042', '153')")
            check("pw kaydet sonrasi aktif", bg.is_enabled())
            check("pw title makbuz no", "1042" in (bg.get_attribute("title") or ""))
            # Tutar alani bosken dogru makbuz acilir
            bg.click()
            page.wait_for_timeout(120)
            check("pw dogru pdf", page.evaluate("__open") == ["/faturalar/tahsilat-pdf/123"],
                  page.evaluate("__open"))
            # Tekrar basmak: ayni makbuz, yeni kayit istegi yok
            bg.click()
            page.wait_for_timeout(120)
            check("pw ikinci tik ayni pdf",
                  page.evaluate("__open") == ["/faturalar/tahsilat-pdf/123", "/faturalar/tahsilat-pdf/123"])
            check("pw hic fetch yok (kayit yok)", page.evaluate("__fetch.length") == 0)
            check("pw tutar mesaji yok", not any("Geçerli bir tutar" in d for d in dialogs), dialogs)
            # Musteri degisirse eski makbuz acilmaz
            page.evaluate("document.getElementById('giris_tahsilat_musteri_id').value = '999'")
            bg.click()
            page.wait_for_timeout(120)
            check("pw musteri degisimi mesaj", dialogs[-1:] == ["Önce kaydedin."], dialogs)
            check("pw musteri degisimi pdf yok", page.evaluate("__open.length") == 2)
            # Sifirla -> pasif
            page.evaluate("tahsilatMakbuzGoruntuleSifirla('giris_tahsilat')")
            check("pw sifirla pasif", bg.is_disabled())
            check("pw sayfa hatasi yok", not errors, errors[:2])
            browser.close()
    except Exception as exc:
        msg = str(exc)
        if "Executable doesn't exist" in msg or "BrowserType.launch" in msg:
            check("playwright atlandi", True, "chromium yok")
        else:
            check("pw calisti", False, msg[:180])


if __name__ == "__main__":
    test_fe_kaynak()
    test_playwright()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
