# -*- coding: utf-8 -*-
"""tahsilat_marker_hedef — birim test. Canlı yazma yok."""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

import tahsilat_marker_hedef as tmh  # noqa: E402

FAILS: list[str] = []
MID = 363
TID = 6008


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _row():
    return {
        "id": TID,
        "mid": MID,
        "tutar": 10000.0,
        "tarih": "2026-10-19",
        "aciklama": (
            "Makbuz |AYLIK_TAH|2024-02-01| |AYLIK_PAY|2024-02-01=1321.50|"
            " |AYLIK_TAH|2024-03-01| |AYLIK_PAY|2024-03-01=1321.50|"
        ),
        "makbuz_no": "M6008",
        "banka_referans_no": None,
        "kaynak": "manuel_makbuz",
        "odeme_turu": "nakit",
    }


class FakeDb:
    def __init__(self, row):
        self.tahsilatlar = {int(row["id"]): copy.deepcopy(row)}
        self.yedek: list[dict] = []
        self.yedek_tablo = False
        self.seq = 0

    def oku(self, sql, params=None):
        s = " ".join(str(sql).split())
        if "FROM tahsilatlar" in s and "WHERE id =" in s:
            tid = int(params[0])
            r = self.tahsilatlar.get(tid)
            return [copy.deepcopy(r)] if r else []
        if "FROM tahsilat_marker_yedek" in s:
            if not self.yedek_tablo:
                raise RuntimeError('relation "tahsilat_marker_yedek" does not exist')
            mid, etiket = params
            return [
                copy.deepcopy(y)
                for y in self.yedek
                if y["mid"] == mid and y["etiket"] == etiket and not y.get("geri_yazildi")
            ]
        return []

    def calistir(self, sql, params=None):
        s = " ".join(str(sql).split())
        if "CREATE TABLE" in s and "tahsilat_marker_yedek" in s:
            self.yedek_tablo = True
            return
        if "pg_advisory" in s:
            return
        if "INSERT INTO tahsilat_marker_yedek" in s:
            self.seq += 1
            self.yedek.append({
                "id": self.seq,
                "tahsilat_id": params[0],
                "mid": params[1],
                "eski_aciklama": params[2],
                "eski_marker_json": params[3],
                "yeni_aciklama": params[4],
                "etiket": params[5],
                "geri_yazildi": False,
            })
            return
        if s.startswith("UPDATE tahsilatlar SET aciklama"):
            ac, tid, mid = params[0], params[1], params[2]
            r = self.tahsilatlar.get(int(tid))
            if not r or int(r["mid"]) != int(mid):
                raise RuntimeError("miss")
            if len(params) >= 4 and str(r.get("kaynak") or "") != str(params[3]):
                raise RuntimeError("kaynak")
            r["aciklama"] = ac
            return
        if "SET islem_tamam" in s:
            return
        if "SET geri_yazildi" in s:
            for y in self.yedek:
                if y["id"] == params[0]:
                    y["geri_yazildi"] = True
            return


def test_parse_hedefler():
    items = tmh.parse_hedefler("2026-01:3163,2026-02:4398,2026-03:2439")
    check("n=3", len(items) == 3)
    check("toplam 10000", abs(sum(v for _, v in items) - 10000) < 0.02)
    check("iso", items[0][0] == "2026-01-01" and items[2][1] == 2439.0)


def test_manuel_makbuz_plan():
    plan = tmh.hesapla_plan(
        MID, _row(),
        tmh.parse_hedefler("2026-01:3163,2026-02:4398,2026-03:2439"),
    )
    check("plan ok", plan.get("ok"), plan)
    check("degisti", plan.get("degisti"))
    check("yeni 3 ay", len(plan["yeni_pay_map"]) == 3, plan["yeni_pay_map"])
    check("eski 2024 yok", "2024-02-01" not in plan["yeni_pay_map"])


def test_dagitim_invariant():
    plan = tmh.hesapla_plan(
        MID, _row(),
        tmh.parse_hedefler("2026-01:3000,2026-02:3000"),
    )
    check("toplam uyusmaz", plan.get("ok") is False)


def test_apply_geri_fake():
    import audit_log

    db = FakeDb(_row())
    eski = audit_log.audit_yaz
    audit_log.audit_yaz = lambda *a, **k: True
    try:
        r = tmh.onar(
            MID, TID,
            "2026-01:3163,2026-02:4398,2026-03:2439",
            dry_run=False,
            etiket="mh_test",
            kaynak="manuel_makbuz",
            oku=db.oku,
            calistir=db.calistir,
            sync_after=False,
        )
        check("apply", r.get("ok") and r.get("yazildi"), r)
        ac = db.tahsilatlar[TID]["aciklama"]
        check("yeni pay ocak", "2026-01-01=3163" in ac.replace("3163.00", "3163"), ac[:120])
        check("eski 2024 yok", "2024-02-01" not in ac)
        check("tutar ayni", db.tahsilatlar[TID]["tutar"] == 10000.0)
        check("kaynak ayni", db.tahsilatlar[TID]["kaynak"] == "manuel_makbuz")

        g = tmh.geri_yukle(MID, "mh_test", oku=db.oku, calistir=db.calistir)
        check("geri", g.get("ok"), g)
        check("geri 2024", "2024-02-01" in db.tahsilatlar[TID]["aciklama"])
    finally:
        audit_log.audit_yaz = eski


def test_kaynak_filtre():
    db = FakeDb(_row())
    try:
        tmh.onar(
            MID, TID, "2026-01:10000",
            dry_run=True, etiket="x",
            kaynak="banka_import",
            oku=db.oku,
        )
        check("yanlis kaynak raise", False)
    except RuntimeError as ex:
        check("yanlis kaynak", "kaynak_uyusmuyor" in str(ex), str(ex))


if __name__ == "__main__":
    test_parse_hedefler()
    test_manuel_makbuz_plan()
    test_dagitim_invariant()
    test_apply_geri_fake()
    test_kaynak_filtre()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
