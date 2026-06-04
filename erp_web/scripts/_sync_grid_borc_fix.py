# -*- coding: utf-8 -*-
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T_JS = ROOT / "_t.js"
INDEX = ROOT / "templates" / "giris" / "index.html"

NAMES = [
    "girisTahsilatYilAyPaneldenHucreDurum",
    "girisTahsilatYilAyPanelGridaKartUygula",
    "sozlesmeAylikBorclandir",
]


def extract_fn(src: str, name: str) -> str | None:
    m = re.search(r"function " + re.escape(name) + r"\([^)]*\)\s*\{", src)
    if not m:
        return None
    i = m.start()
    depth = 0
    j = m.end() - 1
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i : j + 1]
        j += 1
    return None


def replace_fn(html: str, name: str, body: str) -> str:
    old = extract_fn(html, name)
    if not old:
        raise RuntimeError("missing in index: " + name)
    if old == body:
        return html
    return html.replace(old, body, 1)


def main():
    t_src = T_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    for name in NAMES:
        body = extract_fn(t_src, name)
        if not body:
            raise RuntimeError("missing in _t.js: " + name)
        html = replace_fn(html, name, body)
        print("ok", name)
    INDEX.write_text(html, encoding="utf-8")
    print("written")


if __name__ == "__main__":
    main()
