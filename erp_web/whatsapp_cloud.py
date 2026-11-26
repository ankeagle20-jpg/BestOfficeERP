# -*- coding: utf-8 -*-
"""Meta WhatsApp Cloud API taşıma katmanı (Node yolunun yanında).

Bayrak: WHATSAPP_CLOUD_ENABLED=1 ve mid ∈ WHATSAPP_CLOUD_MIDLER.
Varsayılan kapalı. Token/secret/verify değerleri loglanmaz.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# facebook_oauth ile aynı Graph sürümü
GRAPH_VERSION_DEFAULT = "v22.0"

# Cloud test şablon gönderimi yalnız bu mid için (allowlist kapısı)
TEST_MID = 867
_TEST_RATE_MAX = 3
_TEST_RATE_PENCERE_SN = 60.0

_SON_HATA_SINIF = "yok"
_rate_lock = threading.Lock()
_test_gonder_zamanlari: list[float] = []

_webhook_lock = threading.Lock()
_webhook_sayac = {
    "messages": 0,
    "sent": 0,
    "delivered": 0,
    "read": 0,
    "failed": 0,
    "statuses_diger": 0,
    "son_olay_ts": None,  # epoch float veya None
}


def son_hata_sinif() -> str:
    return _SON_HATA_SINIF


def _set_hata(sinif: str) -> str:
    global _SON_HATA_SINIF
    _SON_HATA_SINIF = str(sinif or "yok")[:64]
    return _SON_HATA_SINIF


def graph_version() -> str:
    raw = str(os.environ.get("WHATSAPP_CLOUD_GRAPH_VERSION") or "").strip()
    if raw:
        return raw if raw.startswith("v") else f"v{raw}"
    return GRAPH_VERSION_DEFAULT


def cloud_enabled() -> bool:
    return str(os.environ.get("WHATSAPP_CLOUD_ENABLED") or "0").strip() in (
        "1",
        "true",
        "yes",
        "on",
    )


def cloud_midler() -> set[int]:
    raw = os.environ.get("WHATSAPP_CLOUD_MIDLER")
    if raw is None or str(raw).strip() == "":
        return set()
    out: set[int] = set()
    for part in str(raw).split(","):
        tok = part.strip()
        if not tok:
            continue
        try:
            mid = int(tok)
        except (TypeError, ValueError):
            continue
        if mid > 0:
            out.add(mid)
    return out


def cloud_aktif(mid=None) -> bool:
    """ENABLED=1 ve mid listede. Liste boş → kimse."""
    if not cloud_enabled():
        return False
    if mid is None or mid == "":
        return False
    try:
        mid_i = int(mid)
    except (TypeError, ValueError):
        return False
    if mid_i <= 0:
        return False
    return mid_i in cloud_midler()


def _env_tanimli(ad: str) -> bool:
    return bool(str(os.environ.get(ad) or "").strip())


def access_token_tanimli() -> bool:
    return _env_tanimli("WHATSAPP_CLOUD_ACCESS_TOKEN")


def app_secret_tanimli() -> bool:
    return _env_tanimli("WHATSAPP_CLOUD_APP_SECRET")


def verify_token_tanimli() -> bool:
    return _env_tanimli("WHATSAPP_CLOUD_VERIFY_TOKEN")


def phone_number_id_tanimli() -> bool:
    return _env_tanimli("WHATSAPP_CLOUD_PHONE_NUMBER_ID")


def waba_id_tanimli() -> bool:
    return _env_tanimli("WHATSAPP_CLOUD_WABA_ID")


def saglik_ozet() -> dict:
    """Değer yok; yalnız boolean / sınıf adı."""
    return {
        "cloud_acik": cloud_enabled(),
        "token_tanimli": access_token_tanimli(),
        "phone_number_id_tanimli": phone_number_id_tanimli(),
        "app_secret_tanimli": app_secret_tanimli(),
        "verify_token_tanimli": verify_token_tanimli(),
        "son_hata_sinif": son_hata_sinif(),
        "test_mid_aktif": cloud_aktif(TEST_MID),
        "webhook": webhook_sayaclar(),
    }


def webhook_sayaclar() -> dict:
    """Bellek içi webhook sayaçları — numara/içerik yok."""
    with _webhook_lock:
        out = dict(_webhook_sayac)
    ts = out.get("son_olay_ts")
    out["son_olay_ts"] = float(ts) if ts is not None else None
    return out


def _webhook_sayac_sifirla_test() -> None:
    """Yalnız birim testleri."""
    with _webhook_lock:
        for k in list(_webhook_sayac.keys()):
            _webhook_sayac[k] = 0 if k != "son_olay_ts" else None


def test_gonder_rate_ok() -> bool:
    now = time.time()
    with _rate_lock:
        while _test_gonder_zamanlari and _test_gonder_zamanlari[0] < now - _TEST_RATE_PENCERE_SN:
            _test_gonder_zamanlari.pop(0)
        return len(_test_gonder_zamanlari) < _TEST_RATE_MAX


def test_gonder_rate_kaydet() -> None:
    with _rate_lock:
        _test_gonder_zamanlari.append(time.time())


def test_gonder_rate_sifirla_test() -> None:
    with _rate_lock:
        _test_gonder_zamanlari.clear()


def test_sablon_gonder(numara, sablon_adi="hello_world", dil="en_US") -> dict:
    """Admin test: mid=867 kapısı + rate limit. Numara loglanmaz."""
    if not cloud_aktif(TEST_MID):
        _set_hata("cloud_kapali")
        _log_sonuc(TEST_MID, "cloud_kapali")
        return {
            "ok": False,
            "durum": "hata",
            "neden": "cloud_kapali",
            "message_id": "",
            "mesaj": "Cloud kapalı (ENABLED=1 ve mid 867 listede olmalı).",
        }
    if not test_gonder_rate_ok():
        _set_hata("rate_limit")
        _log_sonuc(TEST_MID, "rate_limit")
        return {
            "ok": False,
            "durum": "hata",
            "neden": "rate_limit",
            "message_id": "",
            "mesaj": "Dakikada en fazla 3 test gönderimi.",
        }
    to = numara_e164(numara)
    if not to:
        _set_hata("kalici")
        _log_sonuc(TEST_MID, "kalici")
        return {
            "ok": False,
            "durum": "hata",
            "neden": "kalici",
            "message_id": "",
            "mesaj": "Geçersiz numara.",
        }
    test_gonder_rate_kaydet()
    ad = str(sablon_adi or "hello_world").strip() or "hello_world"
    lang = str(dil or "en_US").strip() or "en_US"
    # numara yalnız çağrıda; log yok
    return sablon_gonder(TEST_MID, to, ad, lang, None)


def numara_e164(raw) -> str:
    """Mevcut normalize_wa_telefon → Cloud API digits (ülke kodlu, + yok)."""
    from odeme_linki import normalize_wa_telefon

    return normalize_wa_telefon(raw) or ""


def _phone_number_id() -> str:
    return str(os.environ.get("WHATSAPP_CLOUD_PHONE_NUMBER_ID") or "").strip()


def _access_token() -> str:
    return str(os.environ.get("WHATSAPP_CLOUD_ACCESS_TOKEN") or "").strip()


def _messages_url() -> str:
    pid = _phone_number_id()
    return f"https://graph.facebook.com/{graph_version()}/{pid}/messages"


def siniflandir_http_hata(status: int, body: dict | None = None) -> str:
    """geçici | kalici | yetki | sablon_onaysiz | pencere_disi | bilinemeyen"""
    code = None
    msg = ""
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        try:
            code = int(err.get("code") or 0) or None
        except (TypeError, ValueError):
            code = None
        msg = str(err.get("message") or err.get("error_user_msg") or "").lower()
        sub = str(err.get("error_subcode") or "")
    else:
        sub = ""

    if int(status) in (401, 403) or code in (190, 10, 200, 4):
        return _set_hata("yetki")
    if code == 131047 or "24 hour" in msg or "outside" in msg and "window" in msg:
        return _set_hata("pencere_disi")
    if code in (132000, 132001, 132005, 132007, 132012, 132015, 132016) or "template" in msg:
        return _set_hata("sablon_onaysiz")
    if int(status) in (429, 500, 502, 503, 504) or code in (1, 2, 4, 17, 80007):
        return _set_hata("gecici")
    if int(status) >= 400:
        return _set_hata("kalici")
    return _set_hata("bilinemeyen")


def _log_sonuc(mid, sinif: str, message_id: str = "") -> None:
    logger.info(
        "wa_cloud mid=%s sinif=%s msgid=%s",
        int(mid) if mid is not None else 0,
        (sinif or "-")[:32],
        (message_id or "-")[:64],
    )


def _post_messages(payload: dict, mid) -> dict:
    """POST Graph messages. Dönüş: ok, message_id, sinif, durum(kuyruk uyumlu)."""
    import requests

    if not access_token_tanimli() or not phone_number_id_tanimli():
        _set_hata("yetki")
        _log_sonuc(mid, "yetki")
        return {"ok": False, "durum": "hata", "neden": "yetki", "message_id": ""}

    token = _access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(_messages_url(), json=payload, headers=headers, timeout=(5, 30))
    except requests.exceptions.Timeout:
        _set_hata("gecici")
        _log_sonuc(mid, "gecici")
        return {"ok": False, "durum": "belirsiz", "neden": "gecici", "message_id": ""}
    except Exception:
        _set_hata("gecici")
        _log_sonuc(mid, "gecici")
        return {"ok": False, "durum": "hata", "neden": "gecici", "message_id": ""}

    try:
        body = r.json() if r.content else {}
    except Exception:
        body = {}

    if int(r.status_code) >= 400 or not isinstance(body, dict):
        sinif = siniflandir_http_hata(int(r.status_code), body if isinstance(body, dict) else None)
        _log_sonuc(mid, sinif)
        return {"ok": False, "durum": "hata", "neden": sinif, "message_id": ""}

    msg_id = ""
    msgs = body.get("messages")
    if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict):
        msg_id = str(msgs[0].get("id") or "")
    _set_hata("yok")
    _log_sonuc(mid, "ok", msg_id)
    return {"ok": True, "durum": "gonderildi", "neden": "", "message_id": msg_id}


def sablon_gonder(mid, numara, sablon_adi, dil, parametreler=None) -> dict:
    """Onaylı şablon gönderimi."""
    to = numara_e164(numara)
    if not to:
        _set_hata("kalici")
        _log_sonuc(mid, "kalici")
        return {"ok": False, "durum": "hata", "neden": "kalici", "message_id": ""}
    ad = str(sablon_adi or "").strip()
    if not ad:
        _set_hata("sablon_onaysiz")
        _log_sonuc(mid, "sablon_onaysiz")
        return {"ok": False, "durum": "hata", "neden": "sablon_onaysiz", "message_id": ""}
    lang = str(dil or "tr").strip() or "tr"
    components = []
    if parametreler:
        params = []
        for p in parametreler:
            params.append({"type": "text", "text": str(p)})
        if params:
            components.append({"type": "body", "parameters": params})
    template: dict[str, Any] = {
        "name": ad,
        "language": {"code": lang},
    }
    if components:
        template["components"] = components
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": template,
    }
    return _post_messages(payload, mid)


def metin_gonder(mid, numara, metin, *, pencere_acik=None) -> dict:
    """Serbest metin: yalnız 24s müşteri hizmeti penceresi açıkken.

    pencere_acik True değilse (None/False) REDDET — bu turda pencere DB yok.
    """
    if pencere_acik is not True:
        _set_hata("pencere_disi")
        _log_sonuc(mid, "pencere_disi")
        return {
            "ok": False,
            "durum": "hata",
            "neden": "pencere_disi",
            "message_id": "",
            "mesaj": "Cloud API serbest metin için 24 saatlik pencere gerekli; pencere bilgisi yok veya kapalı.",
        }
    to = numara_e164(numara)
    body = str(metin or "").strip()
    if not to or not body:
        _set_hata("kalici")
        _log_sonuc(mid, "kalici")
        return {"ok": False, "durum": "hata", "neden": "kalici", "message_id": ""}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }
    return _post_messages(payload, mid)


def kuyruk_sonucuna(cloud_sonuc: dict) -> dict:
    """sozlesme_whatsapp _arkaplan_yaz uyumlu paket."""
    if not isinstance(cloud_sonuc, dict):
        return {"durum": "hata", "neden": "kalici"}
    return {
        "durum": str(cloud_sonuc.get("durum") or "hata"),
        "neden": str(cloud_sonuc.get("neden") or ""),
    }


def verify_challenge(mode, token, challenge) -> tuple[str | None, int]:
    """GET webhook: hub.mode / hub.verify_token / hub.challenge."""
    if str(mode or "") != "subscribe":
        return None, 403
    expected = str(os.environ.get("WHATSAPP_CLOUD_VERIFY_TOKEN") or "").strip()
    got = str(token or "")
    if not expected or not _sabit_zaman_esit(expected, got):
        return None, 403
    return str(challenge if challenge is not None else ""), 200


def _sabit_zaman_esit(a: str, b: str) -> bool:
    try:
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
    except Exception:
        return False


def imza_dogrula(raw_body: bytes, signature_header: str | None) -> bool:
    """X-Hub-Signature-256: sha256=<hex> — APP_SECRET ile HMAC-SHA256."""
    secret = str(os.environ.get("WHATSAPP_CLOUD_APP_SECRET") or "").strip()
    if not secret:
        return False
    hdr = str(signature_header or "").strip()
    if not hdr.lower().startswith("sha256="):
        return False
    got = hdr.split("=", 1)[1].strip().lower()
    dig = hmac.new(secret.encode("utf-8"), raw_body or b"", hashlib.sha256).hexdigest()
    return _sabit_zaman_esit(dig, got)


def webhook_olaylari_isle(payload: dict) -> dict:
    """messages/statuses sayaç — DB yok; numara/içerik yok."""
    n_msg = 0
    n_sent = n_del = n_read = n_fail = n_diger = 0
    try:
        entries = payload.get("entry") if isinstance(payload, dict) else None
        for ent in entries or []:
            for ch in (ent.get("changes") or []) if isinstance(ent, dict) else []:
                val = ch.get("value") if isinstance(ch, dict) else None
                if not isinstance(val, dict):
                    continue
                msgs = val.get("messages") or []
                sts = val.get("statuses") or []
                if isinstance(msgs, list):
                    n_msg += len(msgs)
                if isinstance(sts, list):
                    for st in sts:
                        if not isinstance(st, dict):
                            n_diger += 1
                            continue
                        s = str(st.get("status") or "").strip().lower()
                        if s == "sent":
                            n_sent += 1
                        elif s == "delivered":
                            n_del += 1
                        elif s == "read":
                            n_read += 1
                        elif s == "failed":
                            n_fail += 1
                        else:
                            n_diger += 1
    except Exception:
        logger.info("wa_cloud_webhook parse_hata")
    n_st = n_sent + n_del + n_read + n_fail + n_diger
    if n_msg or n_st:
        with _webhook_lock:
            _webhook_sayac["messages"] = int(_webhook_sayac["messages"] or 0) + n_msg
            _webhook_sayac["sent"] = int(_webhook_sayac["sent"] or 0) + n_sent
            _webhook_sayac["delivered"] = int(_webhook_sayac["delivered"] or 0) + n_del
            _webhook_sayac["read"] = int(_webhook_sayac["read"] or 0) + n_read
            _webhook_sayac["failed"] = int(_webhook_sayac["failed"] or 0) + n_fail
            _webhook_sayac["statuses_diger"] = int(_webhook_sayac["statuses_diger"] or 0) + n_diger
            _webhook_sayac["son_olay_ts"] = time.time()
    logger.info(
        "wa_cloud_webhook events messages=%s sent=%s delivered=%s read=%s failed=%s",
        int(n_msg),
        int(n_sent),
        int(n_del),
        int(n_read),
        int(n_fail),
    )
    return {
        "messages": n_msg,
        "statuses": n_st,
        "sent": n_sent,
        "delivered": n_del,
        "read": n_read,
        "failed": n_fail,
    }


def phone_meta_salt_okuma() -> dict:
    """Yönetici manuel: phone number id meta. Token/değer loglanmaz."""
    import requests

    if not access_token_tanimli() or not phone_number_id_tanimli():
        return {"ok": False, "sinif": "yetki"}
    pid = _phone_number_id()
    url = f"https://graph.facebook.com/{graph_version()}/{pid}"
    try:
        r = requests.get(
            url,
            params={"fields": "id,display_phone_number_quality_rating,verified_name"},
            headers={"Authorization": f"Bearer {_access_token()}"},
            timeout=(5, 20),
        )
    except Exception:
        return {"ok": False, "sinif": "gecici"}
    if int(r.status_code) >= 400:
        try:
            body = r.json()
        except Exception:
            body = {}
        return {"ok": False, "sinif": siniflandir_http_hata(int(r.status_code), body)}
    return {"ok": True, "sinif": "yok", "http": int(r.status_code)}
