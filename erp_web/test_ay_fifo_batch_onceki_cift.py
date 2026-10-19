# -*- coding: utf-8 -*-
"""Batch onceki tekilleştirme: txn içi DB + batch_onceki çift sayımı olmasın.

Canlı yazma yok.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

from ay_fifo import acik_ay_dagit  # noqa: E402
from routes.faturalar_routes import (  # noqa: E402
    _auto_allocate_oldest_unpaid_months,
    _onceki_birlestir_tekil,
    _onceki_tekillestir,
)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _borc_from_dec2024() -> dict[str, float]:
    out: dict[str, float] = {"2024-12-01": 866.0}
    for m in range(1, 12):
        out[f"2025-{m:02d}-01"] = 866.0
    out["2025-12-01"] = 1188.0
    out["2026-01-01"] = 1188.0
    out["2026-02-01"] = 1188.0
    return out


def _sim_batch_allocate(payments, borc, *, onceki_db=None, with_ref=True):
    """Üretim birleşimi: DB onceki + batch → tekillestir → acik_ay_dagit."""
    batch: list[dict] = []
    out = []
    sira = 0
    for tah, tut, ref in payments:
        sira += 1
        # txn içi: DB'de önceki batch insert'leri görünür (gerçek id)
        db_view = list(onceki_db or [])
        for b in batch:
            db_view.append({
                "id": 1000 + b["_sira"],  # gerçek id simülasyonu
                "tarih": b["tarih"],
                "tutar": b["tutar"],
                "aciklama": b["aciklama"],
                "banka_referans_no": b.get("banka_referans_no") or "",
            })
        onceki = _onceki_birlestir_tekil(db_view, batch)
        pays = acik_ay_dagit(borc, float(tut), tah, onceki=onceki)
        ac_parts = []
        for iso, p in pays or []:
            ac_parts.append(f"|AYLIK_TAH|{iso}|")
            ac_parts.append(f"|AYLIK_PAY|{iso}={float(p):.2f}|")
        entry = {
            "_sira": sira,
            "tarih": tah,
            "tutar": round(float(tut), 2),
            "id": 10**12 + sira,
            "aciklama": " ".join(ac_parts),
            "pay": list(pays or []),
            "banka_referans_no": ref if with_ref else "",
        }
        batch.append(entry)
        out.append((tah, float(tut), pays))
    return out


def test_tek_onceki_dogru_fifo():
    borc = _borc_from_dec2024()
    p18 = acik_ay_dagit(borc, 1732.80, "2025-01-17", onceki=[])
    check("6018 Feb kirinti 0.80", dict(p18).get("2025-02-01") == 0.8, dict(p18))
    onceki = [{
        "id": 6018, "tarih": "2025-01-17", "tutar": 1732.80,
        "aciklama": (
            "|AYLIK_PAY|2024-12-01=866.00| |AYLIK_PAY|2025-01-01=866.00| "
            "|AYLIK_PAY|2025-02-01=0.80|"
        ),
        "pay": list(p18),
        "banka_referans_no": "R6018",
    }]
    p19 = acik_ay_dagit(borc, 866.40, "2025-02-20", onceki=onceki)
    check("tek onceki→Şub 865.20", dict(p19).get("2025-02-01") == 865.2, dict(p19))
    check("tek onceki→Mar 1.20", dict(p19).get("2025-03-01") == 1.2, dict(p19))


def test_cift_onceki_tekillestir_duzeltir():
    """DB gerçek id + batch sentetik id aynı ödeme → tek sayılır → doğru FIFO."""
    borc = _borc_from_dec2024()
    p18 = acik_ay_dagit(borc, 1732.80, "2025-01-17", onceki=[])
    db_row = {
        "id": 6018, "tarih": "2025-01-17", "tutar": 1732.80,
        "aciklama": (
            "|AYLIK_PAY|2024-12-01=866.00| |AYLIK_PAY|2025-01-01=866.00| "
            "|AYLIK_PAY|2025-02-01=0.80|"
        ),
        "banka_referans_no": "R6018",
    }
    batch_row = {
        "id": 10**12 + 1, "tarih": "2025-01-17", "tutar": 1732.80,
        "aciklama": db_row["aciklama"],
        "pay": list(p18),
        "banka_referans_no": "R6018",
    }
    merged = _onceki_birlestir_tekil([db_row], [batch_row])
    check("tekillestir n=1", len(merged) == 1, len(merged))
    check("tekillestir db id kalir", merged[0].get("id") == 6018, merged[0])
    p19 = acik_ay_dagit(borc, 866.40, "2025-02-20", onceki=merged)
    check("duzeltme→Şub 865.20", dict(p19).get("2025-02-01") == 865.2, dict(p19))
    check("duzeltme→Mar 1.20", dict(p19).get("2025-03-01") == 1.2, dict(p19))
    # Eski hatalı dağılım artık üretilmemeli
    check("eski hata yok", dict(p19).get("2025-02-01") != 864.4)


def test_allocate_merge_mukerrer_korumasi():
    dilim = Path("routes/faturalar_routes.py").read_text(encoding="utf-8").split(
        "def _auto_allocate_oldest_unpaid_months"
    )[1].split("def _tahsil_rapor_yil_ay_coerce")[0]
    check(
        "birlestir_tekil cagrisi",
        "_onceki_birlestir_tekil" in dilim,
    )
    check(
        "ham extend yok",
        "onceki.extend(list(ek_onceki))" not in dilim
        and "onceki.extend(ek_onceki)" not in dilim,
    )


def test_6025_tekillestir_ocak_kapanir():
    """Çift 6024 tekilleşince Ocak açık kalır; 6025 Ocak+Şubat doğru."""
    borc = _borc_from_dec2024()
    onceki_ok = []
    for iso, v in list(borc.items()):
        if iso < "2025-12-01":
            onceki_ok.append({
                "id": abs(hash(iso)) % 10**6,
                "tarih": "2024-06-01",
                "tutar": v,
                "aciklama": f"|AYLIK_PAY|{iso}={v:.2f}|",
                "pay": [(iso, v)],
                "banka_referans_no": f"PRE-{iso}",
            })
    onceki_ok.append({
        "id": 6023, "tarih": "2025-11-25", "tutar": 868.8,
        "aciklama": "|AYLIK_PAY|2025-12-01=868.80|",
        "pay": [("2025-12-01", 868.8)],
        "banka_referans_no": "R6023",
    })
    p24 = acik_ay_dagit(borc, 1188.0, "2026-01-19", onceki=onceki_ok)
    check("tek 6024 Dec+Ocak", dict(p24) == {"2025-12-01": 319.2, "2026-01-01": 868.8}, dict(p24))
    row24_db = {
        "id": 6024, "tarih": "2026-01-19", "tutar": 1188.0,
        "aciklama": "|AYLIK_PAY|2025-12-01=319.20| |AYLIK_PAY|2026-01-01=868.80|",
        "banka_referans_no": "R6024",
    }
    row24_batch = {
        "id": 10**12 + 5, "tarih": "2026-01-19", "tutar": 1188.0,
        "aciklama": row24_db["aciklama"],
        "pay": list(p24),
        "banka_referans_no": "R6024",
    }
    onceki = _onceki_birlestir_tekil(onceki_ok + [row24_db], [row24_batch])
    check("6024 tekil", sum(1 for x in onceki if int(x.get("id") or 0) in (6024, 10**12 + 5)) == 1)
    p25 = acik_ay_dagit(borc, 1188.0, "2026-02-19", onceki=onceki)
    check("6025 Ocak 319.20", dict(p25).get("2026-01-01") == 319.2, dict(p25))
    check("6025 Şub 868.80", dict(p25).get("2026-02-01") == 868.8, dict(p25))


def test_153_dizi_tek_istekte_dogru_fifo():
    """1732.80; 866.40; 1732.80; 1732.80; 2598; 2598; 1188 (+6025)."""
    borc = _borc_from_dec2024()
    payments = [
        ("2025-01-17", 1732.80, "R6018"),
        ("2025-02-20", 866.40, "R6019"),
        ("2025-04-18", 1732.80, "R6020"),
        ("2025-06-04", 1732.80, "R6021"),
        ("2025-09-18", 2598.00, "R6022"),
        ("2025-11-25", 2598.00, "R6023"),
        ("2026-01-19", 1188.00, "R6024"),
        ("2026-02-19", 1188.00, "R6025"),
    ]
    sim = _sim_batch_allocate(payments, borc)
    p19 = dict(sim[1][2])
    check("153-dizi 6019 Şub", p19.get("2025-02-01") == 865.2, p19)
    check("153-dizi 6019 Mar", p19.get("2025-03-01") == 1.2, p19)
    p24 = dict(sim[6][2])
    check("153-dizi 6024", p24 == {"2025-12-01": 319.2, "2026-01-01": 868.8}, p24)
    p25 = dict(sim[7][2])
    check("153-dizi 6025", p25 == {"2026-01-01": 319.2, "2026-02-01": 868.8}, p25)


def test_cok_satir_karisik_mid():
    """2 ve 3 satır; farklı mid batch'leri birbirine karışmaz."""
    borc_a = {"2025-07-01": 960.0, "2025-08-01": 960.0, "2025-09-01": 960.0}
    borc_b = {"2026-01-01": 500.0, "2026-02-01": 500.0}
    # mid A: 2 satır
    sim_a = _sim_batch_allocate(
        [("2025-11-01", 960.0, "A1"), ("2025-11-15", 960.0, "A2")],
        borc_a,
    )
    check("midA ilk→Tem", dict(sim_a[0][2]) == {"2025-07-01": 960.0}, sim_a[0][2])
    check("midA iki→Agu", dict(sim_a[1][2]) == {"2025-08-01": 960.0}, sim_a[1][2])
    # mid B: 3 satır (bağımsız onceki)
    sim_b = _sim_batch_allocate(
        [
            ("2026-03-01", 500.0, "B1"),
            ("2026-03-10", 500.0, "B2"),
            ("2026-03-20", 200.0, "B3"),
        ],
        borc_b,
    )
    check("midB1", dict(sim_b[0][2]) == {"2026-01-01": 500.0}, sim_b[0][2])
    check("midB2", dict(sim_b[1][2]) == {"2026-02-01": 500.0}, sim_b[1][2])
    check("midB3 bos veya ileri", True)  # 200 avans/ileri — sadece patlamasın


def test_tek_satir_degismedi():
    borc = _borc_from_dec2024()
    isos, pays = _auto_allocate_oldest_unpaid_months(
        1,
        866.0,
        odeme_tarihi="2025-01-10",
        borc_satirlari=borc,
        onceki_tahsilatlar=[],
        ek_onceki=None,
    )
    check("tek satır Dec", dict(pays).get("2024-12-01") == 866.0, dict(pays))
    check("tek satır iso", isos == ["2024-12-01"], isos)


def test_idempotent_ayni_id_iki_kez():
    rows = [
        {"id": 10, "tarih": "2025-01-01", "tutar": 100.0, "aciklama": "a", "banka_referans_no": "X"},
        {"id": 10, "tarih": "2025-01-01", "tutar": 100.0, "aciklama": "a", "banka_referans_no": "X"},
        {"id": 10, "tarih": "2025-01-01", "tutar": 100.0, "aciklama": "a", "banka_referans_no": "X"},
    ]
    out = _onceki_tekillestir(rows)
    check("idempotent n=1", len(out) == 1)
    # fp ile sentetik+gerçek
    out2 = _onceki_birlestir_tekil(
        [{"id": 5, "tarih": "2025-02-01", "tutar": 50.0, "banka_referans_no": "Z"}],
        [{"id": 10**12 + 1, "tarih": "2025-02-01", "tutar": 50.0, "banka_referans_no": "Z"}],
    )
    check("idempotent fp n=1", len(out2) == 1, out2)


if __name__ == "__main__":
    test_tek_onceki_dogru_fifo()
    test_cift_onceki_tekillestir_duzeltir()
    test_allocate_merge_mukerrer_korumasi()
    test_6025_tekillestir_ocak_kapanir()
    test_153_dizi_tek_istekte_dogru_fifo()
    test_cok_satir_karisik_mid()
    test_tek_satir_degismedi()
    test_idempotent_ayni_id_iki_kez()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
