# -*- coding: utf-8 -*-
"""Payafin — /forgot-password (talep) ve /reset-password (yeni şifre).

Aşama 0 düzeltmeleri:
- Tenant host'ta password_reset_tokens DDL ensure
- Apex/marketing: lookup → doğru kiracı şemasına token + {slug}.{apex} reset link
- SMTP başarısızlığında UI'da açık hata (enumeration'sız: yalnız gönderim denendiğinde)
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from psycopg2 import sql as psql
from werkzeug.security import generate_password_hash

from auth import generate_security_stamp
from db import (
    db,
    ensure_password_reset_tokens_in_schema,
    ensure_password_reset_tokens_table,
    execute,
    fetch_one,
)
from mail_utils import send_password_reset_email
from password_reset_rate_limit import (
    check_forgot_password_identifier_rate,
    check_forgot_password_ip_rate,
    client_ip,
)
from signup_validation import (
    validate_password_confirm,
    validate_password_strength,
)
from tenant_identity import (
    _tenant_apex_domains,
    is_payafin_marketing_host,
    schema_name_for_slug,
)

logger = logging.getLogger(__name__)

bp = Blueprint("forgot_password", __name__)

NEUTRAL_MSG = (
    "Eğer bu bilgilerle kayıtlı bir hesap varsa, "
    "şifre sıfırlama bağlantısı e-posta adresinize gönderildi."
)
SMTP_FAIL_MSG = (
    "Şifre sıfırlama e-postası gönderilemedi (posta sunucusu hatası). "
    "Lütfen daha sonra tekrar deneyin veya destek ile iletişime geçin."
)
EMAIL_REQUIRED_MSG = (
    "Şifre sıfırlama için geçerli bir e-posta adresi gerekli."
)
RATE_LIMIT_MSG = "Çok fazla deneme, lütfen bekleyin."
INVALID_TOKEN_MSG = "Bağlantı geçersiz veya süresi dolmuş."
MISSING_TOKEN_MSG = "Geçersiz istek. Şifre sıfırlama bağlantısı eksik."
SUCCESS_MSG = "Şifreniz başarıyla güncellendi. Yeni şifrenizle giriş yapabilirsiniz."
APEX_HINT_MSG = (
    "Şifre sıfırlama, kiracı (firma) adresinizde tamamlanır. "
    "E-postanızı girin; kayıtlıysa bağlantı e-posta ile gönderilir."
)

_PASSWORD_STRENGTH_MSG = {
    "password_too_short": "Şifre en az 10 karakter olmalıdır.",
    "password_needs_upper": "Şifre en az bir büyük harf içermelidir.",
    "password_needs_lower": "Şifre en az bir küçük harf içermelidir.",
    "password_needs_digit": "Şifre en az bir rakam içermelidir.",
}


class PasswordResetTransactionError(Exception):
    """with db() içi reset başarısız — exception ile rollback tetiklenir."""


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _tenant_reset_host(slug: str) -> str:
    apex = (_tenant_apex_domains() or ("payafin.com",))[0]
    return f"{slug}.{apex}"


def _invalidate_unused_tokens_in_schema(schema: str, user_id: int) -> None:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            psql.SQL(
                """
                UPDATE {}.password_reset_tokens
                SET used_at = NOW()
                WHERE user_id = %s AND used_at IS NULL
                """
            ).format(psql.Identifier(schema)),
            (user_id,),
        )


def _issue_reset_token_in_schema(
    schema: str, user_id: int, request_ip: str | None
) -> str:
    ttl = int(current_app.config.get("PASSWORD_RESET_TTL_SEC", 3600))
    raw_token = secrets.token_urlsafe(32)
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            psql.SQL(
                """
                INSERT INTO {}.password_reset_tokens
                    (user_id, token_hash, expires_at, request_ip)
                VALUES (%s, %s, NOW() + (%s * interval '1 second'), %s)
                """
            ).format(psql.Identifier(schema)),
            (user_id, _token_hash(raw_token), ttl, request_ip),
        )
    return raw_token


def _invalidate_unused_tokens(user_id: int) -> None:
    execute(
        """
        UPDATE password_reset_tokens
        SET used_at = NOW()
        WHERE user_id = %s AND used_at IS NULL
        """,
        (user_id,),
    )


def _issue_reset_token(user_id: int, request_ip: str | None) -> str:
    ttl = int(current_app.config.get("PASSWORD_RESET_TTL_SEC", 3600))
    raw_token = secrets.token_urlsafe(32)
    execute(
        """
        INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, request_ip)
        VALUES (%s, %s, NOW() + (%s * interval '1 second'), %s)
        """,
        (user_id, _token_hash(raw_token), ttl, request_ip),
    )
    return raw_token


def _send_reset_email_result(
    *,
    username: str,
    user_id: int,
    reset_host: str,
    schema: str | None,
) -> str:
    """Döner: 'sent' | 'no_email' | 'smtp_failed'.

    schema=None → mevcut search_path (tenant host).
    schema set → nitelikli SQL (apex → kiracı).
    """
    if "@" not in username:
        logger.info(
            "password reset skipped (no email) user_id=%s host=%s",
            user_id,
            reset_host,
        )
        return "no_email"

    if schema:
        ensure_password_reset_tokens_in_schema(schema)
        _invalidate_unused_tokens_in_schema(schema, user_id)
        raw_token = _issue_reset_token_in_schema(schema, user_id, client_ip())
    else:
        ensure_password_reset_tokens_table()
        _invalidate_unused_tokens(user_id)
        raw_token = _issue_reset_token(user_id, client_ip())

    reset_url = f"https://{reset_host}/reset-password?token={raw_token}"
    if send_password_reset_email(username, reset_url):
        logger.info(
            "password reset mail sent user_id=%s host=%s schema=%s",
            user_id,
            reset_host,
            schema or "(search_path)",
        )
        return "sent"

    logger.warning(
        "password reset mail FAILED user_id=%s host=%s schema=%s",
        user_id,
        reset_host,
        schema or "(search_path)",
    )
    return "smtp_failed"


def _flash_send_result(result: str) -> None:
    if result == "sent":
        flash(NEUTRAL_MSG, "info")
    elif result == "smtp_failed":
        flash(SMTP_FAIL_MSG, "danger")
    elif result == "no_email":
        flash(EMAIL_REQUIRED_MSG, "danger")
    else:
        flash(NEUTRAL_MSG, "info")


def _lookup_tenant_by_email(email: str) -> dict | None:
    """public.tenant_user_lookup → aktif kiracı + şema."""
    row = fetch_one(
        """
        SELECT l.email, l.tenant_slug AS slug, t.schema_name, t.status
        FROM public.tenant_user_lookup l
        JOIN public.tenants t ON t.slug = l.tenant_slug
        WHERE LOWER(l.email) = %s
          AND t.status = 'active'
        LIMIT 1
        """,
        (email,),
    )
    return dict(row) if row else None


def _fetch_admin_in_schema(schema: str, email: str) -> dict | None:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            psql.SQL(
                """
                SELECT id, username
                FROM {}.users
                WHERE LOWER(username) = %s AND COALESCE(is_active, TRUE)
                LIMIT 1
                """
            ).format(psql.Identifier(schema)),
            (email,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _forgot_password_apex(identifier: str):
    """Marketing/apex: e-posta → kiracı şemasına token + kiracı reset linki."""
    if "@" not in identifier:
        flash(EMAIL_REQUIRED_MSG, "danger")
        return render_template(
            "forgot_password.html",
            apex_mode=True,
            apex_hint=APEX_HINT_MSG,
        )

    hit = _lookup_tenant_by_email(identifier)
    if not hit:
        flash(NEUTRAL_MSG, "info")
        return render_template(
            "forgot_password.html",
            apex_mode=True,
            apex_hint=APEX_HINT_MSG,
        )

    slug = str(hit.get("slug") or "").strip()
    schema = str(hit.get("schema_name") or "").strip() or (
        schema_name_for_slug(slug) or ""
    )
    if not schema or (
        schema != "public"
        and not schema.startswith("tenant_")
    ):
        logger.error("apex forgot bad schema slug=%s schema=%s", slug, schema)
        flash(SMTP_FAIL_MSG, "danger")
        return render_template(
            "forgot_password.html",
            apex_mode=True,
            apex_hint=APEX_HINT_MSG,
        )

    user = _fetch_admin_in_schema(schema, identifier)
    if not user:
        flash(NEUTRAL_MSG, "info")
        return render_template(
            "forgot_password.html",
            apex_mode=True,
            apex_hint=APEX_HINT_MSG,
        )

    result = _send_reset_email_result(
        username=str(user.get("username") or identifier),
        user_id=int(user["id"]),
        reset_host=_tenant_reset_host(slug),
        schema=schema,
    )
    _flash_send_result(result)
    return render_template(
        "forgot_password.html",
        apex_mode=True,
        apex_hint=APEX_HINT_MSG,
    )


def _forgot_password_tenant(identifier: str):
    """Kiracı host: search_path users + ensure DDL + SMTP sonucu."""
    ensure_password_reset_tokens_table()

    user = fetch_one(
        """
        SELECT id, username
        FROM users
        WHERE LOWER(username) = %s AND is_active = TRUE
        LIMIT 1
        """,
        (identifier,),
    )
    if not user:
        flash(NEUTRAL_MSG, "info")
        return render_template("forgot_password.html", apex_mode=False)

    host = (request.host or request.headers.get("Host") or "").split(":")[0]
    result = _send_reset_email_result(
        username=str(user.get("username") or ""),
        user_id=int(user["id"]),
        reset_host=host,
        schema=None,
    )
    _flash_send_result(result)
    return render_template("forgot_password.html", apex_mode=False)


def _fetch_token_row(token_hash: str) -> dict | None:
    return fetch_one(
        """
        SELECT t.id, t.user_id, t.expires_at, t.used_at, u.username, u.is_active
        FROM password_reset_tokens t
        JOIN users u ON u.id = t.user_id
        WHERE t.token_hash = %s
        """,
        (token_hash,),
    )


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


def _password_validation_error(password: str, confirm: str) -> str | None:
    reason = validate_password_strength(password)
    if reason:
        return _PASSWORD_STRENGTH_MSG.get(reason, "Şifre geçersiz.")
    if validate_password_confirm(password, confirm):
        return "Yeni şifre ve onayı eşleşmiyor."
    return None


def _apply_password_reset(raw_token: str, password: str) -> None:
    """Tek transaction: token kilidi + şifre/stamp güncelle + used_at."""
    token_hash = _token_hash(raw_token)
    hashed = generate_password_hash(password)
    new_stamp = generate_security_stamp()
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT t.id, t.user_id, t.expires_at, t.used_at, u.username, u.is_active
            FROM password_reset_tokens t
            JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = %s
            FOR UPDATE OF t
            """,
            (token_hash,),
        )
        row = cur.fetchone()
        if not row:
            raise PasswordResetTransactionError("token_not_found")
        locked_row = dict(row)
        if not _token_row_is_valid(locked_row):
            raise PasswordResetTransactionError("token_invalid")
        user_id = int(locked_row["user_id"])
        token_id = int(locked_row["id"])
        cur.execute(
            """
            UPDATE users
            SET password_hash = %s, security_stamp = %s
            WHERE id = %s AND is_active = TRUE
            """,
            (hashed, new_stamp, user_id),
        )
        if cur.rowcount != 1:
            raise PasswordResetTransactionError("user_update_failed")
        cur.execute(
            """
            UPDATE password_reset_tokens
            SET used_at = NOW()
            WHERE id = %s AND used_at IS NULL
            """,
            (token_id,),
        )
        if cur.rowcount != 1:
            raise PasswordResetTransactionError("token_mark_failed")


def _raw_token_from_request() -> str:
    return str(request.args.get("token") or request.form.get("token") or "").strip()


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    host = request.host or request.headers.get("Host") or ""
    marketing = is_payafin_marketing_host(host)
    # Apex/marketing: form göster (artık sessiz index redirect yok)
    apex_mode = bool(marketing)

    if request.method == "GET":
        return render_template(
            "forgot_password.html",
            apex_mode=apex_mode,
            apex_hint=APEX_HINT_MSG if apex_mode else None,
        )

    # POST
    allowed_ip, retry_ip = check_forgot_password_ip_rate()
    if not allowed_ip:
        flash(RATE_LIMIT_MSG, "danger")
        return (
            render_template(
                "forgot_password.html",
                apex_mode=apex_mode,
                apex_hint=APEX_HINT_MSG if apex_mode else None,
            ),
            429,
            {"Retry-After": str(retry_ip or 3600)},
        )

    identifier = (request.form.get("identifier") or "").strip().lower()
    allowed_id, retry_id = check_forgot_password_identifier_rate(identifier)
    if not allowed_id:
        flash(RATE_LIMIT_MSG, "danger")
        return (
            render_template(
                "forgot_password.html",
                apex_mode=apex_mode,
                apex_hint=APEX_HINT_MSG if apex_mode else None,
            ),
            429,
            {"Retry-After": str(retry_id or 3600)},
        )

    if apex_mode:
        return _forgot_password_apex(identifier)

    # Tenant host (g.tenant_schema set)
    if getattr(g, "tenant_schema", None):
        return _forgot_password_tenant(identifier)

    # Public non-marketing (ör. Ofisbir public users) — search_path public
    return _forgot_password_tenant(identifier)


@bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    host = request.host or request.headers.get("Host") or ""
    if is_payafin_marketing_host(host):
        # Reset link kiracı host'ta olmalı — net mesaj
        flash(
            "Bu bağlantı kiracı (firma) adresinizde açılmalıdır. "
            "E-postadaki bağlantıyı kullanın veya giriş sayfasından devam edin.",
            "danger",
        )
        return redirect(url_for("index"))

    if getattr(g, "tenant_schema", None):
        ensure_password_reset_tokens_table()

    raw_token = _raw_token_from_request()

    if request.method == "GET":
        if not raw_token:
            return (
                render_template(
                    "reset_password_error.html",
                    message=MISSING_TOKEN_MSG,
                ),
                400,
            )
        row = _fetch_token_row(_token_hash(raw_token))
        if not _token_row_is_valid(row):
            return render_template(
                "reset_password_invalid.html",
                message=INVALID_TOKEN_MSG,
            )
        return render_template(
            "reset_password.html",
            token=raw_token,
            username=row["username"],
        )

    # POST
    if not raw_token:
        return (
            render_template(
                "reset_password_error.html",
                message=MISSING_TOKEN_MSG,
            ),
            400,
        )

    password = request.form.get("password") or ""
    confirm = request.form.get("password_confirm") or ""
    pw_err = _password_validation_error(password, confirm)
    if pw_err:
        row = _fetch_token_row(_token_hash(raw_token))
        if not _token_row_is_valid(row):
            return render_template(
                "reset_password_invalid.html",
                message=INVALID_TOKEN_MSG,
            )
        flash(pw_err, "danger")
        return render_template(
            "reset_password.html",
            token=raw_token,
            username=row["username"],
        )

    row = _fetch_token_row(_token_hash(raw_token))
    if not _token_row_is_valid(row):
        return render_template(
            "reset_password_invalid.html",
            message=INVALID_TOKEN_MSG,
        )

    try:
        _apply_password_reset(raw_token, password)
    except PasswordResetTransactionError:
        return render_template(
            "reset_password_invalid.html",
            message=INVALID_TOKEN_MSG,
        )

    flash(SUCCESS_MSG, "success")
    return redirect(url_for("auth.login"))
