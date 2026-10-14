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
    check("js v=4", "js/erp-asistan.js') }}?v=4" in layout)
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
            "<script src='/static/js/erp-asistan.js?v=4'></script>"
            "</body></html>"
        )

    @app.route("/asistan-test-b")
    def asistan_test_b():
        return (
            "<!doctype html><html><head><meta charset='utf-8'></head><body>"
            "<script>window.ERP_ASISTAN_UID = 99;</script>"
            "<div id='erp-asistan-ozet'></div>"
            "<script src='/static/js/erp-asistan.js?v=4'></script>"
            "</body></html>"
        )

    client = app.test_client()
    notlar = [
        {"id": 1, "musteri_id": 867, "iliski_etiket": "••••", "not_metni": "kisa not metni uzatildi",
         "created_etiket": "01.10.2026", "hatirlatma_etiket": "02.10.2026", "durum": "bekliyor",
         "olusturan_ad": "A", "gorusulen_kisi": "B", "toplam_borc": 0, "geciken_ay": 0},
        {"id": 2, "musteri_id": 900, "iliski_etiket": "••••", "not_metni": "diger",
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

    def isle(route, request):
        try:
            _isle(route, request)
        except Exception as exc:
            print("ROUTE", type(exc).__name__, str(exc)[:200])
            route.fulfill(status=500, body=str(exc)[:200])

    def _isle(route, request):
        tam = request.url.split("asistan.test")[-1]
        yol = tam.split("?")[0]
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
        tarayici.close()


if __name__ == "__main__":
    test_grid_sayilari()
    test_kaynak_yazmaz()
    test_gecmis_sayfa()
    arayuz()
    if FAILS:
        print("FAIL", len(FAILS))
        raise SystemExit(1)
    print("erp_asistan_ozet ok")
