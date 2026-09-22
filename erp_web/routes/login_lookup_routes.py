# -*- coding: utf-8 -*-
"""Payafin ana sayfa — e-posta/telefon → kiracı yönlendirme API (apex-only, auth yok).

Aşama 0-1: POST /api/login-complete — identifier+şifre → handoff bileti (UI yok).
"""
from __future__ import annotations

import logging
from functools import wraps
from urllib.parse import urlencode

from flask import Blueprint, jsonify, request

from auth import dogrula_giris_kimlik
from db import ensure_tenant_user_lookup_phone_column, fetch_one
from login_handoff import mint_login_handoff_token
from login_lockout import LoginLockedOut
from login_lookup_rate_limit import (
    check_login_complete_rate,
    check_login_lookup_rate,
)
from signup_validation import normalize_phone_e164, validate_email
from tenant_identity import (
    _tenant_apex_domains,
    resolve_tenant_slug,
    schema_name_for_slug,
)

logger = logging.getLogger(__name__)

bp = Blueprint("login_lookup", __name__)

MSG_NOT_FOUND = "Bu bilgilerle kayıtlı bir hesap bulunamadı."
MSG_INVALID = "Geçerli bir e-posta veya cep telefonu girin."
MSG_TENANT_HOST = "Bu endpoint yalnızca ana (public) host üzerinden kullanılabilir."
MSG_LOGIN_FAIL = "E-posta / telefon veya şifre hatalı."
MSG_PASSWORD_REQUIRED = "Şifre gerekli."

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


def _handoff_url(slug: str, token: str) -> str:
    apex = (_tenant_apex_domains() or ("payafin.com",))[0]
    return f"https://{slug}.{apex}/login/handoff?{urlencode({'token': token})}"


def _parse_lookup_identifier(data: dict) -> tuple[str | None, str | None]:
    """Döner: (mode, value) — mode 'email'|'phone'; geçersizse (None, None)."""
    raw = str(
        data.get("identifier")
        or data.get("email")
        or data.get("phone")
        or ""
    ).strip()
    if not raw:
        return None, None
    if "@" in raw:
        email = raw.lower()
        if validate_email(email):
            return None, None
        return "email", email
    try:
        e164 = normalize_phone_e164(raw)
    except ValueError:
        return None, None
    if not e164:
        return None, None
    return "phone", e164


def _lookup_active_tenant_slug(mode: str, ident: str) -> str | None:
    if mode == "phone":
        ensure_tenant_user_lookup_phone_column()
        row = fetch_one(
            """
            SELECT l.tenant_slug
            FROM public.tenant_user_lookup l
            INNER JOIN public.tenants t
                ON t.slug = l.tenant_slug AND t.status = 'active'
            WHERE l.phone = %s
            LIMIT 1
            """,
            (ident,),
        )
    else:
        row = fetch_one(
            """
            SELECT l.tenant_slug
            FROM public.tenant_user_lookup l
            INNER JOIN public.tenants t
                ON t.slug = l.tenant_slug AND t.status = 'active'
            WHERE l.email = %s
            LIMIT 1
            """,
            (ident,),
        )
    if not row:
        return None
    return str(row.get("tenant_slug") or "").strip() or None


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
    mode, ident = _parse_lookup_identifier(data)
    if not mode or not ident:
        return jsonify({"ok": False, "mesaj": MSG_INVALID}), 400

    # Ham "next" / path alanlarını bilerek yok say — yalnız whitelist module
    next_path = _canonical_next_for_module(data.get("module"))

    slug = _lookup_active_tenant_slug(mode, ident)
    if slug:
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


@bp.route("/api/login-complete", methods=["POST"])
@marketing_public_only
def api_login_complete():
    """Apex: identifier+şifre → kiracı şemasında doğrula → handoff redirect_url.

    login_user çağrılmaz; oturum yalnız kiracı /login/handoff'ta açılır.
    """
    allowed, retry_after = check_login_complete_rate()
    if not allowed:
        return (
            jsonify({"ok": False, "mesaj": "Çok fazla deneme, lütfen bekleyin."}),
            429,
            {"Retry-After": str(retry_after)},
        )

    data = request.get_json(silent=True) or {}
    mode, ident = _parse_lookup_identifier(data)
    password = data.get("password")
    if password is None:
        password = ""
    password = str(password)

    if not mode or not ident:
        return jsonify({"ok": False, "mesaj": MSG_INVALID}), 400
    if not password:
        return jsonify({"ok": False, "mesaj": MSG_PASSWORD_REQUIRED}), 400

    next_path = _canonical_next_for_module(data.get("module"))

    slug = _lookup_active_tenant_slug(mode, ident)
    if not slug:
        return jsonify({"ok": False, "mesaj": MSG_LOGIN_FAIL}), 401

    schema = schema_name_for_slug(slug)
    if not schema:
        logger.error("login-complete bad schema slug=%s", slug)
        return jsonify({"ok": False, "mesaj": MSG_LOGIN_FAIL}), 401

    try:
        row = dogrula_giris_kimlik(ident, password, schema=schema)
    except LoginLockedOut as exc:
        return (
            jsonify({"ok": False, "mesaj": exc.message}),
            429,
            {"Retry-After": str(getattr(exc, "retry_after", None) or 3600)},
        )

    if not row:
        return jsonify({"ok": False, "mesaj": MSG_LOGIN_FAIL}), 401

    try:
        token = mint_login_handoff_token(
            user_id=int(row["id"]),
            tenant_slug=slug,
            security_stamp=row.get("security_stamp"),
            next_path=next_path,
        )
    except Exception:
        logger.exception("login-complete mint failed slug=%s", slug)
        return jsonify({"ok": False, "mesaj": MSG_LOGIN_FAIL}), 500

    return jsonify(
        {
            "ok": True,
            "redirect_url": _handoff_url(slug, token),
            "tenant_slug": slug,
        }
    )
