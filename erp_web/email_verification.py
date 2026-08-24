# -*- coding: utf-8 -*-
"""E-posta doğrulama token'ları — signup sonrası arka plan doğrulama."""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone

from flask import current_app, g

from db import db, execute
from mail_utils import send_verification_email
from tenant_identity import _tenant_apex_domains, schema_name_for_slug

logger = logging.getLogger(__name__)


class EmailVerificationError(Exception):
    """Token doğrulama başarısız — transaction rollback için."""


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _token_row_is_valid(row: dict | None) -> bool:
    if not row:
        return False
    if not row.get("is_active"):
        return False
    if row.get("used_at"):
        return False
    expires_at = row.get("expires_at")
    if expires_at is None:
        return False
    if isinstance(expires_at, datetime):
        exp = expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp > datetime.now(timezone.utc)
    return True


def _invalidate_unused_tokens(user_id: int) -> None:
    execute(
        """
        UPDATE email_verification_tokens
        SET used_at = NOW()
        WHERE user_id = %s AND used_at IS NULL
        """,
        (user_id,),
    )


def issue_verification_token(user_id: int) -> str:
    """Yeni doğrulama token'ı üretir (ham token döner, hash DB'de)."""
    ttl = int(current_app.config.get("EMAIL_VERIFICATION_TTL_SEC", 604800))
    _invalidate_unused_tokens(user_id)
    raw_token = secrets.token_urlsafe(32)
    execute(
        """
        INSERT INTO email_verification_tokens (user_id, token_hash, expires_at)
        VALUES (%s, %s, NOW() + (%s * interval '1 second'))
        """,
        (user_id, _token_hash(raw_token), ttl),
    )
    return raw_token


def verification_url_for_slug(slug: str, raw_token: str) -> str:
    apex = (_tenant_apex_domains() or ("payafin.com",))[0]
    return f"https://{slug}.{apex}/verify-email?token={raw_token}"


def send_signup_verification_email(slug: str, user_id: int, email: str) -> bool:
    """Signup provizyonu sonrası admin'e doğrulama e-postası gönder."""
    email = str(email or "").strip().lower()
    if "@" not in email:
        return False
    schema = schema_name_for_slug(slug)
    if not schema:
        return False
    g.tenant_schema = schema
    g.tenant_slug = slug
    try:
        raw_token = issue_verification_token(int(user_id))
        verify_url = verification_url_for_slug(slug, raw_token)
        sent = send_verification_email(email, verify_url)
        if not sent:
            logger.warning(
                "verification email send failed slug=%s user_id=%s",
                slug,
                user_id,
            )
        return sent
    finally:
        g.tenant_schema = None
        g.tenant_slug = None


def apply_email_verification(raw_token: str) -> bool:
    """Geçerli token ile email_verified_at işaretle (giriş engellenmez)."""
    token_hash = _token_hash(str(raw_token or "").strip())
    if not token_hash:
        raise EmailVerificationError("missing token")
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT t.id, t.user_id, t.expires_at, t.used_at, u.is_active
            FROM email_verification_tokens t
            JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = %s
            FOR UPDATE OF t
            """,
            (token_hash,),
        )
        row = cur.fetchone()
        if not row:
            raise EmailVerificationError("token not found")
        if isinstance(row, dict):
            data = row
        else:
            data = {
                "id": row[0],
                "user_id": row[1],
                "expires_at": row[2],
                "used_at": row[3],
                "is_active": row[4],
            }
        if not _token_row_is_valid(data):
            raise EmailVerificationError("token invalid or expired")
        cur.execute(
            """
            UPDATE users
            SET email_verified_at = COALESCE(email_verified_at, NOW())
            WHERE id = %s
            """,
            (data["user_id"],),
        )
        if cur.rowcount != 1:
            raise EmailVerificationError("user update failed")
        cur.execute(
            """
            UPDATE email_verification_tokens
            SET used_at = NOW()
            WHERE id = %s
            """,
            (data["id"],),
        )
        if cur.rowcount != 1:
            raise EmailVerificationError("token mark failed")
    return True
