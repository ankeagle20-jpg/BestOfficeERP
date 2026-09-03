# -*- coding: utf-8 -*-
"""PayTR iFrame API yardımcıları (get-token imza + callback doğrulama).

Secret değerler asla loglanmaz. Vault: credentials_vault.get_credential.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any

from credentials_vault import get_credential

logger = logging.getLogger(__name__)

PAYTR_GET_TOKEN_URL = "https://www.paytr.com/odeme/api/get-token"


class PaytrClientError(RuntimeError):
    """PayTR yardımcı hata — mesajda secret içermez."""


def _req_str(val: Any, field: str) -> str:
    s = "" if val is None else str(val).strip()
    if not s:
        raise PaytrClientError(f"{field} gerekli")
    return s


def _load_merchant_secrets() -> tuple[str, str, str]:
    """merchant_id, merchant_key, merchant_salt — değerleri loglamaz."""
    mid = (get_credential("paytr.merchant_id") or "").strip()
    mkey = (get_credential("paytr.merchant_key") or "").strip()
    msalt = (get_credential("paytr.merchant_salt") or "").strip()
    missing = [
        name
        for name, val in (
            ("paytr.merchant_id", mid),
            ("paytr.merchant_key", mkey),
            ("paytr.merchant_salt", msalt),
        )
        if not val
    ]
    if missing:
        logger.error("PayTR vault eksik alanlar: %s", ",".join(missing))
        raise PaytrClientError(
            "PayTR merchant kimlik bilgileri eksik (vault/env): "
            + ", ".join(missing)
        )
    return mid, mkey, msalt


def build_get_token_request(
    merchant_oid: str,
    payment_amount_kurus: str,
    currency: str,
    user_ip: str,
    email: str,
    user_basket: str,
    user_name: str,
    user_address: str,
    user_phone: str,
    merchant_ok_url: str,
    merchant_fail_url: str,
    test_mode: str,
    no_installment: str = "0",
    max_installment: str = "0",
    *,
    timeout_limit: str = "30",
    debug_on: str = "0",
    lang: str = "tr",
) -> dict[str, str]:
    """iFrame get-token POST gövdesi (application/x-www-form-urlencoded alanları).

    İmza (smoke ile aynı):
      hash_str = merchant_id + user_ip + merchant_oid + email + payment_amount
               + user_basket + no_installment + max_installment + currency + test_mode
      paytr_token = base64(HMAC_SHA256(key=merchant_key, msg=hash_str + merchant_salt))
    """
    merchant_id, merchant_key, merchant_salt = _load_merchant_secrets()

    merchant_oid = _req_str(merchant_oid, "merchant_oid")
    payment_amount = _req_str(payment_amount_kurus, "payment_amount_kurus")
    currency = _req_str(currency, "currency")
    user_ip = _req_str(user_ip, "user_ip")
    email = _req_str(email, "email")
    user_basket = _req_str(user_basket, "user_basket")
    user_name = _req_str(user_name, "user_name")
    user_address = _req_str(user_address, "user_address")
    user_phone = _req_str(user_phone, "user_phone")
    merchant_ok_url = _req_str(merchant_ok_url, "merchant_ok_url")
    merchant_fail_url = _req_str(merchant_fail_url, "merchant_fail_url")
    test_mode = _req_str(test_mode, "test_mode")
    no_installment = _req_str(no_installment, "no_installment")
    max_installment = _req_str(max_installment, "max_installment")
    timeout_limit = _req_str(timeout_limit, "timeout_limit")
    debug_on = _req_str(debug_on, "debug_on")
    lang = _req_str(lang, "lang")

    if not merchant_oid.isalnum():
        raise PaytrClientError(
            "merchant_oid alfanümerik olmalı (özel karakter / tire yok)"
        )
    if not payment_amount.isdigit():
        raise PaytrClientError("payment_amount_kurus yalnızca rakam olmalı")

    hash_str = (
        merchant_id
        + user_ip
        + merchant_oid
        + email
        + payment_amount
        + user_basket
        + no_installment
        + max_installment
        + currency
        + test_mode
    )
    paytr_token = base64.b64encode(
        hmac.new(
            merchant_key.encode("utf-8"),
            (hash_str + merchant_salt).encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("ascii")

    return {
        "merchant_id": merchant_id,
        "user_ip": user_ip,
        "merchant_oid": merchant_oid,
        "email": email,
        "payment_amount": payment_amount,
        "paytr_token": paytr_token,
        "user_basket": user_basket,
        "debug_on": debug_on,
        "no_installment": no_installment,
        "max_installment": max_installment,
        "user_name": user_name,
        "user_address": user_address,
        "user_phone": user_phone,
        "merchant_ok_url": merchant_ok_url,
        "merchant_fail_url": merchant_fail_url,
        "timeout_limit": timeout_limit,
        "currency": currency,
        "test_mode": test_mode,
        "lang": lang,
    }


def verify_callback_signature(
    merchant_oid: str,
    status: str,
    total_amount: str,
    received_hash: str,
) -> bool:
    """Bildirim URL hash doğrulama (timing-safe).

    paytr_token_str = merchant_oid + merchant_salt + status + total_amount
    expected = base64(HMAC_SHA256(key=merchant_key, msg=paytr_token_str))
    """
    try:
        _, merchant_key, merchant_salt = _load_merchant_secrets()
        merchant_oid = _req_str(merchant_oid, "merchant_oid")
        status = _req_str(status, "status")
        total_amount = _req_str(total_amount, "total_amount")
        received = "" if received_hash is None else str(received_hash).strip()
        if not received:
            logger.warning("PayTR callback hash boş")
            return False

        paytr_token_str = merchant_oid + merchant_salt + status + total_amount
        expected = base64.b64encode(
            hmac.new(
                merchant_key.encode("utf-8"),
                paytr_token_str.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("ascii")
        return hmac.compare_digest(expected, received)
    except PaytrClientError:
        logger.exception("PayTR callback imza doğrulama yapılandırma hatası")
        return False
    except Exception:
        logger.exception("PayTR callback imza doğrulama beklenmeyen hata")
        return False
