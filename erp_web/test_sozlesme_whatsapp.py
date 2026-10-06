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


def base_patches():
    sw.ensure_tablo = _noop
    sw._musteri = lambda mid: {"id": int(mid)}
    sw._sayilar = lambda *_a, **_k: (0, 0, 0, 0)
    sw._son_gonderim = lambda *_a, **_k: None
    sw._durum_yaz = lambda *_a, **_k: None
    sw.node_yolu_acik = lambda: True


def gonder(**kwargs):
    data = {
        "musteri_id": 7,
        "telefon": "05550000000",
        "mesaj": "deneme metin",
        "buton": "ust",
        "onay": False,
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
    check("kiraci_kayit", calls["ekle"] == ["geri_dus"])

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
    check("bagli_degil", kod == 200 and govde.get("geri_dus") is True and calls["bagli"] == 1 and calls["kuyruk"] == 0)


def test_tekrar_ve_kilit():
    base_patches()
    ekle = {"n": 0}
    sw._ekle = lambda *_a, **_k: ekle.__setitem__("n", ekle["n"] + 1) or {"id": 1}
    sw._son_gonderim = lambda *_a, **_k: {"id": 9, "durum": "gonderildi"}
    govde, kod = gonder()
    check("tekrar_409", kod == 409 and govde.get("tekrar") is True and ekle["n"] == 0)
    sw._son_gonderim = lambda *_a, **_k: {"id": 9, "durum": "gonderiliyor"}
    govde, kod = gonder()
    check("suruyor", kod == 200 and govde.get("mesaj") == sw._SUREN and ekle["n"] == 0)
    sw._son_gonderim = lambda *_a, **_k: {"id": 9, "durum": "gonderildi"}
    govde, kod = gonder(data={"onay": True})
    check("yine_de", kod == 200 and govde.get("durum") == "gonderiliyor" and ekle["n"] == 1)
    sw._ekle = lambda *_a, **_k: None
    sw._son_gonderim = lambda *_a, **_k: None
    govde, kod = gonder()
    check("cift_kilit", kod == 200 and govde.get("mesaj") == sw._SUREN)


def test_hizli_ve_yok():
    base_patches()
    durum = []
    sw._ekle = lambda *_a, **_k: {"id": 4}
    sw._durum_yaz = lambda rid, d: durum.append(d)

    def kuyruk(*_a):
        time.sleep(0.4)
        return "ok"

    t0 = time.perf_counter()
    govde, kod = gonder(kuyruk=kuyruk)
    sure = time.perf_counter() - t0
    check("arkaplan", kod == 200 and govde.get("durum") == "gonderiliyor" and sure < 0.25)
    time.sleep(0.6)
    check("sonuc_yaz", durum == ["gonderildi"])

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
    check("makbuz_popup", "sozlesmeWhatsAppAc" in makbuz and "hazırdır" in makbuz and "ekte" not in makbuz)
    check("ust_popup", "sozlesmeWhatsAppAc" in ust and "buton: 'ust'" in ust)
    check("tahsilat_ayni", "sozlesmeWhatsAppAc" not in tahsil and "bestOfficeWhatsAppWebAc" in tahsil)
    check("cari_ayni", "sozlesmeWhatsAppAc" not in cari and "bestOfficeWhatsAppWebAc" in cari)
    check("kira_ayni", "sozlesmeWhatsAppAc" not in kira and "bestOfficeWhatsAppWebAc" in kira)
    check("toplu_ayni", "/whatsapp/api/gonder" in html and "function whatsappSeciliGonder" in html)
    check("js_yine", "Yine de gönder" in js and "window.confirm" not in js)
    check("js_tel", "Telefon numarası geçersiz" in js)
    check("yeniden_yok", "whatsapp_yeniden_dene" not in (ROOT / "sozlesme_whatsapp.py").read_text(encoding="utf-8"))


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


if __name__ == "__main__":
    test_sinirlar()
    test_red()
    test_kiraci_ve_bagli()
    test_tekrar_ve_kilit()
    test_hizli_ve_yok()
    test_sayfa_ayrimi()
    test_giris_kapali()
    if fails:
        print("FAIL " + ",".join(fails))
        sys.exit(1)
    print("CASE szwa ok")
