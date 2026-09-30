# -*- coding: utf-8 -*-
"""Google ile giriş / kayıt. Apex host. Kimlik yoksa consent ekranına çıkılmaz."""
from __future__ import annotations

import logging
import secrets
import threading
from functools import wraps
from urllib.parse import urlencode

from flask import Blueprint, current_app, redirect, render_template, request
from flask_login import login_user
from werkzeug.security import generate_password_hash

from auth import User, _fetch_login_user_row
from db import fetch_one
from google_oauth import (
    authorization_url,
    fetch_google_profile,
    google_configured,
    mint_oauth_state,
    mint_provision_ticket,
    read_oauth_state,
    read_provision_ticket,
)
from login_handoff import mint_login_handoff_token
from routes.login_lookup_routes import (
    _canonical_next_for_module,
    _handoff_url,
    _lookup_active_tenant_slug,
)
from routes.signup_routes import (
    PurchasePricingError,
    _compute_purchase_bill,
    _create_purchase_invoice,
    _insert_purchase_signup_intent,
    _provision_worker,
    _release_pending_payment_tenant,
    _resolve_purchase_module_and_tier,
    _slug_is_taken,
)
from signup_validation import normalize_slug_input, validate_company_name, validate_slug_format
from tenant_identity import resolve_tenant_slug, schema_name_for_slug, stamp_session_tenant_slug
from tenant_provisioning import (
    TenantSlugConflictError,
    TenantSlugReserveError,
    reserve_tenant_slug,
)

logger = logging.getLogger(__name__)

bp = Blueprint("google_auth", __name__)

_INTENTS = frozenset({"login", "signup", "purchase"})


def _apex_only(f):
    @wraps(f)
    def _guard(*args, **kwargs):
        if resolve_tenant_slug():
            return _page("Google girişi yalnızca payafin.com üzerinden yapılır.", 403)
        return f(*args, **kwargs)

    return _guard


def _page(message: str, status: int = 200):
    return render_template("auth/google_message.html", message=message), status


def _clean_intent(raw) -> str:
    s = str(raw or "").strip().lower()
    return s if s in _INTENTS else "login"


def _pick_slug(preferred: str, email: str) -> str | None:
    candidates = []
    pref = normalize_slug_input(preferred)
    if pref:
        candidates.append(pref[:32])
    local = normalize_slug_input(email.split("@", 1)[0])
    if local:
        candidates.append(local[:24])
    candidates.append("ofis")
    seen = set()
    for base in candidates:
        base = (base or "ofis")[:24].strip("_") or "ofis"
        if len(base) < 3:
            base = (base + "ofis")[:24]
        for i in range(0, 30):
            slug = base if i == 0 else f"{base[:20]}{i}"
            if slug in seen:
                continue
            seen.add(slug)
            if validate_slug_format(slug):
                continue
            if not _slug_is_taken(slug):
                return slug
    return None


def _company_name(preferred: str, google_name: str, email: str) -> str:
    for raw in (preferred, google_name, email.split("@", 1)[0], "Firma"):
        name = str(raw or "").strip()[:200]
        if not validate_company_name(name):
            return name
    return "Firma"


def _handoff_for_email(email: str, module: str):
    slug = _lookup_active_tenant_slug("email", email)
    if not slug:
        return None
    schema = schema_name_for_slug(slug)
    if not schema:
        return None
    row = _fetch_login_user_row("email", email, schema=schema)
    if not row or not row.get("is_active"):
        return None
    token = mint_login_handoff_token(
        user_id=int(row["id"]),
        tenant_slug=slug,
        security_stamp=row.get("security_stamp"),
        next_path=_canonical_next_for_module(module),
    )
    return _handoff_url(slug, token)


def _pending_purchase_checkout(email: str):
    row = fetch_one(
        """
        SELECT i.invoice_id, t.status
        FROM public.platform_signup_intents i
        INNER JOIN public.tenants t ON t.id = i.tenant_id
        WHERE LOWER(i.email) = %s
        ORDER BY i.id DESC
        LIMIT 1
        """,
        (email,),
    )
    if not row or str(row.get("status") or "") != "pending_payment":
        return None
    invoice_id = row.get("invoice_id")
    if not invoice_id:
        return None
    from paytr_checkout_token import attach_pay_token_to_invoice_metadata
    from tenant_identity import _tenant_apex_domains

    token = attach_pay_token_to_invoice_metadata(int(invoice_id))
    apex = (_tenant_apex_domains() or ("payafin.com",))[0]
    return f"https://{apex}/billing/paytr/checkout/{int(invoice_id)}?token={token}"


def _login_public_user(email: str):
    row = fetch_one(
        """
        SELECT id, username, full_name, role, is_active, security_stamp
        FROM public.users
        WHERE LOWER(username) = %s
        LIMIT 1
        """,
        (email,),
    )
    if not row or not row.get("is_active"):
        return None
    user = User(
        id=row["id"],
        username=row["username"],
        full_name=row["full_name"],
        role=row["role"],
        aktif_mi=row["is_active"],
        security_stamp=row.get("security_stamp"),
    )
    login_user(user, remember=True)
    try:
        stamp_session_tenant_slug()
    except Exception:
        pass
    return True


def _start_trial(email: str, name: str, state: dict):
    slug = _pick_slug(str(state.get("slug") or ""), email)
    if not slug:
        return _page("Uygun bir işletme adresi üretilemedi. Kayıt formundan devam edin.", 400)
    company = _company_name(str(state.get("company_name") or ""), name, email)
    module = str(state.get("module") or "").strip().lower()
    mode = str(state.get("mode") or "").strip().lower()
    ledger_only = mode == "ledger_only" or module == "ledger"
    selected = ["ledger"] if ledger_only else ([module] if module in ("personnel", "randevu", "ledger") else [])
    password = secrets.token_urlsafe(18) + "Aa1"
    try:
        reserve_tenant_slug(
            slug,
            company_name=company,
            country_code="TR",
            plan="trial",
            status="provisioning",
        )
    except (TenantSlugConflictError, TenantSlugReserveError):
        logger.info("google trial reserve failed slug=%s", slug)
        return _page("Bu adres kullanılamıyor. Kayıt formundan farklı bir adres deneyin.", 409)
    app_obj = current_app._get_current_object()
    threading.Thread(
        target=_provision_worker,
        kwargs={
            "app": app_obj,
            "slug": slug,
            "admin_username": email,
            "admin_password": password,
            "admin_full_name": name or company,
            "plan": "trial",
            "selected_module_keys": selected,
            "module_tier_preferences": {},
            "ledger_only": ledger_only,
        },
        name=f"google-provision-{slug}",
        daemon=True,
    ).start()
    ticket = mint_provision_ticket(email=email, slug=slug)
    return redirect(f"/auth/google/waiting?{urlencode({'ticket': ticket})}")


def _start_purchase(email: str, name: str, state: dict):
    module = str(state.get("module") or "").strip().lower()
    mode = str(state.get("mode") or "").strip().lower()
    ledger_only = mode == "ledger_only" or module == "ledger"
    selected = ["ledger"] if ledger_only else ([module] if module in ("personnel", "randevu", "ledger") else [])
    data = {
        "module": "ledger" if ledger_only else (module or "core"),
        "tier": str(state.get("tier") or "").strip().lower(),
    }
    try:
        purchase_module, purchase_tier = _resolve_purchase_module_and_tier(
            data,
            selected_modules=selected,
            tier_prefs={},
            ledger_only=ledger_only,
        )
        bill = _compute_purchase_bill(
            module_key=purchase_module,
            tier_key=purchase_tier,
            country_code="TR",
        )
    except PurchasePricingError as e:
        return _page(str(e) or "Bu plan satın alınamıyor.", 400)

    slug = _pick_slug(str(state.get("slug") or ""), email)
    if not slug:
        return _page("Uygun bir işletme adresi üretilemedi.", 400)
    company = _company_name(str(state.get("company_name") or ""), name, email)
    try:
        reserved = reserve_tenant_slug(
            slug,
            company_name=company,
            country_code="TR",
            plan="purchase",
            status="pending_payment",
        )
    except (TenantSlugConflictError, TenantSlugReserveError):
        return _page("Bu adres kullanılamıyor. Kayıt formundan farklı bir adres deneyin.", 409)
    tenant_id = (reserved.get("tenant") or {}).get("id")
    try:
        if not tenant_id:
            raise PurchasePricingError("Tenant rezervasyonu tamamlanamadı.")
        inv = _create_purchase_invoice(
            tenant_id=int(tenant_id),
            slug=slug,
            module_key=purchase_module,
            tier_key=purchase_tier,
            bill=bill,
        )
        pay_token = inv.get("_pay_token")
        invoice_id = inv.get("id")
        if not pay_token or not invoice_id:
            raise PurchasePricingError("Ödeme bağlantısı oluşturulamadı.")
        _insert_purchase_signup_intent(
            tenant_id=int(tenant_id),
            invoice_id=int(invoice_id),
            email=email,
            admin_full_name=name or company,
            module_key=purchase_module,
            tier_key=purchase_tier,
            password_hash=generate_password_hash(secrets.token_urlsafe(18) + "Aa1"),
            selected_module_keys=selected,
            module_tier_preferences={},
            ledger_only=ledger_only,
        )
    except Exception:
        logger.exception("google purchase invoice failed slug=%s", slug)
        _release_pending_payment_tenant(slug)
        return _page("Ödeme kaydı oluşturulamadı. Lütfen tekrar deneyin.", 400)
    from tenant_identity import _tenant_apex_domains

    apex = (_tenant_apex_domains() or ("payafin.com",))[0]
    return redirect(
        f"https://{apex}/billing/paytr/checkout/{int(invoice_id)}?token={pay_token}"
    )


@bp.route("/auth/google", methods=["GET"])
@_apex_only
def google_start():
    if not google_configured():
        return _page(
            "Google ile giriş henüz yapılandırılmadı. "
            "GOOGLE_CLIENT_ID ve GOOGLE_CLIENT_SECRET eklendikten sonra kullanılabilir.",
            503,
        )
    intent = _clean_intent(request.args.get("intent"))
    state = mint_oauth_state(
        {
            "intent": intent,
            "module": str(request.args.get("module") or "").strip().lower()[:32],
            "tier": str(request.args.get("tier") or "").strip().lower()[:32],
            "mode": str(request.args.get("mode") or "").strip().lower()[:32],
            "slug": normalize_slug_input(request.args.get("slug"))[:32],
            "company_name": str(request.args.get("company_name") or "").strip()[:200],
        }
    )
    return redirect(authorization_url(state))


@bp.route("/auth/google/callback", methods=["GET"])
@_apex_only
def google_callback():
    if not google_configured():
        return _page(
            "Google ile giriş henüz yapılandırılmadı.",
            503,
        )
    err = str(request.args.get("error") or "").strip()
    if err:
        logger.info("google callback error=%s", err[:80])
        return _page("Google girişi iptal edildi veya tamamlanamadı.", 400)
    state = read_oauth_state(request.args.get("state"))
    if not state:
        return _page("Oturum doğrulanamadı. Google ile devam et düğmesine yeniden basın.", 400)
    try:
        profile = fetch_google_profile(request.args.get("code") or "")
    except RuntimeError as exc:
        logger.info("google profile failed reason=%s", exc)
        return _page("Google hesabı doğrulanamadı. Lütfen tekrar deneyin.", 400)

    email = profile["email"]
    name = profile["name"]
    module = str(state.get("module") or "")
    try:
        handoff = _handoff_for_email(email, module)
    except Exception:
        logger.exception("google handoff failed")
        return _page("Giriş tamamlanamadı. Lütfen tekrar deneyin.", 500)
    if handoff:
        return redirect(handoff)

    pending = None
    try:
        pending = _pending_purchase_checkout(email)
    except Exception:
        logger.exception("google pending checkout failed")
    if pending:
        return redirect(pending)

    if _login_public_user(email):
        return redirect("/")

    intent = _clean_intent(state.get("intent"))
    if intent == "purchase":
        return _start_purchase(email, name, state)
    return _start_trial(email, name, state)


@bp.route("/auth/google/waiting", methods=["GET"])
@_apex_only
def google_waiting():
    ticket = read_provision_ticket(request.args.get("ticket"))
    if not ticket:
        return _page("Kurulum bağlantısının süresi doldu. Google ile tekrar deneyin.", 400)
    return render_template(
        "auth/google_waiting.html",
        slug=ticket["slug"],
        ticket=request.args.get("ticket") or "",
    )


@bp.route("/auth/google/enter", methods=["GET"])
@_apex_only
def google_enter():
    ticket = read_provision_ticket(request.args.get("ticket"))
    if not ticket:
        return _page("Giriş bağlantısının süresi doldu. Google ile tekrar deneyin.", 400)
    try:
        handoff = _handoff_for_email(ticket["email"], "")
    except Exception:
        logger.exception("google enter handoff failed")
        return _page("Hesap henüz hazır değil. Birkaç saniye sonra yeniden deneyin.", 409)
    if not handoff:
        return _page("Hesap henüz hazır değil. Birkaç saniye sonra yeniden deneyin.", 409)
    return redirect(handoff)
