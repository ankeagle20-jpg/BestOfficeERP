# -*- coding: utf-8 -*-
"""Payafin ana sayfa — e-posta → kiracı yönlendirme API (apex-only, auth yok)."""
from __future__ import annotations

import logging
from functools import wraps
from urllib.parse import urlencode

from flask import Blueprint, jsonify, request

from db import fetch_one
from login_lookup_rate_limit import check_login_lookup_rate
from signup_validation import validate_email
from tenant_identity import _tenant_apex_domains, resolve_tenant_slug

logger = logging.getLogger(__name__)

bp = Blueprint("login_lookup", __name__)

MSG_NOT_FOUND = "Bu e-posta ile kayıtlı bir hesap bulunamadı."
MSG_TENANT_HOST = "Bu endpoint yalnızca ana (public) host üzerinden kullanılabilir."

# L0: istemci yalnız module key gönderir; kanonik next sunucuda üretilir (ham path yok).
# core / bilinmeyen / boş → next yok (eski /login davranışı).
_MODULE_NEXT: dict[str, str] = {
    "ledger": "/ledger/",
    "randevu": "/randevu/m/",
    "personnel": "/personel/m/",
}


def _json403(msg: str):
    return jsonify({"ok": False, "mesaj": msg}), 403


def marketing_public_only(f):
    """Kiracı subdomain'inde login-lookup API kapalı (auth yok)."""

    @wraps(f)
    def _guard(*args, **kwargs):
        if resolve_tenant_slug():
            path = request.path or ""
            if "/api/" in path or request.is_json or (
                request.accept_mimetypes.best == "application/json"
            ):
                return _json403(MSG_TENANT_HOST)
            return MSG_TENANT_HOST, 403
        return f(*args, **kwargs)

    return _guard


def _canonical_next_for_module(module_raw) -> str | None:
    """Whitelist module → relative next path. Geçersiz/core/boş → None (next yok)."""
    key = str(module_raw or "").strip().lower()
    if not key or key == "core":
        return None
    return _MODULE_NEXT.get(key)


def _login_url(slug: str, next_path: str | None = None) -> str:
    apex = (_tenant_apex_domains() or ("payafin.com",))[0]
    base = f"https://{slug}.{apex}/login"
    if not next_path:
        return base
    # S3 ile uyumlu: yalnız relative path; netloc / // kabul etme
    if (
        not next_path.startswith("/")
        or next_path.startswith("//")
        or "://" in next_path
    ):
        return base
    return f"{base}?{urlencode({'next': next_path})}"


@bp.route("/api/login-lookup", methods=["POST"])
@marketing_public_only
def api_login_lookup():
    allowed, retry_after = check_login_lookup_rate()
    if not allowed:
        return (
            jsonify({"ok": False, "mesaj": "Çok fazla deneme, lütfen bekleyin."}),
            429,
            {"Retry-After": str(retry_after)},
        )

    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    if validate_email(email):
        return jsonify({"ok": False, "mesaj": "Geçersiz e-posta."}), 400

    # Ham "next" / path alanlarını bilerek yok say — yalnız whitelist module
    next_path = _canonical_next_for_module(data.get("module"))

    row = fetch_one(
        """
        SELECT l.tenant_slug
        FROM public.tenant_user_lookup l
        INNER JOIN public.tenants t
            ON t.slug = l.tenant_slug AND t.status = 'active'
        WHERE l.email = %s
        LIMIT 1
        """,
        (email,),
    )

    if row:
        slug = row["tenant_slug"]
        payload = {
            "ok": True,
            "found": True,
            "tenant_slug": slug,
            "login_url": _login_url(slug, next_path),
        }
        if next_path:
            payload["next"] = next_path
        return jsonify(payload)

    return jsonify(
        {
            "ok": True,
            "found": False,
            "mesaj": MSG_NOT_FOUND,
            "signup_url": "/signup",
        }
    )
