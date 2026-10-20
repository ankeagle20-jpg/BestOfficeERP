# -*- coding: utf-8 -*-
"""Banka import satırları (A1): tablo + kayıt + backfill. Sahte DB, canlı yazma yok."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)
os.environ.pop("BANKA_IMPORT_DB_SATIR", None)

import banka_import_backfill as bf  # noqa: E402
import banka_import_satirlari as bis  # noqa: E402
from services.banka_ak_import import normalize_banka_referans_no  # noqa: E402

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


class FakeStore:
    def __init__(self):
        self.satirlar = []  # tuple listeleri
        self.isaretler = []

    def calistir(self, sql, params=None):
        s = " ".join(str(sql).split())
        if s.startswith(("CREATE ", "ALTER ")):
            return None
        if s.startswith("DELETE FROM banka_import_satirlari"):
            did = params[0]
            self.satirlar = [r for r in self.satirlar if r[0] != did]
            return None
        if s.startswith("INSERT INTO banka_import_satirlari"):
            params = tuple(params or ())
            ncol = 16
            if len(params) % ncol != 0:
                raise AssertionError("params 16'nin kati olmali")
            mevcut = {(r[0], r[9]) for r in self.satirlar}
            for i in range(0, len(params), ncol):
                v = params[i:i + ncol]
                if (v[0], v[9]) in mevcut:
                    raise RuntimeError("unique ihlali (dosya, anahtar)")
                self.satirlar.append(v)
            return None
        if s.startswith("UPDATE akbank_import_dosyalar"):
            self.isaretler.append(params)
            return None
        raise AssertionError("beklenmeyen SQL: " + s[:90])

    def oku(self, sql, params=None):
        s = " ".join(str(sql).split())
        if "FROM customers" in s:
            return [{"n": 3, "mx": "2026-10-01"}]
        if s.startswith("SELECT 1 FROM banka_import_satirlari"):
            return [{}]
        if "FROM akbank_import_dosyalar" in s:
            return [
                {"id": 1, "ad_gosterim": "d1", "orijinal_filename": "a.xlsx", "parse_durumu": None},
                {"id": 2, "ad_gosterim": "d2", "orijinal_filename": "b.xlsx", "parse_durumu": None},
            ]
        if "FROM banka_import_satirlari" in s and "COUNT" in s:
            did = params[0]
            rows = [r for r in self.satirlar if r[0] == did]
            return [{"n": len(rows), "ps": bis.PARSE_SURUMU if rows else None,
                     "v": rows[0][14] if rows else None}]
        return []


class FakeCur:
    def __init__(self, store):
        self.store = store

    def execute(self, sql, params=None):
        self.store.calistir(sql, params)
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return None


def _oniz(ref, sira, tutar=100.0, tarih="2026-10-20", aciklama="ÖRNEK"):
    return {
        "sira": sira, "tarih": tarih, "saat": "", "tutar": tutar,
        "aciklama": aciklama, "banka_referans_no": ref, "yon": "gelen",
        "ui_status": "unknown", "musteri_id_oneri": None,
        "musteri_label_oneri": None,
        "eslestirme": {"status": "unknown", "kaynak": "otomatik"},
    }


def test_kayit_ve_6278():
    st = FakeStore()
    cur = FakeCur(st)
    rows = [
        _oniz("", 1, tutar=500.0),   # ref'siz, aynı tarih/tutar
        _oniz("", 2, tutar=500.0),   # ikinci ayrı havale — ikisi de saklanmalı
        _oniz("329961", 3),
    ]
    n = bis.satirlari_kaydet(cur, dosya_id=1, banka="AKBANK", onizlemeler=rows, versiyon="v1")
    check("kayit 3 satir", n == 3 and len(st.satirlar) == 3, st.satirlar)
    check("refsiz cift satir ikisi de var", len([r for r in st.satirlar if r[5] in (1, 2)]) == 2)
    check("anahtar sira ile ayrisir", bis.satir_anahtari("", "2026-10-20", 500.0, 1) != bis.satir_anahtari("", "2026-10-20", 500.0, 2))
    # idempotent: tekrar yaz, adet aynı
    n2 = bis.satirlari_kaydet(cur, dosya_id=1, banka="AKBANK", onizlemeler=rows, versiyon="v1")
    check("tekrar yaz idempotent", n2 == 3 and len(st.satirlar) == 3, len(st.satirlar))


def test_pglast_bayrak_backup():
    v = bis.validate_ddl_pglast()
    check("pglast tablo", v.get("table") == "banka_import_satirlari" and not v.get("missing"), v)
    check("pglast CASCADE", "ON DELETE CASCADE" in bis.SQL_TABLO)
    check("bayrak varsayilan kapali", bis.db_satir_aktif() is False)
    os.environ["BANKA_IMPORT_DB_SATIR"] = "1"
    try:
        check("bayrak acik", bis.db_satir_aktif() is True)
    finally:
        os.environ.pop("BANKA_IMPORT_DB_SATIR", None)
    from tenant_provisioning import BACKUP_TABLES, PLATFORM_STRIP_TABLES
    check("BACKUP_TABLES'ta", "banka_import_satirlari" in BACKUP_TABLES)
    check("PLATFORM_STRIP'te degil", "banka_import_satirlari" not in PLATFORM_STRIP_TABLES)


def test_punto_normalize():
    check(".0 kanonik", normalize_banka_referans_no("329961.0") == "329961")
    check("bilimsel kanonik", normalize_banka_referans_no("3.29961E+5") == "329961")


FAKE_MUSTERILER = [{
    "id": 1, "name": "ÖRNEK A", "musteri_adi": "", "tax_number": "",
    "sirket_unvani": "", "kyc_vergi_no": "", "yetkili_tcno": "", "yetkili_adsoyad": "",
}]


def _parse_fn(dosya_row, oku):
    if dosya_row["id"] == 2:
        raise ValueError("sahte bozuk dosya")
    ham = [{
        "sira": 1, "tarih": "2026-10-20", "saat": "", "tutar": 100.0,
        "aciklama": "BILINMEYEN GONDERICI", "banka_referans_no": "111",
        "tip": "gelen", "yon": "gelen",
    }]
    return ham, "AKBANK", FAKE_MUSTERILER


def test_backfill_idempotent_ve_hata_izolasyonu():
    st = FakeStore()
    r1 = bf.backfill(dry_run=False, oku=st.oku, calistir=st.calistir, parse_dosya_fn=_parse_fn)
    check("backfill ok", r1.get("ok"), r1)
    check("backfill 1 yazildi 1 hata izole", r1.get("yazilan_dosya") == 1 and len(r1.get("hatalar") or []) == 1, r1)
    check("hata dosya isaretli", any(p[0] == "hata" and p[2] == 2 for p in st.isaretler), st.isaretler)
    satir_once = len(st.satirlar)
    r2 = bf.backfill(dry_run=False, oku=st.oku, calistir=st.calistir, parse_dosya_fn=_parse_fn)
    check("backfill ikinci kosu idempotent (guncel atlanir)",
          r2.get("yazilan_dosya") == 0 and len(st.satirlar) == satir_once, (r2, len(st.satirlar)))
    r3 = bf.backfill(dry_run=True, oku=st.oku, calistir=st.calistir, parse_dosya_fn=_parse_fn)
    check("backfill dry yazmaz", r3.get("dry_run") and len(st.satirlar) == satir_once)
    r4 = bf.backfill(dry_run=False, temizle=True, oku=st.oku, calistir=st.calistir, parse_dosya_fn=_parse_fn)
    check("temizle satirlari siler", r4.get("ok") and len(st.satirlar) == 0, r4)


def test_bayrak_kapali_sifir_etki():
    src = (ROOT / "routes/banka_routes.py").read_text(encoding="utf-8")
    check("route bayrak kontrollu", "db_satir_aktif()" in src)
    check("kaydet bayrak disinda cagrilmiyor",
          src.count("_import_satir_kaydet(") <= 4, src.count("_import_satir_kaydet("))


def main():
    test_kayit_ve_6278()
    test_pglast_bayrak_backup()
    test_punto_normalize()
    test_backfill_idempotent_ve_hata_izolasyonu()
    test_bayrak_kapali_sifir_etki()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
