# -*- coding: utf-8 -*-
"""GİB planlı K3 AYLIK tutarı. Canlı DB'ye yazmaz/bağlanmaz."""
from datetime import date
from pathlib import Path

from gib_earsiv import build_fatura_data_from_db
from gib_plan_tutar import (
    aylik_tutar_ayi,
    kart_disi_neden,
    plan_gib_override,
    uygula_gib_basari_tutar,
)
from sozlesme_plan import ay_bazli_tutarlar

ROOT = Path(__file__).resolve().parent
FAILS = []


def check(name, ok):
    print(("ok " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


KYC_BANKA = {
    "aylik_kira": 600,
    "kdv_oran": 20,
    "kira_nakit": False,
    "hizmet_turu": "Sanal Ofis",
    "kira_nakit_tutar": None,
    "kira_banka_tutar": 600,
}

PLAN_10 = [{"gecerlilik_ay": date(2025, 6, 1), "yeni_net": 500, "kdv_oran": 20, "yeni_brut": 600}]


def _aylik_fatura(fid, ay, toplam=900.0, notlar=None, **ek):
    row = {
        "id": fid,
        "musteri_id": 867,
        "musteri_adi": "Test 867",
        "fatura_no": "KIRA%d" % fid,
        "fatura_tarihi": date(int(ay[:4]), int(ay[5:7]), 1),
        "toplam": toplam,
        "tutar": round(toplam / 1.2, 2),
        "kdv_tutar": round(toplam - round(toplam / 1.2, 2), 2),
        "satirlar_json": "",
        "notlar": notlar or ("%s kira |AYLIK_TUTAR|%s-01|" % (ay, ay)),
        "ettn": "",
        "durum": "odenmedi",
        "yon": "giden",
    }
    row.update(ek)
    return row


def _fetch(fatura, kyc=None, cust=None):
    kyc = dict(KYC_BANKA if kyc is None else kyc)
    cust = cust or {"id": 867, "name": "Test 867", "address": "A", "tax_number": "1111111111", "phone": "", "email": ""}

    def fo(sql, params):
        s = " ".join(sql.split()).lower()
        if "from faturalar" in s:
            return dict(fatura)
        if "from customers" in s:
            return dict(cust)
        if "from musteri_kyc" in s:
            return dict(kyc)
        return None

    return fo


def _baglam(ay_sinif, net, brut, kdv, kaynak="plan_tufe"):
    return {
        "ay_sinif": ay_sinif,
        "planlar": PLAN_10,
        "plan_satir": {"net": net, "brut": brut, "kdv": kdv, "kaynak": kaynak},
    }


def _alanlar(fd):
    it = (fd.get("items") or [{}])[0]
    return {
        "birim_fiyat": round(float(fd.get("birim_fiyat") or 0), 2),
        "toplam": round(float(fd.get("toplam") or 0), 2),
        "kdv": int(fd.get("kdv_orani") or 0),
        "unit_price": round(float(it.get("unit_price") or 0), 2),
        "tax_rate": int(it.get("tax_rate") or 0),
        "name": it.get("name"),
    }


def test_k3_plan_tutarlari():
    ornekler = [
        ("2026-02", 900.0, 664.75, 797.70, 132.95),
        ("2026-08", 900.0, 876.81, 1052.17, 175.36),
        ("2027-08", 2399.62, 1156.51, 1387.81, 231.30),
    ]
    for ay, eski, net, brut, kdv in ornekler:
        f = _aylik_fatura(1965, ay, eski)
        fd = build_fatura_data_from_db(f["id"], _fetch(f), plan_baglam=_baglam("K3", net, brut, kdv))
        a = _alanlar(fd)
        check(
            "K3 %s plan" % ay,
            a["birim_fiyat"] == net
            and a["unit_price"] == net
            and a["toplam"] == brut
            and a["kdv"] == 20
            and a["tax_rate"] == 20
            and abs(net + kdv - brut) < 0.02,
        )
        check("K3 %s header != eski faturalar.toplam" % ay, a["toplam"] != eski)


def test_kilitli_eski():
    for ay, sinif, eski in (("2025-06", "K2", 600.0), ("2025-07", "K2", 600.0), ("2026-03", "K0", 900.0), ("2026-04", "K0", 900.0)):
        f = _aylik_fatura(1999, ay, eski)
        fd = build_fatura_data_from_db(
            f["id"], _fetch(f), plan_baglam=_baglam(sinif, 664.75, 797.70, 132.95)
        )
        a = _alanlar(fd)
        check(
            "%s %s eski KYC" % (ay, sinif),
            a["birim_fiyat"] == 600.0 and a["unit_price"] == 600.0 and a["toplam"] == eski and a["kdv"] == 20,
        )


def test_kapsam_disi_degismez():
    f = _aylik_fatura(10, "2026-02", 900.0)
    eski = _alanlar(build_fatura_data_from_db(f["id"], _fetch(f), plan_baglam={"atla": True}))
    check("eski fallback KYC 600 / header 900", eski["birim_fiyat"] == 600.0 and eski["toplam"] == 900.0)

    plansiz = _alanlar(build_fatura_data_from_db(f["id"], _fetch(f), plan_baglam={"ay_sinif": "K3", "planlar": [], "plan_satir": {"net": 664.75, "brut": 797.70, "kdv": 132.95, "kaynak": "plan_tufe"}}))
    check("plansiz = eski", plansiz == eski)

    nakit_kyc = dict(KYC_BANKA, kira_nakit=True)
    nakit = _alanlar(build_fatura_data_from_db(f["id"], _fetch(f, nakit_kyc), plan_baglam=_baglam("K3", 664.75, 797.70, 132.95)))
    nakit_eski = _alanlar(build_fatura_data_from_db(f["id"], _fetch(f, nakit_kyc), plan_baglam={"atla": True}))
    check("nakit = eski", nakit == nakit_eski)

    hibrit_kyc = dict(KYC_BANKA, kira_nakit_tutar=200, kira_banka_tutar=400, aylik_kira=600)
    hibrit = _alanlar(build_fatura_data_from_db(f["id"], _fetch(f, hibrit_kyc), plan_baglam=_baglam("K3", 664.75, 797.70, 132.95)))
    hibrit_eski = _alanlar(build_fatura_data_from_db(f["id"], _fetch(f, hibrit_kyc), plan_baglam={"atla": True}))
    check("hibrit = eski", hibrit == hibrit_eski)

    kdv8_kyc = dict(KYC_BANKA, kdv_oran=8)
    kdv8 = _alanlar(build_fatura_data_from_db(f["id"], _fetch(f, kdv8_kyc), plan_baglam=_baglam("K3", 664.75, 797.70, 132.95)))
    kdv8_eski = _alanlar(build_fatura_data_from_db(f["id"], _fetch(f, kdv8_kyc), plan_baglam={"atla": True}))
    check("kdv!=20 = eski", kdv8 == kdv8_eski)

    auto = _aylik_fatura(140, "2026-03", 600.0, notlar="AUTO |AUTO_INV|2026-03|")
    auto_fd = _alanlar(build_fatura_data_from_db(auto["id"], _fetch(auto), plan_baglam=_baglam("K3", 664.75, 797.70, 132.95)))
    auto_eski = _alanlar(build_fatura_data_from_db(auto["id"], _fetch(auto), plan_baglam={"atla": True}))
    check("AUTO_INV = eski", auto_fd == auto_eski)

    dolu = _aylik_fatura(11, "2026-02", 900.0)
    dolu["satirlar_json"] = '[{"ad":"Hizmet","miktar":1,"birim_fiyat":750,"kdv_orani":20}]'
    dolu_fd = _alanlar(build_fatura_data_from_db(dolu["id"], _fetch(dolu), plan_baglam=_baglam("K3", 664.75, 797.70, 132.95)))
    dolu_eski = _alanlar(build_fatura_data_from_db(dolu["id"], _fetch(dolu), plan_baglam={"atla": True}))
    check("satirlar_json dolu = eski", dolu_fd == dolu_eski and dolu_fd["unit_price"] == 750.0)


def test_saf_kurallar():
    check("aylik ay", aylik_tutar_ayi("|AYLIK_TUTAR|2026-02-01|") == "2026-02")
    check("auto yok", aylik_tutar_ayi("|AUTO_INV|2026-03|") is None)
    check("nakit", kart_disi_neden({"kira_nakit": True, "aylik_kira": 600, "kdv_oran": 20}) == "nakit")
    check("hibrit", kart_disi_neden({"kira_nakit": False, "aylik_kira": 600, "kira_nakit_tutar": 200, "kira_banka_tutar": 400, "kdv_oran": 20}) == "hibrit")
    check("kdv", kart_disi_neden({"kira_nakit": False, "aylik_kira": 600, "kdv_oran": 8}) == "kdv")
    check("banka uygun", kart_disi_neden(KYC_BANKA) is None)
    ov = plan_gib_override(
        _aylik_fatura(1, "2026-02"),
        KYC_BANKA,
        ay_sinif="K3",
        plan_satir={"net": 664.75, "brut": 797.70, "kdv": 132.95, "kaynak": "plan_tufe"},
        planlar=PLAN_10,
    )
    check("override K3", ov and ov["birim_fiyat"] == 664.75 and ov["toplam"] == 797.70)
    yok = plan_gib_override(
        _aylik_fatura(1, "2025-06"),
        KYC_BANKA,
        ay_sinif="K2",
        plan_satir={"net": 500, "brut": 600, "kdv": 100, "kaynak": "plan"},
        planlar=PLAN_10,
    )
    check("K2 override yok", yok is None)


def test_ay_bazli_tutarlar_2026_02():
    zincir = {
        "sozlesme_tarihi": date(2023, 8, 1),
        "artis_tarihi": date(2023, 8, 1),
        "ay_sayisi": 48,
        "aylik_net": 600,
        "kdv_oran": 20,
        "kira_nakit": False,
        "tufe": {2025: {8: 32.95}},
    }
    satirlar = ay_bazli_tutarlar(zincir, PLAN_10)
    hedef = None
    for s in satirlar:
        if s["ay"] == "2026-02":
            hedef = s
            break
    check(
        "ay_bazli 2026-02 plan_tufe 797.70",
        hedef is not None
        and hedef["kaynak"] == "plan_tufe"
        and hedef["net"] == 664.75
        and hedef["brut"] == 797.70
        and abs(hedef["net"] + hedef["kdv"] - hedef["brut"]) < 0.02,
    )


class _Yaz:
    def __init__(self, n=1):
        self.n = n
        self.sql = []
        self.params = []

    def __call__(self, sql, params=()):
        self.sql.append(sql)
        self.params.append(params)
        return self.n


def test_gib_basari_tutar_cek():
    bag = _baglam("K3", 664.75, 797.70, 132.95)
    f = _aylik_fatura(1965, "2026-02", 900.0)
    yaz = _Yaz(1)
    r = uygula_gib_basari_tutar(
        f, KYC_BANKA, ay_sinif="K3", plan_satir=bag["plan_satir"], planlar=PLAN_10, execute_func=yaz
    )
    check(
        "kapi ici yazar net+kdv=brut",
        r["yazildi"] is True
        and r["neden"] == "yazildi"
        and yaz.params[0][:3] == (664.75, 132.95, 797.70)
        and yaz.params[0][3] == 1965
        and yaz.params[0][4] == 900.0
        and abs(664.75 + 132.95 - 797.70) < 0.02
        and "notlar" not in yaz.sql[0].lower(),
    )

    for ay, sinif, eski in (("2025-06", "K2", 600.0), ("2026-03", "K0", 900.0)):
        y2 = _Yaz(1)
        r2 = uygula_gib_basari_tutar(
            _aylik_fatura(1, ay, eski),
            KYC_BANKA,
            ay_sinif=sinif,
            plan_satir=bag["plan_satir"],
            planlar=PLAN_10,
            execute_func=y2,
        )
        check("%s yazilmaz" % sinif, r2["yazildi"] is False and r2["neden"] == "kapi_disi" and y2.sql == [])

    y3 = _Yaz(1)
    r3 = uygula_gib_basari_tutar(
        f, KYC_BANKA, ay_sinif="K3", plan_satir=bag["plan_satir"], planlar=[], execute_func=y3
    )
    check("plansiz yazilmaz", r3["yazildi"] is False and y3.sql == [])

    y4 = _Yaz(1)
    r4 = uygula_gib_basari_tutar(
        f,
        dict(KYC_BANKA, kira_nakit=True),
        ay_sinif="K3",
        plan_satir=bag["plan_satir"],
        planlar=PLAN_10,
        execute_func=y4,
    )
    check("nakit yazilmaz", r4["yazildi"] is False and y4.sql == [])

    y5 = _Yaz(1)
    r5 = uygula_gib_basari_tutar(
        f,
        dict(KYC_BANKA, kira_nakit_tutar=200, kira_banka_tutar=400),
        ay_sinif="K3",
        plan_satir=bag["plan_satir"],
        planlar=PLAN_10,
        execute_func=y5,
    )
    check("hibrit yazilmaz", r5["yazildi"] is False and y5.sql == [])

    y6 = _Yaz(1)
    r6 = uygula_gib_basari_tutar(
        f,
        dict(KYC_BANKA, kdv_oran=8),
        ay_sinif="K3",
        plan_satir=bag["plan_satir"],
        planlar=PLAN_10,
        execute_func=y6,
    )
    check("kdv!=20 yazilmaz", r6["yazildi"] is False and y6.sql == [])

    auto = _aylik_fatura(140, "2026-03", 600.0, notlar="AUTO |AUTO_INV|2026-03|")
    y7 = _Yaz(1)
    r7 = uygula_gib_basari_tutar(
        auto, KYC_BANKA, ay_sinif="K3", plan_satir=bag["plan_satir"], planlar=PLAN_10, execute_func=y7
    )
    check("AUTO_INV yazilmaz", r7["yazildi"] is False and y7.sql == [])

    dolu = _aylik_fatura(11, "2026-02", 900.0)
    dolu["satirlar_json"] = '[{"ad":"Hizmet","birim_fiyat":750}]'
    y8 = _Yaz(1)
    r8 = uygula_gib_basari_tutar(
        dolu, KYC_BANKA, ay_sinif="K3", plan_satir=bag["plan_satir"], planlar=PLAN_10, execute_func=y8
    )
    check("satirlar_json dolu yazilmaz", r8["yazildi"] is False and r8["neden"] == "satirlar_dolu" and y8.sql == [])

    y9 = _Yaz(0)
    r9 = uygula_gib_basari_tutar(
        f, KYC_BANKA, ay_sinif="K3", plan_satir=bag["plan_satir"], planlar=PLAN_10, execute_func=y9
    )
    check("where tutmuyor yazilmaz", r9["yazildi"] is False and r9["neden"] == "where_tutmadi" and len(y9.sql) == 1)

    f_yeni = _aylik_fatura(1965, "2026-02", 797.70)
    y10 = _Yaz(1)
    r10 = uygula_gib_basari_tutar(
        f_yeni, KYC_BANKA, ay_sinif="K3", plan_satir=bag["plan_satir"], planlar=PLAN_10, execute_func=y10
    )
    check("ikinci cagri idempotent", r10["yazildi"] is False and r10["neden"] == "idempotent" and y10.sql == [])


def test_gib_hata_yazmaz():
    src = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    k = src.find("def api_gib_fatura_olustur")
    ol = src[k:src.find("def api_gib_cek_kaydet", k)]
    check(
        "olustur: cek yalniz basari sonrasi",
        ol.find("return jsonify") < ol.find("_gib_olustur_sonrasi_plan_tutar_cek")
        or ("onaylan" in ol and ol.find("onaylan") < ol.find("_gib_olustur_sonrasi_plan_tutar_cek")),
    )
    check(
        "400 onay yolu cek cagirmaz",
        "GİB onayı bekleniyor" in ol
        and ol.find("GİB onayı bekleniyor") < ol.find("_gib_olustur_sonrasi_plan_tutar_cek"),
    )
    check("cek yardimcisi tanimli", "def _gib_olustur_sonrasi_plan_tutar_cek" in src)


def test_gib_olustur_tutara_dokunmaz():
    src = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    i = src.find("def _fatura_gib_bilgilerini_yaz")
    j = src.find("def _faturalar_dev_http_log_path", i)
    parca = src[i:j]
    check(
        "gib yazim yalniz notlar/ettn/fatura_no",
        "UPDATE faturalar SET notlar = %s, ettn = %s, fatura_no = %s WHERE id = %s" in parca
        and "kdv_tutar" not in parca
        and "SET tutar" not in parca
        and "SET toplam" not in parca,
    )
    k = src.find("def api_gib_fatura_olustur")
    ol = src[k:k + 1800]
    check("olustur tutar yazmaz", "_fatura_gib_bilgilerini_yaz" in ol and "UPDATE faturalar SET tutar" not in ol)


def main():
    test_saf_kurallar()
    test_k3_plan_tutarlari()
    test_kilitli_eski()
    test_kapsam_disi_degismez()
    test_ay_bazli_tutarlar_2026_02()
    test_gib_basari_tutar_cek()
    test_gib_hata_yazmaz()
    test_gib_olustur_tutara_dokunmaz()
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("test_gib_plan_tutar ok", "checks_pass")


if __name__ == "__main__":
    main()
