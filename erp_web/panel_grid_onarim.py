# -*- coding: utf-8 -*-
"""Bir kerelik panel/grid cache onarımı — tahsilatlar/faturalar'a dokunmaz.

--dry-run varsayılan. ALLOW_PROD_WRITE yokken yazmaz.
Yedek: musteri_tahsilat_panel_detay_yedek / musteri_aylik_grid_cache_yedek
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from db import db, execute, fetch_one


def _prod_write_ok() -> bool:
    return str(os.environ.get("ALLOW_PROD_WRITE") or "").strip() in ("1", "true", "TRUE", "yes")


def _parse_mids(s: str) -> list[int]:
    out = []
    for p in str(s or "").split(","):
        p = p.strip()
        if not p:
            continue
        out.append(int(p))
    return out


def _cap_panel_by_iso_for_onarim(
    by_iso: dict | None,
    *,
    mid: int | None = None,
    payload: dict | None = None,
    dondurma_aylar: set | None = None,
    tol: float = 0.05,
) -> dict:
    """Onarım yazımı: tahsil = min(map, brüt); kira=0 / dondurma aya yazılmaz.

    --geri bu fonksiyonu kullanmaz.
    """
    if not isinstance(by_iso, dict) or not by_iso:
        return {}
    brut_payload: dict[str, float] = {}
    if isinstance(payload, dict):
        from datetime import date as _date

        for a in payload.get("aylar") or []:
            if not isinstance(a, dict):
                continue
            try:
                iso_m = _date(int(a["yil"]), int(a["ay"]), 1).isoformat()
                bv = round(float(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil") or 0), 2)
            except (TypeError, ValueError, KeyError):
                continue
            brut_payload[iso_m] = bv
    don = set(dondurma_aylar or ())
    if not don and mid is not None:
        try:
            from routes import giris_routes as gr

            don = set(gr._dondurma_ay_kumesi(int(mid)) or ())
        except Exception:
            don = set()
    out: dict = {}
    for iso_raw, prow in by_iso.items():
        if not isinstance(prow, dict):
            continue
        iso = str(iso_raw).strip()[:10]
        if len(iso) < 7:
            continue
        ym = iso[:7]
        if ym in don:
            continue
        try:
            brut = round(float(prow.get("aylik") or prow.get("brut") or brut_payload.get(iso) or 0), 2)
        except (TypeError, ValueError):
            brut = 0.0
        if brut <= tol:
            # kira/brüt 0 — yazma
            continue
        try:
            tah = round(float(prow.get("tahsil") or 0), 2)
        except (TypeError, ValueError):
            tah = 0.0
        if tah < 0:
            tah = 0.0
        tah = round(min(tah, brut), 2)
        kalan = round(max(brut - tah, 0), 2)
        if brut > tol and tah >= brut - tol:
            kalan = 0.0
        out[iso] = {
            "aylik": brut,
            "tahsil": tah,
            "kalan": kalan,
            "tahsil_tarih": str(prow.get("tahsil_tarih") or "")[:10] or None,
        }
    return out


def _ensure_yedek_tables():
    execute(
        """
        CREATE TABLE IF NOT EXISTS musteri_tahsilat_panel_detay_yedek (
            id BIGSERIAL PRIMARY KEY,
            musteri_id INTEGER NOT NULL,
            by_iso TEXT,
            updated_at TIMESTAMPTZ,
            yedek_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            etiket TEXT
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS musteri_aylik_grid_cache_yedek (
            id BIGSERIAL PRIMARY KEY,
            musteri_id INTEGER NOT NULL,
            payload TEXT,
            updated_at TIMESTAMPTZ,
            yedek_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            etiket TEXT
        )
        """
    )


def onar_mid(mid: int, *, dry_run: bool, etiket: str) -> dict:
    """Panel + grid yeniden üret. Tahsilat/fatura yok."""
    from routes import giris_routes as gr

    mid = int(mid)
    panel_once = fetch_one(
        "SELECT by_iso, updated_at FROM musteri_tahsilat_panel_detay WHERE musteri_id = %s",
        (mid,),
    )
    grid_once = fetch_one(
        "SELECT payload, updated_at FROM musteri_aylik_grid_cache WHERE musteri_id = %s",
        (mid,),
    )
    n_panel_ay = 0
    if panel_once and panel_once.get("by_iso"):
        try:
            raw = panel_once["by_iso"]
            obj = json.loads(raw) if isinstance(raw, str) else raw
            n_panel_ay = len(obj) if isinstance(obj, dict) else 0
        except Exception:
            n_panel_ay = 0
    n_grid_ay = 0
    if grid_once and grid_once.get("payload"):
        try:
            raw = grid_once["payload"]
            obj = json.loads(raw) if isinstance(raw, str) else raw
            aylar = (obj or {}).get("aylar") if isinstance(obj, dict) else obj
            n_grid_ay = len(aylar) if isinstance(aylar, list) else 0
        except Exception:
            n_grid_ay = 0

    out = {
        "mid": mid,
        "dry_run": dry_run,
        "panel_ay_once": n_panel_ay,
        "grid_ay_once": n_grid_ay,
        "yazildi": False,
        "panel_ay_sonra": None,
        "grid_ay_sonra": None,
    }
    if dry_run or not _prod_write_ok():
        # Salt hesap — tahsil_guncelle çağırma (heal yazabilir)
        try:
            import copy

            payload = gr._read_aylik_grid_cache_payload(mid)
            if not isinstance(payload, dict):
                payload = gr._build_aylik_grid_cache_payload(mid)
            by_iso = gr._panel_by_iso_from_tahsil_map(
                mid, payload, None, trust_grid_odenen=False
            )
            by_iso = _cap_panel_by_iso_for_onarim(by_iso, mid=mid, payload=payload)
            out["panel_ay_sonra"] = len(by_iso or {})
            if isinstance(payload, dict) and by_iso:
                pl2 = copy.deepcopy(payload)
                tmap = gr._aylik_tahsil_tutar_map(mid)
                gr._apply_panel_by_iso_to_grid_payload(pl2, by_iso, tahsil_map=tmap)
                out["grid_ay_sonra"] = len(pl2.get("aylar") or [])
        except Exception as ex:
            out["hata"] = type(ex).__name__
        return out

    # Yazma: yedek öncesi heal/yazma yok — bellekte üret, tek txn
    import copy

    _ensure_yedek_tables()
    try:
        gr._invalidate_aylik_grid_payload_mem(mid)
        payload = gr._build_aylik_grid_cache_payload(mid)
        by_iso = gr._panel_by_iso_from_tahsil_map(
            mid, payload, None, trust_grid_odenen=False
        )
        by_iso = _cap_panel_by_iso_for_onarim(by_iso, mid=mid, payload=payload)
        if isinstance(payload, dict) and by_iso:
            payload = copy.deepcopy(payload)
            tmap = gr._aylik_tahsil_tutar_map(mid)
            gr._apply_panel_by_iso_to_grid_payload(payload, by_iso, tahsil_map=tmap)
    except Exception as ex:
        out["hata"] = type(ex).__name__
        raise

    panel_json = json.dumps(by_iso or {}, ensure_ascii=False)
    grid_json = json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False)
    p_yedek = (
        panel_once.get("by_iso")
        if panel_once and isinstance(panel_once.get("by_iso"), str)
        else (json.dumps(panel_once.get("by_iso"), ensure_ascii=False) if panel_once else None)
    )
    g_yedek = (
        grid_once.get("payload")
        if grid_once and isinstance(grid_once.get("payload"), str)
        else (json.dumps(grid_once.get("payload"), ensure_ascii=False) if grid_once else None)
    )

    with db() as conn:
        cur = conn.cursor()
        if panel_once and p_yedek is not None:
            cur.execute(
                """
                INSERT INTO musteri_tahsilat_panel_detay_yedek
                    (musteri_id, by_iso, updated_at, etiket)
                VALUES (%s, %s, %s, %s)
                """,
                (mid, p_yedek, panel_once.get("updated_at"), etiket),
            )
        if grid_once and g_yedek is not None:
            cur.execute(
                """
                INSERT INTO musteri_aylik_grid_cache_yedek
                    (musteri_id, payload, updated_at, etiket)
                VALUES (%s, %s, %s, %s)
                """,
                (mid, g_yedek, grid_once.get("updated_at"), etiket),
            )
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
    out["yazildi"] = True
    out["panel_ay_sonra"] = len(by_iso or {})
    out["grid_ay_sonra"] = len((payload or {}).get("aylar") or []) if isinstance(payload, dict) else 0
    return out


def geri_yukle(mid: int, etiket: str) -> dict:
    """Son yedekten panel+grid geri yükle."""
    if not _prod_write_ok():
        return {"mid": mid, "ok": False, "neden": "ALLOW_PROD_WRITE yok"}
    p = fetch_one(
        """
        SELECT by_iso FROM musteri_tahsilat_panel_detay_yedek
        WHERE musteri_id = %s AND etiket = %s
        ORDER BY id DESC LIMIT 1
        """,
        (mid, etiket),
    )
    g = fetch_one(
        """
        SELECT payload FROM musteri_aylik_grid_cache_yedek
        WHERE musteri_id = %s AND etiket = %s
        ORDER BY id DESC LIMIT 1
        """,
        (mid, etiket),
    )
    if not p and not g:
        return {"mid": mid, "ok": False, "neden": "yedek yok"}
    with db() as conn:
        cur = conn.cursor()
        if p:
            cur.execute(
                """
                INSERT INTO musteri_tahsilat_panel_detay (musteri_id, by_iso, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (musteri_id)
                DO UPDATE SET by_iso = EXCLUDED.by_iso, updated_at = NOW()
                """,
                (mid, p.get("by_iso")),
            )
        if g:
            cur.execute(
                """
                INSERT INTO musteri_aylik_grid_cache (musteri_id, payload, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (musteri_id)
                DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
                """,
                (mid, g.get("payload")),
            )
    return {"mid": mid, "ok": True}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Panel/grid onarım (tahsilat dokunulmaz)")
    p.add_argument("--mids", default="867", help="virgülle mid listesi")
    p.add_argument("--dry-run", action="store_true", default=None)
    p.add_argument("--apply", action="store_true", help="yaz (ALLOW_PROD_WRITE gerekir)")
    p.add_argument("--geri", action="store_true", help="yedekten geri yükle")
    p.add_argument("--etiket", default=None)
    args = p.parse_args(argv)
    mids = _parse_mids(args.mids)
    etiket = args.etiket or datetime.now(timezone.utc).strftime("onarim_%Y%m%dT%H%M%SZ")
    dry = True
    if args.apply:
        dry = False
    if args.dry_run is True:
        dry = True

    if args.geri:
        for mid in mids:
            r = geri_yukle(mid, etiket)
            print("geri", r)
        return 0

    ozet = {"n": len(mids), "dry_run": dry, "yazma_izin": _prod_write_ok(), "satirlar": []}
    for mid in mids:
        r = onar_mid(mid, dry_run=dry or (not _prod_write_ok()), etiket=etiket)
        ozet["satirlar"].append(r)
        print(
            "mid", r["mid"],
            "panel", r["panel_ay_once"], "->", r.get("panel_ay_sonra"),
            "grid", r["grid_ay_once"], "->", r.get("grid_ay_sonra"),
            "yazildi", r.get("yazildi"),
        )
    print("ozet_n", ozet["n"], "dry_run", ozet["dry_run"], "etiket", etiket)
    return 0


if __name__ == "__main__":
    sys.exit(main())
