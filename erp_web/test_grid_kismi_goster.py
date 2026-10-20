# -*- coding: utf-8 -*-
"""GRID_KISMI_GOSTER bayrakli Kural A daraltma — yerel/fake. Canli yazma yok."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

from routes import giris_routes as gr  # noqa: E402

FAILS: list[str] = []
TOL = 0.02


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


class _Env:
    def __init__(self, enabled=None, ids=None):
        self.enabled = enabled
        self.ids = ids

    def __enter__(self):
        self._e = os.environ.pop("GRID_KISMI_GOSTER_ENABLED", None)
        self._i = os.environ.pop("GRID_KISMI_GOSTER_MUSTERI_IDS", None)
        if self.enabled is not None:
            os.environ["GRID_KISMI_GOSTER_ENABLED"] = self.enabled
        if self.ids is not None:
            os.environ["GRID_KISMI_GOSTER_MUSTERI_IDS"] = self.ids
        return self

    def __exit__(self, *a):
        os.environ.pop("GRID_KISMI_GOSTER_ENABLED", None)
        os.environ.pop("GRID_KISMI_GOSTER_MUSTERI_IDS", None)
        if self._e is not None:
            os.environ["GRID_KISMI_GOSTER_ENABLED"] = self._e
        if self._i is not None:
            os.environ["GRID_KISMI_GOSTER_MUSTERI_IDS"] = self._i


def test_bayrak():
    with _Env():
        check("bayrak varsayilan kapali", gr.grid_kismi_goster_aktif(867) is False)
    with _Env(enabled="1"):
        check("bayrak acik varsayilan 867", gr.grid_kismi_goster_aktif(867) is True)
        check("bayrak acik allowlist disi", gr.grid_kismi_goster_aktif(153) is False)
    with _Env(enabled="1", ids="153, 363"):
        check("bayrak ozel liste 153", gr.grid_kismi_goster_aktif(153) is True)
        check("bayrak ozel liste 867 yok", gr.grid_kismi_goster_aktif(867) is False)
    with _Env(enabled="1", ids=""):
        check("bayrak bos liste kimse", gr.grid_kismi_goster_aktif(867) is False)
    with _Env(enabled="0"):
        check("bayrak 0 kapali", gr.grid_kismi_goster_aktif(867) is False)


def test_daraltma_birim():
    f = gr._grid_payload_marker_panel_tam_kapandi
    check("daralt yok eski kural A", f(720.0, 1454.71, 2026, None, TOL) is True)
    check("daralt var kucuk TAH", f(720.0, 1454.71, 2026, None, TOL, daralt=True) is False)
    check("daralt var tam TAH (153 tipi)", f(540.0, 540.0, 2024, None, TOL, daralt=True) is True)
    check("daralt esik ici (brut-0.50)", f(1454.21, 1454.71, 2026, None, TOL, daralt=True) is True)
    check("daralt esik disi (brut-1.50)", f(1453.21, 1454.71, 2026, None, TOL, daralt=True) is False)
    check("marker yok her durumda", f(0.0, 1454.71, 2026, None, TOL, daralt=True) is False)
    check("PAY istisnasi korunur", f(1454.71, 1454.71, 2026, None, TOL, pay_t=100.0, daralt=True) is False)


def _payload_867():
    return {
        "musteri_id": 867,
        "aylar": [{
            "yil": 2026, "ay": 2, "brut_tutar_kdv": 1454.71, "tutar_kdv_dahil": 1454.71,
            "odenen_tutar_kdv": 0.0, "kalan_tutar_kdv": 1454.71,
            "tahsil_edildi": False, "kismi_tahsilat": False,
        }],
    }


def _apply_867():
    payload = _payload_867()
    panel = {"2026-02-01": {"aylik": 1454.71, "tahsil": 720.0, "kalan": 734.71}}
    with mock.patch.object(gr, "_ekstre_tahsil_rows_for_musteri", return_value=[]), \
            mock.patch.object(gr, "_ekstre_tahsil_batch_maps_from_rows",
                              return_value={"marker": {"2026-02-01": 720.0}, "pay": {}}):
        gr._apply_panel_by_iso_to_grid_payload(payload, panel)
    return payload["aylar"][0]


def test_payload_867():
    with _Env():
        a = _apply_867()
        check("bayrak kapali kalan 0", a["kalan_tutar_kdv"] == 0.0, a)
        check("bayrak kapali tahsil_edildi", a["tahsil_edildi"] is True, a)
        check("bayrak kapali kismi degil", a["kismi_tahsilat"] is False, a)
    with _Env(enabled="1"):
        a = _apply_867()
        check("bayrak acik kalan 734.71", a["kalan_tutar_kdv"] == 734.71, a)
        check("bayrak acik tahsil_edildi degil", a["tahsil_edildi"] is False, a)
        check("bayrak acik kismi", a["kismi_tahsilat"] is True, a)
        check("bayrak acik odenen 720", float(a["odenen_tutar_kdv"]) == 720.0, a)
    with _Env(enabled="1", ids="153"):
        a = _apply_867()
        check("allowlist disi sifir etki", a["kalan_tutar_kdv"] == 0.0 and a["tahsil_edildi"] is True, a)


def test_kaynak():
    src = (ROOT / "routes/giris_routes.py").read_text(encoding="utf-8")
    check("kaynak bayrak fn", "def grid_kismi_goster_aktif" in src)
    check("kaynak daralt param", "pay_t: float | None = None,\n    daralt: bool = False," in src)
    check("kaynak esik", "KURAL_A_DARALTMA_ESIK_TL = 1.00" in src)
    # panel fonksiyonlarina daralt gecilmemis (kapsam: panel zaten dogru)
    panel_bolum = src[src.find("def _load_musteri_panel_by_iso"):src.find("def _ekstre_panel_filter_db_tahsil")]
    check("panel kapsami disinda", "daralt=" not in panel_bolum)


def test_fe_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        check("playwright atlandi", True, "kurulu degil")
        return
    html = (ROOT / "templates/giris/index.html").read_text(encoding="utf-8")
    i = html.find("function paintCard(card)", 500000)
    js = ""
    if i >= 0:
        # kume parantezi dengesiyle fonksiyon sonunu bul (tirnak farkinda)
        depth, k, in_s, esc = 0, html.find("{", i), None, False
        while k < len(html):
            c = html[k]
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
                        break
            k += 1
        js = html[i:k + 1]
    check("pw paintCard blok", bool(js) and len(js) < 20000, len(js))
    bi = html.find(".sozlesmeler-ay-kart-kismi {")
    css = html[html.rfind("<style", 0, bi) if bi >= 0 else 0:0]
    kismi_css = ""
    if bi >= 0:
        kismi_css = html[bi:html.find("}", bi) + 1]
    check("pw turuncu css", "#ff9800" in kismi_css or "ff9800" in kismi_css, kismi_css[:80])
    page_html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
.sozlesmeler-ay-kart-kismi {{ background: #4a3418; border-color: #ff9800; }}
.sozlesmeler-ay-kart-odendi {{ background: #1b4332; }}
</style></head>
<body>
<div class="sozlesmeler-ay-kart" id="kart1" data-ay-key="2026-2"><span class="aylik-deger"></span></div>
<div class="sozlesmeler-ay-kart" id="kart2" data-ay-key="2026-1"><span class="aylik-deger"></span></div>
<script>
var cacheObj = null;
var tol = 0.02;
var byKey = {{
  '2026-2': {{ brut_tutar_kdv: 1454.71, odenen_tutar_kdv: 720.0, kalan_tutar_kdv: 734.71, kismi_tahsilat: true, tahsil_edildi: false }},
  '2026-1': {{ brut_tutar_kdv: 1454.71, odenen_tutar_kdv: 1189.84, kalan_tutar_kdv: 264.87, kismi_tahsilat: true, tahsil_edildi: false }}
}};
function sozlesmeAylikAyKeyNormalize(k) {{ return k; }}
function sozlesmelerAylikSessizPollMoney2(v) {{
  return Number(v).toLocaleString('tr-TR', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
}}
{js}
paintCard(document.getElementById('kart1'));
paintCard(document.getElementById('kart2'));
</script></body></html>"""
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True, channel="chrome")
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.set_content(page_html, wait_until="domcontentloaded")
            k1 = page.locator("#kart1")
            check("pw turuncu sinif", "sozlesmeler-ay-kart-kismi" in (k1.get_attribute("class") or ""))
            check("pw kalan tutar hucrede", "734,71" in k1.locator(".aylik-deger").inner_text(),
                  k1.locator(".aylik-deger").inner_text())
            check("pw data-kismi", k1.get_attribute("data-kismi") == "1")
            k2 = page.locator("#kart2")
            check("pw ocak da kismi", "sozlesmeler-ay-kart-kismi" in (k2.get_attribute("class") or "")
                  and "264,87" in k2.locator(".aylik-deger").inner_text())
            check("pw sayfa hatasi yok", not errors, errors[:2])
            browser.close()
    except Exception as exc:
        msg = str(exc)
        if "Executable doesn't exist" in msg or "BrowserType.launch" in msg:
            check("playwright atlandi", True, "chromium yok")
        else:
            check("pw calisti", False, msg[:180])


if __name__ == "__main__":
    test_bayrak()
    test_daraltma_birim()
    test_payload_867()
    test_kaynak()
    test_fe_playwright()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
