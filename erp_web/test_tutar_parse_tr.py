# -*- coding: utf-8 -*-
"""Ortak tutar ayrıştırma + ileri dönem onay. Canlıya yazmaz."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

from tutar_parse import (  # noqa: E402
    ileri_donem_onay_ret,
    parse_amount_tr,
    parse_amount_tr_or_zero,
)

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:200]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def test_tablo():
    cases = [
        ("20,60", 20.60),
        ("20.60", 20.60),  # tek nokta 2 hane = ondalık (eski bug: 2060)
        ("2.060", 2060.0),  # 3 hane = binlik
        ("2.060,00", 2060.0),
        ("2060", 2060.0),
        ("1.234,56", 1234.56),
        ("1234,56", 1234.56),
        ("1,234.56", 1234.56),
        ("0,80", 0.80),
        (".80", None),  # belirsiz / geçersiz → None
        ("20,6", 20.6),
        ("20,600", 20.6),  # virgül ondalık; trailing 0
    ]
    for raw, expect in cases:
        got = parse_amount_tr(raw, default=None)
        if expect is None:
            check(f"parse[{raw}]=None", got is None, got)
        else:
            check(f"parse[{raw}]={expect}", got is not None and abs(got - expect) < 0.001, got)


def test_eski_bug_2060():
    # Eski JS: replace all dots then comma → "20.60" → 2060
    check("20.60 not 2060", abs(parse_amount_tr_or_zero("20.60") - 20.60) < 0.001)
    check("20,60 ok", abs(parse_amount_tr_or_zero("20,60") - 20.60) < 0.001)


def test_ileri_onay():
    items = [("2026-08-01", 20.60), ("2026-09-01", 2039.40)]
    ret = ileri_donem_onay_ret(items, "2026-08-27", 2060.0, {})
    check("ileri ister", ret is not None and ret.get("kod") == "ileri_donem_onay_gerekli", ret)
    check("ileri Y", ret and abs(float(ret["ileri_donem"]) - 2039.40) < 0.01)
    check("ileri X", ret and abs(float(ret["vadesi_gelmis"]) - 20.60) < 0.01)
    ret2 = ileri_donem_onay_ret(items, "2026-08-27", 2060.0, {"ileri_donem_onay": 1})
    check("onayli gecer", ret2 is None)
    only = [("2026-08-01", 20.60)]
    check("spill yok", ileri_donem_onay_ret(only, "2026-08-27", 20.60, {}) is None)


def test_flexible_import():
    from routes.faturalar_routes import _parse_amount_flexible

    check("flex 20.60", abs(_parse_amount_flexible("20.60") - 20.60) < 0.001)
    check("flex 2.060", abs(_parse_amount_flexible("2.060") - 2060.0) < 0.001)


def test_ui_marker():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("safe fn", "function parseAmountTRSafe" in html)
    check("ileri kod", "ileri_donem_onay_gerekli" in html)
    check("payload safe", "parseAmountTRSafe(tutarRaw)" in html)
    # Eski tehlikeli yedek hâlâ fallback'te olabilir ama Safe önce
    check("girisTr=Safe", "function girisTrSayiParse(str) {\n    return parseAmountTRSafe(str);\n}" in html
          or "return parseAmountTRSafe(str);" in html)


if __name__ == "__main__":
    test_tablo()
    test_eski_bug_2060()
    test_ileri_onay()
    test_flexible_import()
    test_ui_marker()
    if FAILS:
        print("FAILS", FAILS)
        raise SystemExit(1)
    print("PASS")
