# -*- coding: utf-8 -*-
"""musteri_demirbas (+ adet) + musteri_demirbas_seri DDL — varsayılan dry-run.

Kullanım:
  python scripts/ensure_musteri_demirbas.py           # dry-run (DDL yazdırır, çalıştırmaz)
  ALLOW_PROD_WRITE=1 python scripts/ensure_musteri_demirbas.py --apply

Geri alma (ayrı onay):
  DROP TABLE IF EXISTS musteri_demirbas_seri;
  ALTER TABLE musteri_demirbas DROP COLUMN IF EXISTS adet;
  -- tam silme (önceki sürüm):
  -- DROP TABLE IF EXISTS musteri_demirbas;
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DDL = """
CREATE TABLE IF NOT EXISTS musteri_demirbas (
    id              SERIAL PRIMARY KEY,
    musteri_id      INTEGER NOT NULL
        REFERENCES customers(id) ON DELETE CASCADE,
    sira            INTEGER NOT NULL DEFAULT 1,
    esya            TEXT NOT NULL DEFAULT '',
    marka_model     TEXT NOT NULL DEFAULT '',
    seri_no         TEXT NOT NULL,
    adet_durum      TEXT NOT NULL DEFAULT '',
    adet            INTEGER NOT NULL DEFAULT 1,
    mulkiyet        TEXT NOT NULL DEFAULT 'OFISBIR',
    kapsam          TEXT NOT NULL DEFAULT 'oda_ici',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_musteri_demirbas_seri_no UNIQUE (seri_no),
    CONSTRAINT ck_musteri_demirbas_mulkiyet
        CHECK (mulkiyet IN ('OFISBIR', 'MUSTERI')),
    CONSTRAINT ck_musteri_demirbas_kapsam
        CHECK (kapsam IN ('oda_ici', 'ortak_alan'))
);
ALTER TABLE musteri_demirbas
    ADD COLUMN IF NOT EXISTS adet INTEGER NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_musteri_demirbas_musteri_id
    ON musteri_demirbas (musteri_id);
CREATE INDEX IF NOT EXISTS idx_musteri_demirbas_musteri_sira
    ON musteri_demirbas (musteri_id, sira);

CREATE TABLE IF NOT EXISTS musteri_demirbas_seri (
    id              SERIAL PRIMARY KEY,
    demirbas_id     INTEGER NOT NULL
        REFERENCES musteri_demirbas(id) ON DELETE CASCADE,
    sira            INTEGER NOT NULL DEFAULT 1,
    seri_no         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_musteri_demirbas_seri_seri_no UNIQUE (seri_no),
    CONSTRAINT uq_musteri_demirbas_seri_parent_sira
        UNIQUE (demirbas_id, sira)
);
CREATE INDEX IF NOT EXISTS idx_musteri_demirbas_seri_demirbas_id
    ON musteri_demirbas_seri (demirbas_id);
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="DDL çalıştır (ALLOW_PROD_WRITE=1 gerekir)")
    args = ap.parse_args()
    print("=== DRY-RUN DDL ===")
    print(DDL.strip())
    print("=== /DDL ===")
    if not args.apply:
        print("dry_run=1 apply=0")
        return 0
    if str(os.environ.get("ALLOW_PROD_WRITE") or "").strip() != "1":
        print("RED ALLOW_PROD_WRITE!=1")
        return 2
    from db import (
        ensure_musteri_demirbas_table,
        musteri_demirbas_schema_ready,
        musteri_demirbas_table_exists,
    )

    ensure_musteri_demirbas_table()
    print(
        "apply_ok",
        musteri_demirbas_table_exists(),
        "sema_hazir",
        musteri_demirbas_schema_ready(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
