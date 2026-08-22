# -*- coding: utf-8 -*-
"""Tek seferlik: mevcut şemalara password_reset_tokens tablosunu ekler."""
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


def _ensure_table_in_schema(schema: str) -> None:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            psql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.password_reset_tokens (
                    id          BIGSERIAL PRIMARY KEY,
                    user_id     INTEGER NOT NULL
                        REFERENCES {}.users (id) ON DELETE CASCADE,
                    token_hash  TEXT NOT NULL,
                    expires_at  TIMESTAMPTZ NOT NULL,
                    used_at     TIMESTAMPTZ,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    request_ip  TEXT,
                    CONSTRAINT password_reset_tokens_token_hash_key UNIQUE (token_hash)
                )
                """
            ).format(psql.Identifier(schema), psql.Identifier(schema))
        )
        cur.execute(
            psql.SQL(
                """
                CREATE INDEX IF NOT EXISTS password_reset_tokens_user_id_idx
                ON {}.password_reset_tokens (user_id)
                """
            ).format(psql.Identifier(schema))
        )
        cur.execute(
            psql.SQL(
                """
                CREATE INDEX IF NOT EXISTS password_reset_tokens_expires_at_idx
                ON {}.password_reset_tokens (expires_at)
                WHERE used_at IS NULL
                """
            ).format(psql.Identifier(schema))
        )
        conn.commit()


def backfill_password_reset_tokens() -> dict:
    """Belirtilen şemalarda idempotent olarak tabloyu oluşturur."""
    stats = {"schemas": 0, "ok": 0, "errors": 0}
    for schema in TARGET_SCHEMAS:
        stats["schemas"] += 1
        try:
            _ensure_table_in_schema(schema)
            stats["ok"] += 1
            logger.info("password_reset_tokens ensured in schema=%s", schema)
        except Exception:
            stats["errors"] += 1
            logger.exception("password_reset_tokens failed schema=%s", schema)
            raise
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = backfill_password_reset_tokens()
    print("backfill_password_reset_tokens:", result)
