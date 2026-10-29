"""Toplu mesaj UX: taslak hata metni, sure gosterimi. PII yok."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import wa_kampanya as wa

ROOT = Path(__file__).resolve().parent


def check(ad, ok, detay=""):
    if not ok:
        raise SystemExit(f"FAIL {ad} {detay}")
    print("ok", ad)


def test_sure_gosterim():
    bir = wa.sure_tahmin(1, 150)
    check("1 sure_dk", bir["sure_dk"] == 2)
    check("1 gun kapali", bir["gun"] == 1 and bir["gun_goster"] is False)
    check("1 dakika", "dakika" in bir["sure_metin"] and "saat" not in bir["sure_metin"] and "günde" not in bir["sure_metin"])

    cok = wa.sure_tahmin(151, 150)
    check("151 gun", cok["gun"] == 2 and cok["gun_goster"] is True)
    check("151 metin gun", "günde" in cok["sure_metin"])

    saatlik = wa.sure_tahmin(50, 150)
    # 50/0.75 = 66.67 -> 67 dk -> saat
    check("50 saat", saatlik["sure_dk"] >= 60 and "saat" in saatlik["sure_metin"] and "günde" not in saatlik["sure_metin"])


def test_taslak_neden_ve_kaydet_ui():
    js = (ROOT / "static" / "js" / "wa-toplu.js").read_text(encoding="utf-8")
    check("gecersiz metin", "Mesaj boş veya 1000 karakteri aşıyor" in js)
    check("durum metin", "Önce Bitir'e bas" in js)
    check("once id", "Önce şablonu kaydedin" in js)
    check("kaydet ok", "Şablon kaydedildi" in js)
    check("kaydet fn", "function sablonKaydet" in js)
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("wtKaydet", 'id="wtKaydet"' in html and "Şablonu kaydet" in html)
    check("durum satir", 'id="wtKampanyaDurum"' in html)
    check("JS_SURUM", re.search(r"(?m)^JS_SURUM\s*=\s*5\s*$", (ROOT / "wa_kampanya.py").read_text(encoding="utf-8")))


def test_taslak_gecersiz_ve_durum():
    # DB yazmadan: gecersiz kurallar + kaynakta durum kilidi
    check("bos sablon", wa.sablon_gecerli("") is None and wa.sablon_gecerli("  ") is None)
    check("dolu sablon", wa.sablon_gecerli("merhaba") == "merhaba")
    check("tavan dusuk", wa.tavan_gecerli(10) is None)
    check("tavan ok", wa.tavan_gecerli(150) == 150)
    src = (ROOT / "wa_kampanya.py").read_text(encoding="utf-8")
    i = src.find("def taslak_kaydet")
    parca = src[i:src.find("\ndef ", i + 1)]
    check(
        "durum kural",
        'not in ("taslak", "test_bekliyor")' in parca and 'neden": "durum"' in parca
        and "if not metin or cap is None" in parca,
    )


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
