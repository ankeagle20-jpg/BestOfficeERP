# -*- coding: utf-8 -*-
"""banka_marker_onarim — yerel/fake. Canlı yazma yok."""
from __future__ import annotations

import copy
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)

import banka_marker_onarim as bmo  # noqa: E402
from tenant_provisioning import BACKUP_TABLES  # noqa: E402

FAILS: list[str] = []
MID = 87
IDS = list(range(5983, 5990))  # 5983–5989

PAYMENTS_87 = [
    (5983, "2025-11-19", 960.00),
    (5984, "2025-11-20", 960.00),
    (5985, "2026-01-16", 960.00),
    (5986, "2026-01-26", 960.00),
    (5987, "2026-02-06", 2880.00),
    (5988, "2026-06-08", 1920.00),
    (5989, "2026-08-25", 2534.98),
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


def _expected_87() -> dict[str, float]:
    exp = {f"2025-{m:02d}-01": 960.0 for m in range(7, 13)}
    exp.update({f"2026-{m:02d}-01": 960.0 for m in range(1, 6)})
    exp["2026-06-01"] = 614.98
    return exp


def _bozuk_rows() -> list[dict]:
    """Yığılmış marker kalıbı: hepsi Tem25'e PAY."""
    rows = []
    for tid, tah, tut in PAYMENTS_87:
        ac = (
            f"Banka tahsilat |AYLIK_TAH|2025-07-01| "
            f"|AYLIK_PAY|2025-07-01={float(tut):.2f}|"
        )
        rows.append({
            "id": tid,
            "mid": MID,
            "tutar": tut,
            "tarih": tah,
            "aciklama": ac,
            "makbuz_no": f"M{tid}",
            "banka_referans_no": f"R{tid}",
            "kaynak": "banka_import",
            "odeme_turu": "havale",
        })
    return rows


class FakeDb:
    """In-memory tahsilat + yedek; txn snapshot ile rollback."""

    def __init__(self, rows: list[dict]):
        self.tahsilatlar = {int(r["id"]): copy.deepcopy(r) for r in rows}
        self.yedek: list[dict] = []
        self.yedek_tablo = False
        self.audit: list[dict] = []
        self.seq = 0
        self._snap = None
        self._fail_next = False

    def begin(self):
        self._snap = {
            "tahsilatlar": copy.deepcopy(self.tahsilatlar),
            "yedek": copy.deepcopy(self.yedek),
            "yedek_tablo": self.yedek_tablo,
            "seq": self.seq,
        }

    def rollback(self):
        if self._snap is None:
            return
        self.tahsilatlar = self._snap["tahsilatlar"]
        self.yedek = self._snap["yedek"]
        self.yedek_tablo = self._snap["yedek_tablo"]
        self.seq = self._snap["seq"]
        self._snap = None

    def commit(self):
        self._snap = None

    def oku(self, sql, params=None):
        s = " ".join(str(sql or "").split())
        if "FROM tahsilatlar" in s and "id IN" in s:
            ids = list(params or ())
            out = []
            for i in ids:
                r = self.tahsilatlar.get(int(i))
                if r:
                    out.append(copy.deepcopy(r))
            return out
        if "FROM tahsilatlar" in s and "musteri_id" in s:
            # onceki diger
            mid, _mid2, max_t = params
            out = []
            for r in self.tahsilatlar.values():
                if int(r["mid"]) != int(mid):
                    continue
                if str(r["tarih"])[:10] > str(max_t)[:10]:
                    continue
                out.append({
                    "id": r["id"], "tarih": r["tarih"],
                    "tutar": r["tutar"], "aciklama": r["aciklama"],
                })
            out.sort(key=lambda x: (x["tarih"], x["id"]))
            return out
        if f"FROM {bmo.YEDEK_TABLO}" in s or "FROM tahsilat_marker_yedek" in s:
            if not self.yedek_tablo:
                raise RuntimeError('relation "tahsilat_marker_yedek" does not exist')
            mid, etiket = params[0], params[1]
            return [
                copy.deepcopy(y) for y in self.yedek
                if y["mid"] == mid and y["etiket"] == etiket and not y.get("geri_yazildi")
            ]
        return []

    def calistir(self, sql, params=None):
        s = " ".join(str(sql or "").split())
        if s.startswith("CREATE TABLE"):
            if "tahsilat_marker_yedek" in s:
                self.yedek_tablo = True
            return
        if "pg_advisory_xact_lock" in s:
            return
        if s.startswith("SAVEPOINT") or s.startswith("RELEASE") or s.startswith("ROLLBACK TO"):
            return
        if "INSERT INTO audit_log" in s or s.startswith("CREATE TABLE IF NOT EXISTS audit_log"):
            self.audit.append({"sql": s, "params": params})
            return
        if s.startswith("INSERT INTO tahsilat_marker_yedek") or f"INSERT INTO {bmo.YEDEK_TABLO}" in s:
            if not self.yedek_tablo:
                raise RuntimeError('relation "tahsilat_marker_yedek" does not exist')
            self.seq += 1
            row = {
                "id": self.seq,
                "tahsilat_id": params[0],
                "mid": params[1],
                "eski_aciklama": params[2],
                "eski_marker_json": params[3],
                "yeni_aciklama": params[4],
                "etiket": params[5],
                "islem_tamam": False,
                "geri_yazildi": False,
            }
            self.yedek.append(row)
            return
        if "UPDATE tahsilat_marker_yedek SET islem_tamam" in s or f"UPDATE {bmo.YEDEK_TABLO} SET islem_tamam" in s:
            for y in self.yedek:
                if "tahsilat_id" in s and y["tahsilat_id"] == params[0] and y["etiket"] == params[1]:
                    y["islem_tamam"] = True
                elif "WHERE id" in s and y["id"] == params[0]:
                    y["islem_tamam"] = True
            return
        if "UPDATE tahsilat_marker_yedek SET geri_yazildi" in s or f"UPDATE {bmo.YEDEK_TABLO} SET geri_yazildi" in s:
            for y in self.yedek:
                if y["id"] == params[0]:
                    y["geri_yazildi"] = True
            return
        if s.startswith("UPDATE tahsilatlar SET aciklama"):
            ac, tid, mid = params[0], int(params[1]), int(params[2])
            r = self.tahsilatlar.get(tid)
            if not r or int(r["mid"]) != mid or r.get("kaynak") != "banka_import":
                raise RuntimeError("update_miss")
            r["aciklama"] = ac
            return
        raise RuntimeError("bilinmeyen_sql:" + s[:80])


def test_pglast_ddl():
    info = bmo.validate_ddl_pglast()
    check("pglast tablo adi", info["table"] == "tahsilat_marker_yedek", info)
    check("pglast kolonlar", not info["missing"], info.get("missing"))


def test_backup_tables():
    check("BACKUP_TABLES tahsilat_marker_yedek", "tahsilat_marker_yedek" in BACKUP_TABLES)


def test_parse_ids():
    check("aralik", bmo._parse_ids("5983-5989") == IDS, bmo._parse_ids("5983-5989"))
    check("liste", bmo._parse_ids("5983,5984,5985") == [5983, 5984, 5985])


def test_mid87_fifo_plan():
    rows = _bozuk_rows()
    plan = bmo.hesapla_plan(MID, rows, borc=_borc_87(), onceki_diger=[])
    check("plan ok", plan.get("ok") is True, plan.get("invariant_hatalar"))
    check("ay toplam", plan.get("ay_toplam") == _expected_87(), plan.get("ay_toplam"))
    check("odeme=dagitim", plan["odeme_toplam"] == plan["dagitim_toplam"], plan)
    # tutar/tarih/ref değişmez
    for p, (_, tah, tut) in zip(plan["plans"], PAYMENTS_87):
        check(f"tutar {p['id']}", p["tutar"] == tut)
        check(f"tarih {p['id']}", p["tarih"] == tah)
        check(f"ref {p['id']}", p["banka_referans_no"] == f"R{p['id']}")
        check(f"makbuz {p['id']}", p["makbuz_no"] == f"M{p['id']}")
        check(f"kaynak {p['id']}", p["kaynak"] == "banka_import")


def test_yalniz_banka_import_ve_ids():
    rows = _bozuk_rows()
    rows[0]["kaynak"] = "manuel"
    plan = bmo.hesapla_plan(MID, rows, borc=_borc_87(), onceki_diger=[])
    check("manuel kaynak dur", plan.get("ok") is False and plan.get("neden") == "kaynak_degil_banka_import")

    rows = _bozuk_rows()
    rows[0]["mid"] = 99
    plan = bmo.hesapla_plan(MID, rows, borc=_borc_87(), onceki_diger=[])
    check("yanlis mid dur", plan.get("ok") is False)


def test_load_rows_scope():
    db = FakeDb(_bozuk_rows())
    # ekstra mid satırı ids dışı — load yalnız ids çeker; ek satır görmez
    db.tahsilatlar[9000] = {
        "id": 9000, "mid": 99, "tutar": 1, "tarih": "2026-01-01",
        "aciklama": "x", "makbuz_no": "Z", "banka_referans_no": "Z",
        "kaynak": "banka_import", "odeme_turu": "havale",
    }
    rows = bmo._load_rows(MID, IDS, db.oku)
    check("load 7", len(rows) == 7)
    try:
        bmo._load_rows(MID, IDS + [9000], db.oku)
        check("yanlis mid id ile dur", False)
    except RuntimeError as ex:
        check("yanlis mid id ile dur", "coklu_mid" in str(ex) or "mid_uyusmuyor" in str(ex), str(ex))
    try:
        bmo._load_rows(MID, [5983, 5999], db.oku)
        check("eksik id dur", False)
    except RuntimeError as ex:
        check("eksik id dur", "id_listesi_uyusmuyor" in str(ex), str(ex))


def test_dry_run_yazmaz():
    os.environ.pop("ALLOW_PROD_WRITE", None)
    db = FakeDb(_bozuk_rows())
    before = copy.deepcopy(db.tahsilatlar)
    r = bmo.onar(
        MID, IDS, dry_run=True, etiket="t1",
        oku=db.oku, calistir=db.calistir,
        borc=_borc_87(), onceki_diger=[],
    )
    check("dry ok", r.get("ok") is True, r)
    check("dry yazilmadi", r.get("yazildi") is False)
    check("aciklama ayni", db.tahsilatlar == before)


def test_apply_idempotent_geri_rollback():
    os.environ["ALLOW_PROD_WRITE"] = "1"
    try:
        db = FakeDb(_bozuk_rows())
        # ensure yokken apply → ensure oluşturur
        check("yedek yok baslangic", db.yedek_tablo is False)
        r1 = bmo.onar(
            MID, IDS, dry_run=False, etiket="et1",
            oku=db.oku, calistir=db.calistir,
            borc=_borc_87(), onceki_diger=[],
        )
        check("apply ok", r1.get("ok") and r1.get("yazildi"), r1)
        check("ensure olustu", db.yedek_tablo is True)
        # İlk 960 zaten Tem25=960 → degisti=False (yedek yok); diğer 6 yenilenir
        n_deg = int((r1.get("plan") or {}).get("n_degisti") or 0)
        check("n_degisti 6", n_deg == 6, n_deg)
        check("yedek=n_degisti", len(db.yedek) == n_deg, len(db.yedek))

        # ay toplamları
        tot: dict[str, float] = defaultdict(float)
        for r in db.tahsilatlar.values():
            if r["id"] not in set(IDS):
                continue
            for iso, v in bmo._pay_map(r["aciklama"]).items():
                tot[iso] = round(tot[iso] + v, 2)
        check("apply ay", dict(sorted(tot.items())) == _expected_87(), dict(tot))

        # sabit alanlar
        for tid, tah, tut in PAYMENTS_87:
            r = db.tahsilatlar[tid]
            check(f"tutar sabit {tid}", r["tutar"] == tut)
            check(f"tarih sabit {tid}", r["tarih"] == tah)
            check(f"ref sabit {tid}", r["banka_referans_no"] == f"R{tid}")

        # ikinci apply — değişiklik yok
        rows2 = [db.tahsilatlar[i] for i in IDS]
        r2 = bmo.onar(
            MID, IDS, dry_run=False, etiket="et2",
            oku=db.oku, calistir=db.calistir,
            borc=_borc_87(), onceki_diger=[],
        )
        check("idempotent", r2.get("degisiklik_yok") is True, r2)

        # geri alma birebir
        eski_map = {y["tahsilat_id"]: y["eski_aciklama"] for y in db.yedek if y["etiket"] == "et1"}
        g = bmo.geri_yukle(MID, "et1", oku=db.oku, calistir=db.calistir)
        check("geri ok", g.get("ok") is True, g)
        for tid, ac in eski_map.items():
            check(f"geri aciklama {tid}", db.tahsilatlar[tid]["aciklama"] == ac)

        # hata (force_fail) yazmadan önce → hiçbir değişiklik
        db2 = FakeDb(_bozuk_rows())
        before2 = copy.deepcopy(db2.tahsilatlar)
        r3 = bmo.onar(
            MID, IDS, dry_run=False, etiket="et_fail",
            oku=db2.oku, calistir=db2.calistir,
            borc=_borc_87(), onceki_diger=[],
            force_fail=True,
        )
        check("force_fail ok=False", r3.get("ok") is False, r3)
        check("rollback mesaj", "hiçbir değişiklik" in (r3.get("mesaj") or ""))
        check("rollback aciklama", db2.tahsilatlar == before2)
        check("rollback yedek bos", db2.yedek == [])
    finally:
        os.environ.pop("ALLOW_PROD_WRITE", None)


def test_cli_default_dry():
    os.environ.pop("ALLOW_PROD_WRITE", None)
    called = {}

    def fake_onar(mid, ids, *, dry_run, etiket, **kw):
        called["dry_run"] = dry_run
        called["mid"] = mid
        called["ids"] = ids
        return {"ok": True, "mid": mid, "ids": ids, "dry_run": dry_run, "yazildi": False, "plan": {}}

    orig = bmo.onar
    bmo.onar = fake_onar  # type: ignore
    try:
        rc = bmo.main(["--mids", "87", "--ids", "5983-5989"])
        check("cli rc", rc == 0)
        check("cli dry default", called.get("dry_run") is True, called)
    finally:
        bmo.onar = orig


if __name__ == "__main__":
    test_pglast_ddl()
    test_backup_tables()
    test_parse_ids()
    test_mid87_fifo_plan()
    test_yalniz_banka_import_ve_ids()
    test_load_rows_scope()
    test_dry_run_yazmaz()
    test_apply_idempotent_geri_rollback()
    test_cli_default_dry()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
