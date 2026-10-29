"""#wtModal CSS: navbar üstü z-index, max-height, tablo kaydırma. PII yok."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def check(ad, ok, detay=""):
    if not ok:
        raise SystemExit(f"FAIL {ad} {detay}")
    print("ok", ad)


def _css_prop(blok: str, prop: str) -> str | None:
    m = re.search(rf"(?m)^\s*{re.escape(prop)}\s*:\s*([^;]+);", blok)
    return m.group(1).strip() if m else None


def _kural(css: str, selector: str) -> str:
    # basit: selector { ... } ilk eşleşme
    pat = re.compile(
        rf"(?ms){re.escape(selector)}\s*\{{([^{{}}]*)\}}"
    )
    m = pat.search(css)
    return m.group(1) if m else ""


def test_navbar_zindex():
    layout = (ROOT / "templates" / "layout.html").read_text(encoding="utf-8")
    m = re.search(r"(?ms)\.header\s*\{([^{}]*)\}", layout)
    check("header kural", bool(m))
    blok = m.group(1)
    z = _css_prop(blok, "z-index")
    check("header sticky", "sticky" in blok or "fixed" in (_css_prop(blok, "position") or ""))
    check("header z-index", z is not None and int(z) == 1000, z or "")
    return int(z)


def test_wt_modal_css():
    nav_z = test_navbar_zindex()
    css = (ROOT / "static" / "css" / "wa-toplu.css").read_text(encoding="utf-8")
    modal = _kural(css, "#wtModal")
    check("wtModal kural", bool(modal.strip()))
    mz = _css_prop(modal, "z-index")
    check("wtModal z-index", mz is not None and int(mz) > nav_z, f"{mz}>{nav_z}")
    check("wtModal fixed", (_css_prop(modal, "position") or "") == "fixed")
    check("wtModal inset", "inset" in modal or ("top" in modal and "left" in modal))

    kutu = _kural(css, "#wtModal .wt-kutu")
    check("wt-kutu kural", bool(kutu.strip()))
    mh = _css_prop(kutu, "max-height")
    check("wt-kutu max-height", mh is not None and "100vh" in mh and "calc" in mh, mh or "")
    check("wt-kutu overflow", (_css_prop(kutu, "overflow") or "") == "hidden")
    check("wt-kutu flex", "flex" in (_css_prop(kutu, "display") or ""))

    tablo = _kural(css, "#wtModal .wt-tablo-sarici")
    check("tablo sarici", bool(tablo.strip()))
    ov = _css_prop(tablo, "overflow")
    check("tablo overflow", ov in ("auto", "scroll"), ov or "")
    check("tablo min-height", (_css_prop(tablo, "min-height") or "") == "0")

    ust = _kural(css, "#wtModal .wt-ust")
    filtre = _kural(css, "#wtModal .wt-filtre")
    alt = _kural(css, "#wtModal .wt-alt")
    check("ust flex-sabit", (_css_prop(ust, "flex") or "") == "0 0 auto")
    check("filtre flex-sabit", (_css_prop(filtre, "flex") or "") == "0 0 auto")
    check("alt flex-sabit", (_css_prop(alt, "flex") or "") == "0 0 auto")

    check("mobil media", "@media (max-width: 480px)" in css)
    check("mobil max-height", "calc(100vh - 1rem)" in css)

    # .wt-* yalnız #wtModal altında; çıplak .wt- kuralı yok
    bare = re.findall(r"(?m)^\s*(\.wt-[a-z0-9-]+)\s*\{", css)
    check("global wt yok", bare == [], str(bare))
    scoped = re.findall(r"(?m)^\s*(#wtModal(?:\s+\.wt-[a-z0-9-]+)+)\s*\{", css)
    check("scoped wt var", len(scoped) >= 4, str(len(scoped)))


def test_sablon_ve_surum():
    giris = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    i = giris.find('id="wtModal"')
    check("wtModal html", i > 0)
    parca = giris[giris.rfind("{% if wa_toplu_kampanya %}", 0, i): giris.find("{% endif %}", i) + 20]
    check("css link", "css/wa-toplu.css" in parca and "wa_toplu_js_surum" in parca)
    check("js v", "js/wa-toplu.js" in parca and "v=wa_toplu_js_surum" in parca)
    check("eski inline z90 yok", "z-index:90" not in parca)
    check("yapı sınıfları", all(x in parca for x in (
        'class="wt-kutu"', 'class="wt-ust"', 'class="wt-filtre"',
        'class="wt-tablo-sarici"', 'class="wt-alt"', 'id="wtTumunu"',
        'id="wtSablon"', 'id="wtBaslat"', 'id="wtTest"',
    )))
    modul = (ROOT / "wa_kampanya.py").read_text(encoding="utf-8")
    check("JS_SURUM sabit", re.search(r"(?m)^JS_SURUM\s*=\s*2\s*$", modul) is not None)
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    inj = app[app.find("def inject_wa_toplu_kampanya"): app.find("def inject_globals")]
    check("inject surum", "wa_toplu_js_surum" in inj and "JS_SURUM" in inj)


def _calistir(adlar):
    for ad in adlar:
        fn = globals().get(ad)
        if not callable(fn):
            check(ad, False)
            continue
        try:
            fn()
        except SystemExit:
            raise
        except Exception as e:
            check(ad + " " + type(e).__name__, False)


if __name__ == "__main__":
    secili = [a for a in sys.argv[1:] if a.startswith("test_")]
    if not secili:
        secili = [a for a in globals() if a.startswith("test_")]
    _calistir(secili)
    print("PASS")
