# -*- coding: utf-8 -*-
"""firma_ui_preferences DDL — varsayılan dry-run.

Kullanım:
  python scripts/ensure_firma_ui_preferences.py           # dry-run
  ALLOW_PROD_WRITE=1 python scripts/ensure_firma_ui_preferences.py --apply

Geri alma (ayrı onay):
  DROP TABLE IF EXISTS firma_ui_preferences;
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DDL = """
CREATE TABLE IF NOT EXISTS firma_ui_preferences (
    pref_key   TEXT PRIMARY KEY,
    pref_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# Child/object adları (çakışma probe)
OBJECT_NAMES = [
    "firma_ui_preferences",
    "firma_ui_preferences_pkey",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="DDL çalıştır (ALLOW_PROD_WRITE=1 gerekir)")
    args = ap.parse_args()
    print("=== DRY-RUN DDL ===")
    print(DDL.strip())
    print("=== /DDL ===")
    print("object_names", OBJECT_NAMES)
    if not args.apply:
        print("dry_run=1 apply=0")
        return 0
    if str(os.environ.get("ALLOW_PROD_WRITE") or "").strip() != "1":
        print("RED ALLOW_PROD_WRITE!=1")
        return 2
    from db import ensure_firma_ui_preferences_table, firma_ui_preferences_table_exists

    ensure_firma_ui_preferences_table()
    print("apply_ok", firma_ui_preferences_table_exists())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
