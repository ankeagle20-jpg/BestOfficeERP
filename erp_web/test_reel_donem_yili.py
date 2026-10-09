# -*- coding: utf-8 -*-
"""Reel dönem yılı (artış ayına göre) ile kapanış tutarı seçimi. Canlı veritabanına bağlanmaz.

Hata: marker'lı kısmi ödeme aylarında kalan, takvim yılı anahtarıyla seçilen reel tutardan hesaplanıyordu
(artış ayından önceki aylar bir sonraki dönemin tutarını alıyordu). 867 / 2025-07: 1454,71 − 964,02 = 490,69,
doğrusu 1094,18 − 964,02 = 130,16.
"""
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FAILS = []


def check(name, ok):
    print(("ok " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def _kyc(sozlesme, artis, **ek):
    row = {
        "sozlesme_tarihi": sozlesme,
        "sozlesme_bitis": date(2030, 12, 1),
        "kira_artis_tarihi": artis,
        "aylik_kira": 1000,
        "kdv_oran": 20,
        "kira_nakit": False,
        "durum": "aktif",
    }
    row.update(ek)
    return row


def _hucre(payload, yil, ay):
    for a in payload["aylar"]:
        if int(a["yil"]) == yil and int(a["ay"]) == ay:
            return a
    raise KeyError((yil, ay))


def _satirlar(*ciftler):
    """(iso, tutar) -> |AYLIK_PAY| (bilinçli kısmi) marker tahsilat satırları."""
    out = []
    for iso, tutar in ciftler:
        out.append({
            "tutar": tutar,
            "aciklama": "Tahsilat H|AYLIK_PAY|" + iso + "=" + str(tutar) + "|",
            "tahsilat_tarihi": date(int(iso[:4]), int(iso[5:7]), 15),
            "fatura_tarihi": None,
        })
    return out


def _kur(gr, satirlar):
    eski = {}
    def al(ad, deger):
        eski[ad] = getattr(gr, ad)
        setattr(gr, ad, deger)
    tm = {}
    for s in satirlar:
        iso = s["aciklama"].split("|AYLIK_PAY|")[1][:7] + "-01"
        tm[iso] = round(tm.get(iso, 0) + s["tutar"], 2)
    al("_aylik_tahsil_tutar_map", lambda mid, **k: dict(tm))
    al("_ekstre_tahsil_rows_for_musteri", lambda mid: satirlar)
    al("_aylik_grid_acik_tutar_ay_keys_normalized", lambda mid: set())
    al("_aylik_tahsil_cache_imza", lambda mid: "x")
    return eski


def _geri(gr, eski):
    for ad, v in eski.items():
        setattr(gr, ad, v)


def _uret(gr, kyc, tufe, manual, satirlar):
    eski = _kur(gr, satirlar)
    try:
        return gr._build_aylik_grid_cache_payload(
            1, tufe_map=tufe, kyc_row=kyc, manual_reel_by_year=manual,
            planlar=[], faturali_aylar=[], fatura_belge={},
        )
    finally:
        _geri(gr, eski)


def test_yardimci():
    from routes.giris_routes import _reel_donem_yili_for_ay as f

    check("agustos: temmuz onceki donem", f(2025, 7, 8) == 2024)
    check("agustos: agustos kendi donemi", f(2025, 8, 8) == 2025)
    check("agustos: ocak onceki donem", f(2025, 1, 8) == 2024)
    check("agustos: aralik kendi donemi", f(2025, 12, 8) == 2025)
    check("ocak: tum yil kendi donemi", all(f(2025, m, 1) == 2025 for m in range(1, 13)))
    check("aralik: kasim onceki, aralik kendi", f(2025, 11, 12) == 2024 and f(2025, 12, 12) == 2025 and f(2025, 1, 12) == 2024)
    check("artis ay gecersiz: takvim yili", f(2025, 7, None) == 2025 and f(2025, 7, 0) == 2025 and f(2025, 7, "x") == 2025 and f(2025, 7, 13) == 2025)
    check("ay gecersiz: takvim yili", f(2025, None, 8) == 2025 and f(2025, 13, 8) == 2025)
    check("yil gecersiz", f(None, 7, 8) == 0)


def test_867_benzeri_agustos():
    from routes import giris_routes as gr

    tufe = {2024: {8: 10.0}, 2025: {8: 5.0}}
    manual = {2023: 1200.0, 2024: 1094.18, 2025: 1454.71, 2026: 1918.76}
    sat = _satirlar(("2025-07-01", 964.02), ("2025-08-01", 500.0), ("2024-03-01", 500.0), ("2026-02-01", 1000.0))
    p = _uret(gr, _kyc(date(2023, 8, 1), date(2023, 8, 1)), tufe, manual, sat)
    c = _hucre(p, 2025, 7)
    check("867 2025-07 brut", c["brut_tutar_kdv"] == 1094.18)
    check("867 2025-07 kalan 130,16", c["kalan_tutar_kdv"] == 130.16 and c["odenen_tutar_kdv"] == 964.02 and c["kismi_tahsilat"] is True)
    c = _hucre(p, 2025, 8)
    check("agustos 2025 yeni donem: 1454,71 - 500", c["brut_tutar_kdv"] == 1454.71 and c["kalan_tutar_kdv"] == 954.71)
    c = _hucre(p, 2026, 2)
    check("ocak-temmuz 2026 (2025 donemi): 1454,71 - 1000", c["kalan_tutar_kdv"] == 454.71)
    c = _hucre(p, 2024, 3)
    check("ilk donem Ocak-Temmuz 2024 (2023 donemi, kendi brut)", c["brut_tutar_kdv"] == 1200.0 and c["kalan_tutar_kdv"] == 700.0)
    # tam ödenmiş ay hâlâ kalan 0
    p2 = _uret(gr, _kyc(date(2023, 8, 1), date(2023, 8, 1)), tufe, manual, _satirlar(("2025-07-01", 1094.18)))
    c = _hucre(p2, 2025, 7)
    check("tam odeme kalan 0", c["kalan_tutar_kdv"] == 0.0 and c["tahsil_edildi"] is True)


def test_ocak_ve_aralik_siniri():
    from routes import giris_routes as gr

    tufe = {2024: {1: 10.0, 12: 10.0}, 2025: {1: 5.0, 12: 5.0}}
    # Artış Ocak: takvim yılı = dönem yılı; sonuç eski davranışla aynı.
    manual_o = {2023: 1200.0, 2024: 1320.0, 2025: 1386.0, 2026: 1500.0}
    p = _uret(gr, _kyc(date(2023, 1, 1), date(2023, 1, 1)), tufe, manual_o, _satirlar(("2025-01-01", 1000.0), ("2025-12-01", 1000.0)))
    c1, c12 = _hucre(p, 2025, 1), _hucre(p, 2025, 12)
    check("ocak karti: 2025-01 1386-1000", c1["brut_tutar_kdv"] == 1386.0 and c1["kalan_tutar_kdv"] == 386.0)
    check("ocak karti: 2025-12 1386-1000", c12["brut_tutar_kdv"] == 1386.0 and c12["kalan_tutar_kdv"] == 386.0)
    # Artış Aralık: 2025-11 önceki dönem (2024), 2025-12 yeni dönem (2025).
    manual_a = {2023: 1200.0, 2024: 1320.0, 2025: 1386.0, 2026: 1500.0}
    p = _uret(gr, _kyc(date(2023, 12, 1), date(2023, 12, 1)), tufe, manual_a, _satirlar(("2025-11-01", 1000.0), ("2025-12-01", 1000.0), ("2025-01-01", 1000.0)))
    c11, c12, c01 = _hucre(p, 2025, 11), _hucre(p, 2025, 12), _hucre(p, 2025, 1)
    check("aralik karti: 2025-11 donem 2024", c11["brut_tutar_kdv"] == 1320.0 and c11["kalan_tutar_kdv"] == 320.0)
    check("aralik karti: 2025-12 donem 2025", c12["brut_tutar_kdv"] == 1386.0 and c12["kalan_tutar_kdv"] == 386.0)
    check("aralik karti: 2025-01 donem 2024", c01["brut_tutar_kdv"] == 1320.0 and c01["kalan_tutar_kdv"] == 320.0)


def test_baslangic_mart_artis_agustos():
    """Başlangıç ayı ≠ artış ayı: kapanış tutarı (kap_b) brütle aynı reel pencereyi seçmeli."""
    from routes import giris_routes as gr

    tufe = {2024: {8: 10.0}, 2025: {8: 5.0}}
    manual = {2023: 1200.0, 2024: 1094.18, 2025: 1454.71, 2026: 1918.76}
    ay_listesi = []
    y, m = 2023, 3
    while (y, m) <= (2027, 2):
        ay_listesi.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    sat = _satirlar(*[("%04d-%02d-01" % (yy, mm), 300.0) for yy, mm in ay_listesi])
    p = _uret(gr, _kyc(date(2023, 3, 1), date(2023, 8, 1)), tufe, manual, sat)
    uyumsuz = []
    reel_aylari = 0
    for yy, mm in ay_listesi:
        if (yy, mm) < (2024, 8):
            # İlk reel dönem (başlangıç yılı anahtarı overlay'de atlanır, brüt Mart'tan sayılan grid hesabından gelir):
            # bilinen, önceden var olan tutarsızlık — bu test kapsamı dışı (rapora yazıldı).
            continue
        c = _hucre(p, yy, mm)
        brut = float(c["brut_tutar_kdv"])
        if brut <= 0.01:
            continue
        beklenen = round(max(brut - 300.0, 0), 2)
        if abs(float(c["kalan_tutar_kdv"]) - beklenen) > 0.005:
            uyumsuz.append((yy, mm, brut, c["kalan_tutar_kdv"], beklenen))
        reel_aylari += 1
    check("mart/agustos karti: ilk artistan sonra kalan = brut - odenen her ayda", not uyumsuz and reel_aylari > 20)
    if uyumsuz:
        print("   uyumsuz:", uyumsuz[:6])
    # Pencere sınırı: 2025-07 → 2024 dönemi, 2025-08 → 2025 dönemi
    c7, c8 = _hucre(p, 2025, 7), _hucre(p, 2025, 8)
    check("mart/agustos karti sinir ayi brutleri", c7["brut_tutar_kdv"] == 1094.18 and c8["brut_tutar_kdv"] == 1454.71)
    check("mart/agustos karti sinir ayi kalanlari", c7["kalan_tutar_kdv"] == 794.18 and c8["kalan_tutar_kdv"] == 1154.71)
    # Başlangıç yılı Mart–Temmuz (artıştan önce, dönem 2022 tanımsız): brüt ile devam
    c = _hucre(p, 2023, 5)
    check("mart/agustos karti ilk yil: kalan brutten", c["kalan_tutar_kdv"] == round(c["brut_tutar_kdv"] - 300.0, 2))


def test_zenginlestir_ayni_kural():
    from routes import giris_routes as gr

    manual = {2024: 1094.18, 2025: 1454.71}
    payload = {
        "musteri_id": 1,
        "artis_ay": 8,
        "aylar": [
            {"yil": 2025, "ay": 7, "brut_tutar_kdv": 1094.18, "tutar_kdv_dahil": 1094.18, "odenen_tutar_kdv": 0.0},
            {"yil": 2025, "ay": 8, "brut_tutar_kdv": 1454.71, "tutar_kdv_dahil": 1454.71, "odenen_tutar_kdv": 0.0},
        ],
    }
    sat = _satirlar(("2025-07-01", 964.02), ("2025-08-01", 500.0))
    tm = {"2025-07-01": 964.02, "2025-08-01": 500.0}
    batch = gr._ekstre_tahsil_batch_maps_from_rows(sat)
    gr._ekstre_payload_odenen_zenginlestir(payload, tm, batch, manual_reel_by_year=manual)
    c7, c8 = payload["aylar"]
    check("zenginlestir 2025-07 kalan 130,16", c7["kalan_tutar_kdv"] == 130.16)
    check("zenginlestir 2025-08 kalan 954,71", c8["kalan_tutar_kdv"] == 954.71)
    # artis_ay yoksa (eski cache): takvim yili davranisi korunur
    payload2 = {"musteri_id": 1, "aylar": [dict(payload["aylar"][0], odenen_tutar_kdv=0.0)]}
    gr._ekstre_payload_odenen_zenginlestir(payload2, tm, batch, manual_reel_by_year=manual)
    check("artis_ay yoksa eski davranis", payload2["aylar"][0]["kalan_tutar_kdv"] == 490.69)


def test_panel_kalan():
    from routes import giris_routes as gr

    tufe = {2024: {8: 10.0}, 2025: {8: 5.0}}
    manual = {2023: 1200.0, 2024: 1094.18, 2025: 1454.71}
    sat = _satirlar(("2025-07-01", 964.02))
    p = _uret(gr, _kyc(date(2023, 8, 1), date(2023, 8, 1)), tufe, manual, sat)
    eski = _kur(gr, sat)
    try:
        by = gr._panel_by_iso_from_tahsil_map(1, p, tahsilat_tarihi=None, trust_grid_odenen=False)
    finally:
        _geri(gr, eski)
    b = by.get("2025-07-01") or {}
    check("panel 2025-07 kalan 130,16", float(b.get("kalan") or 0) == 130.16 and float(b.get("aylik") or 0) == 1094.18)


def main():
    import sys

    sys.path.insert(0, str(ROOT))
    for ad, fn in list(globals().items()):
        if ad.startswith("test_") and callable(fn):
            print("==", ad)
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                check(ad + " istisna " + type(exc).__name__ + ": " + str(exc)[:120], False)
    print("SONUC", "TUMU OK" if not FAILS else f"{len(FAILS)} HATA: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
