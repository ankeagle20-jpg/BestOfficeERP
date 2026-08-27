# -*- coding: utf-8 -*-
"""Döviz kuru servisi — open.er-api.com ve fallback mekanizmaları."""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any
import requests

from db import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)

# 3. Seviye: Kod içi sert fallback sabit kurları (API ve DB tamamen ulaşılamazsa)
HARD_FALLBACK_RATES: dict[str, dict[str, Decimal]] = {
    "USD": {
        "USD": Decimal("1.0"),
        "TRY": Decimal("34.0"),
        "EUR": Decimal("0.92"),
        "GBP": Decimal("0.78"),
        "AZN": Decimal("1.70"),
        "AED": Decimal("3.6725"),
        "SAR": Decimal("3.75"),
    }
}

API_URL = "https://open.er-api.com/v6/latest/USD"
REQUEST_TIMEOUT_SECONDS = 5


def fetch_and_store_exchange_rates() -> dict[str, Any]:
    """open.er-api.com API'sinden güncel USD kurlarını çeker ve public.exchange_rates tablosuna kaydeder.

    Returns:
        {"ok": True, "rates_count": N, "source": str} veya {"ok": False, "error": str}
    """
    try:
        resp = requests.get(API_URL, timeout=REQUEST_TIMEOUT_SECONDS)
        if resp.status_code != 200:
            logger.warning(f"Döviz kuru API yanıt vermedi (HTTP {resp.status_code})")
            return {"ok": False, "error": f"HTTP {resp.status_code}"}

        data = resp.json()
        if data.get("result") != "success":
            logger.warning(f"Döviz kuru API başarısız sonuç döndürdü: {data.get('error-type')}")
            return {"ok": False, "error": data.get("error-type", "unknown error")}

        base = str(data.get("base_code") or "USD").upper()
        rates = data.get("rates") or {}
        if not rates or not isinstance(rates, dict):
            return {"ok": False, "error": "Geçersiz kurlar verisi"}

        updated_count = 0
        for target, rate_val in rates.items():
            t_curr = str(target).strip().upper()
            if len(t_curr) != 3 or not t_curr.isalpha():
                continue
            try:
                rate_dec = Decimal(str(rate_val))
                if rate_dec <= Decimal("0"):
                    continue
            except (InvalidOperation, TypeError, ValueError):
                continue

            execute(
                """
                INSERT INTO public.exchange_rates (base_currency, target_currency, rate, fetched_at, source)
                VALUES (%s, %s, %s, NOW(), 'open.er-api.com')
                ON CONFLICT (base_currency, target_currency)
                DO UPDATE SET rate = EXCLUDED.rate, fetched_at = EXCLUDED.fetched_at, source = EXCLUDED.source
                """,
                (base, t_curr, str(rate_dec)),
            )
            updated_count += 1

        logger.info(f"Döviz kurları başarıyla güncellendi: {updated_count} kur kaydedildi.")
        return {"ok": True, "rates_count": updated_count, "source": "open.er-api.com"}

    except Exception as e:
        logger.error(f"Döviz kuru API çekim hatası: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


def get_exchange_rate(base_currency: str = "USD", target_currency: str = "TRY") -> Decimal:
    """Belirtilen para birimleri arasındaki döviz kurunu döner (3 katmanlı fallback stratejisi):

    1. Katman: DB'deki en güncel kayıt (public.exchange_rates)
    2. Katman: Kod içi güvenli sabit fallback kurları (HARD_FALLBACK_RATES)
    3. Katman: Fail-closed (Tanımlanamayan / 0 değerler için hata)
    """
    base = str(base_currency or "USD").strip().upper()
    target = str(target_currency or "TRY").strip().upper()

    if base == target:
        return Decimal("1.0")

    # 1. Katman: DB Cache / Kayıt kontrolü
    try:
        row = fetch_one(
            """
            SELECT rate
            FROM public.exchange_rates
            WHERE base_currency = %s AND target_currency = %s
            """,
            (base, target),
        )
        if row and row.get("rate") is not None:
            r = Decimal(str(row["rate"]))
            if r > Decimal("0"):
                return r
    except Exception as e:
        logger.warning(f"DB exchange_rates sorgulanamadı: {e}")

    # 2. Katman: Kod içi sert fallback kontrolü
    fallback_map = HARD_FALLBACK_RATES.get(base, {})
    if target in fallback_map:
        return fallback_map[target]

    # Eğer USD üzerinden dolaylı çapraz kur bulunabiliyorsa
    if base != "USD" and "USD" in HARD_FALLBACK_RATES:
        usd_to_base = HARD_FALLBACK_RATES["USD"].get(base)
        usd_to_target = HARD_FALLBACK_RATES["USD"].get(target)
        if usd_to_base and usd_to_target and usd_to_base > Decimal("0"):
            return (usd_to_target / usd_to_base).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    # 3. Katman: Fail-Closed
    raise ValueError(f"Döviz kuru bulunamadı: {base}/{target}")
