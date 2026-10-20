# -*- coding: utf-8 -*-
"""Dondurulmus ay (kira=0) dagitim disi — yerel/fake. Canli yazma yok."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

import tahsilat_kayan as tk  # noqa: E402
from ay_fifo import acik_ay_dagit  # noqa: E402
from routes.faturalar_routes import (  # noqa: E402
    _borc_dondurma_filtrele,
    _dondurulmus_ay_isos,
)

FAILS: list[str] = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


BORC_867 = {
    "2026-01-01": 1454.71, "2026-02-01": 1454.71, "2026-03-01": 1454.71,
    "2026-04-01": 1454.71, "2026-05-01": 1454.71, "2026-06-01": 1454.71,
    "2026-07-01": 1454.71, "2026-08-01": 949.68, "2026-09-01": 949.68,
}
GRID_867 = {k: v for k, v in BORC_867.items() if k not in ("2026-06-01", "2026-07-01")}
DONDUR_867 = {"2026-06-01", "2026-07-01"}


def _row(tid, tarih, tutar, kaynak, pays=None, tah=None):
    if pays:
        ac = "Makbuz " + " ".join(
            f"|AYLIK_TAH|{i}| |AYLIK_PAY|{i}={float(v):.2f}|" for i, v in pays
        )
    elif tah:
        ac = "Tahsilat " + " ".join(f"|AYLIK_TAH|{i}|" for i in tah)
    else:
        ac = "Tahsilat"
    return {
        "id": tid, "mid": 867, "tarih": tarih, "tutar": float(tutar),
        "aciklama": ac, "makbuz_no": f"M{tid}", "banka_referans_no": f"R{tid}",
        "kaynak": kaynak, "odeme_turu": "havale",
    }


def test_filtre_saf():
    f = _borc_dondurma_filtrele(dict(BORC_867), dict(GRID_867), set(DONDUR_867))
    check("filtre haz-tem dustu", "2026-06-01" not in f and "2026-07-01" not in f, f.keys())
    check("filtre digerleri kaldi", f.get("2026-08-01") == 949.68 and len(f) == 7)
    # grid brutu olan dondurma ayi korunur
    grid2 = dict(GRID_867)
    grid2["2026-06-01"] = 500.0
    f2 = _borc_dondurma_filtrele(dict(BORC_867), grid2, set(DONDUR_867))
    check("filtre grid-brutlu korur", f2.get("2026-06-01") == 1454.71)
    # dondurma yoksa ayni
    f3 = _borc_dondurma_filtrele(dict(BORC_867), dict(GRID_867), set())
    check("filtre bos kume ayni", f3 == BORC_867)


def test_dagitim_atlar():
    onceki = [
        {"id": 1, "tarih": "2026-01-01", "tutar": 720.0,
         "aciklama": "T |AYLIK_TAH|2026-01-01|"},
    ]
    # buyuk odeme: Oca-May kalanlar (734.71*4 + Oca 734.71) dolar, sonra Agustos
    f = _borc_dondurma_filtrele(dict(BORC_867), dict(GRID_867), set(DONDUR_867))
    onceki2 = [
        {"id": i + 1, "tarih": f"2026-0{i+1}-01", "tutar": 720.0,
         "aciklama": f"T |AYLIK_TAH|2026-0{i+1}-01|"}
        for i in range(5)
    ]
    del onceki
    pay = dict(acik_ay_dagit(f, 5000.0, "2026-10-21", onceki=onceki2))
    check("dagitim haz-tem yok", "2026-06-01" not in pay and "2026-07-01" not in pay, pay)
    check("dagitim agustosa gitti", pay.get("2026-08-01") == 949.68, pay)
    # iptal (filtresiz): eski davranis haz'a yazar (kalan 5000-3673.55)
    pay2 = dict(acik_ay_dagit(dict(BORC_867), 5000.0, "2026-10-21", onceki=onceki2))
    check("iptalde eski davranis", pay2.get("2026-06-01") == 1326.45, pay2)


def test_kayan_867():
    rows = [
        _row(5991 + i, f"2026-0{i+1}-01", 720.0, "grid_toplu", tah=[f"2026-0{i+1}-01"])
        for i in range(5)
    ]
    rows.append(_row(6037, "2026-10-20", 600.0, "manuel_makbuz",
                     pays=[("2026-01-01", 600.0)]))
    rows.append(_row(6038, "2026-10-21", 4500.0, "manuel_makbuz",
                     pays=[("2026-02-01", 4500.0)]))
    f = _borc_dondurma_filtrele(dict(BORC_867), dict(GRID_867), set(DONDUR_867))
    plan = tk.hesapla_plan_kayan(867, rows, borc=f)
    check("kayan plan ok", plan.get("ok") is True, plan.get("neden"))
    yeni = {}
    for p in plan.get("plans") or []:
        yeni[int(p["id"])] = p.get("yeni_pay_map") or {}
    check("kayan 6038 haz-tem yok",
          "2026-06-01" not in (yeni.get(6038) or {}) and "2026-07-01" not in (yeni.get(6038) or {}),
          yeni.get(6038))
    check("kayan 6038 agustos", (yeni.get(6038) or {}).get("2026-08-01") == 949.68, yeni.get(6038))
    # filtresiz: haz'a yazar (fark kaniti)
    plan2 = tk.hesapla_plan_kayan(867, rows, borc=dict(BORC_867))
    yeni2 = {int(p["id"]): (p.get("yeni_pay_map") or {}) for p in plan2.get("plans") or []}
    check("filtresiz haz (eski)", (yeni2.get(6038) or {}).get("2026-06-01") == 1426.45, yeni2.get(6038))


def test_kaynak():
    src = (ROOT / "routes/faturalar_routes.py").read_text(encoding="utf-8")
    check("kaynak filtre cagrisi", "_borc_dondurma_filtrele(borc, grid, _dondurulmus_ay_isos(musteri_id))" in src)
    check("kaynak aktif kosulu", "iptal_at IS NULL" in src)
    check("fonksiyon tanimli", callable(_dondurulmus_ay_isos) and callable(_borc_dondurma_filtrele))


if __name__ == "__main__":
    test_filtre_saf()
    test_dagitim_atlar()
    test_kayan_867()
    test_kaynak()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
