# -*- coding: utf-8 -*-
"""Facebook ile giriş / kayıt. Google ile aynı karar: giriş, Satın Al veya trial."""
from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, redirect, render_template, request

from facebook_oauth import (
    FacebookEmailRequired,
    authorization_url,
    facebook_configured,
    fetch_facebook_profile,
    mint_email_prompt,
    mint_oauth_state,
    read_email_prompt,
    read_oauth_state,
)
from routes.google_auth_routes import finish_social_login
from signup_validation import normalize_slug_input, validate_email
from tenant_identity import resolve_tenant_slug

logger = logging.getLogger(__name__)

bp = Blueprint("facebook_auth", __name__)

_INTENTS = frozenset({"login", "signup", "purchase"})


def _page(message: str, status: int = 200):
    return (
        render_template(
            "auth/google_message.html",
            title="Facebook ile giriş",
            message=message,
        ),
        status,
    )


def _apex_only(f):
    @wraps(f)
    def _guard(*args, **kwargs):
        if resolve_tenant_slug():
            return _page("Facebook girişi yalnızca payafin.com üzerinden yapılır.", 403)
        return f(*args, **kwargs)

    return _guard


def _clean_intent(raw) -> str:
    s = str(raw or "").strip().lower()
    return s if s in _INTENTS else "login"


def _state_from_request() -> dict:
    return {
        "intent": _clean_intent(request.args.get("intent") or request.form.get("intent")),
        "module": str(request.args.get("module") or request.form.get("module") or "").strip().lower()[:32],
        "tier": str(request.args.get("tier") or request.form.get("tier") or "").strip().lower()[:32],
        "mode": str(request.args.get("mode") or request.form.get("mode") or "").strip().lower()[:32],
        "slug": normalize_slug_input(request.args.get("slug") or request.form.get("slug"))[:32],
        "company_name": str(
            request.args.get("company_name") or request.form.get("company_name") or ""
        ).strip()[:200],
    }


@bp.route("/auth/facebook", methods=["GET"])
@_apex_only
def facebook_start():
    if not facebook_configured():
        return _page(
            "Facebook ile giriş henüz yapılandırılmadı. "
            "FACEBOOK_APP_ID ve FACEBOOK_APP_SECRET eklendikten sonra kullanılabilir.",
            503,
        )
    return redirect(authorization_url(mint_oauth_state(_state_from_request())))


@bp.route("/auth/facebook/callback", methods=["GET"])
@_apex_only
def facebook_callback():
    if not facebook_configured():
        return _page("Facebook ile giriş henüz yapılandırılmadı.", 503)
    err = str(request.args.get("error") or request.args.get("error_reason") or "").strip()
    if err:
        logger.info("facebook callback error=%s", err[:80])
        return _page("Facebook girişi iptal edildi veya tamamlanamadı.", 400)
    state = read_oauth_state(request.args.get("state"))
    if not state:
        return _page("Oturum doğrulanamadı. Facebook ile devam et düğmesine yeniden basın.", 400)
    try:
        profile = fetch_facebook_profile(request.args.get("code") or "")
    except FacebookEmailRequired as exc:
        ticket = mint_email_prompt(name=exc.name, state=state)
        return render_template("auth/facebook_email.html", ticket=ticket, name=exc.name)
    except RuntimeError as exc:
        logger.info("facebook profile failed reason=%s", exc)
        return _page("Facebook hesabı doğrulanamadı. Lütfen tekrar deneyin.", 400)
    return finish_social_login(profile["email"], profile["name"], state, email_trusted=True)


@bp.route("/auth/facebook/email", methods=["POST"])
@_apex_only
def facebook_email():
    prompt = read_email_prompt(request.form.get("ticket"))
    if not prompt:
        return _page("E-posta adımının süresi doldu. Facebook ile tekrar deneyin.", 400)
    email = str(request.form.get("email") or "").strip().lower()
    if validate_email(email):
        return render_template(
            "auth/facebook_email.html",
            ticket=request.form.get("ticket") or "",
            name=prompt["name"],
            error="Geçerli bir e-posta girin.",
        )
    return finish_social_login(
        email,
        prompt["name"],
        prompt["state"],
        email_trusted=False,
    )
