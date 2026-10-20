# -*- coding: utf-8 -*-
"""WhatsApp Sağlık: kontrol kartları (sahte yanıtlar), rol kapısı, tazeleme."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)
os.environ["BESTOFFICE_HTTP_FILE_LOG"] = "0"

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


BOŞ_SAYAC = {"bekleyen": 0, "yasli_takili": 0, "gonderildi_24s": 0, "hata_24s": 0,
             "belirsiz_24s": 0, "son_gonderildi": None, "son_hata": "",
             "belirsiz_toplam": 0, "kapatilabilir": 0}


def _kart(kartlar, anahtar):
    return next((k for k in kartlar if k.get("anahtar") == anahtar), {})


def test_kontroller():
    import wa_saglik as ws

    k = ws.kontrolleri_uret("baglanti_yok", None, dict(BOŞ_SAYAC))
    check("ulasim_baglanti_kirmizi", _kart(k, "servis")["seviye"] == "hata")
    check("ulasim_baglanti_metin", "tünel" in _kart(k, "servis")["aciklama"])
    check("ulasim_yokken_oturum_hata", _kart(k, "oturum")["seviye"] == "hata")

    k = ws.kontrolleri_uret("zaman_asimi", None, dict(BOŞ_SAYAC))
    check("ulasim_timeout_kirmizi", _kart(k, "servis")["seviye"] == "hata"
          and "yanıt vermedi" in _kart(k, "servis")["aciklama"])

    k = ws.kontrolleri_uret("http_5xx", None, dict(BOŞ_SAYAC))
    check("ulasim_5xx_kirmizi", _kart(k, "servis")["seviye"] == "hata")

    k = ws.kontrolleri_uret("ok", "qr", dict(BOŞ_SAYAC))
    check("oturum_qr_sari", _kart(k, "oturum")["seviye"] == "uyari")
    k = ws.kontrolleri_uret("ok", "hazir", dict(BOŞ_SAYAC))
    check("oturum_hazir_yesil", _kart(k, "oturum")["seviye"] == "ok")
    k = ws.kontrolleri_uret("ok", "bagli_degil", dict(BOŞ_SAYAC))
    check("oturum_yok_sari", _kart(k, "oturum")["seviye"] == "uyari"
          and "Chrome" in _kart(k, "oturum")["aciklama"])

    s = dict(BOŞ_SAYAC, bekleyen=3, yasli_takili=2, gonderildi_24s=5, hata_24s=1,
             belirsiz_24s=1, belirsiz_toplam=4, son_gonderildi="2026-10-20 09:12",
             son_hata="zaman_asimi")
    k = ws.kontrolleri_uret("ok", "hazir", s)
    check("kuyruk_yasli_sari", _kart(k, "kuyruk")["seviye"] == "uyari")
    check("kuyruk_dagilim_metni", "5 gönderildi" in _kart(k, "kuyruk")["aciklama"]
          and "1 hata" in _kart(k, "kuyruk")["aciklama"])
    check("belirsiz_sari", _kart(k, "belirsiz")["seviye"] == "uyari"
          and "4 kaydın" in _kart(k, "belirsiz")["aciklama"])
    check("son_hata_kartta", "zaman_asimi" in _kart(k, "son_islem")["aciklama"]
          and "2026-10-20 09:12" in _kart(k, "son_islem")["aciklama"])

    k = ws.kontrolleri_uret("ok", "hazir", dict(BOŞ_SAYAC))
    check("sakin_durum_yesil", all(x["seviye"] == "ok" for x in k), k)


def test_oturum_kodu():
    import wa_saglik as ws

    check("kod_hazir", ws.oturum_kodu_govde({"hazir": True, "bagli": True}) == "hazir")
    check("kod_qr_durum", ws.oturum_kodu_govde({"durum": "qr"}) == "qr")
    check("kod_qr_topar", ws.oturum_kodu_govde({"toparlama_durumu": "qr"}) == "qr")
    check("kod_baglaniyor", ws.oturum_kodu_govde({"durum": "starting"}) == "baglaniyor")
    check("kod_bagli_degil", ws.oturum_kodu_govde({"durum": "kapali"}) == "bagli_degil")
    check("kod_bos_bilinmiyor", ws.oturum_kodu_govde({}) == "bilinmiyor")


def test_tazele():
    import wa_saglik as ws

    kayitlar = [{"id": 1, "durum": "kuyrukta", "anahtar": "szwa:d:a"},
                {"id": 2, "durum": "belirsiz", "anahtar": "szwa:d:b"},
                {"id": 3, "durum": "belirsiz", "anahtar": "szwa:d:c"}]
    cevap = {"szwa:d:a": "gonderildi", "szwa:d:b": "gonderildi", "szwa:d:c": "yok"}

    def sorgu(anahtar):
        return {"durum": cevap.get(anahtar, "yok")}

    # Mevcut akistaki davranisin sade esdegeri: gonderildi/basarisiz yazilir, yok belirsiz.
    def tazele_fn(row, anahtar, fn):
        row = dict(row)
        d = (fn(anahtar) or {}).get("durum")
        if d in ("gonderildi", "basarisiz"):
            row["durum"] = d
        elif d == "yok":
            row["durum"] = "belirsiz"
        return row

    oz = ws.tazele_calistir(kayitlar, sorgu, tazele_fn)
    check("tazele_ozet", oz == {"taranan": 3, "gonderildi": 2, "basarisiz": 0,
                                "belirsiz_kalan": 1, "degismedi": 0, "guncellenen": 2,
                                "kalan": 0}, oz)


def test_kapi1():
    import time as _t
    from datetime import datetime, timedelta, timezone

    import sozlesme_whatsapp as sw
    import wa_saglik as ws

    sw._durum_yaz = lambda *a, **k: None  # test: DB yazimi yok

    simdi = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    check("yas_3sa_sorma", not sw._belirsiz_sorgulanir(simdi - timedelta(hours=3), simdi))
    check("yas_30dk_sor", sw._belirsiz_sorgulanir(simdi - timedelta(minutes=30), simdi))
    check("yas_yoksa_sor", sw._belirsiz_sorgulanir(None))

    sayac = {"n": 0}

    def sorgu(_anahtar):
        sayac["n"] += 1
        return {"durum": "yok"}

    eski_belirsiz = {"id": 1, "durum": "belirsiz", "anahtar": "szwa:d:x",
                     "created_at": datetime.now(timezone.utc) - timedelta(hours=3)}
    sw._satiri_tazele(eski_belirsiz, "szwa:d:x", sorgu)
    check("yasli_belirsiz_sorgu_yok", sayac["n"] == 0, sayac)

    genc_belirsiz = {"id": 2, "durum": "belirsiz", "anahtar": "szwa:d:y",
                     "created_at": datetime.now(timezone.utc) - timedelta(minutes=30)}
    sw._satiri_tazele(genc_belirsiz, "szwa:d:y", sorgu)
    check("genc_belirsiz_sorgu_var", sayac["n"] == 1, sayac)

    eski_kuyruk = {"id": 3, "durum": "kuyrukta", "anahtar": "szwa:d:z",
                   "created_at": datetime.now(timezone.utc) - timedelta(hours=5)}
    sw._satiri_tazele(eski_kuyruk, "szwa:d:z", sorgu)
    check("kuyruk_yasli_sorgu_yok", sayac["n"] == 1, sayac)

    genc_kuyruk = {"id": 4, "durum": "kuyrukta", "anahtar": "szwa:d:w",
                   "created_at": datetime.now(timezone.utc) - timedelta(minutes=20)}
    sw._satiri_tazele(genc_kuyruk, "szwa:d:w", sorgu)
    check("kuyruk_genc_sorgu_var", sayac["n"] == 2, sayac)

    gonderiliyor_eski = {"id": 5, "durum": "gonderiliyor", "anahtar": "szwa:d:v",
                         "created_at": datetime.now(timezone.utc) - timedelta(hours=3)}
    sw._satiri_tazele(gonderiliyor_eski, "szwa:d:v", sorgu)
    check("gonderiliyor_yasli_sorgu_yok", sayac["n"] == 2, sayac)

    def tazele_fn(row, anahtar, fn):
        return dict(row, durum="gonderildi")

    cok = [{"id": i, "durum": "kuyrukta", "anahtar": f"szwa:d:{i}"} for i in range(25)]
    oz = ws.tazele_calistir(cok, sorgu, tazele_fn, azami=20, butce_sn=30)
    check("buton_azami_20", oz["taranan"] == 20 and oz["kalan"] == 5, oz)

    def yavas_fn(row, anahtar, fn):
        _t.sleep(0.05)
        return dict(row)

    oz2 = ws.tazele_calistir(cok, sorgu, yavas_fn, azami=25, butce_sn=0.13)
    check("butce_siniri", 0 < oz2["taranan"] < 25 and oz2["kalan"] > 0, oz2)


ROL = {"v": "personel"}


def _istemci():
    from flask import Flask
    from auth import User, login_manager

    from routes import whatsapp_routes

    @login_manager.user_loader
    def _load(_uid):
        return User(id=1, username="t", full_name="T", role=ROL["v"],
                    aktif_mi=True, security_stamp="test-stamp")

    app = Flask(__name__)
    app.secret_key = "test"
    login_manager.init_app(app)
    app.register_blueprint(whatsapp_routes.bp)
    client = app.test_client()
    with client.session_transaction() as s:
        s["_user_id"] = "1"
        s["_fresh"] = True
    return client


def test_eski_kapat_ve_retry():
    from datetime import datetime, timedelta, timezone

    import sozlesme_whatsapp as sw
    import wa_saglik as ws

    # --- eski_kapat: sahte oku/yaz ile ---
    yazilan = []

    def _oku(sql, params=None):
        check("kapat_sorgu_durumlar", "'kuyrukta', 'gonderiliyor'" in sql.replace('"', "'")
              and "created_at <" in sql, sql[:120])
        return [{"id": 5}, {"id": 8}]

    def _yaz(sql, params=None):
        yazilan.append((sql, params))

    sonuc = ws.eski_kapat(oku=_oku, yaz=_yaz)
    check("kapat_sayi_id", sonuc == {"adet": 2, "kayit_idler": [5, 8]}, sonuc)
    check("kapat_update", len(yazilan) == 1 and "suresi_doldu" in yazilan[0][0]
          and "durum IN ('kuyrukta', 'gonderiliyor')" in yazilan[0][0]
          and yazilan[0][1] == ([5, 8],), yazilan[:1])

    yazilan.clear()
    sonuc0 = ws.eski_kapat(oku=lambda sql, params=None: [], yaz=_yaz)
    check("kapat_bos_yazma_yok", sonuc0 == {"adet": 0, "kayit_idler": []}
          and not yazilan, (sonuc0, yazilan))

    # --- suresi_doldu retry/tazele disi ---
    check("suresi_doldu_tazele_disi", "suresi_doldu" not in sw._TAZELE, sw._TAZELE)
    sayac = {"n": 0}

    def _sorgu(_a):
        sayac["n"] += 1
        return {"durum": "gonderildi"}

    kayit = {"id": 9, "durum": "suresi_doldu", "anahtar": "szwa:d:k",
             "created_at": datetime.now(timezone.utc) - timedelta(minutes=5)}
    donen = sw._satiri_tazele(kayit, "szwa:d:k", _sorgu)
    check("suresi_doldu_tazelenmez", sayac["n"] == 0 and donen["durum"] == "suresi_doldu",
          (sayac, donen))

    # --- saglik sayaclari SQL'i suresi_doldu'yu bekleyene saymaz ---
    sorgular = []

    def _sayac_oku(sql, params=None):
        sorgular.append(str(sql))
        if "COUNT(*) FILTER" in sql and "sozlesme_whatsapp_gonderim" in sql:
            return [{"bekleyen": 4, "yasli": 3, "kapatilabilir": 3, "g24": 0,
                     "h24": 0, "b24": 0, "btoplam": 0, "son_ok": None}]
        return [{}]

    sayaclar = ws.saglik_sayaclari(oku=_sayac_oku)
    check("sayac_kapatilabilir", sayaclar["kapatilabilir"] == 3, sayaclar)
    bek_sql = next((s for s in sorgular if "AS bekleyen" in s), "")
    check("sayac_sql_bekleyen_kisitli", "suresi_doldu" not in bek_sql
          and "IN ('kuyrukta','gonderiliyor')" in bek_sql, bek_sql[:160])

    # --- bekleyen_kayitlar (tazele listesi) suresi_doldu icermez ---
    lst_sql = []
    kayitlar = ws.bekleyen_kayitlar(oku=lambda sql, p=None: (lst_sql.append(str(sql))
                                                             or [{"id": 1,
                                                                  "durum": "kuyrukta",
                                                                  "anahtar": "szwa:d:1",
                                                                  "created_at": None}]))
    check("tazele_liste_sql", lst_sql and "suresi_doldu" not in lst_sql[0]
          and "'kuyrukta', 'gonderiliyor', 'belirsiz'" in lst_sql[0]
          and [k["id"] for k in kayitlar] == [1], lst_sql[:1])

    # --- kart ipucu ---
    s = dict(BOŞ_SAYAC, bekleyen=4, yasli_takili=3, kapatilabilir=3)
    k = ws.kontrolleri_uret("ok", "hazir", s)
    check("kart_ipucu", "3 eski kayıt kapatılabilir" in _kart(k, "kuyruk")["aciklama"],
          _kart(k, "kuyruk"))
    k0 = ws.kontrolleri_uret("ok", "hazir", dict(BOŞ_SAYAC))
    check("kart_ipucu_sifir_yok", "kapatılabilir" not in _kart(k0, "kuyruk")["aciklama"],
          _kart(k0, "kuyruk"))


def test_rol_kapisi():
    client = _istemci()
    ROL["v"] = "personel"
    r = client.get("/whatsapp/api/saglik-ozet")
    check("personel_api_403", r.status_code == 403, r.status_code)
    r = client.post("/whatsapp/api/saglik-eski-kapat")
    check("personel_kapat_403", r.status_code == 403, r.status_code)
    ROL["v"] = "muhasebe"
    r = client.get("/whatsapp/api/saglik-ozet")
    govde = r.get_json(silent=True) or {}
    check("muhasebe_api_200", r.status_code == 200 and govde.get("ok") is True,
          (r.status_code, str(govde)[:160]))
    anahtarlar = [k.get("anahtar") for k in govde.get("kontroller") or []]
    check("kartlar_tam", anahtarlar == ["servis", "oturum", "kuyruk", "son_islem",
                                        "belirsiz"], anahtarlar)


def main():
    test_kontroller()
    test_oturum_kodu()
    test_tazele()
    test_kapi1()
    test_eski_kapat_ve_retry()
    test_rol_kapisi()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
