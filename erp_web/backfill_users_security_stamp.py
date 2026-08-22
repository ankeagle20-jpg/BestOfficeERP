# -*- coding: utf-8 -*-
"""Tek seferlik: mevcut şemalarda users.security_stamp sütununu doldurur."""
from __future__ import annotations

import logging

from psycopg2 import sql as psql

from db import db

logger = logging.getLogger(__name__)

TARGET_SCHEMAS: tuple[str, ...] = (
    "public",
    "tenant_demo",
    "tenant_test",
    "tenant_test2",
    "tenant_deneme01",
)


def _ensure_security_stamp_in_schema(schema: str) -> None:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            psql.SQL(
                "ALTER TABLE {}.users ADD COLUMN IF NOT EXISTS security_stamp TEXT"
            ).format(psql.Identifier(schema))
        )
        cur.execute(
            psql.SQL(
                """
                UPDATE {}.users
                SET security_stamp = encode(gen_random_bytes(24), 'base64')
                WHERE security_stamp IS NULL OR btrim(security_stamp) = ''
                """
            ).format(psql.Identifier(schema))
        )
        cur.execute(
            psql.SQL(
                "ALTER TABLE {}.users ALTER COLUMN security_stamp SET NOT NULL"
            ).format(psql.Identifier(schema))
        )
        conn.commit()


def backfill_users_security_stamp() -> dict:
    """Belirtilen şemalarda idempotent olarak security_stamp sütununu doldurur."""
    stats = {"schemas": 0, "ok": 0, "errors": 0}
    for schema in TARGET_SCHEMAS:
        stats["schemas"] += 1
        try:
            _ensure_security_stamp_in_schema(schema)
            stats["ok"] += 1
            logger.info("users.security_stamp ensured in schema=%s", schema)
        except Exception:
            stats["errors"] += 1
            logger.exception("users.security_stamp failed schema=%s", schema)
            raise
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = backfill_users_security_stamp()
    print("backfill_users_security_stamp:", result)
