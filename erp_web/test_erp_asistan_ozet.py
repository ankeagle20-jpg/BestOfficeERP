# -*- coding: utf-8 -*-
"""ERP Asistan özet: grid toplam borç, geciken ay, sütun tercihi. Canlıya yazmaz."""
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS = []
REF = date(2026, 10, 14)


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:180]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def _ay(y, m, tut, tahsil=False, kalan=None, odenen=None, kismi=False, kilit=""):
    row = {
        "yil": y,
        "ay": m,
        "ay_key": "%04d-%02d" % (y, m),
        "tutar_kdv_dahil": tut,
        "brut_tutar_kdv": tut,
        "tahsil_edildi": tahsil,
        "kalan_tutar_kdv": tut if kalan is None else kalan,
        "odenen_tutar_kdv": 0 if odenen is None else odenen,
        "kismi_tahsilat": kismi,
    }
    if kilit:
        row["kilit"] = kilit
    return row


def _payload_867():
    """867 yapısı: ödenmiş, kısmi, FIFO fazla, dondurulmuş (0), gelecek ve K1 açık ay."""
    return {
        "compute_rev": 31,
        "tahsilat_imza": "test",
        "aylar": [
            _ay(2026, 6, 200, tahsil=True, kalan=0, odenen=250),
            _ay(2026, 7, 0),
            _ay(2026, 8, 100, tahsil=True, kalan=0, odenen=100),
            _ay(2026, 9, 100, kalan=40, odenen=60, kismi=True),
            _ay(2026, 5, 50, kilit="K1"),
            _ay(2026, 10, 80, kilit="K2"),
            _ay(2026, 11, 90),
        ],
    }


def _payload_tenant():
    return {
        "compute_rev": 31,
        "tahsilat_imza": "test",
        "aylar": [
            _ay(2026, 8, 300, tahsil=True, kalan=0, odenen=400),
            _ay(2026, 9, 300, tahsil=True, kalan=0, odenen=300),
            _ay(2026, 10, 300, tahsil=True, kalan=0, odenen=300),
            _ay(2026, 11, 300),
        ],
    }


def test_grid_sayilari():
    from routes.giris_routes import (
        _firma_ozet_ozet_from_grid_cache_payload,
        geciken_kayitlari_suz,
    )
    from routes.erp_notlar_routes import _ozet_grid_borc_ekle
    import routes.erp_notlar_routes as en
    import routes.giris_routes as gr

    p867 = _payload_867()
    p900 = _payload_tenant()
    o867 = _firma_ozet_ozet_from_grid_cache_payload(p867, REF.year, REF.month)
    o900 = _firma_ozet_ozet_from_grid_cache_payload(p900, REF.year, REF.month)
    check("867 ozet hesap", isinstance(o867, dict) and isinstance(o900, dict))
    if not isinstance(o867, dict):
        return
    check("867 toplam borc", o867["toplam_borc"] == 170)
    check("867 geciken ay", o867["geciken_ay"] == 3)
    check("test tenant borc sifir", o900["toplam_borc"] == 0 and o900["geciken_ay"] == 0)
    check("kismi kalan", abs(o867["toplam_borc"] - (40 + 50 + 80)) < 0.001)

    def wa_ay(payload, dondurma):
        aylar = []
        for a in payload["aylar"]:
            if a.get("tahsil_edildi"):
                continue
            tutar = float(a.get("tutar_kdv_dahil") or 0)
            if tutar <= 0:
                continue
            anahtar = "%04d-%02d" % (int(a["yil"]), int(a["ay"]))
            if anahtar > "2026-10":
                continue
            aylar.append({"ay_key": anahtar, "tutar": tutar})
        if dondurma:
            süz = geciken_kayitlari_suz(
                [{"musteri_id": 867, "aylar": aylar}],
                {867: set(dondurma)},
                REF,
            )
            return (süz[0]["ay"] if süz else 0)
        return len(aylar)

    check("867 geciken wa listesi", wa_ay(p867, set()) == o867["geciken_ay"])
    check("dondurulmus ay sayilmaz", wa_ay(p867, {"2026-07"}) == o867["geciken_ay"] == 3)
    donuk = [_ay(2026, 9, 100), _ay(2026, 10, 80)]
    süz = geciken_kayitlari_suz(
        [{"musteri_id": 867, "aylar": [{"ay_key": "2026-09", "tutar": 100}, {"ay_key": "2026-10", "tutar": 80}]}],
        {867: {"2026-09"}},
        REF,
    )
    check("suz dondurulan ayi dusurur", süz and süz[0]["ay"] == 1)
    check("suz odememis kalan", len(donuk) == 2)

    okumalar = []
    regclass = {"var": True}
    yasak = []

    def fake_one(sql, params=None):
        check("regclass tek parametre", params == ("musteri_aylik_grid_cache",))
        return {"t": "musteri_aylik_grid_cache" if regclass["var"] else None}

    def fake_oku(ids, mem_ttl=12.0):
        okumalar.append((list(ids), mem_ttl))
        return {867: p867, 900: p900}

    def boom(*_a, **_k):
        yasak.append("cagrildi")
        raise AssertionError("yazma veya yeniden hesap")

    eski_fetch = en.fetch_one
    en.fetch_one = fake_one
    gr._read_aylik_grid_cache_payload_batch = fake_oku
    gr.musteri_firma_ozet_grid_ozet_batch = boom
    gr.prewarm_aylik_grid_cache_for_musteriler = boom
    gr._ensure_aylik_grid_cache_table = boom
    notlar = [
        {"musteri_id": 867, "id": 1},
        {"musteri_id": 900, "id": 2},
        {"musteri_id": 901, "id": 3},
        {"musteri_id": 867, "id": 4},
    ]
    out, adet, ms = _ozet_grid_borc_ekle(notlar)
    check("tek toplu cagri", len(okumalar) == 1 and sorted(okumalar[0][0]) == [867, 900, 901])
    check("onbellek bellegi atlanir", okumalar[0][1] == 0.0)
    check("musteri sayisi tekil", adet == 3)
    check("867 kolon grid ile ayni", out[0]["grid_yok"] is False and out[0]["toplam_borc"] == 170 and out[0]["geciken_ay"] == 3)
    check("tenant kolon sifir", out[1]["grid_yok"] is False and out[1]["toplam_borc"] == 0 and out[1]["geciken_ay"] == 0)
    check("kayitsiz tire", out[2]["grid_yok"] is True and out[2]["toplam_borc"] is None and out[2]["geciken_ay"] is None)
    check("yazma yok", yasak == [])
    check("sure sayi", isinstance(ms, float) and ms >= 0)
    print("olcum fixture musteri=%s sure_ms=%s" % (adet, ms))

    okumalar.clear()
    regclass["var"] = False
    yok, _, _ = _ozet_grid_borc_ekle([{"musteri_id": 867}, {"musteri_id": 900}])
    check("tablo yok okuma yok", okumalar == [] and yasak == [])
    check("tablo yok tire", all(x["grid_yok"] is True and x["toplam_borc"] is None for x in yok))

    okumalar.clear()
    regclass["var"] = True
    bir = [{"musteri_id": 1}]
    cok = [{"musteri_id": i} for i in range(1, 21)]
    _ozet_grid_borc_ekle(bir)
    _ozet_grid_borc_ekle(cok)
    check("n+1 yok", len(okumalar) == 2 and len(okumalar[0][0]) == 1 and len(okumalar[1][0]) == 20)
    en.fetch_one = eski_fetch


def test_kaynak_yazmaz():
    import inspect
    from routes.erp_notlar_routes import _ozet_grid_borc_ekle, _ozet_grid_payload_oku, api_ozet

    src = inspect.getsource(_ozet_grid_borc_ekle) + inspect.getsource(_ozet_grid_payload_oku)
    api = inspect.getsource(api_ozet)
    for yasak in ("CREATE", "ALTER", "INSERT", "UPDATE", "execute(", "musteri_firma_ozet_grid_ozet_batch", "prewarm_", "_ensure_"):
        check("helper " + yasak + " yok", yasak not in src)
    check("tek to_regclass", src.count("to_regclass") == 1)
    check("fetch_all helperda yok", "fetch_all" not in src)
    check("ozet tek ekleme", api.count("_ozet_grid_borc_ekle(") == 1)
    layout = (ROOT / "templates" / "layout.html").read_text(encoding="utf-8")
    check("js v=7", "js/erp-asistan.js') }}?v=7" in layout)
    check("kullanici id", "ERP_ASISTAN_UID" in layout)
    js = (ROOT / "static" / "js" / "erp-asistan.js").read_text(encoding="utf-8")
    check("ipucu borc", "Ekstredeki ek fatura/tediye/devir dahil değildir." in js)
    check("ipucu ay", "dondurulan ve ödenmiş aylar hariç)." in js)
    check("anahtar", "asistan_sutunlar_" in js)
    check("excel yok", "xlsx" not in js and "excel" not in js.lower())


def test_gecmis_sayfa():
    import inspect
    from datetime import datetime
    import routes.erp_notlar_routes as en

    kaynak = inspect.getsource(en.musteri_gecmis_paket) + inspect.getsource(en._serialize_gecmis)
    check("gecmis ddl yok", "CREATE" not in kaynak and "ALTER" not in kaynak and "INSERT" not in kaynak and "UPDATE" not in kaynak)
    check("gecmis wa alani yok", "whatsapp" not in kaynak)
    eski = (en.ensure_erp_notlar_tablolari, en.fetch_all, en._uid, en._benim_departman)
    cagri = []

    def fake_all(sql, params=None):
        cagri.append((sql, tuple(params or ())))
        off = int(sql.rsplit("OFFSET", 1)[-1].strip())
        mid = int(params[-1])
        if mid != 7:
            return [{
                "id": 99, "not_metni": "baska", "durum": "bekliyor",
                "created_at": datetime(2026, 1, 2, 10, 0),
                "hatirlatma_zamani": datetime(2026, 1, 3, 9, 0),
                "olusturan_ad": "X", "gorusulen_kisi": "Y",
                "whatsapp_telefon": "555", "whatsapp_mesaj": "gizli mesaj",
            }]
        if off == 0:
            return [{
                "id": i,
                "not_metni": "<script>alert(1)</script>\nsatir" if i == 3 else "not-%s" % i,
                "durum": "bekliyor",
                "created_at": datetime(2026, 10, i, 14, 30),
                "hatirlatma_zamani": datetime(2026, 10, i, 9, 0),
                "olusturan_ad": "Goren", "gorusulen_kisi": "Karsi",
                "whatsapp_telefon": "5551112233", "whatsapp_mesaj": "bu mesaj popupta yok",
            } for i in (3, 2, 1)]
        return [{
            "id": 8, "not_metni": "eski", "durum": "tamamlandi",
            "created_at": datetime(2025, 1, 1, 8, 0), "hatirlatma_zamani": None,
            "olusturan_ad": "Goren", "gorusulen_kisi": "",
            "whatsapp_telefon": "555",
        }]

    en.ensure_erp_notlar_tablolari = lambda: None
    en.fetch_all = fake_all
    en._uid = lambda: 5
    en._benim_departman = lambda: "satis"
    try:
        paket = en.musteri_gecmis_paket(7, 2, 0)
        sql, params = cagri[0]
        check("tek sorgu", len(cagri) == 1)
        check("yeni ustte sql", "created_at DESC" in sql and "OFFSET 0" in sql and "LIMIT 3" in sql)
        check("hedef ve gorunurluk", params[:3] == (5, 5, "satis") and params[-1] == 7 and "= %s" in sql)
        check("sayfa kesilir", paket["devam"] is True and [n["id"] for n in paket["notlar"]] == [3, 2])
        check("metin oldugu gibi", "<script>" in paket["notlar"][0]["not_metni"] and "\n" in paket["notlar"][0]["not_metni"])
        check("wa donmez", not any("whatsapp" in k or "telefon" in k for n in paket["notlar"] for k in n))
        check("saat etiketi", paket["notlar"][0]["created_etiket"].endswith("14:30"))
        ikinci = en.musteri_gecmis_paket(7, 2, 2)
        check("ofset", "OFFSET 2" in cagri[1][0] and ikinci["notlar"][0]["id"] == 8 and ikinci["devam"] is False)
        baska = en.musteri_gecmis_paket(8, 2, 0)
        check("musteri ayrimi", cagri[2][1][-1] == 8 and baska["notlar"][0]["id"] == 99)
        check("karismaz", all(n["id"] != 99 for n in paket["notlar"]))
        dar = en.musteri_gecmis_paket(7, 500, -4)
        check("olcu siniri", "LIMIT 51" in cagri[-1][0] and "OFFSET 0" in cagri[-1][0] and dar["ofset"] == 0)
    finally:
        en.ensure_erp_notlar_tablolari, en.fetch_all, en._uid, en._benim_departman = eski


def _gorunur_mu(row, uid, dep):
    if int(row["olusturan_kullanici_id"]) == int(uid):
        return True
    if row["gorunurluk"] != "ekip":
        return False
    if int(uid) in row["alicilar"]:
        return True
    return bool(row["departman"]) and row["departman"] == dep


def test_gorunurluk_liste_popup():
    """Kişisel not yalnız yazana, ekip notu alıcı veya aynı departmana. Liste ve popup aynı süzgeç."""
    import auth
    from datetime import datetime
    import routes.erp_notlar_routes as en
    from test_plan_degistir import _app

    class K:
        is_authenticated = True
        is_active = True
        is_anonymous = False
        id = 5

        def get_id(self):
            return "5"

    def satir(i, uid, gor, dep, alicilar, saat, hedef):
        return {
            "id": i,
            "olusturan_kullanici_id": uid,
            "gorunurluk": gor,
            "departman": dep,
            "alicilar": alicilar,
            "not_metni": "n-%s" % i,
            "durum": "bekliyor",
            "created_at": datetime(2026, 10, 10, saat, 0),
            "hatirlatma_zamani": datetime(2026, 10, 11, 9, 0),
            "olusturan_ad": "Y",
            "gorusulen_kisi": "K",
            "hedef_id": hedef,
            "ozet_musteri_adi": "••••",
            "musteri_adi": "••••",
            "iliski_tip": "musteri",
            "iliski_id": hedef,
        }

    havuz = [
        satir(10, 9, "kisisel", "", [], 18, 7),
        satir(11, 9, "ekip", "satis", [], 17, 7),
        satir(12, 9, "ekip", "", [5], 16, 7),
        satir(13, 9, "ekip", "muhasebe", [8], 15, 7),
        satir(14, 5, "kisisel", "", [], 14, 7),
        satir(15, 9, "kisisel", "", [], 19, 8),
    ]
    sqls = []

    def fake_all(sql, params=None):
        sqls.append(sql)
        params = tuple(params or ())
        uid, dep = int(params[0]), str(params[2])
        sec = [r for r in havuz if _gorunur_mu(r, uid, dep)]
        if "DISTINCT ON" in sql:
            grup = {}
            for r in sorted(sec, key=lambda r: (r["created_at"], r["id"]), reverse=True):
                grup.setdefault(r["hedef_id"], r)
            return list(grup.values())
        mid = int(params[-1])
        sec = [r for r in sec if r["hedef_id"] == mid]
        sec.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        lim = int(sql.split("LIMIT", 1)[1].split("OFFSET")[0].strip())
        off = int(sql.rsplit("OFFSET", 1)[-1].strip())
        return sec[off:off + lim]

    eski = (
        auth.current_user,
        en.ensure_erp_notlar_tablolari,
        en.fetch_all,
        en._uid,
        en._benim_departman,
        en._ozet_grid_borc_ekle,
    )
    auth.current_user = K()
    en.ensure_erp_notlar_tablolari = lambda: None
    en.fetch_all = fake_all
    en._uid = lambda: 5
    en._benim_departman = lambda: "satis"
    en._ozet_grid_borc_ekle = lambda notlar: (notlar, 0, 0.0)
    app = _app()
    try:
        paket = en.musteri_gecmis_paket(7, 50, 0)
        ids = [n["id"] for n in paket["notlar"]]
        check("kisisel baskasi gizli", 10 not in ids and 15 not in ids)
        check("ekip departman gorunur", 11 in ids)
        check("ekip alici gorunur", 12 in ids)
        check("ekip yabanci gizli", 13 not in ids)
        check("kendi notu gorunur", 14 in ids)
        with app.test_request_context("/erp-notlar/api/ozet?kapsam=hepsi"):
            yanit = en.api_ozet()
        govde = yanit.get_json()
        liste = [n for n in (govde or {}).get("notlar") or [] if n.get("musteri_id") == 7]
        kural = en._gorunur_sql()
        check("ayni süzgec", any(kural in s and "OFFSET" in s for s in sqls) and any(kural in s and "DISTINCT ON" in s for s in sqls))
        check("son aciklama ayni", len(liste) == 1 and liste[0]["id"] == paket["notlar"][0]["id"] == 11)
    finally:
        (
            auth.current_user,
            en.ensure_erp_notlar_tablolari,
            en.fetch_all,
            en._uid,
            en._benim_departman,
            en._ozet_grid_borc_ekle,
        ) = eski


def _icinde(an, bas, bit):
    return bas <= an < bit


def test_hatirlatma_pencereleri():
    from datetime import datetime
    import auth
    import routes.erp_notlar_routes as en

    def an(y, m, d, sa=0, dk=0, sn=0):
        return datetime(y, m, d, sa, dk, sn, tzinfo=en.IST)

    def pencere(sec, gun, turu="hatirlatma", bas=None, bit=None):
        etkin = en._ozet_tarih_sec(sec, turu)
        return etkin, en._ozet_tarih_penceresi(etkin, bas, bit, bugun=gun)

    car = date(2026, 10, 14)
    check("carsamba", car.weekday() == 2)
    sec, (bas, bit, err) = pencere("bugun", car, "gorusme")
    check("gorusme bugun", sec == "bugun" and err is None and bas == an(2026, 10, 14) and bit == an(2026, 10, 15))
    check("gun sonu dahil", _icinde(an(2026, 10, 14, 23, 59, 59), bas, bit))
    check("ertesi gun basi haric", not _icinde(an(2026, 10, 15), bas, bit))
    _, (bas, bit, _) = pencere("dun", car, "gorusme")
    check("gorusme dun", bas == an(2026, 10, 13) and bit == an(2026, 10, 14))
    _, (bas, bit, _) = pencere("bu_hafta", car, "gorusme")
    check("gorusme bu hafta", bas == an(2026, 10, 12) and bit == an(2026, 10, 15))
    check("gorusme pazar disi", not _icinde(an(2026, 10, 18, 12), bas, bit))
    _, (bas, bit, _) = pencere("bu_hafta", date(2026, 10, 12), "gorusme")
    check("gorusme pazartesi", date(2026, 10, 12).weekday() == 0 and bas == an(2026, 10, 12) and bit == an(2026, 10, 13))
    _, (bas, bit, _) = pencere("bu_hafta", date(2026, 10, 18), "gorusme")
    check("gorusme pazar", date(2026, 10, 18).weekday() == 6 and bas == an(2026, 10, 12) and bit == an(2026, 10, 19))
    _, (bas, bit, _) = pencere("bu_ay", car, "gorusme")
    check("gorusme bu ay", bas == an(2026, 10, 1) and bit == an(2026, 10, 15))
    _, (bas, bit, _) = pencere("son_7", car, "gorusme")
    check("gorusme son 7", bas == an(2026, 10, 8) and bit == an(2026, 10, 15))
    _, (bas, bit, _) = pencere("son_30", car, "gorusme")
    check("gorusme son 30", bas == an(2026, 9, 15) and bit == an(2026, 10, 15))
    _, (bas, bit, err) = pencere("ozel", car, "gorusme", "2026-12-31", "2027-01-02")
    check("ozel sinir dahil", err is None and _icinde(an(2026, 12, 31), bas, bit) and _icinde(an(2027, 1, 2, 23, 59, 59), bas, bit))
    check("ozel disi", not _icinde(an(2027, 1, 3), bas, bit) and not _icinde(an(2026, 12, 30, 23, 59, 59), bas, bit))
    sec, (bas, bit, err) = pencere("ozel", car, "hatirlatma", "2026-05-02", "2026-05-01")
    check("ozel ters", err == "Başlangıç tarihi bitişten sonra olamaz")

    sec, (bas, bit, err) = pencere("yarin", car)
    check("yarin", sec == "yarin" and bas == an(2026, 10, 15) and bit == an(2026, 10, 16) and err is None)
    check("yarin sonu", _icinde(an(2026, 10, 15, 23, 59, 59), bas, bit) and not _icinde(an(2026, 10, 16), bas, bit))
    _, (bas, bit, _) = pencere("sonraki_hafta", car)
    check("sonraki hafta carsamba", bas == an(2026, 10, 19) and bit == an(2026, 10, 26))
    check("bu pazar haric", not _icinde(an(2026, 10, 18, 23, 59, 59), bas, bit))
    check("sonraki pazar dahil", _icinde(an(2026, 10, 25, 23, 59, 59), bas, bit) and not _icinde(an(2026, 10, 26), bas, bit))
    _, (bas, bit, _) = pencere("sonraki_hafta", date(2026, 10, 12))
    check("sonraki hafta pazartesi", bas == an(2026, 10, 19) and not _icinde(an(2026, 10, 12, 12), bas, bit))
    _, (bas, bit, _) = pencere("sonraki_hafta", date(2026, 10, 18))
    check("sonraki hafta pazar", bas == an(2026, 10, 19) and bit == an(2026, 10, 26))
    yil = date(2026, 12, 27)
    check("aralik pazari", yil.weekday() == 6)
    _, (bas, bit, _) = pencere("sonraki_hafta", yil)
    check("hafta yil sonu", bas == an(2026, 12, 28) and bit == an(2027, 1, 4))
    check("ocak pazar dahil", _icinde(an(2027, 1, 3, 23, 59, 59), bas, bit))
    _, (bas, bit, _) = pencere("sonraki_ay", car)
    check("sonraki ay", bas == an(2026, 11, 1) and bit == an(2026, 12, 1))
    check("ay siniri", not _icinde(an(2026, 10, 31, 23, 59, 59), bas, bit) and _icinde(an(2026, 11, 30, 23, 59, 59), bas, bit))
    _, (bas, bit, _) = pencere("sonraki_ay", date(2026, 12, 31))
    check("ay yil sonu", bas == an(2027, 1, 1) and bit == an(2027, 2, 1))
    _, (bas, bit, _) = pencere("sonraki_ay", date(2026, 1, 31))
    check("subat", bas == an(2026, 2, 1) and bit == an(2026, 3, 1) and _icinde(an(2026, 2, 28, 23, 59, 59), bas, bit))
    _, (bas, bit, _) = pencere("sonraki_30", car)
    check("otuz gun", bas == an(2026, 10, 14) and bit == an(2026, 11, 13))
    check("otuz dahil", _icinde(an(2026, 10, 14), bas, bit) and _icinde(an(2026, 11, 12, 23, 59, 59), bas, bit))
    check("otuz haric", not _icinde(an(2026, 10, 13, 23, 59, 59), bas, bit) and not _icinde(an(2026, 11, 13), bas, bit))
    _, (bas, bit, _) = pencere("sonraki_30", date(2026, 12, 15))
    check("otuz yil sonu", bas == an(2026, 12, 15) and bit == an(2027, 1, 14))
    sec, (bas, bit, _) = pencere("dun", car, "hatirlatma")
    check("hatirlatmada dun hepsi", sec == "hepsi" and bas is None and bit is None)
    sec, _ = pencere("yarin", car, "gorusme")
    check("gorusmede yarin hepsi", sec == "hepsi")

    class K:
        is_authenticated = True
        is_active = True
        is_anonymous = False
        id = 5

        def get_id(self):
            return "5"

    sqls = []

    def fake_all(sql, params=None):
        sqls.append((sql, tuple(params or ())))
        return []

    from test_plan_degistir import _app
    eski = (auth.current_user, en.ensure_erp_notlar_tablolari, en.fetch_all, en._uid, en._benim_departman, en._ozet_grid_borc_ekle)
    auth.current_user = K()
    en.ensure_erp_notlar_tablolari = lambda: None
    en.fetch_all = fake_all
    en._uid = lambda: 5
    en._benim_departman = lambda: "satis"
    en._ozet_grid_borc_ekle = lambda notlar: (notlar, 0, 0.0)
    app = _app()
    try:
        with app.test_request_context("/erp-notlar/api/ozet?kapsam=acik&q=x&tarih_turu=hatirlatma&tarih=yarin"):
            en.api_ozet()
        sql, params = sqls[0]
        check("tek sorgu", len(sqls) == 1)
        check("hatirlatma alani", "ozet.hatirlatma_zamani IS NOT NULL" in sql and "ILIKE" in sql and "bekliyor" in sql)
        beklenen = en._ozet_tarih_penceresi("yarin", None, None)
        check("yarin parametre", params[-4:] == (beklenen[0], beklenen[0], beklenen[1], beklenen[1]))
        sqls.clear()
        with app.test_request_context("/erp-notlar/api/ozet?kapsam=hepsi&tarih_turu=hatirlatma&tarih=dun"):
            en.api_ozet()
        check("hatirlatmasiz hepsi", "ozet.hatirlatma_zamani IS NOT NULL" not in sqls[0][0] and len(sqls) == 1)
        sqls.clear()
        with app.test_request_context("/erp-notlar/api/ozet?tarih_turu=gorusme&tarih=bugun"):
            en.api_ozet()
        check("gorusme kolonu", "ozet.created_at IS NOT NULL" in sqls[0][0] and "ozet.hatirlatma_zamani IS NOT NULL" not in sqls[0][0])
    finally:
        (auth.current_user, en.ensure_erp_notlar_tablolari, en.fetch_all, en._uid, en._benim_departman, en._ozet_grid_borc_ekle) = eski


def test_asistan_wa():
    import auth
    from datetime import date
    import routes.erp_notlar_routes as en

    cagri = []

    def musteri(mid):
        if int(mid) == 7:
            return {"id": 7, "name": "••••", "telefon": "05550001122"}
        if int(mid) == 8:
            return {"id": 8, "name": "••••", "telefon": ""}
        return None

    def grid(mid, bugun=None):
        if int(mid) == 7:
            return {"grid_yok": False, "toplam_borc": 10, "geciken_ay": 1, "gun": 0}
        return {"grid_yok": True, "toplam_borc": None, "geciken_ay": None, "gun": None}

    def gonder(uid, data, origin, host):
        cagri.append(data.get("deneme"))
        if data.get("mesaj") == "tunel":
            return {"ok": True, "geri_dus": True, "mesaj": "WhatsApp servisi bağlı değil, WhatsApp Web sayfası açılıyor"}, 200
        return {"ok": True, "durum": "kuyrukta"}, 200

    eski = (en._wa_musteri, en._wa_grid_ozet, en._wa_aktif_link_oku, en._wa_son_tahsilat_oku, en._WA_DENEME)
    en._wa_musteri = musteri
    en._wa_grid_ozet = grid
    en._wa_aktif_link_oku = lambda mid: None
    en._wa_son_tahsilat_oku = lambda mid: None
    en._WA_DENEME.clear()
    try:
        govde, kod = en.asistan_wa_onizleme(9)
        check("baska tenant yok", kod == 404)
        govde, kod = en.asistan_wa_onizleme(8)
        check("numarasiz 400", kod == 400 and govde.get("mesaj") == "Telefon yok")
        govde, kod = en.asistan_wa_onizleme(7)
        check("onizleme", kod == 200 and govde.get("maske") == "**22" and govde.get("geciken_ay") == 1)
        check("borc ayni", govde.get("toplam_borc") == 10)
        check("telefon yok", "telefon" not in govde and "0555" not in str(govde.get("maske")))
        ilk = govde["sablonlar"][0]
        check("onerilen ustte", ilk.get("onerilen") is True and "{isim}" not in ilk.get("metin", ""))
        en._wa_grid_ozet = lambda mid, bugun=None: {"grid_yok": True, "toplam_borc": None, "geciken_ay": None, "gun": None}
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "x", "sablon": "0", "deneme": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("grid yok engel", kod == 409 and not cagri)
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "", "sablon": "ozel", "deneme": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("bos engel", kod == 400)
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "a" * 1001, "sablon": "ozel", "deneme": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3",
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("uzun engel", kod == 400 and govde.get("mesaj") == "Mesaj çok uzun")
        deneme = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4"
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "sahte", "sablon": "ozel", "deneme": deneme,
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("ozel gonder", kod == 200 and govde.get("durum") == "kuyrukta" and cagri == [deneme])
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "sahte", "sablon": "ozel", "deneme": deneme,
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("cift tik", kod == 409 and cagri == [deneme])
        govde, kod = en.asistan_wa_gonder(5, 8, {
            "onay": True, "mesaj": "sahte", "sablon": "ozel", "deneme": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5",
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("numarasiz gonder 400", kod == 400)
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "tunel", "sablon": "ozel", "deneme": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa6",
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("tunel hata", govde.get("durum") == "hata" and "bağlı değil" in govde.get("mesaj", ""))
        kaynak = open(en.__file__, encoding="utf-8").read().split("def asistan_wa_onizleme")[1].split("def asistan_wa_gonder")[0]
        check("onizleme ddl yok", "CREATE" not in kaynak and "ALTER" not in kaynak)
        check("batch yok", "musteri_firma_ozet_grid_ozet_batch" not in kaynak)
    finally:
        en._wa_musteri, en._wa_grid_ozet, en._wa_aktif_link_oku, en._wa_son_tahsilat_oku, en._WA_DENEME = eski
        en._WA_DENEME.clear()


def test_wa_kapi():
    """Oturumsuz uç kapalı; müşteri sorgusu kiracı search_path'inde; istemci telefonu yok."""
    from flask import Flask, g
    from flask_login import login_user
    from auth import User, login_manager
    import db as dbmod
    import routes.erp_notlar_routes as en

    app = Flask(__name__)
    app.secret_key = "wa-kapi"
    login_manager.init_app(app)
    app.register_blueprint(en.bp)
    client = app.test_client()
    r = client.post(
        "/erp-notlar/api/musteri/7/whatsapp",
        json={"onay": True, "mesaj": "x", "telefon": "000", "sablon": "ozel"},
    )
    check("oturumsuz gonder 401", r.status_code == 401)
    r = client.get("/erp-notlar/api/musteri/7/whatsapp")
    check("oturumsuz onizleme 401", r.status_code == 401)
    kaynak = open(en.__file__, encoding="utf-8").read()
    check("gonder giris gerekli", "@giris_gerekli\ndef api_musteri_whatsapp_gonder" in kaynak)
    check("fetch tenant yolu", en.fetch_one is dbmod.fetch_one)

    yakalanan = {}

    def yakala(sql, params=()):
        yakalanan["sql"] = sql
        yakalanan["params"] = params
        return None

    eski_fetch = en.fetch_one
    en.fetch_one = yakala
    try:
        satir = en._wa_musteri(4242)
    finally:
        en.fetch_one = eski_fetch
    sql = str(yakalanan.get("sql") or "")
    check("musteri customers", satir is None and "FROM customers" in sql and "WHERE id = %s" in sql)
    check("sorgu sema oneksiz", "tenant_" not in sql.lower() and "public." not in sql.lower())
    check("musteri id parametre", yakalanan.get("params") == (4242,))
    with app.test_request_context("/"):
        g.tenant_schema = "tenant_abc"
        check("istek semasi", dbmod._tenant_schema_for_request() == "tenant_abc")
        g.tenant_schema = "public"
        try:
            dbmod._tenant_schema_for_request()
            check("gecersiz sema kapali", False)
        except ValueError:
            check("gecersiz sema kapali", True)
    db_src = open(dbmod.__file__, encoding="utf-8").read()
    hazir = db_src.split("def _prepare_db_connection")[1].split("\ndef ")[0]
    check("search_path tenant", "_tenant_schema_for_request" in hazir and "SET LOCAL search_path TO" in db_src)

    gelen = {"n": 0, "telefon": ""}

    def musteri(mid):
        if int(mid) == 7:
            return {"id": 7, "name": "••••", "telefon": "05550001122"}
        return None

    def gonder(uid, data, origin, host):
        gelen["n"] += 1
        gelen["telefon"] = str(data.get("telefon") or "")
        return {"ok": True, "durum": "kuyrukta"}, 200

    eski = (en._wa_musteri, en._wa_grid_ozet)
    en._wa_musteri = musteri
    en._wa_grid_ozet = lambda mid, bugun=None: {"grid_yok": False, "toplam_borc": 1, "geciken_ay": 1, "gun": 0}
    deneme = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa9"
    en._WA_DENEME.pop(deneme, None)
    try:
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "sahte", "sablon": "ozel", "deneme": deneme, "telefon": "0001112222",
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("kapi gonder", kod == 200 and govde.get("durum") == "kuyrukta" and gelen["n"] == 1)
        check("istemci telefonu yok", gelen["telefon"] != "0001112222" and gelen["telefon"].endswith("22"))
        govde, kod = en.asistan_wa_gonder(5, 4242, {
            "onay": True, "mesaj": "sahte", "sablon": "ozel",
            "deneme": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab",
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("kayitsiz musteri 404", kod == 404 and gelen["n"] == 1)
    finally:
        en._wa_musteri, en._wa_grid_ozet = eski
        en._WA_DENEME.pop(deneme, None)

    js = (ROOT / "static/js/erp-asistan.js").read_text(encoding="utf-8")
    govde_js = js.split("JSON.stringify({ mesaj:", 1)[1].split("})", 1)[0]
    check("istemci telefon gondermez", "telefon" not in govde_js)

    sayac = {"n": 0}
    eski_fn = en.asistan_wa_gonder

    def sahte(uid, mid, data, origin, host, gonder_fn=None):
        sayac["n"] += 1
        return {"ok": True, "durum": "kuyrukta", "mesaj": "Kuyruğa alındı"}, 200

    en.asistan_wa_gonder = sahte
    try:
        with app.test_request_context(
            "/erp-notlar/api/musteri/7/whatsapp",
            method="POST",
            json={"onay": True, "mesaj": "sahte", "sablon": "ozel", "deneme": deneme},
            headers={"Origin": "http://localhost"},
        ):
            login_user(User(5, "t", "T", "personel"))
            yanit = en.api_musteri_whatsapp_gonder(7)
        kod = yanit[1] if isinstance(yanit, tuple) else yanit.status_code
        check("oturum acik gecer", kod == 200 and sayac["n"] == 1)
    finally:
        en.asistan_wa_gonder = eski_fn


def test_wa_panel():
    from datetime import datetime, timezone
    import odeme_linki as ol
    import routes.erp_notlar_routes as en
    from sozlesme_whatsapp import tahsilat_metin

    yazildi = {"n": 0}
    eski_olustur = ol.link_olustur
    eski_ensure = ol.ensure_tablolar

    def yasak(*_a, **_k):
        yazildi["n"] += 1
        raise AssertionError("yazma")

    ol.link_olustur = yasak
    ol.ensure_tablolar = yasak
    eski = (en._wa_musteri, en._wa_grid_ozet, en._wa_aktif_link_oku, en._wa_son_tahsilat_oku)
    en._wa_musteri = lambda mid: {"id": 7, "name": "••••", "telefon": "05550001122"} if int(mid) == 7 else None
    en._wa_grid_ozet = lambda mid, bugun=None: {"grid_yok": False, "toplam_borc": 10, "geciken_ay": 1, "gun": 0}
    en._wa_aktif_link_oku = lambda mid: None
    en._wa_son_tahsilat_oku = lambda mid: None
    cagri = []

    def gonder(uid, data, origin, host):
        cagri.append(data.get("sablon"))
        return {"ok": True, "durum": "kuyrukta"}, 200

    try:
        govde, kod = en.asistan_wa_onizleme(7)
        gruplar = [s.get("grup") for s in govde["sablonlar"]]
        check("panel gruplari", kod == 200 and gruplar.count("gecikme") == 10 and "link" in gruplar and "tahsilat" in gruplar)
        check("oneri acilis", govde["sablonlar"][0].get("onerilen") is True and govde.get("onerilen") == govde["sablonlar"][0]["anahtar"])
        link = [s for s in govde["sablonlar"] if s["anahtar"] == "link"][0]
        makbuz = [s for s in govde["sablonlar"] if s["anahtar"] == "tahsilat"][0]
        check("link pasif", link["engel"] is True and link["metin"] == "" and link["ipucu"] == "Aktif ödeme linki yok")
        check("makbuz pasif", makbuz["engel"] is True and makbuz["metin"] == "")
        check("link yazilmadi", yazildi["n"] == 0)
        kok = ol.public_odeme_base().rstrip("/") + "/odeme/sahte-adres"
        en._wa_aktif_link_oku = lambda mid: {
            "tutar_kurus": 1500, "aciklama": "aidat", "durum": "bekliyor",
            "expires_at": datetime(2026, 12, 1, tzinfo=timezone.utc),
            "whatsapp_mesaj": "adres " + kok,
        }
        govde, _kod = en.asistan_wa_onizleme(7)
        link = [s for s in govde["sablonlar"] if s["anahtar"] == "link"][0]
        beklenen = ol.wa_varsayilan_mesaj("••••", "aidat", "15.00", kok, datetime(2026, 12, 1, tzinfo=timezone.utc), ol.ofis_imzasi())
        check("link dolar", link["engel"] is False and link["metin"] == beklenen and yazildi["n"] == 0)
        en._wa_son_tahsilat_oku = lambda mid: {"makbuz_no": "M1", "tutar": 10, "tahsilat_tarihi": datetime(2026, 10, 1)}
        govde, _kod = en.asistan_wa_onizleme(7)
        makbuz = [s for s in govde["sablonlar"] if s["anahtar"] == "tahsilat"][0]
        check("makbuz dolar", makbuz["engel"] is False and makbuz["rozet"] == "Son tahsilat bilgisiyle" and makbuz["metin"] == tahsilat_metin("M1", 10, datetime(2026, 10, 1), False))
        en._wa_grid_ozet = lambda mid, bugun=None: {"grid_yok": True, "toplam_borc": None, "geciken_ay": None, "gun": None}
        en._wa_aktif_link_oku = lambda mid: None
        en._wa_son_tahsilat_oku = lambda mid: None
        govde, _kod = en.asistan_wa_onizleme(7)
        check("borclu pasif", all(s["engel"] and s["metin"] == "" for s in govde["sablonlar"] if s["grup"] == "gecikme"))
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "x", "sablon": "link", "deneme": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa10",
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("link gonderilmez", kod == 409 and not cagri and yazildi["n"] == 0)
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "x", "sablon": "tahsilat", "deneme": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11",
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("makbuz gonderilmez", kod == 409 and not cagri)
        kaynak = open(en.__file__, encoding="utf-8").read().split("def _wa_aktif_link_oku")[1].split("def asistan_wa_onizleme")[0]
        check("panel ddl yok", "CREATE" not in kaynak and "ALTER" not in kaynak and "INSERT" not in kaynak and "link_olustur" not in kaynak)
    finally:
        ol.link_olustur = eski_olustur
        ol.ensure_tablolar = eski_ensure
        en._wa_musteri, en._wa_grid_ozet, en._wa_aktif_link_oku, en._wa_son_tahsilat_oku = eski


def arayuz():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        check("playwright", False, "yok")
        return
    from test_plan_degistir import _app

    app = _app()
    app.logger.disabled = True

    @app.route("/asistan-test")
    def asistan_test():
        return (
            "<!doctype html><html><head><meta charset='utf-8'></head><body>"
            "<script>window.ERP_ASISTAN_UID = 41;</script>"
            "<div id='erp-asistan-ozet'></div>"
            "<script src='/static/js/erp-asistan.js?v=7'></script>"
            "</body></html>"
        )

    @app.route("/asistan-test-b")
    def asistan_test_b():
        return (
            "<!doctype html><html><head><meta charset='utf-8'></head><body>"
            "<script>window.ERP_ASISTAN_UID = 99;</script>"
            "<div id='erp-asistan-ozet'></div>"
            "<script src='/static/js/erp-asistan.js?v=7'></script>"
            "</body></html>"
        )

    client = app.test_client()
    notlar = [
        {"id": 1, "musteri_id": 867, "iliski_etiket": "••••", "wa_var": True, "not_metni": "kisa not metni uzatildi",
         "created_etiket": "01.10.2026", "hatirlatma_etiket": "02.10.2026", "durum": "bekliyor",
         "olusturan_ad": "A", "gorusulen_kisi": "B", "toplam_borc": 0, "geciken_ay": 0},
        {"id": 2, "musteri_id": 900, "iliski_etiket": "••••", "wa_var": False, "not_metni": "diger",
         "created_etiket": "03.10.2026", "hatirlatma_etiket": "04.10.2026", "durum": "bekliyor",
         "olusturan_ad": "A", "gorusulen_kisi": "B", "toplam_borc": 1500.5, "geciken_ay": 4},
        {"id": 3, "musteri_id": 901, "iliski_etiket": "••••", "not_metni": "ucuncu",
         "created_etiket": "05.10.2026", "hatirlatma_etiket": "06.10.2026", "durum": "tamamlandi",
         "olusturan_ad": "A", "gorusulen_kisi": "B", "toplam_borc": 40, "geciken_ay": 1},
        {"id": 4, "musteri_id": 902, "iliski_etiket": "••••", "not_metni": "hesap yok",
         "created_etiket": "07.10.2026", "hatirlatma_etiket": "08.10.2026", "durum": "bekliyor",
         "olusturan_ad": "A", "gorusulen_kisi": "B", "grid_yok": True,
         "toplam_borc": None, "geciken_ay": None},
    ]
    kolon_istek = []
    gecmis_istek = []
    ozet_istek = []
    wa_post = {"n": 0}

    def isle(route, request):
        try:
            _isle(route, request)
        except Exception as exc:
            print("ROUTE", type(exc).__name__, str(exc)[:200])
            route.fulfill(status=500, body=str(exc)[:200])

    def _isle(route, request):
        tam = request.url.split("asistan.test")[-1]
        yol = tam.split("?")[0]
        if yol.endswith("/whatsapp") and "/erp-notlar/api/musteri/" in yol:
            if request.method == "POST":
                wa_post["n"] += 1
                route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "ok": True, "durum": "kuyrukta", "mesaj": "Kuyruğa alındı",
                }))
                return
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "ok": True, "unvan": "••••", "maske": "**11", "grid_yok": False,
                "toplam_borc": 10, "geciken_ay": 1, "onerilen": "0",
                "sablonlar": [
                    {"anahtar": "0", "etiket": "Vade günü", "grup": "gecikme", "baslik": "Gecikme hatırlatma",
                     "metin": "sahte vade", "onizleme": "sahte vade", "borc": True, "engel": False, "onerilen": True, "ipucu": "", "rozet": ""},
                    {"anahtar": "3", "etiket": "3 gün", "grup": "gecikme", "baslik": "Gecikme hatırlatma",
                     "metin": "sahte uc", "onizleme": "sahte uc", "borc": True, "engel": False, "onerilen": False, "ipucu": "", "rozet": ""},
                    {"anahtar": "link", "etiket": "Ödeme linki", "grup": "link", "baslik": "Ödeme linki",
                     "metin": "", "onizleme": "", "borc": False, "engel": True, "onerilen": False, "ipucu": "Aktif ödeme linki yok", "rozet": ""},
                    {"anahtar": "tahsilat", "etiket": "Tahsilat makbuzu", "grup": "tahsilat", "baslik": "Tahsilat bilgilendirme",
                     "metin": "", "onizleme": "", "borc": False, "engel": True, "onerilen": False, "ipucu": "", "rozet": ""},
                ],
            }))
            return
        if "/erp-notlar/api/musteri/" in yol and yol.endswith("/notlar"):
            gecmis_istek.append(tam)
            mid = yol.split("/musteri/")[1].split("/")[0]
            ofset = "0"
            if "ofset=" in tam:
                ofset = tam.split("ofset=", 1)[1].split("&")[0]
            if mid != "867":
                govde = {"ok": True, "devam": False, "ofset": int(ofset or 0), "notlar": [{
                    "id": 9, "created_etiket": "09.09.2026 09:09", "hatirlatma_etiket": "",
                    "durum": "bekliyor", "olusturan_ad": "Z", "gorusulen_kisi": "Z",
                    "not_metni": "baska-musteri-notu",
                }]}
            elif ofset in ("", "0"):
                govde = {"ok": True, "devam": True, "ofset": 0, "notlar": [
                    {"id": 3, "created_etiket": "03.10.2026 18:00", "hatirlatma_etiket": "04.10.2026 09:00",
                     "durum": "bekliyor", "olusturan_ad": "G1", "gorusulen_kisi": "K1",
                     "not_metni": "<b>kalin</b>\nsatir2", "whatsapp_telefon": "5550001111",
                     "whatsapp_mesaj": "gizli-wa-icerik"},
                    {"id": 1, "created_etiket": "01.10.2026 10:00", "hatirlatma_etiket": "02.10.2026 09:00",
                     "durum": "ertelendi", "olusturan_ad": "G1", "gorusulen_kisi": "K1",
                     "not_metni": "orta not"},
                ]}
            else:
                govde = {"ok": True, "devam": False, "ofset": int(ofset or 0), "notlar": [{
                    "id": 0, "created_etiket": "01.01.2025 08:00", "hatirlatma_etiket": "",
                    "durum": "tamamlandi", "olusturan_ad": "G1", "gorusulen_kisi": "K1",
                    "not_metni": "eski-kayit",
                }]}
            route.fulfill(status=200, content_type="application/json", body=json.dumps(govde))
            return
        if yol.startswith("/erp-notlar/api/ozet"):
            ozet_istek.append(tam)
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "ok": True, "notlar": notlar, "grid_musteri": 3, "grid_ms": 1.5,
            }))
            return
        if yol.startswith("/erp-notlar/api/kolonlar"):
            kolon_istek.append(request.method + " " + yol)
            if request.method == "POST":
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": False}))
                return
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "ok": True,
                "siralama": ["musteri", "gorusme", "hatirlatma", "aciklama", "durum", "gorusen", "gorusulen"],
            }))
            return
        if yol.startswith("/static/"):
            hedef = ROOT / yol.lstrip("/")
            if hedef.is_file():
                route.fulfill(status=200, body=hedef.read_bytes())
                return
        if yol.startswith("/asistan-test"):
            r = client.get(yol)
            route.fulfill(status=r.status_code, content_type="text/html", body=r.get_data())
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "notlar": []}))

    with sync_playwright() as pw:
        try:
            tarayici = pw.chromium.launch(channel="msedge", headless=True)
        except Exception as exc:
            check("tarayici", False, type(exc).__name__)
            return
        s = tarayici.new_page(viewport={"width": 1100, "height": 700})
        s.add_init_script(
            "if (!sessionStorage.getItem('asistan-hazir')) {"
            "localStorage.setItem('asistan_sutunlar_41', JSON.stringify({sira:['musteri','durum','gorusme','yok_kolon'], gizli:[]}));"
            "localStorage.setItem('asistan_sutunlar_99', JSON.stringify({sira:['durum','musteri'], gizli:['musteri']}));"
            "sessionStorage.setItem('asistan-hazir','1');}"
        )
        hatalar = []
        s.on("pageerror", lambda e: hatalar.append(str(e)[:200]))
        s.route("**/*", isle)
        s.goto("http://asistan.test/asistan-test", wait_until="load")
        if hatalar:
            print("SAYFA", hatalar[:3])
        try:
            s.wait_for_function("typeof window.erpAsistanOzetYukle === 'function'", timeout=15000)
        except Exception:
            print("HTML", s.content()[:500])
            print("HATA", hatalar[:4])
            raise
        birlesik = s.evaluate(
            "() => window.erpAsistanSutunBirlestir({sira:['musteri','durum','yok'], gizli:['yok','durum']})"
        )
        check("eski kolon yok sayilir", "yok" not in birlesik["sira"] and "yok" not in birlesik["gizli"])
        check("yeni sutun varsayilan yer", birlesik["sira"][:3] == ["musteri", "toplam_borc", "geciken_ay"])
        check("kayitli gizlenen kalir", "durum" in birlesik["gizli"])
        s.evaluate("window.erpAsistanOzetYukle()")
        s.wait_for_selector("#erp-asistan-ozet-tablo th", timeout=8000)
        check("is bitmemis and", s.locator("#erp-asistan-ozet-tablo tbody tr").count() == 3)
        baslik = s.evaluate("() => Array.from(document.querySelectorAll('#erp-asistan-ozet-tablo th')).map(th => th.textContent)")
        check("eski tercihte yeni sutun", baslik[:3] == ["Müşteri", "Toplam borç", "Geciken ay"])
        check("gorusen varsayilan gizli degil kayitta", "Görüşen Kişi" in "".join(baslik))
        ipucu = s.evaluate("() => document.querySelector('th[data-kolon=toplam_borc]').title")
        ipucu_ay = s.evaluate("() => document.querySelector('th[data-kolon=geciken_ay]').title")
        check("baslik ipucu borc", "Ekstredeki ek fatura" in ipucu)
        check("baslik ipucu ay", "dondurulan ve ödenmiş" in ipucu_ay)
        s.click("#erp-asistan-ozet-hepsi")
        s.click("th[data-kolon=toplam_borc]")
        sirali = s.evaluate(
            """() => Array.from(document.querySelectorAll('#erp-asistan-ozet-tablo tbody tr')).map(tr => {
                var h = Array.from(tr.parentElement.parentElement.tHead.rows[0].cells).map(c => c.getAttribute('data-kolon'));
                var i = h.indexOf('toplam_borc');
                return (tr.cells[i] || {}).textContent || '';
            })"""
        )
        check("borc sirasi buyukten", sirali and sirali[0].replace(".", "").startswith("1") and sirali[-1] == "—")
        hucre = s.evaluate(
            """() => {
                var h = Array.from(document.querySelectorAll('#erp-asistan-ozet-tablo th')).map(c => c.getAttribute('data-kolon'));
                var i = h.indexOf('toplam_borc');
                var cells = Array.from(document.querySelectorAll('#erp-asistan-ozet-tablo tbody tr')).map(tr => tr.cells[i]);
                var bos = cells.find(td => td.textContent === '' && td.className.indexOf('ea-sayi-bos') >= 0);
                var tire = cells.find(td => td.textContent === '—');
                return {bos: !!bos, tire: tire ? tire.title : ''};
            }"""
        )
        check("sifir borc bos", hucre["bos"] is True)
        check("hesaplanmadi ipucu", hucre["tire"] == "hesaplanmadı")
        aciklama = s.locator("tr[data-musteri-id='867'] td[data-kolon=aciklama]")
        check("aciklama ipucu", aciklama.get_attribute("title") == "Tüm geçmişi gör")
        check("aciklama imlec", s.evaluate(
            "() => getComputedStyle(document.querySelector(\"tr[data-musteri-id='867'] td[data-kolon=aciklama]\")).cursor"
        ) == "pointer")
        s.set_viewport_size({"width": 390, "height": 700})
        aciklama.click()
        s.wait_for_selector("#erp-asistan-gecmis-pop .ea-gecmis-kayit")
        pop = s.evaluate(
            """() => {
                var p = document.getElementById('erp-asistan-gecmis-pop');
                var kart = p.querySelector('.ea-gecmis-kart');
                var metin = p.innerText;
                return {
                    b: p.querySelectorAll('b,img,script').length,
                    etiket: metin.indexOf('<b>kalin</b>') >= 0,
                    satir: metin.indexOf('satir2') >= 0,
                    sira: metin.indexOf('03.10.2026 18:00') < metin.indexOf('01.10.2026 10:00'),
                    baska: metin.indexOf('baska-musteri-notu') >= 0,
                    tel: metin.indexOf('5550001111') >= 0,
                    wa: metin.indexOf('gizli-wa-icerik') >= 0,
                    pre: getComputedStyle(p.querySelector('.ea-gecmis-metin')).whiteSpace,
                    sigar: kart.scrollWidth <= kart.clientWidth + 2,
                    eskiPop: !!document.getElementById('erp-asistan-ozet-gecmis'),
                    duzen: p.querySelectorAll('textarea,input,select').length
                };
            }"""
        )
        check("popup acildi", pop["etiket"] and pop["satir"] and pop["sira"])
        check("html kacis", pop["b"] == 0 and pop["etiket"])
        check("baska musteri yok", pop["baska"] is False and pop["tel"] is False and pop["wa"] is False)
        check("satir sonu", pop["pre"] == "pre-wrap")
        check("dar ekran", pop["sigar"] is True)
        check("salt okuma", pop["duzen"] == 0 and pop["eskiPop"] is False)
        check("tek istek", len(gecmis_istek) == 1 and "/musteri/867/" in gecmis_istek[0] and "ofset=0" in gecmis_istek[0])
        s.set_viewport_size({"width": 1100, "height": 700})
        s.locator("#erp-asistan-gecmis-pop .ea-gecmis-metin").evaluate_all(
            "els => els.forEach(el => { el.style.filter = 'blur(8px)'; })"
        )
        s.locator("#erp-asistan-gecmis-pop h3").evaluate("el => { el.style.filter = 'blur(8px)'; }")
        gecmis_png = os.path.join(os.environ.get("TEMP", "."), "erp_asistan_gecmis.png")
        s.locator("#erp-asistan-gecmis-pop .ea-gecmis-kart").screenshot(path=gecmis_png)
        check("gecmis ekran", os.path.isfile(gecmis_png))
        print("ekran " + gecmis_png)
        s.click("#erp-asistan-gecmis-pop .ea-gecmis-daha")
        s.wait_for_function("() => (document.getElementById('erp-asistan-gecmis-pop') || {}).innerText.indexOf('eski-kayit') >= 0")
        check("daha eski", len(gecmis_istek) == 2 and "ofset=50" in gecmis_istek[1] and "/musteri/867/" in gecmis_istek[1])
        s.click("#erp-asistan-gecmis-pop .ea-gecmis-x")
        check("x kapatir", s.locator("#erp-asistan-gecmis-pop").count() == 0)
        aciklama.click()
        s.wait_for_selector("#erp-asistan-gecmis-pop")
        s.keyboard.press("Escape")
        check("esc kapatir", s.locator("#erp-asistan-gecmis-pop").count() == 0)
        aciklama.click()
        s.wait_for_selector("#erp-asistan-gecmis-pop")
        s.locator("#erp-asistan-gecmis-pop").click(position={"x": 2, "y": 2})
        check("disari kapatir", s.locator("#erp-asistan-gecmis-pop").count() == 0)
        s.click("#erp-asistan-sutunlar")
        s.wait_for_selector("#erp-asistan-sutun-menu:not([hidden])")
        s.evaluate(
            """() => {
                var k = document.querySelector('[data-sutun-goster="gorusme"]');
                k.click();
            }"""
        )
        gizli = s.evaluate("() => document.querySelector('th[data-kolon=gorusme]')")
        check("sutun gizlendi", gizli is None)
        kayit = s.evaluate("() => JSON.parse(localStorage.getItem('asistan_sutunlar_41'))")
        diger = s.evaluate("() => localStorage.getItem('asistan_sutunlar_99')")
        check("tercih 41 yazildi", "gorusme" in (kayit.get("gizli") or []))
        check("diger kullanici ayni", '"musteri"' in diger and "gorusme" not in json.loads(diger).get("gizli", []))
        s.click("#erp-asistan-sutun-varsayilan")
        baslik2 = s.evaluate("() => Array.from(document.querySelectorAll('#erp-asistan-ozet-tablo th')).map(th => th.getAttribute('data-kolon'))")
        check("varsayilan sira", baslik2[:3] == ["musteri", "toplam_borc", "geciken_ay"])
        check("varsayilan gizli", "gorusen" not in baslik2 and "gorusulen" not in baslik2)
        s.click("[data-sutun=geciken_ay][data-sutun-yon='1']")
        kayit2 = s.evaluate("() => JSON.parse(localStorage.getItem('asistan_sutunlar_41')).sira")
        check("ok ile sira", kayit2.index("geciken_ay") > kayit2.index("gorusme"))
        check("ozet sunucuya yazmaz", not any(x.startswith("POST") for x in kolon_istek))
        s.evaluate(
            """() => {
                var menu = document.getElementById('erp-asistan-sutun-menu');
                if (menu) menu.hidden = true;
                document.querySelectorAll('#erp-asistan-ozet-tablo tbody td').forEach(function (td) {
                    td.style.filter = 'blur(8px)';
                });
            }"""
        )
        yol = os.path.join(os.environ.get("TEMP", "."), "erp_asistan_ozet.png")
        s.locator("#erp-asistan-ozet-tablo").screenshot(path=yol)
        check("ekran", os.path.isfile(yol))
        print("ekran " + yol)
        s.goto("http://asistan.test/asistan-test", wait_until="load")
        s.evaluate("window.erpAsistanOzetYukle()")
        s.wait_for_selector("#erp-asistan-ozet-tablo th")
        kalici = s.evaluate("() => Array.from(document.querySelectorAll('#erp-asistan-ozet-tablo th')).map(th => th.getAttribute('data-kolon'))")
        check("sira kalici", kalici.index("geciken_ay") > kalici.index("gorusme") if "gorusme" in kalici else False)
        s.goto("http://asistan.test/asistan-test-b", wait_until="load")
        s.evaluate("window.erpAsistanOzetYukle()")
        s.wait_for_selector("#erp-asistan-ozet-tablo th")
        b99 = s.evaluate("() => Array.from(document.querySelectorAll('#erp-asistan-ozet-tablo th')).map(th => th.getAttribute('data-kolon'))")
        check("kullanici 99 ayri", b99[0] == "durum" and "musteri" not in b99)
        s.goto("http://asistan.test/asistan-test", wait_until="load")
        s.evaluate("window.erpAsistanOzetYukle()")
        s.wait_for_selector("#erp-asistan-ozet-tarih")
        gorusme = s.evaluate(
            """() => Array.from(document.querySelectorAll('#erp-asistan-ozet-tarih option')).map(o => o.value)"""
        )
        check("gorusme secenek ayni", gorusme == ["hepsi", "bugun", "dun", "bu_hafta", "bu_ay", "son_7", "son_30", "ozel"])
        s.select_option("#erp-asistan-ozet-tur", "hatirlatma")
        s.wait_for_function("() => document.querySelector('#erp-asistan-ozet-tarih').value === 'hepsi'")
        hat = s.evaluate(
            """() => Array.from(document.querySelectorAll('#erp-asistan-ozet-tarih option')).map(o => ({v:o.value, t:o.title}))"""
        )
        check("hatirlatma secenek", [o["v"] for o in hat] == [
            "hepsi", "bugun", "yarin", "sonraki_hafta", "sonraki_ay", "sonraki_30", "ozel"])
        basliklar = {o["v"]: o["t"] for o in hat}
        check("hatirlatma ipucu", basliklar.get("yarin") == "Yarının tamamı"
              and "Pazartesi" in basliklar.get("sonraki_hafta", "")
              and "30 gün" in basliklar.get("sonraki_30", "")
              and "dahil" in basliklar.get("ozel", ""))
        s.select_option("#erp-asistan-ozet-tarih", "ozel")
        s.fill("#erp-asistan-ozet-bas", "2026-03-01")
        s.fill("#erp-asistan-ozet-bit", "2026-03-02")
        s.select_option("#erp-asistan-ozet-tur", "gorusme")
        s.wait_for_function("() => document.querySelector('#erp-asistan-ozet-tarih').value === 'hepsi'")
        temiz = s.evaluate(
            """() => ({
                bas: document.querySelector('#erp-asistan-ozet-bas').value,
                bit: document.querySelector('#erp-asistan-ozet-bit').value,
                gizli: document.querySelector('#erp-asistan-ozet-ozel').hidden
            })"""
        )
        check("tur degisince sifir", temiz["bas"] == "" and temiz["bit"] == "" and temiz["gizli"] is True)
        s.select_option("#erp-asistan-ozet-tur", "hatirlatma")
        s.select_option("#erp-asistan-ozet-tarih", "yarin")
        for _ in range(20):
            if any("tarih_turu=hatirlatma" in u and "tarih=yarin" in u for u in ozet_istek):
                break
            s.wait_for_timeout(100)
        check("yarin istegi", any("tarih_turu=hatirlatma" in u and "tarih=yarin" in u for u in ozet_istek))
        s.evaluate(
            """() => {
                document.querySelectorAll('#erp-asistan-ozet-tablo td').forEach(function (td) {
                    td.style.filter = 'blur(8px)';
                });
            }"""
        )
        tarih_png = os.path.join(os.environ.get("TEMP", "."), "erp_asistan_hatirlatma_tarih.png")
        s.locator(".ea-ozet-arac").screenshot(path=tarih_png)
        check("tarih ekran", os.path.isfile(tarih_png))
        print("ekran " + tarih_png)
        s.evaluate(
            """() => sessionStorage.setItem('asistan_ozet_tarih_41',
               JSON.stringify({turu:'hatirlatma', sec:'dun', bas:'2026-01-01', bit:'2026-02-01'}))"""
        )
        s.goto("http://asistan.test/asistan-test", wait_until="load")
        s.evaluate("window.erpAsistanOzetYukle()")
        s.wait_for_selector("#erp-asistan-ozet-tarih")
        gecersiz = s.evaluate(
            """() => ({
                tur: document.querySelector('#erp-asistan-ozet-tur').value,
                sec: document.querySelector('#erp-asistan-ozet-tarih').value,
                bas: document.querySelector('#erp-asistan-ozet-bas').value
            })"""
        )
        check("eski secim hepsi", gecersiz["tur"] == "hatirlatma" and gecersiz["sec"] == "hepsi" and gecersiz["bas"] == "")
        s.goto("http://asistan.test/asistan-test", wait_until="load")
        s.evaluate("window.erpAsistanOzetYukle()")
        s.wait_for_selector("tr[data-musteri-id='867'] .ea-wa")
        pasif = s.locator("tr[data-musteri-id='900'] .ea-wa")
        check("numarasiz pasif", pasif.get_attribute("disabled") is not None and pasif.get_attribute("title") == "Telefon yok")
        s.click("tr[data-musteri-id='867'] .ea-wa")
        s.wait_for_selector("#erp-asistan-wa-pop textarea")
        check("wa maske", s.locator(".ea-wa-maske").inner_text() == "Numara **11")
        check("panel baslik", "Gecikme hatırlatma" in s.locator("#erp-asistan-wa-liste").inner_text() and "Ödeme linki" in s.locator("#erp-asistan-wa-liste").inner_text() and "Tahsilat bilgilendirme" in s.locator("#erp-asistan-wa-liste").inner_text())
        check("oneri secili", s.locator("#erp-asistan-wa-sec").input_value() == "0" and s.locator("#erp-asistan-wa-metin").input_value() == "sahte vade")
        s.click("[data-wa-anahtar='3']")
        check("panel editor", s.locator("#erp-asistan-wa-metin").input_value() == "sahte uc" and s.locator("#erp-asistan-wa-sec").input_value() == "3")
        check("panel gondermez", wa_post["n"] == 0)
        s.fill("#erp-asistan-wa-metin", "sahte uc degisti")
        s.click("[data-wa-anahtar='0']")
        check("duzenleme onayi", s.locator("#erp-asistan-wa-gecis").is_visible() and s.locator("#erp-asistan-wa-metin").input_value() == "sahte uc degisti")
        s.click("#erp-asistan-wa-gecis-evet")
        check("gecis oldu", s.locator("#erp-asistan-wa-metin").input_value() == "sahte vade")
        check("link pasif dugme", s.locator("[data-wa-anahtar='link']").is_disabled())
        s.set_viewport_size({"width": 390, "height": 700})
        yon = s.locator(".ea-wa-govde").evaluate("el => getComputedStyle(el).flexDirection")
        check("dar ekran panel", yon == "column")
        s.set_viewport_size({"width": 1100, "height": 700})
        s.select_option("#erp-asistan-wa-sec", "ozel")
        check("ozel bos", s.locator("#erp-asistan-wa-gonder").is_disabled())
        s.fill("#erp-asistan-wa-metin", "sahte metin")
        s.click("#erp-asistan-wa-gonder")
        s.evaluate("""() => {
          var b = document.querySelector('#erp-asistan-wa-onay');
          b.click();
          b.click();
        }""")
        s.wait_for_function("() => document.querySelector('.ea-wa-sonuc').textContent.indexOf('Kuyruğa') >= 0")
        check("cift onay tek", wa_post["n"] == 1, str(wa_post["n"]))
        s.locator("#erp-asistan-wa-metin").evaluate("el => { el.style.filter = 'blur(8px)'; }")
        s.locator("#erp-asistan-wa-liste").evaluate("el => { el.style.filter = 'blur(8px)'; }")
        s.locator("#erp-asistan-wa-pop h3").evaluate("el => { el.style.filter = 'blur(8px)'; }")
        wa_png = os.path.join(os.environ.get("TEMP", "."), "erp_asistan_wa.png")
        s.locator("#erp-asistan-wa-pop .ea-wa-kart").screenshot(path=wa_png)
        check("wa ekran", os.path.isfile(wa_png))
        print("ekran " + wa_png)
        tarayici.close()


if __name__ == "__main__":
    test_grid_sayilari()
    test_kaynak_yazmaz()
    test_gecmis_sayfa()
    test_gorunurluk_liste_popup()
    test_hatirlatma_pencereleri()
    test_asistan_wa()
    test_wa_kapi()
    test_wa_panel()
    arayuz()
    if FAILS:
        print("FAIL", len(FAILS))
        raise SystemExit(1)
    print("erp_asistan_ozet ok")
