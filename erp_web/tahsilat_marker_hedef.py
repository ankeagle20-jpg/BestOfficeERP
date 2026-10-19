# -*- coding: utf-8 -*-
"""Tahsilat marker hedef dağılımı — açık PAY/TAH yazımı.

banka_marker_onarim yalnızca banka_import + FIFO. Bu araç:
  --kaynak (varsayılan: herhangi / allowlist)
  --hedefler 2026-01:3163,2026-02:4398,2026-03:2439

Tutar / tarih / makbuz / kaynak değişmez; yalnız aciklama marker'ları.
Yedek: tahsilat_marker_yedek (banka_marker_onarim ile aynı).
Varsayılan DRY-RUN.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

from banka_marker_onarim import (
    YEDEK_TABLO,
    SQL_KILIT,
    _build_aciklama,
    _pay_map,
    ensure_tahsilat_marker_yedek,
    _sync_panel_grid_after,
)

_KAYNAK_ALLOW = frozenset({
    "banka_import",
    "manuel_makbuz",
    "odeme_linki",
    "grid_toplu",
    "",  # boş kaynak (eski toplu aktarım)
})

_YM_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _prod_write_ok() -> bool:
    return str(os.environ.get("ALLOW_PROD_WRITE") or "").strip() in (
        "1", "true", "TRUE", "yes",
    )


def _parse_ids(s: str) -> list[int]:
    out: list[int] = []
    for part in str(s or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a.strip()), int(b.strip())
            if hi < lo:
                lo, hi = hi, lo
            out.extend(range(lo, hi + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def parse_hedefler(s: str) -> list[tuple[str, float]]:
    """'2026-01:3163,2026-02:4398' → [('2026-01-01', 3163.0), ...] sıralı."""
    items: list[tuple[str, float]] = []
    for part in str(s or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"hedef_bicim: {part}")
        ym, tut = part.split(":", 1)
        ym = ym.strip()
        m = _YM_RE.match(ym)
        if not m:
            raise ValueError(f"hedef_ay: {ym}")
        iso = "%04d-%02d-01" % (int(m.group(1)), int(m.group(2)))
        items.append((iso, round(float(tut.strip().replace(",", ".")), 2)))
    items.sort(key=lambda x: x[0])
    if not items:
        raise ValueError("hedefler bos")
    return items


def _load_row(mid: int, tid: int, oku, *, kaynak_filtre: str | None) -> dict:
    rows = oku(
        """
        SELECT id,
               COALESCE(musteri_id, customer_id) AS mid,
               ROUND(COALESCE(tutar, 0)::numeric, 2) AS tutar,
               LEFT(tahsilat_tarihi::text, 10) AS tarih,
               COALESCE(aciklama, '') AS aciklama,
               makbuz_no,
               banka_referans_no,
               COALESCE(kaynak, '') AS kaynak,
               odeme_turu
        FROM tahsilatlar
        WHERE id = %s
        """,
        (int(tid),),
    ) or []
    if len(rows) != 1:
        raise RuntimeError(f"id_yok={tid}")
    r = rows[0]
    if int(r["mid"]) != int(mid):
        raise RuntimeError(f"mid_uyusmuyor id={tid}")
    kay = str(r.get("kaynak") or "")
    if kaynak_filtre is not None and kay != kaynak_filtre:
        raise RuntimeError(
            f"kaynak_uyusmuyor id={tid} beklenen={kaynak_filtre!r} gelen={kay!r}"
        )
    if kay not in _KAYNAK_ALLOW:
        raise RuntimeError(f"kaynak_izinli_degil id={tid} kaynak={kay!r}")
    return r


def hesapla_plan(
    mid: int,
    row: dict,
    pay_items: list[tuple[str, float]],
    *,
    tol: float = 0.02,
) -> dict:
    mid = int(mid)
    tid = int(row["id"])
    tutar = round(float(row.get("tutar") or 0), 2)
    dagitim = round(sum(float(v) for _, v in pay_items), 2)
    eski_ac = row.get("aciklama") or ""
    yeni_ac = _build_aciklama(eski_ac, list(pay_items))
    hatalar: list[str] = []
    if abs(dagitim - tutar) > tol:
        hatalar.append(f"dagitim_toplam {dagitim} != tutar {tutar}")
    for iso, v in pay_items:
        if v < 0:
            hatalar.append(f"negatif {iso}")
    degisti = (eski_ac or "").strip() != (yeni_ac or "").strip()
    return {
        "ok": not hatalar,
        "neden": None if not hatalar else "invariant",
        "invariant_hatalar": hatalar,
        "mid": mid,
        "id": tid,
        "tutar": tutar,
        "tarih": str(row.get("tarih") or "")[:10],
        "makbuz_no": row.get("makbuz_no"),
        "banka_referans_no": row.get("banka_referans_no"),
        "kaynak": row.get("kaynak"),
        "odeme_turu": row.get("odeme_turu"),
        "eski_aciklama": eski_ac,
        "yeni_aciklama": yeni_ac,
        "eski_pay_map": _pay_map(eski_ac),
        "yeni_pay_map": {iso: float(v) for iso, v in pay_items},
        "yeni_pay": list(pay_items),
        "degisti": degisti,
        "dagitim_toplam": dagitim,
    }


def onar(
    mid: int,
    tid: int,
    hedefler: list[tuple[str, float]] | str,
    *,
    dry_run: bool = True,
    etiket: str,
    kaynak: str | None = None,
    oku=None,
    calistir=None,
    force_fail: bool = False,
    sync_after: bool = True,
) -> dict:
    mid = int(mid)
    tid = int(tid)
    if isinstance(hedefler, str):
        pay_items = parse_hedefler(hedefler)
    else:
        pay_items = list(hedefler)
    out: dict[str, Any] = {
        "mid": mid,
        "id": tid,
        "dry_run": dry_run,
        "etiket": etiket,
        "yazildi": False,
        "ok": True,
    }
    if oku is None:
        from db import fetch_all

        def oku(sql, params=None):
            return fetch_all(sql, params)

    row = _load_row(mid, tid, oku, kaynak_filtre=kaynak)
    plan = hesapla_plan(mid, row, pay_items)
    out["plan"] = {
        "ok": plan.get("ok"),
        "neden": plan.get("neden"),
        "invariant_hatalar": plan.get("invariant_hatalar"),
        "eski_pay": plan.get("eski_pay_map"),
        "yeni_pay": plan.get("yeni_pay_map"),
        "degisti": plan.get("degisti"),
        "tutar": plan.get("tutar"),
        "dagitim_toplam": plan.get("dagitim_toplam"),
        "kaynak": plan.get("kaynak"),
    }
    if not plan.get("ok"):
        out["ok"] = False
        out["neden"] = plan.get("neden") or "plan_hata"
        return out
    if not plan.get("degisti"):
        out["degisiklik_yok"] = True
        out["mesaj"] = "degisiklik yok"
        return out

    yaz = (not dry_run) and (_prod_write_ok() or calistir is not None)
    if not yaz:
        out["yazildi"] = False
        out["mesaj"] = "dry-run veya ALLOW_PROD_WRITE yok — yazilmadi"
        return out

    kay_sql = str(plan.get("kaynak") or "")

    if calistir is not None:
        from audit_log import audit_yaz

        try:
            ensure_tahsilat_marker_yedek(calistir)
            calistir(SQL_KILIT, (f"tahsilat_marker_hedef:{mid}",))
            if force_fail:
                raise RuntimeError("force_fail")
            calistir(
                f"""INSERT INTO {YEDEK_TABLO}
                    (tahsilat_id, mid, eski_aciklama, eski_marker_json,
                     yeni_aciklama, etiket, islem_tamam)
                VALUES (%s, %s, %s, %s, %s, %s, FALSE)""",
                (
                    tid, mid, plan["eski_aciklama"],
                    json.dumps(plan["eski_pay_map"], ensure_ascii=False),
                    plan["yeni_aciklama"], etiket,
                ),
            )
            calistir(
                "UPDATE tahsilatlar SET aciklama = %s WHERE id = %s "
                "AND COALESCE(musteri_id, customer_id) = %s "
                "AND COALESCE(kaynak, '') = %s",
                (plan["yeni_aciklama"], tid, mid, kay_sql),
            )
            audit_yaz(
                calistir,
                olay="tahsilat_marker_hedef",
                varlik="tahsilatlar",
                kayit_id=tid,
                musteri_id=mid,
                kullanici="tahsilat_marker_hedef",
                anlik={"etiket": etiket, "eski_pay": plan["eski_pay_map"]},
                yeni={"yeni_pay": plan["yeni_pay_map"], "etiket": etiket},
            )
            calistir(
                f"UPDATE {YEDEK_TABLO} SET islem_tamam = TRUE "
                "WHERE tahsilat_id = %s AND etiket = %s",
                (tid, etiket),
            )
            out["ok"] = True
            out["yazildi"] = True
            return out
        except Exception as ex:
            out["ok"] = False
            out["yazildi"] = False
            out["neden"] = type(ex).__name__
            out["mesaj"] = "hiçbir değişiklik kaydedilmedi"
            out["hata"] = str(ex)[:200]
            return out

    from audit_log import audit_yaz
    from db import db

    try:
        with db() as conn:
            cur = conn.cursor()

            def _cal(sql, params=None):
                if params is None:
                    cur.execute(sql)
                else:
                    cur.execute(sql, params)
                return cur

            ensure_tahsilat_marker_yedek(lambda sql, params=None: _cal(sql, params))
            cur.execute(SQL_KILIT, (f"tahsilat_marker_hedef:{mid}",))
            if force_fail:
                raise RuntimeError("force_fail")
            cur.execute(
                f"""
                INSERT INTO {YEDEK_TABLO}
                    (tahsilat_id, mid, eski_aciklama, eski_marker_json,
                     yeni_aciklama, etiket, islem_tamam)
                VALUES (%s, %s, %s, %s, %s, %s, FALSE)
                RETURNING id
                """,
                (
                    tid, mid, plan["eski_aciklama"],
                    json.dumps(plan["eski_pay_map"], ensure_ascii=False),
                    plan["yeni_aciklama"], etiket,
                ),
            )
            yid = (cur.fetchone() or {}).get("id")
            cur.execute(
                """
                UPDATE tahsilatlar
                SET aciklama = %s
                WHERE id = %s
                  AND COALESCE(musteri_id, customer_id) = %s
                  AND COALESCE(kaynak, '') = %s
                """,
                (plan["yeni_aciklama"], tid, mid, kay_sql),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"update_rowcount n={cur.rowcount}")
            cur.execute(
                """
                SELECT ROUND(COALESCE(tutar,0)::numeric,2) AS tutar,
                       LEFT(tahsilat_tarihi::text,10) AS tarih,
                       banka_referans_no, makbuz_no, kaynak, odeme_turu
                FROM tahsilatlar WHERE id = %s
                """,
                (tid,),
            )
            chk = cur.fetchone() or {}
            if round(float(chk.get("tutar") or 0), 2) != plan["tutar"]:
                raise RuntimeError("tutar_degisti")
            if str(chk.get("tarih") or "")[:10] != plan["tarih"]:
                raise RuntimeError("tarih_degisti")
            if str(chk.get("makbuz_no") or "") != str(plan.get("makbuz_no") or ""):
                raise RuntimeError("makbuz_degisti")
            if str(chk.get("kaynak") or "") != str(plan.get("kaynak") or ""):
                raise RuntimeError("kaynak_degisti")
            audit_yaz(
                (lambda sql, params=None: _cal(sql, params)),
                olay="tahsilat_marker_hedef",
                varlik="tahsilatlar",
                kayit_id=tid,
                musteri_id=mid,
                kullanici="tahsilat_marker_hedef",
                anlik={
                    "etiket": etiket,
                    "eski_pay": plan["eski_pay_map"],
                    "yedek_id": yid,
                },
                yeni={"yeni_pay": plan["yeni_pay_map"], "etiket": etiket},
            )
            cur.execute(
                f"UPDATE {YEDEK_TABLO} SET islem_tamam = TRUE WHERE id = %s",
                (yid,),
            )
        if sync_after:
            # Placeholder koruması: banka sync doğrudan yazar; guarded alternatif
            try:
                from tahsilat_tutar_duzelt import _sync_panel_grid_guarded

                sync = _sync_panel_grid_guarded(mid)
            except Exception:
                sync = _sync_panel_grid_after(mid)
            out["panel_sync"] = sync
        out["ok"] = True
        out["yazildi"] = True
        return out
    except Exception as ex:
        out["ok"] = False
        out["yazildi"] = False
        out["neden"] = type(ex).__name__
        out["mesaj"] = "hiçbir değişiklik kaydedilmedi"
        out["hata"] = str(ex)[:200]
        return out


def geri_yukle(mid: int, etiket: str, *, oku=None, calistir=None) -> dict:
    """banka_marker_onarim.geri_yukle — kaynak kısıtı banka_import; burada geniş."""
    mid = int(mid)
    if not _prod_write_ok() and calistir is None:
        return {"mid": mid, "ok": False, "neden": "ALLOW_PROD_WRITE yok"}
    if oku is None:
        from db import fetch_all

        def oku(sql, params=None):
            return fetch_all(sql, params)

    rows = oku(
        f"""
        SELECT id, tahsilat_id, eski_aciklama
        FROM {YEDEK_TABLO}
        WHERE mid = %s AND etiket = %s AND COALESCE(geri_yazildi, FALSE) = FALSE
        ORDER BY id ASC
        """,
        (mid, etiket),
    ) or []
    if not rows:
        return {"mid": mid, "ok": False, "neden": "yedek yok"}

    if calistir is not None:
        try:
            for y in rows:
                calistir(
                    "UPDATE tahsilatlar SET aciklama = %s WHERE id = %s "
                    "AND COALESCE(musteri_id, customer_id) = %s",
                    (y["eski_aciklama"], y["tahsilat_id"], mid),
                )
                calistir(
                    f"UPDATE {YEDEK_TABLO} SET geri_yazildi = TRUE WHERE id = %s",
                    (y["id"],),
                )
            return {"mid": mid, "ok": True, "n": len(rows), "etiket": etiket}
        except Exception as ex:
            return {
                "mid": mid, "ok": False,
                "mesaj": "hiçbir değişiklik kaydedilmedi",
                "hata": str(ex)[:200],
            }

    from audit_log import audit_yaz
    from db import db

    try:
        with db() as conn:
            cur = conn.cursor()

            def _cal(sql, params=None):
                if params is None:
                    cur.execute(sql)
                else:
                    cur.execute(sql, params)
                return cur

            ensure_tahsilat_marker_yedek(lambda sql, params=None: _cal(sql, params))
            for y in rows:
                cur.execute(
                    """
                    UPDATE tahsilatlar SET aciklama = %s
                    WHERE id = %s AND COALESCE(musteri_id, customer_id) = %s
                    """,
                    (y["eski_aciklama"], y["tahsilat_id"], mid),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"geri_rowcount id={y['tahsilat_id']}")
                cur.execute(
                    f"UPDATE {YEDEK_TABLO} SET geri_yazildi = TRUE WHERE id = %s",
                    (y["id"],),
                )
                audit_yaz(
                    (lambda sql, params=None: _cal(sql, params)),
                    olay="tahsilat_marker_hedef_geri",
                    varlik="tahsilatlar",
                    kayit_id=y["tahsilat_id"],
                    musteri_id=mid,
                    kullanici="tahsilat_marker_hedef",
                    anlik={"etiket": etiket, "yedek_id": y["id"]},
                    yeni={},
                )
        try:
            from tahsilat_tutar_duzelt import _sync_panel_grid_guarded

            sync = _sync_panel_grid_guarded(mid)
        except Exception:
            sync = _sync_panel_grid_after(mid)
        return {
            "mid": mid, "ok": True, "n": len(rows),
            "etiket": etiket, "panel_sync": sync,
        }
    except Exception as ex:
        return {
            "mid": mid, "ok": False,
            "mesaj": "hiçbir değişiklik kaydedilmedi",
            "hata": str(ex)[:200],
        }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Tahsilat marker hedef dağılımı")
    p.add_argument("--mid", type=int, required=True)
    p.add_argument("--ids", required=True, help="tek id (örn. 6008)")
    p.add_argument(
        "--hedefler",
        default=None,
        help="2026-01:3163,2026-02:4398,2026-03:2439",
    )
    p.add_argument(
        "--kaynak",
        default=None,
        help="opsiyonel sıkı filtre (örn. manuel_makbuz)",
    )
    p.add_argument("--dry-run", action="store_true", default=None)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--geri", action="store_true")
    p.add_argument("--etiket", default=None)
    args = p.parse_args(argv)

    mid = int(args.mid)
    ids = _parse_ids(args.ids)
    if len(ids) != 1:
        print("HATA: tek id gerekli, gelen=", ids)
        return 2
    tid = ids[0]
    etiket = args.etiket or datetime.now(timezone.utc).strftime(
        "marker_hedef_%Y%m%dT%H%M%SZ"
    )

    if args.geri:
        r = geri_yukle(mid, etiket)
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0 if r.get("ok") else 1

    if not args.hedefler:
        print("HATA: --hedefler gerekli")
        return 2

    dry = True
    if args.apply:
        dry = False
    if args.dry_run is True:
        dry = True
    dry_eff = dry or (not _prod_write_ok())

    r = onar(
        mid, tid, args.hedefler,
        dry_run=dry_eff, etiket=etiket, kaynak=args.kaynak,
    )
    ozet = {
        "mid": r.get("mid"),
        "id": r.get("id"),
        "dry_run": r.get("dry_run"),
        "yazildi": r.get("yazildi"),
        "ok": r.get("ok"),
        "neden": r.get("neden"),
        "mesaj": r.get("mesaj"),
        "etiket": etiket,
        "plan": r.get("plan"),
    }
    print(json.dumps(ozet, ensure_ascii=False, indent=2, default=str))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
