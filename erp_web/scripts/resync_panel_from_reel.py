#!/usr/bin/env python3
"""
Panel'i musteri_reel_donem_tutar ile yeniden senkronlar.
Mantık: api_reel_donem_tutar_upsert panel bloğu (giris_routes.py ~9327-9361).

Kullanım:
  # Önce tek müşteri dry-run (DB yazmaz)
  python scripts/resync_panel_from_reel.py --musteri-id 357

  # Onay sonrası tek müşteri yaz
  python scripts/resync_panel_from_reel.py --musteri-id 357 --apply

  # Onay sonrası 116 müşteri
  python scripts/resync_panel_from_reel.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from routes.giris_routes import (  # noqa: E402
    _build_aylik_grid_cache_payload,
    _load_musteri_panel_by_iso,
    _save_musteri_panel_by_iso,
    _invalidate_aylik_grid_payload_mem,
    _upsert_aylik_grid_cache,
    _musteri_reel_donem_manual_dict_from_db,
    _reel_donem_ay_keys_for_period,
    _musteri_kyc_grup_for_aylik_grid,
    _aylik_grid_coerce_date,
)

# Önceki analizdeki 116 uyumsuz müşteri
UYUMSUZ_MUSTERI_IDS = [
    64, 67, 69, 70, 72, 73, 75, 76, 77, 92, 103, 105, 107, 110, 111, 116, 117,
    131, 133, 135, 152, 153, 154, 164, 172, 174, 176, 180, 184, 190, 191, 208,
    212, 213, 215, 221, 238, 239, 248, 260, 261, 269, 271, 279, 281, 282, 288,
    295, 300, 305, 317, 325, 328, 335, 342, 344, 348, 349, 350, 351, 352, 354,
    355, 357, 360, 363, 365, 366, 369, 370, 372, 376, 377, 379, 380, 381, 382,
    383, 384, 386, 387, 388, 389, 390, 392, 393, 394, 395, 397, 400, 401, 402,
    403, 404, 408, 410, 411, 415, 417, 421, 437, 440, 450, 453, 454, 457, 490,
    668, 869, 874, 896, 897, 898, 899, 900, 913,
]

TOL_P = 0.05  # upsert ile aynı


def panel_patch_from_reel_upsert(mid: int) -> tuple[dict, dict | None]:
    """
    api_reel_donem_tutar_upsert panel bloğunun aynısı.
    Döner: (patch_by_iso, payload_after)
    """
    mid_int = int(mid)
    payload_after = _build_aylik_grid_cache_payload(mid_int)
    existing_panel = _load_musteri_panel_by_iso(mid_int)
    patch: dict = {}

    if payload_after and isinstance(payload_after.get("aylar"), list):
        for a in payload_after["aylar"]:
            if not isinstance(a, dict):
                continue
            yil_p, ay_p = a.get("yil"), a.get("ay")
            if not yil_p or not ay_p:
                continue
            iso_k = date(int(yil_p), int(ay_p), 1).isoformat()
            new_brut = round(float(a.get("brut_tutar_kdv") or 0), 2)
            if new_brut <= 0:
                continue

            existing_row = existing_panel.get(iso_k) or {}
            eski_brut = round(float(existing_row.get("aylik") or 0), 2)
            eski_tahsil = round(float(existing_row.get("tahsil") or 0), 2)

            if eski_tahsil >= eski_brut - TOL_P and eski_brut > TOL_P:
                yeni_tahsil = new_brut
                yeni_kalan = 0.0
            else:
                yeni_tahsil = eski_tahsil
                yeni_kalan = max(round(new_brut - eski_tahsil, 2), 0.0)

            patch[iso_k] = {
                "aylik": new_brut,
                "tahsil": yeni_tahsil,
                "kalan": yeni_kalan,
                "tahsil_tarih": existing_row.get("tahsil_tarih"),
            }

    return patch, payload_after


def reel_donem_aylari(mid: int) -> set[str]:
    """Rapor için reel dönem ISO anahtarları (YYYY-MM-01)."""
    reel = _musteri_reel_donem_manual_dict_from_db(mid)
    kyc = _musteri_kyc_grup_for_aylik_grid(mid)
    bas = _aylik_grid_coerce_date((kyc or {}).get("sozlesme_tarihi"))
    if not bas:
        bas = _aylik_grid_coerce_date((kyc or {}).get("rent_start_date"))
    if not bas:
        return set()
    artis = _aylik_grid_coerce_date((kyc or {}).get("kira_artis_tarihi")) or bas
    out = set()
    for dy in reel:
        for ky in _reel_donem_ay_keys_for_period(
            bas, int(dy), int(artis.month), int(artis.day)
        ):
            y, m = map(int, ky.split("-"))
            out.add(date(y, m, 1).isoformat())
    return out


def diff_panel(old: dict, new_patch: dict, reel_isos: set[str]) -> list[dict]:
    rows = []
    for iso in sorted(reel_isos):
        o = old.get(iso) or {}
        n = new_patch.get(iso) or {}
        if not n:
            continue
        rows.append({
            "iso": iso,
            "eski": {"aylik": o.get("aylik"), "tahsil": o.get("tahsil"), "kalan": o.get("kalan")},
            "yeni": {"aylik": n.get("aylik"), "tahsil": n.get("tahsil"), "kalan": n.get("kalan")},
        })
    return rows


def apply_panel(mid: int, patch: dict) -> None:
    """Upsert endpoint ile aynı yazma + grid cache yenileme."""
    if not patch:
        return
    _save_musteri_panel_by_iso(int(mid), patch, prune_no_db_tahsil=False)
    _invalidate_aylik_grid_payload_mem(int(mid))
    _upsert_aylik_grid_cache(int(mid))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--musteri-id", type=int, help="Tek müşteri (örn. 357 dry-run testi)")
    ap.add_argument("--apply", action="store_true", help="DB'ye yaz (varsayılan: dry-run)")
    args = ap.parse_args()

    ids = [args.musteri_id] if args.musteri_id else UYUMSUZ_MUSTERI_IDS
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mod: {mode} | Müşteri sayısı: {len(ids)}")

    for mid in ids:
        old = _load_musteri_panel_by_iso(mid)
        patch, _payload = panel_patch_from_reel_upsert(mid)
        reel_isos = reel_donem_aylari(mid)
        changes = diff_panel(old, patch, reel_isos)

        print(f"\n=== musteri_id={mid} ===")
        print(f"Reel dönem: {_musteri_reel_donem_manual_dict_from_db(mid)}")
        print(f"Reel ay sayısı (panel diff): {len(changes)}")
        for row in changes:
            print(json.dumps(row, ensure_ascii=False))

        if args.apply:
            apply_panel(mid, patch)
            print("-> DB'ye yazıldı.")
        else:
            print("-> DRY-RUN: DB'ye yazılmadı.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
