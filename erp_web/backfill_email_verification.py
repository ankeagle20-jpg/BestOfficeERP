# -*- coding: utf-8 -*-
"""Tek seferlik: mevcut şemalara email_verified_at + email_verification_tokens."""
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


def _ensure_in_schema(schema: str) -> None:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            psql.SQL(
                "ALTER TABLE {}.users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ"
            ).format(psql.Identifier(schema))
        )
        cur.execute(
            psql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.email_verification_tokens (
                    id          BIGSERIAL PRIMARY KEY,
                    user_id     INTEGER NOT NULL
                        REFERENCES {}.users (id) ON DELETE CASCADE,
                    token_hash  TEXT NOT NULL,
                    expires_at  TIMESTAMPTZ NOT NULL,
                    used_at     TIMESTAMPTZ,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT email_verification_tokens_token_hash_key UNIQUE (token_hash)
                )
                """
            ).format(psql.Identifier(schema), psql.Identifier(schema))
        )
        cur.execute(
            psql.SQL(
                """
                CREATE INDEX IF NOT EXISTS email_verification_tokens_user_id_idx
                ON {}.email_verification_tokens (user_id)
                """
            ).format(psql.Identifier(schema))
        )
        cur.execute(
            psql.SQL(
                """
                CREATE INDEX IF NOT EXISTS email_verification_tokens_expires_at_idx
                ON {}.email_verification_tokens (expires_at)
                WHERE used_at IS NULL
                """
            ).format(psql.Identifier(schema))
        )
        conn.commit()


def backfill_email_verification() -> dict:
    stats = {"schemas": 0, "ok": 0, "errors": 0}
    for schema in TARGET_SCHEMAS:
        stats["schemas"] += 1
        try:
            _ensure_in_schema(schema)
            stats["ok"] += 1
            logger.info("email_verification ensured in schema=%s", schema)
        except Exception:
            stats["errors"] += 1
            logger.exception("email_verification failed schema=%s", schema)
            raise
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = backfill_email_verification()
    print("backfill_email_verification:", result)
