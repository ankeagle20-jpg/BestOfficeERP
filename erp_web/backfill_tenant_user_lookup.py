# -*- coding: utf-8 -*-
"""Tek seferlik: active kiracı admin e-postalarını tenant_user_lookup'a yazar."""
from __future__ import annotations

import logging
import re

from psycopg2 import sql as psql
from psycopg2.errors import UniqueViolation

from db import _TENANT_SCHEMA_RE, db, ensure_tenant_user_lookup_table, fetch_all

logger = logging.getLogger(__name__)

_ADMIN_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Checkpoint 5/6 test kiracıları: admin kullanıcı adı e-posta değil; iç test lookup'ı.
_LEGACY_LOOKUP_EMAILS: dict[str, str] = {
    "test": "test@test.payafin",
    "test2": "test2@test.payafin",
}


def _normalize_lookup_email(username: str | None) -> str | None:
    email = str(username or "").strip().lower()
    if "@" not in email or not _ADMIN_EMAIL_RE.fullmatch(email):
        return None
    return email


def _admin_email_for_schema(schema: str) -> str | None:
    if not schema or not _TENANT_SCHEMA_RE.fullmatch(schema):
        return None
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            psql.SQL(
                """
                SELECT username FROM {}.users
                WHERE is_active = TRUE
                ORDER BY (CASE WHEN role = 'admin' THEN 0 ELSE 1 END), id ASC
                LIMIT 1
                """
            ).format(psql.Identifier(schema)),
        )
        row = cur.fetchone()
    if not row:
        return None
    username = row["username"] if isinstance(row, dict) else row[0]
    return _normalize_lookup_email(username)


def _lookup_email_for_tenant(slug: str, schema: str) -> str | None:
    email = _admin_email_for_schema(schema)
    if email:
        return email
    return _LEGACY_LOOKUP_EMAILS.get(slug)


def backfill_tenant_user_lookup() -> dict:
    """Tüm active kiracıları salt-okuma tarayıp lookup indeksine ekler."""
    ensure_tenant_user_lookup_table()
    tenants = fetch_all(
        """
        SELECT slug, schema_name
        FROM public.tenants
        WHERE status = 'active'
        ORDER BY slug
        """
    )
    stats = {
        "scanned": 0,
        "inserted": 0,
        "skipped_no_email": 0,
        "skipped_duplicate": 0,
        "errors": 0,
    }
    for row in tenants:
        stats["scanned"] += 1
        slug = row["slug"]
        schema = row["schema_name"]
        email = _lookup_email_for_tenant(slug, schema)
        if not email:
            stats["skipped_no_email"] += 1
            continue
        try:
            with db() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO public.tenant_user_lookup (email, tenant_slug)
                    VALUES (%s, %s)
                    """,
                    (email, slug),
                )
            stats["inserted"] += 1
        except UniqueViolation:
            stats["skipped_duplicate"] += 1
            logger.info(
                "backfill skip duplicate email=%s slug=%s",
                email,
                slug,
            )
        except Exception:
            stats["errors"] += 1
            logger.exception("backfill failed slug=%s schema=%s", slug, schema)
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = backfill_tenant_user_lookup()
    print(result)
