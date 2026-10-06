"""Ekstre FIFO ay dağıtımı. Canlı veritabanına yazmaz."""
from datetime import date

from ay_fifo import TOL, acik_ay_dagit, dagit_seri
from routes.faturalar_routes import _auto_allocate_oldest_unpaid_months


def _borc_ornek():
    rows = []
    y, m = 2022, 9
    while (y, m) <= (2024, 3):
        tutar = 300.0 if (y, m) < (2023, 9) else 571.0
        rows.append((f"{y:04d}-{m:02d}-01", tutar))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return rows


def test_ornek_360_ve_1800():
    borc = _borc_ornek()
    ilk = acik_ay_dagit(borc, 360, "2024-03-05")
    assert ilk == [("2022-09-01", 300.0), ("2022-10-01", 60.0)], ilk
    ikinci = acik_ay_dagit(
        borc,
        1800,
        "2024-03-05",
        onceki=[{"tarih": "2024-03-05", "tutar": 360, "id": 1}],
    )
    assert ikinci == [
        ("2022-10-01", 240.0),
        ("2022-11-01", 300.0),
        ("2022-12-01", 300.0),
        ("2023-01-01", 300.0),
        ("2023-02-01", 300.0),
        ("2023-03-01", 300.0),
        ("2023-04-01", 60.0),
    ], ikinci


def test_yil_filtresi_ve_baslangic_yok_sayilir():
    borc = _borc_ornek()
    _iso, pays = _auto_allocate_oldest_unpaid_months(
        1,
        360,
        [{"iso": "2023-12-01", "kalan": 33}, {"iso": "2024-01-01", "kalan": 571}],
        "2023-12-01",
        odeme_tarihi="2024-03-05",
        borc_satirlari=borc,
        onceki_tahsilatlar=[],
    )
    assert pays[0] == ("2022-09-01", 300.0), pays


def test_ileri_tarihli_isaret_etkisiz():
    borc = _borc_ornek()
    pays = acik_ay_dagit(
        borc,
        360,
        "2024-03-05",
        onceki=[{
            "tarih": "2026-09-01",
            "tutar": 6000,
            "id": 99,
            "parca": "2022-09-01:360",
        }],
    )
    assert pays == [("2022-09-01", 300.0), ("2022-10-01", 60.0)], pays


def test_elle_ay_secimi():
    borc = _borc_ornek()
    pays = acik_ay_dagit(
        borc,
        400,
        "2024-03-05",
        allowlist=["2024-01-01", "2024-03-01"],
    )
    assert pays == [("2024-01-01", 400.0)], pays


def test_avans_vadesi_gelmis_kapaninca():
    borc = [("2024-03-01", 10.0), ("2024-04-01", 100.0), ("2024-05-01", 100.0)]
    pays = acik_ay_dagit(borc, 50, "2024-03-05")
    assert pays == [("2024-03-01", 10.0), ("2024-04-01", 40.0)], pays


def test_kismi_ay():
    borc = [("2022-09-01", 300.0), ("2022-10-01", 300.0)]
    pays = acik_ay_dagit(
        borc,
        100,
        "2024-03-05",
        onceki=[{"tarih": "2024-01-01", "tutar": 240, "id": 1}],
    )
    assert pays == [("2022-09-01", 60.0), ("2022-10-01", 40.0)], pays


def test_tolerans_001():
    borc = [("2022-09-01", 0.01), ("2022-10-01", 300.0)]
    pays = acik_ay_dagit(borc, 10, "2024-03-05")
    assert pays == [("2022-10-01", 10.0)], pays
    assert TOL == 0.01


def test_banka_ayni_istek_sirasi():
    borc = _borc_ornek()
    _a, ilk = _auto_allocate_oldest_unpaid_months(
        1, 360, odeme_tarihi="2024-03-05", borc_satirlari=borc, onceki_tahsilatlar=[],
    )
    _b, ikinci = _auto_allocate_oldest_unpaid_months(
        1,
        1800,
        odeme_tarihi="2024-03-05",
        borc_satirlari=borc,
        onceki_tahsilatlar=[],
        ek_onceki=[{"tarih": "2024-03-05", "tutar": 360, "id": 10**12 + 1}],
    )
    assert ilk[0] == ("2022-09-01", 300.0)
    assert ikinci[0] == ("2022-10-01", 240.0)
    assert ikinci[-1] == ("2023-04-01", 60.0)


def test_seri_ileri_tarih_oncekiyi_degistirmez():
    borc = _borc_ornek()
    parcalar = dagit_seri(borc, [
        {"id": 2, "tarih": "2024-03-05", "tutar": 360},
        {"id": 1, "tarih": "2026-09-01", "tutar": 6000},
    ])
    assert parcalar[0][0] == ("2022-09-01", 300.0)
    assert parcalar[1][0][0] == "2022-10-01"


def test_cagri_metinleri():
    from pathlib import Path
    banka = Path("routes/banka_routes.py").read_text(encoding="utf-8")
    assert "odeme_tarihi=tah_str" in banka
    assert "ek_onceki=batch_onceki.get(mid)" in banka
    assert "_ensure_aylik_cache" not in banka
    fatura = Path("routes/faturalar_routes.py").read_text(encoding="utf-8")
    assert "musteri_aylik_grid_cache" not in fatura.split("def _auto_allocate_oldest_unpaid_months")[1].split("def _tahsil_rapor_yil_ay_coerce")[0]
    betik = Path("scripts/migrate_banka_import_aylik_markers.py").read_text(encoding="utf-8")
    assert "Aşama 3 onayı olmadan çalıştırılmaz" in betik
    assert "haric_tahsilat_id=tid" in betik
    assert "_alloc_oldest" not in betik


if __name__ == "__main__":
    test_ornek_360_ve_1800()
    test_yil_filtresi_ve_baslangic_yok_sayilir()
    test_ileri_tarihli_isaret_etkisiz()
    test_elle_ay_secimi()
    test_avans_vadesi_gelmis_kapaninca()
    test_kismi_ay()
    test_tolerans_001()
    test_banka_ayni_istek_sirasi()
    test_seri_ileri_tarih_oncekiyi_degistirmez()
    test_cagri_metinleri()
    print("CASE ay_fifo ok", date.today().isoformat())
