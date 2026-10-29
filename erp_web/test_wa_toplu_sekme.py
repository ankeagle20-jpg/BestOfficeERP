"""Toplu mesaj alt sekme: bayrak/rol render kapıları. PII yok."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from jinja2 import BaseLoader, Environment

ROOT = Path(__file__).resolve().parent


def check(ad, ok, detay=""):
    if not ok:
        raise SystemExit(f"FAIL {ad} {detay}")
    print("ok", ad)


def _tab12_fragment(html: str) -> str:
    i = html.find('id="tab-12"')
    j = html.find("tab-content-right", i)
    check("tab12 var", i > 0 and j > i)
    return html[i:j]


def _render_frag(frag: str, **ctx) -> str:
    env = Environment(loader=BaseLoader(), autoescape=True)
    # url_for stub
    def url_for(endpoint, **kw):
        if endpoint == "static":
            fn = kw.get("filename", "")
            v = kw.get("v", "")
            return f"/static/{fn}" + (f"?v={v}" if v != "" else "")
        return "/"

    env.globals["url_for"] = url_for
    return env.from_string(frag).render(**ctx)


def test_bayrak_acik_admin_render():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    frag = _tab12_fragment(html)
    # yalnızca wa_toplu if bloklarını + gecikme iskeletini kapsayan frag
    out = _render_frag(frag, wa_toplu_kampanya=True, wa_toplu_js_surum=3)
    check("alt sekme", 'id="wtAltSekmeBar"' in out and "Gecikme hatırlatma" in out and "Toplu mesaj" in out)
    check(
        "panel",
        'id="wtPanel"' in out and 'id="wtSablon"' in out and 'id="wtYenile"' in out
        and 'id="wtKaydet"' in out and 'id="wtKampanyaDurum"' in out,
    )
    check("gecikme durur", "WhatsApp - Gecikme Hatırlatma" in out and "whatsappSeciliGonder" in out)
    check("assets", "wa-toplu.css" in out and "wa-toplu.js" in out and "v=3" in out)


def test_bayrak_kapali_render():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    frag = _tab12_fragment(html)
    out = _render_frag(frag, wa_toplu_kampanya=False, wa_toplu_js_surum=3)
    check("alt sekme yok", "wtAltSekmeBar" not in out and "wtAltToplu" not in out)
    check("panel yok", 'id="wtPanel"' not in out and "wa-toplu.css" not in out and "wa-toplu.js" not in out)
    check("gecikme ayni", "WhatsApp - Gecikme Hatırlatma" in out and "whatsappSeciliGonder" in out)
    check("wrapper yok", "wtPanelGecikme" not in out)


def test_baslik_her_zaman_tekli():
    giris = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    bas = giris.find('<div class="giris-buttons">')
    cubuk = giris.find('id="giris-secili-musteri-toolbar"')
    baslik = giris[bas:cubuk]
    check("btn-toplu yok", 'id="btn-toplu-mesaj"' not in baslik)
    check("tekli onclick", "girisMusteriWhatsAppAc()" in baslik)
    check("wa if baslikta yok", "{% if wa_toplu_kampanya %}" not in baslik)


def test_js_lazy_ve_tek_bagla():
    js = (ROOT / "static" / "js" / "wa-toplu.js").read_text(encoding="utf-8")
    check("panel id", 'getElementById("wtPanel")' in js)
    check("modal yok", "wtModal" not in js and "btn-toplu-mesaj" not in js)
    check("tek bagla", 'data-wt-bagli") === "1"' in js or "data-wt-bagli') === '1'" in js)
    check("lazy", "listeYuklendi" in js and "listeYukle(false)" in js and "listeYukle(true)" in js)
    check("yenile", 'getElementById("wtYenile")' in js)
    check("kaydet", 'getElementById("wtKaydet")' in js and "sablonKaydet" in js)
    check("neden metin", "wtTaslakNedenMetin" in js and "Açık kampanya var" in js)
    check("JS_SURUM", re.search(r"(?m)^JS_SURUM\s*=\s*6\s*$", (ROOT / "wa_kampanya.py").read_text(encoding="utf-8")) is not None)
    check("test no ui", 'id="wtTestNoKaydet"' in (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8"))
    check("test atla ui", 'id="wtTestAtla"' in (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8"))


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
