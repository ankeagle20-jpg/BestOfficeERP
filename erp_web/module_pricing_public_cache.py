# -*- coding: utf-8 -*-
"""Payafin public modül fiyatlandırma API — bellek içi cache + salt-okuma yükleme."""
from __future__ import annotations

import datetime
from decimal import Decimal, ROUND_HALF_UP

from cache_utils import (
    simple_cache_get,
    simple_cache_invalidate,
    simple_cache_invalidate_prefix,
    simple_cache_set,
)
from db import fetch_all, fetch_one
from services.discount_campaign_service import find_active_campaign
from services.exchange_rate_service import get_exchange_rate

PUBLIC_MODULE_PRICING_CACHE_TTL_SEC = 60.0
_PUBLIC_CACHE_PREFIX = "module_pricing:public:v2:"

PUBLIC_MODULE_KEYS = frozenset({"personnel", "randevu"})

_Q2 = Decimal("0.01")
_Q4 = Decimal("0.0001")


def public_module_pricing_cache_key(module_key: str, country_code: str) -> str:
    mk = str(module_key or "").strip()
    cc = str(country_code or "").strip().upper()
    return f"{_PUBLIC_CACHE_PREFIX}{mk}:{cc}"


def invalidate_public_module_pricing_cache(
    module_key: str | None = None, country_code: str | None = None
) -> None:
    """Admin kademe/kampanya güncellemesinden sonra ilgili modül+ülke cache'ini sil."""
    if module_key and country_code:
        mk = str(module_key).strip()
        cc = str(country_code).strip().upper()
        simple_cache_invalidate(public_module_pricing_cache_key(mk, cc))
    elif module_key:
        simple_cache_invalidate_prefix(f"{_PUBLIC_CACHE_PREFIX}{str(module_key).strip()}:")
    else:
        simple_cache_invalidate_prefix(_PUBLIC_CACHE_PREFIX)


def _apply_disc(val: Decimal, disc_pct: Decimal, quant: Decimal) -> Decimal:
    """Yüzdelik indirim uygular."""
    multiplier = Decimal("1") - (disc_pct / Decimal("100"))
    return (val * multiplier).quantize(quant, rounding=ROUND_HALF_UP)


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

    target_currency = str(region["currency"]).upper()

    # 1. Aşama: Override kontrolü
    has_override_tier = fetch_one(
        """
        SELECT 1 FROM public.module_pricing_tiers
        WHERE module_key = %s AND country_code = %s AND is_active = TRUE
        LIMIT 1
        """,
        (mk, cc),
    )

    is_derived = False
    exchange_rate: Decimal | None = None

    if has_override_tier:
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
    else:
        # US Master'dan türet
        is_derived = True
        exchange_rate = get_exchange_rate("USD", target_currency)
        us_self_serve = fetch_all(
            """
            SELECT tier_key, display_name, currency,
                   base_monthly, price_per_personnel, max_personnel,
                   included_branches, price_per_extra_branch,
                   included_monthly_appointments, included_personnel,
                   annual_discount_months, sort_order
            FROM public.module_pricing_tiers
            WHERE module_key = %s
              AND country_code = 'US'
              AND is_active = TRUE
              AND is_contact_sales = FALSE
            ORDER BY sort_order, id
            """,
            (mk,),
        ) or []

        self_serve_rows = []
        for r in us_self_serve:
            tr_dict = dict(r)
            tr_dict["currency"] = target_currency
            tr_dict["base_monthly"] = (Decimal(str(r["base_monthly"])) * exchange_rate).quantize(_Q2, rounding=ROUND_HALF_UP)
            tr_dict["price_per_personnel"] = (Decimal(str(r["price_per_personnel"])) * exchange_rate).quantize(_Q4, rounding=ROUND_HALF_UP)
            tr_dict["price_per_extra_branch"] = (Decimal(str(r["price_per_extra_branch"])) * exchange_rate).quantize(_Q2, rounding=ROUND_HALF_UP)
            self_serve_rows.append(tr_dict)

        enterprise_row = fetch_one(
            """
            SELECT display_name, sort_order
            FROM public.module_pricing_tiers
            WHERE module_key = %s
              AND country_code = 'US'
              AND is_active = TRUE
              AND is_contact_sales = TRUE
            ORDER BY sort_order, id
            LIMIT 1
            """,
            (mk,),
        )

    # 2. Aşama: Aktif Kampanya Kontrolü
    campaign = find_active_campaign(country_code=cc, applies_to_target=f"module:{mk}")
    campaign_info = None
    disc_pct: Decimal | None = None

    if campaign is not None:
        disc_pct = Decimal(str(campaign["discount_percent"]))
        now = datetime.datetime.now(datetime.timezone.utc)
        end_d = campaign["end_date"]
        days_left = max(0, (end_d - now).days) if end_d else None

        campaign_info = {
            "id": campaign["id"],
            "name": campaign["name"],
            "code": campaign.get("code"),
            "discount_percent": float(disc_pct),
            "days_left": days_left,
            "end_date": end_d.isoformat() if hasattr(end_d, "isoformat") else str(end_d),
        }

    # Kademeleri hazırla
    tiers = []
    for r in self_serve_rows:
        raw_base = Decimal(str(r["base_monthly"]))
        raw_ppp = Decimal(str(r["price_per_personnel"]))
        raw_extra_br = Decimal(str(r["price_per_extra_branch"]))

        if disc_pct is not None and disc_pct > 0:
            final_base = _apply_disc(raw_base, disc_pct, _Q2)
            final_ppp = _apply_disc(raw_ppp, disc_pct, _Q4)
            final_extra_br = _apply_disc(raw_extra_br, disc_pct, _Q2)
            has_discount = True
        else:
            final_base = raw_base
            final_ppp = raw_ppp
            final_extra_br = raw_extra_br
            has_discount = False

        tiers.append({
            "tier_key": r["tier_key"],
            "display_name": r["display_name"],
            "currency": r.get("currency", target_currency),
            "base_monthly": float(final_base),
            "raw_base_monthly": float(raw_base),
            "price_per_personnel": float(final_ppp),
            "raw_price_per_personnel": float(raw_ppp),
            "max_personnel": None if r["max_personnel"] is None else int(r["max_personnel"]),
            "included_branches": int(r["included_branches"]),
            "price_per_extra_branch": float(final_extra_br),
            "raw_price_per_extra_branch": float(raw_extra_br),
            "included_monthly_appointments": (
                None
                if r.get("included_monthly_appointments") is None
                else int(r["included_monthly_appointments"])
            ),
            "included_personnel": (
                None
                if r.get("included_personnel") is None
                else int(r["included_personnel"])
            ),
            "annual_discount_months": int(r["annual_discount_months"]),
            "contact_sales": False,
            "has_discount": has_discount,
        })

    if enterprise_row:
        tiers.append({
            "display_name": enterprise_row["display_name"],
            "contact_sales": True,
            "has_discount": False,
        })

    if not tiers:
        return None

    return {
        "ok": True,
        "module_key": mk,
        "country_code": cc,
        "currency": target_currency,
        "is_derived": is_derived,
        "exchange_rate": float(exchange_rate) if exchange_rate is not None else None,
        "campaign": campaign_info,
        "tiers": tiers,
        "n": len(tiers),
    }


def get_public_module_pricing(
    module_key: str, country_code: str
) -> dict | None:
    """Aktif modül fiyatlandırmasını döner (public alanlar).

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
