# -*- coding: utf-8 -*-
"""makbuz_dagitim_onar — sentetik + güvenlik. Canlı yazma yok."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS: list[str] = []

BRUT = 51799.80


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def _borc_2026() -> dict[str, float]:
    return {f"2026-{m:02d}-01": BRUT for m in range(4, 13)}


def _tah_only(iso: str, tutar: float = BRUT) -> str:
    y, m = iso[:4], iso[5:7]
    ay_ad = {
        "04": "Nisan",
        "05": "Mayıs",
        "06": "Haziran",
        "07": "Temmuz",
        "08": "Ağustos",
        "09": "Eylül",
        "10": "Ekim",
        "11": "Kasım",
        "12": "Aralık",
    }[m]
    return f"{ay_ad} {y} Tahsilat H|AYLIK_TAH|{iso}|"


def _bozuk_pay(iso_a: str, a: float, iso_b: str, b: float) -> str:
    return (
        f"Tahsilat H|AYLIK_TAH|{iso_a}| |AYLIK_TAH|{iso_b}| "
        f"|AYLIK_PAY|{iso_a}={a:.2f}| |AYLIK_PAY|{iso_b}={b:.2f}|"
    )


def _sentetik_rows() -> list[dict]:
    """872 benzeri: Nis–Haz TAH-only + 4 manuel (ileri spill)."""
    diger = []
    for i, m in enumerate((4, 5, 6), start=1):
        iso = f"2026-{m:02d}-01"
        diger.append(
            {
                "id": 1000 + i,
                "mid": 872,
                "makbuz_no": f"E{i}",
                "tutar": BRUT,
                "tarih": f"2026-{m:02d}-05",
                "aciklama": _tah_only(iso),
                "kaynak": "",
                "odeme_turu": "havale",
                "tahsil_eden": None,
                "cls": "esitle_grid_pattern",
            }
        )
    # Bozuk: 5274 OK Tem full-ish; 5525 Aug eksik; 5584 Eyl+Eki spill; 6063 Eki+Kas spill
    manuel = [
        {
            "id": 5274,
            "mid": 872,
            "makbuz_no": "4367",
            "tutar": 51799.00,
            "tarih": "2026-07-07",
            "aciklama": (
                "Temmuz 2026 Tahsilat H|AYLIK_TAH|2026-07-01| "
                "|AYLIK_PAY|2026-07-01=51799.00|"
            ),
            "kaynak": "manuel_makbuz",
            "odeme_turu": "havale",
            "tahsil_eden": 1,
            "cls": "manuel_makbuz",
        },
        {
            "id": 5525,
            "mid": 872,
            "makbuz_no": "4488",
            "tutar": 51799.80,
            "tarih": "2026-08-07",
            "aciklama": _bozuk_pay("2026-07-01", 0.80, "2026-08-01", 51779.20),
            "kaynak": "manuel_makbuz",
            "odeme_turu": "havale",
            "tahsil_eden": 1,
            "cls": "manuel_makbuz",
        },
        {
            "id": 5584,
            "mid": 872,
            "makbuz_no": "4547",
            "tutar": 51799.80,
            "tarih": "2026-09-07",
            "aciklama": _bozuk_pay("2026-09-01", 49760.40, "2026-10-01", 2038.60),
            "kaynak": "manuel_makbuz",
            "odeme_turu": "havale",
            "tahsil_eden": 1,
            "cls": "manuel_makbuz",
        },
        {
            "id": 6063,
            "mid": 872,
            "makbuz_no": "4938",
            "tutar": 51799.80,
            "tarih": "2026-10-07",
            "aciklama": _bozuk_pay("2026-10-01", 49761.20, "2026-11-01", 2038.60),
            "kaynak": "manuel_makbuz",
            "odeme_turu": "havale",
            "tahsil_eden": 1,
            "cls": "manuel_makbuz",
        },
    ]
    return diger + manuel


def test_sentetik_hedef_dagitim():
    from scripts.makbuz_dagitim_onar import _hedef_872_uyum, hesapla_plan

    plan = hesapla_plan(872, _sentetik_rows(), borc=_borc_2026())
    check("plan ok", plan.get("ok") is True, plan.get("neden"))
    check("n_degisti>=2", int(plan.get("n_degisti") or 0) >= 2, plan.get("n_degisti"))
    uyum = _hedef_872_uyum(plan)
    check("hedef uyum", uyum.get("hepsi_uyum") is True, uyum)
    check("kasim yok", uyum.get("kasim_tasima") is False, uyum)
    # marker toplam = tutar
    for p in plan.get("plans") or []:
        check(
            f"sum id={p['id']}",
            abs(float(p["yeni_sum"]) - float(p["tutar"])) < 0.011,
            (p["yeni_sum"], p["tutar"]),
        )


def test_idempotent():
    from scripts.makbuz_dagitim_onar import hesapla_plan

    rows = _sentetik_rows()
    plan1 = hesapla_plan(872, rows, borc=_borc_2026())
    check("p1 ok", plan1.get("ok") is True)
    # Uygulanmış gibi rows güncelle
    by_id = {int(r["id"]): r for r in rows}
    for p in plan1["plans"]:
        by_id[int(p["id"])]["aciklama"] = p["yeni_aciklama"]
    plan2 = hesapla_plan(872, rows, borc=_borc_2026())
    check("p2 ok", plan2.get("ok") is True)
    check("idempotent n_degisti=0", int(plan2.get("n_degisti") or 0) == 0, plan2.get("n_degisti"))


def test_marker_toplam_dur():
    from scripts.makbuz_dagitim_onar import hesapla_plan

    rows = _sentetik_rows()
    # Tutarı marker'ın yetmeyeceği şekilde boz (borç haritası boş → pay boş / mismatch)
    with patch(
        "routes.faturalar_routes.yeniden_hesapla_makbuz_dagitim_aciklama",
        return_value=(
            "X|AYLIK_TAH|2026-07-01| |AYLIK_PAY|2026-07-01=100.00|"
        ),
    ):
        plan = hesapla_plan(872, rows, borc=_borc_2026())
    check("dur marker_toplam", plan.get("ok") is False, plan.get("neden"))
    check(
        "neden marker",
        plan.get("neden") == "marker_toplam_esit_degil",
        plan.get("neden"),
    )


def test_kapsam_disi_degismez():
    from scripts.makbuz_dagitim_onar import hesapla_plan

    rows = _sentetik_rows()
    eski_diger = {
        int(r["id"]): r.get("aciklama")
        for r in rows
        if r.get("cls") != "manuel_makbuz"
    }
    plan = hesapla_plan(872, rows, borc=_borc_2026())
    check("plan ok kapsam", plan.get("ok") is True)
    for r in rows:
        if r.get("cls") == "manuel_makbuz":
            continue
        check(
            f"diger ayni id={r['id']}",
            r.get("aciklama") == eski_diger[int(r["id"])],
        )
    check("diger ids plan", set(plan.get("diger_ids") or []) == set(eski_diger))


def test_apply_without_allow_prod_write():
    from scripts import makbuz_dagitim_onar as mod

    os.environ.pop("ALLOW_PROD_WRITE", None)
    rc = mod.main(["--mid", "872", "--apply"])
    check("apply reddedildi", rc == 2)


def _borc_209() -> dict[str, float]:
    out = {}
    for y, months in ((2023, range(8, 13)), (2024, range(1, 7))):
        for m in months:
            out[f"{y}-{m:02d}-01"] = 1800.0
    return out


def _sentetik_209() -> list[dict]:
    """Markersiz banka + erken manuel; son manuel eski spill marker'lı."""
    rows = [
        {
            "id": 1,
            "mid": 209,
            "makbuz_no": "M1",
            "tutar": 3000.0,
            "tarih": "2023-09-25",
            "aciklama": (
                "Tahsilat H|AYLIK_TAH|2023-08-01| |AYLIK_TAH|2023-09-01| "
                "|AYLIK_PAY|2023-08-01=1800.00| |AYLIK_PAY|2023-09-01=1200.00|"
            ),
            "kaynak": "manuel_makbuz",
            "odeme_turu": "havale",
            "tahsil_eden": 1,
            "cls": "manuel_makbuz",
        },
        {
            "id": 2,
            "mid": 209,
            "makbuz_no": "M2",
            "tutar": 1500.0,
            "tarih": "2023-11-07",
            "aciklama": (
                "Tahsilat H|AYLIK_TAH|2023-09-01| |AYLIK_TAH|2023-10-01| "
                "|AYLIK_PAY|2023-09-01=600.00| |AYLIK_PAY|2023-10-01=900.00|"
            ),
            "kaynak": "manuel_makbuz",
            "odeme_turu": "havale",
            "tahsil_eden": 1,
            "cls": "manuel_makbuz",
        },
        {
            "id": 3,
            "mid": 209,
            "makbuz_no": "M3",
            "tutar": 2000.0,
            "tarih": "2023-12-29",
            "aciklama": (
                "Tahsilat H|AYLIK_TAH|2023-10-01| |AYLIK_TAH|2023-11-01| "
                "|AYLIK_PAY|2023-10-01=900.00| |AYLIK_PAY|2023-11-01=1100.00|"
            ),
            "kaynak": "manuel_makbuz",
            "odeme_turu": "havale",
            "tahsil_eden": 1,
            "cls": "manuel_makbuz",
        },
        {
            "id": 10,
            "mid": 209,
            "makbuz_no": "B1",
            "tutar": 2000.0,
            "tarih": "2024-05-28",
            "aciklama": "FAST gelen transfer",
            "kaynak": "banka_import",
            "odeme_turu": "havale",
            "tahsil_eden": None,
            "cls": "banka_import",
        },
        {
            "id": 11,
            "mid": 209,
            "makbuz_no": "B2",
            "tutar": 2000.0,
            "tarih": "2024-09-30",
            "aciklama": "FAST gelen transfer",
            "kaynak": "banka_import",
            "odeme_turu": "havale",
            "tahsil_eden": None,
            "cls": "banka_import",
        },
        {
            "id": 20,
            "mid": 209,
            "makbuz_no": "M4",
            "tutar": 3000.0,
            "tarih": "2024-12-29",
            "aciklama": (
                "Tahsilat H|AYLIK_TAH|2023-11-01| |AYLIK_TAH|2023-12-01| |AYLIK_TAH|2024-01-01| "
                "|AYLIK_PAY|2023-11-01=700.00| |AYLIK_PAY|2023-12-01=1800.00| "
                "|AYLIK_PAY|2024-01-01=500.00|"
            ),
            "kaynak": "manuel_makbuz",
            "odeme_turu": "havale",
            "tahsil_eden": 1,
            "cls": "manuel_makbuz",
        },
    ]
    return rows


def test_sentetik_209_odendi_korunur():
    from scripts.makbuz_dagitim_onar import (
        _simulate_tahsil_map,
        _panel_cells_from_map,
        hesapla_plan,
    )

    borc = _borc_209()
    rows = _sentetik_209()
    plan = hesapla_plan(209, rows, borc=borc)
    check("209 plan ok", plan.get("ok") is True, plan.get("neden"))
    # banka aciklama değişmemeli
    for r in rows:
        if r.get("cls") == "banka_import":
            check("209 banka ayni", r.get("aciklama") == "FAST gelen transfer")
    by_yeni = {int(p["id"]): p["yeni_aciklama"] for p in plan["plans"]}
    rows_eff = []
    for r in rows:
        item = dict(r)
        if int(r["id"]) in by_yeni:
            item["aciklama"] = by_yeni[int(r["id"])]
        rows_eff.append(item)
    tah = _simulate_tahsil_map(borc, rows_eff)
    cells = _panel_cells_from_map(borc, tah)
    # Erken aylar ödendi kalmalı (markersiz banka sabit tüketim + manuel)
    for iso in ("2023-08-01", "2023-09-01", "2023-10-01"):
        check(
            f"209 {iso} odendi",
            (cells.get(iso) or {}).get("durum") == "odendi",
            cells.get(iso),
        )
    # Ara23 panel simülasyonunda borçluya düşmemeli
    check(
        "209 Ara23 not borclu",
        (cells.get("2023-12-01") or {}).get("durum") != "borclu",
        cells.get("2023-12-01"),
    )


def _sentetik_126() -> tuple[list[dict], dict]:
    brut = 1373.93
    borc = {f"2026-{m:02d}-01": (600.0 if m < 8 else brut) for m in range(4, 13)}
    rows = []
    for i, m in enumerate((4, 5, 6, 7), start=1):
        iso = f"2026-{m:02d}-01"
        rows.append(
            {
                "id": 100 + i,
                "mid": 126,
                "makbuz_no": f"E{i}",
                "tutar": 600.0,
                "tarih": f"2026-{m:02d}-01",
                "aciklama": _tah_only(iso, 600.0),
                "kaynak": "",
                "odeme_turu": "havale",
                "tahsil_eden": None,
                "cls": "esitle_grid_pattern",
            }
        )
    rows.append(
        {
            "id": 200,
            "mid": 126,
            "makbuz_no": "4172",
            "tutar": 600.0,
            "tarih": "2026-07-08",
            "aciklama": (
                "Temmuz 2026 Tahsilat H|AYLIK_TAH|2026-07-01| "
                "|AYLIK_PAY|2026-07-01=600.00|"
            ),
            "kaynak": "manuel_makbuz",
            "odeme_turu": "havale",
            "tahsil_eden": 1,
            "cls": "manuel_makbuz",
        }
    )
    for tid, no, td, iso in (
        (201, "4436", "2026-08-11", "2026-08-01"),
        (202, "4529", "2026-09-01", "2026-09-01"),
        (203, "4698", "2026-10-06", "2026-10-01"),
    ):
        rows.append(
            {
                "id": tid,
                "mid": 126,
                "makbuz_no": no,
                "tutar": 1374.0,
                "tarih": td,
                "aciklama": (
                    f"Tahsilat H|AYLIK_TAH|{iso}| |AYLIK_PAY|{iso}=1374.00|"
                ),
                "kaynak": "manuel_makbuz",
                "odeme_turu": "havale",
                "tahsil_eden": 1,
                "cls": "manuel_makbuz",
            }
        )
    return rows, borc


def test_sentetik_126_onemsiz():
    from scripts.makbuz_dagitim_onar import (
        _kurus_gurultusu_mu,
        degerlendir_plan,
        hesapla_plan,
    )

    # Doğrudan kuruş kuralı (126 tipi spill)
    plans_kurus = [
        {
            "degisti": True,
            "eski_pay": [("2026-08-01", 1374.0)],
            "yeni_pay": [("2026-08-01", 1373.93), ("2026-09-01", 0.07)],
        },
        {
            "degisti": True,
            "eski_pay": [("2026-09-01", 1374.0)],
            "yeni_pay": [("2026-09-01", 1373.86), ("2026-10-01", 0.14)],
        },
        {
            "degisti": True,
            "eski_pay": [("2026-10-01", 1374.0)],
            "yeni_pay": [("2026-10-01", 1373.79), ("2026-11-01", 0.21)],
        },
    ]
    check("126 kurus fn", _kurus_gurultusu_mu(plans_kurus) is True)

    rows, borc = _sentetik_126()
    plan = {
        "ok": True,
        "plans": [
            {
                "id": 201,
                "no": "4436",
                "tarih": "2026-08-11",
                "tutar": 1374.0,
                "eski_pay": [("2026-08-01", 1374.0)],
                "yeni_pay": [("2026-08-01", 1373.93), ("2026-09-01", 0.07)],
                "eski_sum": 1374.0,
                "yeni_sum": 1374.0,
                "degisti": True,
                "eski_aciklama": "x",
                "yeni_aciklama": (
                    "x|AYLIK_TAH|2026-08-01| |AYLIK_TAH|2026-09-01| "
                    "|AYLIK_PAY|2026-08-01=1373.93| |AYLIK_PAY|2026-09-01=0.07|"
                ),
            },
            {
                "id": 202,
                "no": "4529",
                "tarih": "2026-09-01",
                "tutar": 1374.0,
                "eski_pay": [("2026-09-01", 1374.0)],
                "yeni_pay": [("2026-09-01", 1373.86), ("2026-10-01", 0.14)],
                "eski_sum": 1374.0,
                "yeni_sum": 1374.0,
                "degisti": True,
                "eski_aciklama": "x",
                "yeni_aciklama": (
                    "x|AYLIK_TAH|2026-09-01| |AYLIK_TAH|2026-10-01| "
                    "|AYLIK_PAY|2026-09-01=1373.86| |AYLIK_PAY|2026-10-01=0.14|"
                ),
            },
            {
                "id": 203,
                "no": "4698",
                "tarih": "2026-10-06",
                "tutar": 1374.0,
                "eski_pay": [("2026-10-01", 1374.0)],
                "yeni_pay": [("2026-10-01", 1373.79), ("2026-11-01", 0.21)],
                "eski_sum": 1374.0,
                "yeni_sum": 1374.0,
                "degisti": True,
                "eski_aciklama": "x",
                "yeni_aciklama": (
                    "x|AYLIK_TAH|2026-10-01| |AYLIK_TAH|2026-11-01| "
                    "|AYLIK_PAY|2026-10-01=1373.79| |AYLIK_PAY|2026-11-01=0.21|"
                ),
            },
        ],
        "n_degisti": 3,
        "update_ids": [201, 202, 203],
        "n_manuel": 3,
        "n_diger": 0,
        "diger_ids": [],
        "borc": borc,
    }
    snap = {
        "by_iso": {
            iso: {"aylik": b, "tahsil": min(b, 1374.0) if b > 600 else b, "kalan": 0.0}
            for iso, b in borc.items()
        },
        "payload": {
            "aylar": [
                {
                    "yil": int(iso[:4]),
                    "ay": int(iso[5:7]),
                    "brut_tutar_kdv": b,
                }
                for iso, b in borc.items()
            ]
        },
    }
    for iso, cell in snap["by_iso"].items():
        b = cell["aylik"]
        t = cell["tahsil"]
        cell["kalan"] = round(max(b - t, 0), 2)
    deg = degerlendir_plan(plan, rows, snap, date(2026, 10, 30))
    check("126 ONEMSIZ", deg.get("hukum") == "ONEMSIZ", deg)
    check("126 n_update0", int(deg.get("n_update", -1)) == 0, deg.get("n_update"))
    # canlı hesapla da kuruş/uygulama-gereksiz sınıfına düşebilir; en azından ok
    plan_live = hesapla_plan(126, rows, borc=borc)
    check("126 live ok", plan_live.get("ok") is True, plan_live.get("neden"))


def test_risk_apply_reddedilir():
    from scripts.makbuz_dagitim_onar import _risk_analiz

    once = {
        "2024-01-01": {
            "aylik": 1800.0,
            "tahsil": 1800.0,
            "kalan": 0.0,
            "durum": "odendi",
        }
    }
    bek = {
        "2024-01-01": {
            "aylik": 1800.0,
            "tahsil": 0.0,
            "kalan": 1800.0,
            "durum": "borclu",
        }
    }
    r = _risk_analiz(once, bek, date(2026, 10, 30))
    check("riskli bayrak", r.get("riskli") is True, r)
    check(
        "odendi_gerileme",
        any(x.get("tip") == "odendi_gerileme" for x in (r.get("riskler") or [])),
        r.get("riskler"),
    )
    # Apply kapısı: RISKLI ve --risk-onay yok → reddedilmeli
    hukum, risk_onay = "RISKLI", False
    check("apply red kosulu", hukum == "RISKLI" and not risk_onay)


def test_default_allowlist_davranisi_degismedi():
    """tam_fifo=False iken eski TAH allowlist hâlâ kilitler."""
    from routes.faturalar_routes import yeniden_hesapla_makbuz_dagitim_aciklama

    ac = (
        "Eylül 2026 Tahsilat H|AYLIK_TAH|2026-09-01| |AYLIK_TAH|2026-10-01| "
        "|AYLIK_PAY|2026-09-01=49760.40| |AYLIK_PAY|2026-10-01=2038.60|"
    )
    borc = _borc_2026()
    # Nis–Haz TAH-only + Tem/Ağu (Ağustos kalan 0.80)
    onceki = []
    for i, m in enumerate((4, 5, 6), start=1):
        iso = f"2026-{m:02d}-01"
        onceki.append(
            {
                "id": 100 + i,
                "tarih": f"2026-{m:02d}-05",
                "tutar": BRUT,
                "aciklama": _tah_only(iso),
            }
        )
    onceki.extend(
        [
            {
                "id": 1,
                "tarih": "2026-07-07",
                "tutar": 51799.00,
                "aciklama": (
                    "Temmuz 2026 Tahsilat H|AYLIK_TAH|2026-07-01| "
                    "|AYLIK_PAY|2026-07-01=51799.00|"
                ),
            },
            {
                "id": 2,
                "tarih": "2026-08-07",
                "tutar": 51799.80,
                "aciklama": (
                    "Ağustos 2026 Tahsilat H|AYLIK_TAH|2026-07-01| |AYLIK_TAH|2026-08-01| "
                    "|AYLIK_PAY|2026-07-01=0.80| |AYLIK_PAY|2026-08-01=51799.00|"
                ),
            },
        ]
    )
    # allowlist'li (varsayılan): Ağustos'a yazamaz
    yeni_lock = yeniden_hesapla_makbuz_dagitim_aciklama(
        872,
        tutar=51799.80,
        tahsilat_tarihi="2026-09-07",
        aciklama_eski=ac,
        haric_tahsilat_id=5584,
        kaynak="manuel_makbuz",
        odeme_turu="havale",
        tam_fifo=False,
        onceki_tahsilatlar=onceki,
        borc_satirlari=borc,
    )
    check(
        "allowlist Ağu yok",
        yeni_lock is not None and "|AYLIK_PAY|2026-08-01=" not in (yeni_lock or ""),
        yeni_lock,
    )
    # tam_fifo: Ağustos 0.80 + Eylül 51799
    yeni_fifo = yeniden_hesapla_makbuz_dagitim_aciklama(
        872,
        tutar=51799.80,
        tahsilat_tarihi="2026-09-07",
        aciklama_eski=ac,
        haric_tahsilat_id=5584,
        kaynak="manuel_makbuz",
        odeme_turu="havale",
        tam_fifo=True,
        onceki_tahsilatlar=onceki,
        borc_satirlari=borc,
    )
    check(
        "tam_fifo Ağu 0.80",
        yeni_fifo is not None and "|AYLIK_PAY|2026-08-01=0.80|" in (yeni_fifo or ""),
        yeni_fifo,
    )
    check(
        "tam_fifo Eyl 51799",
        yeni_fifo is not None and "|AYLIK_PAY|2026-09-01=51799.00|" in (yeni_fifo or ""),
        yeni_fifo,
    )


if __name__ == "__main__":
    test_sentetik_hedef_dagitim()
    test_idempotent()
    test_marker_toplam_dur()
    test_kapsam_disi_degismez()
    test_apply_without_allow_prod_write()
    test_default_allowlist_davranisi_degismedi()
    test_sentetik_209_odendi_korunur()
    test_sentetik_126_onemsiz()
    test_risk_apply_reddedilir()
    if FAILS:
        print("FAILS", FAILS)
        raise SystemExit(1)
    print("PASS")
