"""#wtPanel CSS: kompakt 3 bar + flex liste. PII yok."""
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
    # Tek seçici veya grup içinde (virgülle) geçen kural gövdesi
    esc = re.escape(selector)
    pat = re.compile(
        rf"(?ms)(?:^|,)\s*{esc}\s*(?:,[^{{]+)?\{{([^{{}}]*)\}}"
    )
    m = pat.search(css)
    return m.group(1) if m else ""


def test_wt_panel_css():
    css = (ROOT / "static" / "css" / "wa-toplu.css").read_text(encoding="utf-8")
    check("modal yok", "#wtModal" not in css)
    check("fixed overlay yok", "position: fixed" not in css and "inset: 0" not in css)

    panel = _kural(css, "#wtPanel")
    check("wtPanel kural", bool(panel.strip()))

    panel_acik = _kural(css, "#wtPanel:not([hidden])")
    check("panel acik flex", "flex" in (_css_prop(panel_acik, "display") or ""))
    mh = _css_prop(panel_acik, "max-height") or _css_prop(panel_acik, "height")
    check("panel vh", mh is not None and "100vh" in mh and "calc" in mh, mh or "")

    kutu = _kural(css, "#wtPanel .wt-kutu")
    check("wt-kutu kural", bool(kutu.strip()))
    check("wt-kutu overflow", (_css_prop(kutu, "overflow") or "") == "hidden")
    check("wt-kutu flex", "flex" in (_css_prop(kutu, "display") or ""))

    tablo = _kural(css, "#wtPanel .wt-tablo-sarici")
    check("tablo sarici", bool(tablo.strip()))
    ov = _css_prop(tablo, "overflow")
    check("tablo overflow", ov in ("auto", "scroll"), ov or "")
    check("tablo min-height", (_css_prop(tablo, "min-height") or "") == "0")
    flex = _css_prop(tablo, "flex") or ""
    check("tablo flex1", flex.startswith("1"), flex)

    thead = _kural(css, "#wtPanel .wt-tablo thead th")
    check("thead sticky", (_css_prop(thead, "position") or "") == "sticky")

    for sel in (".wt-ust-bar", ".wt-mesaj-alt-bar", ".wt-filtre", ".wt-alt-bar"):
        blok = _kural(css, "#wtPanel " + sel)
        check(sel + " flex-sabit", (_css_prop(blok, "flex") or "") == "0 0 auto", sel)

    check("alt sekme bar", bool(_kural(css, "#wtAltSekmeBar").strip()))
    check("mobil media", "@media (max-width: 480px)" in css)

    bare = re.findall(r"(?m)^\s*(\.wt-[a-z0-9-]+)\s*\{", css)
    check("global wt yok", bare == [], str(bare))
    scoped = re.findall(r"(?m)^\s*(#wtPanel(?:\s+\.wt-[a-z0-9-]+)+)\s*\{", css)
    check("scoped wt var", len(scoped) >= 4, str(len(scoped)))


def test_kompakt_uc_bar():
    giris = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    i = giris.find('id="wtPanel"')
    check("wtPanel html", i > 0)
    t12i = giris.find('id="tab-12"')
    t12e = giris.find("tab-content-right", t12i)
    t12 = giris[t12i:t12e]
    check("uc bar sinif", all(x in t12 for x in (
        'class="wt-ust-bar"', 'class="wt-mesaj-alt-bar"', 'class="wt-alt-bar"',
        'class="wt-mesaj-sarici"', 'class="wt-tablo-sarici"',
    )))
    check("eski bar yok", 'class="wt-ust"' not in t12 and 'class="wt-alt"' not in t12)
    alt_i = t12.find('class="wt-alt-bar"')
    alt = t12[alt_i: t12.find("</div>", alt_i + 1) + 200]
    # alt bar kapanışını bulmak zor; id listesi panel içinde yeter
    for dugme in ("wtBaslat", "wtDurdur", "wtDevam", "wtBitir", "wtTest", "wtTestNoKaydet", "wtTestAtla", "wtTestNo"):
        check("alt id " + dugme, f'id="{dugme}"' in t12)
    check("ust idler", all(x in t12 for x in ('id="wtKaydet"', 'id="wtYenile"', 'id="wtOzet"', 'id="wtKampanyaDurum"')))
    check("mesaj idler", all(x in t12 for x in ('id="wtSablon"', 'id="wtSayac"', 'id="wtTavan"')))
    check("rows3", 'rows="3"' in t12 and 'placeholder="Mesaj"' in t12)

    css = (ROOT / "static" / "css" / "wa-toplu.css").read_text(encoding="utf-8")
    tablo = _kural(css, "#wtPanel .wt-tablo-sarici")
    check("liste flex1", (_css_prop(tablo, "flex") or "").startswith("1"))
    check("liste overflow", (_css_prop(tablo, "overflow") or "") in ("auto", "scroll"))


def test_sablon_ve_surum():
    giris = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    i = giris.find('id="wtPanel"')
    check("wtPanel html", i > 0)
    t12i = giris.find('id="tab-12"')
    t12e = giris.find("tab-content-right", t12i)
    t12 = giris[t12i:t12e]
    check("panel tab12 icinde", 'id="wtPanel"' in t12 and "wtAltSekmeBar" in t12)
    check("css link", "css/wa-toplu.css" in t12 and "wa_toplu_js_surum" in t12)
    check("js v", "js/wa-toplu.js" in t12 and "v=wa_toplu_js_surum" in t12)
    check("yenile", 'id="wtYenile"' in t12)
    check("kaydet", 'id="wtKaydet"' in t12 and "Şablonu kaydet" in t12)
    check("kampanya durum", 'id="wtKampanyaDurum"' in t12)
    check("kapat yok", 'id="wtKapat"' not in giris)
    check("btn-toplu yok", 'id="btn-toplu-mesaj"' not in giris)
    check("yapı sınıfları", all(x in t12 for x in (
        'class="wt-kutu"', 'class="wt-ust-bar"', 'class="wt-filtre"',
        'class="wt-tablo-sarici"', 'class="wt-alt-bar"', 'id="wtTumunu"',
        'id="wtSablon"', 'id="wtBaslat"', 'id="wtTest"',
    )))
    modul = (ROOT / "wa_kampanya.py").read_text(encoding="utf-8")
    check("JS_SURUM sabit", re.search(r"(?m)^JS_SURUM\s*=\s*6\s*$", modul) is not None)
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
