# -*- coding: utf-8 -*-
"""tahsilat_tutar_duzelt — birim test. Canlı yazma yok."""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

import tahsilat_tutar_duzelt as ttd  # noqa: E402
from tenant_provisioning import BACKUP_TABLES  # noqa: E402

FAILS: list[str] = []
MID = 363


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _rows_base():
    out = []
    for i, (tid, ay, tut) in enumerate([
        (99, "2024-02-01", 701.70),
        (100, "2024-03-01", 701.70),
        (111, "2025-02-01", 975.71),
    ]):
        out.append({
            "id": tid,
            "mid": MID,
            "tutar": tut,
            "tarih": ay,
            "aciklama": f"X |AYLIK_TAH|{ay}|",
            "makbuz_no": f"M{tid}",
            "kaynak": "",
        })
    return out


def _brut():
    return {
        "2024-02-01": 2023.20,
        "2024-03-01": 2023.20,
        "2025-02-01": 3163.00,
    }


class FakeDb:
    def __init__(self, rows):
        self.tahsilatlar = {int(r["id"]): copy.deepcopy(r) for r in rows}
        self.yedek: list[dict] = []
        self.yedek_tablo = False
        self.audit: list[dict] = []
        self.seq = 0

    def oku(self, sql, params=None):
        s = " ".join(str(sql).split())
        if "FROM tahsilatlar" in s and "WHERE id IN" in s:
            ids = list(params or ())
            return [copy.deepcopy(self.tahsilatlar[i]) for i in ids if i in self.tahsilatlar]
        if f"FROM {ttd.YEDEK_TABLO}" in s or "FROM tahsilat_tutar_yedek" in s:
            if not self.yedek_tablo:
                raise RuntimeError('relation "tahsilat_tutar_yedek" does not exist')
            mid, etiket = params
            return [
                copy.deepcopy(y)
                for y in self.yedek
                if y["mid"] == mid and y["etiket"] == etiket and not y.get("geri_yazildi")
            ]
        return []

    def calistir(self, sql, params=None):
        s = " ".join(str(sql).split())
        if "CREATE TABLE" in s and "tahsilat_tutar_yedek" in s:
            self.yedek_tablo = True
            return
        if "pg_advisory_xact_lock" in s:
            return
        if s.startswith("INSERT INTO tahsilat_tutar_yedek") or f"INSERT INTO {ttd.YEDEK_TABLO}" in s:
            if not self.yedek_tablo:
                raise RuntimeError('relation "tahsilat_tutar_yedek" does not exist')
            self.seq += 1
            self.yedek.append({
                "id": self.seq,
                "etiket": params[0],
                "tahsilat_id": params[1],
                "mid": params[2],
                "eski_tutar": params[3],
                "yeni_tutar": params[4],
                "ay_iso": params[5],
                "geri_yazildi": False,
            })
            return
        if s.startswith("UPDATE tahsilatlar SET tutar"):
            tutar, tid, mid = params
            r = self.tahsilatlar.get(int(tid))
            if not r or int(r["mid"]) != int(mid):
                raise RuntimeError("update miss")
            r["tutar"] = round(float(tutar), 2)
            return
        if "UPDATE tahsilat_tutar_yedek SET geri_yazildi" in s or f"UPDATE {ttd.YEDEK_TABLO} SET geri_yazildi" in s:
            yid = params[0]
            for y in self.yedek:
                if y["id"] == yid:
                    y["geri_yazildi"] = True
            return
        if "INSERT INTO audit" in s.lower() or "audit" in s.lower():
            self.audit.append({"sql": s, "params": params})
            return


def test_pglast_ve_backup():
    info = ttd.validate_ddl_pglast()
    check("pglast tablo", info["table"] == "tahsilat_tutar_yedek", info)
    check("pglast kolon", not info["missing"], info.get("missing"))
    check("BACKUP_TABLES", "tahsilat_tutar_yedek" in BACKUP_TABLES)


def test_azaltma_yok():
    rows = [{
        "id": 1, "mid": MID, "tutar": 3000.0, "tarih": "2024-02-01",
        "aciklama": "|AYLIK_TAH|2024-02-01|", "makbuz_no": "M", "kaynak": "",
    }]
    plan = ttd.hesapla_plan(MID, rows, grid_brut={"2024-02-01": 2023.20})
    p = plan["plans"][0]
    check("azaltma atla", p["durum"] == "atla" and p["uyari"] == "azaltma_yok")
    check("azaltma degisti=False", p["degisti"] is False)
    check("azaltma tutar ayni", p["yeni_tutar"] == 3000.0)


def test_ay_gridde_yok():
    rows = [{
        "id": 1, "mid": MID, "tutar": 100.0, "tarih": "2020-01-01",
        "aciklama": "|AYLIK_TAH|2020-01-01|", "makbuz_no": "M", "kaynak": "",
    }]
    plan = ttd.hesapla_plan(MID, rows, grid_brut=_brut())
    p = plan["plans"][0]
    check("grid yok atla", p["uyari"] == "ay_gridde_yok")


def test_tek_tah_zorunlu():
    rows = [{
        "id": 1, "mid": MID, "tutar": 100.0, "tarih": "2024-02-01",
        "aciklama": "|AYLIK_TAH|2024-02-01| |AYLIK_TAH|2024-03-01|",
        "makbuz_no": "M", "kaynak": "",
    }]
    plan = ttd.hesapla_plan(MID, rows, grid_brut=_brut())
    check("coklu tah atla", plan["plans"][0]["uyari"] == "tek_ay_tah_yok")


def test_plan_artis_ve_fingerprint():
    plan = ttd.hesapla_plan(MID, _rows_base(), grid_brut=_brut())
    check("n_degisti 3", plan["n_degisti"] == 3, plan)
    # (2023.20-701.70)*2 + (3163-975.71) = 1321.5*2 + 2187.29 = 4830.29
    check("artis", abs(plan["toplam_artis"] - 4830.29) < 0.02, plan["toplam_artis"])
    fp1 = plan["fingerprint"]
    plan2 = ttd.hesapla_plan(MID, _rows_base(), grid_brut=_brut())
    check("fingerprint idempotent", fp1 == plan2["fingerprint"])


def test_yedek_geri_apply_fake():
    rows = _rows_base()
    db = FakeDb(rows)
    # audit_yaz mock
    import audit_log

    audits = []

    def fake_audit(cal, **kw):
        audits.append(kw)
        return True

    eski = audit_log.audit_yaz
    audit_log.audit_yaz = fake_audit
    try:
        r = ttd.duzelt(
            MID, [99, 100, 111],
            dry_run=False,
            etiket="t_test",
            oku=db.oku,
            calistir=db.calistir,
            grid_brut=_brut(),
        )
        check("apply ok", r.get("ok") and r.get("yazildi"), r)
        check("yedek 3", len(db.yedek) == 3, len(db.yedek))
        check("tutar 99", abs(db.tahsilatlar[99]["tutar"] - 2023.20) < 0.01)
        check("audit", len(audits) >= 3, len(audits))

        # idempotent: ikinci apply degisiklik yok
        r2 = ttd.duzelt(
            MID, [99, 100, 111],
            dry_run=False,
            etiket="t_test2",
            oku=db.oku,
            calistir=db.calistir,
            grid_brut=_brut(),
        )
        check("idempotent", r2.get("degisiklik_yok") is True, r2)

        g = ttd.geri_yukle(MID, "t_test", oku=db.oku, calistir=db.calistir)
        check("geri ok", g.get("ok"), g)
        check("geri tutar 99", abs(db.tahsilatlar[99]["tutar"] - 701.70) < 0.01)
    finally:
        audit_log.audit_yaz = eski


def test_plan_fingerprint_degisince_iptal():
    """Apply öncesi ikinci plan farklıysa yazılmaz — hesapla_plan stub."""
    rows = _rows_base()
    db = FakeDb(rows)
    calls = {"n": 0}
    real = ttd.hesapla_plan

    def flip(*a, **k):
        calls["n"] += 1
        p = real(*a, **k)
        if calls["n"] == 2:
            p = copy.deepcopy(p)
            p["fingerprint"] = "DIFFERENT"
        return p

    ttd.hesapla_plan = flip
    try:
        r = ttd.duzelt(
            MID, [99],
            dry_run=False,
            etiket="fp",
            oku=db.oku,
            calistir=db.calistir,
            grid_brut=_brut(),
        )
        check("fp uyusmaz ok=False", r.get("ok") is False and r.get("neden") == "plan_uyusmuyor", r)
        check("fp yazilmadi", r.get("yazildi") is False)
        check("fp tutar eski", abs(db.tahsilatlar[99]["tutar"] - 701.70) < 0.01)
    finally:
        ttd.hesapla_plan = real


if __name__ == "__main__":
    test_pglast_ve_backup()
    test_azaltma_yok()
    test_ay_gridde_yok()
    test_tek_tah_zorunlu()
    test_plan_artis_ve_fingerprint()
    test_yedek_geri_apply_fake()
    test_plan_fingerprint_degisince_iptal()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
