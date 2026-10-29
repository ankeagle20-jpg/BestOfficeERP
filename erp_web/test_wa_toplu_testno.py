"""Kayıtlı test no + test_atlandi. Gerçek gönderim yok; numara rapora yazılmaz."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest import mock

import wa_kampanya as wa

ROOT = Path(__file__).resolve().parent

# Maskeli sabitler — çıktıya basılmaz
_TEL_A = "905550001111"
_TEL_B = "905550002222"
_TEL_A_HAM = "05550001111"
_TEL_GECERSIZ = "12ab"


def check(ad, ok, detay=""):
    if not ok:
        raise SystemExit(f"FAIL {ad} {detay}")
    print("ok", ad)


def test_norm_ve_hedef():
    check("norm a", wa.norm_telefon(_TEL_A_HAM) == _TEL_A)
    check("norm b", wa.norm_telefon("+90 555 000 22 22") == _TEL_B)
    check("norm gecersiz", wa.norm_telefon(_TEL_GECERSIZ) == "")

    eski = wa.test_telefon_oku
    wa.test_telefon_oku = lambda: _TEL_A
    try:
        check("kayitli oncelik", wa.test_hedef_telefon(_TEL_B) == _TEL_A)
        check("kayitli bos istek", wa.test_hedef_telefon("") == _TEL_A)
    finally:
        wa.test_telefon_oku = eski

    wa.test_telefon_oku = lambda: ""
    try:
        check("eski varsayilan", wa.test_hedef_telefon(_TEL_A_HAM) == _TEL_A)
        check("eski gecersiz", wa.test_hedef_telefon(_TEL_GECERSIZ) == "")
    finally:
        wa.test_telefon_oku = eski


def test_kaydet_oku_uzerine():
    store = {}

    def fake_execute(sql, params=None):
        s = " ".join(str(sql).split())
        if "wa_toplu_ayar" in s and params and len(params) >= 2:
            store[str(params[0])] = str(params[1])
        return None

    def fake_fetch_one(sql, params=None):
        if "wa_toplu_ayar" in str(sql) and params:
            v = store.get(str(params[0]))
            return {"deger": v} if v is not None else None
        return None

    with mock.patch.object(wa, "ensure_tablolar", lambda: None):
        with mock.patch("db.execute", fake_execute):
            with mock.patch("db.fetch_one", fake_fetch_one):
                r = wa.test_telefon_kaydet(_TEL_A_HAM)
                check("kaydet ok", r.get("ok") is True and r.get("kayitli") is True)
                check("maske", str(r.get("maske") or "").startswith("***") and _TEL_A[-4:] in str(r.get("maske")))
                check("oku", wa.test_telefon_oku() == _TEL_A)
                r2 = wa.test_telefon_kaydet(_TEL_B)
                check("uzerine", r2.get("ok") is True and wa.test_telefon_oku() == _TEL_B)
                r3 = wa.test_telefon_kaydet(_TEL_GECERSIZ)
                check("gecersiz kaydet", r3.get("ok") is False and r3.get("neden") == "gecersiz")
                check("gecersiz bozmaz", wa.test_telefon_oku() == _TEL_B)


def test_gonder_kayitli_hedefe(mock_kuyruk=None):
    giden = {"tel": None}

    def kuyruk(tel, metin, anahtar):
        giden["tel"] = tel
        return {"durum": "gonderildi"}

    eski_oku = wa.test_telefon_oku
    wa.test_telefon_oku = lambda: _TEL_A
    try:
        paket = wa.test_gonder(_TEL_B, "Merhaba {yetkili}", kuyruk)
        # test_gonder uses given telefon directly — hedef seçimi test_gonder_istegi'nde
        check("test_gonder dogrudan", paket.get("test_ok") is True and giden["tel"] == _TEL_B)
        hedef = wa.test_hedef_telefon(_TEL_B)
        check("istek hedefi kayitli", hedef == _TEL_A)
        giden["tel"] = None
        paket2 = wa.test_gonder(hedef, "Merhaba", kuyruk)
        check("kayitliya gider", paket2.get("test_ok") is True and giden["tel"] == _TEL_A)
        check("pakette tel yok", "telefon" not in paket2)
    finally:
        wa.test_telefon_oku = eski_oku


def test_atlandi_izin():
    check("atla ac", wa.baslat_izin(False, False, True) == "tamam")
    check("atla kapali", wa.baslat_izin(False, False, False) == "test_yok")
    check("bayrak parse", wa._test_atlandi_bayrak(1) and wa._test_atlandi_bayrak("1") and not wa._test_atlandi_bayrak(0))


def test_kaynak_ve_uc():
    src = (ROOT / "wa_kampanya.py").read_text(encoding="utf-8")
    check("ddl ayar", "CREATE TABLE IF NOT EXISTS wa_toplu_ayar" in src)
    check("ddl kolon", "ADD COLUMN IF NOT EXISTS test_atlandi" in src)
    check("JS5", re.search(r"(?m)^JS_SURUM\s*=\s*5\s*$", src) is not None)
    rt = (ROOT / "routes" / "whatsapp_routes.py").read_text(encoding="utf-8")
    check("uc test-no", "/api/toplu-test-no" in rt and "test_telefon_kaydet" in rt)
    check("uc baslat atla", "test_atlandi" in rt and "baslat_istegi" in rt)
    i = src.find("def baslat_istegi")
    parca = src[i:src.find("\ndef ", i + 1)]
    check("baslat yazar", "test_atlandi = %s" in parca and "test_ok = TRUE OR %s" in parca)
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("ui no", 'id="wtTestNoKaydet"' in html and 'id="wtTestAtla"' in html)
    js = (ROOT / "static" / "js" / "wa-toplu.js").read_text(encoding="utf-8")
    check("js atla confirm", "Test yapılmadı" in js and "test_atlandi" in js)
    check("js checkbox default", "testAtla.checked = false" in js)


def test_yetki_bayrak_uc():
    from flask import Flask
    from auth import User, login_manager
    from routes import whatsapp_routes

    rol = {"v": "personel"}
    os.environ["WA_TOPLU_KAMPANYA"] = "1"

    @login_manager.user_loader
    def _load(_uid):
        return User(
            id=1, username="t", full_name="T", role=rol["v"],
            aktif_mi=True, security_stamp="test-stamp",
        )

    app = Flask(__name__)
    app.secret_key = "test"
    login_manager.init_app(app)
    app.register_blueprint(whatsapp_routes.bp)
    client = app.test_client()
    with client.session_transaction() as s:
        s["_user_id"] = "1"
        s["_fresh"] = True
    try:
        r = client.get("/whatsapp/api/toplu-test-no")
        check("personel 403", r.status_code == 403)
        rol["v"] = "muhasebe"
        r_m = client.post("/whatsapp/api/toplu-test-no", json={"telefon": _TEL_A_HAM})
        check("muhasebe 403", r_m.status_code == 403)
        rol["v"] = "admin"
        os.environ["WA_TOPLU_KAMPANYA"] = "0"
        r2 = client.get("/whatsapp/api/toplu-test-no")
        check("bayrak 404", r2.status_code == 404)
        r3 = client.post("/whatsapp/api/toplu-baslat", json={"id": 1, "test_atlandi": 1})
        check("baslat bayrak 404", r3.status_code == 404)
    finally:
        os.environ["WA_TOPLU_KAMPANYA"] = "0"


def _calistir(adlar):
    for ad in adlar:
        fn = globals().get(ad)
        if not callable(fn):
            check(ad, False)
            continue
        try:
            fn()
        except SystemExit:
            raise
        except Exception as e:
            check(ad + " " + type(e).__name__, False)


if __name__ == "__main__":
    secili = [a for a in sys.argv[1:] if a.startswith("test_")]
    if not secili:
        secili = [a for a in globals() if a.startswith("test_")]
    _calistir(secili)
    print("PASS")
