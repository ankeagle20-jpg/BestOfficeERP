# -*- coding: utf-8 -*-
"""Payafin — /forgot-password (talep) ve /reset-password (yeni şifre)."""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from auth import generate_security_stamp
from db import db, execute, fetch_one
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

logger = logging.getLogger(__name__)

bp = Blueprint("forgot_password", __name__)

NEUTRAL_MSG = (
    "Eğer bu bilgilerle kayıtlı bir hesap varsa, "
    "şifre sıfırlama bağlantısı e-posta adresinize gönderildi."
)
RATE_LIMIT_MSG = "Çok fazla deneme, lütfen bekleyin."
INVALID_TOKEN_MSG = "Bağlantı geçersiz veya süresi dolmuş."
MISSING_TOKEN_MSG = "Geçersiz istek. Şifre sıfırlama bağlantısı eksik."
SUCCESS_MSG = "Şifreniz başarıyla güncellendi. Yeni şifrenizle giriş yapabilirsiniz."

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


def _maybe_send_reset_email(user: dict) -> None:
    username = str(user.get("username") or "")
    if "@" not in username:
        return
    raw_token = _issue_reset_token(int(user["id"]), client_ip())
    reset_url = f"https://{request.host}/reset-password?token={raw_token}"
    if not send_password_reset_email(username, reset_url):
        logger.warning(
            "password reset mail failed user_id=%s host=%s",
            user.get("id"),
            request.host,
        )


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


def _reject_marketing_host():
    from tenant_identity import is_payafin_marketing_host

    if is_payafin_marketing_host(request.host or request.headers.get("Host")):
        return redirect(url_for("index"))
    return None


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    blocked = _reject_marketing_host()
    if blocked:
        return blocked

    if request.method == "POST":
        allowed_ip, retry_ip = check_forgot_password_ip_rate()
        if not allowed_ip:
            flash(RATE_LIMIT_MSG, "danger")
            return (
                render_template("forgot_password.html"),
                429,
                {"Retry-After": str(retry_ip or 3600)},
            )

        identifier = (request.form.get("identifier") or "").strip().lower()
        allowed_id, retry_id = check_forgot_password_identifier_rate(identifier)
        if not allowed_id:
            flash(RATE_LIMIT_MSG, "danger")
            return (
                render_template("forgot_password.html"),
                429,
                {"Retry-After": str(retry_id or 3600)},
            )

        user = fetch_one(
            """
            SELECT id, username
            FROM users
            WHERE LOWER(username) = %s AND is_active = TRUE
            LIMIT 1
            """,
            (identifier,),
        )
        if user:
            _invalidate_unused_tokens(int(user["id"]))
            _maybe_send_reset_email(user)

        flash(NEUTRAL_MSG, "info")
        return render_template("forgot_password.html")

    return render_template("forgot_password.html")


@bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    blocked = _reject_marketing_host()
    if blocked:
        return blocked

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
