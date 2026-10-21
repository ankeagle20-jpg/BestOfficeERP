"""Sözleşme/asistan alıcı seçimi. Sahte servis. Numara ve metin yazılmaz."""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import routes.erp_notlar_routes as en
import sozlesme_whatsapp as sw
import wa_saglik
from odeme_linki import wa_kisi_secenekleri

ROOT = Path(__file__).resolve().parent


def check(ad, ok, detay=""):
    if not ok:
        raise SystemExit(f"FAIL {ad} {detay}")
    print("ok", ad)


BIR = "905550001111"
IKI = "905550002222"
UC = "905550003333"
SERBEST = "905550004444"
DENEME = "dddddddd-dddd-4ddd-8ddd-ddddddddddd1"


def _musteri(_mid=7):
    return {"id": 7, "name": "••••", "telefon": BIR, "telefonlar": [BIR, IKI]}


def test_liste_ve_secim():
    check("yetkili_once", en._wa_telefon_listesi("05550001111", "05550002222")[0].endswith("1111"))
    check("tekil", len(en._wa_telefon_listesi("05550001111", "905550001111")) == 1)
    check("ikinci_gecersiz", len(en._wa_telefon_listesi("05550001111", "abc")) == 1)
    check("ikisi", len(en._wa_telefon_listesi("05550001111", "05550002222")) == 2)
    sec, hata = en._wa_secilen([BIR, IKI], {})
    check("secim_yok_hepsi", hata == "" and [s for s, _t in sec] == [1, 2])
    sec, hata = en._wa_secilen([BIR, IKI], {"secim": [2]})
    check("yalniz_ikinci", hata == "" and [s for s, _t in sec] == [2])
    sec, hata = en._wa_secilen([BIR, IKI], {"secim": []})
    check("hicbiri", hata == "En az bir numara seçin" and sec == [])
    kisi = wa_kisi_secenekleri([{"birincil": True, "sira": 0, "ad_soyad": "A", "tel": "05550001111", "tel2": "905550001111"}])
    check("kisi_tekil", len(kisi) == 1)
    secenek = en._wa_numara_secenekleri([BIR, IKI])
    ham = json.dumps(secenek)
    check("maske_secili", secenek[0]["secili"] is True and secenek[1]["secili"] is False)
    check("maske_son4", secenek[0]["maske"].endswith("1111") and BIR not in ham and IKI not in ham)


def test_gonderim():
    cagri = []

    def gonder(_uid, data, _o, _h):
        cagri.append((int(str(data.get("telefon") or "")[-1:]), data.get("deneme"), data.get("onay")))
        tel = str(data.get("telefon") or "")
        if tel == IKI and gonder.belirsiz:
            return {"ok": True, "durum": "belirsiz"}, 200
        if gonder.limit and tel == IKI:
            return {"ok": False, "mesaj": "Bugünkü gönderim sınırınız doldu."}, 429
        return {"ok": True, "durum": "gonderiliyor"}, 200

    gonder.belirsiz = False
    gonder.limit = False
    eski = (en._wa_musteri, en._wa_on_dk_var, en._WA_DENEME)
    en._wa_musteri = _musteri
    en._wa_on_dk_var = lambda *_a, **_k: False
    en._WA_DENEME.clear()
    try:
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "m", "sablon": "ozel", "deneme": DENEME, "secim": [1],
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("yalniz_bir", kod == 200 and len(cagri) == 1 and cagri[0][0] == 1)
        cagri.clear()
        en._WA_DENEME.clear()
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "m", "sablon": "ozel", "deneme": "dddddddd-dddd-4ddd-8ddd-ddddddddddd2",
            "secim": [2],
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("yalniz_iki", kod == 200 and len(cagri) == 1 and cagri[0][0] == 2)
        cagri.clear()
        en._WA_DENEME.clear()
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "m", "sablon": "ozel", "deneme": "dddddddd-dddd-4ddd-8ddd-ddddddddddd3",
            "secim": [1, 2],
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("ikisi_iki_cagri", kod == 200 and len(cagri) == 2 and cagri[0][1] != cagri[1][1])
        ham = json.dumps(govde)
        check("govdede_numara_yok", BIR not in ham and IKI not in ham)
        cagri.clear()
        en._WA_DENEME.clear()
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "m", "sablon": "ozel", "deneme": "dddddddd-dddd-4ddd-8ddd-ddddddddddd4",
            "secim": [],
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("hic_cagri_yok", kod == 400 and cagri == [] and "seçin" in govde.get("mesaj", ""))
        gonder.belirsiz = True
        en._WA_DENEME.clear()
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "m", "sablon": "ozel", "deneme": "dddddddd-dddd-4ddd-8ddd-ddddddddddd5",
            "secim": [1, 2],
        }, "http://yerel", "yerel", gonder_fn=gonder)
        numaralar = govde.get("numaralar") or []
        check(
            "kismi_belirsiz",
            kod == 200 and numaralar[0].get("durum") == "gonderiliyor" and numaralar[1].get("durum") == "belirsiz",
        )
        gonder.belirsiz = False
        gonder.limit = True
        en._WA_DENEME.clear()
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "m", "sablon": "ozel", "deneme": "dddddddd-dddd-4ddd-8ddd-ddddddddddd6",
            "secim": [1, 2],
        }, "http://yerel", "yerel", gonder_fn=gonder)
        numaralar = govde.get("numaralar") or []
        check(
            "kismi_limit",
            numaralar[0].get("durum") == "gonderiliyor"
            and "sınır" in (numaralar[1].get("mesaj") or "")
            and "sınır" in (govde.get("mesaj") or ""),
        )

        gonder.limit = False

        def yalniz_birinci(_mid, _metin, telefon=None):
            return str(telefon or "").endswith("1111")

        en._wa_on_dk_var = yalniz_birinci
        en._WA_DENEME.clear()
        once = len(cagri)
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "m", "sablon": "ozel", "deneme": "dddddddd-dddd-4ddd-8ddd-ddddddddddd7",
            "secim": [1, 2],
        }, "http://yerel", "yerel", gonder_fn=gonder)
        numaralar = {n.get("sira"): n for n in (govde.get("numaralar") or [])}
        check(
            "mukerrer_numara",
            kod == 200 and numaralar[1].get("durum") == "tekrar" and numaralar[2].get("durum") == "gonderiliyor"
            and len(cagri) == once + 1,
        )
    finally:
        en._wa_musteri, en._wa_on_dk_var, en._WA_DENEME = eski
        en._WA_DENEME.clear()


def test_arayuz_kaynak():
    soz = (ROOT / "static" / "js" / "sozlesme-whatsapp.js").read_text(encoding="utf-8")
    asi = (ROOT / "static" / "js" / "erp-asistan.js").read_text(encoding="utf-8")
    check("soz_kutu", "sozlesme-wa-numaralar" in soz and "En az bir numara seçin" in soz)
    check("soz_tahsilat_ayri", "if (!acik.tahsilatId)" in soz and "tahsilat-whatsapp/gonder" in soz)
    check("soz_maske", "function maske4" in soz and "Kuyruğa alındı" in soz)
    check("asi_kutu", "erp-asistan-wa-numaralar" in asi and "secim" in asi)
    check("asi_etiket", "Kuyruğa alındı" in asi and "Gönderim sürüyor" in asi)
    check("ek_link", "Başka numara ekle" in soz and "Başka numara ekle" in asi)
    check("ek_kapali", 'giris.id = "sozlesme-wa-ek"' in soz and 'giris.id = "erp-asistan-wa-ek"' in asi)
    check("poll_180", "POLL_SON = 180000" in soz and "90000" not in soz and "180000" in asi and "90000" not in asi)
    check("sure_satir", "Sonuç belirsiz, telefondan kontrol edin" in soz and "Sonuç belirsiz, telefondan kontrol edin" in asi)
    check("asi_uzun_metin", "Sonuç belirsiz, Tüm mesajlar/telefondan kontrol edin" in asi)


def _gonder_kur(cagri, audit):
    def gonder(_uid, data, _o, _h):
        tel = str(data.get("telefon") or "")
        cagri.append({"son": tel[-1:], "serbest": bool(data.get("serbest")), "ayni": data.get("deneme")})
        if gonder.kayitsiz and tel.endswith("4444"):
            return {"ok": False, "kayitli": False, "mesaj": "Bu numara WhatsApp'ta kayıtlı değil"}, 400
        if gonder.limit_hepsi or (gonder.limit and tel.endswith("4444")):
            return {"ok": False, "mesaj": "Bugünkü gönderim sınırınız doldu."}, 429
        return {"ok": True, "durum": "gonderiliyor"}, 200

    def spy(uid, mid, norm, kart, calistir=None):
        audit.append((int(uid), int(mid), norm == SERBEST, SERBEST not in list(kart or [])))
        return True

    gonder.kayitsiz = False
    gonder.limit = False
    gonder.limit_hepsi = False
    return gonder, spy


def _istek(gonder, govde):
    return en.asistan_wa_gonder(5, 7, govde, "http://yerel", "yerel", gonder_fn=gonder)


def test_serbest():
    cagri = []
    audit = []
    gonder, spy = _gonder_kur(cagri, audit)
    eski = (en._wa_musteri, en._wa_on_dk_var, sw.serbest_numara_audit)
    en._wa_musteri = _musteri
    en._wa_on_dk_var = lambda *_a, **_k: False
    sw.serbest_numara_audit = spy
    en._WA_DENEME.clear()
    try:
        govde, kod = _istek(gonder, {
            "onay": True, "mesaj": "m", "sablon": "ozel",
            "deneme": "dddddddd-dddd-4ddd-8ddd-dddddddddd11", "secim": [1],
        })
        check("kapali", kod == 200 and len(cagri) == 1 and cagri[0]["son"] == "1" and not cagri[0]["serbest"] and not audit)
        cagri.clear()
        audit.clear()
        en._WA_DENEME.clear()
        govde, kod = _istek(gonder, {
            "onay": True, "mesaj": "m", "sablon": "ozel",
            "deneme": "dddddddd-dddd-4ddd-8ddd-dddddddddd12",
            "secim": [1], "ek_numara": SERBEST,
        })
        ham = json.dumps(govde, ensure_ascii=False)
        satir = [s for s in govde.get("numaralar") or [] if str(s.get("maske") or "").endswith("4444")]
        check(
            "acik_gecerli",
            kod == 200 and len(cagri) == 2 and cagri[1]["son"] == "4" and not cagri[1]["serbest"]
            and cagri[0]["ayni"] != cagri[1]["ayni"] and len(satir) == 1
            and SERBEST not in ham and len(audit) == 1 and audit[0][0] == 5 and audit[0][2] and audit[0][3],
        )
        cagri.clear()
        audit.clear()
        en._WA_DENEME.clear()
        govde, kod = _istek(gonder, {
            "onay": True, "mesaj": "m", "sablon": "ozel",
            "deneme": "dddddddd-dddd-4ddd-8ddd-dddddddddd13",
            "secim": [1], "ek_numara": "0" + BIR[2:],
        })
        check("ayni_numara", kod == 200 and len(cagri) == 1 and cagri[0]["son"] == "1" and not audit)
        cagri.clear()
        en._WA_DENEME.clear()
        govde, kod = _istek(gonder, {
            "onay": True, "mesaj": "m", "sablon": "ozel",
            "deneme": "dddddddd-dddd-4ddd-8ddd-dddddddddd14",
            "secim": [1, 2], "ek_numara": "12",
        })
        check("gecersiz", kod == 400 and "geçersiz" in govde.get("mesaj", "") and not cagri)
        gonder.kayitsiz = True
        en._WA_DENEME.clear()
        govde, kod = _istek(gonder, {
            "onay": True, "mesaj": "m", "sablon": "ozel",
            "deneme": "dddddddd-dddd-4ddd-8ddd-dddddddddd15",
            "secim": [1], "ek_numara": SERBEST,
        })
        kayit = [s for s in govde.get("numaralar") or [] if s.get("durum") == "hata"]
        check(
            "numara_kontrol",
            len(cagri) == 2 and len(kayit) == 1 and "kayıtlı" in kayit[0].get("mesaj", "")
            and SERBEST not in json.dumps(govde, ensure_ascii=False),
        )
        gonder.kayitsiz = False
        gonder.limit = True
        cagri.clear()
        en._WA_DENEME.clear()
        govde, kod = _istek(gonder, {
            "onay": True, "mesaj": "m", "sablon": "ozel",
            "deneme": "dddddddd-dddd-4ddd-8ddd-dddddddddd16",
            "secim": [1], "ek_numara": SERBEST,
        })
        check("limit", kod == 200 and "sınır" in govde.get("mesaj", "") and len(cagri) == 2 and cagri[1]["son"] == "4")
        gonder.limit = False
        gonder.limit_hepsi = True
        cagri.clear()
        en._WA_DENEME.clear()
        _istek(gonder, {
            "onay": True, "mesaj": "m", "sablon": "ozel",
            "deneme": "dddddddd-dddd-4ddd-8ddd-dddddddddd17",
            "secim": [1], "ek_numara": SERBEST,
        })
        check("limit_durdurur", len(cagri) == 1 and cagri[0]["son"] == "1")
        gonder.limit_hepsi = False
        cagri.clear()
        en._WA_DENEME.clear()
        govde, kod = _istek(gonder, {
            "onay": True, "mesaj": "m", "sablon": "ozel",
            "deneme": "dddddddd-dddd-4ddd-8ddd-dddddddddd18",
            "secim": [1, 2], "ek_numara": SERBEST,
        })
        check("uc_alici", kod == 200 and len(cagri) == 3)
        en._wa_musteri = lambda _mid=7: {"id": 7, "name": "••••", "telefon": BIR, "telefonlar": [BIR, IKI, UC]}
        cagri.clear()
        en._WA_DENEME.clear()
        govde, kod = _istek(gonder, {
            "onay": True, "mesaj": "m", "sablon": "ozel",
            "deneme": "dddddddd-dddd-4ddd-8ddd-dddddddddd19",
            "ek_numara": SERBEST,
        })
        check("uc_sinir", kod == 400 and govde.get("mesaj") == "En fazla 3 alıcı" and not cagri)
        en._wa_on_dk_var = lambda *_a, **_k: str((_a[2] if len(_a) > 2 else _k.get("telefon")) or "").endswith("4444")
        en._wa_musteri = _musteri
        audit.clear()
        cagri.clear()
        en._WA_DENEME.clear()
        govde, kod = _istek(gonder, {
            "onay": True, "mesaj": "m", "sablon": "ozel",
            "deneme": "dddddddd-dddd-4ddd-8ddd-dddddddddd20",
            "secim": [1], "ek_numara": SERBEST,
        })
        tekrar = [s for s in govde.get("numaralar") or [] if s.get("durum") == "tekrar"]
        check("on_dk", kod == 200 and len(cagri) == 1 and len(tekrar) == 1 and not audit[-1:])
    finally:
        en._wa_musteri, en._wa_on_dk_var, sw.serbest_numara_audit = eski
        en._WA_DENEME.clear()


def test_audit_govde():
    yakalanan = []

    def calistir(sql, params=None):
        yakalanan.append(params)
        return True

    yazildi = sw.serbest_numara_audit(5, 7, SERBEST, [BIR], calistir=calistir)
    blob = json.dumps(yakalanan, ensure_ascii=False, default=str)
    check("audit_yazildi", yazildi is True and "4444" in blob and SERBEST not in blob and BIR not in blob)
    check("audit_tam_yok", "telefon" not in blob and "mesaj" not in blob)
    yakalanan.clear()
    kartta = sw.serbest_numara_audit(5, 7, BIR, [BIR], calistir=calistir)
    check("karttaki_yazilmaz", kartta is False and yakalanan == [])
    kaynak = inspect.getsource(sw.sozlesme_wa_isle)
    check("numara_kontrol_duruyor", "_numara_bir_ve_yenile" in kaynak and "serbest_numara_audit" in kaynak)


def test_yoklama_tazele():
    html = (ROOT / "templates" / "whatsapp" / "saglik.html").read_text(encoding="utf-8")
    route = (ROOT / "routes" / "whatsapp_routes.py").read_text(encoding="utf-8")
    check("tazele_dugme", 'id="btnTazele"' in html and "Bekleyenleri tazele" in html)
    check("tazele_yol", "bekleyen_kayitlar" in route and "_satiri_tazele" in route)
    sql = inspect.getsource(wa_saglik.bekleyen_kayitlar)
    check("tazele_durumlar", "kuyrukta" in sql and "gonderiliyor" in sql and "belirsiz" in sql)
    check("tazele_kume", {"kuyrukta", "gonderiliyor", "belirsiz"} <= set(sw._TAZELE))
    yazilan = []
    eski = sw._durum_yaz

    def yaz(_rid, durum, neden=""):
        yazilan.append(durum)
        return True

    sw._durum_yaz = yaz
    try:
        an = datetime.now(timezone.utc)
        g = sw._satiri_tazele(
            {"id": 9, "durum": "kuyrukta", "created_at": an},
            "szwa:d:yoklama",
            lambda _a: {"durum": "gonderildi"},
        )
        check("kuyruk_netlesir", g.get("durum") == "gonderildi" and yazilan == ["gonderildi"])
        yazilan.clear()
        g2 = sw._satiri_tazele(
            {"id": 10, "durum": "gonderiliyor", "created_at": an},
            "szwa:d:yoklama2",
            lambda _a: {"durum": "gonderildi"},
        )
        check("suren_netlesir", g2.get("durum") == "gonderildi" and yazilan == ["gonderildi"])
    finally:
        sw._durum_yaz = eski


def test_ek_pencere():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        check("ek playwright", False)
        return
    js_yol = str(ROOT / "static" / "js" / "sozlesme-whatsapp.js")
    govdeler = []

    def hepsi(route):
        url = route.request.url
        if url.endswith("/sozlesme-whatsapp.js"):
            route.fulfill(path=js_yol, content_type="text/javascript")
            return
        if "kisiler" in url:
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"ok":true,"kisiler":[{"telefon":"905550001111","ad":""}]}',
            )
            return
        if url.endswith("/gonder"):
            try:
                j = json.loads(route.request.post_data or "{}")
            except Exception:
                j = {}
            govdeler.append({
                "serbest": j.get("serbest") is True,
                "son4": str(j.get("telefon") or "")[-4:],
            })
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"ok":true,"durum":"gonderildi"}',
            )
            return
        route.fulfill(
            status=200,
            content_type="text/html",
            body="<!doctype html><meta charset='utf-8'><script src='/sozlesme-whatsapp.js'></script>",
        )

    with sync_playwright() as pw:
        tarayici = pw.chromium.launch(channel="msedge", headless=True)
        s = tarayici.new_page()
        s.route("**/*", hepsi)
        s.goto("http://ek.test/sayfa")
        s.wait_for_function("() => typeof sozlesmeWhatsAppAc === 'function'")
        with s.expect_response("**/kisiler**"):
            s.evaluate(
                "() => sozlesmeWhatsAppAc({buton:'ust', musteriId:1, telefon:'905550001111', ad:'', mesaj:'deneme'})"
            )
        s.wait_for_selector("#sozlesme-wa-ek-ac")
        check("ek_baslangic_kapali", s.locator("#sozlesme-wa-ek").count() == 0 and s.locator("#sozlesme-wa-gonder").is_enabled())
        s.click("#sozlesme-wa-ek-ac")
        check("ek_bir_kutu", s.locator("#sozlesme-wa-ek").count() == 1)
        s.fill("#sozlesme-wa-ek", "12")
        s.click("#sozlesme-wa-gonder")
        check("ek_gecersiz_yok", "geçersiz" in s.locator("#sozlesme-wa-uyari").inner_text() and not govdeler)
        s.fill("#sozlesme-wa-ek", "05550001111")
        s.click("#sozlesme-wa-gonder")
        s.wait_for_function("() => document.getElementById('sozlesme-wa-sonuc').textContent.indexOf('Gönderildi') >= 0")
        gorunen = s.locator("#sozlesme-wa-sonuc").inner_text()
        check(
            "ek_tekil_pencere",
            len(govdeler) == 1 and not govdeler[0]["serbest"] and govdeler[0]["son4"] == "1111"
            and "1111" in gorunen and "905550001111" not in gorunen,
        )
        govdeler.clear()
        s.fill("#sozlesme-wa-ek", "05550004444")
        s.click("#sozlesme-wa-gonder")
        s.wait_for_function("() => (document.getElementById('sozlesme-wa-sonuc').textContent.match(/Gönderildi/g) || []).length >= 2")
        gorunen = s.locator("#sozlesme-wa-sonuc").inner_text()
        serbest = [g for g in govdeler if g["serbest"]]
        check(
            "ek_gecerli_pencere",
            len(govdeler) == 2 and len(serbest) == 1 and serbest[0]["son4"] == "4444"
            and "4444" in gorunen and "905550004444" not in gorunen and "905550001111" not in gorunen,
        )
        s.click("#sozlesme-wa-kapat")
        s.evaluate(
            "() => sozlesmeWhatsAppAc({buton:'makbuz', musteriId:1, tahsilatId:9, telefon:'905550001111', ad:'', mesaj:'deneme'})"
        )
        check(
            "tahsilat_ek_yok",
            s.locator("#sozlesme-wa-numaralar").is_hidden() and s.locator("#sozlesme-wa-ek").count() == 0
            and s.locator("#sozlesme-wa-tel").is_visible(),
        )
        tarayici.close()


def main():
    test_liste_ve_secim()
    test_gonderim()
    test_arayuz_kaynak()
    test_serbest()
    test_audit_govde()
    test_yoklama_tazele()
    test_ek_pencere()
    print("CASE alici secim ok")


if __name__ == "__main__":
    main()
