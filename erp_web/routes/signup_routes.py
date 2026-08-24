# -*- coding: utf-8 -*-
"""Payafin herkese açık kayıt API (apex-only, async provisioning)."""
from __future__ import annotations

import logging
import threading
import time
from functools import wraps

from flask import Blueprint, current_app, jsonify, render_template, request
from db import fetch_one
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


def _provision_worker(
    app,
    slug: str,
    *,
    admin_username: str,
    admin_password: str,
    admin_full_name: str,
    plan: str,
    selected_module_keys: list[str] | None = None,
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
            )
            logger.info(
                "signup_provision ok slug=%s duration_sec=%.1f",
                slug,
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

    try:
        reserve_tenant_slug(
            slug,
            company_name=str(data.get("company_name") or "").strip(),
            country_code=str(data.get("country_code") or "").strip().upper(),
            plan="trial",
        )
    except TenantSlugConflictError:
        retry = _slug_conflict_response(slug)
        if retry:
            return retry
        return jsonify({"ok": False, "mesaj": MSG_SLUG_TAKEN}), 409
    except (TenantSlugReserveError, TenantProvisionError) as e:
        logger.warning("reserve_tenant_slug failed slug=%s: %s", slug, e)
        return jsonify({"ok": False, "mesaj": "Kayıt tamamlanamadı, bilgileri kontrol edin."}), 400

    app_obj = current_app._get_current_object()
    selected_modules = _parse_selected_modules(data)
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
