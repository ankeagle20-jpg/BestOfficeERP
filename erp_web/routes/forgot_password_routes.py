# -*- coding: utf-8 -*-
"""Payafin — GET/POST /forgot-password (şifre sıfırlama talebi)."""
from __future__ import annotations

import hashlib
import logging
import secrets

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from db import execute, fetch_one
from mail_utils import send_password_reset_email
from password_reset_rate_limit import (
    check_forgot_password_identifier_rate,
    check_forgot_password_ip_rate,
    client_ip,
)

logger = logging.getLogger(__name__)

bp = Blueprint("forgot_password", __name__)

NEUTRAL_MSG = (
    "Eğer bu bilgilerle kayıtlı bir hesap varsa, "
    "şifre sıfırlama bağlantısı e-posta adresinize gönderildi."
)
RATE_LIMIT_MSG = "Çok fazla deneme, lütfen bekleyin."


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


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    from tenant_identity import is_payafin_marketing_host

    if is_payafin_marketing_host(request.host or request.headers.get("Host")):
        return redirect(url_for("index"))

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
