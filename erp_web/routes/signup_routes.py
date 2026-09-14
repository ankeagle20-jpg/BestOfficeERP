# -*- coding: utf-8 -*-
"""Payafin herkese açık kayıt API (apex-only, async provisioning)."""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from decimal import Decimal
from functools import wraps

from flask import Blueprint, current_app, jsonify, render_template, request
from db import execute, execute_returning, fetch_one
from module_pricing_engine import ModulePricingEngineError, calculate_module_bill
from pricing_engine import PricingEngineError, calculate_tenant_bill
from signup_provision_errors import map_provision_error
from signup_rate_limit import check_signup_post_rate, check_slug_available_rate
from signup_validation import (
    honeypot_triggered,
    normalize_slug_input,
    validate_admin_full_name,
    validate_company_name,
    validate_country_code,
    validate_email,
    validate_password_confirm,
    validate_password_strength,
    validate_slug_format,
)
from tenant_identity import _tenant_apex_domains, resolve_tenant_slug
from tenant_provisioning import (
    TenantProvisionError,
    TenantSlugConflictError,
    TenantSlugReserveError,
    mark_tenant_provision_failed,
    normalize_module_tier_preferences,
    normalize_signup_selected_modules,
    provision_new_tenant,
    reserve_tenant_slug,
    schema_name_for_slug,
)

logger = logging.getLogger(__name__)

bp = Blueprint("signup", __name__)

MSG_SLUG_TAKEN = "Bu adres zaten kullanılıyor."
MSG_SLUG_FAILED = "Bu adres kullanılamıyor; farklı bir subdomain deneyin."


def _json403(msg: str):
    return jsonify({"ok": False, "mesaj": msg}), 403


def platform_public_only(f):
    """Kiracı subdomain'inde signup API kapalı (admin_pricing tersi; auth yok)."""

    @wraps(f)
    def _guard(*args, **kwargs):
        if resolve_tenant_slug():
            path = request.path or ""
            msg = "Kayıt yalnızca ana (public) host üzerinden yapılabilir."
            if "/api/" in path or request.is_json or (
                request.accept_mimetypes.best == "application/json"
            ):
                return _json403(msg)
            return msg, 403
        return f(*args, **kwargs)

    return _guard


def _login_url(slug: str) -> str:
    apex = (_tenant_apex_domains() or ("payafin.com",))[0]
    return f"https://{slug}.{apex}/login"


def _tenant_catalog_row(slug: str) -> dict | None:
    schema = schema_name_for_slug(slug)
    if not schema:
        return None
    return fetch_one(
        """
        SELECT slug, schema_name, status, error_message
        FROM public.tenants
        WHERE slug = %s OR schema_name = %s
        LIMIT 1
        """,
        (slug, schema),
    )


def _slug_is_taken(slug: str) -> bool:
    if _tenant_catalog_row(slug):
        return True
    schema = schema_name_for_slug(slug)
    if not schema:
        return True
    ns = fetch_one("SELECT 1 AS ok FROM pg_namespace WHERE nspname = %s", (schema,))
    return bool(ns)


def _slug_conflict_response(slug: str):
    """Slug alınmışsa (failed/active/provisioning/orphan şema) 409 yanıtı."""
    row = _tenant_catalog_row(slug)
    if row:
        if row.get("status") == "failed":
            return jsonify({"ok": False, "mesaj": MSG_SLUG_FAILED}), 409
        return jsonify({"ok": False, "mesaj": MSG_SLUG_TAKEN}), 409
    schema = schema_name_for_slug(slug)
    if schema and fetch_one("SELECT 1 AS ok FROM pg_namespace WHERE nspname = %s", (schema,)):
        return jsonify({"ok": False, "mesaj": MSG_SLUG_TAKEN}), 409
    return None


def _fake_success_payload(slug: str) -> dict:
    return {
        "ok": True,
        "slug": slug or "pending",
        "status": "provisioning",
        "login_url": _login_url(slug) if slug else None,
    }


def _parse_selected_modules(data: dict) -> list[str]:
    """Request body selected_modules — geçersiz değerler sessizce elenir."""
    raw = data.get("selected_modules")
    if raw is None:
        return []
    if not isinstance(raw, list):
        return []
    return normalize_signup_selected_modules(raw)


def _parse_module_tier_preferences(data: dict) -> dict[str, str]:
    """Request body module_tier_preferences — geçersiz anahtar/değer sessizce elenir."""
    raw = data.get("module_tier_preferences")
    if raw is None:
        return {}
    return normalize_module_tier_preferences(raw)


def _parse_ledger_only(data: dict) -> bool:
    """Request body ledger_only — yalnız açık true benzeri değerler; varsayılan False."""
    raw = data.get("ledger_only")
    if raw is True or raw == 1:
        return True
    if isinstance(raw, str) and raw.strip().lower() in ("1", "true", "yes", "on"):
        return True
    return False


def _parse_signup_intent(data: dict) -> str:
    """A2.1: yalnız 'purchase' özel dal; yok/trial/diğer → mevcut trial+provision yolu."""
    raw = data.get("intent")
    if raw is None:
        return "trial"
    s = str(raw).strip().lower()
    if s == "purchase":
        return "purchase"
    return "trial"


_PURCHASE_MODULES = frozenset({"core", "personnel", "randevu", "ledger"})


class PurchasePricingError(Exception):
    """Geçersiz module/tier veya fiyatlandırılamayan Satın Al isteği."""

    def __init__(self, message: str, *, errors: dict | None = None):
        super().__init__(message)
        self.errors = errors or {}


def _release_pending_payment_tenant(slug: str) -> None:
    """Fatura oluşturulamazsa pending_payment rezervasyonunu geri al."""
    slug_s = str(slug or "").strip().lower()
    if not slug_s:
        return
    try:
        execute(
            """
            DELETE FROM public.tenants
            WHERE slug = %s AND status = 'pending_payment'
            """,
            (slug_s,),
        )
    except Exception:
        logger.exception("release pending_payment tenant failed slug=%s", slug_s)


def _normalize_tier_key(raw) -> str:
    return str(raw or "").strip().lower()


def _resolve_purchase_module_and_tier(
    data: dict,
    *,
    selected_modules: list[str],
    tier_prefs: dict[str, str],
    ledger_only: bool,
) -> tuple[str, str]:
    """Satın Al için tek module + tier. Client tutarı yok sayılır."""
    tier = _normalize_tier_key(data.get("tier"))
    module = str(data.get("module") or "").strip().lower()

    if ledger_only:
        module = "ledger"
        if not tier:
            tier = _normalize_tier_key(tier_prefs.get("ledger"))
    elif module in _PURCHASE_MODULES:
        if not tier and module != "core":
            tier = _normalize_tier_key(tier_prefs.get(module))
        if not tier and module == "core":
            # core: body tier zorunlu; prefs'te core olmayabilir
            tier = _normalize_tier_key(tier_prefs.get("core"))
    elif len(selected_modules) == 1:
        module = selected_modules[0]
        if not tier:
            tier = _normalize_tier_key(tier_prefs.get(module))
    elif not selected_modules and not module:
        module = "core"
    else:
        raise PurchasePricingError(
            "Satın Al için tek bir modül ve kademe gerekli.",
            errors={"module": "invalid_module"},
        )

    if module not in _PURCHASE_MODULES:
        raise PurchasePricingError(
            "Geçersiz modül.",
            errors={"module": "invalid_module"},
        )
    if not tier or not re.fullmatch(r"[a-z0-9_]{1,32}", tier):
        raise PurchasePricingError(
            "Geçersiz veya eksik kademe (tier).",
            errors={"tier": "invalid_tier"},
        )
    if tier in ("enterprise", "contact", "contact_sales"):
        raise PurchasePricingError(
            "Bu kademe self-servis Satın Al için uygun değil.",
            errors={"tier": "contact_sales"},
        )
    return module, tier


def _bill_core_for_tier(country_code: str, tier_key: str) -> dict:
    """Core ERP: istenen tier_key için sunucu tutarı (client amount yok sayılır)."""
    cc = str(country_code or "TR").strip().upper() or "TR"
    row = fetch_one(
        """
        SELECT tier_key, min_customers, max_customers, is_active
        FROM public.pricing_tiers
        WHERE country_code = %s
          AND tier_key = %s
          AND is_active = TRUE
        LIMIT 1
        """,
        (cc, tier_key),
    )
    if not row:
        # US master türetilmiş ülkelerde override yoksa US'tan kontrol
        row = fetch_one(
            """
            SELECT tier_key, min_customers, max_customers, is_active
            FROM public.pricing_tiers
            WHERE country_code = 'US'
              AND tier_key = %s
              AND is_active = TRUE
            LIMIT 1
            """,
            (tier_key,),
        )
    if not row:
        raise PurchasePricingError(
            "Geçersiz Core ERP kademesi.",
            errors={"tier": "invalid_tier"},
        )
    n = int(row.get("min_customers") or 0)
    try:
        bill = calculate_tenant_bill(cc, n, 1)
    except PricingEngineError as e:
        raise PurchasePricingError(
            "Fiyat hesaplanamadı.",
            errors={"tier": "pricing_error", "detail": str(e)},
        ) from e
    if str(bill.get("tier_key") or "") != tier_key:
        raise PurchasePricingError(
            "Seçilen kademe fiyat motoru ile eşleşmedi.",
            errors={"tier": "tier_mismatch"},
        )
    return bill


def _bill_module_for_tier(
    module_key: str, country_code: str, tier_key: str
) -> dict:
    """Modül: taban kademe tutarı (0 personel/şube/randevu — aşım yok)."""
    cc = str(country_code or "TR").strip().upper() or "TR"
    kwargs = {
        "module_key": module_key,
        "country_code": cc,
        "personnel_count": 0,
        "branch_count": 0,
        "billing_period": "monthly",
        "tier_key": tier_key,
    }
    if module_key == "randevu":
        kwargs["appointment_count"] = 0
    try:
        bill = calculate_module_bill(**kwargs)
    except ModulePricingEngineError as e:
        raise PurchasePricingError(
            "Geçersiz modül veya kademe.",
            errors={"tier": "invalid_tier", "detail": str(e)},
        ) from e
    if bill.get("requires_contact_sales") or bill.get("kind") == "contact":
        raise PurchasePricingError(
            "Bu kademe self-servis Satın Al için uygun değil.",
            errors={"tier": "contact_sales"},
        )
    if str(bill.get("tier_key") or "") != tier_key:
        raise PurchasePricingError(
            "Seçilen kademe fiyat motoru ile eşleşmedi.",
            errors={"tier": "tier_mismatch"},
        )
    total = Decimal(str(bill.get("total_monthly") or "0"))
    if total <= 0:
        raise PurchasePricingError(
            "Hesaplanan tutar geçersiz.",
            errors={"tier": "invalid_amount"},
        )
    return bill


def _compute_purchase_bill(
    *,
    module_key: str,
    tier_key: str,
    country_code: str,
) -> dict:
    """Client tutarını yok say; sunucu motorundan bill üret."""
    if module_key == "core":
        bill = _bill_core_for_tier(country_code, tier_key)
        total = Decimal(str(bill.get("total_monthly") or "0"))
        if total <= 0:
            raise PurchasePricingError(
                "Hesaplanan tutar geçersiz.",
                errors={"tier": "invalid_amount"},
            )
        return bill
    return _bill_module_for_tier(module_key, country_code, tier_key)

def _create_purchase_invoice(
    *,
    tenant_id: int,
    slug: str,
    module_key: str,
    tier_key: str,
    bill: dict,
) -> dict:
    """platform_tenant_invoices: source=paytr, status=sent + merchant_oid damgası."""
    currency = str(bill.get("currency") or "TRY").strip().upper() or "TRY"
    total = Decimal(str(bill.get("total_monthly") or "0")).quantize(Decimal("0.01"))
    invoice_no = f"PUR-{slug}-{secrets.token_hex(4)}"[:64]
    meta = {
        "intent": "purchase",
        "module": module_key,
        "tier": tier_key,
        "signup_slug": slug,
        "bill_tier_key": bill.get("tier_key"),
        "bill_total_monthly": float(total),
    }
    row = execute_returning(
        """
        INSERT INTO public.platform_tenant_invoices (
            tenant_id, tenant_slug, subscription_id, invoice_no, status, currency,
            total_gross, issued_at, due_at, paid_at, source, external_ref, metadata
        ) VALUES (
            %s, %s, NULL, %s, 'sent', %s,
            %s, NOW(), NOW() + INTERVAL '24 hours', NULL, 'paytr', NULL, %s::jsonb
        )
        RETURNING *
        """,
        (
            int(tenant_id),
            slug,
            invoice_no,
            currency,
            total,
            json.dumps(meta),
        ),
    )
    if not row:
        raise PurchasePricingError("Fatura oluşturulamadı.")
    # PayTR init için merchant_oid (A2.3 checkout)
    inv_id = int(row["id"])
    meta["merchant_oid"] = f"INV{inv_id}{secrets.token_hex(4)}"
    updated = execute_returning(
        """
        UPDATE public.platform_tenant_invoices
        SET metadata = %s::jsonb, updated_at = NOW()
        WHERE id = %s
        RETURNING *
        """,
        (json.dumps(meta), inv_id),
    )
    return updated or row


def _provision_worker(
    app,
    slug: str,
    *,
    admin_username: str,
    admin_password: str,
    admin_full_name: str,
    plan: str,
    selected_module_keys: list[str] | None = None,
    module_tier_preferences: dict[str, str] | None = None,
    ledger_only: bool = False,
) -> None:
    t0 = time.monotonic()
    with app.app_context():
        try:
            result = provision_new_tenant(
                slug,
                plan=plan,
                admin_username=admin_username,
                admin_password=admin_password,
                admin_full_name=admin_full_name,
                allow_existing_provisioning_row=True,
                selected_module_keys=selected_module_keys or [],
                module_tier_preferences=module_tier_preferences or {},
                ledger_only=bool(ledger_only),
            )
            logger.info(
                "signup_provision ok slug=%s ledger_only=%s duration_sec=%.1f",
                slug,
                bool(ledger_only),
                time.monotonic() - t0,
            )
            try:
                from email_verification import send_signup_verification_email

                send_signup_verification_email(
                    slug,
                    int(result["admin_id"]),
                    str(result["admin_username"]),
                )
            except Exception:
                logger.exception(
                    "signup verification email failed slug=%s", slug
                )
        except Exception as exc:
            duration = time.monotonic() - t0
            user_msg = map_provision_error(exc)
            logger.exception(
                "signup_provision failed slug=%s duration_sec=%.1f",
                slug,
                duration,
            )
            mark_tenant_provision_failed(slug, reason=str(exc), error_message=user_msg)


@bp.route("/signup", methods=["GET"])
@platform_public_only
def signup_page():
    """Herkese açık kayıt formu (apex-only, kimlik doğrulama yok)."""
    from flask import make_response

    apex = (_tenant_apex_domains() or ("payafin.com",))[0]
    resp = make_response(render_template("signup/signup.html", apex_domain=apex))
    resp.headers["Cache-Control"] = "no-cache, must-revalidate, max-age=0"
    return resp


@bp.route("/api/signup/slug-available", methods=["GET"])
@platform_public_only
def api_signup_slug_available():
    allowed, retry_after = check_slug_available_rate()
    if not allowed:
        return (
            jsonify({"ok": False, "mesaj": "Çok fazla deneme, lütfen bekleyin."}),
            429,
            {"Retry-After": str(retry_after)},
        )

    slug = normalize_slug_input(request.args.get("slug"))
    reason = validate_slug_format(slug)
    if reason:
        return jsonify(
            {
                "ok": True,
                "slug": slug,
                "available": False,
                "reason": reason,
            }
        )

    taken = _slug_is_taken(slug)
    return jsonify(
        {
            "ok": True,
            "slug": slug,
            "available": not taken,
            "reason": "taken" if taken else None,
        }
    )


@bp.route("/api/signup/status", methods=["GET"])
@platform_public_only
def api_signup_status():
    slug = normalize_slug_input(request.args.get("slug"))
    if validate_slug_format(slug):
        return jsonify({"ok": False, "mesaj": "Geçersiz slug."}), 400

    row = fetch_one(
        """
        SELECT slug, schema_name, status, plan, error_message
        FROM public.tenants WHERE slug=%s
        """,
        (slug,),
    )
    if not row:
        return jsonify({"ok": False, "mesaj": "Kayıt bulunamadı."}), 404

    err = row.get("error_message") if row["status"] == "failed" else None
    return jsonify(
        {
            "ok": True,
            "slug": row["slug"],
            "schema_name": row["schema_name"],
            "status": row["status"],
            "plan": row["plan"],
            "error_message": err,
            "login_url": _login_url(slug) if row["status"] == "active" else None,
        }
    )


@bp.route("/api/signup", methods=["POST"])
@platform_public_only
def api_signup():
    data = request.get_json(silent=True) or {}
    slug = normalize_slug_input(data.get("slug"))
    website = data.get("website")

    if honeypot_triggered(website):
        return jsonify(_fake_success_payload(slug)), 200

    errors: dict[str, str] = {}
    slug_reason = validate_slug_format(slug)
    if slug_reason:
        errors["slug"] = slug_reason
    if validate_company_name(data.get("company_name")):
        errors["company_name"] = "invalid_company_name"
    if validate_country_code(data.get("country_code")):
        errors["country_code"] = "invalid_country"
    email = str(data.get("admin_username") or data.get("email") or "").strip().lower()
    if validate_email(email):
        errors["admin_username"] = "invalid_email"
    if validate_admin_full_name(data.get("admin_full_name")):
        errors["admin_full_name"] = "invalid_full_name"
    password = data.get("admin_password")
    pw_reason = validate_password_strength(password)
    if pw_reason:
        errors["admin_password"] = pw_reason
    if validate_password_confirm(password, data.get("password_confirm")):
        errors["password_confirm"] = "password_mismatch"

    if errors:
        return jsonify({"ok": False, "mesaj": "Doğrulama hatası.", "errors": errors}), 400

    selected_modules = _parse_selected_modules(data)
    tier_prefs = _parse_module_tier_preferences(data)
    ledger_only = _parse_ledger_only(data)
    signup_intent = _parse_signup_intent(data)
    # S4.1: ledger_only sunucuda doğrulanır — boş/ledger-dışı → 400 (fail-closed, reserve öncesi)
    if ledger_only and set(selected_modules) != {"ledger"}:
        return (
            jsonify(
                {
                    "ok": False,
                    "mesaj": "Sadece Payafin Cari kaydı için selected_modules yalnızca ['ledger'] olmalı.",
                    "errors": {"selected_modules": "ledger_only_requires_ledger"},
                }
            ),
            400,
        )
    if ledger_only:
        selected_modules = ["ledger"]

    conflict = _slug_conflict_response(slug)
    if conflict:
        return conflict

    allowed, retry_after = check_signup_post_rate()
    if not allowed:
        return (
            jsonify({"ok": False, "mesaj": "Çok fazla deneme, lütfen bekleyin."}),
            429,
            {"Retry-After": str(retry_after)},
        )

    purchase = signup_intent == "purchase"
    reserve_status = "pending_payment" if purchase else "provisioning"
    reserve_plan = "purchase" if purchase else "trial"
    country_code = str(data.get("country_code") or "").strip().upper() or "TR"

    # A2.2: Satın Al — fiyatı reserve ÖNCESİ hesapla (geçersiz tier → slug kilitleme)
    purchase_module = None
    purchase_tier = None
    purchase_bill = None
    if purchase:
        # Client'tan gelen tutarı yok say (total_gross / amount / price)
        try:
            purchase_module, purchase_tier = _resolve_purchase_module_and_tier(
                data,
                selected_modules=selected_modules,
                tier_prefs=tier_prefs,
                ledger_only=ledger_only,
            )
            purchase_bill = _compute_purchase_bill(
                module_key=purchase_module,
                tier_key=purchase_tier,
                country_code=country_code,
            )
        except PurchasePricingError as e:
            return (
                jsonify(
                    {
                        "ok": False,
                        "mesaj": str(e) or "Geçersiz modül veya kademe.",
                        "errors": e.errors or {"tier": "invalid_tier"},
                    }
                ),
                400,
            )

    try:
        reserved = reserve_tenant_slug(
            slug,
            company_name=str(data.get("company_name") or "").strip(),
            country_code=country_code,
            plan=reserve_plan,
            status=reserve_status,
        )
    except TenantSlugConflictError:
        retry = _slug_conflict_response(slug)
        if retry:
            return retry
        return jsonify({"ok": False, "mesaj": MSG_SLUG_TAKEN}), 409
    except (TenantSlugReserveError, TenantProvisionError) as e:
        logger.warning("reserve_tenant_slug failed slug=%s: %s", slug, e)
        return jsonify({"ok": False, "mesaj": "Kayıt tamamlanamadı, bilgileri kontrol edin."}), 400

    # A2.2: Satın Al — pending_payment + fatura; provizyon/pay_url YOK (A2.3+)
    if purchase:
        tenant_row = reserved.get("tenant") or {}
        tenant_id = tenant_row.get("id")
        try:
            if not tenant_id:
                raise PurchasePricingError("Tenant rezervasyonu tamamlanamadı.")
            inv = _create_purchase_invoice(
                tenant_id=int(tenant_id),
                slug=slug,
                module_key=purchase_module,
                tier_key=purchase_tier,
                bill=purchase_bill,
            )
        except Exception as e:
            logger.exception("purchase invoice failed slug=%s", slug)
            _release_pending_payment_tenant(slug)
            mesaj = (
                str(e)
                if isinstance(e, PurchasePricingError)
                else "Fatura oluşturulamadı."
            )
            return (
                jsonify(
                    {
                        "ok": False,
                        "mesaj": mesaj,
                        "errors": {"invoice": "create_failed"},
                    }
                ),
                400,
            )
        total_gross = inv.get("total_gross")
        return (
            jsonify(
                {
                    "ok": True,
                    "slug": slug,
                    "status": "pending_payment",
                    "tenant_id": tenant_id,
                    "plan": reserve_plan,
                    "invoice_id": inv.get("id"),
                    "total_gross": float(total_gross) if total_gross is not None else None,
                    "currency": inv.get("currency") or purchase_bill.get("currency"),
                }
            ),
            200,
        )

    app_obj = current_app._get_current_object()
    thread = threading.Thread(
        target=_provision_worker,
        kwargs={
            "app": app_obj,
            "slug": slug,
            "admin_username": email,
            "admin_password": str(password),
            "admin_full_name": str(data.get("admin_full_name") or "").strip(),
            "plan": "trial",
            "selected_module_keys": selected_modules,
            "module_tier_preferences": tier_prefs,
            "ledger_only": ledger_only,
        },
        name=f"provision-{slug}",
        daemon=True,
    )
    thread.start()

    return (
        jsonify(
            {
                "ok": True,
                "slug": slug,
                "status": "provisioning",
                "schema_name": schema_name_for_slug(slug),
                "plan": "trial",
                "poll_url": f"/api/signup/status?slug={slug}",
                "login_url": _login_url(slug),
            }
        ),
        202,
    )
