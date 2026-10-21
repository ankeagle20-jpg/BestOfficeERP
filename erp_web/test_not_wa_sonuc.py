"""Not WhatsApp: deneme anahtarı, servis sonucu, etiket, belirsiz yeniden denenmez."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path

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
    check("yorum_zaman", en._wa_kuyruk_yorum({"durum": "basarisiz", "kod": "zaman_asimi"}) == "belirsiz")
    check("zaman_asimi_kesin_hata_degil", "zaman_asimi" not in en._WA_BASARISIZ_KOD)
    check("kesin_hata", en._WA_BASARISIZ_KOD == frozenset(("gonderim_hata", "medya_hata", "oturum")))
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
        en._wa_not_sonuc_tazele(sonuc_fn=lambda _a: {"durum": "basarisiz", "kod": "zaman_asimi"})
        check("yorum_yazilir", uygulanan == ["gonderildi", "belirsiz", "basarisiz", "belirsiz"])
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


def test_zaman_asimi_yeniden_yok():
    uygulanan = []
    eski_fa = en.fetch_all
    eski_uy = en._wa_tazele_uygula
    try:
        en.fetch_all = lambda *a, **k: [{
            "id": 8, "whatsapp_kuyruk_anahtar": ANAHTAR,
            "whatsapp_gonderim_deneme": 1,
            "created_at": datetime.now(timezone.utc),
        }]
        en._wa_tazele_uygula = lambda *a, **k: uygulanan.append(a[1])
        en._wa_not_sonuc_tazele(sonuc_fn=lambda _a: {"durum": "basarisiz", "kod": "zaman_asimi"})
        check("zaman_asimi_belirsiz_yazar", uygulanan == ["belirsiz"])
        check("zaman_asimi_basarisiz_yazmaz", "basarisiz" not in uygulanan)
    finally:
        en.fetch_all = eski_fa
        en._wa_tazele_uygula = eski_uy


def test_ddl_ve_okuma():
    from pglast import parse_sql

    src = inspect.getsource(en.ensure_erp_notlar_tablolari)
    check("ensure_sema", '_tenant_schema_for_request() or "public"' in src)
    for ad in ("whatsapp_kuyruk_anahtar", "whatsapp_sonuc_kodu"):
        stmt = f"ALTER TABLE erp_notlar ADD COLUMN IF NOT EXISTS {ad} TEXT"
        check("ensure_" + ad, stmt in src)
        agac = parse_sql(stmt)
        check("pglast_" + ad, bool(agac) and agac[0].stmt.__class__.__name__ == "AlterTableStmt")
    dump = Path(__file__).resolve().parent / "tenant_ddl" / "schema_only_public.sql"
    metin = dump.read_text(encoding="utf-8", errors="ignore")
    check("dump_tablo_yok", "erp_notlar" not in metin)
    is_src = inspect.getsource(en.gonder_whatsapp_notlari)
    check("is_once_ensure", is_src.find("ensure_erp_notlar_tablolari") < is_src.find("_wa_not_sonuc_tazele"))
    liste = inspect.getsource(en._notlari_getir)
    check("liste_once_ensure", liste.find("ensure_erp_notlar_tablolari") < liste.find("SELECT"))
    check("liste_yildiz", "n.*" in en._LISTE_COLS)

    def _oku(sql, params=None):
        if "whatsapp_sonuc_kodu" in sql:
            raise RuntimeError("kolon_yok")
        if "FROM erp_notlar" in sql and "AS g24" in sql:
            return [{"g24": 4, "son_ok": None}]
        return [{}]

    sayac = ws.saglik_sayaclari(oku=_oku)
    check("saglik_fallback", sayac["gonderildi_24s_not"] == 4 and sayac["gonderildi_24s_not_dogrulandi"] == 0)

    eski_fa = en.fetch_all
    try:
        def _patla(*a, **k):
            raise RuntimeError("kolon_yok")

        en.fetch_all = _patla
        say = en._wa_not_sonuc_tazele()
        check("sorgu_fallback", say == {"gonderildi": 0, "belirsiz": 0, "basarisiz": 0, "birak": 0})
    finally:
        en.fetch_all = eski_fa


def test_servis_kapali_isi_tutmaz():
    imza = inspect.signature(en._wa_sonuc_sor)
    check("baglanti_tavani", imza.parameters["timeout"].default == (3, 3))
    check("limit_20", en._WA_TAZELE_LIMIT == 20)
    check("butce_30", en._WA_TAZELE_BUTCE_SN == 30.0)
    app = (Path(__file__).resolve().parent / "app.py").read_text(encoding="utf-8")
    check("scheduler_is", "BackgroundScheduler" in app and 'id="erp_not_whatsapp"' in app)
    check("istek_tazele_yok", "_wa_not_sonuc_tazele" not in inspect.getsource(en.api_olustur))
    check("istek_arkaplan", "_wa_arkaplan_baslat" in inspect.getsource(en.api_olustur))
    cagri = {"n": 0}
    saat = {"t": 0.0}

    def _simdi():
        return saat["t"]

    def _sor(_a):
        cagri["n"] += 1
        saat["t"] += 3.0
        return {"durum": "erisilemedi"}

    satir = {
        "id": 1, "whatsapp_kuyruk_anahtar": ANAHTAR,
        "whatsapp_gonderim_deneme": 0, "created_at": datetime.now(timezone.utc),
    }
    eski_fa = en.fetch_all
    eski_uy = en._wa_tazele_uygula
    try:
        en.fetch_all = lambda *a, **k: [satir] * 20
        en._wa_tazele_uygula = lambda *a, **k: None
        en._wa_not_sonuc_tazele(sonuc_fn=_sor, simdi_fn=_simdi)
        check("kapali_on_sorgu", cagri["n"] == 10, str(cagri["n"]))
        check("kapali_otuz", saat["t"] == 30.0)
    finally:
        en.fetch_all = eski_fa
        en._wa_tazele_uygula = eski_uy


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
    test_zaman_asimi_yeniden_yok()
    test_ddl_ve_okuma()
    test_servis_kapali_isi_tutmaz()
    test_liste_saat()
    print("CASE not wa sonuc ok")


if __name__ == "__main__":
    main()
