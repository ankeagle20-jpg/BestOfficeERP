# -*- coding: utf-8 -*-
"""Facebook Login (authorization code). Kimlik bilgisi yoksa akış kapalı kalır."""
from __future__ import annotations

import logging
import os
import secrets
from urllib.parse import urlencode

import httpx
from flask import current_app, session
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

_GRAPH = "v22.0"
_AUTH_URL = f"https://www.facebook.com/{_GRAPH}/dialog/oauth"
_TOKEN_URL = f"https://graph.facebook.com/{_GRAPH}/oauth/access_token"
_ME_URL = f"https://graph.facebook.com/{_GRAPH}/me"
_SCOPES = "email,public_profile"
_STATE_SALT = "payafin-facebook-oauth-state-v1"
_EMAIL_SALT = "payafin-facebook-email-v1"
_STATE_MAX_AGE = 600
_EMAIL_MAX_AGE = 900


class FacebookEmailRequired(Exception):
    """Facebook e-posta izni vermedi; ad ve state duruyor."""

    def __init__(self, name: str):
        super().__init__("facebook_email_missing")
        self.name = name


def _env_value(name: str) -> str:
    raw = (os.environ.get(name) or "").strip().lstrip("\ufeff")
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        raw = raw[1:-1].strip()
    return raw


def facebook_configured() -> bool:
    return bool(_env_value("FACEBOOK_APP_ID") and _env_value("FACEBOOK_APP_SECRET"))


def facebook_redirect_uri() -> str:
    """Facebook Valid OAuth Redirect URIs ile birebir aynı adres."""
    return "https://payafin.com/auth/facebook/callback"


def _serializer(salt: str) -> URLSafeTimedSerializer:
    secret = current_app.config.get("SECRET_KEY") or "degistir-bunu-uretimde"
    return URLSafeTimedSerializer(str(secret), salt=salt)


def mint_oauth_state(payload: dict) -> str:
    nonce = secrets.token_urlsafe(18)
    session["facebook_oauth_nonce"] = nonce
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
    expected = str(session.get("facebook_oauth_nonce") or "")
    session.pop("facebook_oauth_nonce", None)
    if not nonce or nonce != expected:
        return None
    return data


def mint_email_prompt(*, name: str, state: dict) -> str:
    return _serializer(_EMAIL_SALT).dumps(
        {"name": str(name or "").strip()[:120], "state": dict(state or {})}
    )


def read_email_prompt(raw: str) -> dict | None:
    token = str(raw or "").strip()
    if not token:
        return None
    try:
        data = _serializer(_EMAIL_SALT).loads(token, max_age=_EMAIL_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict):
        return None
    state = data.get("state")
    if not isinstance(state, dict):
        return None
    return {"name": str(data.get("name") or "").strip()[:120], "state": state}


def _graph_failure(resp: httpx.Response) -> str:
    """Facebook hata kodu. Secret, kod ve token yazılmaz."""
    code = sub = None
    typ = msg = ""
    try:
        err = (resp.json() or {}).get("error") or {}
    except Exception:
        err = {}
    if isinstance(err, dict):
        code = err.get("code")
        sub = err.get("error_subcode")
        typ = str(err.get("type") or "")[:40]
        msg = str(err.get("message") or "")[:180]
    secret = _env_value("FACEBOOK_APP_SECRET")
    if secret and secret in msg:
        msg = msg.replace(secret, "[secret]")
    return f"status={resp.status_code} type={typ} code={code} sub={sub} msg={msg}"


def facebook_failure_message(detail: str) -> str:
    low = str(detail or "").lower()
    if "client secret" in low:
        return (
            "Facebook uygulama parolası kabul edilmedi. "
            "Render'daki FACEBOOK_APP_SECRET, Developers panelindeki App Secret olmalı; "
            "Client Token yapıştırılmamalı."
        )
    if "redirect_uri" in low or "verification code" in low or "sub=36008" in low:
        return (
            "Facebook yönlendirme adresi uyuşmuyor. "
            "Valid OAuth Redirect URIs alanına tam olarak "
            "https://payafin.com/auth/facebook/callback yazın; sonda eğik çizgi olmasın."
        )
    if any(
        part in low
        for part in (
            "app not active",
            "not available",
            "not authorized",
            "application does not have permission",
        )
    ):
        return (
            "Bu Facebook uygulaması Development modunda olabilir. "
            "Yalnızca uygulamaya ekli yönetici, geliştirici veya test kullanıcısı girebilir."
        )
    return "Facebook hesabı doğrulanamadı. Lütfen tekrar deneyin."


def authorization_url(state: str) -> str:
    app_id = _env_value("FACEBOOK_APP_ID")
    query = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": facebook_redirect_uri(),
            "state": state,
            "scope": _SCOPES,
            "response_type": "code",
        }
    )
    return f"{_AUTH_URL}?{query}"


def fetch_facebook_profile(code: str) -> dict:
    """Kodu ada ve varsa e-postaya çevirir. Token ve secret loglanmaz."""
    app_id = _env_value("FACEBOOK_APP_ID")
    secret = _env_value("FACEBOOK_APP_SECRET")
    if not app_id or not secret:
        raise RuntimeError("facebook_not_configured")
    with httpx.Client(timeout=15.0) as client:
        token_resp = client.get(
            _TOKEN_URL,
            params={
                "client_id": app_id,
                "client_secret": secret,
                "redirect_uri": facebook_redirect_uri(),
                "code": str(code or "").strip(),
            },
        )
        if token_resp.status_code != 200:
            detail = _graph_failure(token_resp)
            logger.warning("facebook token exchange failed %s", detail)
            raise RuntimeError(f"facebook_token_failed {detail}")
        try:
            token_body = token_resp.json() or {}
        except Exception:
            logger.warning("facebook token exchange failed status=200 body=not_json")
            raise RuntimeError("facebook_token_failed status=200 body=not_json")
        access = str(token_body.get("access_token") or "").strip()
        if not access:
            detail = _graph_failure(token_resp)
            logger.warning("facebook token exchange empty %s", detail)
            raise RuntimeError(f"facebook_token_failed {detail}")
        info = client.get(
            _ME_URL,
            params={"fields": "id,name,email", "access_token": access},
        )
        if info.status_code != 200:
            detail = _graph_failure(info)
            logger.warning("facebook profile failed %s", detail)
            raise RuntimeError(f"facebook_profile_failed {detail}")
        body = info.json() or {}
    name = str(body.get("name") or "").strip()[:120]
    email = str(body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise FacebookEmailRequired(name)
    return {"email": email, "name": name}
