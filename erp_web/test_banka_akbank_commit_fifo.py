# -*- coding: utf-8 -*-
"""Akbank commit: mid başına tarih artan sıra + batch_onceki PAY. Canlı DB yazmaz."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ay_fifo import acik_ay_dagit, dagit_seri
from routes.banka_routes import _akbank_commit_items_sorted
from routes.faturalar_routes import _auto_allocate_oldest_unpaid_months

ROOT = Path(__file__).resolve().parent
FAILS: list[str] = []

# mid=87 kalıbı (960×12 Tem25–Haz26 borcu)
PAYMENTS_87 = [
    ("2025-11-19", 960.00),
    ("2025-11-20", 960.00),
    ("2026-01-16", 960.00),
    ("2026-01-26", 960.00),
    ("2026-02-06", 2880.00),
    ("2026-06-08", 1920.00),
    ("2026-08-25", 2534.98),
]


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _borc_87() -> dict[str, float]:
    out: dict[str, float] = {}
    for m in range(7, 13):
        out[f"2025-{m:02d}-01"] = 960.0
    for m in range(1, 7):
        out[f"2026-{m:02d}-01"] = 960.0
    return out


def _month_totals(pay_lists) -> dict[str, float]:
    tot: dict[str, float] = defaultdict(float)
    for pays in pay_lists:
        for iso, p in pays:
            tot[str(iso)[:10]] = round(tot[str(iso)[:10]] + float(p), 2)
    return dict(sorted(tot.items()))


def _expected_87() -> dict[str, float]:
    exp = {f"2025-{m:02d}-01": 960.0 for m in range(7, 13)}
    exp.update({f"2026-{m:02d}-01": 960.0 for m in range(1, 6)})
    exp["2026-06-01"] = 614.98
    return exp


def _simulate_commit_allocate(payments, borc, onceki_db=None):
    """api_akbank_tahsilat_commit dağıtım döngüsünün saf simülasyonu (PAY'li onceki)."""
    batch_onceki: list[dict] = []
    out = []
    sira = 0
    for tah, tut in payments:
        pays = acik_ay_dagit(
            borc,
            float(tut),
            tah,
            onceki=list(onceki_db or []) + batch_onceki,
        )
        out.append((tah, float(tut), pays))
        sira += 1
        ac_parts = []
        for iso, p in pays or []:
            ac_parts.append(f"|AYLIK_TAH|{iso}|")
            ac_parts.append(f"|AYLIK_PAY|{iso}={float(p):.2f}|")
        batch_onceki.append({
            "tarih": tah,
            "tutar": round(float(tut), 2),
            "id": 10**12 + sira,
            "aciklama": " ".join(ac_parts),
            "pay": list(pays or []),
        })
    return out


def _rows_to_items(payments, mid=87, newest_first=False, shuffled=None):
    seq = list(payments)
    if shuffled is not None:
        seq = [payments[i] for i in shuffled]
    elif newest_first:
        seq = list(reversed(payments))
    items = []
    for i, (tah, tut) in enumerate(seq):
        items.append({
            "onay": True,
            "musteri_id": mid,
            "tutar": tut,
            "tahsilat_tarihi": tah,
            "banka_referans_no": f"R{i}-{tah}",
            "aciklama": "Banka tahsilat",
            "tip": "gelen",
        })
    return items


def test_static_source():
    banka = (ROOT / "routes" / "banka_routes.py").read_text(encoding="utf-8")
    check("sirala helper var", "def _akbank_commit_items_sorted" in banka)
    check("commit siraliyor", "items = _akbank_commit_items_sorted(items)" in banka)
    check("ek_onceki duruyor", "ek_onceki=batch_onceki.get(mid)" in banka)
    check("onceki pay tasir", '"pay": list(pay_items or [])' in banka or '"pay": list(pay_items' in banka)
    check("onceki aciklama tasir", '"aciklama": aciklama' in banka)
    check("mukerrer ref duruyor", "banka_referans_no = %s" in banka)
    # UI dir=-1 dokunulmadi
    html = (ROOT / "templates" / "bankalar" / "akbank_tahsilat_import.html").read_text(encoding="utf-8")
    check("UI dir=-1 duruyor", 'sortState = { key: "tarih", dir: -1 }' in html)


def test_sort_helper():
    items = _rows_to_items(PAYMENTS_87, newest_first=True)
    # iki mid karışık
    items.append({
        "onay": True,
        "musteri_id": 99,
        "tutar": 100,
        "tahsilat_tarihi": "2026-01-01",
        "banka_referans_no": "X99",
        "aciklama": "x",
        "tip": "gelen",
    })
    items.insert(0, {"onay": False, "musteri_id": 87, "tahsilat_tarihi": "2020-01-01", "tutar": 1})
    sorted_items = _akbank_commit_items_sorted(items)
    onayli = [it for it in sorted_items if it.get("onay")]
    mids_dates = [(int(it["musteri_id"]), it["tahsilat_tarihi"][:10]) for it in onayli]
    # mid 87 chronologic then mid 99
    only87 = [d for m, d in mids_dates if m == 87]
    check("87 artan tarih", only87 == sorted(only87), str(only87))
    check("mid ayrimi: 87 sonra 99 veya gruplu", mids_dates[-1][0] == 99 or all(
        mids_dates[i][0] <= mids_dates[i + 1][0] for i in range(len(mids_dates) - 1)
    ), str(mids_dates))
    # aynı gün kararlı: iki satır aynı tarih, orijinal indeks sırası
    same = [
        {"onay": True, "musteri_id": 1, "tahsilat_tarihi": "2026-03-01", "tutar": 10, "banka_referans_no": "a"},
        {"onay": True, "musteri_id": 1, "tahsilat_tarihi": "2026-03-01", "tutar": 20, "banka_referans_no": "b"},
    ]
    s2 = _akbank_commit_items_sorted(same)
    check("ayni gun kararli", [x["tutar"] for x in s2] == [10, 20])


def test_87_three_orders_same():
    borc = _borc_87()
    exp = _expected_87()
    orders = {
        "oldest": PAYMENTS_87,
        "newest": list(reversed(PAYMENTS_87)),
        "mixed": [PAYMENTS_87[i] for i in (4, 0, 6, 2, 5, 1, 3)],
    }
    totals = {}
    for name, seq in orders.items():
        # sunucu gibi önce sırala
        items = _rows_to_items(seq, newest_first=False)
        sorted_items = _akbank_commit_items_sorted(items)
        pays_seq = [
            (it["tahsilat_tarihi"][:10], float(it["tutar"]))
            for it in sorted_items if it.get("onay")
        ]
        sim = _simulate_commit_allocate(pays_seq, borc)
        tot = _month_totals([p for _, _, p in sim])
        totals[name] = tot
        check(f"87 {name} ay map", tot == exp, str(tot))
        check(f"87 {name} toplam", abs(sum(tot.values()) - 11174.98) < 0.02, sum(tot.values()))
    check("87 3 sira ayni", totals["oldest"] == totals["newest"] == totals["mixed"])


def test_same_day_two_rows():
    borc = {"2025-07-01": 960.0, "2025-08-01": 960.0}
    items = [
        {"onay": True, "musteri_id": 1, "tahsilat_tarihi": "2025-11-01", "tutar": 960, "banka_referans_no": "d1"},
        {"onay": True, "musteri_id": 1, "tahsilat_tarihi": "2025-11-01", "tutar": 960, "banka_referans_no": "d2"},
    ]
    sorted_items = _akbank_commit_items_sorted(items)
    seq = [(it["tahsilat_tarihi"][:10], float(it["tutar"])) for it in sorted_items]
    sim = _simulate_commit_allocate(seq, borc)
    check("ayni gun ilk→Tem", sim[0][2] == [("2025-07-01", 960.0)], str(sim[0][2]))
    check("ayni gun ikinci→Agu", sim[1][2] == [("2025-08-01", 960.0)], str(sim[1][2]))


def test_tek_satir():
    borc = _borc_87()
    sim = _simulate_commit_allocate([("2025-11-19", 960.0)], borc)
    check("tek satir Tem", sim[0][2] == [("2025-07-01", 960.0)], str(sim[0][2]))


def test_iki_mid_izole():
    borc_a = {"2025-07-01": 960.0, "2025-08-01": 960.0}
    borc_b = {"2025-07-01": 500.0}
    # newest-first UI: mid B new, mid A old mixed
    items = [
        {"onay": True, "musteri_id": 2, "tahsilat_tarihi": "2026-08-01", "tutar": 500, "banka_referans_no": "b1"},
        {"onay": True, "musteri_id": 1, "tahsilat_tarihi": "2025-12-01", "tutar": 960, "banka_referans_no": "a2"},
        {"onay": True, "musteri_id": 1, "tahsilat_tarihi": "2025-11-01", "tutar": 960, "banka_referans_no": "a1"},
    ]
    sorted_items = _akbank_commit_items_sorted(items)
    # mid 1 first (smaller mid), then mid 2 — within mid1 chronological
    batch: dict[int, list] = {}
    results = {}
    for it in sorted_items:
        mid = int(it["musteri_id"])
        borc = borc_a if mid == 1 else borc_b
        tah = it["tahsilat_tarihi"][:10]
        tut = float(it["tutar"])
        pays = acik_ay_dagit(borc, tut, tah, onceki=batch.get(mid))
        results.setdefault(mid, []).append((tah, pays))
        ac = " ".join(f"|AYLIK_PAY|{iso}={p:.2f}|" for iso, p in pays)
        batch.setdefault(mid, []).append({
            "tarih": tah, "tutar": tut, "id": len(batch.get(mid, [])) + 1,
            "aciklama": ac, "pay": pays,
        })
    check("mid1 iki ay", len(results[1]) == 2 and results[1][0][1][0][0] == "2025-07-01")
    check("mid1 ikinci Agu", results[1][1][1][0][0] == "2025-08-01", str(results[1]))
    check("mid2 kendi borcu", results[2][0][1] == [("2025-07-01", 500.0)], str(results[2]))


def test_db_onceki_var_yeni_batch():
    borc = _borc_87()
    db_onceki = [{
        "tarih": "2025-10-01",
        "tutar": 960.0,
        "id": 1,
        "aciklama": "|AYLIK_TAH|2025-07-01| |AYLIK_PAY|2025-07-01=960.00|",
        "pay": [("2025-07-01", 960.0)],
    }]
    sim = _simulate_commit_allocate(
        [("2025-11-19", 960.0), ("2025-11-20", 960.0)],
        borc,
        onceki_db=db_onceki,
    )
    check("db onceki sonrasi Agu", sim[0][2][0][0] == "2025-08-01", str(sim[0][2]))
    check("db onceki 2. Eyl", sim[1][2][0][0] == "2025-09-01", str(sim[1][2]))


def test_8a945f4_ileri_tarih_elenir():
    """DB'de daha YENİ ödeme var; batch'te eski tarihli satır — ileri onceki düşmez."""
    borc = {"2025-07-01": 960.0, "2025-08-01": 960.0, "2025-09-01": 960.0}
    # DB: 2026-08 ödemesi Temmuz'u kapatmış
    db_yeni = [{
        "tarih": "2026-08-25",
        "tutar": 960.0,
        "id": 99,
        "aciklama": "|AYLIK_PAY|2025-07-01=960.00|",
        "pay": [("2025-07-01", 960.0)],
    }]
    # Batch eski: 2025-11 — onceki filtre tarih>odeme ile 2026-08'i ELER
    pays = acik_ay_dagit(borc, 960.0, "2025-11-19", onceki=db_yeni)
    # İleri onceki elendiği için Temmuz hâlâ açık görünür (bilinçli kural)
    check(
        "8a945f4 ileri onceki elenir→Tem tekrar",
        pays and pays[0][0] == "2025-07-01",
        str(pays),
    )


def test_borc_yok_avans():
    pays = acik_ay_dagit({}, 960.0, "2025-11-19", onceki=[])
    check("borc yok bos dagitim", pays == [])


def test_kismi_ay_devami():
    borc = {"2025-07-01": 960.0, "2025-08-01": 960.0}
    sim = _simulate_commit_allocate(
        [("2025-11-01", 614.98), ("2025-12-01", 960.0)],
        borc,
    )
    check("kismi ilk Tem", sim[0][2] == [("2025-07-01", 614.98)], str(sim[0][2]))
    check(
        "kismi sonra kalan Tem+Agu",
        sim[1][2] == [("2025-07-01", 345.02), ("2025-08-01", 614.98)],
        str(sim[1][2]),
    )


def test_idempotency_static():
    banka = (ROOT / "routes" / "banka_routes.py").read_text(encoding="utf-8")
    # commit gövdesinde mükerrer ref kontrolü
    i = banka.find("def api_akbank_tahsilat_commit")
    chunk = banka[i:i + 8000]
    check("idempotent ref SELECT", "SELECT 1 FROM tahsilatlar WHERE banka_referans_no" in chunk)


def test_auto_allocate_ek_onceki_pay():
    """_auto_allocate yolu da PAY'li ek_onceki ile ilerler (commit ile aynı)."""
    borc = _borc_87()
    _a, ilk = _auto_allocate_oldest_unpaid_months(
        1, 960, odeme_tarihi="2025-11-19", borc_satirlari=borc, onceki_tahsilatlar=[],
    )
    _b, ikinci = _auto_allocate_oldest_unpaid_months(
        1,
        960,
        odeme_tarihi="2025-11-20",
        borc_satirlari=borc,
        onceki_tahsilatlar=[],
        ek_onceki=[{
            "tarih": "2025-11-19",
            "tutar": 960,
            "id": 10**12 + 1,
            "pay": list(ilk),
            "aciklama": "|AYLIK_PAY|2025-07-01=960.00|",
        }],
    )
    check("allocate ilk Tem", ilk[0] == ("2025-07-01", 960.0), str(ilk))
    check("allocate ikinci Agu", ikinci[0] == ("2025-08-01", 960.0), str(ikinci))


def test_dagit_seri_uyum():
    borc = _borc_87()
    items = [
        {"id": i, "tarih": t, "tutar": tut}
        for i, (t, tut) in enumerate(PAYMENTS_87, 1)
    ]
    seri = dagit_seri(borc, items)
    tot = _month_totals(seri)
    check("dagit_seri beklenen", tot == _expected_87(), str(tot))


if __name__ == "__main__":
    test_static_source()
    test_sort_helper()
    test_87_three_orders_same()
    test_same_day_two_rows()
    test_tek_satir()
    test_iki_mid_izole()
    test_db_onceki_var_yeni_batch()
    test_8a945f4_ileri_tarih_elenir()
    test_borc_yok_avans()
    test_kismi_ay_devami()
    test_idempotency_static()
    test_auto_allocate_ek_onceki_pay()
    test_dagit_seri_uyum()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    raise SystemExit(1 if FAILS else 0)
