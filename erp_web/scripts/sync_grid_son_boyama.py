# -*- coding: utf-8 -*-
"""Sync sozlesmelerAylikGridVeriSonBoyama* from _t.js into templates/giris/index.html."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T_JS = ROOT / "_t.js"
INDEX = ROOT / "templates" / "giris" / "index.html"

NAMES = [
    "sozlesmelerAylikGridVeriSonBoyamaTail",
    "sozlesmelerAylikGridVeriSonBoyama",
    "sozlesmeAylikTahsilSonrasiSenkron",
    "girisTahsilatYilAyPanelDbYukle",
    "sozlesmeTahsilSetFromJson",
    "sozlesmeTahsilSetFromGridCache",
    "sozlesmelerAylikTahsilSetiniGorseleZorla",
    "girisTahsilatHizliYuklePanelVeGrid",
    "sozlesmelerAylikCacheRender",
    "girisTahsilatYilAyDbKaydet",
    "girisTahsilatYilAyPanelDbKaydet",
    "girisTahsilatYilAyPanelCacheDenDoldur",
    "girisTahsilatYilAyPanelGuncelle",
]


def extract_fn(src: str, name: str) -> str | None:
    m = re.search(r"function " + re.escape(name) + r"\([^)]*\)\s*\{", src)
    if not m:
        return None
    i = m.start()
    depth = 0
    j = m.end() - 1
    while j < len(src):
        c = src[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i : j + 1]
        j += 1
    return None


def replace_fn(html: str, name: str, body: str) -> tuple[str, bool]:
    old = extract_fn(html, name)
    if old:
        if old == body:
            return html, False
        return html.replace(old, body, 1), True
    return html, False


def insert_tail_before_son_boyama(html: str, tail_body: str) -> tuple[str, bool]:
    anchor = extract_fn(html, "sozlesmelerAylikGridVeriSonBoyama")
    if not anchor:
        raise RuntimeError("sozlesmelerAylikGridVeriSonBoyama not in index.html")
    marker = "/**\n * Tahsil + grid önbellek + reel aynı anda geldikten sonra tek seferde boyar."
    idx = html.find(marker)
    if idx < 0:
        raise RuntimeError("SonBoyama comment marker not found")
    chunk = tail_body + "\n\n" + marker
    if tail_body in html:
        return html, False
    return html[:idx] + chunk + html[idx + len(marker) :], True


def main():
    t_src = T_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    orig = html
    for name in NAMES:
        body = extract_fn(t_src, name)
        if not body:
            raise RuntimeError("missing in _t.js: " + name)
        if name == "sozlesmelerAylikGridVeriSonBoyamaTail":
            if extract_fn(html, name):
                html, ch = replace_fn(html, name, body)
            else:
                html, ch = insert_tail_before_son_boyama(html, body)
        else:
            html, ch = replace_fn(html, name, body)
        print("ok", name, "changed" if ch else "unchanged")
    if html != orig:
        INDEX.write_text(html, encoding="utf-8")
        print("written", INDEX)
    else:
        print("no changes")


if __name__ == "__main__":
    main()
