# -*- coding: utf-8 -*-
"""Payafin public modül fiyatlandırma API — bellek içi cache + salt-okuma yükleme."""
from __future__ import annotations

from cache_utils import simple_cache_get, simple_cache_invalidate, simple_cache_set
from db import fetch_all, fetch_one

PUBLIC_MODULE_PRICING_CACHE_TTL_SEC = 60.0
# v2: included_monthly_appointments / included_personnel public alanları
_PUBLIC_CACHE_PREFIX = "module_pricing:public:v2:"

# Personel + Randevu (public gösterim)
PUBLIC_MODULE_KEYS = frozenset({"personnel", "randevu"})


def public_module_pricing_cache_key(module_key: str, country_code: str) -> str:
    mk = str(module_key or "").strip()
    cc = str(country_code or "").strip().upper()
    return f"{_PUBLIC_CACHE_PREFIX}{mk}:{cc}"


def invalidate_public_module_pricing_cache(
    module_key: str, country_code: str
) -> None:
    """Admin kademe güncellemesinden sonra ilgili modül+ülke cache'ini sil."""
    mk = str(module_key or "").strip()
    cc = str(country_code or "").strip().upper()
    if not mk or not cc:
        return
    simple_cache_invalidate(public_module_pricing_cache_key(mk, cc))


def _self_serve_public(row: dict) -> dict:
    return {
        "tier_key": row["tier_key"],
        "display_name": row["display_name"],
        "currency": row["currency"],
        "base_monthly": float(row["base_monthly"]),
        "price_per_personnel": float(row["price_per_personnel"]),
        "max_personnel": (
            None if row["max_personnel"] is None else int(row["max_personnel"])
        ),
        "included_branches": int(row["included_branches"]),
        "price_per_extra_branch": float(row["price_per_extra_branch"]),
        "included_monthly_appointments": (
            None
            if row.get("included_monthly_appointments") is None
            else int(row["included_monthly_appointments"])
        ),
        "included_personnel": (
            None
            if row.get("included_personnel") is None
            else int(row["included_personnel"])
        ),
        "annual_discount_months": int(row["annual_discount_months"]),
        "contact_sales": False,
    }


def _enterprise_public(row: dict) -> dict:
    return {
        "display_name": row["display_name"],
        "contact_sales": True,
    }


def _load_public_module_pricing_from_db(
    module_key: str, country_code: str
) -> dict | None:
    mk = str(module_key or "").strip()
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

    self_serve_rows = fetch_all(
        """
        SELECT tier_key, display_name, currency,
               base_monthly, price_per_personnel, max_personnel,
               included_branches, price_per_extra_branch,
               included_monthly_appointments, included_personnel,
               annual_discount_months, sort_order
        FROM public.module_pricing_tiers
        WHERE module_key = %s
          AND country_code = %s
          AND is_active = TRUE
          AND is_contact_sales = FALSE
        ORDER BY sort_order, id
        """,
        (mk, cc),
    ) or []

    enterprise_row = fetch_one(
        """
        SELECT display_name, sort_order
        FROM public.module_pricing_tiers
        WHERE module_key = %s
          AND country_code = %s
          AND is_active = TRUE
          AND is_contact_sales = TRUE
        ORDER BY sort_order, id
        LIMIT 1
        """,
        (mk, cc),
    )

    tiers = [_self_serve_public(r) for r in self_serve_rows]
    if enterprise_row:
        tiers.append(_enterprise_public(enterprise_row))

    if not tiers:
        return None

    return {
        "ok": True,
        "module_key": mk,
        "country_code": cc,
        "currency": region["currency"],
        "tiers": tiers,
        "n": len(tiers),
    }


def get_public_module_pricing(
    module_key: str, country_code: str
) -> dict | None:
    """
    Aktif modül fiyatlandırmasını döner (public alanlar).
    Bilinmeyen modül → ValueError.
    Bilinmeyen/pasif ülke veya boş kademe → None (endpoint 404).
    """
    mk = str(module_key or "").strip()
    if mk not in PUBLIC_MODULE_KEYS:
        raise ValueError("geçersiz module")

    cc = str(country_code or "").strip().upper()
    if not cc or len(cc) != 2 or not cc.isalpha():
        raise ValueError("geçersiz country")

    key = public_module_pricing_cache_key(mk, cc)
    cached = simple_cache_get(key, max_age_sec=PUBLIC_MODULE_PRICING_CACHE_TTL_SEC)
    if cached is not None:
        return cached

    payload = _load_public_module_pricing_from_db(mk, cc)
    if payload is None:
        return None

    simple_cache_set(key, payload)
    return payload
