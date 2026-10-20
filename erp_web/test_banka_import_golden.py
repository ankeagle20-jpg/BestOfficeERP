# -*- coding: utf-8 -*-
"""A2 golden: eski analyze yolu vs DB okuma yolu çıktı eşitliği (sahte veri)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)
os.environ.pop("BANKA_IMPORT_DB_SATIR", None)

import banka_import_satirlari as bis  # noqa: E402
import test_banka_import_satirlari as t1  # noqa: E402
from routes.banka_routes import _ham_birlestir_dedupe  # noqa: E402
from services.banka_ak_import import onizleme_satirlari  # noqa: E402

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:260]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


MUSTERILER = [
    {"id": 5, "name": "ACME DANIŞMANLIK LİMİTED ŞİRKETİ", "musteri_adi": "",
     "tax_number": "", "sirket_unvani": "ACME DANIŞMANLIK LİMİTED ŞİRKETİ",
     "kyc_vergi_no": "", "yetkili_tcno": "", "yetkili_adsoyad": ""},
    {"id": 6, "name": "BETA LOJİSTİK ANONİM ŞİRKETİ", "musteri_adi": "",
     "tax_number": "", "sirket_unvani": "BETA LOJİSTİK ANONİM ŞİRKETİ",
     "kyc_vergi_no": "", "yetkili_tcno": "", "yetkili_adsoyad": ""},
]

HAM1 = [
    {"sira": 1, "excel_index": 0, "tarih": "2026-10-19", "saat": "09:00", "tutar": 1000.0,
     "aciklama": "ACME DANIŞMANLIK LİMİTED ŞİRKETİ kira havale", "banka_referans_no": "111",
     "tip": "gelen", "yon": "gelen"},
    {"sira": 2, "excel_index": 1, "tarih": "2026-10-20", "saat": "", "tutar": 500.0,
     "aciklama": "BILINMEYEN KISI odeme", "banka_referans_no": "222",
     "tip": "gelen", "yon": "gelen"},
    {"sira": 3, "excel_index": 2, "tarih": "2026-10-20", "saat": "", "tutar": 500.0,
     "aciklama": "BILINMEYEN KISI odeme", "banka_referans_no": "",
     "tip": "gelen", "yon": "gelen"},
]
HAM2 = [
    {"sira": 1, "excel_index": 0, "tarih": "2026-10-19", "saat": "09:00", "tutar": 1000.0,
     "aciklama": "ACME DANIŞMANLIK LİMİTED ŞİRKETİ kira havale", "banka_referans_no": "111",
     "tip": "gelen", "yon": "gelen"},
    {"sira": 2, "excel_index": 1, "tarih": "2026-10-21", "saat": "", "tutar": 750.0,
     "aciklama": "BETA LOJİSTİK ANONİM ŞİRKETİ odeme", "banka_referans_no": "333",
     "tip": "gelen", "yon": "gelen"},
]

BY_REF = {"111": {"tahsilat_id": 42, "makbuz_no": "M-42"}}
ANAHTARLAR = ("sira", "tarih", "saat", "tutar", "aciklama", "banka_referans_no",
              "tip", "yon", "ui_status", "musteri_id_oneri", "musteri_label_oneri",
              "sender_key", "tahsilatta", "tahsilat_id", "makbuz_no")


def _eski_yol():
    birlesik = _ham_birlestir_dedupe([ [dict(r) for r in HAM1], [dict(r) for r in HAM2] ])
    satirlar = onizleme_satirlari(birlesik, MUSTERILER, set(), {})
    for s in satirlar:
        info = BY_REF.get(str(s.get("banka_referans_no") or "").strip())
        s["tahsilatta"] = bool(info)
        s["tahsilat_id"] = info.get("tahsilat_id") if info else None
        s["makbuz_no"] = info.get("makbuz_no") if info else None
    return satirlar


def _yeni_yol():
    st = t1.FakeStore()
    cur = t1.FakeCur(st)
    bis.parse_ve_kaydet_dosya(cur, dosya_id=1, banka="AKBANK", ham=[dict(r) for r in HAM1],
                              musteriler=MUSTERILER, manual_by_key={}, versiyon="vX",
                              ozet={"islenen": 3})
    bis.parse_ve_kaydet_dosya(cur, dosya_id=2, banka="AKBANK", ham=[dict(r) for r in HAM2],
                              musteriler=MUSTERILER, manual_by_key={}, versiyon="vX",
                              ozet={"islenen": 2})

    def oku(sql, params=None):
        s = " ".join(str(sql).split())
        if "FROM banka_import_satirlari" in s and "SELECT *" in s:
            ids = set(params[0])
            out = []
            for i, v in enumerate(st.satirlar):
                if v[0] in ids:
                    d = {"id": i + 1}
                    d.update(zip(bis._SUTUNLAR, v))
                    out.append(d)
            return out
        return []

    db_rows = bis.db_satirlarini_oku(oku, [1, 2], yon="gelen")
    return bis.yanit_satirlari(db_rows, BY_REF)


def test_golden():
    eski = _eski_yol()
    yeni = _yeni_yol()
    check("golden satir sayisi ayni", len(eski) == len(yeni), (len(eski), len(yeni)))
    farklar = []
    for i, (e, y) in enumerate(zip(eski, yeni)):
        for k in ANAHTARLAR:
            if e.get(k) != y.get(k):
                farklar.append((i, k, e.get(k), y.get(k)))
        ee, yy = e.get("eslestirme") or {}, y.get("eslestirme") or {}
        for k in ("status", "musteri_id", "musteri_label", "kaynak"):
            if ee.get(k) != yy.get(k):
                farklar.append((i, "eslestirme." + k, ee.get(k), yy.get(k)))
    check("golden satirlar birebir ayni", not farklar, farklar[:6])
    check("golden aktarildi canli (111 tahsilatta)",
          any(s.get("tahsilatta") and s.get("tahsilat_id") == 42 for s in yeni), yeni)
    check("golden dosyalar arasi dedupe ilk kazanir",
          sum(1 for s in yeni if s.get("banka_referans_no") == "111") == 1, yeni)


def main():
    test_golden()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
