# -*- coding: utf-8 -*-
"""v2 sözleşme önizleme FE + url_map. DB yazmaz. SKIP=FAIL."""
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


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + ek[:200]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


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
        return r.returncode == 0, f"{label}: {err[:200]}" if r.returncode else label


def test_fe_globals_and_urls():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    for label, src in (("tjs", tjs), ("idx", html)):
        check(f"{label}_sozlesmeOlustur", "async function sozlesmeOlustur(" in src)
        check(f"{label}_onizleGoster", "function sozlesmeV2OnizleGoster(" in src)
        check(
            f"{label}_api_url",
            "/giris/api/sozlesme-v2-onizle/" in src and "selectedId" in src,
        )
        check(f"{label}_indir_url", "/giris/sozlesme-olustur/" in src and "indir=1" in src)
        # src'li script gövdesine yazılmamış olsun: tanım inline/_t.js'te
        ok_n, msg = _node_check(f"{label}_node", src[src.find("async function sozlesmeOlustur") : src.find("function sozlesmeV2OnizleGoster") + 800])
        # extract only the two functions roughly
        m = re.search(
            r"async function sozlesmeOlustur\([\s\S]*?^function sozlesmeV2OnizleGoster\([\s\S]*?^}\s*$",
            src,
            flags=re.M,
        )
        body = m.group(0) if m else ""
        ok_n, msg = _node_check(f"{label}_fns", body)
        check(f"{label}_node_check", ok_n, msg)


def test_url_map():
    os.environ.setdefault("SECRET_KEY", "test")
    from app import app

    rules = {str(r) for r in app.url_map.iter_rules()}
    check(
        "url_onizle_api",
        any("/giris/api/sozlesme-v2-onizle/" in x or "sozlesme-v2-onizle" in x for x in rules),
        str([x for x in rules if "sozlesme" in x][:8]),
    )
    check(
        "url_olustur",
        any("sozlesme-olustur" in x for x in rules),
    )


def test_preview_no_write_mutation():
    """Önizleme yolu disk/DB yazmamalı (execute/open patch)."""
    os.environ["SOZLESME_SABLON_V2"] = "1"
    os.environ.pop("SOZLESME_SABLON_V2_MIDLER", None)
    writes: list[str] = []

    import routes.giris_routes as gr
    from sozlesme_sablon_v2 import render_sozlesme_docx_v2, docx_bytes_to_preview_html, build_fill_ctx

    # Birim: render + html bellek içi
    ctx = build_fill_ctx(
        musteri={"name": "SENT", "tax_number": "VN", "guncel_kira_bedeli": 100},
        kyc={"hizmet_turu": "Sanal Ofis", "aylik_kira": 100},
        sozlesme_no="PREV",
        sozlesme_tarihi="2026-01-01",
        yetkililer=[],
    )
    ctx["demirbas_rows"] = []
    data = render_sozlesme_docx_v2(ctx)
    html = docx_bytes_to_preview_html(data)
    check("preview_html_has_ek1", "Ek-1" in html or "ek-1" in html.casefold())
    check("preview_html_table", "<table" in html.casefold())

    # Route kaynak: onizle dalında INSERT/makedirs/save yok
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    # api_sozlesme_v2_onizle fonksiyon gövdesi
    m = re.search(
        r"def api_sozlesme_v2_onizle\([\s\S]*?\n(?=@bp\.route|def )",
        src,
    )
    body = m.group(0) if m else ""
    check("api_no_insert", "INSERT INTO sozlesmeler" not in body)
    check("api_no_makedirs", "makedirs" not in body)
    check("api_no_save_file", "save_sozlesme_docx_v2" not in body)
    check("api_render_memory", "render_sozlesme_docx_v2" in body)
    check("api_mammoth", "docx_bytes_to_preview_html" in body)

    # non-indir v2 sayfa dalı da INSERT yazmamalı (yazma yalnız indir=1)
    m2 = re.search(r"def sozlesme_olustur\([\s\S]*?# --- İNDİR", src)
    pre = m2.group(0) if m2 else ""
    check("page_onizle_no_insert", "INSERT INTO sozlesmeler" not in pre)

    os.environ.pop("SOZLESME_SABLON_V2", None)


def main() -> int:
    test_fe_globals_and_urls()
    test_url_map()
    test_preview_no_write_mutation()
    print("---")
    if FAILS:
        print("FAILS", len(FAILS), FAILS)
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
