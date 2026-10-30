# -*- coding: utf-8 -*-
"""makbuz_dagitim_onar — sentetik + güvenlik. Canlı yazma yok."""
from __future__ import annotations

import os
import sys
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
    if FAILS:
        print("FAILS", FAILS)
        raise SystemExit(1)
    print("PASS")
