# -*- coding: utf-8 -*-
"""
İşaretsiz banka tahsilatlarına ekstre FIFO ay parçası yazar.

Dağılım _auto_allocate_oldest_unpaid_months ile aynı kuraldır:
ekstre borç satırı, tarih sırası, vadesi gelmiş ay, sonra avans.
Aşama 3 onayı olmadan yazmaz. Çalıştırmak için --uygula gerekir.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import execute, fetch_all  # noqa: E402
from routes.faturalar_routes import (  # noqa: E402
    _aciklama_with_aylik_markers,
    _aciklama_with_aylik_pay_tokens,
    _auto_allocate_oldest_unpaid_months,
)
from routes.giris_routes import (  # noqa: E402
    _upsert_aylik_grid_cache,
    apply_makbuz_dagitim_to_panel_db,
)


def _has_markers(ac: str) -> bool:
    s = ac or ""
    return "|AYLIK_TAH|" in s or "|AYLIK_PAY|" in s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--uygula", action="store_true")
    ap.add_argument("--musteri-id", type=int, default=0)
    args = ap.parse_args()
    if not args.uygula:
        print("Yazma kapalı. Aşama 3 onayı olmadan çalıştırılmaz.")
        return 0

    sql = """
        SELECT id, COALESCE(musteri_id, customer_id) AS mid, makbuz_no, tutar,
               tahsilat_tarihi, COALESCE(aciklama, '') AS aciklama, banka_referans_no
        FROM tahsilatlar
        WHERE COALESCE(kaynak, '') = 'banka_import'
          AND COALESCE(tutar, 0) > 0
          AND COALESCE(aciklama, '') NOT LIKE %s
          AND COALESCE(aciklama, '') NOT LIKE %s
    """
    params: list = ["%|AYLIK_TAH|%", "%|AYLIK_PAY|%"]
    if args.musteri_id > 0:
        sql += " AND COALESCE(musteri_id, customer_id) = %s"
        params.append(args.musteri_id)
    sql += " ORDER BY COALESCE(musteri_id, customer_id) ASC, tahsilat_tarihi ASC NULLS LAST, id ASC"
    rows = fetch_all(sql, tuple(params)) or []
    print("ADAY", len(rows), "dry_run", args.dry_run)

    bak_dir = ROOT / "_backup_banka_aylik_markers"
    bak_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = bak_dir / ("tahsilatlar_banka_import_pre_markers_%s.json" % stamp)
    bak_path.write_text(
        json.dumps(
            [
                {
                    "id": r.get("id"),
                    "mid": r.get("mid"),
                    "makbuz_no": r.get("makbuz_no"),
                    "tutar": float(r.get("tutar") or 0),
                    "tahsilat_tarihi": str(r.get("tahsilat_tarihi") or ""),
                    "aciklama": r.get("aciklama") or "",
                    "banka_referans_no": r.get("banka_referans_no"),
                }
                for r in rows
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("YEDEK", bak_path)

    updated = 0
    report = []
    rem_by_mid: dict[int, dict] = {}

    for r in rows:
        tid = int(r["id"])
        mid = int(r["mid"])
        tutar = round(float(r.get("tutar") or 0), 2)
        ham = (r.get("aciklama") or "").strip() or "Banka tahsilat"
        tah = str(r.get("tahsilat_tarihi") or "")[:10]
        if _has_markers(ham):
            continue
        _iso_list, pay_items = _auto_allocate_oldest_unpaid_months(
            mid,
            tutar,
            odeme_tarihi=tah,
            haric_tahsilat_id=tid,
        )
        pay_items = list(pay_items or [])
        rem_by_mid.setdefault(mid, {})
        new_ac = _aciklama_with_aylik_markers(ham, [iso for iso, _ in pay_items])
        if pay_items:
            new_ac = _aciklama_with_aylik_pay_tokens(new_ac, pay_items)
        dagitim = [{"iso": iso, "tutar": pay} for iso, pay in pay_items]
        print(
            "ROW", tid, "makbuz", r.get("makbuz_no"), "mid", mid,
            "tutar", tutar, "pays", [(i, p) for i, p in pay_items],
        )
        report.append({
            "id": tid,
            "makbuz_no": r.get("makbuz_no"),
            "mid": mid,
            "tutar": tutar,
            "pay_items": dagitim,
            "aciklama_new": new_ac,
        })
        if args.dry_run:
            continue
        execute(
            "UPDATE tahsilatlar SET aciklama = %s WHERE id = %s AND COALESCE(kaynak,'') = 'banka_import'",
            (new_ac, tid),
        )
        if dagitim:
            try:
                apply_makbuz_dagitim_to_panel_db(mid, dagitim, tahsilat_tarihi=tah)
            except Exception as e:
                print("PANEL_WARN", tid, e)
        updated += 1

    if not args.dry_run:
        for mid in sorted(rem_by_mid.keys()):
            try:
                _upsert_aylik_grid_cache(mid)
                print("CACHE_REBUILD mid", mid)
            except Exception as e:
                print("CACHE_REBUILD_FAIL", mid, e)

    out_rep = bak_dir / ("migrate_report_%s.json" % stamp)
    out_rep.write_text(
        json.dumps({"updated": updated, "dry_run": args.dry_run, "rows": report}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("DONE updated", updated, "report", out_rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
