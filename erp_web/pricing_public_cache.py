# -*- coding: utf-8 -*-
"""Payafin public fiyatlandırma API — bellek içi cache + salt-okuma yükleme."""
from __future__ import annotations

from cache_utils import simple_cache_get, simple_cache_invalidate, simple_cache_set
from db import fetch_all, fetch_one

PUBLIC_PRICING_CACHE_TTL_SEC = 60.0
_PUBLIC_CACHE_PREFIX = "pricing:public:v1:"


def public_pricing_cache_key(country_code: str) -> str:
    return f"{_PUBLIC_CACHE_PREFIX}{str(country_code or '').strip().upper()}"


def invalidate_public_pricing_cache(country_code: str) -> None:
    """Admin kademe/overage güncellemesinden sonra ilgili ülke cache'ini sil."""
    cc = str(country_code or "").strip().upper()
    if not cc:
        return
    simple_cache_invalidate(public_pricing_cache_key(cc))


def _tier_public(row: dict) -> dict:
    return {
        "tier_key": row["tier_key"],
        "display_name": row["display_name"],
        "min_customers": row["min_customers"],
        "max_customers": row["max_customers"],
        "base_monthly": float(row["base_monthly"]),
        "price_per_customer": float(row["price_per_customer"]),
        "included_users": row["included_users"],
        "price_per_extra_user": float(row["price_per_extra_user"]),
        "currency": row["currency"],
    }


def _overage_public(row: dict) -> dict:
    return {
        "tier_key": row["tier_key"],
        "threshold_customers": row["threshold_customers"],
        "pack_size": row["pack_size"],
        "pack_price": float(row["pack_price"]),
        "currency": row["currency"],
    }


def _load_public_pricing_from_db(country_code: str) -> dict | None:
    cc = str(country_code or "").strip().upper()
    region = fetch_one(
        """
        SELECT country_code, currency, name, is_active
        FROM public.pricing_regions
        WHERE country_code = %s
        """,
        (cc,),
    )
    if not region or not region.get("is_active"):
        return None

    tier_rows = fetch_all(
        """
        SELECT tier_key, display_name, currency,
               min_customers, max_customers,
               base_monthly, price_per_customer,
               included_users, price_per_extra_user
        FROM public.pricing_tiers
        WHERE country_code = %s AND is_active = TRUE
        ORDER BY sort_order, min_customers, id
        """,
        (cc,),
    )
    overage_row = fetch_one(
        """
        SELECT tier_key, threshold_customers, pack_size, pack_price, currency
        FROM public.pricing_overage_rules
        WHERE country_code = %s AND is_active = TRUE
        ORDER BY tier_key
        LIMIT 1
        """,
        (cc,),
    )
    tiers = [_tier_public(r) for r in tier_rows]
    return {
        "ok": True,
        "country_code": cc,
        "currency": region["currency"],
        "tiers": tiers,
        "overage": _overage_public(overage_row) if overage_row else None,
        "n": len(tiers),
    }


def get_public_pricing(country_code: str) -> dict | None:
    """
    Aktif ülke fiyatlandırmasını döner.
    Bilinmeyen/pasif ülke → None (endpoint 404).
    """
    cc = str(country_code or "").strip().upper()
    if not cc or len(cc) != 2 or not cc.isalpha():
        raise ValueError("geçersiz country")

    key = public_pricing_cache_key(cc)
    cached = simple_cache_get(key, max_age_sec=PUBLIC_PRICING_CACHE_TTL_SEC)
    if cached is not None:
        return cached

    payload = _load_public_pricing_from_db(cc)
    if payload is None:
        return None

    simple_cache_set(key, payload)
    return payload
