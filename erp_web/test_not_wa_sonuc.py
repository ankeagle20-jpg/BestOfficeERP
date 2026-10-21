"""Not WhatsApp: deneme anahtarı, servis sonucu, etiket, belirsiz yeniden denenmez."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone

from routes import erp_notlar_routes as en
import wa_saglik as ws


def check(ad, ok, detay=""):
    if not ok:
        raise SystemExit(f"FAIL {ad} {detay}")
    print("ok", ad)


ANAHTAR = "szwa:d:aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def test_etiket():
    ortak = {"whatsapp_gonderilsin": True}
    check("etiket_eski_kuyruk", en._wa_durum_etiket({
        **ortak, "whatsapp_gonderim_durumu": "gonderildi"}) == "Kuyruğa alındı")
    check("etiket_kuyruk_kod", en._wa_durum_etiket({
        **ortak, "whatsapp_gonderim_durumu": "gonderildi",
        "whatsapp_sonuc_kodu": "kuyruk"}) == "Kuyruğa alındı")
    check("etiket_dogrulandi", en._wa_durum_etiket({
        **ortak, "whatsapp_gonderim_durumu": "gonderildi",
        "whatsapp_sonuc_kodu": "dogrulandi"}) == "Gönderildi")
    check("etiket_belirsiz", en._wa_durum_etiket({
        **ortak, "whatsapp_gonderim_durumu": "belirsiz",
    }) == "Sonuç belirsiz, telefondan kontrol edin")
    check("etiket_basarisiz", en._wa_durum_etiket({
        **ortak, "whatsapp_gonderim_durumu": "basarisiz"}) == "Gönderilemedi")
    check("rozet_ayni", en._wa_rozet({
        **ortak, "whatsapp_gonderim_durumu": "gonderildi",
        "whatsapp_sonuc_kodu": "dogrulandi"}) == "Gönderildi")


def test_yorum():
    check("yorum_gonderildi", en._wa_kuyruk_yorum({"durum": "gonderildi", "kod": "ack_dogrulandi"}) == "gonderildi")
    check("yorum_belirsiz", en._wa_kuyruk_yorum({"durum": "belirsiz", "kod": "bos_donus_belirsiz"}) == "belirsiz")
    check("yorum_bos", en._wa_kuyruk_yorum({"durum": "basarisiz", "kod": "bos_donus"}) == "belirsiz")
    check("yorum_hata", en._wa_kuyruk_yorum({"durum": "basarisiz", "kod": "gonderim_hata"}) == "basarisiz")
    check("yorum_zaman", en._wa_kuyruk_yorum({"durum": "basarisiz", "kod": "zaman_asimi"}) == "basarisiz")
    check("yorum_yok", en._wa_kuyruk_yorum({"durum": "yok"}) == "birak")
    check("yorum_erisilemedi", en._wa_kuyruk_yorum({"durum": "erisilemedi"}) == "birak")


def test_karar():
    ayni, ana = en._wa_gonder_karar(ANAHTAR, {"durum": "kuyrukta"})
    check("karar_kuyrukta_ayni", ayni == "ayni" and ana == ANAHTAR)
    bekle, ana2 = en._wa_gonder_karar(ANAHTAR, {"durum": "erisilemedi"})
    check("karar_erisilemedi", bekle == "bekle" and ana2 == ANAHTAR)
    yeni1, k1 = en._wa_gonder_karar("", {})
    yeni2, k2 = en._wa_gonder_karar(k1, {"durum": "yok"})
    check("karar_yeni_bicim", yeni1 == "yeni" and en._SZWA_ANAHTAR.match(k1) is not None)
    check("karar_tekrar_degisir", yeni2 == "yeni" and k2 != k1 and en._SZWA_ANAHTAR.match(k2) is not None)
    bitti, k3 = en._wa_gonder_karar(ANAHTAR, {"durum": "gonderildi", "kod": "ack_dogrulandi"})
    check("karar_eski_sonuc_yeni", bitti == "yeni" and k3 != ANAHTAR)


def test_gonder_cagri():
    kayit = {"anahtar": "", "node": [], "yaz": []}
    eski_s = en._wa_sahiplen
    eski_sor = en._wa_sonuc_sor
    eski_node = en._wa_node_gonder
    eski_yaz = en._wa_gonderildi_yaz
    eski_kaydet = en._wa_anahtar_kaydet
    eski_bekle = en._wa_kuyruk_bekle

    def sahiplen(_nid):
        return {
            "whatsapp_telefon": "1", "whatsapp_mesaj": "m",
            "whatsapp_gonderim_deneme": 0,
            "created_at": datetime.now(timezone.utc),
            "whatsapp_kuyruk_anahtar": kayit["anahtar"],
        }

    try:
        en._wa_sahiplen = sahiplen
        en._wa_sonuc_sor = lambda _a: {"durum": "yok"}
        en._wa_node_gonder = lambda *a, **k: kayit["node"].append(k.get("anahtar")) or ("ok", "")
        en._wa_gonderildi_yaz = lambda _n: kayit["yaz"].append("gonderildi")
        en._wa_anahtar_kaydet = lambda _n, a: kayit.__setitem__("anahtar", a)
        en._wa_kuyruk_bekle = lambda _n: kayit["yaz"].append("bekle")
        check("ilk_gonderim", en._wa_gonder_bir(9) == "gonderildi" and len(kayit["node"]) == 1)
        ilk = kayit["node"][0]
        check("ikinci_anahtar", en._wa_gonder_bir(9) == "gonderildi" and kayit["node"][1] != ilk)
        en._wa_sonuc_sor = lambda _a: {"durum": "gonderiliyor"}
        once = len(kayit["node"])
        check("kuyrukta_ikinci_yok", en._wa_gonder_bir(9) == "gonderildi" and len(kayit["node"]) == once)
    finally:
        en._wa_sahiplen = eski_s
        en._wa_sonuc_sor = eski_sor
        en._wa_node_gonder = eski_node
        en._wa_gonderildi_yaz = eski_yaz
        en._wa_anahtar_kaydet = eski_kaydet
        en._wa_kuyruk_bekle = eski_bekle


def test_tazele_yok_ve_butce():
    uygulanan = []
    eski_fa = en.fetch_all
    eski_uy = en._wa_tazele_uygula
    try:
        en.fetch_all = lambda *a, **k: [{
            "id": 7, "whatsapp_kuyruk_anahtar": ANAHTAR,
            "whatsapp_gonderim_deneme": 1,
            "created_at": datetime.now(timezone.utc),
        }]
        en._wa_tazele_uygula = lambda *a, **k: uygulanan.append(a[1])
        say = en._wa_not_sonuc_tazele(sonuc_fn=lambda _a: {"durum": "yok"})
        check("yok_yazmaz", say["birak"] == 1 and uygulanan == [])
        en._wa_not_sonuc_tazele(sonuc_fn=lambda _a: {"durum": "gonderildi", "kod": "fetch_dogrulandi"})
        en._wa_not_sonuc_tazele(sonuc_fn=lambda _a: {"durum": "belirsiz", "kod": "bos_donus_belirsiz"})
        en._wa_not_sonuc_tazele(sonuc_fn=lambda _a: {"durum": "basarisiz", "kod": "oturum"})
        check("yorum_yazilir", uygulanan == ["gonderildi", "belirsiz", "basarisiz"])
        say_bos = en._wa_not_sonuc_tazele(butce_sn=0, sonuc_fn=lambda _a: {"durum": "gonderildi"})
        check("butce_keser", say_bos == {"gonderildi": 0, "belirsiz": 0, "basarisiz": 0, "birak": 0})
    finally:
        en.fetch_all = eski_fa
        en._wa_tazele_uygula = eski_uy


def test_belirsiz_yeniden_yok():
    src = inspect.getsource(en.gonder_whatsapp_notlari)
    check("retry_yalniz_basarisiz", "whatsapp_gonderim_durumu = 'basarisiz'" in src)
    check("retry_belirsiz_yok", "whatsapp_gonderim_durumu = 'belirsiz'" not in src)
    check("belirsiz_kapali", "belirsiz" in en._WA_KAPALI)


def test_liste_saat():
    an = datetime(2026, 10, 21, 8, 51, tzinfo=timezone.utc)

    def _oku(sql, params=None):
        if "sozlesme_whatsapp_gonderim" in sql:
            return [{"id": 1, "zaman": an}]
        return []

    liste = ws.belirsiz_kayitlar(oku=_oku, limit=1)
    zaman = liste[0]["zaman"]
    check("liste_tsi", "11:51" in zaman and "08:51" not in zaman, zaman)


def main():
    test_etiket()
    test_yorum()
    test_karar()
    test_gonder_cagri()
    test_tazele_yok_ve_butce()
    test_belirsiz_yeniden_yok()
    test_liste_saat()
    print("CASE not wa sonuc ok")


if __name__ == "__main__":
    main()
