# -*- coding: utf-8 -*-
"""Mevcut aktif kiracilara + public (platform sahibi) genis modul entitlement seed.

KRITIK: Mevcut kullanicilarin erisimi bozulmasin diye ilk katalogdaki
tum moduller status='active', billing_mode='included' olarak verilir.
Entitlement tablosu yalniz public semadadir; kiraci DDL'ine yayilmaz.
"""
from __future__ import annotations

import logging

from db import (
    ensure_tenant_module_entitlements_table,
    execute,
    fetch_all,
    fetch_one,
)

logger = logging.getLogger(__name__)

INITIAL_MODULE_KEYS: tuple[str, ...] = (
    "core_erp",
    "crm",
    "randevu",
    "personnel",
    "attendance",
)


def _ensure_public_owner_tenant_row() -> dict:
    """public sema (ana sirket) icin platform katalog satiri.

    public.tenants.schema_name CHECK'i yalnizca tenant_* kabul ederdi;
    public'i de izin verecek sekilde genisletilir (additive).
    """
    try:
        execute("ALTER TABLE public.tenants DROP CONSTRAINT IF EXISTS tenants_schema_format")
        execute(
            """
            ALTER TABLE public.tenants ADD CONSTRAINT tenants_schema_format CHECK (
                schema_name ~ '^tenant_[a-z0-9_]+$' OR schema_name = 'public'
            )
            """
        )
    except Exception as e:
        logger.warning("tenants_schema_format widen: %s", e)

    row = fetch_one(
        """
        SELECT id, slug, schema_name, status
        FROM public.tenants
        WHERE slug = 'public' OR schema_name = 'public'
        LIMIT 1
        """
    )
    if row:
        return row

    execute(
        """
        INSERT INTO public.tenants (slug, schema_name, plan, status, company_name)
        VALUES ('public', 'public', 'included', 'active', 'Platform owner (public schema)')
        ON CONFLICT (slug) DO NOTHING
        """
    )
    row = fetch_one(
        """
        SELECT id, slug, schema_name, status
        FROM public.tenants
        WHERE slug = 'public' OR schema_name = 'public'
        LIMIT 1
        """
    )
    if not row:
        raise RuntimeError("public platform owner tenant row could not be created")
    return row


def _grant_modules_for_tenant(tenant_id: int, tenant_slug: str) -> int:
    inserted = 0
    for module_key in INITIAL_MODULE_KEYS:
        before = fetch_one(
            """
            SELECT 1 AS ok
            FROM public.tenant_module_entitlements
            WHERE tenant_id = %s AND module_key = %s
            """,
            (tenant_id, module_key),
        )
        execute(
            """
            INSERT INTO public.tenant_module_entitlements (
                tenant_id, tenant_slug, module_key, status, billing_mode,
                source_plan, source_reference, metadata
            )
            VALUES (
                %s, %s, %s, 'active', 'included',
                'legacy_backfill', 'sprint1_wide_grant',
                '{"note": "Sprint 1 backfill - mevcut erisimi koru"}'::jsonb
            )
            ON CONFLICT (tenant_id, module_key) DO NOTHING
            """,
            (tenant_id, tenant_slug, module_key),
        )
        after = fetch_one(
            """
            SELECT 1 AS ok
            FROM public.tenant_module_entitlements
            WHERE tenant_id = %s AND module_key = %s
            """,
            (tenant_id, module_key),
        )
        if after and not before:
            inserted += 1
    return inserted


def backfill_tenant_module_entitlements() -> dict:
    ensure_tenant_module_entitlements_table()
    _ensure_public_owner_tenant_row()

    tenants = fetch_all(
        """
        SELECT id, slug, schema_name, status
        FROM public.tenants
        WHERE status = 'active'
        ORDER BY id
        """
    ) or []

    stats = {
        "tenants": 0,
        "modules_per_tenant": len(INITIAL_MODULE_KEYS),
        "inserted": 0,
        "skipped_existing": 0,
        "errors": 0,
        "tenant_slugs": [],
    }

    for t in tenants:
        stats["tenants"] += 1
        tid = int(t["id"])
        slug = str(t["slug"])
        stats["tenant_slugs"].append(slug)
        try:
            inserted = _grant_modules_for_tenant(tid, slug)
            stats["inserted"] += inserted
            expected = len(INITIAL_MODULE_KEYS)
            existing = fetch_one(
                """
                SELECT COUNT(*)::int AS n
                FROM public.tenant_module_entitlements
                WHERE tenant_id = %s
                  AND module_key = ANY(%s)
                  AND status = 'active'
                """,
                (tid, list(INITIAL_MODULE_KEYS)),
            )
            n = int((existing or {}).get("n") or 0)
            stats["skipped_existing"] += max(0, expected - inserted)
            if n < expected:
                raise RuntimeError(
                    f"tenant={slug} expected {expected} active modules, got {n}"
                )
            logger.info(
                "entitlements ok tenant_id=%s slug=%s inserted=%s active=%s",
                tid,
                slug,
                inserted,
                n,
            )
        except Exception:
            stats["errors"] += 1
            logger.exception("entitlement backfill failed slug=%s", slug)
            raise

    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = backfill_tenant_module_entitlements()
    print("backfill_tenant_module_entitlements:", result)
