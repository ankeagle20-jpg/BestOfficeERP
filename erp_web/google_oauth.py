# -*- coding: utf-8 -*-
"""Google OAuth 2.0 (authorization code). Kimlik bilgisi yoksa akış kapalı kalır."""
from __future__ import annotations

import logging
import os
import secrets
from urllib.parse import urlencode

import httpx
from flask import current_app, session
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
_SCOPES = "openid email profile"
_STATE_SALT = "payafin-google-oauth-state-v1"
_TICKET_SALT = "payafin-google-oauth-ticket-v1"
_STATE_MAX_AGE = 600
_TICKET_MAX_AGE = 1200


def google_configured() -> bool:
    cid = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    secret = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
    return bool(cid and secret)


def google_redirect_uri() -> str:
    """Google Console Authorized redirect URIs ile birebir aynı adres."""
    return "https://payafin.com/auth/google/callback"


def _serializer(salt: str) -> URLSafeTimedSerializer:
    secret = current_app.config.get("SECRET_KEY") or "degistir-bunu-uretimde"
    return URLSafeTimedSerializer(str(secret), salt=salt)


def mint_oauth_state(payload: dict) -> str:
    nonce = secrets.token_urlsafe(18)
    session["google_oauth_nonce"] = nonce
    body = dict(payload or {})
    body["n"] = nonce
    return _serializer(_STATE_SALT).dumps(body)


def read_oauth_state(raw: str) -> dict | None:
    token = str(raw or "").strip()
    if not token:
        return None
    try:
        data = _serializer(_STATE_SALT).loads(token, max_age=_STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict):
        return None
    nonce = str(data.get("n") or "")
    expected = str(session.get("google_oauth_nonce") or "")
    session.pop("google_oauth_nonce", None)
    if not nonce or nonce != expected:
        return None
    return data


def mint_provision_ticket(*, email: str, slug: str) -> str:
    return _serializer(_TICKET_SALT).dumps(
        {"email": str(email or "").strip().lower(), "slug": str(slug or "").strip().lower()}
    )


def read_provision_ticket(raw: str) -> dict | None:
    token = str(raw or "").strip()
    if not token:
        return None
    try:
        data = _serializer(_TICKET_SALT).loads(token, max_age=_TICKET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict):
        return None
    email = str(data.get("email") or "").strip().lower()
    slug = str(data.get("slug") or "").strip().lower()
    if not email or not slug:
        return None
    return {"email": email, "slug": slug}


def authorization_url(state: str) -> str:
    cid = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    query = urlencode(
        {
            "client_id": cid,
            "redirect_uri": google_redirect_uri(),
            "response_type": "code",
            "scope": _SCOPES,
            "state": state,
            "prompt": "select_account",
            "include_granted_scopes": "true",
        }
    )
    return f"{_AUTH_URL}?{query}"


def fetch_google_profile(code: str) -> dict:
    """Kodu e-posta + ada çevirir. Token ve secret loglanmaz."""
    cid = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    secret = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
    if not cid or not secret:
        raise RuntimeError("google_not_configured")
    with httpx.Client(timeout=15.0) as client:
        token_resp = client.post(
            _TOKEN_URL,
            data={
                "code": str(code or "").strip(),
                "client_id": cid,
                "client_secret": secret,
                "redirect_uri": google_redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            logger.info("google token exchange failed status=%s", token_resp.status_code)
            raise RuntimeError("google_token_failed")
        access = str((token_resp.json() or {}).get("access_token") or "").strip()
        if not access:
            raise RuntimeError("google_token_failed")
        info = client.get(_USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
        if info.status_code != 200:
            logger.info("google userinfo failed status=%s", info.status_code)
            raise RuntimeError("google_userinfo_failed")
        body = info.json() or {}
    email = str(body.get("email") or "").strip().lower()
    verified = body.get("email_verified")
    if verified is False or str(verified).strip().lower() in ("false", "0"):
        raise RuntimeError("google_email_unverified")
    if not email or "@" not in email:
        raise RuntimeError("google_email_missing")
    name = str(body.get("name") or body.get("given_name") or "").strip()
    return {"email": email, "name": name[:120]}
