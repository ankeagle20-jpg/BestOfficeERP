# -*- coding: utf-8 -*-
"""Admin: Payafin platform fiyatlandırma yönetimi (yalnız public host)."""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Blueprint, g, jsonify, render_template, request
from flask_login import current_user

from auth import admin_gerekli
from db import ensure_pricing_display_columns_once, execute, fetch_all, fetch_one
from pricing_display_fx import (
    display_dolar,
    get_usd_try_rate,
    parse_dolar,
    parse_indirim,
    recalculate_auto_dolar,
    resolve_dolar_on_save,
    set_usd_try_rate,
    usd_try_status,
)
from pricing_engine import PricingEngineError, calculate_tenant_bill
from pricing_public_cache import invalidate_public_pricing_cache
from module_pricing_public_cache import invalidate_public_module_pricing_cache
from services.exchange_rate_service import fetch_and_store_exchange_rates

logger = logging.getLogger(__name__)

bp = Blueprint("admin_pricing", __name__)


def _json403(msg: str):
    return jsonify({"ok": False, "mesaj": msg}), 403


def platform_pricing_admin(f):
    """@admin_gerekli + kiracı subdomain'inde 403 (platform-only)."""

    @wraps(f)
    def _tenant_guard(*args, **kwargs):
        if getattr(g, "tenant_schema", None):
            path = request.path or ""
            if "/api/" in path or request.is_json or (
                request.accept_mimetypes.best == "application/json"
            ):
                return _json403(
                    "Platform fiyatlandırması yalnızca ana (public) host'ta kullanılabilir."
                )
            return (
                "Platform fiyatlandırması yalnızca ana (public) host'ta kullanılabilir.",
                403,
            )
        return f(*args, **kwargs)

    return admin_gerekli(_tenant_guard)


def _country_param() -> str:
    cc = (request.args.get("country") or request.args.get("country_code") or "TR").strip().upper()
    if not cc or len(cc) != 2 or not cc.isalpha():
        raise ValueError("geçersiz country")
    return cc


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


def _opt_num(row: dict, key: str):
    if row.get(key) is None:
        return None
    return float(row[key])


def _tier_row_to_json(row: dict, rate: Decimal | None = None) -> dict:
    return {
        "id": row["id"],
        "country_code": row["country_code"],
        "currency": row["currency"],
        "tier_key": row["tier_key"],
        "display_name": row["display_name"],
        "min_customers": row["min_customers"],
        "max_customers": row["max_customers"],
        "base_monthly": float(row["base_monthly"]),
        "price_per_customer": float(row["price_per_customer"]),
        "included_users": row["included_users"],
        "price_per_extra_user": float(row["price_per_extra_user"]),
        "dolar_fiyat": _opt_num(row, "dolar_fiyat"),
        "dolar_fiyat_manuel": bool(row.get("dolar_fiyat_manuel")),
        "dolar_goster": display_dolar(row, rate),
        "indirim_yuzde": _opt_num(row, "indirim_yuzde"),
        "sort_order": row["sort_order"],
        "is_active": bool(row["is_active"]),
    }


def _validate_tier_fields(
    *,
    min_customers: int,
    max_customers: int | None,
    base_monthly: Decimal,
    price_per_customer: Decimal,
    included_users: int,
    price_per_extra_user: Decimal,
    display_name: str,
) -> None:
    if not (display_name or "").strip():
        raise ValueError("display_name gerekli")
    if max_customers is not None and max_customers < min_customers:
        raise ValueError("max_customers, min_customers'tan küçük olamaz")
    for label, val in (
        ("base_monthly", base_monthly),
        ("price_per_customer", price_per_customer),
        ("price_per_extra_user", price_per_extra_user),
    ):
        if val < 0:
            raise ValueError(f"{label} negatif olamaz")


def _ranges_overlap(min_a: int, max_a: int | None, min_b: int, max_b: int | None) -> bool:
    hi_a = max_a if max_a is not None else 10**9
    hi_b = max_b if max_b is not None else 10**9
    return not (hi_a < min_b or min_a > hi_b)


def _assert_no_tier_overlap(
    country_code: str,
    tier_id: int,
    min_customers: int,
    max_customers: int | None,
) -> None:
    others = fetch_all(
        """
        SELECT id, tier_key, min_customers, max_customers
        FROM public.pricing_tiers
        WHERE country_code = %s AND id <> %s AND is_active = TRUE
        """,
        (country_code, tier_id),
    )
    for o in others:
        if _ranges_overlap(
            min_customers,
            max_customers,
            int(o["min_customers"]),
            o["max_customers"],
        ):
            raise ValueError(
                f"kademe aralığı çakışıyor: {o['tier_key']} ile örtüşme var"
            )


@bp.route("/pricing")
@platform_pricing_admin
def pricing_page():
    return render_template(
        "admin/pricing.html",
        username=getattr(current_user, "username", "") or "",
    )


@bp.route("/api/pricing/regions")
@platform_pricing_admin
def api_pricing_regions():
    rows = fetch_all(
        """
        SELECT country_code, currency, name, is_active, sort_order
        FROM public.pricing_regions
        ORDER BY sort_order, country_code
        """
    )
    return jsonify({"ok": True, "regions": rows, "n": len(rows)})


@bp.route("/api/pricing/tiers")
@platform_pricing_admin
def api_pricing_tiers():
    ensure_pricing_display_columns_once()
    try:
        cc = _country_param()
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    try:
        rate, _src = get_usd_try_rate()
    except ValueError:
        rate = None
    rows = fetch_all(
        """
        SELECT id, country_code, currency, tier_key, display_name,
               min_customers, max_customers,
               base_monthly, price_per_customer,
               included_users, price_per_extra_user,
               dolar_fiyat, dolar_fiyat_manuel, indirim_yuzde,
               sort_order, is_active
        FROM public.pricing_tiers
        WHERE country_code = %s
        ORDER BY sort_order, min_customers, id
        """,
        (cc,),
    )
    return jsonify(
        {
            "ok": True,
            "country_code": cc,
            "tiers": [_tier_row_to_json(r, rate) for r in rows],
            "n": len(rows),
        }
    )


@bp.route("/api/pricing/tiers/<int:tier_id>", methods=["PUT"])
@platform_pricing_admin
def api_pricing_tier_update(tier_id: int):
    ensure_pricing_display_columns_once()
    body = request.get_json(silent=True) or {}
    row = fetch_one(
        """
        SELECT id, country_code, currency, tier_key,
               base_monthly, dolar_fiyat, dolar_fiyat_manuel, indirim_yuzde
        FROM public.pricing_tiers
        WHERE id = %s
        """,
        (tier_id,),
    )
    if not row:
        return jsonify({"ok": False, "mesaj": "Kademe bulunamadı."}), 404
    try:
        display_name = str(body.get("display_name") or "").strip()
        min_customers = _parse_int_nonneg("min_customers", body.get("min_customers"))
        max_customers = _parse_int_nonneg(
            "max_customers", body.get("max_customers"), allow_null=True
        )
        base_monthly = _parse_decimal("base_monthly", body.get("base_monthly"))
        price_per_customer = _parse_decimal(
            "price_per_customer", body.get("price_per_customer"), places=4
        )
        included_users = _parse_int_nonneg("included_users", body.get("included_users"))
        price_per_extra_user = _parse_decimal(
            "price_per_extra_user", body.get("price_per_extra_user")
        )
        is_active = bool(body.get("is_active", True))
        if "indirim_yuzde" in body:
            indirim_yuzde = parse_indirim(body.get("indirim_yuzde"))
        else:
            indirim_yuzde = (
                None if row.get("indirim_yuzde") is None else Decimal(str(row["indirim_yuzde"]))
            )
        dolar_fiyat, dolar_manuel = resolve_dolar_on_save(
            old_base=Decimal(str(row["base_monthly"])),
            new_base=base_monthly,
            old_dolar=None if row.get("dolar_fiyat") is None else Decimal(str(row["dolar_fiyat"])),
            old_manuel=bool(row.get("dolar_fiyat_manuel")),
            currency=str(row["currency"]),
            submitted_dolar=parse_dolar(body.get("dolar_fiyat")) if body.get("dolar_edited") else None,
            dolar_edited=bool(body.get("dolar_edited")),
        )
        _validate_tier_fields(
            min_customers=min_customers,
            max_customers=max_customers,
            base_monthly=base_monthly,
            price_per_customer=price_per_customer,
            included_users=included_users,
            price_per_extra_user=price_per_extra_user,
            display_name=display_name,
        )
        if is_active:
            _assert_no_tier_overlap(
                row["country_code"], tier_id, min_customers, max_customers
            )
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400

    execute(
        """
        UPDATE public.pricing_tiers
        SET display_name = %s,
            min_customers = %s,
            max_customers = %s,
            base_monthly = %s,
            price_per_customer = %s,
            included_users = %s,
            price_per_extra_user = %s,
            dolar_fiyat = %s,
            dolar_fiyat_manuel = %s,
            indirim_yuzde = %s,
            is_active = %s,
            updated_at = NOW()
        WHERE id = %s
        """,
        (
            display_name,
            min_customers,
            max_customers,
            str(base_monthly),
            str(price_per_customer),
            included_users,
            str(price_per_extra_user),
            None if dolar_fiyat is None else str(dolar_fiyat),
            bool(dolar_manuel),
            None if indirim_yuzde is None else str(indirim_yuzde),
            is_active,
            tier_id,
        ),
    )
    updated = fetch_one(
        """
        SELECT id, country_code, currency, tier_key, display_name,
               min_customers, max_customers,
               base_monthly, price_per_customer,
               included_users, price_per_extra_user,
               dolar_fiyat, dolar_fiyat_manuel, indirim_yuzde,
               sort_order, is_active
        FROM public.pricing_tiers
        WHERE id = %s
        """,
        (tier_id,),
    )
    invalidate_public_pricing_cache(row["country_code"])
    try:
        saved_rate, _src = get_usd_try_rate()
    except ValueError:
        saved_rate = None
    return jsonify({"ok": True, "tier": _tier_row_to_json(updated, saved_rate)})


@bp.route("/api/pricing/overage", methods=["GET", "PUT"])
@platform_pricing_admin
def api_pricing_overage():
    try:
        cc = _country_param()
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400

    if request.method == "GET":
        row = fetch_one(
            """
            SELECT id, country_code, tier_key,
                   threshold_customers, pack_size, pack_price,
                   currency, is_active
            FROM public.pricing_overage_rules
            WHERE country_code = %s
            ORDER BY tier_key
            LIMIT 1
            """,
            (cc,),
        )
        if not row:
            return jsonify({"ok": True, "country_code": cc, "overage": None})
        return jsonify(
            {
                "ok": True,
                "country_code": cc,
                "overage": {
                    "id": row["id"],
                    "country_code": row["country_code"],
                    "tier_key": row["tier_key"],
                    "threshold_customers": row["threshold_customers"],
                    "pack_size": row["pack_size"],
                    "pack_price": float(row["pack_price"]),
                    "currency": row["currency"],
                    "is_active": bool(row["is_active"]),
                },
            }
        )

    body = request.get_json(silent=True) or {}
    row = fetch_one(
        """
        SELECT id, country_code, tier_key, currency
        FROM public.pricing_overage_rules
        WHERE country_code = %s
        ORDER BY tier_key
        LIMIT 1
        """,
        (cc,),
    )
    if not row:
        return jsonify({"ok": False, "mesaj": "Overage kuralı bulunamadı."}), 404
    try:
        threshold = _parse_int_nonneg("threshold_customers", body.get("threshold_customers"))
        pack_size = _parse_int_nonneg("pack_size", body.get("pack_size"))
        if pack_size <= 0:
            raise ValueError("pack_size pozitif olmalı")
        pack_price = _parse_decimal("pack_price", body.get("pack_price"))
        is_active = bool(body.get("is_active", True))
        if pack_price < 0 or threshold < 0:
            raise ValueError("negatif değer olamaz")
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400

    execute(
        """
        UPDATE public.pricing_overage_rules
        SET threshold_customers = %s,
            pack_size = %s,
            pack_price = %s,
            is_active = %s,
            updated_at = NOW()
        WHERE id = %s
        """,
        (threshold, pack_size, str(pack_price), is_active, row["id"]),
    )
    updated = fetch_one(
        """
        SELECT id, country_code, tier_key,
               threshold_customers, pack_size, pack_price,
               currency, is_active
        FROM public.pricing_overage_rules
        WHERE id = %s
        """,
        (row["id"],),
    )
    invalidate_public_pricing_cache(cc)
    return jsonify(
        {
            "ok": True,
            "overage": {
                "id": updated["id"],
                "country_code": updated["country_code"],
                "tier_key": updated["tier_key"],
                "threshold_customers": updated["threshold_customers"],
                "pack_size": updated["pack_size"],
                "pack_price": float(updated["pack_price"]),
                "currency": updated["currency"],
                "is_active": bool(updated["is_active"]),
            },
        }
    )


@bp.route("/api/pricing/preview", methods=["POST"])
@platform_pricing_admin
def api_pricing_preview():
    body = request.get_json(silent=True) or {}
    try:
        cc = str(body.get("country_code") or "TR").strip().upper()
        musteri = _parse_int_nonneg("musteri_sayisi", body.get("musteri_sayisi"))
        kullanici = _parse_int_nonneg("kullanici_sayisi", body.get("kullanici_sayisi"))
        bill = calculate_tenant_bill(cc, musteri, kullanici)
        return jsonify({"ok": True, "bill": bill})
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except PricingEngineError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except Exception:
        logger.exception("api_pricing_preview")
        return jsonify({"ok": False, "mesaj": "Önizleme hesaplanamadı."}), 500


def _after_rate_change(rate: Decimal) -> int:
    ensure_pricing_display_columns_once()
    updated = recalculate_auto_dolar(rate)
    invalidate_public_pricing_cache()
    invalidate_public_module_pricing_cache()
    return updated


@bp.route("/api/pricing/usd-try", methods=["GET"])
@platform_pricing_admin
def api_pricing_usd_try():
    ensure_pricing_display_columns_once()
    try:
        return jsonify({"ok": True, **usd_try_status()})
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/pricing/usd-try", methods=["PUT"])
@platform_pricing_admin
def api_pricing_usd_try_save():
    """Elle girilen USD/TRY. Otomatik dolar fiyatlarını yeniden hesaplar."""
    body = request.get_json(silent=True) or {}
    try:
        try:
            rate = Decimal(str(body.get("rate")))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("kur geçersiz")
        if rate <= 0:
            raise ValueError("kur pozitif olmalı")
        saved = set_usd_try_rate(rate, "manual")
        updated = _after_rate_change(saved)
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    status = usd_try_status()
    return jsonify({"ok": True, "updated_rows": updated, **status})


@bp.route("/api/pricing/usd-try/refresh", methods=["POST"])
@platform_pricing_admin
def api_pricing_usd_try_refresh():
    """open.er-api.com üzerinden USD/TRY çeker; manuel dolar satırlarına dokunmaz."""
    ensure_pricing_display_columns_once()
    res = fetch_and_store_exchange_rates()
    if not res.get("ok"):
        return jsonify({"ok": False, "mesaj": f"Kur alınamadı: {res.get('error')}"}), 502
    try:
        rate, _src = get_usd_try_rate()
        updated = _after_rate_change(rate)
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    status = usd_try_status()
    return jsonify({"ok": True, "updated_rows": updated, **status})
