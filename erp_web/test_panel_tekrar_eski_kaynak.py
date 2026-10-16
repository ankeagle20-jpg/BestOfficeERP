# -*- coding: utf-8 -*-
"""Borçlandır kural A: boş kaynaklı marker + bayrak + onay_eski_kayit."""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import panel_tekrar as pt  # noqa: E402
from test_panel_tekrar import Sahte, _tah, _borc, ISARET, MID, AY, check, FAILS  # noqa: E402

FAILS.clear()


def _flag(on: bool):
    if on:
        os.environ["PANEL_TEKRAR_ESKI_KAYNAK_ENABLED"] = "1"
    else:
        os.environ.pop("PANEL_TEKRAR_ESKI_KAYNAK_ENABLED", None)


def test_bayrak_kapali_ayni():
    _flag(False)
    try:
        check("bayrak varsayilan kapali", pt.eski_kaynak_enabled() is False)
        bos = _tah(None, 10, "Tem " + "|AYLIK_TAH|2025-07-01|", tahsilat_tarihi="2025-07-01")
        plan = pt.karar_ver([(2025, 7)], [bos], [], taraf="borc")
        check(
            "kapali bos silinmez",
            plan["sil_tahsil"] == [] and plan["atla_tam"].get("2025-07", {}).get("neden") == "eski_bos_kaynak",
            plan["atla_tam"].get("2025-07"),
        )
        check("kapali eski liste bos", plan.get("silinecek_eski") == [])
        check(
            "kapali metin eski etiketsiz",
            "Eski kayıt, kaynağı etiketsiz" in (plan.get("mesaj_onay") or "")
            and "baska_kaynak" not in (plan.get("mesaj_onay") or ""),
            plan.get("mesaj_onay"),
        )
    finally:
        _flag(False)


def test_mid87_tipi_onay_eski():
    _flag(True)
    try:
        rows = []
        for i, (y, a) in enumerate(
            [(2025, 7), (2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12),
             (2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5), (2026, 6)],
            start=100,
        ):
            iso = f"{y:04d}-{a:02d}-01"
            rows.append(_tah(None, i, f"Ay|AYLIK_TAH|{iso}|", tahsilat_tarihi=iso, tutar=960.0))
        aylar = [(int(r["tahsilat_tarihi"][:4]), int(r["tahsilat_tarihi"][5:7])) for r in rows]
        plan = pt.karar_ver(aylar, rows, [], taraf="borc")
        check("87 onay gerekli", plan["onay_gerekli"] is True and plan["onay_eski_kayit_gerekli"] is True)
        check("87 12 eski", len(plan["sil_tahsil_eski"]) == 12 and len(plan["sil_tahsil"]) == 12, len(plan.get("sil_tahsil") or []))
        check("87 grid yok", plan["sil_tahsil_grid"] == [])

        p2 = copy.deepcopy(plan)
        pt.plan_eski_filtrele(p2, False)
        check("87 onaysiz silme yok", p2["sil_tahsil"] == [] and p2["panel_n"] == 0)

        db = Sahte()
        for r in rows:
            db.tahsilatlar[r["id"]] = copy.deepcopy(r)
        p3 = copy.deepcopy(plan)
        pt.plan_eski_filtrele(p3, True)
        sonuc = pt.panel_satirlari_sil(
            db.calistir, db.oku,
            tahsil_rows=p3["sil_tahsil"], borc_rows=[],
            musteri_id=MID, kullanici="1", taraf="borc",
        )
        check("87 12 silindi", len(sonuc["tahsil_silinen"]) == 12 and len(db.tahsilatlar) == 0)
        check("87 yedek 12", len(db.yedek) == 12)
        audit = json.dumps(db.audit, ensure_ascii=False, default=str)
        check("87 audit eski_bos", "eski_bos" in audit)
        for y in db.yedek:
            geri = pt.panel_satir_geri_yaz(y["id"], db.calistir, db.oku)
            check("87 geri ok", geri.get("ok") is True, geri)
        check("87 geri 12", len(db.tahsilatlar) == 12)
        null_ok = all(r.get("kaynak") is None for r in db.tahsilatlar.values())
        check("87 kaynak NULL", null_ok)
        ikinci = pt.panel_satir_geri_yaz(db.yedek[0]["id"], db.calistir, db.oku)
        check("87 ikinci ret", ikinci.get("neden") == "zaten_geri_yazildi", ikinci)
    finally:
        _flag(False)


def test_mid117_tipi():
    _flag(True)
    try:
        r1 = _tah(None, 1, "|AYLIK_TAH|2022-12-01|", tahsilat_tarihi="2022-12-01", tutar=2300)
        r2 = _tah(None, 2, "|AYLIK_TAH|2023-01-01|", tahsilat_tarihi="2023-01-01", tutar=2300)
        r3 = _tah(None, 3, "|AYLIK_TAH|2023-02-01|", tahsilat_tarihi="2026-07-24", tutar=2300)
        r4 = _tah(None, 4, "|AYLIK_TAH|2023-03-01|", tahsilat_tarihi="2026-07-24", tutar=2300)
        r5 = _tah(None, 5, "|AYLIK_TAH|2023-04-01|", tahsilat_tarihi="2026-07-24", tutar=2300)
        plan = pt.karar_ver(
            [(2022, 12), (2023, 1), (2023, 2), (2023, 3), (2023, 4)],
            [r1, r2, r3, r4, r5], [], taraf="borc",
        )
        ids = sorted(_tah_id(r) for r in plan["sil_tahsil"])
        check("117 sil 2", ids == [1, 2], ids)
        check("117 koru 3", plan["diger_m"] >= 3)
        korunan_ay = {
            (d.get("yil"), d.get("ay"))
            for d in (plan.get("degismeyecek") or [])
            if d.get("neden") in ("tarih_uyusmaz", "baska_kaynak")
        }
        check("117 degis tarih", (2023, 2) in korunan_ay and (2023, 3) in korunan_ay and (2023, 4) in korunan_ay, korunan_ay)
    finally:
        _flag(False)


def _tah_id(r):
    return int(r["id"])


def test_mid148_uyari_onaysiz():
    _flag(True)
    try:
        r = _tah(None, 1986, "|AYLIK_TAH|2025-10-01|", tahsilat_tarihi="2025-10-01", tutar=1200)
        plan = pt.karar_ver([(2025, 10)], [r], [], taraf="borc")
        check("148 listelenir", len(plan["silinecek_eski"]) == 1 and plan["onay_eski_kayit_gerekli"] is True)
        p2 = copy.deepcopy(plan)
        pt.plan_eski_filtrele(p2, False)
        check("148 onaysiz silinmez", p2["sil_tahsil"] == [])
        db = Sahte()
        db.tahsilatlar[1986] = copy.deepcopy(r)
        # filtre sonrası boş liste → sil çağrılmaz; satır durur
        check("148 satir duruyor", 1986 in db.tahsilatlar)
    finally:
        _flag(False)


def test_cakisma_makbuz():
    _flag(True)
    try:
        eski = _tah(None, 1, "|AYLIK_TAH|2026-03-01|", tahsilat_tarihi="2026-03-01")
        makbuz = _tah("manuel_makbuz", 2, "makbuz " + ISARET, tahsilat_tarihi="2026-03-15")
        plan = pt.karar_ver([(2026, 3)], [eski, makbuz], [], taraf="borc")
        check("cakisma sil yok", plan["sil_tahsil"] == [])
        check("cakisma atla", plan["atla_tam"].get(AY, {}).get("neden") == "baska_kaynak" or any(
            u.get("neden") == "cakisma_koru" for u in plan.get("uyarilar") or []
        ))
    finally:
        _flag(False)


def test_tahsil_yonu_etkilenmez():
    _flag(True)
    try:
        bos = _tah(None, 10, "|AYLIK_TAH|2026-03-01|", tahsilat_tarihi="2026-03-01")
        plan = pt.karar_ver([(2026, 3)], [bos], [], taraf="tahsil")
        check("tahsil eski yok", plan["sil_tahsil"] == [] and plan.get("silinecek_eski") == [])
        check("tahsil eski_bos (bos kaynak)", plan["atla_tam"].get(AY, {}).get("neden") == "eski_bos_kaynak")
        check("tahsil eski_enabled false in plan", plan.get("eski_kaynak_enabled") is False)
    finally:
        _flag(False)


def test_null_vs_bos_string():
    _flag(True)
    try:
        n = _tah(None, 1, "|AYLIK_TAH|2026-03-01|", tahsilat_tarihi="2026-03-01")
        e = _tah("", 2, "|AYLIK_TAH|2026-04-01|", tahsilat_tarihi="2026-04-01")
        check("null bos", pt.kaynak_bos_mu(None) is True and pt.kaynak_bos_mu("") is True)
        check("dolu degil", pt.kaynak_bos_mu("grid_toplu") is False)
        plan = pt.karar_ver([(2026, 3), (2026, 4)], [n, e], [], taraf="borc")
        check("null ve '' silinir", len(plan["sil_tahsil"]) == 2)
        db = Sahte()
        db.tahsilatlar[1] = copy.deepcopy(n)
        db.tahsilatlar[2] = copy.deepcopy(e)
        pt.panel_satirlari_sil(
            db.calistir, db.oku, tahsil_rows=plan["sil_tahsil"], borc_rows=[],
            musteri_id=MID, kullanici="1", taraf="borc",
        )
        # geri: NULL vs '' korunur
        g1 = pt.panel_satir_geri_yaz(db.yedek[0]["id"], db.calistir, db.oku)
        g2 = pt.panel_satir_geri_yaz(db.yedek[1]["id"], db.calistir, db.oku)
        check("geri ok", g1.get("ok") and g2.get("ok"))
        orij1 = json.loads(db.yedek[0]["satir_json"])
        orij2 = json.loads(db.yedek[1]["satir_json"])
        check("null korunur", db.tahsilatlar[orij1["id"]].get("kaynak") is None)
        check("bos string korunur", db.tahsilatlar[orij2["id"]].get("kaynak") == "")
    finally:
        _flag(False)


def test_tarih_text_tipi():
    _flag(True)
    try:
        # TEXT tahsilat_tarihi (PG canlı)
        r = _tah(None, 1, "|AYLIK_TAH|2026-03-01|", tahsilat_tarihi="2026-03-01")
        check("text ayin1i", pt.eski_panel_silinebilir_mi(r, "2026-03") is True)
        r2 = _tah(None, 2, "|AYLIK_TAH|2026-03-01|", tahsilat_tarihi="2026-03-15")
        check("text gun farkli", pt.eski_panel_silinebilir_mi(r2, "2026-03") is False)
        from datetime import date
        r3 = _tah(None, 3, "|AYLIK_TAH|2026-03-01|", tahsilat_tarihi=date(2026, 3, 1))
        check("date tip", pt.eski_panel_silinebilir_mi(r3, "2026-03") is True)
    finally:
        _flag(False)


def test_durust_mesaj():
    _flag(False)
    try:
        panel = _tah("grid_toplu", 1, "x" + ISARET)
        banka = _tah("banka_import", 2, "b" + ISARET)
        plan = pt.karar_ver([(2026, 3)], [panel, banka], [], taraf="borc")
        check("mesaj silinecek", "Silinecek" in (plan.get("mesaj_onay") or "") or "grid" in (plan.get("mesaj_onay") or "").lower())
        check("listeler var", isinstance(plan.get("silinecek_grid"), list) and isinstance(plan.get("degismeyecek"), list))
        check(
            "mesaj makbuz/banka dili",
            "makbuz/banka/ödeme linki" in (plan.get("mesaj_onay") or ""),
            plan.get("mesaj_onay"),
        )
    finally:
        _flag(False)


def test_neden_metin_ve_atlanan_mesaj():
    check(
        "metin eski_bos",
        "etiketsiz" in pt.neden_kullanici_metni("eski_bos_kaynak")
        and "yönetici ayarı" in pt.neden_kullanici_metni("eski_bos_kaynak"),
    )
    check(
        "metin baska",
        "makbuz/banka" in pt.neden_kullanici_metni("baska_kaynak"),
    )
    check(
        "metin tarih",
        "gerçek ödeme" in pt.neden_kullanici_metni("tarih_uyusmaz"),
    )
    check(
        "metin cakisma",
        "makbuz/banka kaydı var" in pt.neden_kullanici_metni("cakisma_makbuz_banka"),
    )
    msg = pt.atlanan_neden_mesajlari(
        [
            {"yil": 2025, "ay": 7, "neden": "eski_bos_kaynak"},
            {"yil": 2025, "ay": 8, "neden": "eski_bos_kaynak"},
            {"yil": 2025, "ay": 9, "neden": "baska_kaynak"},
            {"yil": 2025, "ay": 10, "neden": "tarih_uyusmaz"},
        ]
    )
    check("grup eski", "etiketsiz" in msg and "2025-07" in msg and "2025-08" in msg, msg)
    check("grup baska", "makbuz/banka/ödeme linki" in msg and "2025-09" in msg, msg)
    check("grup tarih", "gerçek ödeme" in msg and "2025-10" in msg, msg)
    check("ham kod yok", "baska_kaynak" not in msg and "eski_bos_kaynak" not in msg, msg)


if __name__ == "__main__":
    test_bayrak_kapali_ayni()
    test_mid87_tipi_onay_eski()
    test_mid117_tipi()
    test_mid148_uyari_onaysiz()
    test_cakisma_makbuz()
    test_tahsil_yonu_etkilenmez()
    test_null_vs_bos_string()
    test_tarih_text_tipi()
    test_durust_mesaj()
    test_neden_metin_ve_atlanan_mesaj()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
