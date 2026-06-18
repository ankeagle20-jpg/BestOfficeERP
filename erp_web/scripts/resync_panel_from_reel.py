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

from db import execute, fetch_all  # noqa: E402
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
PLACEHOLDER_BRUT_MAX = 0.5  # grid min tutar (0.01); gerçek brüt yazılmamış panel


def _tahsilat_candidates_for_ay(mid: int, iso_k: str) -> list[dict]:
    """fatura_id NULL + |AYLIK_TAH|iso| marker'lı tahsilat kayıtları."""
    marker = f"%|AYLIK_TAH|{iso_k}|%"
    return fetch_all(
        """
        SELECT id, tutar, tahsilat_tarihi, odeme_turu, makbuz_no, aciklama, fatura_id
        FROM tahsilatlar
        WHERE (musteri_id = %s OR customer_id = %s)
          AND fatura_id IS NULL
          AND COALESCE(aciklama, '') LIKE %s
          AND COALESCE(aciklama, '') LIKE '%%|AYLIK_TAH|%%'
        ORDER BY id
        """,
        (int(mid), int(mid), marker),
    ) or []


def plan_tahsil_update_for_ay(
    mid: int,
    iso_k: str,
    new_brut: float,
    eski_tahsil: float,
) -> dict:
    """
    Tek kayıt + tutar uyumu varsa update planı; aksi halde skip_*.
    Panel zaten new_brut'a çekilmiş olsa bile DB'deki eski tutarı günceller.
    """
    nb = round(float(new_brut or 0), 2)
    et = round(float(eski_tahsil or 0), 2)
    base = {"iso": iso_k, "new_brut": nb, "eski_tahsil_panel": et}
    if nb <= TOL_P:
        return {**base, "action": "skip_brut_yok"}

    rows = _tahsilat_candidates_for_ay(mid, iso_k)
    if not rows:
        return {**base, "action": "skip_kayit_yok"}

    if len(rows) > 1:
        return {
            **base,
            "action": "skip_coklu",
            "count": len(rows),
            "ids": [int(r["id"]) for r in rows],
            "tutarlar": [round(float(r.get("tutar") or 0), 2) for r in rows],
        }

    row = rows[0]
    tid = int(row["id"])
    tutar_cur = round(float(row.get("tutar") or 0), 2)

    if abs(tutar_cur - nb) <= TOL_P:
        return {**base, "action": "skip_zaten_guncel", "id": tid, "tutar": tutar_cur}

    panel_zaten_tam = abs(et - nb) <= TOL_P
    tutar_panel_uyumlu = abs(tutar_cur - et) <= TOL_P
    tutar_db_eski = panel_zaten_tam and tutar_cur < nb - TOL_P

    if not tutar_panel_uyumlu and not tutar_db_eski:
        return {
            **base,
            "action": "skip_tutar_uyumsuz",
            "id": tid,
            "tutar": tutar_cur,
        }

    return {
        **base,
        "action": "update",
        "id": tid,
        "eski_tutar": tutar_cur,
        "yeni_tutar": nb,
    }


def apply_tahsil_updates(plans: list[dict]) -> list[dict]:
    """Yalnızca action=update planlarını yazar; uygulanan kayıtları döner."""
    applied: list[dict] = []
    for plan in plans:
        if plan.get("action") != "update":
            continue
        tid = int(plan["id"])
        yeni = round(float(plan["yeni_tutar"]), 2)
        eski = round(float(plan["eski_tutar"]), 2)
        n = execute(
            """
            UPDATE tahsilatlar
            SET tutar = %s
            WHERE id = %s
              AND fatura_id IS NULL
              AND COALESCE(aciklama, '') LIKE '%%|AYLIK_TAH|%%'
            """,
            (yeni, tid),
        )
        if n:
            applied.append({
                "id": tid,
                "iso": plan.get("iso"),
                "eski_tutar": eski,
                "yeni_tutar": yeni,
            })
    return applied


def panel_patch_from_reel_upsert(mid: int) -> tuple[dict, dict | None, list[dict]]:
    """
    api_reel_donem_tutar_upsert panel bloğunun aynısı.
    Döner: (patch_by_iso, payload_after, tahsil_plan)
    """
    mid_int = int(mid)
    payload_after = _build_aylik_grid_cache_payload(mid_int)
    existing_panel = _load_musteri_panel_by_iso(mid_int)
    reel_isos_set = reel_donem_aylari(mid_int)
    patch: dict = {}
    tahsil_plan: list[dict] = []

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
            eski_kalan = round(float(existing_row.get("kalan") or 0), 2)
            brut_placeholder = eski_brut <= PLACEHOLDER_BRUT_MAX
            tam_tahsil_sayilir = (
                (eski_tahsil >= eski_brut - TOL_P and eski_brut > TOL_P)
                or (brut_placeholder and eski_tahsil > TOL_P and eski_kalan <= TOL_P)
            )
            if tam_tahsil_sayilir:
                yeni_tahsil = new_brut
                yeni_kalan = 0.0
                if iso_k in reel_isos_set:
                    tahsil_plan.append(
                        plan_tahsil_update_for_ay(mid_int, iso_k, new_brut, eski_tahsil)
                    )
            else:
                yeni_tahsil = eski_tahsil
                yeni_kalan = max(round(new_brut - eski_tahsil, 2), 0.0)

            patch[iso_k] = {
                "aylik": new_brut,
                "tahsil": yeni_tahsil,
                "kalan": yeni_kalan,
                "tahsil_tarih": existing_row.get("tahsil_tarih"),
            }

    return patch, payload_after, tahsil_plan


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


def _tahsil_plan_by_iso(tahsil_plan: list[dict]) -> dict[str, dict]:
    return {p["iso"]: p for p in tahsil_plan if p.get("iso")}


def _summarize_tahsil_plan(tahsil_plan: list[dict]) -> dict[str, int]:
    counts = {
        "update": 0,
        "skip_coklu": 0,
        "skip_kayit_yok": 0,
        "skip_tutar_uyumsuz": 0,
        "skip_zaten_guncel": 0,
        "skip_brut_yok": 0,
        "other": 0,
    }
    for p in tahsil_plan:
        act = p.get("action") or "other"
        if act in counts:
            counts[act] += 1
        else:
            counts["other"] += 1
    return counts


def apply_panel(mid: int, patch: dict, refresh_grid: bool = True) -> None:
    """Upsert endpoint ile aynı yazma + grid cache yenileme."""
    if not patch:
        return
    _save_musteri_panel_by_iso(int(mid), patch, prune_no_db_tahsil=False)
    if refresh_grid:
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
        patch, _payload, tahsil_plan = panel_patch_from_reel_upsert(mid)
        reel_isos = reel_donem_aylari(mid)
        changes = diff_panel(old, patch, reel_isos)
        tahsil_by_iso = _tahsil_plan_by_iso(tahsil_plan)

        print(f"\n=== musteri_id={mid} ===")
        print(f"Reel dönem: {_musteri_reel_donem_manual_dict_from_db(mid)}")
        print(f"Reel ay sayısı (panel diff): {len(changes)}")
        change_isos = {r["iso"] for r in changes}
        for row in changes:
            iso = row["iso"]
            tp = tahsil_by_iso.get(iso)
            if tp and tp.get("action") not in (None, "skip_brut_yok"):
                out = dict(row)
                if tp.get("action") == "update":
                    out["tahsilat_plan"] = {
                        "action": "update",
                        "id": tp["id"],
                        "eski_tutar": tp["eski_tutar"],
                        "yeni_tutar": tp["yeni_tutar"],
                    }
                else:
                    slim = {"action": tp["action"]}
                    if tp.get("count") is not None:
                        slim["count"] = tp["count"]
                    if tp.get("ids") is not None:
                        slim["ids"] = tp["ids"]
                    if tp.get("id") is not None:
                        slim["id"] = tp["id"]
                    if tp.get("tutar") is not None:
                        slim["tutar"] = tp["tutar"]
                    out["tahsilat_plan"] = slim
                print(json.dumps(out, ensure_ascii=False))
            else:
                print(json.dumps(row, ensure_ascii=False))

        for iso in sorted(reel_isos):
            if iso in change_isos:
                continue
            tp = tahsil_by_iso.get(iso)
            if not tp or tp.get("action") in (None, "skip_brut_yok", "skip_zaten_guncel"):
                continue
            o = old.get(iso) or {}
            n = patch.get(iso) or {}
            row = {
                "iso": iso,
                "eski": {"aylik": o.get("aylik"), "tahsil": o.get("tahsil"), "kalan": o.get("kalan")},
                "yeni": {"aylik": n.get("aylik"), "tahsil": n.get("tahsil"), "kalan": n.get("kalan")},
            }
            if tp.get("action") == "update":
                row["tahsilat_plan"] = {
                    "action": "update",
                    "id": tp["id"],
                    "eski_tutar": tp["eski_tutar"],
                    "yeni_tutar": tp["yeni_tutar"],
                }
            else:
                slim = {"action": tp["action"]}
                if tp.get("count") is not None:
                    slim["count"] = tp["count"]
                if tp.get("ids") is not None:
                    slim["ids"] = tp["ids"]
                row["tahsilat_plan"] = slim
            print(json.dumps(row, ensure_ascii=False))

        summary = _summarize_tahsil_plan(tahsil_plan)
        print(
            f"Tahsilat plan: {summary['update']} guncellenecek, "
            f"{summary['skip_coklu']} coklu_kayit, "
            f"{summary['skip_kayit_yok']} kayit_yok, "
            f"{summary['skip_tutar_uyumsuz']} tutar_uyumsuz, "
            f"{summary['skip_zaten_guncel']} zaten_guncel"
        )

        if args.apply:
            apply_panel(mid, patch, refresh_grid=False)
            applied = apply_tahsil_updates(tahsil_plan)
            for a in applied:
                print(
                    f"-> tahsilat id={a['id']}: "
                    f"{a['eski_tutar']} -> {a['yeni_tutar']} (UYGULANDI)"
                )
            if patch or applied:
                _invalidate_aylik_grid_payload_mem(int(mid))
                _upsert_aylik_grid_cache(int(mid))
            print("-> DB'ye yazıldı.")
        else:
            for tp in tahsil_plan:
                if tp.get("action") == "update":
                    print(
                        f"-> tahsilat id={tp['id']}: "
                        f"{tp['eski_tutar']} -> {tp['yeni_tutar']} (DRY-RUN)"
                    )
            print("-> DRY-RUN: DB'ye yazılmadı.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
