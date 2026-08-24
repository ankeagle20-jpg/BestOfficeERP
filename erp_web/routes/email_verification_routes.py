# -*- coding: utf-8 -*-
"""Payafin — GET /verify-email (herkese açık, giriş engellemez)."""
from __future__ import annotations

from flask import Blueprint, render_template, request, url_for

from email_verification import EmailVerificationError, apply_email_verification
from tenant_identity import is_payafin_marketing_host

bp = Blueprint("email_verification", __name__)

INVALID_MSG = "Bağlantı geçersiz veya süresi dolmuş."
MISSING_MSG = "Geçersiz istek. Doğrulama bağlantısı eksik."


def _reject_marketing_host():
    if is_payafin_marketing_host(request.host or request.headers.get("Host")):
        return render_template(
            "marketing/verify_email_invalid.html",
            message="Doğrulama bağlantısı kiracı adresinizde açılmalıdır.",
            login_url="/",
        ), 400
    return None


@bp.route("/verify-email", methods=["GET"])
def verify_email():
    blocked = _reject_marketing_host()
    if blocked:
        return blocked

    raw_token = (request.args.get("token") or "").strip()
    if not raw_token:
        return (
            render_template(
                "marketing/verify_email_invalid.html",
                message=MISSING_MSG,
                login_url=url_for("auth.login"),
            ),
            400,
        )

    try:
        apply_email_verification(raw_token)
    except EmailVerificationError:
        return (
            render_template(
                "marketing/verify_email_invalid.html",
                message=INVALID_MSG,
                login_url=url_for("auth.login"),
            ),
            400,
        )

    return render_template(
        "marketing/verify_email_success.html",
        login_url=url_for("auth.login"),
    )
