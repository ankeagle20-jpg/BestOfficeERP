# -*- coding: utf-8 -*-
"""Makbuz düzenle/sil → marker yenile + tahsilat_degisti. Canlı yazma yok."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def test_strip_and_rebuild_helpers():
    from routes.faturalar_routes import (
        _strip_aylik_dagitim_markers,
        yeniden_hesapla_makbuz_dagitim_aciklama,
    )

    ac = (
        "Ağustos 2026 Tahsilat H|AYLIK_TAH|2026-07-01| |AYLIK_TAH|2026-08-01| "
        "|AYLIK_PAY|2026-07-01=0.80| |AYLIK_PAY|2026-08-01=51779.20|"
    )
    plain = _strip_aylik_dagitim_markers(ac)
    check("strip markers", "AYLIK_" not in plain and "Ağustos 2026 Tahsilat H" in plain, plain)

    with patch(
        "routes.faturalar_routes._auto_allocate_oldest_unpaid_months",
        return_value=(
            ["2026-07-01", "2026-08-01"],
            [("2026-07-01", 0.80), ("2026-08-01", 51799.00)],
        ),
    ):
        yeni = yeniden_hesapla_makbuz_dagitim_aciklama(
            872,
            tutar=51799.80,
            tahsilat_tarihi="2026-08-07",
            aciklama_eski=ac,
            haric_tahsilat_id=5525,
            kaynak="manuel_makbuz",
            odeme_turu="havale",
        )
    check("rebuild has pay", yeni and "|AYLIK_PAY|2026-08-01=51799.00|" in yeni, yeni)
    check("rebuild has tah", yeni and "|AYLIK_TAH|2026-08-01|" in yeni, yeni)
    check("eski pay yok", yeni and "51779.20" not in yeni, yeni)

    skip = yeniden_hesapla_makbuz_dagitim_aciklama(
        1,
        tutar=100,
        tahsilat_tarihi="2026-08-01",
        aciklama_eski="x",
        kaynak="grid_toplu",
    )
    check("grid_toplu skip", skip is None)


def test_call_sites_use_tahsilat_degisti():
    giris = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    # ekstre sil/guncelle gövdeleri
    i_sil = giris.find("def api_ekstre_tahsilat_sil")
    i_gun = giris.find("def api_ekstre_tahsilat_guncelle")
    i_next = giris.find("\ndef api_ekstre_tediye_guncelle", i_sil)
    sil_body = giris[i_sil:i_next]
    gun_body = giris[i_gun:i_sil]
    check("sil tahsilat_degisti", "tahsilat_degisti(" in sil_body)
    check("sil banka_birak", "banka_bagini_birak=True" in sil_body)
    check(
        "sil eski invalidate yok",
        "_ekstre_invalidate_after_change" not in sil_body,
    )
    check("guncelle tahsilat_degisti", "tahsilat_degisti(" in gun_body)
    check(
        "guncelle marker yenile",
        "yeniden_hesapla_makbuz_dagitim_aciklama" in gun_body,
    )
    check(
        "guncelle eski invalidate yok",
        "_ekstre_invalidate_after_change" not in gun_body,
    )

    fat = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    i_tg = fat.find("def tahsilat_guncelle")
    i_tn = fat.find("\ndef tahsilat_hizmet_turu_guncelle", i_tg)
    tg = fat[i_tg:i_tn]
    check("makbuz guncelle degisti", "tahsilat_degisti(" in tg)
    check("makbuz guncelle marker", "yeniden_hesapla_makbuz_dagitim_aciklama" in tg)
    check(
        "makbuz guncelle bare resync yok",
        "resync_panel_and_grid_after_tahsil_change" not in tg,
    )


if __name__ == "__main__":
    test_strip_and_rebuild_helpers()
    test_call_sites_use_tahsilat_degisti()
    if FAILS:
        print("FAILS", FAILS)
        raise SystemExit(1)
    print("PASS")
