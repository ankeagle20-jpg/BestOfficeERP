# -*- coding: utf-8 -*-
"""Fiyat ekranı dolar fiyatı ve indirim notu.

Fatura motorları (calculate_tenant_bill / calculate_module_bill) bu alanları okumaz.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from db import execute, fetch_one
from services.exchange_rate_service import clear_exchange_rate_cache

_Q2 = Decimal("0.01")
_Q6 = Decimal("0.000001")


def auto_dolar_fiyat(base: Decimal, currency: str, rate: Decimal) -> Decimal:
    """TRY taban / USDTRY. USD taban zaten dolar olduğu için kendisi kalır."""
    cur = (currency or "").strip().upper()
    if cur == "USD":
        return Decimal(str(base)).quantize(_Q2, rounding=ROUND_HALF_UP)
    if rate <= 0:
        raise ValueError("USD/TRY kuru geçersiz")
    return (Decimal(str(base)) / rate).quantize(_Q2, rounding=ROUND_HALF_UP)


def get_usd_try_rate() -> tuple[Decimal, str | None]:
    row = fetch_one(
        """
        SELECT rate, source, fetched_at
        FROM public.exchange_rates
        WHERE base_currency = 'USD' AND target_currency = 'TRY'
        """
    )
    if not row or row.get("rate") is None:
        raise ValueError("USD/TRY kuru kayıtlı değil")
    rate = Decimal(str(row["rate"]))
    if rate <= 0:
        raise ValueError("USD/TRY kuru geçersiz")
    return rate, (None if row.get("source") is None else str(row["source"]))


def usd_try_status() -> dict:
    row = fetch_one(
        """
        SELECT rate, source, fetched_at
        FROM public.exchange_rates
        WHERE base_currency = 'USD' AND target_currency = 'TRY'
        """
    )
    if not row or row.get("rate") is None:
        raise ValueError("USD/TRY kuru kayıtlı değil")
    fetched = row.get("fetched_at")
    return {
        "rate": float(row["rate"]),
        "source": row.get("source"),
        "fetched_at": fetched.isoformat() if hasattr(fetched, "isoformat") else (
            None if fetched is None else str(fetched)
        ),
    }


def set_usd_try_rate(rate: Decimal, source: str) -> Decimal:
    q = Decimal(str(rate)).quantize(_Q6, rounding=ROUND_HALF_UP)
    if q <= 0:
        raise ValueError("kur pozitif olmalı")
    src = (source or "manual").strip()[:64] or "manual"
    execute(
        """
        INSERT INTO public.exchange_rates (base_currency, target_currency, rate, fetched_at, source)
        VALUES ('USD', 'TRY', %s, NOW(), %s)
        ON CONFLICT (base_currency, target_currency)
        DO UPDATE SET rate = EXCLUDED.rate,
                      fetched_at = EXCLUDED.fetched_at,
                      source = EXCLUDED.source
        """,
        (str(q), src),
    )
    clear_exchange_rate_cache()
    return q


def recalculate_auto_dolar(rate: Decimal) -> int:
    """dolar_fiyat_manuel = false satırları yeni kurla yazar. Manuel satıra dokunmaz."""
    updated = 0
    rate_s = str(Decimal(str(rate)))
    for table in ("public.pricing_tiers", "public.module_pricing_tiers"):
        updated += execute(
            f"""
            UPDATE {table}
            SET dolar_fiyat = ROUND(base_monthly / %s::numeric, 2),
                updated_at = NOW()
            WHERE dolar_fiyat_manuel = FALSE
              AND currency <> 'USD'
            """,
            (rate_s,),
        )
        updated += execute(
            f"""
            UPDATE {table}
            SET dolar_fiyat = ROUND(base_monthly, 2),
                updated_at = NOW()
            WHERE dolar_fiyat_manuel = FALSE
              AND currency = 'USD'
            """,
        )
    return int(updated)


def parse_dolar(raw) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        d = Decimal(str(raw)).quantize(_Q2, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("dolar_fiyat geçersiz")
    if d < 0:
        raise ValueError("dolar_fiyat negatif olamaz")
    return d


def parse_indirim(raw) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        d = Decimal(str(raw)).quantize(_Q2, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("indirim_yuzde geçersiz")
    if d < 0 or d > 100:
        raise ValueError("indirim_yuzde 0–100 arası olmalı")
    return d


def resolve_dolar_on_save(
    *,
    old_base: Decimal,
    new_base: Decimal,
    old_dolar: Decimal | None,
    old_manuel: bool,
    currency: str,
    submitted_dolar: Decimal | None,
    dolar_edited: bool,
) -> tuple[Decimal | None, bool]:
    """Taban değişince otomatik, yalnız dolar değişince sabit, ikisi birden elle."""
    old_q = Decimal(str(old_base)).quantize(_Q2, rounding=ROUND_HALF_UP)
    new_q = Decimal(str(new_base)).quantize(_Q2, rounding=ROUND_HALF_UP)
    base_changed = new_q != old_q
    if dolar_edited and base_changed:
        if submitted_dolar is None:
            raise ValueError("dolar_fiyat gerekli")
        return submitted_dolar, True
    if base_changed:
        rate, _src = get_usd_try_rate()
        return auto_dolar_fiyat(new_q, currency, rate), False
    if dolar_edited:
        if submitted_dolar is None:
            raise ValueError("dolar_fiyat gerekli")
        return submitted_dolar, True
    if old_dolar is None:
        return None, bool(old_manuel)
    return Decimal(str(old_dolar)).quantize(_Q2, rounding=ROUND_HALF_UP), bool(old_manuel)


def display_dolar(row: dict, rate: Decimal | None) -> float | None:
    manuel = bool(row.get("dolar_fiyat_manuel"))
    stored = row.get("dolar_fiyat")
    if manuel and stored is not None:
        return float(Decimal(str(stored)).quantize(_Q2, rounding=ROUND_HALF_UP))
    if rate is None:
        return None if stored is None else float(stored)
    try:
        return float(auto_dolar_fiyat(Decimal(str(row["base_monthly"])), str(row.get("currency") or ""), rate))
    except (InvalidOperation, TypeError, ValueError):
        return None
