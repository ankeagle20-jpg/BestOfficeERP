# -*- coding: utf-8 -*-
"""Demirbaş FE: girisDemirbasYukle gerçek sayfa HTML'inde global olmalı.

Kök neden regressiyonu: src'li <script> gövdesindeki JS tarayıcıda yok sayılır.
DB yazmaz. Playwright varsa typeof doğrular.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_", "DEMIRBAS_", "SOZLESME_")):
        os.environ.pop(k, None)

FAILS: list[str] = []
HTML_PATH = ROOT / "templates" / "giris" / "index.html"


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + ek) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _extract_scripts(html: str) -> list[tuple[str, str]]:
    """[(attrs, body), ...]"""
    return [
        (m.group(1) or "", m.group(2) or "")
        for m in re.finditer(r"<script([^>]*)>(.*?)</script>", html, flags=re.I | re.S)
    ]


def _neutralize_jinja(s: str) -> str:
    s = re.sub(r"\{#.*?#\}", "", s, flags=re.S)
    s = re.sub(r"\{\%.*?\%\}", "", s, flags=re.S)

    def repl(m: re.Match) -> str:
        inner = m.group(1)
        if "else 0" in inner:
            return "0"
        if "else 'null'" in inner or "else null" in inner:
            return "null"
        if "url_for" in inner:
            return "'/static/x.js'"
        return "null"

    return re.sub(r"\{\{(.*?)\}\}", repl, s, flags=re.S)


def _node_check(label: str, body: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.js"
        p.write_text(body, encoding="utf-8")
        r = subprocess.run(
            ["node", "--check", str(p)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        err = (r.stderr or r.stdout or "").strip()
        return r.returncode == 0, f"{label}: {err[:300]}" if r.returncode else label


def test_src_govde_ve_inline_tanim():
    raw = HTML_PATH.read_text(encoding="utf-8")
    scripts = _extract_scripts(raw)
    check("script_adet", len(scripts) >= 6, str(len(scripts)))

    src_with_dem = []
    inline_defs = []
    for attrs, body in scripts:
        has_src = bool(re.search(r"\bsrc\s*=", attrs, flags=re.I))
        if has_src and re.search(r"girisDemirbasYukle|GIRIS_DEMIRBAS_API", body):
            src_with_dem.append(attrs[:80])
        if (not has_src) and "function girisDemirbasYukle" in body:
            inline_defs.append(len(body))
        if has_src and body.strip():
            # src'li etiket govdesi bos olmali (tarayici yok sayar; regressiyon kapisi)
            check(
                "src_govde_bos:" + (re.search(r"filename=['\"]([^'\"]+)", attrs).group(1) if re.search(r"filename=", attrs) else attrs[:40]),
                False,
                f"nonempty={len(body.strip())}",
            )

    check("src_govdede_demirbas_yok", not src_with_dem, str(src_with_dem))
    check("inline_tanim_var", len(inline_defs) == 1, str(inline_defs))

    # inline demirbas blogu node --check
    for attrs, body in scripts:
        if re.search(r"\bsrc\s*=", attrs, flags=re.I):
            continue
        if "function girisDemirbasYukle" not in body:
            continue
        ok, msg = _node_check("dem_inline", _neutralize_jinja(body))
        check("dem_inline_syntax", ok, msg)

    # tum inline bloklar (jinja notr)
    neut_html = _neutralize_jinja(raw)
    for i, (attrs, body) in enumerate(_extract_scripts(neut_html)):
        if re.search(r"\bsrc\s*=", attrs, flags=re.I):
            continue
        if not body.strip():
            continue
        ok, msg = _node_check(f"inline[{i}]", body)
        check(f"inline_syntax[{i}]", ok, msg)

    # _t.js ayna
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    check("tjs_tanim", "function girisDemirbasYukle" in tjs)
    check("tjs_ekler", "function girisDemirbasEklerPanelAc" in tjs and "demirbas-ekler" in tjs)
    ok, msg = _node_check("_t.js", tjs)
    check("tjs_syntax", ok, msg)
    # inline HTML'de Ekler paneli (src'siz blok)
    check("html_ekler_panel", "giris_demirbas_ekler_panel" in raw and 'data-demirbas-action="ekler-ac"' in raw)


def test_playwright_typeof_global():
    """Render edilmis/notr HTML'de typeof girisDemirbasYukle === 'function'."""
    raw = HTML_PATH.read_text(encoding="utf-8")
    html = _neutralize_jinja(raw)
    # harici script src'lerini bos stub yap (404 olmasin)
    html = re.sub(
        r"<script([^>]*\bsrc\s*=[^>]*)>\s*</script>",
        "<script></script>",
        html,
        flags=re.I,
    )
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"C:\Users\MSI\AppData\Local\ms-playwright")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        check("playwright_import", False, type(e).__name__)
        return

    console_errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda err: console_errors.append(str(err)[:200]))
        page.on("console", lambda msg: console_errors.append(msg.text[:200]) if msg.type == "error" else None)
        page.set_content(html, wait_until="domcontentloaded")
        typeof_v = page.evaluate("() => typeof girisDemirbasYukle")
        typeof_sel = page.evaluate("() => typeof selectMusteri")
        same = page.evaluate(
            "() => (typeof girisDemirbasYukle === 'function') && (typeof selectMusteri === 'function')"
        )
        browser.close()

    check("typeof_yukle", typeof_v == "function", typeof_v)
    check("typeof_select", typeof_sel == "function", typeof_sel)
    check("ayni_global_kapsam", same is True)
    # demirbas tanimi kaynakli parse error olmamali
    dem_errs = [e for e in console_errors if "demirbas" in e.lower() or "GIRIS_DEMIRBAS" in e]
    check("console_dem_yok", not dem_errs, str(dem_errs[:3]))
    print("console_error_n", len(console_errors))


def main() -> int:
    test_src_govde_ve_inline_tanim()
    test_playwright_typeof_global()
    print("---")
    if FAILS:
        print("FAILS", len(FAILS), FAILS)
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
