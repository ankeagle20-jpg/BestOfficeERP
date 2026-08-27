# -*- coding: utf-8 -*-
"""Payafin public fiyatlandırma API — bellek içi cache + salt-okuma yükleme."""
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

PUBLIC_PRICING_CACHE_TTL_SEC = 60.0
_PUBLIC_CACHE_PREFIX = "pricing:public:v1:"

_Q2 = Decimal("0.01")
_Q4 = Decimal("0.0001")


def public_pricing_cache_key(country_code: str) -> str:
    return f"{_PUBLIC_CACHE_PREFIX}{str(country_code or '').strip().upper()}"


def invalidate_public_pricing_cache(country_code: str | None = None) -> None:
    """Admin kademe/overage/kampanya güncellemesinden sonra public fiyat cache'ini sil."""
    if country_code:
        cc = str(country_code).strip().upper()
        simple_cache_invalidate(public_pricing_cache_key(cc))
    else:
        simple_cache_invalidate_prefix(_PUBLIC_CACHE_PREFIX)


def _apply_disc(val: Decimal, disc_pct: Decimal, quant: Decimal) -> Decimal:
    """Yüzdelik indirim uygular."""
    multiplier = Decimal("1") - (disc_pct / Decimal("100"))
    return (val * multiplier).quantize(quant, rounding=ROUND_HALF_UP)


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

    target_currency = str(region["currency"]).upper()

    # 1. Aşama: Override var mı kontrol et
    has_override_tier = fetch_one(
        "SELECT 1 FROM public.pricing_tiers WHERE country_code = %s AND is_active = TRUE LIMIT 1",
        (cc,),
    )

    is_derived = False
    exchange_rate: Decimal | None = None

    if has_override_tier:
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
        ) or []
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
    else:
        # US Master'dan türet
        is_derived = True
        exchange_rate = get_exchange_rate("USD", target_currency)
        us_tier_rows = fetch_all(
            """
            SELECT tier_key, display_name, currency,
                   min_customers, max_customers,
                   base_monthly, price_per_customer,
                   included_users, price_per_extra_user
            FROM public.pricing_tiers
            WHERE country_code = 'US' AND is_active = TRUE
            ORDER BY sort_order, min_customers, id
            """
        ) or []
        tier_rows = []
        for r in us_tier_rows:
            tr_dict = dict(r)
            tr_dict["currency"] = target_currency
            tr_dict["base_monthly"] = (Decimal(str(r["base_monthly"])) * exchange_rate).quantize(_Q2, rounding=ROUND_HALF_UP)
            tr_dict["price_per_customer"] = (Decimal(str(r["price_per_customer"])) * exchange_rate).quantize(_Q4, rounding=ROUND_HALF_UP)
            tr_dict["price_per_extra_user"] = (Decimal(str(r["price_per_extra_user"])) * exchange_rate).quantize(_Q2, rounding=ROUND_HALF_UP)
            tier_rows.append(tr_dict)

        us_overage = fetch_one(
            """
            SELECT tier_key, threshold_customers, pack_size, pack_price, currency
            FROM public.pricing_overage_rules
            WHERE country_code = 'US' AND is_active = TRUE
            ORDER BY tier_key
            LIMIT 1
            """
        )
        if us_overage:
            overage_row = dict(us_overage)
            overage_row["currency"] = target_currency
            overage_row["pack_price"] = (Decimal(str(us_overage["pack_price"])) * exchange_rate).quantize(_Q2, rounding=ROUND_HALF_UP)
        else:
            overage_row = None

    # 2. Aşama: Aktif Kampanya Kontrolü
    campaign = find_active_campaign(country_code=cc, applies_to_target="core_erp")
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

    # Kademeleri zenginleştir
    tiers = []
    for r in tier_rows:
        raw_base = Decimal(str(r["base_monthly"]))
        raw_ppc = Decimal(str(r["price_per_customer"]))
        raw_extra_u = Decimal(str(r["price_per_extra_user"]))

        if disc_pct is not None and disc_pct > 0:
            final_base = _apply_disc(raw_base, disc_pct, _Q2)
            final_ppc = _apply_disc(raw_ppc, disc_pct, _Q4)
            final_extra_u = _apply_disc(raw_extra_u, disc_pct, _Q2)
            has_discount = True
        else:
            final_base = raw_base
            final_ppc = raw_ppc
            final_extra_u = raw_extra_u
            has_discount = False

        tiers.append({
            "tier_key": r["tier_key"],
            "display_name": r["display_name"],
            "min_customers": r["min_customers"],
            "max_customers": r["max_customers"],
            "base_monthly": float(final_base),
            "raw_base_monthly": float(raw_base),
            "price_per_customer": float(final_ppc),
            "raw_price_per_customer": float(raw_ppc),
            "included_users": r["included_users"],
            "price_per_extra_user": float(final_extra_u),
            "raw_price_per_extra_user": float(raw_extra_u),
            "currency": r.get("currency", target_currency),
            "has_discount": has_discount,
        })

    overage_obj = None
    if overage_row:
        raw_pack = Decimal(str(overage_row["pack_price"]))
        if disc_pct is not None and disc_pct > 0:
            final_pack = _apply_disc(raw_pack, disc_pct, _Q2)
            ov_disc = True
        else:
            final_pack = raw_pack
            ov_disc = False

        overage_obj = {
            "tier_key": overage_row["tier_key"],
            "threshold_customers": overage_row["threshold_customers"],
            "pack_size": overage_row["pack_size"],
            "pack_price": float(final_pack),
            "raw_pack_price": float(raw_pack),
            "currency": overage_row.get("currency", target_currency),
            "has_discount": ov_disc,
        }

    return {
        "ok": True,
        "country_code": cc,
        "currency": target_currency,
        "is_derived": is_derived,
        "exchange_rate": float(exchange_rate) if exchange_rate is not None else None,
        "campaign": campaign_info,
        "tiers": tiers,
        "overage": overage_obj,
        "n": len(tiers),
    }


def get_public_pricing(country_code: str) -> dict | None:
    """Aktif ülke fiyatlandırmasını döner.

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
