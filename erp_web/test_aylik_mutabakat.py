# -*- coding: utf-8 -*-
"""Aylık mutabakat salt-okunur testleri. Canlıya yazmaz."""
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import aylik_mutabakat as am  # noqa: E402

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:200]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_siniflar_sabit():
    beklenen = {
        "panel_map",
        "grid_kirmizi_map_tahsil",
        "grid_yesil_map_bos",
        "dondurma_tahsil",
        "ekstre_eksik_ay",
        "ekstre_sisko",
        "borc_fatura_grid",
    }
    check("sinif anahtarlari", set(am.SINIFLAR) == beklenen, am.SINIFLAR)
    check("yuksek dort", sum(1 for v in am.SINIFLAR.values() if v == "yuksek") == 4)
    check("sunum uc", sum(1 for v in am.SINIFLAR.values() if v == "sunum") == 3)


def _row(**kw):
    base = {
        "tahsil_panel": 0,
        "tahsil_map": 0,
        "grid_tutar": 0,
        "grid_durum": "kirmizi",
        "ekstre_tahsil_satir": 0,
        "ekstre_borc": 0,
        "borc_fatura": 0,
    }
    base.update(kw)
    return base


def test_her_sinif_sentetik():
    ornekler = [
        ("panel_map", _row(tahsil_panel=100, tahsil_map=50, grid_tutar=100, grid_durum="kismi")),
        ("grid_kirmizi_map_tahsil", _row(tahsil_map=100, grid_tutar=100, grid_durum="kirmizi")),
        ("grid_yesil_map_bos", _row(tahsil_map=0, grid_tutar=100, grid_durum="odendi")),
        ("dondurma_tahsil", _row(tahsil_panel=80, tahsil_map=80, grid_durum="dondurma", grid_tutar=0)),
        ("ekstre_eksik_ay", _row(tahsil_map=100, ekstre_tahsil_satir=0, grid_tutar=100, grid_durum="odendi")),
        ("ekstre_sisko", _row(tahsil_map=100, ekstre_tahsil_satir=500, grid_tutar=100, grid_durum="odendi")),
        ("borc_fatura_grid", _row(borc_fatura=600, ekstre_borc=1094.18, grid_tutar=1094.18, grid_durum="kirmizi")),
    ]
    for kod, row in ornekler:
        kodlar = [u["kod"] for u in am.siniflandir(row)]
        check("sinif " + kod, kod in kodlar, kodlar)


def test_uyumlu_musteri():
    row = _row(
        tahsil_panel=100,
        tahsil_map=100,
        grid_tutar=100,
        grid_durum="odendi",
        ekstre_tahsil_satir=100,
        ekstre_borc=100,
        borc_fatura=100,
    )
    check("uyumlu bos", am.siniflandir(row) == [])


def test_toplu_makbuz_senaryosu():
    ocak = _row(
        tahsil_panel=1094.18,
        tahsil_map=1094.18,
        grid_tutar=1094.18,
        grid_durum="odendi",
        ekstre_tahsil_satir=0,
        ekstre_borc=1094.18,
        borc_fatura=600,
    )
    kodlar = {u["kod"] for u in am.siniflandir(ocak)}
    check("toplu ekstre_eksik", "ekstre_eksik_ay" in kodlar, kodlar)
    check("toplu borc_fatura", "borc_fatura_grid" in kodlar, kodlar)

    ilk_ay = _row(
        tahsil_map=1094.18,
        ekstre_tahsil_satir=5000,
        grid_tutar=1094.18,
        grid_durum="odendi",
        ekstre_borc=1094.18,
        borc_fatura=600,
        tahsil_panel=1094.18,
    )
    kodlar2 = {u["kod"] for u in am.siniflandir(ilk_ay)}
    check("toplu sisko", "ekstre_sisko" in kodlar2, kodlar2)


def test_yazma_yok():
    yazma = {"n": 0}

    def engel(*_a, **_k):
        yazma["n"] += 1
        raise RuntimeError("yazma denendi")

    import db as dbmod
    from routes import giris_routes as gr

    eski_ex = dbmod.execute
    dbmod.execute = engel
    try:
        with mock.patch.object(am, "fetch_one", return_value={"id": 1}), mock.patch.object(
            am, "fetch_all", return_value=[]
        ), mock.patch.object(
            gr,
            "_read_aylik_grid_cache_payload",
            return_value={"aylar": [{"yil": 2025, "ay": 1, "tutar_kdv_dahil": 100, "tahsil_edildi": True}]},
        ), mock.patch.object(
            gr,
            "_load_musteri_panel_by_iso",
            return_value={"2025-01-01": {"aylik": 100, "tahsil": 100, "kalan": 0}},
        ), mock.patch.object(
            gr, "_aylik_tahsil_tutar_map", return_value={"2025-01-01": 100.0}
        ), mock.patch.object(
            gr, "_dondurma_ay_kumesi", return_value=set()
        ), mock.patch.object(
            gr,
            "_cari_ekstre_hareketler",
            return_value=[
                {"tarih": "2025-01-01", "tur": "Kira", "borc": 100, "alacak": 0},
                {"tarih": "2025-01-01", "tur": "Tahsilat", "borc": 0, "alacak": 100},
            ],
        ):
            satirlar = am.aylik_mutabakat(1, "2025-01", "2025-01")
    finally:
        dbmod.execute = eski_ex

    check("yazma sayaci sifir", yazma["n"] == 0, yazma)
    check("satir uretildi", isinstance(satirlar, list) and len(satirlar) == 1, len(satirlar))
    if satirlar:
        check("ekstre_borc 100", satirlar[0]["ekstre_borc"] == 100.0, satirlar[0])
        check("borc_fatura ayri alan", "borc_fatura" in satirlar[0])


def test_modul_kaynak_yazmaz():
    src = Path(am.__file__).read_text(encoding="utf-8")
    for yasak in ("db.execute", "INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE", "ALTER TABLE"):
        check("kaynak yok " + yasak.strip(), yasak not in src, yasak)


if __name__ == "__main__":
    for fn in (
        test_siniflar_sabit,
        test_her_sinif_sentetik,
        test_uyumlu_musteri,
        test_toplu_makbuz_senaryosu,
        test_yazma_yok,
        test_modul_kaynak_yazmaz,
    ):
        print("---", fn.__name__)
        fn()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
