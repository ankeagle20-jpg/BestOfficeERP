"""Ekstre FIFO ay dağıtımı. Canlı veritabanına yazmaz."""
from datetime import date

from ay_fifo import TOL, acik_ay_dagit, dagit_seri, gorunen_borc_biles, reel_ay_haritasi
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
    dilim = fatura.split("def _auto_allocate_oldest_unpaid_months")[1].split("def _tahsil_rapor_yil_ay_coerce")[0]
    assert "musteri_aylik_grid_cache" not in dilim
    assert "_gorunen_borc_satirlari" in dilim
    assert "ay_borc_yedek adet" in fatura
    betik = Path("asama3_ay.py").read_text(encoding="utf-8")
    yukleme = betik.split("def hesap_yukle")[1].split("def _yedek_yolu")[0]
    assert "gorunen_borc_biles" in yukleme
    html = Path("templates/giris/index.html").read_text(encoding="utf-8")

    def pencere(bas, uzun=2200):
        i = html.find(bas)
        assert i >= 0, bas
        return html[i:i + uzun]

    giris = pencere("function girisTahsilatFormPayload")
    soz = pencere("function sozlesmeTahsilatFormPayload")
    assert "ayRefIsoList = ayRefIsoListRaw.slice()" in giris
    assert "ayRefIsoList = ayRefIsoListRaw.slice()" in soz
    assert "tekAyTutar" not in giris
    secilen = pencere("function sozlesmeAylikTahsilEt", 3200)
    assert "aylik-tutarlardan-tahsil-et" in secilen
    assert "ayRefIsoList" not in secilen
    betik_m = Path("scripts/migrate_banka_import_aylik_markers.py").read_text(encoding="utf-8")
    assert "Aşama 3 onayı olmadan çalıştırılmaz" in betik_m
    assert "haric_tahsilat_id=tid" in betik_m
    assert "_alloc_oldest" not in betik_m


def _borc_867():
    rows = []
    for m in range(1, 8):
        rows.append((f"2024-{m:02d}-01", 720.0))
    y, m = 2024, 8
    while (y, m) <= (2025, 6):
        rows.append((f"{y:04d}-{m:02d}-01", 1094.18))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return rows


def _onceki_867(agustos_bir=True):
    onceki = []
    for m in range(1, 8):
        iso = f"2024-{m:02d}-01"
        onceki.append({
            "id": m,
            "tarih": iso,
            "tutar": 720.0,
            "aciklama": f"|AYLIK_TAH|{iso}|",
        })
    if agustos_bir:
        onceki.append({
            "id": 8,
            "tarih": "2024-08-01",
            "tutar": 1.0,
            "aciklama": "|AYLIK_PAY|2024-08-01=1.00|",
        })
    return onceki


def test_867_bin_agustos():
    pays = acik_ay_dagit(_borc_867(), 1000, "2026-10-07", onceki=_onceki_867(True))
    assert pays == [("2024-08-01", 1000.0)], pays
    assert not any(iso.startswith("2025") for iso, _t in pays)


def test_867_tam_acik_ve_uc_bin():
    tam = acik_ay_dagit(_borc_867(), 1000, "2026-10-07", onceki=_onceki_867(False))
    assert tam == [("2024-08-01", 1000.0)], tam
    uc = acik_ay_dagit(_borc_867(), 3000, "2026-10-07", onceki=_onceki_867(False))
    assert uc == [
        ("2024-08-01", 1094.18),
        ("2024-09-01", 1094.18),
        ("2024-10-01", 811.64),
    ], uc
    kalanli = acik_ay_dagit(_borc_867(), 3000, "2026-10-07", onceki=_onceki_867(True))
    assert kalanli[0] == ("2024-08-01", 1093.18), kalanli
    assert kalanli[1] == ("2024-09-01", 1094.18)
    assert kalanli[2] == ("2024-10-01", 812.64)


def test_vadesi_gelmis_kapaninca_avans():
    borc = [("2024-08-01", 1094.18), ("2024-09-01", 1094.18)]
    onceki = [{"id": 1, "tarih": "2024-08-01", "tutar": 1094.18, "aciklama": "|AYLIK_TAH|2024-08-01|"}]
    pays = acik_ay_dagit(borc, 40, "2024-08-20", onceki=onceki)
    assert pays == [("2024-09-01", 40.0)], pays


def test_yil_listesi_867_etkisiz():
    _iso, pays = _auto_allocate_oldest_unpaid_months(
        1,
        1000,
        [{"iso": "2025-01-01", "kalan": 1094.18}],
        "2025-01-01",
        odeme_tarihi="2026-10-07",
        borc_satirlari=_borc_867(),
        onceki_tahsilatlar=_onceki_867(True),
    )
    assert pays == [("2024-08-01", 1000.0)], pays


def test_secilen_ay_acik_tutardan():
    pays = acik_ay_dagit(
        _borc_867(),
        2000,
        "2026-10-07",
        onceki=_onceki_867(True),
        allowlist=["2024-09-01", "2024-11-01"],
    )
    assert pays == [("2024-09-01", 1094.18), ("2024-11-01", 905.82)], pays


def test_isaret_sonraki_aya_tasmaz():
    borc = [("2024-01-01", 400.0), ("2024-02-01", 400.0)]
    onceki = [{
        "id": 1,
        "tarih": "2024-01-01",
        "tutar": 720.0,
        "aciklama": "|AYLIK_TAH|2024-01-01|",
    }]
    pays = acik_ay_dagit(borc, 100, "2024-03-01", onceki=onceki)
    assert pays == [("2024-02-01", 100.0)], pays
    kismi = acik_ay_dagit(
        [("2024-01-01", 720.0), ("2024-02-01", 720.0)],
        100,
        "2024-03-01",
        onceki=[{
            "id": 1,
            "tarih": "2024-01-01",
            "tutar": 720.0,
            "aciklama": "|AYLIK_PAY|2024-01-01=400.00|",
        }],
    )
    assert kismi == [("2024-01-01", 100.0)], kismi


def test_fatura_yedek_ve_reel():
    borc, yedek = gorunen_borc_biles(
        {"2024-01-01": 720.0},
        {"2024-01-01": 300.0, "2024-08-01": 1094.18},
        {"2024-01-01": 400.0, "2024-02-01": 400.0},
    )
    assert borc["2024-01-01"] == 720.0
    assert borc["2024-08-01"] == 1094.18
    assert borc["2024-02-01"] == 400.0
    assert yedek == ["2024-02-01"], yedek
    reel = reel_ay_haritasi([(2024, 1094.18)], "2024-08-01")
    assert reel["2024-08-01"] == 1094.18
    assert reel["2025-07-01"] == 1094.18
    assert "2024-07-01" not in reel


def test_banka_yolu_gorunen_borc():
    _iso, pays = _auto_allocate_oldest_unpaid_months(
        1,
        1000,
        odeme_tarihi="2026-10-07",
        borc_satirlari=_borc_867(),
        onceki_tahsilatlar=_onceki_867(False),
        ek_onceki=[{
            "id": 50,
            "tarih": "2024-08-01",
            "tutar": 1.0,
            "aciklama": "|AYLIK_PAY|2024-08-01=1.00|",
        }],
    )
    assert pays == [("2024-08-01", 1000.0)], pays


def test_tutarli_hesap_ayni_dagilim():
    borc = _borc_ornek()
    harita = {iso: tutar for iso, tutar in borc}
    gorunen, yedek = gorunen_borc_biles(harita, harita, harita)
    assert yedek == []
    assert acik_ay_dagit(gorunen, 360, "2024-03-05") == acik_ay_dagit(borc, 360, "2024-03-05")
    seri = dagit_seri(gorunen, [
        {"id": 7, "tarih": "2024-03-05", "tutar": 360, "aciklama": "|AYLIK_PAY|2024-01-01=360.00|"},
    ])
    assert seri[0] == [("2022-09-01", 300.0), ("2022-10-01", 60.0)], seri


def test_tah_isaret_kaynagi_ve_cift_sayim():
    """Tutar AYLIK_TAH'tan gelir. PAY varsa PAY tutarı kullanılır, ikisi toplanmaz."""
    from ay_fifo import _isaret
    tah = {"tutar": 10000, "aciklama": "|AYLIK_TAH|2026-08-01|"}
    assert _isaret(tah) == ("tah", ["2026-08-01"])
    borc = [
        ("2026-06-01", 10000.0),
        ("2026-07-01", 10000.0),
        ("2026-08-01", 10000.0),
        ("2026-09-01", 10000.0),
    ]
    rows = [
        {"id": 5819, "tarih": "2026-08-01", "tutar": 10000, "aciklama": "|AYLIK_TAH|2026-08-01|"},
        {"id": 5701, "tarih": "2026-08-28", "tutar": 10000, "aciklama": "|AYLIK_PAY|2026-06-01=10000.00|"},
        {"id": 5700, "tarih": "2026-09-28", "tutar": 10000, "aciklama": "|AYLIK_PAY|2026-07-01=10000.00|"},
    ]
    parca = dagit_seri(borc, rows)
    assert parca[0] == [("2026-08-01", 10000.0)], parca[0]
    assert parca[1] == [("2026-06-01", 10000.0)], parca[1]
    assert parca[2] == [("2026-07-01", 10000.0)], parca[2]
    agustos = sum(t for p in parca for iso, t in p if iso == "2026-08-01")
    assert agustos == 10000.0
    pays = acik_ay_dagit(
        borc,
        10000,
        "2026-09-28",
        onceki=[rows[0]],
    )
    assert pays == [("2026-06-01", 10000.0)], pays
    karisik = acik_ay_dagit(
        [("2026-08-01", 10000.0)],
        100,
        "2026-09-28",
        onceki=[{
            "id": 1,
            "tarih": "2026-08-01",
            "tutar": 10000,
            "aciklama": "|AYLIK_TAH|2026-08-01| |AYLIK_PAY|2026-08-01=1.00|",
        }],
    )
    assert _isaret({"aciklama": "|AYLIK_TAH|2026-08-01| |AYLIK_PAY|2026-08-01=1.00|"})[0] == "pay"
    assert karisik == [("2026-08-01", 100.0)], karisik


def _aylar(y, m, y2, m2, tutar):
    out = []
    while (y, m) <= (y2, m2):
        out.append((f"{y:04d}-{m:02d}-01", tutar))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def test_fatura_yedek_sozlesme_oncesi_yok():
    fatura = {f"2023-{m:02d}-01": 300.0 for m in range(1, 8)}
    fatura["2023-08-01"] = 300.0
    borc, yedek = gorunen_borc_biles(
        None, None, fatura, sozlesme_basi="2023-08-01",
    )
    assert "2023-01-01" not in borc
    assert borc["2023-08-01"] == 300.0
    assert yedek == ["2023-08-01"], yedek
    eski, yedek_eski = gorunen_borc_biles(None, None, fatura)
    assert eski["2023-01-01"] == 300.0
    assert "2023-01-01" in yedek_eski


def test_867_bes_bin_sozlesme_sonrasi():
    """İlk ay Ağustos 2023. 5.000, kapalı ilk yıldan sonra Ağustos 2024'ten kapanır."""
    borc = [(f"2023-{m:02d}-01", 300.0) for m in range(1, 8)]
    borc += _aylar(2023, 8, 2024, 7, 720.0)
    borc += _aylar(2024, 8, 2024, 12, 1094.18)
    onceki = []
    for i, (iso, _t) in enumerate(_aylar(2023, 8, 2024, 7, 720.0), start=1):
        onceki.append({
            "id": i,
            "tarih": iso,
            "tutar": 720.0,
            "aciklama": f"|AYLIK_TAH|{iso}|",
        })
    pays = acik_ay_dagit(
        borc, 5000, "2026-10-07", onceki=onceki, sozlesme_basi="2023-08-01",
    )
    assert pays == [
        ("2024-08-01", 1094.18),
        ("2024-09-01", 1094.18),
        ("2024-10-01", 1094.18),
        ("2024-11-01", 1094.18),
        ("2024-12-01", 623.28),
    ], pays
    assert not any(iso < "2023-08-01" for iso, _t in pays)
    assert round(sum(t for _iso, t in pays), 2) == 5000.0


def test_baslangic_tarihi_yoksa_eski_fifo():
    borc = [(f"2023-{m:02d}-01", 300.0) for m in range(1, 4)]
    pays = acik_ay_dagit(borc, 500, "2026-10-07")
    assert pays[0] == ("2023-01-01", 300.0), pays
    assert pays[1] == ("2023-02-01", 200.0)


def test_isaret_ve_allowlist_sozlesme_sinirini_gecer():
    borc = [("2023-01-01", 300.0), ("2024-08-01", 1094.18)]
    pays = acik_ay_dagit(
        borc,
        100,
        "2026-10-07",
        onceki=[{
            "id": 1,
            "tarih": "2026-10-01",
            "tutar": 300.0,
            "aciklama": "|AYLIK_PAY|2023-01-01=300.00|",
        }],
        sozlesme_basi="2023-08-01",
    )
    assert pays == [("2024-08-01", 100.0)], pays
    secilen = acik_ay_dagit(
        borc, 100, "2026-10-07",
        allowlist=["2023-01-01"],
        sozlesme_basi="2023-08-01",
    )
    assert secilen == [("2023-01-01", 100.0)], secilen


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
    test_867_bin_agustos()
    test_867_tam_acik_ve_uc_bin()
    test_vadesi_gelmis_kapaninca_avans()
    test_yil_listesi_867_etkisiz()
    test_secilen_ay_acik_tutardan()
    test_isaret_sonraki_aya_tasmaz()
    test_fatura_yedek_ve_reel()
    test_banka_yolu_gorunen_borc()
    test_tutarli_hesap_ayni_dagilim()
    test_tah_isaret_kaynagi_ve_cift_sayim()
    test_fatura_yedek_sozlesme_oncesi_yok()
    test_867_bes_bin_sozlesme_sonrasi()
    test_baslangic_tarihi_yoksa_eski_fifo()
    test_isaret_ve_allowlist_sozlesme_sinirini_gecer()
    print("CASE ay_fifo ok", date.today().isoformat())
