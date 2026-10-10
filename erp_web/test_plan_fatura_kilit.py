# -*- coding: utf-8 -*-
"""Plan değiştir: faturalı ay kilit sınıflandırması (K1/K2/K0/K3). Canlı veritabanına bağlanmaz."""
import os
from datetime import date

from plan_fatura_kilit import (
    K0,
    K1,
    K2,
    K3,
    ay_siniflari,
    gib_sinyali,
    kayit_gecerli,
    kayit_sinifla,
    kilit_listesi,
    serbest_liste,
)
from sozlesme_plan import ay_bazli_tutarlar
import sozlesme_plan_api as api
from test_plan_degistir import Tx, _app, _govde, _kur, _yanit

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _satir(**ek):
    r = {
        "id": 1,
        "musteri_id": 1,
        "durum": "odenmedi",
        "yon": "giden",
        "toplam": 1200.0,
        "notlar": "|AYLIK_TUTAR|2026-10-01|",
        "fatura_tarihi": date(2026, 9, 15),
        "fatura_no": "GIB2026000000001",
        "ettn": "",
    }
    r.update(ek)
    return r


def _sinif(rows, **kw):
    return (ay_siniflari(rows, **kw).get(1) or {})


def test_siniflandirici():
    ay = "2026-10"
    check("ettn dolu -> K1", _sinif([_satir(ettn="abc-1")])[ay]["sinif"] == K1)
    check("GIB imzalandi notu -> K1", _sinif([_satir(notlar="|AYLIK_TUTAR|2026-10-01| GİB İMZALANDI")])[ay]["sinif"] == K1)
    check("GIB durum imzali -> K1", _sinif([_satir(notlar="|AYLIK_TUTAR|2026-10-01| GİB durum: imzali")])[ay]["sinif"] == K1)
    check("GIB durum taslak -> K1", _sinif([_satir(notlar="|AYLIK_TUTAR|2026-10-01| GİB durum: taslak")])[ay]["sinif"] == K1)
    check("GIB ETTN etiketi + ettn bos -> K1", _sinif([_satir(notlar="|AYLIK_TUTAR|2026-10-01| GİB ETTN: 1234-abcd", ettn="")])[ay]["sinif"] == K1)
    check("sadece GIB bicimli numara -> K3", _sinif([_satir()])[ay]["sinif"] == K3)
    check(
        "AUTO_INV isaretli, GIBsiz -> K3",
        _sinif([_satir(notlar="|AUTO_INV|2026-10|", fatura_no="INV2026000001")])[ay]["sinif"] == K3,
    )
    check("GIB_NO_TASINDI gecersiz", kayit_sinifla(_satir(notlar="|AYLIK_TUTAR|2026-10-01||GIB_NO_TASINDI|x|")) is None)
    check("durum iptal gecersiz", kayit_sinifla(_satir(durum="iptal")) is None)
    check("durum taslak gecersiz", kayit_sinifla(_satir(durum="taslak")) is None)
    check("GIB durum iptal notu gecersiz", kayit_sinifla(_satir(notlar="|AYLIK_TUTAR|2026-10-01| GİB durum: iptal", ettn="x")) is None)
    check("ERP durum taslak (GIBsiz) gecersiz", kayit_sinifla(_satir(notlar="|AYLIK_TUTAR|2026-10-01| ERP durum: taslak")) is None)
    check(
        "ERP durum taslak ama ettn dolu -> K1",
        _sinif([_satir(notlar="|AYLIK_TUTAR|2026-10-01| ERP durum: taslak", ettn="x")])[ay]["sinif"] == K1,
    )
    check("gelen gecersiz", kayit_sinifla(_satir(yon="gelen")) is None)
    check("gecersiz kayit kilit uretmez", ay_siniflari([_satir(yon="gelen"), _satir(durum="iptal")]) == {})

    t = _sinif([_satir()], tahsil_ay={1: {ay: 500.0}})[ay]
    check("tahsilat ay haritasi -> K2", t["sinif"] == K2 and "tahsilat" in t["neden"], t)
    t = _sinif([_satir(id=7)], fid_tahsil={7: 100.0})[ay]
    check("fatura_id bagli tahsilat -> K2", t["sinif"] == K2, t)
    check("tahsilat sifir -> K3", _sinif([_satir()], tahsil_ay={1: {ay: 0.0}}, fid_tahsil={1: 0.0})[ay]["sinif"] == K3)
    check("K1 tahsilattan once gelir", _sinif([_satir(ettn="x")], tahsil_ay={1: {ay: 500.0}})[ay]["sinif"] == K1)
    check("baska ayin tahsilati bu ayi etkilemez", _sinif([_satir()], tahsil_ay={1: {"2026-09": 900.0}})[ay]["sinif"] == K3)

    cok = _sinif([_satir(id=1), _satir(id=2, notlar="|AYLIK_TUTAR|2026-10-01|")])[ay]
    check("ayni ayda birden cok kayit -> K0", cok["sinif"] == K0 and cok["kayit_sayisi"] == 2 and cok["fatura_tutari"] == 2400.0, cok)
    k1_cok = _sinif([_satir(id=1, ettn="x"), _satir(id=2)])[ay]
    check("ayni ay K1 + K3 -> K1", k1_cok["sinif"] == K1)
    cok_ay = _sinif([_satir(notlar="|AYLIK_TUTAR|2026-10-01||AYLIK_TUTAR|2026-11-01|")])
    check("cok aylı isaret -> K0 her iki ay", cok_ay["2026-10"]["sinif"] == K0 and cok_ay["2026-11"]["sinif"] == K0, cok_ay)
    isaretsiz = _sinif([_satir(notlar="", fatura_tarihi=date(2026, 11, 20))])["2026-11"]
    check("isaretsiz GIBsiz -> K0", isaretsiz["sinif"] == K0 and isaretsiz["kaynak"] == "fatura_tarihi", isaretsiz)
    check(
        "isaretsiz ama GIB imzali -> K1",
        _sinif([_satir(notlar="GİB İMZALANDI", fatura_tarihi=date(2026, 11, 20))])["2026-11"]["sinif"] == K1,
    )
    bil = _sinif([_satir()], tahsil_bilinmiyor={1})[ay]
    check("tahsilat okunamadi -> K0", bil["sinif"] == K0 and "okunamadı" in bil["neden"], bil)
    check("tahsilat okunamadi K1'i bozmaz", _sinif([_satir(ettn="x")], tahsil_bilinmiyor={1})[ay]["sinif"] == K1)

    # ettn sütunu olmayan şema: satırlarda ettn anahtarı yok; notlara göre karar, K0'a düşmez
    ettnsiz = dict(_satir())
    ettnsiz.pop("ettn")
    check("ettn anahtari yok: isaretli -> K3 (K0 degil)", _sinif([ettnsiz])[ay]["sinif"] == K3)
    ettnsiz2 = dict(_satir(notlar="|AYLIK_TUTAR|2026-10-01| GİB İMZALANDI"))
    ettnsiz2.pop("ettn")
    check("ettn anahtari yok: imza notu -> K1", _sinif([ettnsiz2])[ay]["sinif"] == K1)
    check("gib_sinyali temiz satir None", gib_sinyali(_satir()) is None and kayit_gecerli(_satir()))

    sin = _sinif([_satir(ettn="x"), _satir(id=2, notlar="|AYLIK_TUTAR|2026-11-01|"), _satir(id=3, notlar="|AYLIK_TUTAR|2026-12-01|", toplam=900)])
    check(
        "kilit/serbest listeleri",
        [k["ay"] for k in kilit_listesi(sin)] == ["2026-10"] and [k["ay"] for k in serbest_liste(sin)] == ["2026-11", "2026-12"],
        sin,
    )


def test_plan_zinciri_867_benzeri():
    """11 ay K3 (2026-10..2027-08): plan 2026-10'dan itibaren uygulanır, önceki K1 aylar sabit."""
    from routes.giris_routes import _plan_fatura_aylari, _plan_fatura_kilit_listesi

    rows = []
    for i, ay in enumerate(["2026-08", "2026-09"]):
        rows.append(_satir(id=100 + i, notlar=f"|AYLIK_TUTAR|{ay}-01|", ettn="e-" + ay, toplam=1818.0))
    k3_aylar = [f"2026-{m:02d}" for m in (10, 11, 12)] + [f"2027-{m:02d}" for m in range(1, 9)]
    for i, ay in enumerate(k3_aylar):
        rows.append(_satir(id=200 + i, notlar=f"|AYLIK_TUTAR|{ay}-01|", toplam=900.0 if i < 3 else 2399.62))
    faturali, belge = _plan_fatura_aylari(rows)
    check("867 benzeri: kilitli yalniz K1 aylar", sorted(faturali.get(1) or []) == ["2026-08", "2026-09"], faturali)
    check("867 benzeri: kilit listesi sinif K1", [k["sinif"] for k in _plan_fatura_kilit_listesi(rows)[1]] == [K1, K1])

    zincir = {
        "sozlesme_tarihi": date(2023, 8, 1),
        "artis_tarihi": date(2023, 8, 1),
        "ay_sayisi": 60,
        "aylik_net": 1000,
        "kdv_oran": 20,
        "kira_nakit": False,
        "tufe": {2024: {8: 10}, 2025: {8: 5}, 2026: {8: 5}, 2027: {8: 10}},
        "faturali_aylar": list(faturali[1]),
        "fatura_belge": dict(belge[1]),
    }
    plan = {"gecerlilik_ay": date(2026, 10, 1), "yeni_net": 1000, "kdv_oran": 20, "yeni_brut": 1200}
    eski = {s["ay"]: s["brut"] for s in ay_bazli_tutarlar(zincir, [])}
    yeni = {s["ay"]: s["brut"] for s in ay_bazli_tutarlar(zincir, [plan])}
    check("867 benzeri: 2026-10..2027-07 brut 1200", all(yeni[a] == 1200 for a in k3_aylar[:-1]), [yeni[a] for a in k3_aylar])
    check("867 benzeri: 2027-08 yildonumu yeni netten (1000*1.10*1.2)", yeni["2027-08"] == 1320, yeni["2027-08"])
    check("867 benzeri: plandan once aylar degismez", all(yeni[a] == eski[a] for a in ("2026-08", "2026-09", "2026-07")))
    eski_kilit = dict(zincir, faturali_aylar=["2026-08", "2026-09"] + k3_aylar)
    yeni_eski_kilit = {s["ay"]: s["brut"] for s in ay_bazli_tutarlar(eski_kilit, [plan])}
    check("eski kilit: K3 aylar planlanmazdi (karsilastirma)", yeni_eski_kilit["2026-10"] != 1200, yeni_eski_kilit["2026-10"])


def test_onizleme_ve_kayit_api():
    app = _app()
    tx = Tx()
    kilit_k1_once = {"ay": "2024-05", "fatura_tutari": 1200.0, "kaynak": "isaret", "sinif": K1, "neden": "ettn dolu"}
    k3 = [
        {"ay": "2024-10", "fatura_tutari": 900.0, "kaynak": "isaret", "sinif": K3, "neden": "x"},
        {"ay": "2024-11", "fatura_tutari": 900.0, "kaynak": "isaret", "sinif": K3, "neden": "x"},
        {"ay": "2025-08", "fatura_tutari": 2399.62, "kaynak": "isaret", "sinif": K3, "neden": "x"},
    ]
    try:
        _kur(tx, kilit=[kilit_k1_once])
        api._kilit_k3_aylari = lambda mid: list(k3)
        api._resync = lambda mid: True
        with app.test_request_context(json=_govde(), method="POST"):
            g, kod = _yanit(api.plan_onizleme_yanit(7))
        kl = g.get("kilit_listeleri") or {}
        k3g = kl.get("K3_guncellenecek_aylar") or []
        check("onizleme: yalniz gecerlilik oncesi K1 -> kilit kutusu/onay yok", kod == 200 and g.get("kilitlenen_aylar") == [] and kl.get("K1") == [] and g.get("faturali") is False, g)
        check(
            "onizleme: K3 listesi gecerlilik ayindan itibaren, eski->yeni brut",
            [r["ay"] for r in k3g] == ["2024-11", "2025-08"]
            and k3g[0]["fatura_tutari"] == 900.0
            and k3g[0]["eski_zincir_brut"] == 1320
            and k3g[0]["zincir_brut"] == 2400
            and k3g[1]["fatura_tutari"] == 2399.62,
            k3g,
        )
        check(
            "onizleme: bilgi satiri",
            g.get("k3_bilgi")
            == "ERP içi borç kayıtlarındaki tutarlar (örn. 900, 2.399,62) bu aşamada güncellenmez; "
            "aylık grid, borçlandırma/tahsilat paneli ve ekstre yeni plana göre görünür.",
            g.get("k3_bilgi"),
        )
        check("onizleme: gorunur degisiklik var (K3 kilitsiz)", g.get("gorunur_degisiklik") is True)

        # Kayıt: yalnız K3 -> kilit onayı istenmez
        with app.test_request_context(json=_govde(), method="POST"):
            g2, kod2 = _yanit(api.plan_ekle_yanit(7))
        check("kayit: yalniz K3 + eski K1 -> onaysiz kaydedilir", kod2 == 200 and g2.get("ok") is True and tx.commit == 1, (kod2, g2))

        # K1/K2/K0 geçerlilik ayından sonra: kutu + onay
        tx2 = Tx()
        kilitli = [
            {"ay": "2024-12", "fatura_tutari": 1200.0, "kaynak": "isaret", "sinif": K1, "neden": "ettn dolu"},
            {"ay": "2025-01", "fatura_tutari": 1200.0, "kaynak": "isaret", "sinif": K2, "neden": "tahsilat var (ay haritası)"},
            {"ay": "2025-02", "fatura_tutari": 1200.0, "kaynak": "fatura_tarihi", "sinif": K0, "neden": "işaretsiz kayıt"},
        ]
        _kur(tx2, kilit=kilitli)
        api._kilit_k3_aylari = lambda mid: list(k3)
        api._resync = lambda mid: True
        with app.test_request_context(json=_govde(), method="POST"):
            g3, kod3 = _yanit(api.plan_onizleme_yanit(7))
        kl3 = g3.get("kilit_listeleri") or {}
        check(
            "onizleme: K1/K2/K0 ayri listeler + nedenli kilit kutusu",
            kod3 == 200
            and [r["ay"] for r in kl3.get("K1", [])] == ["2024-12"]
            and [r["ay"] for r in kl3.get("K2", [])] == ["2025-01"]
            and [r["ay"] for r in kl3.get("K0", [])] == ["2025-02"]
            and [k.get("sinif") for k in g3.get("kilitlenen_aylar", [])] == [K1, K2, K0]
            and all(k.get("neden") for k in g3.get("kilitlenen_aylar", []))
            and kl3["K2"][0]["neden"] == "tahsilat var (ay haritası)",
            g3,
        )
        with app.test_request_context(json=_govde(), method="POST"):
            g4, kod4 = _yanit(api.plan_ekle_yanit(7))
        check("kayit: K1/K2/K0 varsa onaysiz 400", kod4 == 400 and g4.get("kilitlenen_aylar") and tx2.commit == 0, (kod4, g4))
        with app.test_request_context(json=_govde(onay_kilitli_aylar=True), method="POST"):
            g5, kod5 = _yanit(api.plan_ekle_yanit(7))
        check("kayit: onayla kaydedilir", kod5 == 200 and tx2.commit == 1, (kod5, g5))

        # Kilit sınıfları okunamazsa kilitsiz devam edilmez
        tx3 = Tx()
        _kur(tx3)
        api._resync = lambda mid: True

        def patla(mid):
            raise api.KilitOkunamadi("x")

        api._kilitler = patla
        with app.test_request_context(json=_govde(), method="POST"):
            g6, kod6 = _yanit(api.plan_onizleme_yanit(7))
        with app.test_request_context(json=_govde(), method="POST"):
            g7, kod7 = _yanit(api.plan_ekle_yanit(7))
        check("kilit okunamazsa onizleme/kayit 500, yazma yok", kod6 == 500 and kod7 == 500 and tx3.commit == 0, (kod6, kod7))
    finally:
        os.environ.pop("PLAN_DEGISTIR_ENABLED", None)
        os.environ.pop("PLAN_DEGISTIR_MUSTERI_IDS", None)


def test_plansiz_cikti_ayni():
    """Plansız kartta faturalı ay listesi kullanılmaz: sınıflandırma çıktıyı değiştiremez."""
    zincir = {
        "sozlesme_tarihi": date(2023, 8, 1),
        "artis_tarihi": date(2023, 8, 1),
        "ay_sayisi": 36,
        "aylik_net": 1000,
        "kdv_oran": 20,
        "kira_nakit": False,
        "tufe": {2024: {8: 10}, 2025: {8: 5}},
    }
    bos = ay_bazli_tutarlar(zincir, [])
    tum = ay_bazli_tutarlar(dict(zincir, faturali_aylar=[f"2024-{m:02d}" for m in range(1, 13)]), [])
    kilitsiz = ay_bazli_tutarlar(dict(zincir, faturali_aylar=[]), [])
    brut = lambda l: [(s["ay"], s["brut"], s["net"]) for s in l]
    check("plansiz: faturali liste ne olursa olsun cikti ayni", brut(bos) == brut(tum) == brut(kilitsiz))


if __name__ == "__main__":
    test_siniflandirici()
    test_plan_zinciri_867_benzeri()
    test_onizleme_ve_kayit_api()
    test_plansiz_cikti_ayni()
    if FAILS:
        print("FAIL", len(FAILS), FAILS)
        raise SystemExit(1)
    print("plan_fatura_kilit ok")
