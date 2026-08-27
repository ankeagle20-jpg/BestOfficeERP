# -*- coding: utf-8 -*-
"""Payafin platform fiyatlandırma motoru — Flask/ödeme bağımsız saf hesaplama."""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from db import fetch_all, fetch_one
from services.exchange_rate_service import get_exchange_rate
from services.discount_campaign_service import find_active_campaign

_Q2 = Decimal("0.01")
_Q4 = Decimal("0.0001")


class PricingEngineError(RuntimeError):
    """Fiyatlandırma hesaplaması durdu (pasif/bilinmeyen ülke, bozuk kademe verisi)."""


def _d(value: Any, quant: Decimal = _Q2) -> Decimal:
    return Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)


def _money(amount: Decimal, currency: str) -> str:
    cur = (currency or "").upper()
    if cur == "TRY":
        s = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"₺{s}"
    return f"{amount:,.2f} {cur}"


def _load_region(country_code: str) -> dict:
    cc = str(country_code or "").strip().upper()
    if not cc:
        raise PricingEngineError("country_code gerekli")
    row = fetch_one(
        """
        SELECT country_code, currency, name, is_active
        FROM public.pricing_regions
        WHERE country_code = %s
        """,
        (cc,),
    )
    if not row:
        raise PricingEngineError(f"bilinmeyen ülke: {cc}")
    if not row.get("is_active"):
        raise PricingEngineError(f"pasif ülke: {cc}")
    return row


def _has_country_tiers(country_code: str) -> bool:
    """Belirtilen ülke için pricing_tiers tablosunda aktif kayıt olup olmadığını kontrol eder."""
    cc = str(country_code).strip().upper()
    row = fetch_one(
        """
        SELECT 1 AS ok
        FROM public.pricing_tiers
        WHERE country_code = %s AND is_active = TRUE
        LIMIT 1
        """,
        (cc,),
    )
    return bool(row and row.get("ok"))


def _match_tier(country_code: str, musteri_sayisi: int) -> dict:
    cc = str(country_code).strip().upper()
    n = int(musteri_sayisi)
    if n < 0:
        raise PricingEngineError("musteri_sayisi negatif olamaz")
    if n == 0:
        row = fetch_one(
            """
            SELECT id, country_code, currency, tier_key, display_name,
                   min_customers, max_customers,
                   base_monthly, price_per_customer,
                   included_users, price_per_extra_user,
                   sort_order, is_active
            FROM public.pricing_tiers
            WHERE country_code = %s
              AND is_active = TRUE
              AND tier_key = 'starter'
            """,
            (cc,),
        )
        if not row:
            raise PricingEngineError(
                f"N=0 için starter kademesi bulunamadı (country={cc})"
            )
        return row

    rows = fetch_all(
        """
        SELECT id, country_code, currency, tier_key, display_name,
               min_customers, max_customers,
               base_monthly, price_per_customer,
               included_users, price_per_extra_user,
               sort_order, is_active
        FROM public.pricing_tiers
        WHERE country_code = %s
          AND is_active = TRUE
          AND min_customers <= %s
          AND (max_customers IS NULL OR max_customers >= %s)
        ORDER BY sort_order, min_customers, id
        """,
        (cc, n, n),
    )
    if not rows:
        raise PricingEngineError(
            f"musteri_sayisi={n} için kademe bulunamadı (country={cc})"
        )
    if len(rows) > 1:
        keys = [r["tier_key"] for r in rows]
        raise PricingEngineError(
            f"çakışan kademe satırları (country={cc}, N={n}): {keys}"
        )
    return rows[0]


def _load_overage(country_code: str, tier_key: str) -> dict | None:
    return fetch_one(
        """
        SELECT country_code, tier_key, threshold_customers, pack_size, pack_price, currency, is_active
        FROM public.pricing_overage_rules
        WHERE country_code = %s
          AND tier_key = %s
          AND is_active = TRUE
        """,
        (country_code, tier_key),
    )


def calculate_tenant_bill(
    country_code: str,
    musteri_sayisi: int,
    kullanici_sayisi: int,
) -> dict:
    """Aylık kiracı faturasını hesapla (public.pricing_* kaynaklı, fail-closed).

    1. Manuel Override Kontrolü: İstenen ülke için pricing_tiers'ta kayıt varsa doğrudan kullanılır.
    2. Dinamik Türetme: Yoksa US master kademesi yüklenip güncel döviz kuruyla hedef para birimine çevrilir.
    3. Kampanya Uygulaması: discount_campaigns tablosunda aktif geçerli indirim varsa faturaya uygulanır.
    """
    region = _load_region(country_code)
    cc = region["country_code"]
    target_currency = region["currency"]
    n = int(musteri_sayisi)
    u = int(kullanici_sayisi)
    if n < 0:
        raise PricingEngineError("musteri_sayisi negatif olamaz")
    if u < 0:
        raise PricingEngineError("kullanici_sayisi negatif olamaz")

    # 1. Aşama: Manuel Override vs Master US Türetme
    has_override = _has_country_tiers(cc)
    is_derived = False
    exchange_rate: Decimal | None = None

    if has_override:
        # Ülkeye özel bağımsız fiyat satırları mevcut (örn: TR)
        tier = _match_tier(cc, n)
        if tier["currency"] != target_currency:
            raise PricingEngineError(
                f"kademe para birimi uyuşmazlığı ({tier['currency']} != {target_currency})"
            )
        base = _d(tier["base_monthly"])
        price_per_customer = _d(tier["price_per_customer"], _Q4)
        price_per_extra_user = _d(tier["price_per_extra_user"])
        overage_rule = _load_overage(cc, tier["tier_key"])
    else:
        # Ülkeye özel satır yok -> US master'dan dinamik döviz kuru dönüşümü
        is_derived = True
        exchange_rate = get_exchange_rate("USD", target_currency)
        us_tier = _match_tier("US", n)

        tier = dict(us_tier)
        tier["country_code"] = cc
        tier["currency"] = target_currency

        base = (Decimal(str(us_tier["base_monthly"])) * exchange_rate).quantize(_Q2, rounding=ROUND_HALF_UP)
        price_per_customer = (Decimal(str(us_tier["price_per_customer"])) * exchange_rate).quantize(_Q4, rounding=ROUND_HALF_UP)
        price_per_extra_user = (Decimal(str(us_tier["price_per_extra_user"])) * exchange_rate).quantize(_Q2, rounding=ROUND_HALF_UP)

        us_overage = _load_overage("US", us_tier["tier_key"])
        if us_overage is not None:
            overage_rule = dict(us_overage)
            overage_rule["country_code"] = cc
            overage_rule["currency"] = target_currency
            overage_rule["pack_price"] = (Decimal(str(us_overage["pack_price"])) * exchange_rate).quantize(_Q2, rounding=ROUND_HALF_UP)
        else:
            overage_rule = None

    customer_fee = (Decimal(n) * price_per_customer).quantize(_Q2, rounding=ROUND_HALF_UP)

    overage_packs = 0
    overage_fee = Decimal("0.00")
    if overage_rule is not None:
        threshold = int(overage_rule["threshold_customers"])
        pack_size = int(overage_rule["pack_size"])
        if pack_size <= 0:
            raise PricingEngineError("overage pack_size pozitif olmalı")
        pack_price = _d(overage_rule["pack_price"])
        excess = max(0, n - threshold)
        if excess > 0:
            overage_packs = int(math.ceil(excess / pack_size))
            overage_fee = (Decimal(overage_packs) * pack_price).quantize(
                _Q2, rounding=ROUND_HALF_UP
            )

    included_users = int(tier["included_users"])
    extra_users = max(0, u - included_users)
    user_fee = (Decimal(extra_users) * price_per_extra_user).quantize(
        _Q2, rounding=ROUND_HALF_UP
    )

    # Brüt tutar (indirim öncesi)
    raw_total = (base + customer_fee + overage_fee + user_fee).quantize(
        _Q2, rounding=ROUND_HALF_UP
    )

    # 2. Aşama: Aktif Kampanya Kontrolü & İndirim
    campaign = find_active_campaign(country_code=cc, applies_to_target="core_erp")
    discount_amount = Decimal("0.00")
    if campaign is not None:
        disc_pct = Decimal(str(campaign["discount_percent"]))
        discount_amount = (raw_total * (disc_pct / Decimal("100"))).quantize(
            _Q2, rounding=ROUND_HALF_UP
        )

    final_total = max(Decimal("0.00"), raw_total - discount_amount)

    lines: list[dict[str, str]] = [
        {
            "key": "base",
            "label": f"Taban ücret ({tier['display_name']})",
            "amount": str(base),
            "text": f"Taban ücret: {_money(base, target_currency)}",
        },
    ]
    if n > 0:
        lines.append(
            {
                "key": "customers",
                "label": f"{n} müşteri × {_money(price_per_customer, target_currency)}",
                "amount": str(customer_fee),
                "text": (
                    f"{n} müşteri × {_money(price_per_customer, target_currency)}: "
                    f"{_money(customer_fee, target_currency)}"
                ),
            }
        )
    elif customer_fee == 0:
        lines.append(
            {
                "key": "customers",
                "label": "Müşteri ücreti (0 müşteri)",
                "amount": "0.00",
                "text": f"Müşteri ücreti (0 müşteri): {_money(Decimal('0.00'), target_currency)}",
            }
        )
    if overage_packs > 0 and overage_rule is not None:
        pack_price = _d(overage_rule["pack_price"])
        pack_size = int(overage_rule["pack_size"])
        lines.append(
            {
                "key": "overage",
                "label": (
                    f"Ek müşteri paketi ({overage_packs}×{pack_size}, "
                    f">{overage_rule['threshold_customers']} müşteri)"
                ),
                "amount": str(overage_fee),
                "text": (
                    f"Ek müşteri paketi ({overage_packs} paket × "
                    f"{_money(pack_price, target_currency)}): {_money(overage_fee, target_currency)}"
                ),
            }
        )
    if extra_users > 0:
        lines.append(
            {
                "key": "users",
                "label": f"Ek kullanıcı ({extra_users}×{_money(price_per_extra_user, target_currency)})",
                "amount": str(user_fee),
                "text": (
                    f"Ek kullanıcı ({extra_users} × {_money(price_per_extra_user, target_currency)}): "
                    f"{_money(user_fee, target_currency)}"
                ),
            }
        )

    if campaign is not None and discount_amount > 0:
        disc_pct_str = f"{campaign['discount_percent']:g}"
        lines.append(
            {
                "key": "subtotal",
                "label": "Ara toplam",
                "amount": str(raw_total),
                "text": f"Ara toplam: {_money(raw_total, target_currency)}",
            }
        )
        lines.append(
            {
                "key": "campaign_discount",
                "label": f"Kampanya indirimi ({campaign['name']} - %{disc_pct_str})",
                "amount": f"-{discount_amount}",
                "text": (
                    f"Kampanya indirimi ({campaign['name']} - %{disc_pct_str}): "
                    f"-{_money(discount_amount, target_currency)}"
                ),
            }
        )

    lines.append(
        {
            "key": "total",
            "label": "Toplam aylık",
            "amount": str(final_total),
            "text": f"Toplam aylık: {_money(final_total, target_currency)}",
        }
    )

    applied_campaign_info = None
    if campaign is not None:
        applied_campaign_info = {
            "id": campaign["id"],
            "name": campaign["name"],
            "code": campaign.get("code"),
            "discount_percent": float(campaign["discount_percent"]),
        }

    return {
        "country_code": cc,
        "currency": target_currency,
        "tier_key": tier["tier_key"],
        "tier_name": tier["display_name"],
        "musteri_sayisi": n,
        "kullanici_sayisi": u,
        "base_monthly": float(base),
        "customer_fee": float(customer_fee),
        "overage_packs": overage_packs,
        "overage_fee": float(overage_fee),
        "included_users": included_users,
        "extra_users": extra_users,
        "user_fee": float(user_fee),
        "total_monthly": float(final_total),
        "lines": lines,
        "raw_total_monthly": float(raw_total),
        "is_derived": is_derived,
        "exchange_rate": float(exchange_rate) if exchange_rate is not None else None,
        "applied_campaign": applied_campaign_info,
        "discount_amount": float(discount_amount),
    }
