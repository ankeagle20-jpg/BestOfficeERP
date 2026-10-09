# -*- coding: utf-8 -*-
"""Plan değiştirme uçları. Canlı veritabanına bağlanmaz."""
import logging
import os
import subprocess
from copy import deepcopy
from datetime import date
from pathlib import Path

from flask import Flask

from auth import login_manager
from routes.giris_routes import bp
from test_sozlesme_plan import Bellek
import sozlesme_plan_api as api

ROOT = Path(__file__).resolve().parent
FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


class Tx:
    def __init__(self):
        self.bellek = Bellek()
        self.commit = 0
        self.rollback = 0

    def __call__(self, fn):
        snap = deepcopy(self.bellek.satirlar)
        nsql = len(self.bellek.sql)
        try:
            out = fn(self.bellek.calistir, self.bellek.oku, self.bellek.oku)
        except Exception:
            self.bellek.satirlar[:] = snap
            del self.bellek.sql[nsql:]
            self.rollback += 1
            raise
        self.commit += 1
        return out


def _app():
    app = Flask(__name__)
    app.secret_key = "plan-test"
    login_manager.init_app(app)
    app.register_blueprint(bp, url_prefix="/giris")
    return app


def _govde(**ek):
    g = {
        "gecerlilik_ay": "2024-11-18",
        "yeni_net": 2000,
        "kdv_oran": 20,
        "odeme": "banka",
        "yeni_brut": 2400,
    }
    g.update(ek)
    return g


def _kur(tx, kilit=None, durum="aktif", bugun=None):
    api.plan_tx = tx
    api._sema = lambda: "tenant_abc"
    api._kim = lambda: "test-kullanici"
    # Geçmiş aya plan girilemediği için varsayılan "bugün", test planlarından (2024-11 vb.) önce.
    _b = bugun or date(2024, 10, 9)
    api._bugun = lambda: _b
    api._kilitler = lambda mid: list(kilit or [])
    api._tahsil_haritasi = lambda mid: {}
    api._musteri_ve_kyc = lambda mid: (
        {"id": int(mid), "durum": durum},
        {"sozlesme_tarihi": date(2023, 8, 1), "aylik_kira": 1000, "kdv_oran": 20},
    )
    api._onizleme_zinciri = lambda mid, kyc: (
        {
            "sozlesme_tarihi": date(2023, 8, 1),
            "artis_tarihi": date(2023, 8, 1),
            "ay_sayisi": 36,
            "aylik_net": 1000,
            "kdv_oran": 20,
            "kira_nakit": False,
            "tufe": {2024: {8: 10}, 2025: {8: 5}},
        },
        [],
        None,
    )
    os.environ["PLAN_DEGISTIR_ENABLED"] = "1"
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "7"


def _yanit(yanit):
    if isinstance(yanit, tuple):
        resp, kod = yanit
        return resp.get_json(), kod
    return yanit.get_json(), yanit.status_code


def test_kapi_ve_yetki():
    app = _app()
    client = app.test_client()
    r = client.get("/giris/api/musteri/7/plan")
    check("oturum_401", r.status_code == 401)
    kurallar = {rule.rule for rule in app.url_map.iter_rules()}
    check(
        "rotalar kayitli",
        "/giris/api/musteri/<int:mid>/plan" in kurallar
        and "/giris/api/musteri/<int:mid>/plan/onizleme" in kurallar
        and "/giris/api/musteri/<int:mid>/plan/<int:plan_id>/iptal" in kurallar,
    )
    os.environ.pop("PLAN_DEGISTIR_ENABLED", None)
    os.environ.pop("PLAN_DEGISTIR_MUSTERI_IDS", None)
    sayac = {"m": 0}
    eski = api._musteri_ve_kyc

    def say(mid):
        sayac["m"] += 1
        return eski(mid)

    api._musteri_ve_kyc = say
    with app.test_request_context("/giris/api/musteri/7/plan"):
        _g, kod = _yanit(api.plan_liste_yanit(7))
    check("kapi kapali 404", kod == 404 and sayac["m"] == 0)
    os.environ["PLAN_DEGISTIR_ENABLED"] = "1"
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = ""
    with app.test_request_context("/giris/api/musteri/7/plan"):
        _g, kod = _yanit(api.plan_liste_yanit(7))
    check("bos liste 404", kod == 404 and sayac["m"] == 0)
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "7"
    with app.test_request_context("/giris/api/musteri/8/plan"):
        _g, kod = _yanit(api.plan_liste_yanit(8))
    check("izin disi 404", kod == 404 and sayac["m"] == 0)

    kayitlar = []

    class Tut(logging.Handler):
        def emit(self, record):
            kayitlar.append(record.getMessage())

    tut = Tut()
    api._LOG.addHandler(tut)
    api._LOG.setLevel(logging.INFO)
    os.environ["PLAN_DEGISTIR_ENABLED"] = "gizli-deger"
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "424242"
    try:
        api._kapi_acik(7)
    finally:
        api._LOG.removeHandler(tut)
    birlesik = " ".join(kayitlar)
    check("kapi degeri loglanmaz", "gizli-deger" not in birlesik and "424242" not in birlesik)


def test_yazma():
    app = _app()
    tx = Tx()
    resync = []
    api._resync = lambda mid: resync.append(int(mid)) or True
    _kur(tx)
    try:
        with app.test_request_context(json=_govde(musteri_id=8), method="POST"):
            _g, kod = _yanit(api.plan_ekle_yanit(7))
        check("mid uyusmaz 403", kod == 403 and tx.commit == 0 and resync == [])

        api._musteri_ve_kyc = lambda mid: (None, None)
        with app.test_request_context(json=_govde(), method="POST"):
            _g, kod = _yanit(api.plan_ekle_yanit(7))
        check("musteri yok 403", kod == 403 and tx.commit == 0)

        _kur(tx, durum="pasif")
        api._resync = lambda mid: resync.append(int(mid)) or True
        with app.test_request_context(json=_govde(), method="POST"):
            govde, kod = _yanit(api.plan_ekle_yanit(7))
        check("pasif hata", kod == 400 and govde.get("mesaj") == "Pasif müşteriye plan eklenemez." and tx.commit == 0)

        _kur(tx)
        api._resync = lambda mid: resync.append(int(mid)) or True
        api._bugun = lambda: date(2023, 1, 1)  # sözleşme başlangıcı bugünden sonra: yalnız sözleşme kuralı çalışır
        with app.test_request_context(json=_govde(gecerlilik_ay="2023-07-02"), method="POST"):
            govde, kod = _yanit(api.plan_ekle_yanit(7))
        check("sozlesmeden once", kod == 400 and "sözleşme" in govde.get("mesaj", "") and tx.commit == 0)

        with app.test_request_context(json=_govde(yeni_brut=9999), method="POST"):
            govde, kod = _yanit(api.plan_ekle_yanit(7))
        check("brut tutarsiz", kod == 400 and govde.get("mesaj") == "Brüt tutar tutarsız." and tx.commit == 0)

        # Geçmiş ay reddi kalktı; tek alt sınır sözleşme başlangıç ayı (bugün: 2026-10-09, başlangıç: 2023-08-01)
        api._bugun = lambda: date(2026, 10, 9)
        check("gecmis ay reddi mesaji kalkti", not hasattr(api, "MSG_GECMIS_AY") and not hasattr(api, "MSG_IPTAL_GECMIS"))
        for ay_once in ("2023-07-31", "2023-07-01", "2020-01-15"):
            with app.test_request_context(json=_govde(gecerlilik_ay=ay_once), method="POST"):
                govde, kod = _yanit(api.plan_ekle_yanit(7))
            check(
                "sozlesme oncesi reddi " + ay_once,
                kod == 400
                and govde.get("mesaj") == "Geçerlilik ayı sözleşme başlangıcından önce olamaz."
                and tx.commit == 0
                and resync == []
                and not tx.bellek.satirlar,
            )

        _kur(tx, kilit=[{"ay": "2024-11", "fatura_tutari": 999, "kaynak": "fatura_tarihi"}])
        api._resync = lambda mid: resync.append(int(mid)) or True
        with app.test_request_context(json=_govde(), method="POST"):
            govde, kod = _yanit(api.plan_ekle_yanit(7))
        check("kilit onaysiz", kod == 400 and tx.commit == 0 and govde.get("kilitlenen_aylar"))

        with app.test_request_context(json=_govde(onay_kilitli_aylar=True), method="POST"):
            govde, kod = _yanit(api.plan_ekle_yanit(7))
        check(
            "ekle ve resync",
            kod == 200
            and govde.get("ok") is True
            and govde["plan"]["gecerlilik_ay"][:10] == "2024-11-01"
            and govde["plan"]["yeni_brut"] == 2400
            and resync == [7]
            and tx.commit == 1
            and any(s.startswith("SET LOCAL search_path TO tenant_abc,") for s in tx.bellek.sql)
            and any("CREATE TABLE IF NOT EXISTS sozlesme_plan_degisiklik" in s for s in tx.bellek.sql)
            and "audit_log" not in "\n".join(tx.bellek.sql).lower(),
        )

        with app.test_request_context(json=_govde(onay_kilitli_aylar=True), method="POST"):
            govde, kod = _yanit(api.plan_ekle_yanit(7))
        check(
            "cift istek",
            kod == 409
            and "açık bir plan zaten var" in govde.get("mesaj", "")
            and len(tx.bellek.satirlar) == 1
            and tx.rollback == 1,
        )

        def patlat(sql, params=None):
            if str(sql).lstrip().startswith("INSERT"):
                raise RuntimeError("duplicate key uq_sozlesme_plan_degisiklik_acik")
            return tx.bellek.calistir(sql, params)

        eski_tx = api.plan_tx

        def tx_pat(fn):
            try:
                return fn(patlat, tx.bellek.oku, tx.bellek.oku)
            except Exception:
                tx.rollback += 1
                raise

        api.plan_tx = tx_pat
        once = len(tx.bellek.satirlar)
        with app.test_request_context(json=_govde(gecerlilik_ay="2025-03-01", yeni_net=2100, yeni_brut=2520, onay_kilitli_aylar=True), method="POST"):
            govde, kod = _yanit(api.plan_ekle_yanit(7))
        check("unique anlasilir", kod == 409 and "açık bir plan zaten var" in govde.get("mesaj", "") and len(tx.bellek.satirlar) == once)
        api.plan_tx = eski_tx

        resync.clear()
        api._resync = lambda mid: False
        api._kilitler = lambda mid: []
        with app.test_request_context(json=_govde(gecerlilik_ay="2025-04-01", yeni_net=2100, yeni_brut=2520), method="POST"):
            _g, kod = _yanit(api.plan_ekle_yanit(7))
        check("resync hata geri alinir", kod == 500 and tx.rollback >= 2 and all(s["gecerlilik_ay"] != date(2025, 4, 1) for s in tx.bellek.satirlar))

        api._resync = lambda mid: resync.append(int(mid)) or True
        api._bugun = lambda: date(2026, 10, 9)
        tx.bellek.satirlar.append(
            {
                "id": 9,
                "musteri_id": 7,
                "gecerlilik_ay": date(2026, 9, 1),
                "yeni_net": 2000,
                "kdv_oran": 20,
                "yeni_brut": 2400,
                "nakit_tutar": None,
                "banka_tutar": 2000,
                "olusturan": "test-kullanici",
                "created_at": None,
                "iptal_at": None,
                "iptal_eden": None,
            }
        )
        with app.test_request_context(json={}, method="POST"):
            govde, kod = _yanit(api.plan_iptal_yanit(7, 9))
        check(
            "gecmis plan iptal edilir ve resync",
            kod == 200 and resync == [7] and govde["plan"]["iptal_at"] and tx.bellek.satirlar[-1]["iptal_at"] is not None,
        )
        resync.clear()
        tx.bellek.satirlar.append(
            {
                "id": 11,
                "musteri_id": 7,
                "gecerlilik_ay": date(2026, 11, 1),
                "yeni_net": 2000,
                "kdv_oran": 20,
                "yeni_brut": 2400,
                "nakit_tutar": None,
                "banka_tutar": 2000,
                "olusturan": "test-kullanici",
                "created_at": None,
                "iptal_at": None,
                "iptal_eden": None,
            }
        )
        with app.test_request_context(json={}, method="POST"):
            govde, kod = _yanit(api.plan_iptal_yanit(7, 11))
        check("gelecek iptal resync", kod == 200 and resync == [7] and govde["plan"]["iptal_at"])

        # Bulunulan aydaki plan iptal edilebilir (aynı transaction'da resync); resync hatasında geri alınır
        def plan_satiri(pid, ay):
            return {
                "id": pid,
                "musteri_id": 7,
                "gecerlilik_ay": ay,
                "yeni_net": 2000,
                "kdv_oran": 20,
                "yeni_brut": 2400,
                "nakit_tutar": None,
                "banka_tutar": 2000,
                "olusturan": "test-kullanici",
                "created_at": None,
                "iptal_at": None,
                "iptal_eden": None,
            }

        resync.clear()
        tx.bellek.satirlar.append(plan_satiri(13, date(2026, 10, 1)))
        with app.test_request_context(json={}, method="POST"):
            govde, kod = _yanit(api.plan_iptal_yanit(7, 13))
        check("bulunulan ay iptal resync", kod == 200 and resync == [7] and govde["plan"]["iptal_at"])

        resync.clear()
        api._resync = lambda mid: False
        tx.bellek.satirlar.append(plan_satiri(14, date(2026, 10, 1)))
        rb0 = tx.rollback
        with app.test_request_context(json={}, method="POST"):
            govde, kod = _yanit(api.plan_iptal_yanit(7, 14))
        check(
            "bulunulan ay iptal resync hata geri alinir",
            kod == 500 and tx.rollback == rb0 + 1 and tx.bellek.satirlar[-1]["iptal_at"] is None,
        )
        api._resync = lambda mid: resync.append(int(mid)) or True

        # Geçmiş plan: resync hatasında geri alınır; başarıda iptal edilir
        resync.clear()
        api._resync = lambda mid: False
        tx.bellek.satirlar.append(plan_satiri(15, date(2026, 9, 1)))
        rb1 = tx.rollback
        with app.test_request_context(json={}, method="POST"):
            govde, kod = _yanit(api.plan_iptal_yanit(7, 15))
        check("gecmis iptal resync hata geri alinir", kod == 500 and tx.rollback == rb1 + 1 and tx.bellek.satirlar[-1]["iptal_at"] is None)
        api._resync = lambda mid: resync.append(int(mid)) or True
        with app.test_request_context(json={}, method="POST"):
            govde, kod = _yanit(api.plan_iptal_yanit(7, 15))
        check("onceki ay iptal edilir", kod == 200 and resync == [7] and tx.bellek.satirlar[-1]["iptal_at"] is not None)
        tx.bellek.satirlar.pop()

        with app.test_request_context(json={}, method="POST"):
            tx.bellek.satirlar.append(
                {
                    "id": 12,
                    "musteri_id": 8,
                    "gecerlilik_ay": date(2026, 12, 1),
                    "yeni_net": 1,
                    "kdv_oran": 20,
                    "yeni_brut": 1.2,
                    "nakit_tutar": None,
                    "banka_tutar": 1,
                    "olusturan": "test-kullanici",
                    "created_at": None,
                    "iptal_at": None,
                    "iptal_eden": None,
                }
            )
            resync.clear()
            _g, kod = _yanit(api.plan_iptal_yanit(7, 12))
        check("baska kart iptal 403", kod == 403 and resync == [])
    finally:
        os.environ.pop("PLAN_DEGISTIR_ENABLED", None)
        os.environ.pop("PLAN_DEGISTIR_MUSTERI_IDS", None)


def test_onizleme_ve_liste():
    app = _app()
    tx = Tx()
    _kur(tx)
    cagri = {"tx": 0}

    def say_tx(fn):
        cagri["tx"] += 1
        return tx(fn)

    api.plan_tx = say_tx
    try:
        with app.test_request_context(json=_govde(), method="POST"):
            govde, kod = _yanit(api.plan_onizleme_yanit(7))
        ilk = govde.get("aylar") or []
        check(
            "onizleme 24 yazmaz",
            kod == 200
            and len(ilk) == 24
            and ilk[0]["ay"] == "2024-11"
            and ilk[0]["eski_brut"] == 1320
            and ilk[0]["yeni_brut"] == 2400
            and cagri["tx"] == 0
            and govde.get("kilitlenen_aylar") == [],
        )

        # geçmiş ay önizlemede serbest (bugün: 2024-10-09); sözleşme öncesi reddedilir
        with app.test_request_context(json=_govde(gecerlilik_ay="2024-09-01"), method="POST"):
            g_gecmis, kod_gecmis = _yanit(api.plan_onizleme_yanit(7))
        check(
            "onizleme gecmis ay serbest",
            kod_gecmis == 200 and g_gecmis["aylar"][0]["ay"] == "2024-09" and g_gecmis.get("gecmis_plan") is True and g_gecmis.get("odemeli_aylar") == [] and cagri["tx"] == 0,
        )
        with app.test_request_context(json=_govde(gecerlilik_ay="2023-07-01"), method="POST"):
            g_once, kod_once = _yanit(api.plan_onizleme_yanit(7))
        check("onizleme sozlesme oncesi reddi", kod_once == 400 and "sözleşme başlangıcından önce" in g_once.get("mesaj", "") and cagri["tx"] == 0)
        with app.test_request_context(json=_govde(gecerlilik_ay="2024-10-01"), method="POST"):
            g_bu, kod_bu = _yanit(api.plan_onizleme_yanit(7))
        check("onizleme bulunulan ay serbest", kod_bu == 200 and g_bu["aylar"][0]["ay"] == "2024-10")

        # görünür değişiklik uyarısı
        zincir_eski = api._onizleme_zinciri

        def zincir_faturali(faturali):
            def f(mid, kyc):
                z, mevcut, kutu = zincir_eski(mid, kyc)
                z["faturali_aylar"] = list(faturali)
                return z, mevcut, kutu
            return f

        def aylar_listesi(bas_y, bas_m, adet):
            out = []
            y, m = bas_y, bas_m
            for _ in range(adet):
                out.append(f"{y:04d}-{m:02d}")
                m += 1
                if m > 12:
                    y, m = y + 1, 1
            return out

        try:
            # 40 ay faturalı: 24 aylık pencerede hiç değişiklik yok
            api._onizleme_zinciri = zincir_faturali(aylar_listesi(2024, 11, 40))
            with app.test_request_context(json=_govde(), method="POST"):
                g_u, kod_u = _yanit(api.plan_onizleme_yanit(7))
            ayni = all(a["eski_brut"] == a["yeni_brut"] for a in g_u.get("aylar", []))
            uy = [u.get("mesaj", "") for u in g_u.get("uyarilar", []) if u.get("kod") == "gorunur_degisiklik_yok"]
            ilk = g_u.get("ilk_degisen_ay")
            check(
                "gorunur degisiklik yok uyarisi",
                kod_u == 200
                and ayni
                and g_u.get("gorunur_degisiklik") is False
                and ilk == "2028-03"
                and len(uy) == 1
                and uy[0]
                == "Bu plan seçilen geçerlilik ayından itibaren faturalı aylar nedeniyle şu an görünür bir değişiklik yaratmıyor; ilk değişen ay: 2028-03",
                (kod_u, ayni, g_u.get("gorunur_degisiklik"), ilk, uy),
            )
            # 3 ay faturalı: pencerede değişiklik var, uyarı yok
            api._onizleme_zinciri = zincir_faturali(aylar_listesi(2024, 11, 3))
            with app.test_request_context(json=_govde(), method="POST"):
                g_v, kod_v = _yanit(api.plan_onizleme_yanit(7))
            check(
                "gorunur degisiklik varsa uyari yok",
                kod_v == 200
                and g_v.get("gorunur_degisiklik") is True
                and g_v.get("ilk_degisen_ay") == "2025-02"
                and not [u for u in g_v.get("uyarilar", []) if u.get("kod") == "gorunur_degisiklik_yok"],
                (kod_v, g_v.get("gorunur_degisiklik"), g_v.get("ilk_degisen_ay")),
            )
        finally:
            api._onizleme_zinciri = zincir_eski
        import db as dbmod

        def fetch_all(sql, params=None):
            if "CREATE" in str(sql):
                raise RuntimeError("ensure")
            return [
                {
                    "id": 1,
                    "musteri_id": 7,
                    "gecerlilik_ay": date(2024, 11, 1),
                    "yeni_net": 2000,
                    "kdv_oran": 20,
                    "yeni_brut": 2400,
                    "nakit_tutar": None,
                    "banka_tutar": 2000,
                    "olusturan": "test-kullanici",
                    "created_at": None,
                    "iptal_at": None,
                    "iptal_eden": None,
                },
                {
                    "id": 2,
                    "musteri_id": 7,
                    "gecerlilik_ay": date(2025, 1, 1),
                    "yeni_net": 1,
                    "kdv_oran": 20,
                    "yeni_brut": 1.2,
                    "nakit_tutar": None,
                    "banka_tutar": 1,
                    "olusturan": "test-kullanici",
                    "created_at": None,
                    "iptal_at": date(2026, 1, 1),
                    "iptal_eden": "test-kullanici",
                },
            ]

        eski_all = dbmod.fetch_all
        dbmod.fetch_all = fetch_all
        dbmod.fetch_one = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("canli-yok"))
        try:
            with app.test_request_context("/giris/api/musteri/7/plan"):
                govde, kod = _yanit(api.plan_liste_yanit(7))
        finally:
            dbmod.fetch_all = eski_all
        check(
            "liste aktif iptal",
            kod == 200 and len(govde.get("aktif") or []) == 1 and len(govde.get("iptaller") or []) == 1 and len(govde.get("gecmis") or []) == 2,
        )
        check("liste sozlesme baslangic ayi", govde.get("sozlesme_baslangic") == "2023-08")
    finally:
        os.environ.pop("PLAN_DEGISTIR_ENABLED", None)
        os.environ.pop("PLAN_DEGISTIR_MUSTERI_IDS", None)


def _yeni_tx(tahsil=None, bugun=date(2026, 10, 9), kilit=None):
    tx = Tx()
    resync = []
    _kur(tx, kilit=kilit, bugun=bugun)
    api._resync = lambda mid: resync.append(int(mid)) or True
    api._tahsil_haritasi = lambda mid: dict(tahsil or {})
    return tx, resync


def test_gecmis_ay_ve_odeme():
    app = _app()
    try:
        # 1) Geçmiş aya (ödeme yok) plan eklenir; resync çalışır
        tx, resync = _yeni_tx()
        with app.test_request_context(json=_govde(gecerlilik_ay="2026-03-18"), method="POST"):
            g, kod = _yanit(api.plan_ekle_yanit(7))
        check(
            "gecmis ay plan eklenir",
            kod == 200 and g["plan"]["gecerlilik_ay"][:10] == "2026-03-01" and resync == [7] and tx.commit == 1,
            (kod, g),
        )
        plan_id = g["plan"]["id"]
        # geçmiş planı iptal et: aynı transaction'da resync
        resync.clear()
        with app.test_request_context(json={}, method="POST"):
            g2, kod2 = _yanit(api.plan_iptal_yanit(7, plan_id))
        check("gecmis plan iptal resync", kod2 == 200 and resync == [7] and g2["plan"]["iptal_at"])

        # 2) Sözleşme başlangıç ayı serbest, öncesi ret
        tx, resync = _yeni_tx()
        with app.test_request_context(json=_govde(gecerlilik_ay="2023-07-31"), method="POST"):
            g, kod = _yanit(api.plan_ekle_yanit(7))
        check("baslangictan once ret", kod == 400 and "sözleşme başlangıcından önce" in g.get("mesaj", "") and tx.commit == 0 and resync == [])
        with app.test_request_context(json=_govde(gecerlilik_ay="2023-08-10"), method="POST"):
            g, kod = _yanit(api.plan_ekle_yanit(7))
        check("baslangic ayi serbest", kod == 200 and g["plan"]["gecerlilik_ay"][:10] == "2023-08-01" and resync == [7])

        # 3) Ödemesi olan geçmiş aylar: önizleme bilgisi + sunucuda zorunlu onay
        odemeler = {"2026-01": 700.0, "2026-03": 500.0, "2026-04": 99999.0}
        tx, resync = _yeni_tx(tahsil=odemeler)
        with app.test_request_context(json=_govde(gecerlilik_ay="2026-03-01"), method="POST"):
            on, kod = _yanit(api.plan_onizleme_yanit(7))
        om = {o["ay"]: o for o in on.get("odemeli_aylar", [])}
        check(
            "onizleme odemeli aylar",
            kod == 200
            and on.get("gecmis_plan") is True
            and "2026-03" in om
            and "2026-04" in om
            and "2026-01" not in om
            and om["2026-03"]["odenen"] == 500.0
            and om["2026-03"]["yeni_brut"] == 2400
            and om["2026-03"]["yeni_kalan"] == 1900.0
            and om["2026-03"]["fazla_odeme"] == 0
            and om["2026-04"]["fazla_odeme"] == round(99999.0 - 2400, 2)
            and om["2026-04"]["yeni_kalan"] == 0
            and on.get("fazla_odeme") is True,
            on.get("odemeli_aylar"),
        )
        with app.test_request_context(json=_govde(gecerlilik_ay="2026-03-01"), method="POST"):
            g, kod = _yanit(api.plan_ekle_yanit(7))
        check(
            "odemeli onaysiz 400",
            kod == 400
            and g.get("mesaj") == api.MSG_ODEMELI_ONAY
            and g.get("odemeli_aylar")
            and tx.commit == 0
            and resync == []
            and not tx.bellek.satirlar,
        )
        with app.test_request_context(json=_govde(gecerlilik_ay="2026-03-01", onay_odemeli_aylar="true"), method="POST"):
            g, kod = _yanit(api.plan_ekle_yanit(7))
        check("odemeli onay yalniz true kabul", kod == 400 and tx.commit == 0)
        with app.test_request_context(json=_govde(gecerlilik_ay="2026-03-01", onay_odemeli_aylar=True), method="POST"):
            g, kod = _yanit(api.plan_ekle_yanit(7))
        check("odemeli onayli kayit", kod == 200 and resync == [7] and tx.commit == 1 and len(tx.bellek.satirlar) == 1)

        # 4) Kilitli ay onayı ayrı kalır: ikisi de gerekir
        kilit = [{"ay": "2026-05", "fatura_tutari": 1200, "kaynak": "fatura_tarihi"}]
        tx, resync = _yeni_tx(tahsil=odemeler, kilit=kilit)
        with app.test_request_context(json=_govde(gecerlilik_ay="2026-03-01", onay_odemeli_aylar=True), method="POST"):
            g, kod = _yanit(api.plan_ekle_yanit(7))
        check("kilit onayi hala sart", kod == 400 and g.get("kilitlenen_aylar") and tx.commit == 0)
        with app.test_request_context(json=_govde(gecerlilik_ay="2026-03-01", onay_kilitli_aylar=True), method="POST"):
            g, kod = _yanit(api.plan_ekle_yanit(7))
        check("odemeli onayi kilit onayiyla yerine gecmez", kod == 400 and g.get("mesaj") == api.MSG_ODEMELI_ONAY and tx.commit == 0)
        with app.test_request_context(json=_govde(gecerlilik_ay="2026-03-01", onay_kilitli_aylar=True, onay_odemeli_aylar=True), method="POST"):
            g, kod = _yanit(api.plan_ekle_yanit(7))
        check("iki onayla kayit", kod == 200 and tx.commit == 1)

        # 5) Bulunulan ay ve gelecek: ödeme olsa bile ek onay istenmez; önizlemede odemeli bilgi yok
        for ay in ("2026-10-01", "2026-12-01"):
            tx, resync = _yeni_tx(tahsil={"2026-10": 99999.0, "2026-12": 99999.0})
            with app.test_request_context(json=_govde(gecerlilik_ay=ay), method="POST"):
                on, kod_on = _yanit(api.plan_onizleme_yanit(7))
            check("onizleme odemeli yok " + ay, kod_on == 200 and on.get("gecmis_plan") is False and on.get("odemeli_aylar") == [] and on.get("fazla_odeme") is False)
            with app.test_request_context(json=_govde(gecerlilik_ay=ay), method="POST"):
                g, kod = _yanit(api.plan_ekle_yanit(7))
            check("onay istemeden kayit " + ay, kod == 200 and resync == [7] and tx.commit == 1)

        # 6) Ödeme etkisi okunamazsa kayıt/önizleme yapılmaz (güvenli taraf)
        tx, resync = _yeni_tx()

        def patla(mid):
            raise RuntimeError("tahsil-yok")

        api._tahsil_haritasi = patla
        with app.test_request_context(json=_govde(gecerlilik_ay="2026-03-01", onay_odemeli_aylar=True), method="POST"):
            g, kod = _yanit(api.plan_ekle_yanit(7))
        check("odeme okunamazsa kayit yok", kod == 500 and tx.commit == 0 and resync == [] and not tx.bellek.satirlar)
    finally:
        os.environ.pop("PLAN_DEGISTIR_ENABLED", None)
        os.environ.pop("PLAN_DEGISTIR_MUSTERI_IDS", None)


def test_js():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    i = html.find('id="musteri_durum"')
    parca = html[i:html.find("</select>", i)]
    check("kapali secenek yok", 'value="plan"' not in parca and "Plan değiştir" not in parca)
    check("onchange karar", "musteriDurumSecildi(this.value)" in parca and "/durum" not in (ROOT / "static" / "js" / "plan_degistir.js").read_text(encoding="utf-8"))
    js_metin = (ROOT / "static" / "js" / "plan_degistir.js").read_text(encoding="utf-8")
    kutu_blok = js_metin[js_metin.find('kutu.id = "plan_gecmis_bolum"'):][:600]
    check(
        "gecmis kutusu form-grid'de tam genislikli ayri satir",
        'kutu.style.gridColumn = "1 / -1"' in kutu_blok and "insertBefore(kutu, sel.nextSibling)" in js_metin,
    )
    check("script surumu artirildi", "js/plan_degistir.js', v=5" in html)
    check(
        "ay secici min = sozlesme baslangic ayi",
        "gec.min = sozBas" in js_metin and "gec.value < ayIso" not in js_metin and "sozlesme_baslangic" in js_metin,
    )
    check("gecmis iptal notu kalkti", "Geçmiş aya ait plan iptal edilemez" not in js_metin)
    check(
        "kaydet ipucu ve odemeli onay",
        "plan_kaydet_ipucu" in js_metin and "plan_odemeli_onay" in js_metin and "onay_odemeli_aylar" in js_metin,
    )
    taze = html.find("dnormFresh")
    taze_blok = html[taze:taze + 900] if taze >= 0 else ""
    check(
        "ikinci kart yukleme",
        "planKapisiniYenile(m.id)" in taze_blok and html.count("planKapisiniYenile(m.id)") >= 2,
    )
    aktif2 = html.find("syncDurumUI('aktif', {});")
    once2 = html[max(0, aktif2 - 800):aktif2] if aktif2 >= 0 else ""
    sonra2 = html[aktif2:aktif2 + 280] if aktif2 >= 0 else ""
    check(
        "ikinci syncDurumUI aktif dali",
        "dnormFresh" in once2 and "planKapisiniYenile(m.id)" in sonra2,
    )
    idx_aktif = 0
    aktif_yol = 0
    while True:
        bulunan = html.find("syncDurumUI('aktif'", idx_aktif)
        if bulunan < 0:
            break
        aktif_yol += 1
        pencere = html[bulunan:bulunan + 450]
        check(f"aktif yolu {aktif_yol}", "planKapisiniYenile(" in pencere)
        idx_aktif = bulunan + 1
    check("uc kart yolu", aktif_yol == 3)
    bos = html.find("syncDurumUI('aktif');\n    var csEl")
    if bos < 0:
        bos = html.find("planKapisiniYenile(null)")
    bos_blok = html[max(0, bos - 80):bos + 160] if bos >= 0 else ""
    check("bos kart temizler", "planKapisiniYenile(null)" in bos_blok)
    kod = r"""
const m = require('./static/js/plan_degistir.js');
const plan = m.planDurumKarari('plan', 'aktif', true);
const pasif = m.planDurumKarari('pasif', 'aktif', true);
const kapali = m.planDurumKarari('plan', 'aktif', false);
if (!(plan.modal && plan.kaydetDurum === false && plan.durum === 'aktif')) process.exit(2);
if (!(pasif.modal === false && pasif.kaydetDurum === true && pasif.durum === 'pasif')) process.exit(3);
if (!(kapali.goster === false && kapali.modal === false && m.planSecenekEklensin(false) === false)) process.exit(4);
if (m.planIptalEdilebilir('2026-11-01', '2026-10-09') !== true) process.exit(5);
if (m.planIptalEdilebilir('2026-09-01', '2026-10-09') !== true) process.exit(6);
if (m.planIptalEdilebilir('2026-10-01', '2026-10-09') !== true) process.exit(15);
if (m.planIptalEdilebilir('2026-10-31', '2026-10-01') !== true) process.exit(16);
if (m.planIptalEdilebilir('2025-12-01', '2026-01-01') !== true) process.exit(17);
if (m.planIptalEdilebilir('', '2026-01-01') !== false) process.exit(18);
process.exit(0);
"""
    r = subprocess.run(["node", "-e", kod], cwd=str(ROOT), capture_output=True, text=True)
    check("js karar", r.returncode == 0)


def main():
    test_kapi_ve_yetki()
    test_yazma()
    test_onizleme_ve_liste()
    test_gecmis_ay_ve_odeme()
    test_js()
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("plan_degistir ok")


if __name__ == "__main__":
    main()
