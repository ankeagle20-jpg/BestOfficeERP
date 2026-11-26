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
    wc._hata_ayrinti_sifirla_test()
    wc.test_gonder_rate_sifirla_test()
    wc._webhook_sayac_sifirla_test()


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
    wh = next((x for x in k if x.get("anahtar") == "cloud_webhook"), None)
    check("saglik_webhook_kart", wh is not None and "sent=" in (wh.get("aciklama") or ""))
    os.environ["WHATSAPP_CLOUD_ENABLED"] = "1"
    k2 = ws.kontrolleri_uret("ok", "hazir", {})
    cloud2 = next((x for x in k2 if x.get("anahtar") == "cloud_api"), None)
    check("saglik_acik_token_bos", cloud2 and "açık" in cloud2.get("aciklama", ""))


def test_webhook_sayaclar():
    _clean()
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{"id": "m1", "from": "905551111111", "text": {"body": "x"}}],
                    "statuses": [
                        {"id": "w1", "status": "sent", "recipient_id": "905551111111"},
                        {"id": "w2", "status": "delivered"},
                        {"id": "w3", "status": "read"},
                        {"id": "w4", "status": "failed"},
                    ],
                }
            }]
        }]
    }
    r = wc.webhook_olaylari_isle(payload)
    check("wh_counts_ret", r.get("sent") == 1 and r.get("delivered") == 1
          and r.get("read") == 1 and r.get("failed") == 1 and r.get("messages") == 1)
    s = wc.webhook_sayaclar()
    check("wh_counts_mem", s.get("sent") == 1 and s.get("delivered") == 1
          and s.get("read") == 1 and s.get("failed") == 1 and s.get("messages") == 1)
    check("wh_son_ts", s.get("son_olay_ts") is not None)
    dump = str(s) + str(r)
    check("wh_sayac_pii_yok", "905551111111" not in dump and "body" not in dump)


def test_test_sablon_kapali_ve_basari():
    _clean()
    r0 = wc.test_sablon_gonder("05551234567", "hello_world", "en_US")
    check("test_cloud_kapali", r0.get("neden") == "cloud_kapali" and r0.get("ok") is False)
    check("test_kapali_mesaj", "Cloud kapalı" in str(r0.get("mesaj") or ""))

    os.environ["WHATSAPP_CLOUD_ENABLED"] = "1"
    os.environ["WHATSAPP_CLOUD_MIDLER"] = "867"
    os.environ["WHATSAPP_CLOUD_ACCESS_TOKEN"] = "tok-TEST-NO-LEAK"
    os.environ["WHATSAPP_CLOUD_PHONE_NUMBER_ID"] = "111"

    class _R:
        status_code = 200
        content = b'{"messages":[{"id":"wamid.HELLO"}]}'

        def json(self):
            return {"messages": [{"id": "wamid.HELLO"}]}

    buf = StringIO()
    handler = logging.StreamHandler(buf)
    logger = logging.getLogger("whatsapp_cloud")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with mock.patch("requests.post", return_value=_R()):
            r = wc.test_sablon_gonder("05551234567", "hello_world", "en_US")
        out = buf.getvalue()
    finally:
        logger.removeHandler(handler)

    check("test_ok", r.get("ok") is True and r.get("message_id") == "wamid.HELLO")
    check("test_log_numara_yok", "05551234567" not in out and "905551234567" not in out)
    check("test_log_token_yok", "tok-TEST-NO-LEAK" not in out)
    check("test_yanit_numara_yok", "0555" not in str(r) and "90555" not in str(r))


def test_test_sablon_rate_limit():
    _clean()
    os.environ["WHATSAPP_CLOUD_ENABLED"] = "1"
    os.environ["WHATSAPP_CLOUD_MIDLER"] = "867"
    os.environ["WHATSAPP_CLOUD_ACCESS_TOKEN"] = "tok"
    os.environ["WHATSAPP_CLOUD_PHONE_NUMBER_ID"] = "1"

    class _R:
        status_code = 200
        content = b'{"messages":[{"id":"wamid.R"}]}'

        def json(self):
            return {"messages": [{"id": "wamid.R"}]}

    with mock.patch("requests.post", return_value=_R()):
        for _i in range(3):
            rr = wc.test_sablon_gonder("05551234567")
            check("rate_ok_slot", rr.get("ok") is True)
        r4 = wc.test_sablon_gonder("05551234567")
    check("rate_limit", r4.get("neden") == "rate_limit" and r4.get("ok") is False)


def test_graph_hata_ayrinti_ve_maske():
    _clean()
    ornekler = [
        (190, "OAuthException", "Invalid OAuth access token", "yetki"),
        (200, "OAuthException", "Permissions error", "yetki"),
        (10, "OAuthException", "Application does not have permission", "yetki"),
        (131030, "OAuthException", "Recipient phone number not in allowed list", "kalici"),
        (132001, "OAuthException", "Template name does not exist in the translation", "sablon_onaysiz"),
    ]
    for code, tip, msg, sinif in ornekler:
        body = {"error": {"code": code, "error_subcode": 33, "type": tip, "message": msg}}
        check(f"sinif_{code}", wc.siniflandir_http_hata(400 if code not in (190,) else 401, body) == sinif)
        ay = wc.graph_hata_ayrinti(body)
        check(f"ay_code_{code}", ay.get("code") == code)
        check(f"ay_sub_{code}", ay.get("error_subcode") == 33)
        check(f"ay_type_{code}", ay.get("type") == tip)
        check(f"ay_msg_{code}", msg[:40] in (ay.get("message") or ""))

    # Token/numara görünümlü parçalar runtime'da üretilir (kaynakta bitişik sızıntı yok)
    fake_tok = "EAA" + ("ZzYyXxWw" * 6)
    fake_digits = "9" * 12
    bearer = "Bearer " + ("q" * 28)
    dirty = f"fail {fake_tok} phone={fake_digits} auth={bearer} end"
    masked = wc.maskele_guvenli_metin(dirty)
    check("mask_eaa", "[gizli]" in masked and fake_tok not in masked)
    check("mask_digits", fake_digits not in masked)
    check("mask_bearer", "Bearer [gizli]" in masked and ("q" * 28) not in masked)

    ay2 = wc.graph_hata_ayrinti({
        "error": {
            "code": 190,
            "type": "OAuthException",
            "message": dirty + ("!" * 200),
        }
    })
    check("ay_msg_len", len(ay2.get("message") or "") <= 120)
    check("ay_msg_masked", fake_tok not in (ay2.get("message") or "")
          and fake_digits not in (ay2.get("message") or ""))

    os.environ["WHATSAPP_CLOUD_ACCESS_TOKEN"] = "tok"
    os.environ["WHATSAPP_CLOUD_PHONE_NUMBER_ID"] = "1"

    class _Err:
        status_code = 400
        content = b'{"error":{"code":132001,"error_subcode":99,"type":"OAuthException","message":"template name"}}'

        def json(self):
            return {
                "error": {
                    "code": 132001,
                    "error_subcode": 99,
                    "type": "OAuthException",
                    "message": "template name",
                }
            }

    tel = "05" + ("1" * 9)
    with mock.patch("requests.post", return_value=_Err()):
        r = wc.sablon_gonder(867, tel, "hello_world", "en_US")
    check("post_hata_sinif", r.get("neden") == "sablon_onaysiz")
    check("post_hata_ay", (r.get("hata_ayrinti") or {}).get("code") == 132001)
    check("post_hata_mem", wc.son_hata_sinif() == "sablon_onaysiz"
          and (wc.son_hata_ayrinti() or {}).get("code") == 132001)
    check("post_hata_pii_yok", tel not in str(r) and ("90" + tel[1:]) not in str(r))

    import wa_saglik as ws
    os.environ["WHATSAPP_CLOUD_ENABLED"] = "1"
    k = ws.kontrolleri_uret("ok", "hazir", {})
    cloud = next((x for x in k if x.get("anahtar") == "cloud_api"), None)
    check("saglik_ayrinti", cloud and "code=132001" in (cloud.get("aciklama") or "")
          and "sablon_onaysiz" in (cloud.get("aciklama") or ""), cloud)


def test_test_gonder_route_auth():
    _clean()
    from flask import Flask
    from auth import User, login_manager
    import routes.whatsapp_routes as wr

    rol = {"v": "admin"}

    @login_manager.user_loader
    def _load(_uid):
        return User(id=1, username="t", full_name="T", role=rol["v"],
                    aktif_mi=True, security_stamp="test-stamp")

    app = Flask(__name__)
    app.secret_key = "test"
    login_manager.init_app(app)
    app.register_blueprint(wr.bp)
    c = app.test_client()

    r_anon = c.post("/whatsapp/cloud/test-gonder", json={"numara": "05551234567"})
    check("route_anon_401", r_anon.status_code == 401, r_anon.status_code)
    j_anon = r_anon.get_json(silent=True) or {}
    check("route_anon_json", j_anon.get("ok") is False)
    check("route_anon_pii_yok", "0555" not in str(j_anon) and "90555" not in str(j_anon))

    with c.session_transaction() as s:
        s["_user_id"] = "1"
        s["_fresh"] = True
    rol["v"] = "muhasebe"
    r_muh = c.post("/whatsapp/cloud/test-gonder", json={"numara": "05551234567"})
    check("route_muh_403", r_muh.status_code == 403, r_muh.status_code)

    rol["v"] = "admin"
    r_kapali = c.post(
        "/whatsapp/cloud/test-gonder",
        json={"numara": "05551234567", "sablon": "hello_world", "dil": "en_US"},
    )
    j_kapali = r_kapali.get_json(silent=True) or {}
    check("route_cloud_kapali", j_kapali.get("neden") == "cloud_kapali", j_kapali)
    check("route_kapali_mesaj", "Cloud kapalı" in str(j_kapali.get("mesaj") or ""))

    os.environ["WHATSAPP_CLOUD_ENABLED"] = "1"
    os.environ["WHATSAPP_CLOUD_MIDLER"] = "867"
    os.environ["WHATSAPP_CLOUD_ACCESS_TOKEN"] = "tok-ROUTE"
    os.environ["WHATSAPP_CLOUD_PHONE_NUMBER_ID"] = "1"

    class _R:
        status_code = 200
        content = b'{"messages":[{"id":"wamid.ROUTE"}]}'

        def json(self):
            return {"messages": [{"id": "wamid.ROUTE"}]}

    with mock.patch("requests.post", return_value=_R()):
        r_ok = c.post(
            "/whatsapp/cloud/test-gonder",
            json={"numara": "05551234567", "sablon": "hello_world", "dil": "en_US"},
        )
    j_ok = r_ok.get_json(silent=True) or {}
    check("route_ok", r_ok.status_code == 200 and j_ok.get("ok") is True
          and j_ok.get("wamid") == "wamid.ROUTE" and j_ok.get("durum"), j_ok)
    check("route_ok_yalniz_wamid", set(j_ok.keys()) <= {"ok", "durum", "wamid"})
    check("route_ok_pii_yok", "0555" not in str(j_ok) and "tok-ROUTE" not in str(j_ok))

    html = Path(ROOT / "templates" / "whatsapp" / "saglik.html").read_text(encoding="utf-8")
    check("fe_cloud_test_kart", 'data-cloud-test="1"' in html and 'type="tel"' in html)
    check("fe_autocomplete_off", 'autocomplete="off"' in html)
    check("fe_addEventListener", "addEventListener" in html and "data-cloud-test-gonder" in html)


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
    test_webhook_sayaclar()
    test_test_sablon_kapali_ve_basari()
    test_test_sablon_rate_limit()
    test_graph_hata_ayrinti_ve_maske()
    test_test_gonder_route_auth()
    print("SONUC", "FAIL" if FAILS else "PASS", len(FAILS))
    sys.exit(1 if FAILS else 0)
