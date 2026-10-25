# -*- coding: utf-8 -*-
"""Toplu mesaj kampanyası. Canlı yazma yok. Çıktıda kimlik ve metin yok.

Tek tek: python test_wa_kampanya.py test_adi
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)
os.environ["WA_TOPLU_KAMPANYA"] = "0"

import wa_kampanya as wa

FAILS: list[str] = []


def check(name, ok):
    print(("ok " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def _an(saat, dakika=0):
    return __import__("datetime").datetime(2026, 10, 25, saat, dakika, tzinfo=wa.IST)


def _tel(n):
    return f"9055500{int(n):05d}"


def _model(adet=1, seri=0, bugun=0, tavan=150, sablon="Merhaba {yetkili}", yetkili="Kisi"):
    bekleyen = []
    for i in range(adet):
        tel = _tel(i + 1)
        bekleyen.append({
            "id": i + 1,
            "mid": 100 + i,
            "tel": tel,
            "hash": wa.numara_ozet(wa.norm_telefon(tel)),
            "yetkili": yetkili if i == 0 else "Diger",
            "durum": "bekliyor",
        })
    return {
        "bayrak": True,
        "kampanya": {
            "id": 7, "durum": "calisiyor", "sablon": sablon,
            "tavan": tavan, "seri": seri,
        },
        "bugun": bugun,
        "ucus": None,
        "bekleyen": bekleyen,
        "giden_hash": [],
        "kuyruk_cagri": 0,
        "dokunulan": [],
    }


def _kuyruk(donus="kuyrukta"):
    kutu = {"n": 0, "anahtar": "", "gizli": False}

    def fn(tel, mesaj, anahtar):
        kutu["n"] += 1
        kutu["anahtar"] = anahtar
        kutu["gizli"] = "GIZLI_METIN" in str(mesaj or "")
        return {"durum": donus}

    return fn, kutu


def _yok(_anahtar):
    return {"durum": "yok"}


def test_aday_suzgec():
    sablon = "Merhaba {yetkili}"
    rows = [
        {"mid": 1, "durum": "aktif", "haric": False, "tel": "05550001122", "yetkili": "A"},
        {"mid": 2, "durum": "pasif", "haric": False, "tel": "05550001123", "yetkili": "B"},
        {"mid": 3, "durum": "aktif", "haric": True, "tel": "05550001124", "yetkili": "C"},
        {"mid": 4, "durum": "aktif", "haric": False, "tel": "", "yetkili": "D"},
        {"mid": 5, "durum": "aktif", "haric": False, "tel": "05550001125", "yetkili": ""},
        {"mid": 2, "durum": "aktif", "haric": False, "tel": "05550001126", "yetkili": "B"},
    ]
    s = wa.aday_sinifla(rows, sablon)
    check("aktif pasif haric", s["alici"] == 2 and s["pasif"] == 1 and s["haric"] == 1
          and s["numarasiz"] == 1 and s["yedek"] == 1)
    govde = wa.onizleme_govde({**s, **wa.sure_tahmin(s["alici"], 150), "sablon": sablon, "isim": "X"})
    check("onizleme yalniz sayi", set(govde) == set(wa._ONIZLEME_ALAN) and "sablon" not in govde)


def test_mid_mukerrer():
    rows = [
        {"mid": 9, "durum": "aktif", "haric": False, "tel": "05550002221", "yetkili": "A"},
        {"mid": 9, "durum": "aktif", "haric": False, "tel": "05550002222", "yetkili": "A"},
    ]
    s = wa.aday_sinifla(rows, "Merhaba")
    check("mid tek", s["alici"] == 1)
    tablo = []
    ilk = s["uygun"][0]
    check("ekle", wa.alici_ekle(tablo, ilk) == "tamam")
    check("mid engel", wa.alici_ekle(tablo, dict(ilk)) == "mid")


def test_numara_mukerrer():
    rows = [
        {"mid": 1, "durum": "aktif", "haric": False, "tel": "05550003333", "yetkili": "A"},
        {"mid": 2, "durum": "aktif", "haric": False, "tel": "905550003333", "yetkili": "B"},
    ]
    s = wa.aday_sinifla(rows, "Merhaba")
    check("ayni numara tek", s["alici"] == 1)
    tablo = [dict(s["uygun"][0])]
    diger = {"mid": 2, "hash": tablo[0]["hash"]}
    check("numara engel", wa.alici_ekle(tablo, diger) == "numara")
    fn, kutu = _kuyruk()
    m = _model(1)
    m["bekleyen"] = [{
        "id": 1, "mid": 1, "tel": "05550003333",
        "hash": tablo[0]["hash"], "yetkili": "A", "durum": "bekliyor",
    }]
    m["giden_hash"] = [tablo[0]["hash"]]
    sonuc = wa.tur_uygula(m, fn, _yok, 0.9, _an(10))
    check("ikinci gonderim yok", sonuc["eylem"] == "mukerrer" and kutu["n"] == 0)


def test_pencere_ve_tavan():
    fn, kutu = _kuyruk()
    m = _model(2)
    check("gece", wa.tur_uygula(m, fn, _yok, 0.9, _an(8, 59))["eylem"] == "pencere")
    check("kapanis", wa.tur_uygula(m, fn, _yok, 0.9, _an(20))["eylem"] == "pencere")
    check("sabah", wa.pencere_icinde(_an(9)) and wa.pencere_icinde(_an(19, 59)))
    m["bugun"] = 150
    m["kampanya"]["tavan"] = 150
    check("tavan", wa.tur_uygula(m, fn, _yok, 0.9, _an(10))["eylem"] == "tavan")
    check("kesildi", kutu["n"] == 0 and len(m["bekleyen"]) == 2)


def test_uc_hata_durdurur():
    fn, kutu = _kuyruk("hata")
    m = _model(4, seri=0)
    for _ in range(3):
        wa.tur_uygula(m, fn, _yok, 0.9, _an(10))
    check("durdu", m["kampanya"]["durum"] == "durduruldu" and m["kampanya"]["seri"] == 3)
    wa.tur_uygula(m, fn, _yok, 0.9, _an(10))
    check("dorduncu yok", kutu["n"] == 3 and m["kampanya"]["durum"] == "durduruldu")


def test_gecersiz_seri():
    fn, kutu = _kuyruk("hata")
    m = _model(1, seri=2)
    m["bekleyen"][0]["tel"] = "abc"
    sonuc = wa.tur_uygula(m, fn, _yok, 0.9, _an(10))
    check("seri ayni", sonuc["eylem"] == "gecersiz" and m["kampanya"]["seri"] == 2
          and m["kampanya"]["durum"] == "calisiyor" and kutu["n"] == 0)


def test_belirsiz_tekrar_yok():
    fn, kutu = _kuyruk("belirsiz")
    m = _model(1, seri=1)
    sonuc = wa.tur_uygula(m, fn, _yok, 0.9, _an(10))
    check("belirsiz seri", sonuc["eylem"] == "belirsiz" and m["kampanya"]["seri"] == 1
          and m["kampanya"]["durum"] == "calisiyor" and kutu["n"] == 1)
    ayni = {
        "id": 1, "mid": m["dokunulan"][0]["mid"], "tel": _tel(1),
        "hash": m["giden_hash"][0], "yetkili": "Kisi", "durum": "bekliyor",
    }
    m["bekleyen"] = [ayni]
    ikinci = wa.tur_uygula(m, fn, _yok, 0.9, _an(10))
    check("tekrar yok", ikinci["eylem"] == "mukerrer" and kutu["n"] == 1)
    eski = _an(10) - timedelta(hours=3)
    m2 = _model(0, seri=0)
    m2["ucus"] = {
        "id": 4, "mid": 4, "durum": "kuyrukta", "anahtar": wa.yeni_anahtar(),
        "hash": "hazir", "son_deneme_at": eski,
    }
    fn2, kutu2 = _kuyruk()
    karar = wa.tur_uygula(m2, fn2, _yok, 0.9, _an(12))
    check("sure asimi belirsiz", karar["eylem"] == "belirsiz" and m2["kampanya"]["seri"] == 0
          and kutu2["n"] == 0 and m2["ucus"] is None)


def test_test_olmadan_baslamaz():
    check("test yok", wa.baslat_izin(False, False) == "test_yok")
    check("acik var", wa.baslat_izin(True, True) == "acik_var")
    check("test tamam", wa.baslat_izin(True, False) == "tamam")
    fn, kutu = _kuyruk("gonderildi")
    paket = wa.test_gonder("05551110022", "Merhaba {yetkili}", fn)
    check("test anahtar", paket.get("test_ok") is True and wa.anahtar_gecerli(paket.get("anahtar"))
          and "telefon" not in paket and "mesaj" not in paket and kutu["n"] == 1)
    check("anahtar bicimi", str(paket.get("anahtar") or "").startswith("szwa:d:"))


def test_bayrak_kapali_job():
    os.environ["WA_TOPLU_KAMPANYA"] = "0"

    def patla(*_a, **_k):
        raise RuntimeError("cagrildi")

    try:
        sonuc = wa.run_wa_kampanya_job(semalar_fn=patla, tur=patla)
        check("job noop", sonuc.get("atlandi") == "bayrak" and wa.bayrak_acik() is False)
    finally:
        os.environ["WA_TOPLU_KAMPANYA"] = "0"


def test_rol_403():
    from flask import Flask
    from auth import User, login_manager
    from routes import whatsapp_routes

    rol = {"v": "personel"}
    os.environ["WA_TOPLU_KAMPANYA"] = "1"

    @login_manager.user_loader
    def _load(_uid):
        return User(id=1, username="t", full_name="T", role=rol["v"],
                    aktif_mi=True, security_stamp="test-stamp")

    app = Flask(__name__)
    app.secret_key = "test"
    login_manager.init_app(app)

    @app.route("/")
    def index():
        return "home"

    app.register_blueprint(whatsapp_routes.bp)
    client = app.test_client()
    with client.session_transaction() as s:
        s["_user_id"] = "1"
        s["_fresh"] = True
    uclar = [
        ("GET", "/whatsapp/api/toplu-durum"),
        ("POST", "/whatsapp/api/toplu-taslak"),
        ("POST", "/whatsapp/api/toplu-onizleme"),
        ("POST", "/whatsapp/api/toplu-test"),
        ("GET", "/whatsapp/api/toplu-test-sonuc"),
        ("POST", "/whatsapp/api/toplu-baslat"),
        ("POST", "/whatsapp/api/toplu-durdur"),
        ("POST", "/whatsapp/api/toplu-bitir"),
        ("POST", "/whatsapp/api/toplu-devam"),
        ("GET", "/whatsapp/api/toplu-haric"),
        ("POST", "/whatsapp/api/toplu-haric"),
        ("GET", "/whatsapp/api/toplu-liste"),
    ]
    try:
        for ad in ("personel", "muhasebe"):
            rol["v"] = ad
            kodlar = []
            for yontem, url in uclar:
                r = client.open(url, method=yontem, json={})
                kodlar.append(r.status_code)
            check(ad + " 403", kodlar == [403] * len(uclar))
        rol["v"] = "admin"
        os.environ["WA_TOPLU_KAMPANYA"] = "0"
        r = client.get("/whatsapp/api/toplu-durum")
        check("bayrak 404", r.status_code == 404)
        r = client.post("/whatsapp/api/toplu-baslat", json={"id": 1})
        check("bayrak baslat 404", r.status_code == 404)
        r = client.get("/whatsapp/api/toplu-liste")
        check("bayrak liste 404", r.status_code == 404 and "no-store" not in (r.headers.get("Cache-Control") or ""))
    finally:
        os.environ["WA_TOPLU_KAMPANYA"] = "0"


def test_sema_karismaz():
    os.environ["WA_TOPLU_KAMPANYA"] = "1"
    gorulen = {"public": [], "tenant_a": []}

    def tur(sch, rastgele):
        anahtar = sch or "public"

        def kuyruk(tel, _mesaj, _anahtar):
            gorulen[anahtar].append(tel[-4:])
            return {"durum": "kuyrukta"}

        m = _model(1)
        m["bekleyen"][0]["tel"] = _tel(1 if anahtar == "public" else 2)
        m["bekleyen"][0]["mid"] = 1 if anahtar == "public" else 2
        wa.tur_uygula(m, kuyruk, _yok, rastgele, _an(11))

    try:
        wa.run_wa_kampanya_job(
            semalar_fn=lambda: [None, "tenant_a"],
            tur=tur,
            rastgele_fn=lambda: 0.9,
        )
        check(
            "sema ayri",
            gorulen["public"] != gorulen["tenant_a"]
            and len(gorulen["public"]) == 1
            and len(gorulen["tenant_a"]) == 1,
        )
        semalar = wa.sema_listesi([
            {"schema_name": "tenant_a"},
            {"schema_name": "public"},
            {"schema_name": "diger"},
            {"schema_name": "Tenant_Buyuk"},
        ])
        check("sema liste", semalar == [None, "tenant_a"])
    finally:
        os.environ["WA_TOPLU_KAMPANYA"] = "0"


def test_turda_bir_is():
    fn, kutu = _kuyruk("kuyrukta")
    m = _model(5)
    ilk = wa.tur_uygula(m, fn, _yok, 0.9, _an(10))
    ikinci = wa.tur_uygula(m, fn, _yok, 0.9, _an(10, 1))
    atla = wa.tur_uygula(_model(3), fn, _yok, 0.1, _an(10))
    check("bir is", ilk["eylem"] == "kuyrukta" and ikinci["eylem"] == "bekle_sonuc"
          and kutu["n"] == 1 and len(m["bekleyen"]) == 4 and atla["eylem"] == "atla")
    check("anahtar tek", wa.anahtar_gecerli(kutu["anahtar"]))


def test_saglik_kart_sayi():
    import wa_saglik

    dolu = {"kampanya_acik": 2, "kampanya_bekleyen": 4, "kampanya_durdurulan": 1}
    kartlar = wa_saglik.kontrolleri_uret("ok", "hazir", dolu)
    kart = next((k for k in kartlar if k.get("anahtar") == "kampanya"), {})
    check("kart sayi", kart.get("aciklama") == "Açık 2. Bekleyen 4. Durdurulan 1."
          and kart.get("seviye") == "ok" and "kayit" not in kart)
    bos = wa_saglik.kontrolleri_uret("ok", "hazir", {"bekleyen": 0})
    check("bayraksiz kart yok", all(k.get("anahtar") != "kampanya" for k in bos))
    src = (ROOT / "wa_saglik.py").read_text(encoding="utf-8")
    fn = src[src.find("def rozet_seviye"): src.find("def kontrolleri_uret")]
    check("rozet ayni", "kampanya" not in fn)


def test_log_maske():
    kayit = logging.getLogger("wa_kampanya")
    satirlar = []

    class _H(logging.Handler):
        def emit(self, record):
            satirlar.append(record.getMessage())

    handler = _H()
    kayit.addHandler(handler)
    kayit.setLevel(logging.INFO)
    fn, kutu = _kuyruk("kuyrukta")
    m = _model(1, sablon="GIZLI_METIN {yetkili}")
    try:
        wa.tur_uygula(m, fn, _yok, 0.9, _an(10))
        wa.guvenli_log("deneme", telefon=_tel(1), mesaj="GIZLI_METIN", maske=_tel(1))
        birlesik = "\n".join(satirlar)
        check("log temiz", "GIZLI_METIN" not in birlesik and _tel(1) not in birlesik
              and "***" in birlesik and kutu["gizli"] is True)
    finally:
        kayit.removeHandler(handler)


def test_giris_secili_toolbar_whatsapp():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    i = html.find('id="giris-secili-musteri-toolbar"')
    j = html.find('<div class="giris-container">', i)
    cubuk = html[i:j]
    check(
        "cubuk dugme",
        i > 0 and j > i
        and 'class="giris-btn-whatsapp"' in cubuk
        and 'onclick="girisMusteriWhatsAppAc()"' in cubuk
        and "function girisMusteriWhatsAppAc" in html
        and ".giris-secili-toolbar .giris-btn-whatsapp" in html,
    )


def test_giris_butonu_ayni():
    giris = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    liste = (ROOT / "templates" / "musteriler" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "wa-toplu.js").read_text(encoding="utf-8")
    modul = (ROOT / "wa_kampanya.py").read_text(encoding="utf-8")
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    kart = modul[modul.find("def kart_telefon"): modul.find("def _kuyruk_bir")]
    check("giris fn", "function girisMusteriWhatsAppAc" in giris and "wa-toplu.js" in giris and "wa_toplu_kampanya" in giris)
    check("gecikme uc", "function whatsappSeciliGonder" in giris and "/whatsapp/api/gonder" in giris)
    check("liste buton", 'id="btn-toplu-mesaj"' in giris and "wa_toplu_kampanya" in giris and "wa_toplu_kampanya" in liste and "wt-haric" in liste)
    check("tel2 yok", "tel2" not in kart and "whatsapp_geciken_haric" not in modul)
    check("js metin yok", "GIZLI_METIN" not in js and "data-phone" not in js)
    i = app.find('id="wa_toplu_kampanya"')
    parca = app[max(0, i - 500): i + 40]
    check("job tek", i > 0 and "max_instances=1" in parca and "minutes=1" in parca)


def test_bitir_kilit():
    k = {"durum": "durduruldu"}
    r = wa.bitir_uygula(k, [])
    check("kilit kalkti", r.get("ok") is True and k["durum"] == "bitti" and not wa.kilit_acik(k["durum"]))
    check("yeni acilir", wa.baslat_izin(True, wa.kilit_acik(k["durum"])) == "tamam")
    t = {"durum": "test_bekliyor"}
    wa.bitir_uygula(t, [])
    check("test kilit", t["durum"] == "bitti" and not wa.kilit_acik(t["durum"]))
    js = (ROOT / "static" / "js" / "wa-toplu.js").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("onay sayi", 'Bekleyen " + n + " alıcı iptal edilecek' in js and 'id="wtBitir"' in html)


def test_bitir_calisiyor_red():
    k = {"durum": "calisiyor"}
    satirlar = [{"durum": "bekliyor"}, {"durum": "gonderildi"}]
    r = wa.bitir_uygula(k, satirlar)
    check(
        "calisiyor red",
        r.get("ok") is False and r.get("neden") == "durum"
        and k["durum"] == "calisiyor" and satirlar[0]["durum"] == "bekliyor"
        and satirlar[1]["durum"] == "gonderildi",
    )


def test_bitir_alici():
    k = {"durum": "taslak"}
    satirlar = [
        {"durum": "bekliyor"},
        {"durum": "bekliyor"},
        {"durum": "gonderildi"},
        {"durum": "belirsiz"},
    ]
    r = wa.bitir_uygula(k, satirlar)
    check(
        "iptal ve koru",
        r.get("iptal") == 2 and k["durum"] == "bitti"
        and satirlar[0]["durum"] == "iptal" and satirlar[1]["durum"] == "iptal"
        and satirlar[2]["durum"] == "gonderildi" and satirlar[3]["durum"] == "belirsiz",
    )
    modul = (ROOT / "wa_kampanya.py").read_text(encoding="utf-8")
    govde = modul[modul.find("def bitir_istegi"): modul.find("def devam_istegi")]
    check("sql silmez", "DELETE" not in govde and "durum = 'iptal'" in govde and "bekliyor" in govde)


def test_bitir_rol():
    from flask import Flask
    from auth import User, login_manager
    from routes import whatsapp_routes

    rol = {"v": "personel"}
    os.environ["WA_TOPLU_KAMPANYA"] = "1"

    @login_manager.user_loader
    def _load(_uid):
        return User(id=1, username="t", full_name="T", role=rol["v"],
                    aktif_mi=True, security_stamp="test-stamp")

    app = Flask(__name__)
    app.secret_key = "test"
    login_manager.init_app(app)

    @app.route("/")
    def index():
        return "home"

    app.register_blueprint(whatsapp_routes.bp)
    client = app.test_client()
    with client.session_transaction() as s:
        s["_user_id"] = "1"
        s["_fresh"] = True
    try:
        for ad in ("personel", "muhasebe"):
            rol["v"] = ad
            r = client.post("/whatsapp/api/toplu-bitir", json={"id": 1})
            check(ad + " bitir 403", r.status_code == 403)
        rol["v"] = "admin"
        os.environ["WA_TOPLU_KAMPANYA"] = "0"
        r = client.post("/whatsapp/api/toplu-bitir", json={"id": 1})
        check("bitir bayrak 404", r.status_code == 404)
    finally:
        os.environ["WA_TOPLU_KAMPANYA"] = "0"


def _kuyruk_raise(exc):
    import requests

    eski = requests.post

    def _post(*_a, **_k):
        raise exc

    requests.post = _post
    try:
        return wa._kuyruk_bir("1", "a", wa.yeni_anahtar())
    finally:
        requests.post = eski


def test_baglanti_deneme():
    import requests

    bag = _kuyruk_raise(requests.exceptions.ConnectionError("yok"))
    zaman = _kuyruk_raise(requests.exceptions.ConnectTimeout("yok"))
    check("baglanti sinif", bag.get("durum") == "baglanti" and zaman.get("durum") == "baglanti")
    n = {"v": 0}

    def kuyruk(_tel, _mesaj, _anahtar):
        n["v"] += 1
        raise requests.exceptions.ConnectionError("yok")

    m = _model(1, seri=0)
    ilk = wa.tur_uygula(m, kuyruk, _yok, 0.9, _an(10))
    ikinci = wa.tur_uygula(m, kuyruk, _yok, 0.9, _an(10))
    ucuncu = wa.tur_uygula(m, kuyruk, _yok, 0.9, _an(10))
    dort = wa.tur_uygula(m, kuyruk, _yok, 0.9, _an(10))
    satir = m["dokunulan"][-1]
    check(
        "uc deneme",
        ilk["eylem"] == "baglanti" and ikinci["eylem"] == "baglanti"
        and satir["durum"] == "basarisiz" and satir["deneme"] == 3
        and m["kampanya"]["seri"] == 3 and m["kampanya"]["durum"] == "durduruldu"
        and ucuncu["eylem"] == "basarisiz" and dort["eylem"] == "yok" and n["v"] == 3,
    )


def test_okuma_zamani_belirsiz():
    import requests

    oku = _kuyruk_raise(requests.exceptions.ReadTimeout("yok"))
    check("okuma belirsiz", oku.get("durum") == "belirsiz" and oku.get("durum") != "baglanti")
    n = {"v": 0}

    def kuyruk(_tel, _mesaj, _anahtar):
        n["v"] += 1
        raise requests.exceptions.ReadTimeout("yok")

    m = _model(1, seri=2)
    sonuc = wa.tur_uygula(m, kuyruk, _yok, 0.9, _an(10))
    check(
        "seri girmez",
        sonuc["eylem"] == "belirsiz" and m["kampanya"]["seri"] == 2
        and m["kampanya"]["durum"] == "calisiyor" and m["dokunulan"][0]["durum"] == "belirsiz",
    )
    ayni = {
        "id": 1, "mid": m["dokunulan"][0]["mid"], "tel": _tel(1),
        "hash": m["giden_hash"][0], "yetkili": "Kisi", "durum": "bekliyor",
    }
    m["bekleyen"] = [ayni]
    ikinci = wa.tur_uygula(m, kuyruk, _yok, 0.9, _an(10))
    check("tekrar yok", ikinci["eylem"] == "mukerrer" and n["v"] == 1 and m["kampanya"]["seri"] == 2)


def _secim_kart(mid, durum="aktif", tel="", haric=False):
    return {
        "mid": mid,
        "durum": durum,
        "haric": haric,
        "tel": tel,
        "yetkili": "",
        "ad": "AdayGizli",
        "adaylar": ([{"telefon": tel, "etiket": "Kart telefonu"}] if tel else []),
    }


def test_secilen_disi():
    rows = [_secim_kart(1, tel=_tel(1)), _secim_kart(2, tel=_tel(2))]
    s = wa.secilen_uygula(rows, [1], "Merhaba")
    check("disari yok", s["alici"] == 1 and [u["mid"] for u in s["uygun"]] == [1] and s["atilan"] == 0)


def test_secilen_atilan():
    rows = [
        _secim_kart(1, durum="pasif", tel=_tel(1)),
        _secim_kart(2, haric=True, tel=_tel(2)),
        _secim_kart(3, tel=""),
    ]
    s = wa.secilen_uygula(rows, [1, 2, 3], "Merhaba")
    check(
        "sunucu atar",
        s["alici"] == 0 and s["atilan"] == 3 and s["pasif"] == 1 and s["haric"] == 1 and s["numarasiz"] == 1,
    )


def test_secilen_tenant():
    rows = [_secim_kart(1, tel=_tel(1))]
    s = wa.secilen_uygula(rows, [1, 999001], "Merhaba")
    check("baska tenant", s["alici"] == 1 and s["atilan"] == 1 and s["uygun"][0]["mid"] == 1)


def test_secilen_mukerrer():
    rows = [_secim_kart(1, tel=_tel(3)), _secim_kart(2, tel=_tel(3))]
    s = wa.secilen_uygula(rows, [1, 2], "Merhaba")
    ekran = wa.liste_derle(rows)
    check("tek numara", s["alici"] == 1 and s["atilan"] == 1 and s["uygun"][0]["mid"] == 1)
    check("rozet", ekran[0]["mukerrer"] is False and ekran[1]["mukerrer"] is True and ekran[1]["sebep"] == "")


def test_liste_log_nostore():
    kayit = logging.getLogger("wa_kampanya")
    satirlar = []

    class _H(logging.Handler):
        def emit(self, record):
            satirlar.append(record.getMessage())

    handler = _H()
    kayit.addHandler(handler)
    kayit.setLevel(logging.INFO)
    try:
        wa.liste_derle([_secim_kart(1, tel=_tel(4))])
        birlesik = "\n".join(satirlar)
        check("liste log", "AdayGizli" not in birlesik and _tel(4) not in birlesik)
    finally:
        kayit.removeHandler(handler)
    check("no-store", wa.liste_yanit_baslik().get("Cache-Control") == "no-store")
    src = (ROOT / "routes" / "whatsapp_routes.py").read_text(encoding="utf-8")
    i = src.find("def api_toplu_liste")
    parca = src[i:src.find("def api_toplu_durum", i)]
    check("yanit baslik", "liste_yanit_baslik" in parca and "guvenli_log" not in parca and "logger" not in parca)


def test_baslik_dugme():
    giris = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    bas = giris.find('<div class="giris-buttons">')
    cubuk = giris.find('id="giris-secili-musteri-toolbar"')
    baslik = giris[bas:cubuk]
    kollar = baslik.split("{% else %}")
    check("baslik acik", len(kollar) == 2 and 'id="btn-toplu-mesaj"' in kollar[0] and "girisMusteriWhatsAppAc" not in kollar[0])
    check("baslik kapali", "girisMusteriWhatsAppAc()" in kollar[1])
    form_i = giris.find("musteri-form-btn-whatsapp", cubuk)
    arac = giris[cubuk:form_i]
    check(
        "cubuk form ayni",
        form_i > cubuk and arac.count("girisMusteriWhatsAppAc()") == 1
        and "girisMusteriWhatsAppAc()" in giris[form_i:form_i + 180],
    )
    js = (ROOT / "static" / "js" / "wa-toplu.js").read_text(encoding="utf-8")
    check("onay metin", "kişiye, yaklaşık " in js and "saatte/" in js and "günde gönderilecek" in js)


def _calistir(adlar):
    for ad in adlar:
        fn = globals().get(ad)
        if not callable(fn):
            check(ad, False)
            continue
        try:
            fn()
        except Exception as e:
            check(ad + " " + type(e).__name__, False)


if __name__ == "__main__":
    secili = [a for a in sys.argv[1:] if a.startswith("test_")]
    if not secili:
        secili = [a for a in globals() if a.startswith("test_")]
    _calistir(secili)
    if FAILS:
        sys.exit(1)
