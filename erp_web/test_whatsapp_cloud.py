# -*- coding: utf-8 -*-
"""WhatsApp Cloud API taşıma — mock HTTP; gerçek ağ/token yok."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sys
import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith("WHATSAPP_CLOUD_"):
        os.environ.pop(k, None)

import whatsapp_cloud as wc  # noqa: E402
import sozlesme_whatsapp as sw  # noqa: E402

FAILS: list[str] = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:180]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _clean():
    for k in list(os.environ):
        if k.startswith("WHATSAPP_CLOUD_"):
            os.environ.pop(k, None)
    wc._set_hata("yok")


def test_bayrak_ve_mid():
    _clean()
    check("kapali_default", wc.cloud_aktif(867) is False)
    os.environ["WHATSAPP_CLOUD_ENABLED"] = "1"
    check("enabled_liste_bos", wc.cloud_aktif(867) is False)
    os.environ["WHATSAPP_CLOUD_MIDLER"] = "867"
    check("mid_867_acik", wc.cloud_aktif(867) is True)
    check("mid_1_kapali", wc.cloud_aktif(1) is False)
    os.environ["WHATSAPP_CLOUD_MIDLER"] = " 42 , abc , , 0 , -3 , 867 "
    check("parse_867", wc.cloud_aktif(867) is True and 42 in wc.cloud_midler())
    check("joker_yok", "*" not in str(wc.cloud_midler()) and wc.cloud_aktif("all") is False)


def test_numara_e164():
    _clean()
    check("tr_05", wc.numara_e164("05551234567") == "905551234567")
    check("tr_5", wc.numara_e164("5551234567") == "905551234567")
    check("bos", wc.numara_e164("12") == "")


def test_metin_pencere_reddi():
    _clean()
    os.environ["WHATSAPP_CLOUD_ENABLED"] = "1"
    os.environ["WHATSAPP_CLOUD_MIDLER"] = "867"
    os.environ["WHATSAPP_CLOUD_ACCESS_TOKEN"] = "x"
    os.environ["WHATSAPP_CLOUD_PHONE_NUMBER_ID"] = "1"
    r = wc.metin_gonder(867, "05551234567", "merhaba", pencere_acik=None)
    check("pencere_none_red", r.get("neden") == "pencere_disi" and r.get("ok") is False)
    r2 = wc.metin_gonder(867, "05551234567", "merhaba", pencere_acik=False)
    check("pencere_false_red", r2.get("neden") == "pencere_disi")


def test_hata_sinif():
    _clean()
    check("yetki_401", wc.siniflandir_http_hata(401, {"error": {"code": 190}}) == "yetki")
    check(
        "pencere",
        wc.siniflandir_http_hata(400, {"error": {"code": 131047, "message": "outside 24 hour window"}})
        == "pencere_disi",
    )
    check(
        "sablon",
        wc.siniflandir_http_hata(400, {"error": {"code": 132001, "message": "template name"}})
        == "sablon_onaysiz",
    )
    check("gecici_503", wc.siniflandir_http_hata(503, {}) == "gecici")
    check("kalici_400", wc.siniflandir_http_hata(400, {"error": {"code": 100}}) == "kalici")


def test_verify_challenge():
    _clean()
    os.environ["WHATSAPP_CLOUD_VERIFY_TOKEN"] = "secret-verify"
    body, kod = wc.verify_challenge("subscribe", "secret-verify", "12345")
    check("verify_ok", kod == 200 and body == "12345")
    body2, kod2 = wc.verify_challenge("subscribe", "wrong", "12345")
    check("verify_kotu", kod2 == 403 and body2 is None)
    body3, kod3 = wc.verify_challenge("x", "secret-verify", "1")
    check("verify_mode", kod3 == 403)


def test_imza():
    _clean()
    os.environ["WHATSAPP_CLOUD_APP_SECRET"] = "app-secret"
    raw = b'{"object":"whatsapp_business_account"}'
    dig = hmac.new(b"app-secret", raw, hashlib.sha256).hexdigest()
    check("imza_ok", wc.imza_dogrula(raw, f"sha256={dig}") is True)
    check("imza_kotu", wc.imza_dogrula(raw, "sha256=deadbeef") is False)
    check("imza_eksik", wc.imza_dogrula(raw, None) is False)
    check("imza_prefix_yok", wc.imza_dogrula(raw, dig) is False)


def test_log_sızıntı_yok():
    _clean()
    os.environ["WHATSAPP_CLOUD_ENABLED"] = "1"
    os.environ["WHATSAPP_CLOUD_MIDLER"] = "867"
    os.environ["WHATSAPP_CLOUD_ACCESS_TOKEN"] = "SUPER_SECRET_TOKEN_XYZ"
    os.environ["WHATSAPP_CLOUD_PHONE_NUMBER_ID"] = "999"
    buf = StringIO()
    handler = logging.StreamHandler(buf)
    logger = logging.getLogger("whatsapp_cloud")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        wc.metin_gonder(867, "05551234567", "gizli metin govde", pencere_acik=False)
        out = buf.getvalue()
    finally:
        logger.removeHandler(handler)
    check("log_token_yok", "SUPER_SECRET_TOKEN_XYZ" not in out)
    check("log_numara_yok", "05551234567" not in out and "905551234567" not in out)
    check("log_govde_yok", "gizli metin govde" not in out)
    check("log_mid_var", "mid=867" in out)


def test_sablon_mock_http():
    _clean()
    os.environ["WHATSAPP_CLOUD_ACCESS_TOKEN"] = "tok"
    os.environ["WHATSAPP_CLOUD_PHONE_NUMBER_ID"] = "111"
    class _R:
        status_code = 200
        content = b'{"messages":[{"id":"wamid.TEST"}]}'

        def json(self):
            return {"messages": [{"id": "wamid.TEST"}]}

    with mock.patch("requests.post", return_value=_R()) as post:
        r = wc.sablon_gonder(867, "05551234567", "hello_world", "tr", ["A"])
        check("sablon_ok", r.get("ok") is True and r.get("message_id") == "wamid.TEST")
        check("sablon_post", post.called)
        args, kwargs = post.call_args
        check("auth_bearer", "Bearer" in (kwargs.get("headers") or {}).get("Authorization", ""))
        # token değeri testte env'de var ama rapora yazılmaz; logda olmamalı
        body = kwargs.get("json") or {}
        check("payload_to_digits", body.get("to") == "905551234567")


DENEME = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _base_patches():
    sw.ensure_tablo = lambda: None
    sw._musteri = lambda mid: {"id": int(mid)}
    sw._sayilar = lambda *_a, **_k: (0, 0, 0, 0)
    sw._son_gonderim = lambda *_a, **_k: None
    sw._bul = lambda *_a, **_k: None
    sw._durum_yaz = lambda *_a, **_k: None
    sw.node_yolu_acik = lambda: True
    sw._ekle = lambda *_a, **_k: {"id": 99}


def test_regresyon_bayrak_kapali_node():
    """Bayrak kapalı → eski Node kuyruk yolu (_kuyruk_varsayilan)."""
    _clean()
    _base_patches()
    called = {"kuyruk": 0, "cloud": 0}

    def fake_kuyruk(*_a, **_k):
        called["kuyruk"] += 1
        return {"durum": "gonderildi", "neden": ""}

    with mock.patch.object(sw, "_kuyruk_varsayilan", side_effect=fake_kuyruk):
        with mock.patch(
            "whatsapp_cloud.metin_gonder",
            side_effect=lambda *_a, **_k: called.__setitem__("cloud", called["cloud"] + 1),
        ):
            govde, kod = sw.sozlesme_wa_isle(
                3,
                {
                    "musteri_id": 7,
                    "telefon": "05550000000",
                    "mesaj": "deneme",
                    "buton": "ust",
                    "onay": True,
                    "deneme": DENEME,
                },
                "https://payafin.com",
                "payafin.com",
                simdi=datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc),
                bagli_fn=lambda: True,
                numara_fn=lambda _t: ("kayitli", ""),
                kuyruk_fn=None,
            )
    check("kapali_200", kod == 200 and govde.get("ok") is True)
    time.sleep(0.2)
    check("kapali_kuyruk_cagrildi", called["kuyruk"] == 1)
    check("kapali_cloud_yok", called["cloud"] == 0)


def test_enabled_mid_disi_node():
    """ENABLED=1 ama mid MIDLER'de değil → Node yolu (kuyruk_fn=None, varsayılan kuyruk)."""
    _clean()
    os.environ["WHATSAPP_CLOUD_ENABLED"] = "1"
    os.environ["WHATSAPP_CLOUD_MIDLER"] = "867"
    _base_patches()
    called = {"kuyruk": 0, "cloud": 0}

    def fake_kuyruk(*_a, **_k):
        called["kuyruk"] += 1
        return {"durum": "gonderildi", "neden": ""}

    with mock.patch.object(sw, "_kuyruk_varsayilan", side_effect=fake_kuyruk):
        with mock.patch(
            "whatsapp_cloud.metin_gonder",
            side_effect=lambda *_a, **_k: called.__setitem__("cloud", called["cloud"] + 1),
        ):
            govde, kod = sw.sozlesme_wa_isle(
                3,
                {
                    "musteri_id": 7,
                    "telefon": "05550000000",
                    "mesaj": "deneme",
                    "buton": "ust",
                    "onay": True,
                    "deneme": DENEME,
                },
                "https://payafin.com",
                "payafin.com",
                simdi=datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc),
                bagli_fn=lambda: True,
                numara_fn=lambda _t: ("kayitli", ""),
                kuyruk_fn=None,
            )
    check("mid_disi_ok", kod == 200 and govde.get("ok") is True)
    time.sleep(0.2)
    check("mid_disi_node", called["kuyruk"] == 1)
    check("mid_disi_cloud_yok", called["cloud"] == 0)


def test_mid_867_cloud_yolu():
    _clean()
    os.environ["WHATSAPP_CLOUD_ENABLED"] = "1"
    os.environ["WHATSAPP_CLOUD_MIDLER"] = "867"
    os.environ["WHATSAPP_CLOUD_ACCESS_TOKEN"] = "tok"
    os.environ["WHATSAPP_CLOUD_PHONE_NUMBER_ID"] = "1"
    _base_patches()
    called = {"kuyruk": 0, "cloud": 0}

    def kuyruk(*_a, **_k):
        called["kuyruk"] += 1
        return {"durum": "gonderildi", "neden": ""}

    def fake_metin(mid, numara, metin, *, pencere_acik=None):
        called["cloud"] += 1
        check("cloud_mid", int(mid) == 867)
        check("cloud_pencere_false", pencere_acik is False)
        return {"ok": False, "durum": "hata", "neden": "pencere_disi", "message_id": ""}

    with mock.patch("whatsapp_cloud.metin_gonder", side_effect=fake_metin):
        govde, kod = sw.sozlesme_wa_isle(
            3,
            {
                "musteri_id": 867,
                "telefon": "05550000000",
                "mesaj": "deneme",
                "buton": "ust",
                "onay": True,
                "deneme": DENEME,
            },
            "https://payafin.com",
            "payafin.com",
            simdi=datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc),
            bagli_fn=lambda: (_ for _ in ()).throw(AssertionError("node bagli cagrilmamali")),
            numara_fn=lambda _t: (_ for _ in ()).throw(AssertionError("node numara cagrilmamali")),
            kuyruk_fn=None,
        )
    check("cloud_867_start", kod == 200 and govde.get("durum") == "gonderiliyor")
    time.sleep(0.2)
    check("cloud_cagrildi", called["cloud"] == 1)
    check("cloud_node_kuyruk_yok", called["kuyruk"] == 0)


def test_webhook_flask():
    _clean()
    os.environ["WHATSAPP_CLOUD_VERIFY_TOKEN"] = "vt"
    os.environ["WHATSAPP_CLOUD_APP_SECRET"] = "as"
    from flask import Flask
    import routes.whatsapp_routes as wr

    app = Flask(__name__)
    app.register_blueprint(wr.bp)
    c = app.test_client()
    r = c.get(
        "/whatsapp/cloud/webhook",
        query_string={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "ch9"},
    )
    check("wh_get", r.status_code == 200 and r.data == b"ch9")
    r_bad = c.get(
        "/whatsapp/cloud/webhook",
        query_string={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "ch"},
    )
    check("wh_get_403", r_bad.status_code == 403)

    raw = b'{"object":"whatsapp_business_account","entry":[]}'
    dig = hmac.new(b"as", raw, hashlib.sha256).hexdigest()
    r2 = c.post(
        "/whatsapp/cloud/webhook",
        data=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={dig}"},
    )
    check("wh_post_ok", r2.status_code == 200)
    r3 = c.post(
        "/whatsapp/cloud/webhook",
        data=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=00"},
    )
    check("wh_post_403", r3.status_code == 403)
    r4 = c.post("/whatsapp/cloud/webhook", data=raw, headers={"Content-Type": "application/json"})
    check("wh_post_eksik_imza", r4.status_code == 403)


def test_saglik_kart():
    _clean()
    import wa_saglik as ws

    k = ws.kontrolleri_uret("ok", "hazir", {})
    cloud = next((x for x in k if x.get("anahtar") == "cloud_api"), None)
    check("saglik_kart_var", cloud is not None)
    check("saglik_kapali_metin", cloud and "kapalı" in cloud.get("aciklama", ""))
    os.environ["WHATSAPP_CLOUD_ENABLED"] = "1"
    k2 = ws.kontrolleri_uret("ok", "hazir", {})
    cloud2 = next((x for x in k2 if x.get("anahtar") == "cloud_api"), None)
    check("saglik_acik_token_bos", cloud2 and "açık" in cloud2.get("aciklama", ""))


if __name__ == "__main__":
    test_bayrak_ve_mid()
    test_numara_e164()
    test_metin_pencere_reddi()
    test_hata_sinif()
    test_verify_challenge()
    test_imza()
    test_log_sızıntı_yok()
    test_sablon_mock_http()
    test_regresyon_bayrak_kapali_node()
    test_enabled_mid_disi_node()
    test_mid_867_cloud_yolu()
    test_webhook_flask()
    test_saglik_kart()
    print("SONUC", "FAIL" if FAILS else "PASS", len(FAILS))
    sys.exit(1 if FAILS else 0)
