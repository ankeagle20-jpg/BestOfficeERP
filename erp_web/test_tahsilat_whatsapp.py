"""Tahsilat WhatsApp. Ag yok, canli tablo yok, PDF dosyaya yazilmaz."""
import inspect
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import sozlesme_whatsapp as sw  # noqa: E402

EKLE_SRC = inspect.getsource(sw._ekle)
SON_SRC = inspect.getsource(sw._makbuz_son)
PDF_SRC = inspect.getsource(sw._pdf_gercek)
ENSURE_SRC = inspect.getsource(sw.ensure_tablo)
fails = []
DENEME = "11111111-1111-4111-8111-111111111111"
DENEME_2 = "22222222-2222-4222-8222-222222222222"
ROW = {
    "id": 55,
    "makbuz_no": "260",
    "tutar": 10,
    "tahsilat_tarihi": "2026-10-06",
    "musteri_id": 7,
    "musteri_adi": "Gizli",
}
METIN = "not"


def check(name, ok):
    print(("ok " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)


def base():
    sw.ensure_tablo = lambda: None
    sw._sayilar = lambda *_a, **_k: (0, 0, 0, 0)
    sw._bul = lambda *_a, **_k: None
    sw._makbuz_son = lambda *_a, **_k: None
    sw._durum_yaz = lambda *_a, **_k: None
    sw._ekle_tahsilat = lambda *_a, **_k: {"id": 1}
    sw.node_yolu_acik = lambda: True


def gonder(**kwargs):
    data = {
        "tahsilat_id": 55,
        "musteri_id": 7,
        "telefon": "05550000000",
        "mesaj": METIN,
        "deneme": DENEME,
        "onay": False,
    }
    data.update(kwargs.pop("data", {}))
    return sw.tahsilat_wa_isle(
        3,
        data,
        kwargs.get("origin", "https://payafin.com"),
        kwargs.get("host", "payafin.com"),
        simdi=datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc),
        bagli_fn=kwargs.get("bagli", lambda: True),
        numara_fn=kwargs.get("numara", lambda _t: ("kayitli", "")),
        kuyruk_fn=kwargs.get("kuyruk", lambda *_a: "ok"),
        uyandir_fn=kwargs.get("uyandir"),
        getir_fn=kwargs.get("getir", lambda _i: dict(ROW)),
        pdf_fn=kwargs.get("pdf", lambda _r: b"%PDF-1.4\n"),
        node_fn=kwargs.get("node", lambda: True),
    )


def test_kimlik_ve_pdf():
    base()
    calls = {"pdf": 0, "kuyruk": 0, "getir": 0}

    def getir(i):
        calls["getir"] += 1
        return dict(ROW) if int(i) == 55 else None

    govde, kod = gonder(origin="https://evil.example", getir=getir)
    check("origin_403", kod == 403 and govde.get("geri_dus") is False and calls["getir"] == 0)

    govde, kod = gonder(data={"tahsilat_id": 99}, getir=getir, pdf=lambda _r: calls.__setitem__("pdf", calls["pdf"] + 1))
    check("baska_id_404", kod == 404 and calls["pdf"] == 0)

    govde, kod = gonder(data={"musteri_id": 8}, getir=getir, kuyruk=lambda *_a: calls.__setitem__("kuyruk", 1) or "ok")
    check("musteri_403", kod == 403 and calls["kuyruk"] == 0)

    govde, kod = gonder(data={"makbuz_no": "999"}, getir=getir)
    check("makbuz_403", kod == 403 and govde.get("geri_dus") is False)

    row = dict(ROW)
    row["makbuz_no"] = "260 X"
    govde, kod = gonder(getir=lambda _i: row, kuyruk=lambda *_a: calls.__setitem__("kuyruk", calls["kuyruk"] + 1) or "ok")
    check("ad_red", kod == 400 and calls["kuyruk"] == 0 and govde.get("geri_dus") is False)

    def pdf(row):
        calls["tutar"] = row.get("tutar")
        return b"%PDF-1.4\n"
    govde, kod = gonder(data={"tutar": 99999, "makbuz_no": ""}, getir=getir, pdf=pdf)
    check("tutar_kayit", kod == 200 and calls.get("tutar") == 10 and govde.get("durum") == "gonderiliyor")

    calls["pdf"] = 0
    govde, kod = gonder(
        pdf=lambda _r: calls.__setitem__("pdf", 1) or (b"%PDF" + b"x" * sw.PDF_BAYT_LIMIT),
        kuyruk=lambda *_a: calls.__setitem__("kuyruk", calls["kuyruk"] + 1) or "ok",
    )
    check("boyut_400", kod == 400 and calls["pdf"] == 1 and govde.get("mesaj") == sw._PDF_OLMADI and govde.get("geri_dus") is False)


def test_gonderim_ve_ek():
    base()
    yaz = []
    args = []
    hold = {}
    sw._durum_yaz = lambda _rid, durum, neden="": yaz.append(durum)
    sw._ekle_tahsilat = lambda *a, **_k: args.append(a) or {"id": 4}

    def kuyruk(_tel, _mesaj, _anahtar, ek=None):
        hold["ad"] = ek.get("ad")
        hold["mime"] = ek.get("mime")
        hold["raw"] = __import__("base64").b64decode(ek.get("veri") or "")
        hold["ek"] = ek
        return "ok"

    govde, kod = gonder(kuyruk=kuyruk)
    time.sleep(0.5)
    norm = sw.normalize_wa_telefon("05550000000")
    flat = " ".join(str(x) for x in (args[0] if args else ()))
    check("kuyruk_200", kod == 200 and govde.get("durum") == "gonderiliyor" and govde.get("geri_dus") is False)
    check("durum_kuyrukta", yaz == ["kuyrukta"])
    check("ad", hold.get("ad") == "260.pdf" and hold.get("mime") == "application/pdf")
    check("pdf_bayt", isinstance(hold.get("raw"), bytes) and hold["raw"].startswith(b"%PDF") and len(hold["raw"]) < 100)
    check("ek_dustu", hold.get("ek") == {})
    check("kayit", args and args[0][2] == "gonderiliyor" and args[0][8] == 55 and args[0][9] == "260")
    check("maske", args[0][3] == sw.telefon_maske(norm) and norm not in args[0][3])
    check("metin_yok", METIN not in flat and "Gizli" not in flat and b"%PDF" not in flat.encode("utf-8", "ignore"))

    base()
    yaz = []
    sw._durum_yaz = lambda _rid, durum, neden="": yaz.append(durum)
    gonder(kuyruk=lambda *_a: "gonderildi")
    time.sleep(0.5)
    check("durum_gonderildi", yaz == ["gonderildi"])


def test_kapali_ve_tekrar():
    base()
    calls = {"k": 0, "p": 0}

    def pdf(_r):
        calls["p"] += 1
        return b"%PDF-1.4\n"

    def kuyruk(*_a):
        calls["k"] += 1
        return "ok"

    govde, kod = gonder(node=lambda: False, pdf=pdf, kuyruk=kuyruk)
    check("kapali", kod == 200 and govde.get("pdf_indir") is True and govde.get("geri_dus") is False and govde.get("qr") is False)
    check("kapali_metin", "ekte" not in (govde.get("mesaj") or "") and govde.get("uyari") == sw._PDF_UYARI)
    check("kapali_indir", govde.get("indir") == "/faturalar/tahsilat-pdf/55?indir=1")
    check("kapali_web", "web.whatsapp" not in json.dumps(govde) and calls["k"] == 0 and calls["p"] == 0)

    govde, kod = gonder(
        bagli=lambda: {"hazir": False, "neden": "oturum"},
        uyandir=lambda: {"hazir": False, "neden": "oturum"},
        pdf=pdf,
        kuyruk=kuyruk,
    )
    check("oturum", govde.get("pdf_indir") is True and govde.get("geri_dus") is False and "ekte" not in (govde.get("mesaj") or ""))

    govde, kod = gonder(
        bagli=lambda: {"hazir": False, "neden": "qr"},
        uyandir=lambda: {"hazir": False, "neden": "qr"},
        pdf=pdf,
        kuyruk=kuyruk,
    )
    check("qr", govde.get("qr") is True and govde.get("pdf_indir") is True and govde.get("geri_dus") is False and "ekte" not in (govde.get("mesaj") or ""))
    check("pdf_yok", calls["p"] == 0 and calls["k"] == 0)

    base()
    sw._makbuz_son = lambda *_a, **_k: {"id": 3, "durum": "gonderildi"}
    govde, kod = gonder(kuyruk=kuyruk)
    check("makbuz_tekrar", kod == 409 and govde.get("tekrar") is True and govde.get("mesaj") == sw._MAKBUZ_YINE and calls["k"] == 0)

    govde, kod = gonder(data={"onay": True, "deneme": DENEME_2}, kuyruk=kuyruk)
    time.sleep(0.3)
    check("yine_de", kod == 200 and govde.get("durum") == "gonderiliyor" and calls["k"] == 1)

    base()
    sw._sayilar = lambda *_a, **_k: (10, 0, 0, 0)
    govde, kod = gonder()
    check("hiz_dk", kod == 429 and "dakika" in (govde.get("mesaj") or ""))
    sw._sayilar = lambda *_a, **_k: (0, 0, 0, 300)
    govde, kod = gonder()
    check("hiz_gun", kod == 429 and "sınırı" in (govde.get("mesaj") or ""))


def test_cift_tik():
    base()
    kayit = {}
    calls = {"k": 0}

    def ekle(*a, **_k):
        anah = a[7]
        if anah in kayit:
            return None
        kayit[anah] = {"id": 1, "durum": a[2]}
        return dict(kayit[anah])

    def bul(_uid, anah):
        row = kayit.get(anah)
        return dict(row) if row else None

    sw._ekle_tahsilat = ekle
    sw._bul = bul
    govde, kod = gonder(kuyruk=lambda *_a: calls.__setitem__("k", calls["k"] + 1) or "ok")
    govde2, kod2 = gonder(kuyruk=lambda *_a: calls.__setitem__("k", calls["k"] + 1) or "ok")
    time.sleep(0.4)
    check("cift", kod == 200 and kod2 == 200 and govde.get("durum") == "gonderiliyor" and govde2.get("cakisma") is True and calls["k"] == 1)


def test_hazir_ve_kaynak():
    govde, kod = sw.tahsilat_wa_hazir(55, getir_fn=lambda _i: dict(ROW), node_fn=lambda: True)
    check("hazir_ek", kod == 200 and govde.get("pdf_ek") is True and "ekte" in (govde.get("mesaj") or "") and "260" in govde.get("mesaj") and "06.10.2026" in govde.get("mesaj") and "Gizli" not in govde.get("mesaj") and not govde.get("pdf_indir"))
    govde, kod = sw.tahsilat_wa_hazir(55, getir_fn=lambda _i: dict(ROW), node_fn=lambda: False)
    check("hazir_yok", kod == 200 and govde.get("pdf_ek") is False and "ekte" not in (govde.get("mesaj") or "") and govde.get("uyari") == sw._PDF_UYARI and govde.get("geri_dus") is False)
    govde, kod = sw.tahsilat_wa_hazir(0, getir_fn=lambda _i: dict(ROW), node_fn=lambda: True)
    check("hazir_0", kod == 400)
    govde, kod = sw.tahsilat_wa_hazir(99, getir_fn=lambda _i: None, node_fn=lambda: True)
    check("hazir_404", kod == 404)

    check("sozlesme_insert", "tahsilat_id" not in EKLE_SRC)
    check("ensure_sutun", "tahsilat_id INTEGER" in ENSURE_SRC and "makbuz_no TEXT" in ENSURE_SRC and "ADD COLUMN IF NOT EXISTS tahsilat_id" in ENSURE_SRC and "ADD COLUMN IF NOT EXISTS makbuz_no" in ENSURE_SRC)
    check("pdf_indir_sayilmaz", "pdf_indir" not in SON_SRC and "tahsilat_id" in SON_SRC)
    check("pdf_bellek", "build_makbuz_pdf" in PDF_SRC and "uploads" not in PDF_SRC and "UPLOAD" not in PDF_SRC)
    py = (ROOT / "sozlesme_whatsapp.py").read_text(encoding="utf-8")
    check("otomatik_mig_yok", "migrate_sozlesme_whatsapp_tahsilat" not in py)
    liste = sw._kuyruk_liste("1", "m", "a")
    check("liste_yazi", "ek" not in liste[0])
    liste = sw._kuyruk_liste("1", "m", "a", {"mime": "application/pdf", "veri": "YQ==", "ad": "1.pdf"})
    check("liste_ek", liste[0]["ek"]["mime"] == "application/pdf" and liste[0]["ek"]["ad"] == "1.pdf" and "veri" in liste[0]["ek"])


def test_sayfa_ve_mig():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "sozlesme-whatsapp.js").read_text(encoding="utf-8")
    durum = (ROOT / "static" / "js" / "tahsilat-wa-durum.js").read_text(encoding="utf-8")

    def govde(ad):
        i = html.find("function " + ad)
        j = html.find("\nfunction ", i + 10)
        return html[i:j]

    yer = []
    bas = 0
    while True:
        i = html.find("tahsilatWaKayit(", bas)
        if i < 0:
            break
        yer.append(html.rfind("\nfunction ", 0, i))
        bas = i + 1
    check("kayit_iki", len(yer) == 2 and "girisTahsilatMakbuzKaydet" in html[yer[0]:yer[0] + 80] and "sozlesmeTahsilatKaydet" in html[yer[1]:yer[1] + 80])
    check("yazdir_yok", "tahsilatWaKayit" not in govde("girisTahsilatYazdir") and "tahsilatWaKayit" not in govde("sozlesmeTahsilatYazdir"))
    check("toplu_yok", "tahsilatWaKayit" not in govde("sozlesmeAylikTahsilEt") and "tahsilatWaKayit" not in govde("girisTahsilatYilAySecilenleriTahsilet"))
    check("iptal", "tahsilatWaPasif('giris')" in govde("girisTahsilatIptal") and "tahsilatWaPasif('sozlesme')" in govde("sozlesmeTahsilatIptal"))
    check("musteri", "tahsilatWaMusteri('giris'" in govde("girisTahsilatFormunuMusteriyleDoldur") and "tahsilatWaMusteri('sozlesme'" in govde("sozlesmeTahsilatFormunuSeciliMusteridenDoldur"))
    check("pasif_html", 'id="giris-tahsilat-wa" disabled' in html and 'id="sozlesme-tahsilat-wa" disabled' in html and html.count('title="Önce makbuzu kaydedin"') >= 2)
    check("web_fonk", "bestOfficeWhatsAppWebAc" not in govde("girisTahsilatWhatsApp") and "bestOfficeWhatsAppWebAc" not in govde("sozlesmeTahsilatWhatsApp"))
    check("script", html.find("tahsilat-wa-durum.js") < html.find("sozlesme-whatsapp.js") and "tahsilat_wa_js_surum" in html)
    i = js.find("function pdfIndirGoster")
    j = js.find("\n  function ", i + 10)
    check("pdf_web_yok", i > 0 and "webAc" not in js[i:j] and "PDF'yi indirip WhatsApp'a ekleyin" in js[i:j])
    dal = js.find("acik.tahsilatId && (j.pdf_indir || j.geri_dus || j.qr)")
    check("web_sonra", dal > 0 and js.find("webAc(ham, metin)", dal) > dal)
    check("alan_dinle", '"tutar", "tarih", "aciklama"' in js)
    check("kalici_yok", "localStorage" not in js and "localStorage" not in durum and "sessionStorage" not in durum)
    check("kuyruk_yazi", "Kuyruğa alındı ✓ (birkaç saniyede iletilir)" in js and "Gönderildi ✓" in js)

    sql = (ROOT / "scripts" / "migrate_sozlesme_whatsapp_tahsilat.sql").read_text(encoding="utf-8")
    check("mig_sql", sql.count("ADD COLUMN IF NOT EXISTS") >= 2 and "tahsilat_id INTEGER" in sql and "makbuz_no TEXT" in sql and "DROP COLUMN IF EXISTS" in sql)
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    check("app_mig_yok", "migrate_sozlesme_whatsapp_tahsilat" not in app)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate_sozlesme_whatsapp_tahsilat.py")], capture_output=True, text=True)
    check("mig_dry", r.returncode == 0 and "Calistirilmadi" in r.stdout)
    r2 = subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate_sozlesme_whatsapp_tahsilat.py"), "--uygula"], capture_output=True, text=True)
    check("mig_baglanti_yok", r2.returncode == 2 and "BAGLANTI YOK" in r2.stdout)


def test_neden_esleme():
    import logging

    yaz = []
    sw._durum_yaz = lambda rid, durum, neden="": yaz.append((durum, neden))
    sw._arkaplan_yaz(4, {"durum": "hata", "neden": "ek_cok_buyuk"})
    sw._arkaplan_yaz(4, {"durum": "hata", "neden": "ek_gecersiz"})
    sw._arkaplan_yaz(4, {"durum": "hata", "neden": "ulasilamadi"})
    sw._arkaplan_yaz(4, {"durum": "hata", "neden": "oturum_yok"})
    sw._arkaplan_yaz(4, "ok")
    check("neden_yaz", yaz == [
        ("basarisiz", "ek_cok_buyuk"),
        ("basarisiz", "ek_gecersiz"),
        ("basarisiz", "ulasilamadi"),
        ("basarisiz", "oturum_yok"),
        ("kuyrukta", ""),
    ])
    buyuk = sw._kuyruk_oku(413, {"ok": False, "code": "ek_cok_buyuk"})
    check("http_413", buyuk["durum"] == "hata" and buyuk["neden"] == "ek_cok_buyuk")
    cozulmus = sw._kuyruk_oku(200, {"ok": True, "oge": [{"durum": "basarisiz", "kod": "ek_cok_buyuk"}]})
    check("cozulmus_kod", cozulmus["neden"] == "ek_cok_buyuk")
    ek = sw._kuyruk_oku(200, {"ok": True, "oge": [{"durum": "basarisiz", "kod": "ek_gecersiz"}]})
    check("ek_kod", ek["neden"] == "ek_gecersiz")
    duz = sw._kuyruk_oku(200, {"ok": True, "oge": [{"durum": "bekliyor"}]})
    check("duz_kuyruk", duz["durum"] == "kuyrukta" and duz["neden"] == "" and sw._kuyruk_sonuc({"ok": True, "oge": [{"durum": "bekliyor"}]}) == "kuyrukta")
    check("metin_buyuk", sw.durum_mesaji("basarisiz", False, "ek_cok_buyuk") == sw._EK_BUYUK)
    check("metin_ek", sw.durum_mesaji("basarisiz", False, "ek_gecersiz") == sw._EK_YOK)
    check("metin_servis", sw.durum_mesaji("basarisiz", False, "ulasilamadi") == sw._SERVIS)
    check("metin_oturum", sw.durum_mesaji("basarisiz", False, "oturum_yok") == sw._OTURUM_YOK)
    check("metin_genel", sw.durum_mesaji("basarisiz", False, "") == sw._BASARISIZ)
    g1 = sw._durum_govde({"durum": "basarisiz", "neden": "ek_cok_buyuk"}, False)
    g2 = sw._durum_govde({"durum": "basarisiz", "neden": "ek_gecersiz"}, False)
    g3 = sw._durum_govde({"durum": "basarisiz", "neden": "oturum_yok"}, False)
    g4 = sw._durum_govde({"durum": "basarisiz", "neden": ""}, False)
    check("yine_kapali", g1.get("yine") is False and g1.get("mesaj") == sw._EK_BUYUK and g2.get("yine") is False)
    check("yine_oturum", g3.get("yine") is True and g3.get("mesaj") == sw._OTURUM_YOK and g4.get("yine") is True)
    check("yine_409", sw._YINE == "Bu mesaj bu numaraya az önce gönderildi.")
    kayit = []

    class H(logging.Handler):
        def emit(self, record):
            kayit.append(record.getMessage())

    h = H()
    sw.logger.addHandler(h)
    sw.logger.setLevel(logging.INFO)
    sw._kuyruk_log(413, "ek_cok_buyuk")
    sw.logger.removeHandler(h)
    check("log_kod", kayit and "http=413" in kayit[-1] and "kod=ek_cok_buyuk" in kayit[-1] and "905" not in kayit[-1])
    js = (ROOT / "static" / "js" / "sozlesme-whatsapp.js").read_text(encoding="utf-8")
    check("js_yine_bayrak", "j.yine === false" in js and "Bu mesaj bu numaraya az önce gönderildi." in js)
    check("ensure_neden", "ADD COLUMN IF NOT EXISTS neden TEXT" in ENSURE_SRC and "neden TEXT" in ENSURE_SRC)
    sql = (ROOT / "scripts" / "migrate_sozlesme_whatsapp_neden.sql").read_text(encoding="utf-8")
    check("neden_sql", "public.sozlesme_whatsapp_gonderim" in sql and "ADD COLUMN IF NOT EXISTS neden TEXT" in sql and "DROP COLUMN IF EXISTS neden" in sql)
    py = (ROOT / "sozlesme_whatsapp.py").read_text(encoding="utf-8")
    check("neden_otomatik_yok", "migrate_sozlesme_whatsapp_neden" not in py)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate_sozlesme_whatsapp_neden.py")], capture_output=True, text=True)
    check("neden_dry", r.returncode == 0 and "Calistirilmadi" in r.stdout)
    r2 = subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate_sozlesme_whatsapp_neden.py"), "--uygula"], capture_output=True, text=True)
    check("neden_baglanti_yok", r2.returncode == 2 and "BAGLANTI YOK" in r2.stdout)


def test_yetkisiz():
    from flask import Flask
    from auth import login_manager
    from routes.sozlesme_whatsapp_routes import bp

    app = Flask(__name__)
    app.secret_key = "test"
    login_manager.init_app(app)
    app.register_blueprint(bp)
    client = app.test_client()
    r = client.post("/giris/api/tahsilat-whatsapp/gonder", json={"tahsilat_id": 1}, headers={"Origin": "http://localhost"})
    check("gonder_401", r.status_code == 401)
    r = client.get("/giris/api/tahsilat-whatsapp/hazir?tahsilat_id=1")
    check("hazir_401", r.status_code == 401)


if __name__ == "__main__":
    test_kimlik_ve_pdf()
    test_gonderim_ve_ek()
    test_kapali_ve_tekrar()
    test_cift_tik()
    test_hazir_ve_kaynak()
    test_sayfa_ve_mig()
    test_neden_esleme()
    test_yetkisiz()
    if fails:
        print("FAIL", len(fails))
        sys.exit(1)
    print("CASE tahsilat whatsapp ok")
