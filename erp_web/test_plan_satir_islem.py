# -*- coding: utf-8 -*-
"""Plan / dondurma satir islemleri: soft-delete, audit, kapi, kilit, odenmis ay.

Canli veritabanina yazmaz. Bellek sahte calistirici kullanir.
"""
import json
import logging
import os
from datetime import date

from flask import Flask

import db as dbmod
import sozlesme_plan_api as api
import sozlesme_plan_dondurma_api as da
from audit_log import anlik_al
from test_plan_degistir import Tx, _govde, _kur, _yanit
from test_plan_dondurma import Bellek, _zincir

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _liste_bagla(tx):
    dbmod.fetch_all = lambda sql, params=(): tx.bellek.oku(sql, params)
    dbmod.fetch_one = lambda sql, params=(): tx.bellek.oku(sql, params)


def test_plan_sil_soft_ve_audit():
    tx = Tx()
    app = Flask(__name__)
    _kur(tx)
    api._resync = lambda mid: True
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "867"
    _liste_bagla(tx)
    with app.test_request_context(json=_govde(), method="POST"):
        govde, kod = _yanit(api.plan_ekle_yanit(867))
    check("plan yazildi", kod == 200 and govde.get("ok") is True, (kod, govde))
    pid = govde["plan"]["id"]
    tx.bellek.satirlar[0]["telefon"] = "05000000000"
    tx.bellek.satirlar[0]["email"] = "gizli@ornek"
    once_sql = len(tx.bellek.sql)
    with app.test_request_context(json={}, method="POST"):
        sil, kod = _yanit(api.plan_sil_yanit(867, pid))
    satir = tx.bellek.satirlar[0]
    check("sil 200", kod == 200 and sil.get("ok") is True, (kod, sil))
    check("satir duruyor", satir["id"] == pid and satir.get("silindi_at") is not None and satir.get("silen") == "test-kullanici")
    check("aktif sil once iptal", satir.get("iptal_at") is not None)
    blob = "\n".join(tx.bellek.sql[once_sql:]).lower()
    check("delete yok", "delete from" not in blob)
    check("alter yalniz silmede", "add column if not exists silindi_at" in blob)
    with app.test_request_context(method="GET"):
        liste, kod = _yanit(api.plan_liste_yanit(867))
    kimlikler = [p.get("id") for p in (liste.get("gecmis") or [])]
    check("gecmis gizler", kod == 200 and pid not in kimlikler and liste.get("aktif") == [], kimlikler)
    anlik = json.loads(tx.bellek.audit[-1][5])
    check(
        "audit alanlari",
        tx.bellek.audit[-1][0] == "plan_sil"
        and anlik.get("id") == pid
        and anlik.get("musteri_id") == 867
        and "yeni_net" in anlik
        and "yeni_brut" in anlik
        and anlik.get("odeme") == "banka"
        and "telefon" not in anlik
        and "email" not in anlik,
        anlik,
    )
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "7"
    with app.test_request_context(json={}, method="POST"):
        _g, kod = _yanit(api.plan_sil_yanit(867, pid))
    check("kapi disi 404", kod == 404)


def test_audit_hatasi_islemi_durdurmaz():
    tx = Tx()
    app = Flask(__name__)
    _kur(tx)
    api._resync = lambda mid: True
    os.environ["PLAN_DEGISTIR_ENABLED"] = "1"
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "867"
    _liste_bagla(tx)
    kayitlar = []
    kayit = logging.getLogger("bestofficeerp.audit")
    kayit.addHandler(logging.Handler())
    kayit.handlers[-1].emit = lambda record: kayitlar.append(record.getMessage())
    with app.test_request_context(json=_govde(gecerlilik_ay="2025-01-01"), method="POST"):
        govde, kod = _yanit(api.plan_ekle_yanit(867))
    check("ikinci plan", kod == 200, kod)
    pid = govde["plan"]["id"]
    tx.bellek.audit_boz = True
    with app.test_request_context(json={}, method="POST"):
        sil, kod = _yanit(api.plan_sil_yanit(867, pid))
    satir = [s for s in tx.bellek.satirlar if s["id"] == pid][0]
    check("audit kirik olsa sil biter", kod == 200 and sil.get("ok") is True and satir.get("silindi_at") is not None, kod)
    check("audit hata loglandi", any("audit yazilamadi" in m for m in kayitlar), kayitlar)


def test_kilit_ayni():
    tx = Tx()
    app = Flask(__name__)
    _kur(tx, kilit=[{"ay": "2024-11", "fatura_tutari": 999, "kaynak": "fatura_tarihi"}])
    api._resync = lambda mid: True
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "867"
    with app.test_request_context(json=_govde(), method="POST"):
        govde, kod = _yanit(api.plan_ekle_yanit(867))
    check("kilit onaysiz 400", kod == 400 and govde.get("kilitlenen_aylar") and tx.commit == 0, (kod, govde))


def test_dondurma_duzenle_ve_sil():
    os.environ["PLAN_DEGISTIR_ENABLED"] = "1"
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "867"
    bellek = Bellek()
    yakalanan = []

    odeme_modu = {"acik": False}

    def odenen(mid, zincir, planlar, aday):
        kume = set(aday or [])
        yakalanan.append(kume)
        if odeme_modu["acik"] and kume == {"2024-08"}:
            return {"2024-08": 99999}
        return {}

    da._odenen_haritasi = odenen
    da.plan_tx = lambda fn: fn(bellek.calistir, bellek.oku, bellek.oku)
    da._kim = lambda: "test-kullanici"
    da._sema = lambda: None
    da._siniflar = lambda mid: {}
    api._onizleme_zinciri = lambda mid, kyc: (_zincir(), [], None)
    da._musteri_ve_kyc = lambda mid: (
        {"id": int(mid), "durum": "aktif"},
        {"sozlesme_tarihi": date(2023, 8, 1), "aylik_kira": 1000, "kdv_oran": 20},
    )
    dbmod.fetch_one = lambda sql, params=(): bellek.oku(sql, params)
    dbmod.fetch_all = lambda sql, params=(): bellek.oku(sql, params)
    app = Flask(__name__)
    with app.test_request_context("/x", method="POST", json={"baslangic_ay": "2024-08", "ay_sayisi": 1}):
        govde, kod = _yanit(da.dondurma_ekle_yanit(867))
    check("dondurma acildi", kod == 200, (kod, govde))
    did = govde["dondurma"]["id"]
    odeme_modu["acik"] = True
    yakalanan.clear()
    with app.test_request_context(
        "/x", method="POST", json={"baslangic_ay": "2024-08", "ay_sayisi": 1, "duzenlenen_dondurma_id": did}
    ):
        ret, kod = _yanit(da.dondurma_ekle_yanit(867))
    check("odenmis ay duzenle reddedildi", kod == 400 and bellek.satirlar[0]["iptal_at"] is None, (kod, ret))
    check("eski ay haric kumede", yakalanan and "2024-08" in yakalanan[-1], yakalanan)
    yakalanan.clear()
    with app.test_request_context(
        "/x", method="POST", json={"baslangic_ay": "2024-10", "ay_sayisi": 1, "duzenlenen_dondurma_id": did}
    ):
        yeni, kod = _yanit(da.dondurma_ekle_yanit(867))
    eski = bellek.satirlar[0]
    check(
        "duzenle eski iptal yeni acik",
        kod == 200 and eski["iptal_at"] is not None and yeni["dondurma"]["iptal_at"] is None and yeni["dondurma"]["id"] != did,
        (kod, yeni),
    )
    check("baska aya tasirken eski ay haricte", yakalanan and "2024-08" in yakalanan[-1] and "2024-10" in yakalanan[-1], yakalanan)
    acik = [s for s in bellek.satirlar if s["iptal_at"] is None][0]
    with app.test_request_context("/x", method="POST", json={}):
        sil, kod = _yanit(da.dondurma_sil_yanit(867, acik["id"]))
    check("dondurma sil soft", kod == 200 and acik.get("silindi_at") is not None and acik.get("iptal_at") is not None, (kod, sil))
    with app.test_request_context(method="GET"):
        liste, kod = _yanit(da.dondurma_liste_yanit(867))
    kimlikler = [p.get("id") for p in (liste.get("gecmis") or [])]
    check("dondurma gecmis gizler", kod == 200 and acik["id"] not in kimlikler, kimlikler)
    anlik = json.loads(bellek.audit[-1][5])
    check(
        "dondurma audit",
        bellek.audit[-1][0] == "dondurma_sil" and anlik.get("musteri_id") == 867 and "baslangic_ay" in anlik and "telefon" not in anlik,
        anlik,
    )
    da._musteri_ve_kyc = lambda mid: (
        {"id": int(mid), "durum": "pasif"},
        {"sozlesme_tarihi": date(2023, 8, 1), "aylik_kira": 1000, "kdv_oran": 20},
    )
    with app.test_request_context("/x", method="POST", json={"baslangic_ay": "2025-01", "ay_sayisi": 1}):
        _g, kod = _yanit(da.dondurma_ekle_yanit(867))
    check("pasif yeni dondurma kapali", kod == 400)
    iptal_satir = bellek.satirlar[0]
    with app.test_request_context("/x", method="POST", json={}):
        _g, kod = _yanit(da.dondurma_sil_yanit(867, iptal_satir["id"]))
    check("pasif sil acik", kod == 200 and iptal_satir.get("silindi_at") is not None, kod)
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "7"
    with app.test_request_context("/x", method="POST", json={}):
        _g, kod = _yanit(da.dondurma_liste_yanit(867))
    check("kapi disi liste 404", kod == 404)


def test_anlik_yasak():
    gorunen = anlik_al(
        {"id": 1, "musteri_id": 3, "yeni_net": 10, "telefon": "1", "email": "a@b", "tc": "111"},
        ("id", "musteri_id", "yeni_net", "telefon", "email", "tc"),
    )
    check("helper yasak dusur", gorunen.get("id") == 1 and "telefon" not in gorunen and "email" not in gorunen and "tc" not in gorunen, gorunen)


def main():
    test_anlik_yasak()
    test_plan_sil_soft_ve_audit()
    test_audit_hatasi_islemi_durdurmaz()
    test_kilit_ayni()
    test_dondurma_duzenle_ve_sil()
    if FAILS:
        print("PLAN_SATIR_FAIL", FAILS)
        raise SystemExit(1)
    print("plan_satir_islem ok")


if __name__ == "__main__":
    main()
