# -*- coding: utf-8 -*-
"""Ref normalize + soft-match ay stop + geriye dönük uyarı. Canlı DB yazmaz."""
from __future__ import annotations

from services.banka_ak_import import (
    banka_referans_no_variants,
    eslestir_musteri,
    normalize_banka_referans_no,
)
from routes.banka_routes import _akbank_geriye_donuk_banka_ids

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_ref_normalize():
    check("329961.0 → 329961", normalize_banka_referans_no("329961.0") == "329961")
    check("329961 → 329961", normalize_banka_referans_no("329961") == "329961")
    check("boşluk", normalize_banka_referans_no(" 329 961.0 ") == "329961")
    check("bilimsel", normalize_banka_referans_no("3.29961E+5") == "329961")
    v1 = set(banka_referans_no_variants("329961"))
    v2 = set(banka_referans_no_variants("329961.0"))
    check("varyant kesişim", "329961" in v1 and "329961.0" in v1)
    check("329961 ve 329961.0 aynı küme", v1 == v2 or ("329961" in v2 and "329961.0" in v2))
    # mükerrer: iki biçim aynı sayılsın
    a = normalize_banka_referans_no("329961")
    b = normalize_banka_referans_no("329961.0")
    check("mükerrer aynı kanonik", a == b == "329961")


def test_softmatch_ay_stop():
    # mid=754 benzeri: kart adı yalnız ay
    musteriler = [
        {"id": 754, "name": "Eylül", "musteri_adi": "Eylül", "sirket_unvani": ""},
        {"id": 550, "name": "Mayıs", "musteri_adi": "Mayıs", "sirket_unvani": ""},
        {"id": 82, "name": "Mart", "musteri_adi": "Mart", "sirket_unvani": ""},
        {"id": 104, "name": "Nisan", "musteri_adi": "Nisan", "sirket_unvani": ""},
        {"id": 447, "name": "Haziran", "musteri_adi": "Haziran", "sirket_unvani": ""},
        {
            "id": 9001,
            "name": "Ahmet Yılmaz Ofis",
            "musteri_adi": "Ahmet Yılmaz",
            "sirket_unvani": "Yılmaz Danışmanlık Ltd",
        },
    ]
    r = eslestir_musteri("Eylül 2025 kira", musteriler)
    mid = r.get("musteri_id")
    cands = [c.get("id") for c in (r.get("candidates") or [])]
    check("Eylül 2025 kira → 754 önerme", mid != 754 and 754 not in cands, str(r))
    check("Eylül 2025 kira → ay mid yok", mid not in (754, 550, 82, 104, 447), str(r))

    r2 = eslestir_musteri("AHMET YILMAZ ofis kira eylül 2025", musteriler)
    check("gerçek isim eşleşir", r2.get("musteri_id") == 9001, str(r2))


class _FakeCur:
    def __init__(self, rows):
        self._rows = rows
        self.last_sql = ""
        self.last_params = None

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params

    def fetchall(self):
        return list(self._rows)


def test_geriye_donuk_tespit():
    # mevcut daha yeni banka_import: 5983.. (2025-11+) varken yeni 2025-08 ekleniyor
    cur = _FakeCur([{"id": 5983}, {"id": 5984}, {"id": 5990}])
    ids = _akbank_geriye_donuk_banka_ids(cur, 87, "2025-08-11")
    check("geriye_donuk ids", ids == [5983, 5984, 5990], str(ids))
    check("sql mid", cur.last_params[0] == 87)
    check("sql tarih", cur.last_params[1] == "2025-08-11")
    check("sql banka_import", "banka_import" in (cur.last_sql or ""))

    cur2 = _FakeCur([])
    ids2 = _akbank_geriye_donuk_banka_ids(cur2, 87, "2026-12-01")
    check("ileri tarih boş", ids2 == [])


if __name__ == "__main__":
    test_ref_normalize()
    test_softmatch_ay_stop()
    test_geriye_donuk_tespit()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    raise SystemExit(1 if FAILS else 0)
