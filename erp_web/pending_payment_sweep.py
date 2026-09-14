# -*- coding: utf-8 -*-
"""A2.5 / A3.1 — süresi dolmuş pending_payment kiracılarını temizle.

- tenants.status = 'pending_payment' ve created_at < now() - TTL
- İlgili paytr/sent faturaları → void
- platform_signup_intents satırını sil (tenant CASCADE yedek; açık DELETE)
- tenants satırını sil (slug serbest; CASCADE faturaları da siler)

Tetikleme: APScheduler (boot + interval) — her slug-available isteğinde değil.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from db import db, fetch_all

logger = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 24.0


def _ttl_hours() -> float:
    raw = (os.environ.get("PENDING_PAYMENT_TTL_HOURS") or "").strip()
    if not raw:
        return DEFAULT_TTL_HOURS
    try:
        h = float(raw)
    except ValueError:
        return DEFAULT_TTL_HOURS
    if h < 1.0 or h > 168.0:  # 1 saat .. 7 gün
        return DEFAULT_TTL_HOURS
    return h


def sweep_expired_pending_payments(
    *, older_than_hours: float | None = None
) -> dict[str, Any]:
    """Süresi geçmiş pending_payment kayıtlarını void+sil.

    Dönüş: {scanned, voided_invoices, deleted_tenants, skipped, errors[]}
    """
    ttl = float(older_than_hours) if older_than_hours is not None else _ttl_hours()
    if ttl < 0.01:
        ttl = DEFAULT_TTL_HOURS

    out: dict[str, Any] = {
        "scanned": 0,
        "voided_invoices": 0,
        "deleted_intents": 0,
        "deleted_tenants": 0,
        "skipped": 0,
        "errors": [],
        "ttl_hours": ttl,
    }

    try:
        rows = fetch_all(
            """
            SELECT id, slug, created_at
            FROM public.tenants
            WHERE status = 'pending_payment'
              AND created_at < (NOW() - (%s * INTERVAL '1 hour'))
            ORDER BY created_at ASC, id ASC
            LIMIT 200
            """,
            (ttl,),
        )
    except Exception as e:
        logger.exception("sweep_expired_pending_payments list failed")
        out["errors"].append({"phase": "list", "detail": str(e)})
        return out

    out["scanned"] = len(rows)

    for row in rows:
        tid = int(row["id"])
        slug = str(row.get("slug") or "")
        try:
            with db() as conn:
                cur = conn.cursor()
                # Güvenlik: ödenmiş fatura varsa bu tenant'a dokunma
                cur.execute(
                    """
                    SELECT 1 AS ok
                    FROM public.platform_tenant_invoices
                    WHERE tenant_id = %s AND status = 'paid'
                    LIMIT 1
                    """,
                    (tid,),
                )
                if cur.fetchone():
                    out["skipped"] += 1
                    logger.warning(
                        "sweep skip pending_payment with paid invoice tenant_id=%s slug=%s",
                        tid,
                        slug,
                    )
                    continue

                cur.execute(
                    """
                    UPDATE public.platform_tenant_invoices
                    SET status = 'void',
                        updated_at = NOW()
                    WHERE tenant_id = %s
                      AND source = 'paytr'
                      AND status = 'sent'
                    RETURNING id, status
                    """,
                    (tid,),
                )
                void_rows = cur.fetchall() or []
                voided = len(void_rows)
                # Aynı transaction içinde void'u doğrula (sonra tenant silinince CASCADE siler)
                for vr in void_rows:
                    if str(vr.get("status") if isinstance(vr, dict) else vr[1]) != "void":
                        raise RuntimeError(f"void beklenirken status={vr}")

                # A3.1: intent'i tenant silmeden önce açıkça kaldır (CASCADE yedek)
                cur.execute(
                    """
                    DELETE FROM public.platform_signup_intents
                    WHERE tenant_id = %s
                    RETURNING id
                    """,
                    (tid,),
                )
                intent_rows = cur.fetchall() or []
                deleted_intents = len(intent_rows)

                cur.execute(
                    """
                    DELETE FROM public.tenants
                    WHERE id = %s AND status = 'pending_payment'
                    RETURNING id
                    """,
                    (tid,),
                )
                deleted = 1 if cur.fetchone() else 0

            out["voided_invoices"] += int(voided)
            out["deleted_intents"] += int(deleted_intents)
            if deleted:
                out["deleted_tenants"] += 1
                logger.info(
                    "sweep expired pending_payment slug=%s tenant_id=%s voided=%s intents=%s",
                    slug,
                    tid,
                    voided,
                    deleted_intents,
                )
            else:
                out["skipped"] += 1
        except Exception as e:
            logger.exception(
                "sweep failed tenant_id=%s slug=%s", tid, slug
            )
            out["errors"].append(
                {"tenant_id": tid, "slug": slug, "detail": str(e)}
            )

    return out


def run_pending_payment_sweep_job() -> None:
    """APScheduler / boot tick — hata yutmaz loglar."""
    try:
        result = sweep_expired_pending_payments()
        if result.get("scanned"):
            logger.info(
                "pending_payment_sweep scanned=%s deleted=%s intents=%s voided=%s skipped=%s errors=%s",
                result.get("scanned"),
                result.get("deleted_tenants"),
                result.get("deleted_intents"),
                result.get("voided_invoices"),
                result.get("skipped"),
                len(result.get("errors") or []),
            )
    except Exception:
        logger.exception("run_pending_payment_sweep_job failed")
