"""Sözleşmeler WhatsApp. Ağ yok, canlı tablo yok."""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import sozlesme_whatsapp as sw  # noqa: E402

fails = []


def check(name, ok):
    print(("ok " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)


def _noop(*_a, **_k):
    return None


DENEME = "11111111-1111-4111-8111-111111111111"
DENEME_2 = "22222222-2222-4222-8222-222222222222"


def base_patches():
    sw.ensure_tablo = _noop
    sw._musteri = lambda mid: {"id": int(mid)}
    sw._sayilar = lambda *_a, **_k: (0, 0, 0, 0)
    sw._son_gonderim = lambda *_a, **_k: None
    sw._bul = lambda *_a, **_k: None
    sw._durum_yaz = lambda *_a, **_k: None
    sw.node_yolu_acik = lambda: True


def gonder(**kwargs):
    data = {
        "musteri_id": 7,
        "telefon": "05550000000",
        "mesaj": "deneme metin",
        "buton": "ust",
        "onay": False,
        "deneme": DENEME,
    }
    data.update(kwargs.pop("data", {}))
    an = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)
    return sw.sozlesme_wa_isle(
        3,
        data,
        kwargs.get("origin", "https://payafin.com"),
        kwargs.get("host", "payafin.com"),
        simdi=an,
        bagli_fn=kwargs.get("bagli", lambda: True),
        numara_fn=kwargs.get("numara", lambda _t: ("kayitli", "")),
        kuyruk_fn=kwargs.get("kuyruk", lambda *_a: "ok"),
        uyandir_fn=kwargs.get("uyandir"),
    )


def test_sinirlar():
    check("limit_dk", "dakika" in sw.limit_mesaji(10, 0, 0, 0))
    check("limit_kiraci_dk", "kiracı" in sw.limit_mesaji(0, 0, 20, 0))
    check("limit_gun", "Bugünkü gönderim sınırınız" in sw.limit_mesaji(0, 100, 0, 0))
    check("limit_kiraci_gun", "kiracı gönderim sınırı doldu" in sw.limit_mesaji(0, 0, 0, 300))
    check("origin_yok", not sw.origin_uygun("", "payafin.com"))
    check("origin_kotu", not sw.origin_uygun("https://evil.example", "payafin.com"))
    check("origin_iyi", sw.origin_uygun("https://payafin.com", "payafin.com"))
    norm = sw.normalize_wa_telefon("05550000000")
    check("maske", sw.telefon_maske(norm).startswith("***") and norm not in sw.telefon_maske(norm))
    check("hash", len(sw.ozet_hash("abc")) == 64)


def test_red():
    base_patches()
    called = {"musteri": 0, "ekle": 0, "bagli": 0}
    sw._musteri = lambda mid: called.__setitem__("musteri", called["musteri"] + 1) or {"id": mid}
    sw._ekle = lambda *_a, **_k: called.__setitem__("ekle", called["ekle"] + 1) or {"id": 1}
    govde, kod = gonder(origin="https://evil.example", bagli=lambda: called.__setitem__("bagli", 1) or True)
    check("origin_403", kod == 403 and govde.get("mesaj") == sw._ORIGIN and called["musteri"] == 0 and called["bagli"] == 0)
    govde, kod = gonder(data={"musteri_id": 0})
    check("musteri_yok_id", kod == 400)
    sw._musteri = lambda _mid: None
    govde, kod = gonder()
    check("musteri_404", kod == 404)
    base_patches()
    govde, kod = gonder(data={"telefon": "12"})
    check("tel_400", kod == 400 and govde.get("mesaj") == sw._TEL)
    govde, kod = gonder(data={"mesaj": "x" * 1001})
    check("uzun_400", kod == 400)
    sw._sayilar = lambda *_a, **_k: (10, 0, 0, 0)
    govde, kod = gonder()
    check("hiz_429", kod == 429 and "dakika" in govde.get("mesaj", ""))


def test_kiraci_ve_bagli():
    base_patches()
    calls = {"kuyruk": 0, "bagli": 0, "ekle": []}
    sw.node_yolu_acik = lambda: False
    sw._ekle = lambda *a, **_k: calls["ekle"].append(a[3]) or {"id": 1}

    def bagli():
        calls["bagli"] += 1
        return True

    def kuyruk(*_a):
        calls["kuyruk"] += 1
        return "ok"

    govde, kod = gonder(bagli=bagli, kuyruk=kuyruk)
    check("kiraci_geri", kod == 200 and govde.get("geri_dus") is True and calls["bagli"] == 0 and calls["kuyruk"] == 0)
    check("kiraci_kayit", calls["ekle"] == ["geri_kiraci"])
    check("kiraci_metin", "WhatsApp Web sayfası açılıyor" in govde.get("mesaj", ""))

    base_patches()
    calls = {"kuyruk": 0, "bagli": 0}
    sw._ekle = lambda *a, **_k: {"id": 1}

    def bagli2():
        calls["bagli"] += 1
        return False

    def kuyruk2(*_a):
        calls["kuyruk"] += 1
        return "ok"

    govde, kod = gonder(bagli=bagli2, kuyruk=kuyruk2)
    check("bagli_degil", kod == 200 and govde.get("geri_dus") is True and govde.get("neden") == "bagli_degil" and calls["bagli"] == 1 and calls["kuyruk"] == 0)
    check("bagli_metin", govde.get("mesaj") == sw._GERI_METIN["bagli_degil"])


def test_tekrar_ve_kilit():
    base_patches()
    ekle = {"n": 0}
    sw._ekle = lambda *_a, **_k: ekle.__setitem__("n", ekle["n"] + 1) or {"id": 1}
    sw._son_gonderim = lambda *_a, **_k: {"id": 9, "durum": "gonderildi"}
    govde, kod = gonder()
    check("tekrar_409", kod == 409 and govde.get("tekrar") is True and ekle["n"] == 0)
    sw._son_gonderim = lambda *_a, **_k: {"id": 9, "durum": "gonderiliyor"}
    govde, kod = gonder()
    check("on_dk_uyari", kod == 409 and govde.get("tekrar") is True and govde.get("mesaj") == sw._YINE and ekle["n"] == 0)
    sw._son_gonderim = lambda *_a, **_k: {"id": 9, "durum": "gonderildi"}
    govde, kod = gonder(data={"onay": True, "deneme": DENEME_2})
    check("yine_de", kod == 200 and govde.get("durum") == "gonderiliyor" and ekle["n"] == 1)


def test_hizli_ve_yok():
    base_patches()
    durum = []
    sw._ekle = lambda *_a, **_k: {"id": 4}
    sw._durum_yaz = lambda rid, d, neden="": durum.append(d)

    def kuyruk(*_a):
        time.sleep(0.4)
        return "ok"

    t0 = time.perf_counter()
    govde, kod = gonder(kuyruk=kuyruk)
    sure = time.perf_counter() - t0
    check("arkaplan", kod == 200 and govde.get("durum") == "gonderiliyor" and sure < 0.25)
    time.sleep(0.6)
    check("sonuc_yaz", durum == ["kuyrukta"])

    base_patches()
    yaz = []
    sw._ekle = lambda *a, **_k: yaz.append(a[3]) or {"id": 5}
    govde, kod = gonder(numara=lambda _t: ("yok", ""))
    check("kayitsiz", kod == 400 and yaz == ["basarisiz"])


def test_sayfa_ayrimi():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "sozlesme-whatsapp.js").read_text(encoding="utf-8")

    def govde(ad):
        i = html.find("function " + ad)
        j = html.find("\nfunction ", i + 10)
        if j < 0:
            j = i + 1200
        return html[i:j]

    makbuz = govde("sozlesmeTahsilatWhatsApp")
    ust = govde("girisMusteriWhatsAppAc")
    tahsil = govde("girisTahsilatWhatsApp")
    cari = govde("cariKartWhatsApp")
    kira = govde("kiraBildirgesiWhatsApp")
    check("makbuz_popup", "tahsilatWaAc('sozlesme')" in makbuz and "bestOfficeWhatsAppWebAc" not in makbuz and "sozlesmeWhatsAppAc" not in makbuz and "ekte" not in makbuz)
    check("ust_popup", "sozlesmeWhatsAppAc" in ust and "buton: 'ust'" in ust)
    check("tahsilat_ayni", "tahsilatWaAc('giris')" in tahsil and "bestOfficeWhatsAppWebAc" not in tahsil and "ekte" not in tahsil)
    check("cari_ayni", "sozlesmeWhatsAppAc" not in cari and "bestOfficeWhatsAppWebAc" in cari)
    check("kira_ayni", "sozlesmeWhatsAppAc" not in kira and "bestOfficeWhatsAppWebAc" in kira)
    check("toplu_ayni", "/whatsapp/api/gonder" in html and "function whatsappSeciliGonder" in html)
    check("js_yine", "Yine de gönder" in js and "window.confirm" not in js)
    check("js_tel", "Telefon numarası geçersiz" in js)
    check("yeniden_yok", "whatsapp_yeniden_dene" not in (ROOT / "sozlesme_whatsapp.py").read_text(encoding="utf-8"))
    check("js_baglaniyor", "WhatsApp bağlanıyor…" in js)
    check("js_neden", "WhatsApp servisi bağlı değil, WhatsApp Web sayfası açılıyor" in js)
    check("js_qr", "WhatsApp oturumu yenilenmeli (QR)" in js)
    check("js_eski_yok", "WhatsApp Web açıldı" not in js)
    check("js_deneme", "denemeUret" in js and "deneme: deneme" in js)
    check("js_poll", "/giris/api/sozlesme-whatsapp/durum?deneme=" in js and "POLL_SON = 30000" in js)
    check("js_sonuc", "Kuyruğa alındı ✓ (birkaç saniyede iletilir)" in js and "Gönderildi ✓" in js and "Sonuç belirsiz, telefondan kontrol edin" in js and "Gönderilemedi" in js)
    check("js_iki_pencere", "gonderiyor = false" in js and "acik.deneme = denemeUret()" in js)
    check("eski_anahtar_yok", "metin_h[:12]" not in (ROOT / "sozlesme_whatsapp.py").read_text(encoding="utf-8"))


def test_uyandirma_ve_yeniden():
    base_patches()
    calls = {"kuyruk": 0, "uyandir": 0, "ekle": []}
    sw._ekle = lambda *a, **_k: calls["ekle"].append(a[3]) or {"id": 1}

    def uyandir():
        calls["uyandir"] += 1
        return {"hazir": True, "neden": ""}

    def kuyruk(*_a):
        calls["kuyruk"] += 1
        return "ok"

    govde, kod = gonder(bagli=lambda: False, uyandir=uyandir, kuyruk=kuyruk)
    time.sleep(0.3)
    check("uyandir_kuyruk", kod == 200 and govde.get("durum") == "gonderiliyor" and calls["uyandir"] == 1 and calls["kuyruk"] == 1)

    base_patches()
    calls = {"kuyruk": 0, "ekle": []}
    sw._ekle = lambda *a, **_k: calls["ekle"].append(a[3]) or {"id": 1}
    govde, kod = gonder(bagli=lambda: False, uyandir=lambda: {"hazir": False, "neden": "oturum"}, kuyruk=lambda *_a: calls.__setitem__("kuyruk", 1) or "ok")
    check("uyandir_olmadi", kod == 200 and govde.get("geri_dus") is True and govde.get("neden") == "oturum" and calls["kuyruk"] == 0)
    check("uyandir_metin", govde.get("mesaj") == sw._GERI_METIN["oturum"])
    check("uyandir_kayit", calls["ekle"] == ["geri_oturum"])

    base_patches()
    sw._ekle = lambda *a, **_k: {"id": 1}
    govde, kod = gonder(bagli=lambda: {"hazir": False, "neden": "qr"})
    check("qr_metin", kod == 200 and govde.get("qr") is True and govde.get("geri_dus") is False and govde.get("mesaj") == sw._GERI_METIN["qr"])

    say = {"n": 0}

    def cagri():
        say["n"] += 1
        if say["n"] == 1:
            raise RuntimeError("zaman")
        return "oldu"

    sonuc = sw.dene_bir_kez(cagri, (RuntimeError,))
    check("yeniden_bir", sonuc == "oldu" and say["n"] == 2)
    say["n"] = 0

    def hep():
        say["n"] += 1
        raise RuntimeError("zaman")

    try:
        sw.dene_bir_kez(hep, (RuntimeError,))
        yeniden_dustu = False
    except RuntimeError:
        yeniden_dustu = True
    check("yeniden_iki", yeniden_dustu and say["n"] == 2)


def test_giris_kapali():
    from flask import Flask
    from auth import login_manager
    from routes.sozlesme_whatsapp_routes import bp

    app = Flask(__name__)
    app.secret_key = "test"
    login_manager.init_app(app)
    app.register_blueprint(bp)
    client = app.test_client()
    r = client.post(
        "/giris/api/sozlesme-whatsapp/gonder",
        json={"buton": "ust"},
        headers={"Origin": "http://localhost"},
    )
    check("yetkisiz_401", r.status_code == 401)
    r = client.get("/giris/api/sozlesme-whatsapp/durum?deneme=" + DENEME)
    check("durum_yetkisiz_401", r.status_code == 401)


def test_deneme_ve_poll():
    base_patches()
    govde, kod = gonder(data={"deneme": "eski-kalici"})
    check("deneme_400", kod == 400 and govde.get("mesaj") == sw._ORIGIN)

    base_patches()
    kuyruklar = []
    kayit = {}

    def ekle(*a, **_k):
        anahtar = a[8]
        if anahtar in kayit:
            return None
        kayit[anahtar] = {"id": len(kayit) + 1, "durum": "gonderiliyor"}
        return dict(kayit[anahtar])

    def bul(_uid, anahtar):
        return kayit.get(anahtar)

    sw._ekle = ekle
    sw._bul = bul
    sw._durum_yaz = lambda rid, durum, neden="": [row.__setitem__("durum", durum) for row in kayit.values() if row["id"] == rid]

    def kuyruk(*a):
        kuyruklar.append(a[2])
        return "ok"

    govde, kod = gonder(kuyruk=kuyruk)
    time.sleep(0.2)
    govde2, kod2 = gonder(kuyruk=kuyruk)
    check("cift_tik_tek", kod == 200 and len(kuyruklar) == 1 and kod2 == 200 and govde2.get("cakisma") is True)
    check("cift_tik_durum", govde2.get("durum") in ("gonderiliyor", "kuyrukta", "gonderildi"))
    check("cift_suren_veya_onceden", govde2.get("mesaj") in (sw._SUREN, sw._ONCEDEN, sw._GONDERILDI_ONCEDEN))

    base_patches()
    kuyruklar = []
    sw._ekle = lambda *a, **_k: {"id": len(kuyruklar) + 1}
    govde, kod = gonder(kuyruk=lambda *a: kuyruklar.append(a[2]) or "ok", data={"deneme": DENEME})
    govde, kod = gonder(kuyruk=lambda *a: kuyruklar.append(a[2]) or "ok", data={"deneme": DENEME_2})
    time.sleep(0.3)
    check("ertesi_gun", len(kuyruklar) == 2 and kuyruklar[0] != kuyruklar[1])

    base_patches()
    sw._son_gonderim = lambda *_a, **_k: {"id": 2, "durum": "gonderildi"}
    sw._ekle = lambda *_a, **_k: {"id": 6}
    kuyruklar = []
    govde, kod = gonder(kuyruk=lambda *a: kuyruklar.append(1) or "ok")
    check("on_dk_uyari_yok_kuyruk", kod == 409 and govde.get("tekrar") is True and kuyruklar == [])
    govde, kod = gonder(kuyruk=lambda *a: kuyruklar.append(a[2]) or "ok", data={"onay": True, "deneme": DENEME_2})
    time.sleep(0.3)
    check("yine_yeni_deneme", kod == 200 and govde.get("durum") == "gonderiliyor" and kuyruklar == ["szwa:d:" + DENEME_2])

    for durum, mesaj in (
        ("gonderildi", sw._GONDERILDI_ONCEDEN),
        ("kuyrukta", sw._ONCEDEN),
        ("gonderiliyor", sw._SUREN),
        ("basarisiz", sw._BASARISIZ),
        ("belirsiz", sw._BELIRSIZ),
    ):
        base_patches()
        sw._bul = lambda *_a, d=durum: {"id": 4, "durum": d}
        govde, kod = gonder()
        check("cakisma_" + durum, kod == 200 and govde.get("durum") == durum and govde.get("mesaj") == mesaj and govde.get("cakisma") is True)

    base_patches()
    sw._bul = lambda *_a, **_k: None
    sw._ekle = lambda *_a, **_k: None

    def bul_ikinci(_uid, _anahtar, k={"n": 0}):
        k["n"] += 1
        if k["n"] < 2:
            return None
        return {"id": 8, "durum": "gonderildi"}

    sw._bul = bul_ikinci
    govde, kod = gonder()
    check("cakisma_yaris", kod == 200 and govde.get("durum") == "gonderildi" and govde.get("mesaj") == sw._GONDERILDI_ONCEDEN)

    base_patches()
    sw._bul = lambda *_a, **_k: {"id": 5, "durum": "gonderildi"}
    govde, kod = sw.sozlesme_wa_durum(3, DENEME)
    check("poll_gonderildi", kod == 200 and govde.get("durum") == "gonderildi" and govde.get("mesaj") == "Gönderildi")
    sw._bul = lambda *_a, **_k: {"id": 5, "durum": "basarisiz"}
    govde, kod = sw.sozlesme_wa_durum(3, DENEME)
    check("poll_basarisiz", kod == 200 and govde.get("ok") is False and "Gönderilemedi" in govde.get("mesaj", ""))
    sw._bul = lambda *_a, **_k: {"id": 5, "durum": "belirsiz"}
    govde, kod = sw.sozlesme_wa_durum(3, DENEME)
    check("poll_belirsiz", kod == 200 and govde.get("mesaj") == sw._BELIRSIZ)
    sw._bul = lambda *_a, **_k: None
    govde, kod = sw.sozlesme_wa_durum(3, DENEME)
    check("poll_yok", kod == 404 and govde.get("durum") == "yok")
    check("kuyruk_basarisiz", sw._kuyruk_sonuc({"ok": True, "oge": [{"durum": "basarisiz"}]}) == "hata")
    check("kuyruk_belirsiz", sw._kuyruk_sonuc({"ok": True, "oge": [{"durum": "belirsiz"}]}) == "belirsiz")
    check("kuyruk_bekliyor", sw._kuyruk_sonuc({"ok": True, "oge": [{"durum": "bekliyor"}]}) == "kuyrukta")
    check("kuyruk_gercek", sw._kuyruk_sonuc({"ok": True, "oge": [{"durum": "gonderildi"}]}) == "gonderildi")
    sw._bul = lambda *_a, **_k: {"id": 5, "durum": "kuyrukta"}
    govde, kod = sw.sozlesme_wa_durum(3, DENEME)
    check("poll_kuyrukta", kod == 200 and govde.get("durum") == "kuyrukta" and govde.get("mesaj") == "Kuyruğa alındı")

    base_patches()
    anahtarlar = []
    sw._ekle = lambda *a, **_k: anahtarlar.append(a[8]) or {"id": 1}
    gonder(data={"buton": "ust", "deneme": DENEME})
    gonder(data={"buton": "makbuz", "deneme": DENEME_2})
    check("iki_buton", anahtarlar == ["szwa:d:" + DENEME, "szwa:d:" + DENEME_2])


if __name__ == "__main__":
    test_sinirlar()
    test_red()
    test_kiraci_ve_bagli()
    test_tekrar_ve_kilit()
    test_hizli_ve_yok()
    test_uyandirma_ve_yeniden()
    test_deneme_ve_poll()
    test_sayfa_ayrimi()
    test_giris_kapali()
    if fails:
        print("FAIL " + ",".join(fails))
        sys.exit(1)
    print("CASE szwa ok")
