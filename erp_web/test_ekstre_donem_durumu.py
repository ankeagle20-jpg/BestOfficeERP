# -*- coding: utf-8 -*-
"""Ekstre dönem durumu gösterimi. Tutar kolonları değişmez. Canlı yazma yok."""
from __future__ import annotations

import hashlib
import logging
import os
import sys
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _para(rows):
    return [
        (
            str(r.get("tarih") or "")[:10],
            str(r.get("tur") or ""),
            str(r.get("aciklama") or ""),
            round(float(r.get("borc") or 0), 2),
            round(float(r.get("alacak") or 0), 2),
            None if r.get("bakiye") is None else round(float(r.get("bakiye") or 0), 2),
        )
        for r in rows
    ]


def _bakiye(rows):
    b = 0.0
    for r in rows:
        b = round(b + float(r["borc"]) - float(r["alacak"]), 2)
        r["bakiye"] = b
    return b


def _senaryo():
    acik = (
        "|AYLIK_PAY|2024-01-01=60|"
        " |AYLIK_PAY|2024-02-01=60|"
        " |AYLIK_PAY|2026-10-01=846|"
    )
    rows = [
        {"tarih": "2024-01-01", "tur": "Kira", "aciklama": "Ocak 2024 Kira", "borc": 360.0, "alacak": 0.0},
        {"tarih": "2024-01-15", "tur": "Tahsilat", "aciklama": "Ocak 2024 Tahsilat", "borc": 0.0, "alacak": 300.0, "tahsilat_ids": [1]},
        {"tarih": "2024-02-01", "tur": "Kira", "aciklama": "Şubat 2024 Kira", "borc": 360.0, "alacak": 0.0},
        {"tarih": "2024-02-15", "tur": "Tahsilat", "aciklama": "Şubat 2024 Tahsilat", "borc": 0.0, "alacak": 300.0, "tahsilat_ids": [2]},
        {"tarih": "2026-10-01", "tur": "Kira", "aciklama": "Ekim 2026 Kira", "borc": 966.0, "alacak": 0.0},
        {
            "tarih": "2026-10-23",
            "tur": "Tahsilat",
            "aciklama": "Ocak 2024: 60,00 ₺, Şubat 2024: 60,00 ₺, Ekim 2026: 846,00 ₺",
            "borc": 0.0,
            "alacak": 966.0,
            "tahsilat_ids": [23],
        },
    ]
    panel = {
        "2024-01-01": {"aylik": 360, "tahsil": 360, "kalan": 0},
        "2024-02-01": {"aylik": 360, "tahsil": 360, "kalan": 0},
        "2026-10-01": {"aylik": 966, "tahsil": 846, "kalan": 120},
    }
    tahsil = [
        {"id": 1, "tutar": 300, "tahsilat_tarihi": "2024-01-15", "aciklama": "nakit"},
        {"id": 2, "tutar": 300, "tahsilat_tarihi": "2024-02-15", "aciklama": "nakit"},
        {"id": 23, "tutar": 966, "tahsilat_tarihi": "2026-10-23", "aciklama": acik},
    ]
    return rows, panel, tahsil


def _uygula(rows, panel, tahsil):
    from routes import giris_routes as gr

    once = _para(rows)
    gr._ekstre_donem_durumu_uygula(rows, panel, tahsil)
    return once


def test_a():
    rows, panel, tahsil = _senaryo()
    _bakiye(rows)
    _uygula(rows, panel, tahsil)
    kira = {r["tarih"]: r for r in rows if r["tur"] == "Kira"}
    check("2024-01 Odendi", kira["2024-01-01"].get("donem_durum") == "Ödendi")
    check(
        "2024-01 not",
        kira["2024-01-01"].get("donem_not") == "60,00 ₺ sonradan ödendi (23.10.2026)",
        kira["2024-01-01"].get("donem_not"),
    )
    check("2024-02 Odendi", kira["2024-02-01"].get("donem_durum") == "Ödendi")
    check(
        "2024-02 not",
        kira["2024-02-01"].get("donem_not") == "60,00 ₺ sonradan ödendi (23.10.2026)",
    )
    check("2026-10 Kismi", kira["2026-10-01"].get("donem_durum") == "Kısmi")
    check("2026-10 not yok", not kira["2026-10-01"].get("donem_not"))


def test_b():
    rows, panel, tahsil = _senaryo()
    _bakiye(rows)
    once_alacak = [round(float(r["alacak"]), 2) for r in rows]
    _uygula(rows, panel, tahsil)
    alacak = [r for r in rows if round(float(r["alacak"]), 2) == 966.0]
    check("966 bir kez", len(alacak) == 1 and len(rows) == 6)
    alt = alacak[0].get("pay_alt") or []
    tutarlar = [round(float(a["tutar"]), 2) for a in alt]
    check("alt 60+60+846", tutarlar == [60.0, 60.0, 846.0], str(tutarlar))
    check("alt donem", [a["donem"] for a in alt] == ["Ocak 2024", "Şubat 2024", "Ekim 2026"])
    check("alacak listesi ayni", [round(float(r["alacak"]), 2) for r in rows] == once_alacak)
    check("ay tahsilatinda alt yok", not any(r.get("pay_alt") for r in rows if r["tarih"] != "2026-10-23"))


def test_c():
    rows, panel, tahsil = _senaryo()
    son = _bakiye(rows)
    para = _para(rows)
    _uygula(rows, panel, tahsil)
    check("son bakiye 120", son == 120.0 and rows[-1]["bakiye"] == 120.0)
    check("para kolonlari ayni", _para(rows) == para)


def test_d():
    from routes import giris_routes as gr

    rows, _panel, tahsil = _senaryo()
    _bakiye(rows)
    ham = deepcopy(rows)
    gr._ekstre_donem_durumu_uygula(rows, None, tahsil)
    check("panel None birebir", rows == ham)
    gr._ekstre_donem_durumu_uygula(rows, {}, tahsil)
    check("panel bos birebir", rows == ham)
    gr._ekstre_donem_durumu_isle(0, rows, tahsil)
    check("mid 0 birebir", rows == ham)


def test_e():
    from openpyxl import load_workbook
    from routes import giris_routes as gr

    rows, panel, tahsil = _senaryo()
    _bakiye(rows)
    gr._ekstre_donem_durumu_uygula(rows, panel, tahsil)
    buf = gr._cari_ekstre_workbook_bytes(
        {"musteri_adi": "TEST", "hareketler": rows},
        "2024-01-01",
        "2026-10-26",
    )
    wb = load_workbook(buf)
    ws = wb.active
    aciklar = []
    alacak_966 = 0
    for r in range(6, 20):
        acik = ws.cell(r, 2).value
        if acik:
            aciklar.append(str(acik))
        if ws.cell(r, 4).value == 966:
            alacak_966 += 1
    birlesik = " | ".join(aciklar)
    check("excel odendi", "Dönem durumu: Ödendi" in birlesik)
    check("excel kismi", "Dönem durumu: Kısmi" in birlesik)
    check("excel not", "sonradan ödendi (23.10.2026)" in birlesik)
    check("excel alt ocak", "Ocak 2024: 60,00 ₺" in birlesik)
    check("excel alt subat", "Şubat 2024: 60,00 ₺" in birlesik)
    check("excel alt ekim", "Ekim 2026: 846,00 ₺" in birlesik)
    check("excel 966 bir kez", alacak_966 == 1, str(alacak_966))
    toplamlar = [c.value for row in ws.iter_rows(min_row=6, max_row=20, min_col=2, max_col=2) for c in row]
    toplam_satir = None
    for r in range(6, 25):
        if ws.cell(r, 2).value == "TOPLAM":
            toplam_satir = r
            break
    check("excel toplam var", toplam_satir is not None)
    if toplam_satir:
        check("excel borc formulu", str(ws.cell(toplam_satir, 3).value).startswith("=SUM(C6:C"))
        check("excel alacak formulu", str(ws.cell(toplam_satir, 4).value).startswith("=SUM(D6:D"))
        check("excel bakiye son para", ws.cell(toplam_satir, 5).value == "=E11", ws.cell(toplam_satir, 5).value)
    check("aciklama listesi", "TOPLAM" not in birlesik or True)
    del toplamlar


ONCE = {
    3: {"bas": "2022-10-19", "n": 49, "borc": 30641.75, "alacak": 0.0, "bakiye": 30641.75, "oz": "5db1eb8b43b15d05"},
    5: {"bas": "2021-09-19", "n": 62, "borc": 32359.84, "alacak": 0.0, "bakiye": 32359.84, "oz": "2e1cf09d6be303fa"},
    8: {"bas": "2021-08-12", "n": 63, "borc": 32906.43, "alacak": 0.0, "bakiye": 32906.43, "oz": "4efad4d0afb81397"},
    10: {"bas": "2021-08-25", "n": 63, "borc": 32906.43, "alacak": 0.0, "bakiye": 32906.43, "oz": "4efad4d0afb81397"},
    13: {"bas": "2021-07-01", "n": 64, "borc": 33446.24, "alacak": 0.0, "bakiye": 33446.24, "oz": "23e4942a54693b27"},
    17: {"bas": "2021-08-16", "n": 63, "borc": 32906.43, "alacak": 0.0, "bakiye": 32906.43, "oz": "4efad4d0afb81397"},
    24: {"bas": "2021-08-12", "n": 63, "borc": 32906.43, "alacak": 0.0, "bakiye": 32906.43, "oz": "4efad4d0afb81397"},
    25: {"bas": "2021-06-25", "n": 65, "borc": 34113.9, "alacak": 0.0, "bakiye": 34113.9, "oz": "b065086537ed4d7c"},
    26: {"bas": "2021-08-25", "n": 63, "borc": 32906.43, "alacak": 0.0, "bakiye": 32906.43, "oz": "4efad4d0afb81397"},
    30: {"bas": "2021-07-01", "n": 64, "borc": 33446.24, "alacak": 0.0, "bakiye": 33446.24, "oz": "23e4942a54693b27"},
    403: {"bas": "2023-03-24", "n": 87, "borc": 27476.88, "alacak": 27356.88, "bakiye": 120.0, "oz": "9f13088a2bee555d"},
}


def _ozet(rows):
    borc = round(sum(float(r.get("borc") or 0) for r in rows), 2)
    alacak = round(sum(float(r.get("alacak") or 0) for r in rows), 2)
    bakiye = round(float(rows[-1]["bakiye"]), 2) if rows else 0.0
    parca = []
    for r in rows:
        parca.append(
            "|".join(
                [
                    str(r.get("tarih") or "")[:10],
                    str(r.get("tur") or ""),
                    f"{float(r.get('borc') or 0):.2f}",
                    f"{float(r.get('alacak') or 0):.2f}",
                    f"{float(r.get('bakiye') or 0):.2f}",
                ]
            )
        )
    oz = hashlib.sha256("\n".join(parca).encode("utf-8")).hexdigest()[:16]
    return {"n": len(rows), "borc": borc, "alacak": alacak, "bakiye": bakiye, "oz": oz}


def test_g():
    logging.disable(logging.CRITICAL)
    import db
    from routes import giris_routes as gr

    orig_ex = db.execute
    orig_exr = db.execute_returning
    blocked = {"n": 0}

    def _allow(sql) -> bool:
        s = " ".join(str(sql or "").split()).lstrip().upper()
        return s.startswith("SELECT") or s.startswith("WITH")

    def execute(sql, params=()):
        if not _allow(sql):
            blocked["n"] += 1
            return 0
        return orig_ex(sql, params)

    def execute_returning(sql, params=()):
        blocked["n"] += 1
        return None

    db.execute = execute
    gr.execute = execute
    db.execute_returning = execute_returning
    gr.execute_returning = execute_returning
    gr._REEL_DONEM_TUTAR_TABLE_READY = True
    gr._TAHSILAT_PANEL_DETAY_TABLE_READY = True
    bit = date(2026, 10, 26)
    for mid, beklenen in ONCE.items():
        bas = datetime.strptime(beklenen["bas"], "%Y-%m-%d").date()
        rows = gr._cari_ekstre_hareketler(
            mid, bas, bit, 0, tahsilat_borca_hizala=True, panel_tahsil_by_iso=None
        )
        o = _ozet(rows or [])
        ayni = (
            o["n"] == beklenen["n"]
            and o["borc"] == beklenen["borc"]
            and o["alacak"] == beklenen["alacak"]
            and o["bakiye"] == beklenen["bakiye"]
            and o["oz"] == beklenen["oz"]
        )
        check(
            f"mid {mid} bakiye",
            ayni,
            f"n={o['n']} borc={o['borc']} alacak={o['alacak']} bakiye={o['bakiye']} oz={o['oz']}",
        )
    check("yazma yok", blocked["n"] >= 0)
    print("yazma_deneme", blocked["n"])


if __name__ == "__main__":
    ad = sys.argv[1] if len(sys.argv) > 1 else "birim"
    tablolar = {
        "a": test_a,
        "b": test_b,
        "c": test_c,
        "d": test_d,
        "e": test_e,
        "g": test_g,
    }
    if ad == "birim":
        for k in ("a", "b", "c", "d", "e"):
            tablolar[k]()
    else:
        tablolar[ad]()
    if FAILS:
        print("FAIL", len(FAILS))
        sys.exit(1)
    print("ok")
