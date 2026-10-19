# -*- coding: utf-8 -*-
"""Tahsilat tutar düzeltme — hedef grid cache brüt.

Varsayılan DRY-RUN. --apply yalnız ALLOW_PROD_WRITE=1 ile yazar.
Yalnız tutar değişir (makbuz/tarih/kaynak/marker dokunulmaz).
Azaltma yok; ay grid'de yoksa atlanır.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

YEDEK_TABLO = "tahsilat_tutar_yedek"

_TAH_RE = re.compile(r"\|AYLIK_TAH\|([0-9]{4}-[0-9]{2}-[0-9]{2})\|")

SQL_TABLO = f"""
CREATE TABLE IF NOT EXISTS {YEDEK_TABLO} (
    id SERIAL PRIMARY KEY,
    etiket TEXT NOT NULL,
    tahsilat_id INTEGER NOT NULL,
    mid INTEGER NOT NULL,
    eski_tutar NUMERIC(14, 2) NOT NULL,
    yeni_tutar NUMERIC(14, 2) NOT NULL,
    ay_iso TEXT,
    zaman TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    geri_yazildi BOOLEAN NOT NULL DEFAULT FALSE
)
""".strip()

SQL_KILIT = "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)"


def _prod_write_ok() -> bool:
    return str(os.environ.get("ALLOW_PROD_WRITE") or "").strip() in (
        "1", "true", "TRUE", "yes",
    )


def _parse_ids(s: str) -> list[int]:
    """'99,100' veya '99-121' (tek aralık / karışık)."""
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


def ensure_tahsilat_tutar_yedek(calistir) -> None:
    if not callable(calistir):
        raise TypeError("calistir callable olmali")
    calistir(SQL_TABLO)


def validate_ddl_pglast() -> dict[str, Any]:
    from pglast import parse_sql

    bare = SQL_TABLO.replace("IF NOT EXISTS ", "")
    stmts = parse_sql(bare)
    if not stmts:
        raise ValueError("pglast: bos parse")
    create = stmts[0].stmt
    relname = create.relation.relname
    cols = []
    for elt in create.tableElts or ():
        if hasattr(elt, "colname") and elt.colname:
            cols.append(elt.colname)
    expected = {
        "id", "etiket", "tahsilat_id", "mid", "eski_tutar", "yeni_tutar",
        "ay_iso", "zaman", "geri_yazildi",
    }
    missing = sorted(expected - set(cols))
    return {"table": relname, "cols": cols, "missing": missing}


def _tek_tah_iso(aciklama: str | None) -> str | None:
    found = _TAH_RE.findall(str(aciklama or ""))
    if len(found) != 1:
        return None
    raw = found[0][:10]
    try:
        y, m, _ = raw.split("-")
        return "%04d-%02d-01" % (int(y), int(m))
    except (TypeError, ValueError):
        return None


def _grid_brut_by_iso(payload: dict | None) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(payload, dict):
        return out
    for a in payload.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        try:
            iso = "%04d-%02d-01" % (int(a["yil"]), int(a["ay"]))
            t = round(
                float(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil") or 0), 2
            )
        except (TypeError, ValueError, KeyError):
            continue
        out[iso] = t
    return out


def _load_grid_payload(mid: int, oku) -> dict | None:
    rows = oku(
        "SELECT payload FROM musteri_aylik_grid_cache WHERE musteri_id = %s",
        (int(mid),),
    ) or []
    if not rows:
        return None
    raw = rows[0].get("payload")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except Exception:
            return None
    return None


def _load_rows(mid: int, ids: list[int], oku) -> list[dict]:
    if not ids:
        raise RuntimeError("ids bos")
    ph = ",".join(["%s"] * len(ids))
    sql = f"""
        SELECT id,
               COALESCE(musteri_id, customer_id) AS mid,
               ROUND(COALESCE(tutar, 0)::numeric, 2) AS tutar,
               LEFT(tahsilat_tarihi::text, 10) AS tarih,
               COALESCE(aciklama, '') AS aciklama,
               makbuz_no,
               COALESCE(kaynak, '') AS kaynak
        FROM tahsilatlar
        WHERE id IN ({ph})
        ORDER BY id
    """
    rows = list(oku(sql, tuple(ids)) or [])
    id_set = set(ids)
    found = {int(r["id"]) for r in rows}
    if found != id_set:
        raise RuntimeError(
            f"id_listesi_uyusmuyor eksik={sorted(id_set - found)} fazla={sorted(found - id_set)}"
        )
    mids = {int(r["mid"]) for r in rows}
    if len(mids) != 1 or mid not in mids:
        raise RuntimeError(f"mid_uyusmuyor beklenen={mid} gelen={sorted(mids)}")
    return rows


def _plan_fingerprint(plans: list[dict]) -> str:
    blob = [
        {
            "id": p["id"],
            "ay": p.get("ay_iso"),
            "eski": p.get("eski_tutar"),
            "yeni": p.get("yeni_tutar"),
            "durum": p.get("durum"),
        }
        for p in plans
    ]
    raw = json.dumps(blob, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hesapla_plan(
    mid: int,
    rows: list[dict],
    *,
    grid_brut: dict[str, float] | None,
    hedef: str = "grid_brut",
    tol: float = 0.005,
) -> dict:
    if hedef != "grid_brut":
        return {"ok": False, "neden": "hedef_desteklenmiyor", "plans": []}
    brut = grid_brut if isinstance(grid_brut, dict) else {}
    plans: list[dict] = []
    for r in rows:
        tid = int(r["id"])
        eski = round(float(r.get("tutar") or 0), 2)
        ay = _tek_tah_iso(r.get("aciklama"))
        plan: dict[str, Any] = {
            "id": tid,
            "mid": mid,
            "eski_tutar": eski,
            "ay_iso": ay,
            "tarih": str(r.get("tarih") or "")[:10],
            "kaynak": r.get("kaynak"),
            "makbuz_no": r.get("makbuz_no"),
            "yeni_tutar": eski,
            "durum": "ok",
            "uyari": None,
            "degisti": False,
        }
        if not ay:
            plan["durum"] = "atla"
            plan["uyari"] = "tek_ay_tah_yok"
            plans.append(plan)
            continue
        if ay not in brut:
            plan["durum"] = "atla"
            plan["uyari"] = "ay_gridde_yok"
            plans.append(plan)
            continue
        hedef_t = round(float(brut[ay]), 2)
        plan["yeni_tutar"] = hedef_t
        if hedef_t + tol < eski:
            plan["durum"] = "atla"
            plan["uyari"] = "azaltma_yok"
            plan["yeni_tutar"] = eski
            plans.append(plan)
            continue
        if abs(hedef_t - eski) <= tol:
            plan["durum"] = "ayni"
            plan["degisti"] = False
            plans.append(plan)
            continue
        plan["durum"] = "ok"
        plan["degisti"] = True
        plans.append(plan)

    n_yaz = sum(1 for p in plans if p["degisti"])
    artis = round(
        sum(
            float(p["yeni_tutar"]) - float(p["eski_tutar"])
            for p in plans
            if p["degisti"]
        ),
        2,
    )
    fp = _plan_fingerprint(plans)
    return {
        "ok": True,
        "mid": mid,
        "plans": plans,
        "n_degisti": n_yaz,
        "n_atla": sum(1 for p in plans if p["durum"] == "atla"),
        "toplam_artis": artis,
        "fingerprint": fp,
    }


def _sync_panel_grid_guarded(mid: int) -> dict:
    """Panel/grid hizala; placeholder çöküşünde sağlıklı cache'i koru (0f2bc62)."""
    import copy

    from panel_grid_onarim import (
        _cap_panel_by_iso_for_onarim,
        _sync_grid_payload_after_onarim,
    )
    from routes import giris_routes as gr

    gr._invalidate_aylik_grid_payload_mem(mid)
    payload = gr._build_aylik_grid_cache_payload(mid)
    old = gr._aylik_grid_cache_payload_from_db(mid)
    if gr._aylik_grid_should_skip_placeholder_collapse(
        mid, payload, force_placeholder_collapse=False, old_payload=old
    ):
        return {"skipped": True, "reason": "placeholder_collapse_guard"}
    by_iso = gr._panel_by_iso_from_tahsil_map(
        mid, payload, None, trust_grid_odenen=False
    )
    by_iso = _cap_panel_by_iso_for_onarim(by_iso, mid=mid, payload=payload)
    if isinstance(payload, dict):
        payload = copy.deepcopy(payload)
        tmap = gr._aylik_tahsil_tutar_map(mid) or {}
        _sync_grid_payload_after_onarim(payload, by_iso or {}, tmap)
        if gr._aylik_grid_should_skip_placeholder_collapse(
            mid, payload, force_placeholder_collapse=False, old_payload=old
        ):
            return {"skipped": True, "reason": "placeholder_collapse_guard_after_sync"}
    panel_json = json.dumps(by_iso or {}, ensure_ascii=False)
    grid_json = json.dumps(
        payload if isinstance(payload, dict) else {}, ensure_ascii=False
    )
    from db import db

    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO musteri_tahsilat_panel_detay (musteri_id, by_iso, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (musteri_id)
            DO UPDATE SET by_iso = EXCLUDED.by_iso, updated_at = NOW()
            """,
            (mid, panel_json),
        )
        cur.execute(
            """
            INSERT INTO musteri_aylik_grid_cache (musteri_id, payload, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (musteri_id)
            DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
            """,
            (mid, grid_json),
        )
    try:
        gr._invalidate_aylik_grid_payload_mem(mid)
    except Exception:
        pass
    return {
        "skipped": False,
        "panel_ay": len(by_iso or {}),
        "grid_ay": len((payload or {}).get("aylar") or [])
        if isinstance(payload, dict)
        else 0,
    }


def duzelt(
    mid: int,
    ids: list[int],
    *,
    dry_run: bool = True,
    etiket: str,
    hedef: str = "grid_brut",
    oku=None,
    calistir=None,
    grid_brut: dict[str, float] | None = None,
    force_fail: bool = False,
) -> dict:
    mid = int(mid)
    ids = sorted(set(int(x) for x in ids))
    out: dict[str, Any] = {
        "mid": mid,
        "ids": ids,
        "dry_run": dry_run,
        "etiket": etiket,
        "hedef": hedef,
        "yazildi": False,
        "ok": True,
    }
    if oku is None:
        from db import fetch_all

        def oku(sql, params=None):
            return fetch_all(sql, params)

    rows = _load_rows(mid, ids, oku)
    if grid_brut is None:
        payload = _load_grid_payload(mid, oku)
        grid_brut = _grid_brut_by_iso(payload)
    plan = hesapla_plan(mid, rows, grid_brut=grid_brut, hedef=hedef)
    out["plan"] = {
        "ok": plan.get("ok"),
        "neden": plan.get("neden"),
        "n_degisti": plan.get("n_degisti"),
        "n_atla": plan.get("n_atla"),
        "toplam_artis": plan.get("toplam_artis"),
        "fingerprint": plan.get("fingerprint"),
        "satirlar": [
            {
                "id": p["id"],
                "ay": p.get("ay_iso"),
                "eski": p.get("eski_tutar"),
                "yeni": p.get("yeni_tutar"),
                "durum": p.get("durum"),
                "uyari": p.get("uyari"),
                "degisti": p.get("degisti"),
            }
            for p in plan.get("plans") or []
        ],
    }
    if not plan.get("ok"):
        out["ok"] = False
        out["neden"] = plan.get("neden")
        return out

    if plan.get("n_degisti", 0) == 0:
        out["degisiklik_yok"] = True
        out["mesaj"] = "degisiklik yok"
        return out

    # Plan parmak izi: ikinci hesap aynı olmalı
    plan2 = hesapla_plan(mid, rows, grid_brut=grid_brut, hedef=hedef)
    if plan2.get("fingerprint") != plan.get("fingerprint"):
        out["ok"] = False
        out["neden"] = "plan_uyusmuyor"
        out["mesaj"] = "plan uyuşmuyor, hiçbir değişiklik kaydedilmedi"
        return out

    yaz = (not dry_run) and (_prod_write_ok() or calistir is not None)
    if not yaz:
        out["yazildi"] = False
        out["mesaj"] = "dry-run veya ALLOW_PROD_WRITE yok — yazilmadi"
        return out

    if calistir is not None:
        return _apply_with_calistir(
            mid, plan, etiket, calistir, out, force_fail=force_fail
        )

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

            ensure_tahsilat_tutar_yedek(lambda sql, params=None: _cal(sql, params))
            cur.execute(SQL_KILIT, (f"tahsilat_tutar_duzelt:{mid}",))
            if force_fail:
                raise RuntimeError("force_fail")
            for p in plan["plans"]:
                if not p["degisti"]:
                    continue
                cur.execute(
                    f"""
                    INSERT INTO {YEDEK_TABLO}
                        (etiket, tahsilat_id, mid, eski_tutar, yeni_tutar, ay_iso)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        etiket, p["id"], mid,
                        p["eski_tutar"], p["yeni_tutar"], p.get("ay_iso"),
                    ),
                )
                yid = (cur.fetchone() or {}).get("id")
                cur.execute(
                    """
                    UPDATE tahsilatlar
                    SET tutar = %s
                    WHERE id = %s
                      AND COALESCE(musteri_id, customer_id) = %s
                    """,
                    (p["yeni_tutar"], p["id"], mid),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"update_rowcount id={p['id']} n={cur.rowcount}")
                cur.execute(
                    """
                    SELECT ROUND(COALESCE(tutar,0)::numeric,2) AS tutar,
                           LEFT(tahsilat_tarihi::text,10) AS tarih,
                           makbuz_no, COALESCE(kaynak,'') AS kaynak,
                           COALESCE(aciklama,'') AS aciklama
                    FROM tahsilatlar WHERE id = %s
                    """,
                    (p["id"],),
                )
                chk = cur.fetchone() or {}
                if round(float(chk.get("tutar") or 0), 2) != round(float(p["yeni_tutar"]), 2):
                    raise RuntimeError("tutar_yazilamadi")
                if str(chk.get("tarih") or "")[:10] != p["tarih"]:
                    raise RuntimeError("tarih_degisti")
                if str(chk.get("makbuz_no") or "") != str(p.get("makbuz_no") or ""):
                    raise RuntimeError("makbuz_degisti")
                if str(chk.get("kaynak") or "") != str(p.get("kaynak") or ""):
                    raise RuntimeError("kaynak_degisti")
                if _tek_tah_iso(chk.get("aciklama")) != p.get("ay_iso"):
                    raise RuntimeError("marker_degisti")
                audit_yaz(
                    (lambda sql, params=None: _cal(sql, params)),
                    olay="tahsilat_tutar_duzelt",
                    varlik="tahsilatlar",
                    kayit_id=p["id"],
                    musteri_id=mid,
                    kullanici="tahsilat_tutar_duzelt",
                    anlik={
                        "etiket": etiket,
                        "eski_tutar": p["eski_tutar"],
                        "ay_iso": p.get("ay_iso"),
                        "yedek_id": yid,
                    },
                    yeni={"yeni_tutar": p["yeni_tutar"], "etiket": etiket},
                )
        sync = _sync_panel_grid_guarded(mid)
        out["ok"] = True
        out["yazildi"] = True
        out["panel_sync"] = sync
        return out
    except Exception as ex:
        out["ok"] = False
        out["yazildi"] = False
        out["neden"] = type(ex).__name__
        out["mesaj"] = "hiçbir değişiklik kaydedilmedi"
        out["hata"] = str(ex)[:200]
        return out


def _apply_with_calistir(mid, plan, etiket, calistir, out, *, force_fail=False) -> dict:
    from audit_log import audit_yaz

    try:
        ensure_tahsilat_tutar_yedek(calistir)
        calistir(SQL_KILIT, (f"tahsilat_tutar_duzelt:{mid}",))
        if force_fail:
            raise RuntimeError("force_fail")
        for p in plan["plans"]:
            if not p["degisti"]:
                continue
            calistir(
                f"""INSERT INTO {YEDEK_TABLO}
                    (etiket, tahsilat_id, mid, eski_tutar, yeni_tutar, ay_iso)
                VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    etiket, p["id"], mid,
                    p["eski_tutar"], p["yeni_tutar"], p.get("ay_iso"),
                ),
            )
            calistir(
                "UPDATE tahsilatlar SET tutar = %s WHERE id = %s "
                "AND COALESCE(musteri_id, customer_id) = %s",
                (p["yeni_tutar"], p["id"], mid),
            )
            audit_yaz(
                calistir,
                olay="tahsilat_tutar_duzelt",
                varlik="tahsilatlar",
                kayit_id=p["id"],
                musteri_id=mid,
                kullanici="tahsilat_tutar_duzelt",
                anlik={"etiket": etiket, "eski_tutar": p["eski_tutar"]},
                yeni={"yeni_tutar": p["yeni_tutar"], "etiket": etiket},
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


def geri_yukle(mid: int, etiket: str, *, oku=None, calistir=None) -> dict:
    mid = int(mid)
    if not _prod_write_ok() and calistir is None:
        return {"mid": mid, "ok": False, "neden": "ALLOW_PROD_WRITE yok"}
    if oku is None:
        from db import fetch_all

        def oku(sql, params=None):
            return fetch_all(sql, params)

    rows = oku(
        f"""
        SELECT id, tahsilat_id, eski_tutar
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
                    "UPDATE tahsilatlar SET tutar = %s WHERE id = %s "
                    "AND COALESCE(musteri_id, customer_id) = %s",
                    (y["eski_tutar"], y["tahsilat_id"], mid),
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

            ensure_tahsilat_tutar_yedek(lambda sql, params=None: _cal(sql, params))
            for y in rows:
                cur.execute(
                    """
                    UPDATE tahsilatlar SET tutar = %s
                    WHERE id = %s AND COALESCE(musteri_id, customer_id) = %s
                    """,
                    (y["eski_tutar"], y["tahsilat_id"], mid),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"geri_rowcount id={y['tahsilat_id']}")
                cur.execute(
                    f"UPDATE {YEDEK_TABLO} SET geri_yazildi = TRUE WHERE id = %s",
                    (y["id"],),
                )
                audit_yaz(
                    (lambda sql, params=None: _cal(sql, params)),
                    olay="tahsilat_tutar_duzelt_geri",
                    varlik="tahsilatlar",
                    kayit_id=y["tahsilat_id"],
                    musteri_id=mid,
                    kullanici="tahsilat_tutar_duzelt",
                    anlik={"etiket": etiket, "yedek_id": y["id"]},
                    yeni={"eski_tutar": float(y["eski_tutar"])},
                )
        sync = _sync_panel_grid_guarded(mid)
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
    p = argparse.ArgumentParser(description="Tahsilat tutar → grid_brut düzeltme")
    p.add_argument("--mid", type=int, required=True)
    p.add_argument("--ids", required=True, help="99-121 veya 99,100,...")
    p.add_argument("--hedef", default="grid_brut", choices=["grid_brut"])
    p.add_argument("--dry-run", action="store_true", default=None)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--geri", action="store_true")
    p.add_argument("--etiket", default=None)
    args = p.parse_args(argv)

    mid = int(args.mid)
    ids = _parse_ids(args.ids)
    etiket = args.etiket or datetime.now(timezone.utc).strftime(
        "tutar_duzelt_%Y%m%dT%H%M%SZ"
    )

    if args.geri:
        r = geri_yukle(mid, etiket)
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0 if r.get("ok") else 1

    dry = True
    if args.apply:
        dry = False
    if args.dry_run is True:
        dry = True
    dry_eff = dry or (not _prod_write_ok())

    r = duzelt(mid, ids, dry_run=dry_eff, etiket=etiket, hedef=args.hedef)
    ozet = {
        "mid": r.get("mid"),
        "ids": r.get("ids"),
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
