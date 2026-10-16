# -*- coding: utf-8 -*-
"""G3: okuma waterfall daraltılmış — bayrak kapalı varsayılan, yazma yok."""
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
    """İki tahsilat PAY üst üste → Ağu–Kas 881, avans 0 (PG Decimal)."""
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
    check("eslesmemis 0", abs(_f(r.get("eslesmemis_toplam") or 0)) < 0.02, r)
    check("korunum", r["korunum_ok"] is True, r)


def test_454_sozlesme_oncesi_tasinmaz():
    """Ödeme kira=0 (sözleşme öncesi) aylara → sonraki aylar kırmızı kalır; tutar eşleşmemişte."""
    mp = {
        "2025-01-01": Decimal("2000.00"),  # sözleşme öncesi / kira 0
        "2025-02-01": Decimal("500.00"),
    }
    br = {
        "2025-01-01": Decimal("0"),
        "2025-03-01": Decimal("1000.00"),
        "2025-04-01": Decimal("1000.00"),
    }
    r = mo.waterfall_tasima(mp, br)
    d = r["dagitim"]
    check("mart bos/kirmizi", abs(_f(d.get("2025-03-01", 0))) < 0.02, d)
    check("nisan bos/kirmizi", abs(_f(d.get("2025-04-01", 0))) < 0.02, d)
    check("eslesmemis 2500", abs(_f(r["eslesmemis_toplam"]) - 2500.0) < 0.02, r)
    check("avans 0", abs(_f(r["avans"])) < 0.02, r)
    check("korunum 454", r["korunum_ok"] is True, r)


def test_867_kismi_waterfall_dokunmaz():
    mp = {"2025-07-01": Decimal("964.00")}
    br = {"2025-07-01": Decimal("1094.00"), "2025-08-01": Decimal("1094.00")}
    r = mo.waterfall_tasima(mp, br)
    check("kismi ayni", abs(_f(r["dagitim"].get("2025-07-01", 0)) - 964.0) < 0.02, r)
    check("sonraki yok", "2025-08-01" not in r["dagitim"], r)
    check("avans 0 kismi", abs(_f(r["avans"])) < 0.02, r)


def test_dondurma_ayindan_tasima_yok():
    mp = {"2026-02-01": Decimal("800"), "2026-03-01": Decimal("0")}
    br = {"2026-02-01": Decimal("500"), "2026-03-01": Decimal("500")}
    r = mo.waterfall_tasima(mp, br, skip_ym={"2026-02"})
    d = r["dagitim"]
    # Şubat dondurma: 800 eşleşmemiş, taşınmaz → Mart boş/kırmızı
    check("dondurma eslesmemis", abs(_f(r["eslesmemis"].get("2026-02-01", 0)) - 800) < 0.02, r)
    check("mart tasima yok", abs(_f(d.get("2026-03-01", 0))) < 0.02, d)
    check("korunum don", r["korunum_ok"] is True, r)


def test_kira_sifir_eslesmemis():
    mp = {"2026-01-01": Decimal("500")}
    br = {
        "2026-01-01": Decimal("0"),
        "2026-02-01": Decimal("300"),
        "2026-03-01": Decimal("300"),
    }
    r = mo.waterfall_tasima(mp, br, skip_ym={"2026-02"})
    d = r["dagitim"]
    check("kira0 yazilmaz", "2026-01-01" not in d, d)
    check("sonraki tasinmaz", abs(_f(d.get("2026-03-01", 0))) < 0.02, d)
    check("eslesmemis 500", abs(_f(r["eslesmemis_toplam"]) - 500) < 0.02, r)
    check("avans 0", abs(_f(r["avans"])) < 0.02, r)
    check("korunum2", r["korunum_ok"] is True, r)


def test_avans_olusur_kira_pozitif_asim():
    mp = {"2026-01-01": Decimal("2000")}
    br = {"2026-01-01": Decimal("500"), "2026-02-01": Decimal("500")}
    r = mo.waterfall_tasima(mp, br)
    check("iki ay dolu", abs(_f(r["dagitim"]["2026-01-01"]) - 500) < 0.02)
    check("tasima", abs(_f(r["dagitim"]["2026-02-01"]) - 500) < 0.02)
    check("avans", abs(_f(r["avans"]) - 1000) < 0.02, r)
    check("eslesmemis yok", abs(_f(r.get("eslesmemis_toplam") or 0)) < 0.02, r)
    check("korunum3", r["korunum_ok"] is True, r)


def test_etiket_454_onceki_sozlesme():
    """454-benzeri: sözleşme 2026-05-06 öncesi kira=0 aylar → önceki sözleşme dönemi."""
    from datetime import date
    from decimal import Decimal as D

    mp = {("2025-%02d-01" % m): D("23000") for m in range(6, 13)}
    mp["2026-01-01"] = D("23000")
    mp["2026-02-01"] = D("23000")
    mp["2026-03-01"] = D("23000")
    mp["2026-04-01"] = D("23000")
    br = {k: D("0") for k in mp}
    br["2026-05-01"] = D("10000")
    br["2026-06-01"] = D("10000")
    r = mo.waterfall_tasima(mp, br)
    check("454 eslesmemis 253k", abs(_f(r["eslesmemis_toplam"]) - 253000) < 0.02, r)
    kovalar = mo.sinifla_eslesmemis_rapor(
        r["eslesmemis"], date(2026, 5, 6), mid=454
    )
    check("454 tek kova", len(kovalar) == 1, kovalar)
    check("454 etiket onceki", kovalar[0]["etiket"] == mo.ETIKET_ONCEKI_SOZLESME, kovalar)
    check("454 mid", kovalar[0].get("mid") == 454, kovalar)
    check("454 tl", abs(_f(kovalar[0]["toplam_tl"]) - 253000) < 0.02, kovalar)
    check("454 aralik", kovalar[0]["ay_aralik"] == "2025-06..2026-04", kovalar)


def test_etiket_896_onceki_sozlesme():
    """896-benzeri: 2025-03…11 ödemeleri, sözleşme 2026-02-28."""
    from datetime import date
    from decimal import Decimal as D

    mp = {("2025-%02d-01" % m): D("13000") for m in range(3, 12)}
    br = {k: D("0") for k in mp}
    for m in range(2, 10):
        br["2026-%02d-01" % m] = D("1200")
    r = mo.waterfall_tasima(mp, br)
    check("896 es 117k", abs(_f(r["eslesmemis_toplam"]) - 117000) < 0.02, r)
    check("896 yeni tasima yok", abs(_f(r["dagitim"].get("2026-02-01", 0))) < 0.02, r)
    kovalar = mo.sinifla_eslesmemis_rapor(
        r["eslesmemis"], "2026-02-28", mid=896
    )
    check("896 etiket", kovalar[0]["etiket"] == mo.ETIKET_ONCEKI_SOZLESME, kovalar)
    check("896 tl", abs(_f(kovalar[0]["toplam_tl"]) - 117000) < 0.02, kovalar)
    check("896 aralik", kovalar[0]["ay_aralik"] == "2025-03..2025-11", kovalar)


def test_etiket_dondurma_eslesmemis_kalir():
    """Sözleşme sonrası dondurma ayı → eşleşmemiş (önceki dönem değil)."""
    from datetime import date
    from decimal import Decimal as D

    mp = {"2026-06-01": D("500")}
    br = {"2026-06-01": D("0"), "2026-07-01": D("500")}
    r = mo.waterfall_tasima(mp, br, skip_ym={"2026-06"})
    kovalar = mo.sinifla_eslesmemis_rapor(
        r["eslesmemis"], date(2026, 5, 1), mid=1
    )
    check("don etiket eslesmemis", kovalar[0]["etiket"] == mo.ETIKET_ESLESMEMIS, kovalar)


def test_okuma_map_bayrak_kapali_birebir():
    os.environ.pop("MUTABAKAT_OKUMA_ENABLED", None)
    os.environ.pop("MUTABAKAT_OKUMA_MUSTERI_IDS", None)
    mp = {"2026-08-01": 1540.18, "2026-09-01": 1540.18}
    br = {"2026-08-01": 881.0, "2026-09-01": 881.0}
    out = mo.okuma_tahsil_map(402, mp, br)
    check("kapali ham", abs(out.get("2026-08-01", 0) - 1540.18) < 0.02, out)
    full = mo.okuma_waterfall_full(402, mp, br)
    check("kapali eslesmemis bos", abs(_f(full.get("eslesmemis_toplam") or 0)) < 0.02, full)
    check("kapali aktif false", full.get("aktif") is False, full)
    kovalar = mo.sinifla_eslesmemis_rapor(full.get("eslesmemis"), "2026-01-01", mid=402)
    check("kapali kova yok", kovalar == [], kovalar)


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
        test_454_sozlesme_oncesi_tasinmaz,
        test_867_kismi_waterfall_dokunmaz,
        test_dondurma_ayindan_tasima_yok,
        test_kira_sifir_eslesmemis,
        test_avans_olusur_kira_pozitif_asim,
        test_etiket_454_onceki_sozlesme,
        test_etiket_896_onceki_sozlesme,
        test_etiket_dondurma_eslesmemis_kalir,
        test_okuma_map_bayrak_kapali_birebir,
        test_okuma_map_allowlist_disi,
    ):
        print("---", fn.__name__)
        fn()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
