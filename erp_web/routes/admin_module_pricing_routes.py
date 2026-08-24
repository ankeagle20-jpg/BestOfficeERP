# -*- coding: utf-8 -*-
"""Admin: Payafin modül hibrit fiyatlandırma yönetimi (yalnız public host)."""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Blueprint, g, jsonify, render_template, request
from flask_login import current_user

from auth import admin_gerekli
from db import execute, fetch_all, fetch_one
from module_pricing_engine import ModulePricingEngineError, calculate_module_bill
from module_pricing_public_cache import invalidate_public_module_pricing_cache

logger = logging.getLogger(__name__)

bp = Blueprint("admin_module_pricing", __name__)

MSG_PLATFORM_ONLY = (
    "Platform modül fiyatlandırması yalnızca ana (public) host'ta kullanılabilir."
)

# Aşama C: şimdilik yalnız Personel Yönetimi
MODULE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("personnel", "Personel Yönetimi"),
)
MODULE_KEYS = frozenset(k for k, _ in MODULE_OPTIONS)


def _json403(msg: str):
    return jsonify({"ok": False, "mesaj": msg}), 403


def platform_module_pricing_admin(f):
    """@admin_gerekli + kiracı subdomain'inde 403 (platform-only)."""

    @wraps(f)
    def _tenant_guard(*args, **kwargs):
        if getattr(g, "tenant_schema", None):
            path = request.path or ""
            if "/api/" in path or request.is_json or (
                request.accept_mimetypes.best == "application/json"
            ):
                return _json403(MSG_PLATFORM_ONLY)
            return MSG_PLATFORM_ONLY, 403
        return f(*args, **kwargs)

    return admin_gerekli(_tenant_guard)


def _parse_decimal(name: str, raw, *, places: int = 2) -> Decimal:
    try:
        d = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{name} geçersiz")
    if places == 4:
        q = Decimal("0.0001")
    else:
        q = Decimal("0.01")
    return d.quantize(q)


def _parse_int_nonneg(name: str, raw, *, allow_null: bool = False):
    if allow_null and (raw is None or raw == ""):
        return None
    try:
        v = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} geçersiz")
    if v < 0:
        raise ValueError(f"{name} negatif olamaz")
    return v


def _tier_row_to_json(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "module_key": row["module_key"],
        "country_code": row["country_code"],
        "currency": row["currency"],
        "tier_key": row["tier_key"],
        "display_name": row["display_name"],
        "base_monthly": float(row["base_monthly"]),
        "price_per_personnel": float(row["price_per_personnel"]),
        "max_personnel": (
            None if row["max_personnel"] is None else int(row["max_personnel"])
        ),
        "included_branches": int(row["included_branches"]),
        "price_per_extra_branch": float(row["price_per_extra_branch"]),
        "is_contact_sales": bool(row["is_contact_sales"]),
        "annual_discount_months": int(row["annual_discount_months"]),
        "setup_fee": float(row["setup_fee"]),
        "sort_order": int(row["sort_order"]),
        "is_active": bool(row["is_active"]),
    }


def _validate_update_fields(
    *,
    base_monthly: Decimal,
    price_per_personnel: Decimal,
    max_personnel: int | None,
    price_per_extra_branch: Decimal,
    annual_discount_months: int,
    is_contact_sales: bool,
) -> None:
    for label, val in (
        ("base_monthly", base_monthly),
        ("price_per_personnel", price_per_personnel),
        ("price_per_extra_branch", price_per_extra_branch),
    ):
        if val < 0:
            raise ValueError(f"{label} negatif olamaz")
    if max_personnel is not None and max_personnel <= 0:
        raise ValueError("max_personnel pozitif olmalı veya boş (sınırsız)")
    if is_contact_sales and max_personnel is not None:
        raise ValueError(
            "contact_sales kademelerinde max_personnel boş (NULL) olmalı"
        )
    if annual_discount_months < 0 or annual_discount_months >= 12:
        raise ValueError("annual_discount_months 0–11 arası olmalı")


@bp.route("/module-pricing")
@platform_module_pricing_admin
def module_pricing_page():
    return render_template(
        "admin/module_pricing.html",
        username=getattr(current_user, "username", "") or "",
        module_options=[{"key": k, "label": lab} for k, lab in MODULE_OPTIONS],
    )


@bp.route("/api/module-pricing/tiers")
@platform_module_pricing_admin
def api_module_pricing_tiers():
    mk = (request.args.get("module") or request.args.get("module_key") or "").strip()
    cc = (request.args.get("country") or request.args.get("country_code") or "TR").strip().upper()
    if not mk:
        return jsonify({"ok": False, "mesaj": "module gerekli"}), 400
    if mk not in MODULE_KEYS:
        return jsonify({"ok": False, "mesaj": "geçersiz module"}), 400
    if not cc or len(cc) != 2 or not cc.isalpha():
        return jsonify({"ok": False, "mesaj": "geçersiz country"}), 400

    rows = fetch_all(
        """
        SELECT id, module_key, country_code, currency, tier_key, display_name,
               base_monthly, price_per_personnel, max_personnel,
               included_branches, price_per_extra_branch,
               is_contact_sales, annual_discount_months, setup_fee,
               sort_order, is_active
        FROM public.module_pricing_tiers
        WHERE module_key = %s AND country_code = %s
        ORDER BY sort_order, id
        """,
        (mk, cc),
    ) or []
    return jsonify(
        {
            "ok": True,
            "module_key": mk,
            "country_code": cc,
            "tiers": [_tier_row_to_json(r) for r in rows],
            "n": len(rows),
        }
    )


@bp.route("/api/module-pricing/tiers/<int:tier_id>", methods=["PUT"])
@platform_module_pricing_admin
def api_module_pricing_tier_update(tier_id: int):
    body = request.get_json(silent=True) or {}
    row = fetch_one(
        """
        SELECT id, module_key, country_code, currency, tier_key,
               is_contact_sales, included_branches, setup_fee, sort_order
        FROM public.module_pricing_tiers
        WHERE id = %s
        """,
        (tier_id,),
    )
    if not row:
        return jsonify({"ok": False, "mesaj": "Kademe bulunamadı."}), 404
    if str(row["module_key"]) not in MODULE_KEYS:
        return jsonify({"ok": False, "mesaj": "bu modül yönetilemez"}), 400

    try:
        base_monthly = _parse_decimal("base_monthly", body.get("base_monthly"))
        price_per_personnel = _parse_decimal(
            "price_per_personnel", body.get("price_per_personnel"), places=4
        )
        max_personnel = _parse_int_nonneg(
            "max_personnel", body.get("max_personnel"), allow_null=True
        )
        price_per_extra_branch = _parse_decimal(
            "price_per_extra_branch", body.get("price_per_extra_branch")
        )
        annual_discount_months = _parse_int_nonneg(
            "annual_discount_months", body.get("annual_discount_months")
        )
        is_active = bool(body.get("is_active", True))
        is_contact_sales = bool(row["is_contact_sales"])
        _validate_update_fields(
            base_monthly=base_monthly,
            price_per_personnel=price_per_personnel,
            max_personnel=max_personnel,
            price_per_extra_branch=price_per_extra_branch,
            annual_discount_months=annual_discount_months,
            is_contact_sales=is_contact_sales,
        )
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400

    execute(
        """
        UPDATE public.module_pricing_tiers
        SET base_monthly = %s,
            price_per_personnel = %s,
            max_personnel = %s,
            price_per_extra_branch = %s,
            annual_discount_months = %s,
            is_active = %s,
            updated_at = NOW()
        WHERE id = %s
        """,
        (
            str(base_monthly),
            str(price_per_personnel),
            max_personnel,
            str(price_per_extra_branch),
            annual_discount_months,
            is_active,
            tier_id,
        ),
    )
    updated = fetch_one(
        """
        SELECT id, module_key, country_code, currency, tier_key, display_name,
               base_monthly, price_per_personnel, max_personnel,
               included_branches, price_per_extra_branch,
               is_contact_sales, annual_discount_months, setup_fee,
               sort_order, is_active
        FROM public.module_pricing_tiers
        WHERE id = %s
        """,
        (tier_id,),
    )
    invalidate_public_module_pricing_cache(
        str(row["module_key"]), str(row["country_code"])
    )
    return jsonify({"ok": True, "tier": _tier_row_to_json(updated)})


@bp.route("/api/module-pricing/preview", methods=["POST"])
@platform_module_pricing_admin
def api_module_pricing_preview():
    body = request.get_json(silent=True) or {}
    try:
        mk = str(body.get("module_key") or "").strip()
        if mk not in MODULE_KEYS:
            raise ValueError("geçersiz module_key")
        cc = str(body.get("country_code") or "TR").strip().upper()
        if not cc or len(cc) != 2 or not cc.isalpha():
            raise ValueError("geçersiz country_code")
        personnel_count = _parse_int_nonneg(
            "personnel_count", body.get("personnel_count")
        )
        branch_count = _parse_int_nonneg("branch_count", body.get("branch_count"))
        billing_period = str(body.get("billing_period") or "monthly").strip().lower()
        tier_key_raw = body.get("tier_key")
        tier_key = (
            None
            if tier_key_raw is None or str(tier_key_raw).strip() == ""
            else str(tier_key_raw).strip()
        )
        bill = calculate_module_bill(
            mk,
            cc,
            personnel_count,
            branch_count,
            billing_period=billing_period,
            tier_key=tier_key,
        )
        return jsonify({"ok": True, "bill": bill})
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except ModulePricingEngineError as e:
        return jsonify(
            {
                "ok": False,
                "mesaj": str(e),
                "code": getattr(e, "code", None),
                "required_tier_key": getattr(e, "required_tier_key", None),
                "selected_tier_key": getattr(e, "selected_tier_key", None),
            }
        ), 400
    except Exception:
        logger.exception("api_module_pricing_preview")
        return jsonify({"ok": False, "mesaj": "Önizleme hesaplanamadı."}), 500
