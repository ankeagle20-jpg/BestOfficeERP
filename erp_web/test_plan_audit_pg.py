# -*- coding: utf-8 -*-
"""Yerel PostgreSQL: audit_log mevcut kolonlara yazılır, şema karışmaz.

Canlı veritabanına bağlanmaz. Yalnız 127.0.0.1:54329 üzerindeki boş test
kümesine gider. Küme yoksa test durur.
"""
import json
import logging
import os
from datetime import date

import psycopg2
from flask import Flask
from flask_login import LoginManager, login_user
from psycopg2.extras import RealDictCursor

import sozlesme_plan_api as api
import sozlesme_plan_dondurma_api as da
from audit_log import SQL_TABLO, _oturum_id, anlik_al
from auth import User
from test_plan_degistir import _govde, _yanit
from test_plan_dondurma import _zincir

DSN = os.environ.get("BOE_AUDIT_DSN", "host=127.0.0.1 port=54329 dbname=postgres user=postgres")
FAILS = []
YASAK = ("telefon", "email", "tc", "aciklama", "not")


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _ddl_db_ile_ayni():
    yol = os.path.join(os.path.dirname(__file__), "db.py")
    metin = open(yol, encoding="utf-8").read()
    i = metin.find("CREATE TABLE IF NOT EXISTS audit_log (")
    parca = metin[i:metin.find(")", i) + 1]
    def norm(s):
        return " ".join(s.replace("\n", " ").split())
    check("ddl db.py ile ayni", norm(SQL_TABLO).endswith(norm(parca)) or norm(parca) in norm(SQL_TABLO), parca)


def _kurulum(conn, sema):
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS %s" % sema if sema != "public" else "SELECT 1")
    cur.execute("SET search_path TO %s, pg_catalog" % sema)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY
        )
        """
    )
    cur.execute("INSERT INTO customers (id) VALUES (867) ON CONFLICT (id) DO NOTHING")
    cur.execute(SQL_TABLO)
    conn.commit()


def _satirlar(conn, sema):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT islem, tablo_adi, kayit_id, eski_deger, yeni_deger, user_id FROM %s.audit_log ORDER BY id" % sema
    )
    return [dict(r) for r in cur.fetchall()]


def main():
    if os.environ.get("ALLOW_PROD_WRITE") == "1":
        print("FAIL ALLOW_PROD_WRITE acik")
        raise SystemExit(1)
    _ddl_db_ile_ayni()
    check("metin kullanici id olmaz", _oturum_id("test-kullanici") is None)
    check("sayi kullanici id olur", _oturum_id("15") == 15)
    yasakli = anlik_al(
        {"id": 1, "musteri_id": 867, "telefon": "0500", "email": "a@b.c", "aciklama": "serbest", "yeni_net": 10},
        ("id", "musteri_id", "telefon", "email", "aciklama", "yeni_net"),
    )
    check("anlik yasak dusur", "telefon" not in yasakli and "aciklama" not in yasakli and yasakli.get("yeni_net") == 10, yasakli)
    try:
        conn = psycopg2.connect(DSN, connect_timeout=3)
    except Exception as exc:
        print("FAIL yerel postgres yok:", type(exc).__name__)
        raise SystemExit(1)
    conn.autocommit = False
    boz = {"v": False}
    try:
        kur = conn.cursor()
        for sema in ("public", "tenant_audit_e", "yanlis"):
            if sema != "public":
                kur.execute("DROP SCHEMA IF EXISTS %s CASCADE" % sema)
                kur.execute("CREATE SCHEMA %s" % sema)
            else:
                kur.execute("DROP TABLE IF EXISTS public.audit_log CASCADE")
                kur.execute("DROP TABLE IF EXISTS public.sozlesme_plan_dondurma CASCADE")
                kur.execute("DROP TABLE IF EXISTS public.sozlesme_plan_degisiklik CASCADE")
                kur.execute("DROP TABLE IF EXISTS public.customers CASCADE")
            conn.commit()
            _kurulum(conn, sema)
        kur.execute("SET search_path TO yanlis, pg_catalog")
        conn.commit()

        def plan_tx(fn):
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                sema = api._sema()
                if sema:
                    cur.execute("SET LOCAL search_path TO %s, pg_catalog" % sema)
                else:
                    cur.execute("SET LOCAL search_path TO public, pg_catalog")

                def calistir(sql, params=None):
                    if boz["v"] and "audit_log" in sql and sql.lstrip().upper().startswith("INSERT"):
                        raise RuntimeError("audit kirildi")
                    cur.execute(sql, params or ())
                    if cur.description:
                        row = cur.fetchone()
                        return dict(row) if row else None
                    return None

                def oku_bir(sql, params=None):
                    cur.execute(sql, params or ())
                    row = cur.fetchone()
                    return dict(row) if row else None

                def oku_cok(sql, params=None):
                    cur.execute(sql, params or ())
                    return [dict(r) for r in cur.fetchall()]

                out = fn(calistir, oku_bir, oku_cok)
                conn.commit()
                return out
            except Exception:
                conn.rollback()
                raise

        api.plan_tx = plan_tx
        da.plan_tx = plan_tx
        api._resync = lambda mid: True
        api._kim = lambda: "deneme"
        da._kim = lambda: "deneme"
        api._bugun = lambda: date(2024, 10, 9)
        api._kilitler = lambda mid: []
        api._kilit_k3_aylari = lambda mid: []
        api._tahsil_haritasi = lambda mid: {}
        api._musteri_ve_kyc = lambda mid: (
            {"id": int(mid), "durum": "aktif"},
            {"sozlesme_tarihi": date(2023, 8, 1), "aylik_kira": 1000, "kdv_oran": 20},
        )
        da._musteri_ve_kyc = api._musteri_ve_kyc
        da._odenen_haritasi = lambda *a, **k: {}
        da._siniflar = lambda mid: {}
        api._onizleme_zinciri = lambda mid, kyc: (_zincir(), [], None)
        import db as dbmod

        def _yerel_fetch_one(sql, params=None):
            """Düzenleme önizlemesi canlı havuza gitmesin; aynı test kümesini okusun."""
            sema = api._sema() or "public"
            if sema not in ("public", "tenant_audit_e", "yanlis"):
                raise RuntimeError("sema")
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SET LOCAL search_path TO %s, pg_catalog" % sema)
            cur.execute(sql, params or ())
            row = cur.fetchone()
            out = dict(row) if row else None
            conn.rollback()
            return out

        dbmod.fetch_one = _yerel_fetch_one
        os.environ["PLAN_DEGISTIR_ENABLED"] = "1"
        os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "867"
        app = Flask(__name__)
        app.secret_key = "audit-e"
        LoginManager().init_app(app)

        def sema_ayarla(ad):
            api._sema = lambda: ad
            da._sema = lambda: ad

        def girisli(yol, govde, fn):
            with app.test_request_context(yol, method="POST", json=govde):
                login_user(User(15, "deneme", "Deneme", "admin"))
                return _yanit(fn())

        def plan_olustur(ay):
            govde, kod = girisli("/x", _govde(gecerlilik_ay=ay), lambda: api.plan_ekle_yanit(867))
            check("plan yazildi " + ay, kod == 200 and govde.get("ok"), (kod, govde))
            return govde["plan"]["id"]

        def dondurma_olustur(ay):
            govde, kod = girisli(
                "/x",
                {"baslangic_ay": ay, "ay_sayisi": 1, "musteri_id": 867},
                lambda: da.dondurma_ekle_yanit(867),
            )
            check("dondurma yazildi " + ay, kod == 200 and govde.get("ok"), (kod, govde))
            return govde["dondurma"]["id"]

        def kisisel_yok(satir):
            blob = (satir["eski_deger"] or "") + (satir["yeni_deger"] or "")
            return not any(k in blob for k in YASAK)

        def bak(sema, islem, tablo):
            bulunan = [s for s in _satirlar(conn, sema) if s["islem"] == islem and s["tablo_adi"] == tablo]
            check(sema + " " + islem + " var", len(bulunan) == 1, bulunan)
            if not bulunan:
                return
            satir = bulunan[0]
            eski = json.loads(satir["eski_deger"])
            yeni = json.loads(satir["yeni_deger"])
            check(
                sema + " " + islem + " kolon",
                satir["user_id"] == 15 and satir["kayit_id"] and "musteri_id" in eski and kisisel_yok(satir),
                satir,
            )
            check(sema + " " + islem + " yeni json", isinstance(yeni, dict) and yeni, yeni)

        sema_ayarla(None)
        pid = plan_olustur("2024-11-18")
        _g, kod = girisli("/x", {}, lambda: api.plan_iptal_yanit(867, pid))
        check("plan kaldir 200", kod == 200, kod)
        bak("public", "plan_kaldir", "sozlesme_plan_degisiklik")

        pid = plan_olustur("2025-01-01")
        _g, kod = girisli(
            "/x",
            _govde(gecerlilik_ay="2025-02-01", yeni_net=2100, yeni_brut=2520, duzenlenen_plan_id=pid),
            lambda: api.plan_ekle_yanit(867),
        )
        check("plan duzenle 200", kod == 200, kod)
        bak("public", "plan_duzenle", "sozlesme_plan_degisiklik")

        pid = plan_olustur("2025-03-01")
        _g, kod = girisli("/x", {}, lambda: api.plan_sil_yanit(867, pid))
        check("plan sil 200", kod == 200, kod)
        bak("public", "plan_sil", "sozlesme_plan_degisiklik")

        did = dondurma_olustur("2024-08")
        _g, kod = girisli("/x", {"musteri_id": 867}, lambda: da.dondurma_iptal_yanit(867, did))
        check("dondurma kaldir 200", kod == 200, kod)
        bak("public", "dondurma_kaldir", "sozlesme_plan_dondurma")

        did = dondurma_olustur("2024-09")
        _g, kod = girisli(
            "/x",
            {"baslangic_ay": "2024-10", "ay_sayisi": 1, "musteri_id": 867, "duzenlenen_dondurma_id": did},
            lambda: da.dondurma_ekle_yanit(867),
        )
        check("dondurma duzenle 200", kod == 200, kod)
        bak("public", "dondurma_duzenle", "sozlesme_plan_dondurma")

        did = dondurma_olustur("2024-11")
        _g, kod = girisli("/x", {"musteri_id": 867}, lambda: da.dondurma_sil_yanit(867, did))
        check("dondurma sil 200", kod == 200, kod)
        bak("public", "dondurma_sil", "sozlesme_plan_dondurma")

        once_public = len(_satirlar(conn, "public"))
        once_tenant = len(_satirlar(conn, "tenant_audit_e"))
        once_yanlis = len(_satirlar(conn, "yanlis"))
        sema_ayarla("tenant_audit_e")
        pid = plan_olustur("2025-04-01")
        _g, kod = girisli("/x", {}, lambda: api.plan_sil_yanit(867, pid))
        check("tenant plan sil 200", kod == 200, kod)
        did = dondurma_olustur("2024-12")
        _g, kod = girisli("/x", {"musteri_id": 867}, lambda: da.dondurma_sil_yanit(867, did))
        check("tenant dondurma sil 200", kod == 200, kod)
        tenant = _satirlar(conn, "tenant_audit_e")
        check("tenant yalniz kendi audit", len(tenant) == once_tenant + 2, tenant)
        check("yanlis sema bos", len(_satirlar(conn, "yanlis")) == once_yanlis)
        check("public tenant yazisini almaz", len(_satirlar(conn, "public")) == once_public)

        kolon = conn.cursor()
        kolon.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'audit_log'
            ORDER BY ordinal_position
            """
        )
        tipler = {r[0]: r[1] for r in kolon.fetchall()}
        check(
            "kolon turleri",
            tipler.get("user_id") == "integer"
            and tipler.get("eski_deger") == "text"
            and tipler.get("yeni_deger") == "text"
            and "olay" not in tipler
            and "anlik" not in tipler,
            tipler,
        )

        sema_ayarla(None)
        kayitlar = []
        kayit = logging.getLogger("bestofficeerp.audit")
        kayit.addHandler(logging.Handler())
        kayit.handlers[-1].emit = lambda record: kayitlar.append(record.getMessage())
        pid = plan_olustur("2025-05-01")
        once = len(_satirlar(conn, "public"))
        boz["v"] = True
        _g, kod = girisli("/x", {}, lambda: api.plan_sil_yanit(867, pid))
        boz["v"] = False
        cur = conn.cursor()
        cur.execute("SET search_path TO public, pg_catalog")
        cur.execute("SELECT silindi_at IS NOT NULL FROM sozlesme_plan_degisiklik WHERE id = %s", (pid,))
        silindi = cur.fetchone()[0]
        conn.rollback()
        check("audit hatasi silmeyi durdurmaz", kod == 200 and silindi, kod)
        check("audit hata satiri yok", len(_satirlar(conn, "public")) == once)
        check("audit hata loglandi", any("audit yazilamadi" in m for m in kayitlar), kayitlar)
    finally:
        try:
            conn.rollback()
            c = conn.cursor()
            c.execute("DROP SCHEMA IF EXISTS tenant_audit_e CASCADE")
            c.execute("DROP SCHEMA IF EXISTS yanlis CASCADE")
            c.execute("DROP TABLE IF EXISTS public.audit_log CASCADE")
            c.execute("DROP TABLE IF EXISTS public.sozlesme_plan_dondurma CASCADE")
            c.execute("DROP TABLE IF EXISTS public.sozlesme_plan_degisiklik CASCADE")
            c.execute("DROP TABLE IF EXISTS public.customers CASCADE")
            conn.commit()
        except Exception:
            pass
        conn.close()
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("plan_audit_pg ok")


if __name__ == "__main__":
    main()
