# -*- coding: utf-8 -*-
"""G2: okuma waterfall — bayrak kapalı varsayılan, yazma yok."""
from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mutabakat_okuma as mo  # noqa: E402

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _f(x):
    return float(Decimal(str(x)))


def test_bayrak_kapali_varsayilan():
    os.environ.pop("MUTABAKAT_OKUMA_ENABLED", None)
    os.environ.pop("MUTABAKAT_OKUMA_MUSTERI_IDS", None)
    check("enabled kapali", mo.mutabakat_okuma_enabled() is False)
    check("aktif yok", mo.mutabakat_okuma_aktif(402) is False)


def test_allowlist_bos_etkilemez():
    os.environ["MUTABAKAT_OKUMA_ENABLED"] = "1"
    os.environ["MUTABAKAT_OKUMA_MUSTERI_IDS"] = ""
    check("allowlist bos", mo.mutabakat_okuma_aktif(402) is False)
    os.environ["MUTABAKAT_OKUMA_MUSTERI_IDS"] = "402"
    check("allowlist 402", mo.mutabakat_okuma_aktif(402) is True)
    check("allowlist disi", mo.mutabakat_okuma_aktif(111) is False)
    os.environ.pop("MUTABAKAT_OKUMA_ENABLED", None)
    os.environ.pop("MUTABAKAT_OKUMA_MUSTERI_IDS", None)


def test_402_cift_pay_waterfall():
    """İki tahsilat PAY üst üste → Ağu–Kas 881, avans 0 (PG Decimal tipleriyle)."""
    # map ham: Ağu/Eyl 1540.18, Eki 443.64 (PAY toplamı)
    mp = {
        "2026-08-01": Decimal("1540.18"),
        "2026-09-01": Decimal("1540.18"),
        "2026-10-01": Decimal("443.64"),
    }
    br = {
        "2026-08-01": Decimal("881.00"),
        "2026-09-01": Decimal("881.00"),
        "2026-10-01": Decimal("881.00"),
        "2026-11-01": Decimal("881.00"),
        "2026-12-01": Decimal("881.00"),
    }
    r = mo.waterfall_tasima(mp, br)
    d = r["dagitim"]
    check("agu 881", abs(_f(d.get("2026-08-01", 0)) - 881.0) < 0.02, d)
    check("eyl 881", abs(_f(d.get("2026-09-01", 0)) - 881.0) < 0.02, d)
    check("eki 881", abs(_f(d.get("2026-10-01", 0)) - 881.0) < 0.02, d)
    check("kas 881", abs(_f(d.get("2026-11-01", 0)) - 881.0) < 0.02, d)
    check("avans 0", abs(_f(r["avans"]) - 0.0) < 0.02, r)
    check("korunum", r["korunum_ok"] is True, r)
    # Ekim yeşil eşiği
    check("ekim tam", _f(d["2026-10-01"]) >= 881.0 - 0.05)


def test_867_kismi_waterfall_dokunmaz():
    mp = {"2025-07-01": Decimal("964.00")}
    br = {"2025-07-01": Decimal("1094.00"), "2025-08-01": Decimal("1094.00")}
    r = mo.waterfall_tasima(mp, br)
    check("kismi ayni", abs(_f(r["dagitim"].get("2025-07-01", 0)) - 964.0) < 0.02, r)
    check("sonraki yok", "2025-08-01" not in r["dagitim"], r)
    check("avans 0 kismi", abs(_f(r["avans"])) < 0.02, r)


def test_kira_sifir_atla():
    mp = {"2026-01-01": Decimal("500"), "2026-02-01": Decimal("0"), "2026-03-01": Decimal("0")}
    br = {
        "2026-01-01": Decimal("0"),  # kira 0 — atla, carry
        "2026-02-01": Decimal("300"),
        "2026-03-01": Decimal("300"),
    }
    r = mo.waterfall_tasima(mp, br, skip_ym={"2026-02"})
    d = r["dagitim"]
    check("kira0 yazilmaz", "2026-01-01" not in d, d)
    check("dondurma atla", "2026-02-01" not in d, d)
    check("sonraki dolar", abs(_f(d.get("2026-03-01", 0)) - 300.0) < 0.02, d)
    check("avans kalan", abs(_f(r["avans"]) - 200.0) < 0.02, r)
    check("korunum2", r["korunum_ok"] is True, r)


def test_avans_olusur():
    mp = {"2026-01-01": Decimal("2000")}
    br = {"2026-01-01": Decimal("500"), "2026-02-01": Decimal("500")}
    r = mo.waterfall_tasima(mp, br)
    check("iki ay dolu", abs(_f(r["dagitim"]["2026-01-01"]) - 500) < 0.02)
    check("avans", abs(_f(r["avans"]) - 1000) < 0.02, r)
    check("korunum3", r["korunum_ok"] is True, r)


def test_okuma_map_bayrak_kapali_birebir():
    os.environ.pop("MUTABAKAT_OKUMA_ENABLED", None)
    os.environ.pop("MUTABAKAT_OKUMA_MUSTERI_IDS", None)
    mp = {"2026-08-01": 1540.18, "2026-09-01": 1540.18}
    br = {"2026-08-01": 881.0, "2026-09-01": 881.0}
    out = mo.okuma_tahsil_map(402, mp, br)
    check("kapali ham", abs(out.get("2026-08-01", 0) - 1540.18) < 0.02, out)


def test_okuma_map_allowlist_disi():
    os.environ["MUTABAKAT_OKUMA_ENABLED"] = "1"
    os.environ["MUTABAKAT_OKUMA_MUSTERI_IDS"] = "402"
    mp = {"2026-08-01": 1540.18}
    br = {"2026-08-01": 881.0, "2026-09-01": 881.0}
    out = mo.okuma_tahsil_map(999, mp, br)
    check("disi ham", abs(out.get("2026-08-01", 0) - 1540.18) < 0.02, out)
    out2 = mo.okuma_tahsil_map(402, mp, br)
    check("liste tavan tasi", abs(out2.get("2026-08-01", 0) - 881.0) < 0.02, out2)
    check("liste tasima", abs(out2.get("2026-09-01", 0) - 659.18) < 0.02, out2)
    os.environ.pop("MUTABAKAT_OKUMA_ENABLED", None)
    os.environ.pop("MUTABAKAT_OKUMA_MUSTERI_IDS", None)


if __name__ == "__main__":
    for fn in (
        test_bayrak_kapali_varsayilan,
        test_allowlist_bos_etkilemez,
        test_402_cift_pay_waterfall,
        test_867_kismi_waterfall_dokunmaz,
        test_kira_sifir_atla,
        test_avans_olusur,
        test_okuma_map_bayrak_kapali_birebir,
        test_okuma_map_allowlist_disi,
    ):
        print("---", fn.__name__)
        fn()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
