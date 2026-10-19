# -*- coding: utf-8 -*-
"""Kiracı dump: panel/grid onarım yedek + panel_tekrar_yedek strip."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from tenant_provisioning import BACKUP_TABLES, transform_public_dump  # noqa: E402

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:200]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _tbl(ad: str) -> str:
    return (
        "\n--\n-- Name: "
        + ad
        + "; Type: TABLE; Schema: public; Owner: -\n--\n\n"
        "CREATE TABLE public."
        + ad
        + " (\n    id integer\n);\n"
    )


def test_backup_tables_listesi():
    for ad in (
        "musteri_tahsilat_panel_detay_backup_20260617",
        "musteri_tahsilat_panel_detay_yedek",
        "musteri_aylik_grid_cache_yedek",
        "panel_tekrar_yedek",
        "tahsilat_marker_yedek",
        "tahsilat_tutar_yedek",
    ):
        check("listede " + ad, ad in BACKUP_TABLES, BACKUP_TABLES)


def test_transform_yedek_strip():
    yedekler = (
        "musteri_tahsilat_panel_detay_yedek",
        "musteri_aylik_grid_cache_yedek",
        "panel_tekrar_yedek",
        "musteri_tahsilat_panel_detay_backup_20260617",
        "tahsilat_marker_yedek",
        "tahsilat_tutar_yedek",
    )
    src = "".join(_tbl(ad) for ad in yedekler)
    src += _tbl("customers")
    src += _tbl("musteri_aylik_grid_cache")
    out = transform_public_dump(src, "tenant_ornek")
    for ad in yedekler:
        check("strip " + ad, ad not in out, out[:300])
    check("customers sema", "tenant_ornek.customers" in out, out[:300])
    check("grid_cache korunur", "tenant_ornek.musteri_aylik_grid_cache" in out, out[:300])
    check("public kalmadi", "public.customers" not in out)


if __name__ == "__main__":
    test_backup_tables_listesi()
    test_transform_yedek_strip()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
