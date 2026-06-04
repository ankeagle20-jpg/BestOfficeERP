# -*- coding: utf-8 -*-
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T_JS = ROOT / "_t.js"
INDEX = ROOT / "templates" / "giris" / "index.html"


def extract_fn(src: str, name: str) -> str | None:
    m = re.search(r"function " + re.escape(name) + r"\([^)]*\)\s*\{", src)
    if not m:
        return None
    i, depth, j = m.start(), 0, m.end() - 1
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i : j + 1]
        j += 1
    return None


def main():
    t_src = T_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    for name in ("girisTahsilatEkstreSilSonrasiPanelTemizle", "cariEkstreSatirSil"):
        body = extract_fn(t_src, name)
        if not body:
            raise RuntimeError("missing: " + name)
        old = extract_fn(html, name)
        if not old:
            raise RuntimeError("missing in index: " + name)
        html = html.replace(old, body, 1)
        print("ok", name)
    INDEX.write_text(html, encoding="utf-8")
    print("written")


if __name__ == "__main__":
    main()
