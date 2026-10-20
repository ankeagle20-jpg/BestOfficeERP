# -*- coding: utf-8 -*-
"""Tahsilat silme izi: ortak silme + yedek + geri alma. Sahte DB, canlı yazma yok."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

import tahsilat_silme as ts  # noqa: E402
import tahsilat_silinen_geri_al as ga  # noqa: E402

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


class FakeCur:
    def __init__(self, db):
        self.db = db
        self.rowcount = 0
        self._one = None
        self._all = []

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split())
        self.db.calls.append(s[:60])
        if s.startswith("CREATE TABLE"):
            return
        if s.startswith("SAVEPOINT") or s.startswith("RELEASE") or s.startswith("ROLLBACK"):
            return
        if s.startswith("SELECT * FROM tahsilatlar"):
            rows = list(self.db.tahsilatlar.values())
            if "id = ANY" in s:
                ids = set(params[0])
                rows = [r for r in rows if r["id"] in ids]
            elif "fatura_id = %s" in s:
                rows = [r for r in rows if r.get("fatura_id") == params[0]]
            elif "LIKE" in s:
                pat = str(params[-1]).replace("%", "")
                rows = [r for r in rows if pat in str(r.get("aciklama") or "")]
            self._all = [dict(r) for r in rows]
            return
        if s.startswith("INSERT INTO tahsilat_silinen_yedek"):
            if self.db.yedek_patla:
                raise RuntimeError("yedek yazilamadi")
            tid, mid, yol, satir_json, silen = params
            yid = len(self.db.yedek) + 1
            self.db.yedek.append({
                "id": yid, "tahsilat_id": tid, "musteri_id": mid,
                "silme_yolu": yol, "satir_json": satir_json, "geri_alindi": False,
            })
            self._one = {"id": yid}
            return
        if s.startswith("DELETE FROM tahsilatlar"):
            ids = set(params[0])
            n0 = len(self.db.tahsilatlar)
            self.db.tahsilatlar = {
                k: v for k, v in self.db.tahsilatlar.items() if k not in ids
            }
            self.rowcount = n0 - len(self.db.tahsilatlar)
            return
        if s.startswith("INSERT INTO audit_log"):
            self.db.audit.append(params)
            return
        raise AssertionError("beklenmeyen SQL: " + s[:100])

    def fetchall(self):
        return self._all

    def fetchone(self):
        return self._one


class FakeDB:
    def __init__(self):
        self.tahsilatlar = {
            11: {"id": 11, "musteri_id": 867, "customer_id": 867, "fatura_id": None,
                 "tutar": 469.84, "tahsilat_tarihi": "2026-10-20", "aciklama": "|AYLIK_TAH|2026-08-01|",
                 "makbuz_no": "M-11", "kaynak": "manuel_makbuz"},
            12: {"id": 12, "musteri_id": 867, "customer_id": 867, "fatura_id": 55,
                 "tutar": 100.0, "tahsilat_tarihi": "2026-10-01", "aciklama": "",
                 "makbuz_no": "M-12", "kaynak": "grid_toplu"},
            13: {"id": 13, "musteri_id": 153, "customer_id": 153, "fatura_id": 55,
                 "tutar": 200.0, "tahsilat_tarihi": "2026-10-02", "aciklama": "",
                 "makbuz_no": "M-13", "kaynak": "grid_toplu"},
        }
        self.yedek = []
        self.audit = []
        self.calls = []
        self.yedek_patla = False


def test_sil_ids():
    db = FakeDB()
    silinen = ts.tahsilatlari_sil_cursor(FakeCur(db), silme_yolu="tahsilat-sil", ids=[11])
    check("sil ids donen 1", len(silinen) == 1 and silinen[0]["id"] == 11, silinen)
    check("sil ids tahsilat gitti", 11 not in db.tahsilatlar)
    check("sil ids yedek 1", len(db.yedek) == 1 and db.yedek[0]["tahsilat_id"] == 11, db.yedek)
    govde = json.loads(db.yedek[0]["satir_json"])
    check("yedek satir tam (tutar/makbuz)", govde.get("tutar") == 469.84 and govde.get("makbuz_no") == "M-11", govde)
    check("yedek yolu", db.yedek[0]["silme_yolu"] == "tahsilat-sil")
    check("audit yazildi", len(db.audit) == 1 and db.audit[0][2] == "tahsilat_sil", db.audit)


def test_sil_where_cascade():
    db = FakeDB()
    silinen = ts.tahsilatlari_sil_cursor(
        FakeCur(db), silme_yolu="fatura-sil-cascade",
        where_sql="fatura_id = %s", where_params=(55,),
    )
    check("cascade 2 satir (musteri ayirmaz)", sorted(r["id"] for r in silinen) == [12, 13], silinen)
    check("cascade yedek 2", len(db.yedek) == 2)


def test_yedek_patlarsa_silme_yok():
    db = FakeDB()
    db.yedek_patla = True
    patladi = False
    try:
        ts.tahsilatlari_sil_cursor(FakeCur(db), silme_yolu="tahsilat-sil", ids=[11])
    except RuntimeError:
        patladi = True
    check("yedek hata istisna", patladi)
    check("yedek hata tahsilat duruyor", 11 in db.tahsilatlar)


def test_bos_secim():
    db = FakeDB()
    check("bos ids []", ts.tahsilatlari_sil_cursor(FakeCur(db), silme_yolu="x", ids=[]) == [])
    check("olmayan id []", ts.tahsilatlari_sil_cursor(FakeCur(db), silme_yolu="x", ids=[999]) == [])


def _ga_oku(yedekler, cakisan=()):
    def oku(sql, params=None):
        s = " ".join(str(sql).split())
        if "FROM tahsilat_silinen_yedek" in s:
            return [dict(r) for r in yedekler]
        if s.startswith("SELECT id FROM tahsilatlar"):
            return [{"id": i} for i in cakisan]
        if "information_schema" in s:
            return [{"column_name": c} for c in ("id", "musteri_id", "tutar", "tahsilat_tarihi", "aciklama")]
        return []
    return oku


YEDEK_SATIR = [{
    "id": 7, "tahsilat_id": 11, "musteri_id": 867, "silme_yolu": "tahsilat-sil",
    "satir_json": json.dumps({"id": 11, "musteri_id": 867, "tutar": 469.84,
                              "tahsilat_tarihi": "2026-10-20", "aciklama": "|AYLIK_TAH|2026-08-01|"}),
    "geri_alindi": False,
}]


def test_geri_dry_run():
    kayitlar = []
    r = ga.geri_al(yedek_id=7, dry_run=True, oku=_ga_oku(YEDEK_SATIR),
                   calistir=lambda sql, params=None: kayitlar.append(sql))
    check("geri dry ok", r.get("ok") and r.get("dry_run") and r.get("n") == 1, r)
    check("geri dry yazma yok", not kayitlar, kayitlar)


def test_geri_cakisma_durur():
    kayitlar = []
    r = ga.geri_al(yedek_id=7, dry_run=False, oku=_ga_oku(YEDEK_SATIR, cakisan=(11,)),
                   calistir=lambda sql, params=None: kayitlar.append((sql, params)),
                   tahsilat_sutunlar=["id", "musteri_id", "tutar", "tahsilat_tarihi", "aciklama"])
    check("geri cakisma ok=False", r.get("ok") is False and r.get("neden") == "id cakismasi", r)
    check("geri cakisma insert yok", not any("INSERT INTO tahsilatlar" in str(k[0]) for k in kayitlar), kayitlar)


def test_geri_apply_ayni_id():
    kayitlar = []

    def cal(sql, params=None):
        kayitlar.append((" ".join(str(sql).split()), params))
        return None

    r = ga.geri_al(yedek_id=7, dry_run=False, oku=_ga_oku(YEDEK_SATIR), calistir=cal,
                   tahsilat_sutunlar=["id", "musteri_id", "tutar", "tahsilat_tarihi", "aciklama"])
    check("geri apply ok", r.get("ok") and r.get("n") == 1, r)
    ins = [k for k in kayitlar if k[0].startswith("INSERT INTO tahsilatlar")]
    check("geri ayni id insert", len(ins) == 1 and ins[0][1][0] == 11, ins)
    upd = [k for k in kayitlar if "SET geri_alindi = TRUE" in k[0]]
    check("geri yedek isaretlendi", len(upd) == 1, upd)


def test_geri_allow_kapisi():
    os.environ.pop("ALLOW_PROD_WRITE", None)
    r = ga.geri_al(yedek_id=7, dry_run=False, oku=_ga_oku(YEDEK_SATIR))
    check("geri ALLOW yoksa yazmaz", r.get("ok") is False and "ALLOW" in str(r.get("neden")), r)


def test_kaynak_ve_ddl():
    v = ts.validate_ddl_pglast()
    check("pglast tablo", v.get("table") == "tahsilat_silinen_yedek" and not v.get("missing"), v)
    from tenant_provisioning import BACKUP_TABLES
    check("BACKUP_TABLES kayitli", "tahsilat_silinen_yedek" in BACKUP_TABLES)
    for dosya in ("routes/giris_routes.py", "routes/faturalar_routes.py",
                  "routes/tahsilat_routes.py", "routes/banka_routes.py"):
        src = (ROOT / dosya).read_text(encoding="utf-8")
        check(f"{dosya} dogrudan DELETE yok", "DELETE FROM tahsilatlar" not in src)
    src_g = (ROOT / "routes/giris_routes.py").read_text(encoding="utf-8")
    for yol in ("tahsilden-cikar", "ekstre-tahsilat-sil", "ekstre-kayit-donustur"):
        check(f"giris yolu ortak: {yol}", f'silme_yolu="{yol}"' in src_g)
    check("ensure cagrisi modülde", "ensure_tahsilat_silinen_yedek(_cal)" in
          (ROOT / "tahsilat_silme.py").read_text(encoding="utf-8"))


def main():
    test_sil_ids()
    test_sil_where_cascade()
    test_yedek_patlarsa_silme_yok()
    test_bos_secim()
    test_geri_dry_run()
    test_geri_cakisma_durur()
    test_geri_apply_ayni_id()
    test_geri_allow_kapisi()
    test_kaynak_ve_ddl()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
