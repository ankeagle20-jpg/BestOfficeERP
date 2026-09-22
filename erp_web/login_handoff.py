# -*- coding: utf-8 -*-
"""Apex → kiracı imzalı tek kullanımlık login handoff bileti (şifre taşımaz)."""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from db import db, ensure_login_handoff_tokens_table, execute, fetch_one

logger = logging.getLogger(__name__)

HANDOFF_TTL_SEC = 60
_SERIALIZER_SALT = "payafin-login-handoff-v1"


def _serializer() -> URLSafeTimedSerializer:
    secret = current_app.config.get("SECRET_KEY") or "degistir-bunu-uretimde"
    return URLSafeTimedSerializer(str(secret), salt=_SERIALIZER_SALT)


def mint_login_handoff_token(
    *,
    user_id: int,
    tenant_slug: str,
    security_stamp: str | None,
    next_path: str | None = None,
) -> str:
    """İmzalı token üret + jti'yi public.login_handoff_tokens'a yaz (TTL 60 sn)."""
    ensure_login_handoff_tokens_table()
    jti = secrets.token_urlsafe(24)
    slug = str(tenant_slug or "").strip().lower()
    stamp = str(security_stamp or "")
    nxt = str(next_path or "").strip() or ""
    payload = {
        "jti": jti,
        "uid": int(user_id),
        "slug": slug,
        "stamp": stamp,
        "next": nxt,
    }
    token = _serializer().dumps(payload)
    execute(
        """
        INSERT INTO public.login_handoff_tokens (jti, tenant_slug, user_id, expires_at)
        VALUES (%s, %s, %s, NOW() + (%s * interval '1 second'))
        """,
        (jti, slug, int(user_id), HANDOFF_TTL_SEC),
    )
    return token


def consume_login_handoff_token(
    raw_token: str,
    *,
    expected_slug: str,
) -> dict | None:
    """Token doğrula + tek kullanımlık tüket.

    Döner: {user_id, tenant_slug, security_stamp, next} veya None.
    """
    ensure_login_handoff_tokens_table()
    raw = str(raw_token or "").strip()
    if not raw:
        return None
    expected = str(expected_slug or "").strip().lower()
    if not expected:
        return None
    try:
        data = _serializer().loads(raw, max_age=HANDOFF_TTL_SEC)
    except SignatureExpired:
        logger.info("login handoff expired")
        return None
    except BadSignature:
        logger.info("login handoff bad signature")
        return None
    if not isinstance(data, dict):
        return None
    jti = str(data.get("jti") or "").strip()
    slug = str(data.get("slug") or "").strip().lower()
    stamp = str(data.get("stamp") or "")
    try:
        uid = int(data.get("uid"))
    except (TypeError, ValueError):
        return None
    if not jti or not slug or uid < 1:
        return None
    if slug != expected:
        logger.info("login handoff slug mismatch token=%s host=%s", slug, expected)
        return None

    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT jti, tenant_slug, user_id, expires_at, used_at
            FROM public.login_handoff_tokens
            WHERE jti = %s
            FOR UPDATE
            """,
            (jti,),
        )
        row = cur.fetchone()
        if not row:
            return None
        rec = dict(row)
        if rec.get("used_at") is not None:
            return None
        if str(rec.get("tenant_slug") or "").strip().lower() != slug:
            return None
        if int(rec.get("user_id") or 0) != uid:
            return None
        expires_at = rec.get("expires_at")
        if expires_at is not None:
            exp = expires_at
            if isinstance(exp, datetime) and exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if isinstance(exp, datetime) and exp <= datetime.now(timezone.utc):
                return None
        cur.execute(
            """
            UPDATE public.login_handoff_tokens
            SET used_at = NOW()
            WHERE jti = %s AND used_at IS NULL
            """,
            (jti,),
        )
        if cur.rowcount != 1:
            return None

    nxt = str(data.get("next") or "").strip() or None
    return {
        "user_id": uid,
        "tenant_slug": slug,
        "security_stamp": stamp,
        "next": nxt,
    }
