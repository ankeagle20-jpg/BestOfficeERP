# -*- coding: utf-8 -*-
"""A2.3 — imzalı, tek kullanımlık PayTR public checkout token (HMAC).

Format: ``{invoice_id}.{exp_unix}.{nonce}.{hmac_hex}``
Secret: PAYTR_CHECKOUT_TOKEN_SECRET env → vault paytr.checkout_token_secret
         → Flask SECRET_KEY (son çare).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Any

from db import execute_returning, fetch_one

logger = logging.getLogger(__name__)

DEFAULT_TTL_SEC = 24 * 60 * 60  # fatura due_at ile uyumlu
_MIN_SECRET_LEN = 16


class CheckoutTokenError(Exception):
    """Token üretimi / doğrulama hatası (iç mesaj; HTTP katmanı geneller)."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _meta_dict(val: Any) -> dict:
    if val is None or val == "":
        return {}
    if isinstance(val, dict):
        return dict(val)
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def resolve_checkout_token_secret() -> bytes:
    """Vault / env / Flask SECRET_KEY — sırayı fail-closed uygula."""
    raw = (os.environ.get("PAYTR_CHECKOUT_TOKEN_SECRET") or "").strip()
    if not raw:
        try:
            from credentials_vault import get_credential

            raw = (get_credential("paytr.checkout_token_secret") or "").strip()
        except Exception:
            logger.exception("checkout token vault read failed")
            raw = ""
    if not raw:
        try:
            from flask import current_app, has_app_context

            if has_app_context():
                raw = str(current_app.config.get("SECRET_KEY") or "").strip()
        except Exception:
            raw = ""
    if not raw or len(raw) < _MIN_SECRET_LEN:
        raise CheckoutTokenError(
            "secret_missing",
            "checkout token secret eksik veya çok kısa",
        )
    return raw.encode("utf-8")


def mint_pay_token(invoice_id: int, *, ttl_sec: int = DEFAULT_TTL_SEC) -> tuple[str, str, int]:
    """İmzalı token üret. Dönüş: (token, nonce, exp_unix)."""
    iid = int(invoice_id)
    if iid <= 0:
        raise CheckoutTokenError("bad_invoice", "geçersiz invoice_id")
    ttl = int(ttl_sec) if ttl_sec else DEFAULT_TTL_SEC
    if ttl < 60 or ttl > 7 * 24 * 3600:
        raise CheckoutTokenError("bad_ttl", "geçersiz ttl")
    secret = resolve_checkout_token_secret()
    nonce = secrets.token_hex(8)
    exp = int(time.time()) + ttl
    msg = f"{iid}.{exp}.{nonce}"
    sig = hmac.new(secret, msg.encode("utf-8"), hashlib.sha256).hexdigest()
    token = f"{msg}.{sig}"
    return token, nonce, exp


def parse_and_verify_mac(token: str, *, expected_invoice_id: int) -> tuple[int, int, str]:
    """HMAC + yapı + invoice_id eşleşmesi. DB/single-use yok. (invoice_id, exp, nonce)."""
    raw = str(token or "").strip()
    if not raw or len(raw) > 512:
        raise CheckoutTokenError("malformed", "token yok veya çok uzun")
    parts = raw.split(".")
    if len(parts) != 4:
        raise CheckoutTokenError("malformed", "token formatı geçersiz")
    id_s, exp_s, nonce, sig = parts
    if not id_s.isdigit() or not exp_s.isdigit():
        raise CheckoutTokenError("malformed", "token alanları geçersiz")
    if not nonce or not all(c in "0123456789abcdef" for c in nonce) or len(nonce) != 16:
        raise CheckoutTokenError("malformed", "nonce geçersiz")
    if not sig or not all(c in "0123456789abcdef" for c in sig) or len(sig) != 64:
        raise CheckoutTokenError("malformed", "imza geçersiz")
    invoice_id = int(id_s)
    exp = int(exp_s)
    if invoice_id != int(expected_invoice_id):
        raise CheckoutTokenError("invoice_mismatch", "token invoice_id uyuşmazlığı")
    now = int(time.time())
    if exp < now:
        raise CheckoutTokenError("expired", "token süresi dolmuş")
    # clock skew: max 7 gün ileri exp zaten mint'te sınırlı; ekstra üst sınır
    if exp > now + 7 * 24 * 3600 + 60:
        raise CheckoutTokenError("expired", "token exp anormal")
    secret = resolve_checkout_token_secret()
    msg = f"{invoice_id}.{exp}.{nonce}"
    expected = hmac.new(secret, msg.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise CheckoutTokenError("bad_sig", "HMAC doğrulanamadı")
    return invoice_id, exp, nonce


def consume_pay_token(invoice_id: int, nonce: str) -> bool:
    """Tek kullanım: nonce eşleşir ve henüz kullanılmamışsa damgala. True = bu istek kazandı."""
    row = fetch_one(
        "SELECT id, metadata FROM public.platform_tenant_invoices WHERE id=%s",
        (int(invoice_id),),
    )
    if not row:
        raise CheckoutTokenError("not_found", "fatura yok")
    meta = _meta_dict(row.get("metadata"))
    stored = str(meta.get("pay_token_nonce") or "").strip()
    if not stored or not hmac.compare_digest(stored, str(nonce)):
        raise CheckoutTokenError("nonce_mismatch", "token bu faturaya ait değil")
    if meta.get("pay_token_used_at"):
        raise CheckoutTokenError("already_used", "token daha önce kullanıldı")

    meta["pay_token_used_at"] = int(time.time())
    updated = execute_returning(
        """
        UPDATE public.platform_tenant_invoices
        SET metadata = %s::jsonb, updated_at = NOW()
        WHERE id = %s
          AND (metadata->>'pay_token_used_at') IS NULL
          AND (metadata->>'pay_token_nonce') = %s
        RETURNING id
        """,
        (json.dumps(meta), int(invoice_id), str(nonce)),
    )
    if not updated:
        raise CheckoutTokenError("already_used", "token daha önce kullanıldı")
    return True


def attach_pay_token_to_invoice_metadata(invoice_id: int, *, ttl_sec: int = DEFAULT_TTL_SEC) -> str:
    """Fatura metadata'sına nonce/exp yazar ve imzalı token döner."""
    token, nonce, exp = mint_pay_token(invoice_id, ttl_sec=ttl_sec)
    row = fetch_one(
        "SELECT metadata FROM public.platform_tenant_invoices WHERE id=%s",
        (int(invoice_id),),
    )
    if not row:
        raise CheckoutTokenError("not_found", "fatura yok")
    meta = _meta_dict(row.get("metadata"))
    meta["pay_token_nonce"] = nonce
    meta["pay_token_exp"] = exp
    # yeniden mint: önceki kullanım damgasını temizle
    meta.pop("pay_token_used_at", None)
    updated = execute_returning(
        """
        UPDATE public.platform_tenant_invoices
        SET metadata = %s::jsonb, updated_at = NOW()
        WHERE id = %s
        RETURNING id
        """,
        (json.dumps(meta), int(invoice_id)),
    )
    if not updated:
        raise CheckoutTokenError("not_found", "fatura güncellenemedi")
    return token
