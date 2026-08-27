# -*- coding: utf-8 -*-
"""İndirim kampanyaları sorgulama ve çözümleme servisi."""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal
from typing import Any

from db import fetch_all, fetch_one

logger = logging.getLogger(__name__)


def find_active_campaign(
    country_code: str,
    applies_to_target: str,
    at_time: datetime.datetime | None = None,
) -> dict[str, Any] | None:
    """Belirtilen ülke ve modül/sistem için şu anda geçerli en yüksek öncelikli aktif kampanyayı döner.

    Args:
        country_code: 'TR', 'US' vb.
        applies_to_target: 'core_erp', 'modules', 'module:personel', 'module:randevu' vb.
        at_time: Kontrol edilecek zaman (varsayılan: UTC şimdi).

    Returns:
        dict (id, name, code, discount_percent, applies_to, target_countries, ...) veya None.
    """
    cc = str(country_code or "").strip().upper()
    target = str(applies_to_target or "").strip().lower()

    if not cc or not target:
        return None

    check_time = at_time or datetime.datetime.now(datetime.timezone.utc)

    # Hedef modül/sistem için kabul edilebilir applies_to değerleri listesi
    applies_set = {"all", target}
    if target in ("modules", "module:personel", "module:personnel", "module:randevu"):
        applies_set.add("modules")
    if target == "module:personel":
        applies_set.add("module:personnel")
    elif target == "module:personnel":
        applies_set.add("module:personel")

    applies_list = list(applies_set)

    try:
        row = fetch_one(
            """
            SELECT id, name, code, discount_percent, applies_to,
                   target_countries, start_date, end_date, is_active, priority
            FROM public.discount_campaigns
            WHERE is_active = TRUE
              AND start_date <= %s
              AND end_date >= %s
              AND applies_to = ANY(%s)
              AND (%s = ANY(target_countries) OR 'ALL' = ANY(target_countries))
            ORDER BY priority DESC, discount_percent DESC, id DESC
            LIMIT 1
            """,
            (check_time, check_time, applies_list, cc),
        )
        if not row:
            return None

        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "code": str(row["code"]) if row.get("code") else None,
            "discount_percent": Decimal(str(row["discount_percent"])),
            "applies_to": str(row["applies_to"]),
            "target_countries": list(row["target_countries"] or []),
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "priority": int(row["priority"]),
        }
    except Exception as e:
        logger.warning(f"find_active_campaign hatası (fail-safe None döner): {e}")
        return None
