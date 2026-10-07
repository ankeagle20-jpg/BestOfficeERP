"""Aşama 3 ve gecikme sınıfı. Canlı veritabanına bağlanmaz."""
from asama3_ay import (
    BellekDepo,
    IslemReddi,
    aciklama_yenile,
    cli_yazma_reddi,
    gecikme_bakiye_uyarisi,
    geri_al,
    planla,
    uygula,
)
from ay_sinif import UYARI_METNI, gonderime_izin, sinif_hesapla


def _hesap(sinif="A", panel_aylik=300.0):
    acik = "Eski |AYLIK_TAH|2024-01-01| |AYLIK_PAY|2024-01-01=360.00|"
    return {
        "kod": "H01",
        "mid": 1,
        "sinif": sinif,
        "borc": {"2022-09-01": 300.0, "2022-10-01": 300.0},
        "rows": [{
            "id": 7,
            "tarih": "2024-03-05",
            "tutar": 360.0,
            "aciklama": acik,
            "payli": True,
        }],
        "panel": {"2022-09-01": {"aylik": panel_aylik, "tahsil": 0, "kalan": panel_aylik}},
        "cache": {"aylar": [
            {"yil": 2022, "ay": 9, "brut_tutar_kdv": 300.0, "tutar_kdv_dahil": 300.0, "odenen_tutar_kdv": 0, "kalan_tutar_kdv": 300.0},
            {"yil": 2022, "ay": 10, "brut_tutar_kdv": 300.0, "tutar_kdv_dahil": 300.0, "odenen_tutar_kdv": 0, "kalan_tutar_kdv": 300.0},
        ]},
    }


def _depo(h):
    d = BellekDepo()
    d.tahsilat[7] = {"id": 7, "mid": 1, "tarih": "2024-03-05", "tutar": 360.0, "aciklama": h["rows"][0]["aciklama"]}
    d.panel[1] = h["panel"]
    d.cache[1] = h["cache"]
    return d


def test_kuru_plan():
    plan = planla(_hesap()["rows"], _hesap()["borc"])
    assert plan[0]["yeni"] == [("2022-09-01", 300.0), ("2022-10-01", 60.0)], plan[0]["yeni"]
    assert plan[0]["tutar"] == 360.0
    assert "2024-01-01" not in plan[0]["aciklama_yeni"]
    sonuc = uygula(BellekDepo(), [_hesap()], yaz=False)
    assert sonuc["yazildi"] is False
    kayit = sonuc["denetim"][0]
    assert kayit["hesap_kodu"] == "H01"
    assert "aciklama" not in kayit
    assert kayit["yeni_toplam"] == 360.0


def test_yaz_ve_geri_al():
    h = _hesap()
    d = _depo(h)
    eski = d.tahsilat[7]["aciklama"]
    sonuc = uygula(d, [h], yaz=True, kim="test")
    assert sonuc["yazildi"] is True
    assert d.tahsilat[7]["tutar"] == 360.0
    assert "|AYLIK_PAY|2022-09-01=300.00|" in d.tahsilat[7]["aciklama"]
    assert "|AYLIK_PAY|2022-10-01=60.00|" in d.tahsilat[7]["aciklama"]
    eylul = d.cache[1]["aylar"][0]
    assert eylul["brut_tutar_kdv"] == 300.0
    assert eylul["tutar_kdv_dahil"] == 300.0
    assert eylul["odenen_tutar_kdv"] == 300.0
    assert eylul["kalan_tutar_kdv"] == 0.0
    assert d.panel[1]["2022-09-01"]["aylik"] == 300.0
    assert "aciklama" not in sonuc["denetim"][0]
    geri_al(d, sonuc["yedek"])
    assert d.tahsilat[7]["aciklama"] == eski
    assert d.cache[1]["aylar"][0]["odenen_tutar_kdv"] == 0
    assert d.panel[1]["2022-09-01"]["tahsil"] == 0


def test_hata_geri_alinir():
    h = _hesap()
    d = _depo(h)
    eski_ac = d.tahsilat[7]["aciklama"]
    eski_cache = d.cache[1]["aylar"][0]["odenen_tutar_kdv"]
    d.fail_at = 2
    try:
        uygula(d, [h], yaz=True)
        raised = False
    except RuntimeError:
        raised = True
    assert raised
    assert d.tahsilat[7]["aciklama"] == eski_ac
    assert d.cache[1]["aylar"][0]["odenen_tutar_kdv"] == eski_cache
    assert d.denetim == []


def test_bc_red():
    h = _hesap(sinif="B", panel_aylik=360.0)
    d = _depo(h)
    eski = d.tahsilat[7]["aciklama"]
    try:
        uygula(d, [h], yaz=True)
        raised = False
    except IslemReddi:
        raised = True
    assert raised
    assert d.writes == 0
    assert d.tahsilat[7]["aciklama"] == eski
    assert sinif_hesapla(h["borc"], {"2022-09-01": 300.0, "2022-10-01": 300.0}, {"2022-09-01": 360.0}, h["rows"]) == "B"
    assert sinif_hesapla({}, {}, {}, h["rows"]) == "C"


def test_gonderim_kapisi():
    assert gonderime_izin("A", False, False) == (True, "")
    assert gonderime_izin("B", False, False) == (False, "uyari")
    assert gonderime_izin("C", False, False) == (False, "uyari")
    assert gonderime_izin("B", True, False) == (True, "")
    assert gonderime_izin("A", True, True) == (False, "kilit")
    assert UYARI_METNI.startswith("Bu hesapların aylık tutarı")
    assert cli_yazma_reddi("block") is True
    assert cli_yazma_reddi("off") is False
    assert "360" not in aciklama_yenile("eski metin |AYLIK_PAY|2024-01-01=360.00|", [])


def test_sayfa_kapisi():
    from pathlib import Path
    html = Path("templates/giris/index.html").read_text(encoding="utf-8")
    assert UYARI_METNI in html
    assert "data-guvenilmez" in html
    assert "guvenilmez_onay" in html
    assert "data-guvenilmez') === '1'" in html
    route = Path("routes/whatsapp_routes.py").read_text(encoding="utf-8")
    assert "gonderime_izin" in route
    assert "siniflari_yukle" in route
    assert gecikme_bakiye_uyarisi(30000, 20000, 20000) is True
    assert gecikme_bakiye_uyarisi(20000, 20000, 20000) is False
    betik = Path("asama3_ay.py").read_text(encoding="utf-8")
    assert 'print("UYARI", "gecikme > bakiye"' in betik
    assert "gecikme > bakiye" not in route


if __name__ == "__main__":
    test_kuru_plan()
    test_yaz_ve_geri_al()
    test_hata_geri_alinir()
    test_bc_red()
    test_gonderim_kapisi()
    test_sayfa_kapisi()
    print("CASE asama3 ok")
