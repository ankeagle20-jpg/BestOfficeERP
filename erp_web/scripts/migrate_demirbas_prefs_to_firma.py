# -*- coding: utf-8 -*-
"""user_ui_preferences → firma_ui_preferences demirbaş anahtarları (tek seferlik).

Varsayılan: dry-run (yalnızca sayılar; değer/isim YAZMAZ).
  python scripts/migrate_demirbas_prefs_to_firma.py
  ALLOW_PROD_WRITE=1 python scripts/migrate_demirbas_prefs_to_firma.py --apply

Geri alma: firma satırlarını silmek ayrı onay gerektirir
  DELETE FROM firma_ui_preferences WHERE pref_key IN (...);
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KEYS = (
    "demirbas_urunler_ek",
    "demirbas_urunler_gizli",
    "demirbas_markalar",
)


def _count_items(pref_key: str, prefs: dict) -> int:
    if not isinstance(prefs, dict):
        return 0
    if pref_key == "demirbas_urunler_ek":
        arr = prefs.get("urunler") or []
        return len(arr) if isinstance(arr, list) else 0
    if pref_key == "demirbas_urunler_gizli":
        arr = prefs.get("gizli") or []
        return len(arr) if isinstance(arr, list) else 0
    if pref_key == "demirbas_markalar":
        arr = prefs.get("markalar") or []
        return len(arr) if isinstance(arr, list) else 0
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from demirbas import (
        DEMIRBAS_PREF_MARKA,
        DEMIRBAS_PREF_URUN_EK,
        DEMIRBAS_PREF_URUN_GIZLI,
        demirbas_pref_merge_json,
    )
    from db import fetch_all, fetch_one, firma_ui_preferences_table_exists

    print("schema", (fetch_one("SELECT current_schema() AS s") or {}).get("s"))
    firma_var = firma_ui_preferences_table_exists()
    print("firma_ui_preferences_var", int(bool(firma_var)))

    # kullanıcı başına öğe sayıları (PII yok — user_id sayısal)
    rows = fetch_all(
        """
        SELECT user_id, pref_key, pref_json
        FROM user_ui_preferences
        WHERE pref_key = ANY(%s)
        ORDER BY pref_key, user_id
        """,
        (list(KEYS),),
    ) or []

    per_key_users: dict[str, int] = {k: 0 for k in KEYS}
    per_key_items: dict[str, int] = {k: 0 for k in KEYS}
    merged: dict[str, dict] = {k: {} for k in KEYS}

    for r in rows:
        key = str(r.get("pref_key") or "")
        if key not in KEYS:
            continue
        prefs = r.get("pref_json")
        if isinstance(prefs, str):
            try:
                prefs = json.loads(prefs)
            except Exception:
                prefs = {}
        if not isinstance(prefs, dict):
            prefs = {}
        n = _count_items(key, prefs)
        if n <= 0 and not prefs:
            continue
        per_key_users[key] = per_key_users.get(key, 0) + 1
        per_key_items[key] = per_key_items.get(key, 0) + n
        merged[key] = demirbas_pref_merge_json(key, merged[key], prefs)

    print("=== DRY-RUN SAYILAR ===")
    for k in KEYS:
        birlesim = _count_items(k, merged[k])
        print(
            f"key={k} kullanici_n={per_key_users[k]} "
            f"toplam_oge={per_key_items[k]} birlesim_n={birlesim}"
        )
    print("=== /SAYILAR ===")

    if not args.apply:
        print("dry_run=1 apply=0")
        return 0
    if str(os.environ.get("ALLOW_PROD_WRITE") or "").strip() != "1":
        print("RED ALLOW_PROD_WRITE!=1")
        return 2
    if not firma_var:
        print("RED firma_ui_preferences yok — önce ensure_firma_ui_preferences --apply")
        return 3

    from db import execute

    for k in KEYS:
        raw = json.dumps(merged[k], ensure_ascii=False)
        execute(
            """
            INSERT INTO firma_ui_preferences (pref_key, pref_json, updated_at)
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT (pref_key) DO UPDATE SET
                pref_json = EXCLUDED.pref_json,
                updated_at = NOW()
            """,
            (k, raw),
        )
        print("wrote", k, "birlesim_n", _count_items(k, merged[k]))
    print("apply_ok 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
