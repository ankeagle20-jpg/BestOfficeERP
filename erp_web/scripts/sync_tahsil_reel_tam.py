# -*- coding: utf-8 -*-
"""Sync full-reel tahsil helpers from _t.js into templates/giris/index.html."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T_JS = ROOT / "_t.js"
INDEX = ROOT / "templates" / "giris" / "index.html"

NAMES_REPLACE = [
    "girisTahsilatYilAyPaneldenHucreDurum",
    "girisTahsilatYilAyPanelReelBrutAl",
    "girisTahsilatYilAyPanelTahsilNet",
    "girisTahsilatYilAyMapDomdan",
    "girisTahsilatYilAySatirlarTopla",
    "girisTahsilatYilAySecilenleriTahsilet",
    "girisTahsilatYilAyTumunuSec",
    "girisTahsilatYilAySatirDegisti",
    "girisTahsilatPanelOlusturulanSenkron",
    "girisTahsilatYilAyDbKaydet",
    "girisTahsilatYilAyPanelSatirNormalize",
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


def replace_fn(html: str, name: str, new_body: str) -> str:
    old = extract_fn(html, name)
    if old:
        if old == new_body:
            return html
        return html.replace(old, new_body, 1)
    # insert after PanelBrutGoster for reel helpers
    if name in ("girisTahsilatYilAyPanelReelBrutAl", "girisTahsilatYilAyPanelTahsilNet"):
        anchor = extract_fn(html, "girisTahsilatYilAyPanelBrutGoster")
        if anchor:
            return html.replace(anchor, anchor + "\n" + new_body, 1)
    raise RuntimeError("function not found for replace/insert: " + name)


def main():
    t_src = T_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    orig = html
    for name in NAMES_REPLACE:
        body = extract_fn(t_src, name)
        if not body:
            raise RuntimeError("missing in _t.js: " + name)
        html = replace_fn(html, name, body)
        print("ok", name, len(body))
    if html == orig:
        print("no changes")
    else:
        INDEX.write_text(html, encoding="utf-8")
        print("written", INDEX)


if __name__ == "__main__":
    main()
