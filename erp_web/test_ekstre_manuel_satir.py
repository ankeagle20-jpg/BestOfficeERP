# -*- coding: utf-8 -*-
"""Ekstre: manuel makbuz tek satır. Uygulama modülü yüklenmez."""
import re
from datetime import date, datetime
from pathlib import Path


def _yukle():
    src = Path("routes/giris_routes.py").read_text(encoding="utf-8")
    bas = src.find("_EKSTRE_KENDI_KAYNAK = ")
    son = src.find("\ndef _ekstre_kaynak_kendi_satiri")
    assert bas >= 0 and son > bas
    ns = {"date": date, "datetime": datetime, "re": re}
    exec(src[bas:son], ns)
    return ns["_ekstre_eslesme_tarihi"], src


def test_manuel_tarih_ay_isaretini_ezmez():
    fn, src = _yukle()
    ac = (
        "|AYLIK_TAH|2023-01-01||AYLIK_PAY|2023-01-01=300.00|"
        "|AYLIK_PAY|2024-08-01=1094.18|"
    )
    assert fn("manuel_makbuz", "2026-10-07", None, ac, "2023-08-01") == "2026-10-07"
    assert fn("banka_import", "2026-10-07", "2023-01-01", ac, "2023-08-01") == "2026-10-07"
    assert fn("odeme_linki", "2026-10-07", None, ac, "2023-08-01") == "2026-10-07"
    assert fn("grid_toplu", "2023-08-01", None, "|AYLIK_TAH|2023-08-01|", "2023-08-01") == "2023-08-01"
    assert "THEN t.tahsilat_tarihi::date" in src
    assert "AYLIK_PAY" in src
    assert "elif kay0 in _EKSTRE_KENDI_KAYNAK" in src
    assert "kay0 == \"grid_toplu\"" in src


def test_bes_bin_tek_satir_cift_sayim_yok():
    fn, _src = _yukle()
    floor = "2023-08-01"
    bit = "2026-10-07"
    grid = []
    y, m = 2023, 8
    while (y, m) <= (2024, 7):
        iso = f"{y:04d}-{m:02d}-01"
        grid.append({
            "kaynak": "grid_toplu",
            "tarih": iso,
            "tutar": 720.0,
            "aciklama": f"|AYLIK_TAH|{iso}|",
        })
        m += 1
        if m == 13:
            y, m = y + 1, 1
    manuel = {
        "kaynak": "manuel_makbuz",
        "tarih": "2026-10-07",
        "tutar": 5000.0,
        "aciklama": "|AYLIK_TAH|2023-01-01||AYLIK_PAY|2023-01-01=300.00|",
    }
    ay_alacak = 0.0
    for row in grid:
        es = fn(row["kaynak"], row["tarih"], None, row["aciklama"], floor)
        assert floor <= es <= bit
        ay_alacak += row["tutar"]
    es_m = fn(manuel["kaynak"], manuel["tarih"], None, manuel["aciklama"], floor)
    assert es_m == "2026-10-07"
    assert floor <= es_m <= bit
    # Ay satırına yazılmaz; tutarın tamamı tek satırdır.
    direkt = manuel["tutar"]
    assert ay_alacak == 8640.0
    assert direkt == 5000.0
    borc = round(12 * 720 + 12 * 1094.18 + 12 * 1454.71 + 3 * 1918.76, 2)
    assert borc == 44982.96
    bakiye = round(borc - ay_alacak - direkt, 2)
    assert bakiye == 31342.96
    assert round(ay_alacak + direkt, 2) == 13640.0


def test_gelecek_isaret_eski_elenir():
    fn, _src = _yukle()
    bit = "2026-10-07"
    es_a = fn(
        "manuel_makbuz", "2026-08-26", None,
        "|AYLIK_TAH|2027-06-01||AYLIK_PAY|2027-06-01=2880.00|",
        "2026-08-01",
    )
    assert es_a == "2027-06-01", es_a
    assert es_a > bit
    es_b = fn(
        "manuel_makbuz", "2026-10-07", None,
        "|AYLIK_TAH|2026-11-01||AYLIK_PAY|2026-11-01=10000.00|",
        "2026-10-01",
    )
    assert es_b == "2026-11-01", es_b
    assert es_b > bit


if __name__ == "__main__":
    test_manuel_tarih_ay_isaretini_ezmez()
    test_bes_bin_tek_satir_cift_sayim_yok()
    test_gelecek_isaret_eski_elenir()
    print("CASE ekstre manuel ok", date.today().isoformat())
