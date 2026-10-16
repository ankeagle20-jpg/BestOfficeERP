# -*- coding: utf-8 -*-
"""Devreden görüntü-tarihi = dönem satırı kuralı. Canlı yazma yok."""
from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_goruntu_tarihi_kurallari():
    from routes import giris_routes as gr

    check(
        "banka odeme gunu",
        gr._ekstre_satir_goruntu_tarihi({
            "kaynak": "banka_import",
            "fatura_id": None,
            "tarih": "2026-01-16",
            "eslesme_tarihi": "2025-09-01",
            "tahsilat_aciklama": "|AYLIK_TAH|2025-09-01|",
        }) == "2026-01-16",
    )
    check(
        "manuel odeme gunu",
        gr._ekstre_satir_goruntu_tarihi({
            "kaynak": "manuel_makbuz",
            "tarih": "2025-11-19",
            "eslesme_tarihi": "2025-07-01",
        }) == "2025-11-19",
    )
    check(
        "grid_toplu eslesme",
        gr._ekstre_satir_goruntu_tarihi({
            "kaynak": "grid_toplu",
            "tarih": "2026-01-16",
            "eslesme_tarihi": "2025-09-01",
        }) == "2025-09-01",
    )
    check(
        "faturali eslesme",
        gr._ekstre_satir_goruntu_tarihi({
            "kaynak": "banka_import",
            "fatura_id": 99,
            "tarih": "2026-01-16",
            "eslesme_tarihi": "2025-09-01",
        }) == "2025-09-01",
    )


def _borc_87_slice():
    out = {}
    for m in range(7, 13):
        out[f"2025-{m:02d}-01"] = 960.0
    for m in range(1, 11):
        out[f"2026-{m:02d}-01"] = 960.0
    return out


def _pays_87_pattern():
    """mid=87 kalıbı: marker ayı ≠ ödeme günü (yıl sınırı aşan)."""
    rows = [
        (5989, "2025-11-19", "2025-07-01", 960.0, "2025-07-01"),
        (5988, "2025-11-20", "2025-08-01", 960.0, "2025-08-01"),
        (5987, "2026-01-16", "2025-09-01", 960.0, "2025-09-01"),
        (5986, "2026-01-26", "2025-10-01", 960.0, "2025-10-01"),
        (5985, "2026-02-06", "2025-11-01", 2880.0, "2025-11-01"),  # 3 ay
        (5984, "2026-06-08", "2026-02-01", 1920.0, "2026-02-01"),
        (5983, "2026-08-25", "2026-04-01", 2534.98, "2026-04-01"),
    ]
    out = []
    for tid, odeme, esl, tut, tah_iso in rows:
        ac = f"|AYLIK_TAH|{tah_iso}|"
        if tut == 2880:
            ac = (
                "|AYLIK_TAH|2025-11-01| |AYLIK_PAY|2025-11-01=960.00| "
                "|AYLIK_TAH|2025-12-01| |AYLIK_PAY|2025-12-01=960.00| "
                "|AYLIK_TAH|2026-01-01| |AYLIK_PAY|2026-01-01=960.00|"
            )
        elif tut == 1920:
            ac = (
                "|AYLIK_TAH|2026-02-01| |AYLIK_PAY|2026-02-01=960.00| "
                "|AYLIK_TAH|2026-03-01| |AYLIK_PAY|2026-03-01=960.00|"
            )
        elif tut == 2534.98:
            ac = (
                "|AYLIK_TAH|2026-04-01| |AYLIK_PAY|2026-04-01=960.00| "
                "|AYLIK_TAH|2026-05-01| |AYLIK_PAY|2026-05-01=960.00| "
                "|AYLIK_TAH|2026-06-01| |AYLIK_PAY|2026-06-01=614.98|"
            )
        else:
            ac = f"|AYLIK_TAH|{tah_iso}| |AYLIK_PAY|{tah_iso}={tut:.2f}|"
        out.append({
            "id": tid,
            "kaynak": "banka_import",
            "fatura_id": None,
            "tarih": odeme,
            "tahsilat_tarihi": odeme,
            "eslesme_tarihi": esl,
            "tutar": tut,
            "tahsilat_aciklama": ac,
            "odeme_turu": "havale",
        })
    return out


def test_acilis_87_yil_basi():
    from routes import giris_routes as gr

    borc = _borc_87_slice()
    pays = _pays_87_pattern()
    opening = gr._ekstre_acilis_bakiyesi_fifo(
        borc, "2026-01-01", pays, "1900-01-01",
    )
    # Jul-Dec 5760 − Nov ödemeleri 1920 = 3840
    check("opening_3840", abs(opening - 3840.0) < 0.02, opening)
    dev_b, dev_a = gr._ekstre_devreden_satir_toplamlari(borc, "2026-01-01", opening)
    check("dev_borc_5760", abs(dev_b - 5760.0) < 0.02, dev_b)
    check("dev_alacak_1920", abs(dev_a - 1920.0) < 0.02, dev_a)


def test_eski_kural_cift_sayardi():
    """Marker ile sınıflandırma + eslesme cutoff → opening 0 (çift sayım kökü)."""
    from routes import giris_routes as gr

    borc = _borc_87_slice()
    pays = _pays_87_pattern()
    # Eski sınıflandırma simülasyonu: hepsi marker → eslesme < 2026 → full 5760 mahsup
    # (yeni kod banka_import'u general+odeme günü yapar → 3840)
    opening_new = gr._ekstre_acilis_bakiyesi_fifo(
        borc, "2026-01-01", pays, "1900-01-01",
    )
    check("yeni_degil_sifir", opening_new > 1000, opening_new)


def test_cok_ay_kapatan_odeme():
    from routes import giris_routes as gr

    borc = {f"2025-{m:02d}-01": 1000.0 for m in range(1, 7)}
    borc.update({f"2026-{m:02d}-01": 1000.0 for m in range(1, 4)})
    pays = [{
        "id": 1,
        "kaynak": "banka_import",
        "fatura_id": None,
        "tarih": "2026-02-01",
        "eslesme_tarihi": "2025-04-01",
        "tutar": 3000.0,
        "tahsilat_aciklama": (
            "|AYLIK_TAH|2025-04-01| |AYLIK_PAY|2025-04-01=1000.00| "
            "|AYLIK_TAH|2025-05-01| |AYLIK_PAY|2025-05-01=1000.00| "
            "|AYLIK_TAH|2025-06-01| |AYLIK_PAY|2025-06-01=1000.00|"
        ),
    }]
    # Ödeme 2026-02 → Devreden (bas=2026-01) dışı; opening = tam 6000
    op = gr._ekstre_acilis_bakiyesi_fifo(borc, "2026-01-01", pays, "1900-01-01")
    check("cok_ay_opening_tam", abs(op - 6000.0) < 0.02, op)


def test_avans_borcsuz():
    from routes import giris_routes as gr

    borc = {"2026-01-01": 500.0, "2026-02-01": 500.0}
    pays = [{
        "id": 1,
        "kaynak": "manuel_makbuz",
        "fatura_id": None,
        "tarih": "2025-12-15",
        "eslesme_tarihi": "2025-12-15",
        "tutar": 200.0,
        "tahsilat_aciklama": "avans",
    }]
    # bas=2026-01: ödeme Devreden'de; borç ayları 2026 → pre_months boş mu?
    # pre_months = iso < 2026-01-01 → boş → opening 0
    op = gr._ekstre_acilis_bakiyesi_fifo(borc, "2026-01-01", pays, "1900-01-01")
    check("avans_pre_bos", op == 0.0, op)
    # bas=2026-03: pre = Oca+Şub=1000; Dec ödemesi FIFO ile 200 düşer → 800
    op2 = gr._ekstre_acilis_bakiyesi_fifo(borc, "2026-03-01", pays, "1900-01-01")
    check("avans_opening_800", abs(op2 - 800.0) < 0.02, op2)


def _net_from_rows(rows):
    tb = ta = 0.0
    for h in rows or []:
        tb += float(h.get("borc") or 0)
        ta += float(h.get("alacak") or 0)
    bak = None
    if rows:
        bak = rows[-1].get("bakiye")
    return round(tb, 2), round(ta, 2), bak


def _stmt_bakiye_direct(borc, pays, bas: str, bit: str, gr) -> float:
    """Kendi-satır (ödeme günü) modelinde ekstre son bakiyesi.

    Devreden net (= açılış) + dönem kira − dönem görüntü-tarihli ödemeler.
    """
    floor = "1900-01-01"
    op = gr._ekstre_acilis_bakiyesi_fifo(borc, bas, pays, floor)
    # bit ayının sonuna kadar aylık borç (bit ISO ay başı ise o ay dahil)
    bit_m = bit[:8] + "01" if len(bit) >= 10 else bit
    kira = round(
        sum(float(v) for iso, v in borc.items() if bas <= str(iso) <= bit_m),
        2,
    )
    alacak = round(
        sum(
            float(p["tutar"])
            for p in pays
            if bas <= gr._ekstre_satir_goruntu_tarihi(p) <= bit
        ),
        2,
    )
    return round(op + kira - alacak, 2)


def test_degismezlik_property_synthetic():
    """Devreden_bakiye(bas) + dönem net = tam dönem (epoch→bit) bakiyesi.

    Kendi-satır modelinde (ödeme günü): pre-ödeme toplamı ≤ pre-kira olacak
    şekilde üretilir → FIFO taşması olmaz, eşitlik aritmetik tutulur.
    Kaynak karışımı + yıl sınırı + çok ay + avans + grid_toplu görüntü kuralı.
    """
    from routes import giris_routes as gr

    borc = {}
    for y, lo, hi in ((2024, 7, 12), (2025, 1, 12), (2026, 1, 6)):
        for m in range(lo, hi + 1):
            borc[f"{y}-{m:02d}-01"] = 800.0
    rng = random.Random(7)
    kaynaklar = ("banka_import", "manuel_makbuz", "odeme_linki")
    pays = []
    tid = 1
    # kontrollü: her ay en fazla bir 400'lük ödeme (pre taşmasın)
    for y, lo, hi in ((2024, 8, 12), (2025, 1, 12), (2026, 1, 5)):
        for m in range(lo, hi + 1):
            odeme = f"{y}-{m:02d}-15"
            # yari: marker bir ay geri (yil siniri senaryosu)
            if m == 1:
                esl = f"{y - 1}-12-01"
            else:
                esl = f"{y}-{m - 1:02d}-01"
            kay = kaynaklar[tid % len(kaynaklar)]
            tut = 400.0
            if tid % 5 == 0:
                # cok ay: 800 = iki ay
                tut = 800.0
                ac = (
                    f"|AYLIK_TAH|{esl}| |AYLIK_PAY|{esl}=400.00| "
                    f"|AYLIK_TAH|{y}-{m:02d}-01| |AYLIK_PAY|{y}-{m:02d}-01=400.00|"
                )
            elif tid % 9 == 0:
                ac = "avans"
                esl = odeme
            else:
                ac = f"|AYLIK_TAH|{esl}| |AYLIK_PAY|{esl}={tut:.2f}|"
            pays.append({
                "id": tid,
                "kaynak": kay,
                "fatura_id": None,
                "tarih": odeme,
                "tahsilat_tarihi": odeme,
                "eslesme_tarihi": esl,
                "tutar": tut,
                "tahsilat_aciklama": ac,
            })
            tid += 1

    gt = {
        "id": tid,
        "kaynak": "grid_toplu",
        "fatura_id": None,
        "tarih": "2026-03-15",
        "eslesme_tarihi": "2025-12-01",
        "tutar": 800.0,
        "tahsilat_aciklama": "|AYLIK_TAH|2025-12-01| |AYLIK_PAY|2025-12-01=800.00|",
    }
    check(
        "grid_toplu_goruntu_eslesme",
        gr._ekstre_satir_goruntu_tarihi(gt) == "2025-12-01",
    )
    op_gt = gr._ekstre_acilis_bakiyesi_fifo(
        borc, "2026-01-01", [gt], "1900-01-01",
    )
    pre_sum = sum(v for iso, v in borc.items() if iso < "2026-01-01")
    check("grid_toplu_acilis_etkiler", op_gt < pre_sum - 0.01, op_gt)

    bit = "2026-06-15"
    bit_m = "2026-06-01"
    pays_kendi = list(pays)  # grid_toplu ayri
    full = _stmt_bakiye_direct(borc, pays_kendi, "1900-01-01", bit, gr)
    yil = _stmt_bakiye_direct(borc, pays_kendi, "2026-01-01", bit, gr)
    check("degismezlik_yilbasi", abs(yil - full) < 0.02, (yil, full))

    for _ in range(16):
        bas_d = date(2024, 9, 1) + timedelta(days=30 * rng.randint(0, 16))
        bas = date(bas_d.year, bas_d.month, 1).isoformat()
        if bas > bit_m:
            continue
        part = _stmt_bakiye_direct(borc, pays_kendi, bas, bit, gr)
        check(
            "degismezlik_%s" % bas,
            abs(part - full) < 0.02,
            (part, full),
        )
        in_dev = {
            p["id"] for p in pays_kendi
            if gr._ekstre_satir_goruntu_tarihi(p) < bas
        }
        in_don = {
            p["id"] for p in pays_kendi
            if bas <= gr._ekstre_satir_goruntu_tarihi(p) <= bit
        }
        check("kesisim_yok_%s" % bas, not (in_dev & in_don), in_dev & in_don)


def test_pasif_mid_acilis_imza():
    """Pasif senaryoda da görüntü tarihi fonksiyonu aynı (kaynak kuralı)."""
    from routes import giris_routes as gr

    p = {
        "kaynak": "odeme_linki",
        "fatura_id": None,
        "tarih": "2025-06-01",
        "eslesme_tarihi": "2024-01-01",
    }
    check("pasif_odeme_linki", gr._ekstre_satir_goruntu_tarihi(p) == "2025-06-01")


def test_87_stmt_degismezlik():
    """mid=87 kalıbı: yilbasi stmt bakiyesi = tam dönem stmt bakiyesi."""
    from routes import giris_routes as gr

    borc = _borc_87_slice()
    pays = _pays_87_pattern()
    bit = "2026-10-16"
    full = _stmt_bakiye_direct(borc, pays, "1900-01-01", bit, gr)
    yil = _stmt_bakiye_direct(borc, pays, "2026-01-01", bit, gr)
    check("87_stmt_esit", abs(full - yil) < 0.02, (full, yil))
    # beklenen ~ 5414.98 civarı (borc dilimine göre)
    op = gr._ekstre_acilis_bakiyesi_fifo(borc, "2026-01-01", pays, "1900-01-01")
    check("87_opening_3840", abs(op - 3840.0) < 0.02, op)


if __name__ == "__main__":
    test_goruntu_tarihi_kurallari()
    test_acilis_87_yil_basi()
    test_eski_kural_cift_sayardi()
    test_cok_ay_kapatan_odeme()
    test_avans_borcsuz()
    test_degismezlik_property_synthetic()
    test_pasif_mid_acilis_imza()
    test_87_stmt_degismezlik()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
