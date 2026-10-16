# -*- coding: utf-8 -*-
"""Banka_import PAY/TAH marker onarımı — kronolojik FIFO yeniden yazım.

Varsayılan --dry-run. --apply yalnız ALLOW_PROD_WRITE=1 ile yazar.
Tutar / tarih / makbuz_no / banka_referans_no / kaynak / odeme_turu değişmez.
Yalnız aciklama içindeki |AYLIK_TAH| / |AYLIK_PAY| (ve ilgili bölüm) yenilenir.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from ay_fifo import acik_ay_dagit, borc_haritasi
from routes.faturalar_routes import (
    _aciklama_with_aylik_markers,
    _aciklama_with_aylik_pay_tokens,
    _gorunen_borc_satirlari,
    _sozlesme_ay_basi,
)
from routes.giris_routes import _tahsilat_aciklama_temizle

YEDEK_TABLO = "tahsilat_marker_yedek"

_PAY_RE = re.compile(
    r"\|AYLIK_PAY\|([0-9]{4}-[0-9]{2}-[0-9]{2})=([0-9]+(?:\.[0-9]+)?)\|"
)
_TAH_RE = re.compile(r"\|AYLIK_TAH\|([0-9]{4}-[0-9]{2}-[0-9]{2})\|")

SQL_TABLO = f"""
CREATE TABLE IF NOT EXISTS {YEDEK_TABLO} (
    id SERIAL PRIMARY KEY,
    tahsilat_id INTEGER NOT NULL,
    mid INTEGER NOT NULL,
    eski_aciklama TEXT NOT NULL,
    eski_marker_json TEXT,
    yeni_aciklama TEXT,
    etiket TEXT NOT NULL,
    islem_zamani TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    islem_tamam BOOLEAN NOT NULL DEFAULT FALSE,
    geri_yazildi BOOLEAN NOT NULL DEFAULT FALSE
)
""".strip()

SQL_KILIT = "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)"


def _prod_write_ok() -> bool:
    return str(os.environ.get("ALLOW_PROD_WRITE") or "").strip() in (
        "1", "true", "TRUE", "yes",
    )


def _parse_mids(s: str) -> list[int]:
    out: list[int] = []
    for p in str(s or "").split(","):
        p = p.strip()
        if not p:
            continue
        out.append(int(p))
    return out


def _parse_ids(s: str) -> list[int]:
    """'5983,5984' veya '5983-5989' (tek aralık / karışık)."""
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
    # sıralı tekil
    return sorted(set(out))


def _pay_list(aciklama: str | None) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for m in _PAY_RE.finditer(str(aciklama or "")):
        try:
            out.append((m.group(1), round(float(m.group(2)), 2)))
        except (TypeError, ValueError):
            continue
    return out


def _pay_map(aciklama: str | None) -> dict[str, float]:
    tot: dict[str, float] = defaultdict(float)
    for iso, v in _pay_list(aciklama):
        tot[iso] = round(tot[iso] + v, 2)
    return dict(sorted(tot.items()))


def _strip_markers(aciklama: str | None) -> str:
    return _tahsilat_aciklama_temizle(aciklama) or "Banka tahsilat"


def _build_aciklama(ham: str, pay_items: list[tuple[str, float]]) -> str:
    text = _strip_markers(ham)
    if not pay_items:
        return text
    isos = [iso for iso, _ in pay_items]
    text = _aciklama_with_aylik_markers(text, isos)
    text = _aciklama_with_aylik_pay_tokens(text, pay_items)
    return text


def ensure_tahsilat_marker_yedek(calistir) -> None:
    """Yedek tablosu — okuma/yazmadan ÖNCE çağrılır (apply yolu)."""
    if callable(calistir):
        calistir(SQL_TABLO)
    else:
        raise TypeError("calistir callable olmali")


def validate_ddl_pglast() -> dict[str, Any]:
    """CREATE TABLE ad/sütunlarını pglast ile doğrula."""
    from pglast import parse_sql

    # IF NOT EXISTS pglast CreateStmt'de ayrı; parse için sade DDL
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
        "tahsilat_id", "mid", "eski_aciklama", "eski_marker_json",
        "yeni_aciklama", "etiket", "islem_zamani", "islem_tamam", "geri_yazildi",
        "id",
    }
    missing = sorted(expected - set(cols))
    return {"table": relname, "cols": cols, "missing": missing}


def _month_totals(pay_lists) -> dict[str, float]:
    tot: dict[str, float] = defaultdict(float)
    for pays in pay_lists:
        for iso, p in pays or []:
            tot[str(iso)[:10]] = round(tot[str(iso)[:10]] + float(p), 2)
    return dict(sorted(tot.items()))


def _invariant_ok(
    rows: list[dict],
    plans: list[dict],
    borc: dict[str, float],
    *,
    mid: int,
) -> list[str]:
    """Apply öncesi/sonrası değişmezler. Boş liste = OK."""
    hatalar: list[str] = []
    if len(rows) != len(plans):
        hatalar.append("kayit_sayisi")
    sum_old = round(sum(float(r["tutar"]) for r in rows), 2)
    sum_new_pay = round(
        sum(sum(float(p) for _, p in (pl.get("yeni_pay") or [])) for pl in plans),
        2,
    )
    if abs(sum_old - sum_new_pay) > 0.02:
        hatalar.append(f"dagitim_toplam {sum_new_pay} != odeme {sum_old}")
    for pl in plans:
        if int(pl.get("mid") or 0) != int(mid):
            hatalar.append(f"mid_sapma id={pl.get('id')}")
        for k in ("tutar", "tarih", "makbuz_no", "banka_referans_no", "kaynak", "odeme_turu"):
            if pl.get(f"eski_{k}", pl.get(k)) != pl.get(k) and f"eski_{k}" in pl:
                # tutar/tarih plan içinde sabit tutulur
                pass
    brut = borc_haritasi(borc)
    for iso, v in _month_totals([pl.get("yeni_pay") for pl in plans]).items():
        cap = round(float(brut.get(iso) or 0), 2)
        if cap > 0 and v > cap + 0.02:
            hatalar.append(f"ay_asim {iso}: {v}>{cap}")
        # brüt yoksa avans ayı — izinli
    return hatalar


def hesapla_plan(
    mid: int,
    rows: list[dict],
    *,
    borc: dict | None = None,
    onceki_diger: list[dict] | None = None,
    sozlesme_basi: str | None = None,
) -> dict:
    """Saf hesap: rows (banka_import) → yeni marker planı.

    onceki_diger: onarım id'leri HARİÇ diğer tahsilatlar (PAY/TAH'li veya markersiz).
    """
    mid = int(mid)
    if not rows:
        return {"ok": False, "neden": "bos_rows", "plans": []}
    mids = {int(r["mid"]) for r in rows}
    if len(mids) != 1 or mid not in mids:
        return {"ok": False, "neden": "coklu_veya_yanlis_mid", "mids": sorted(mids), "plans": []}
    for r in rows:
        if str(r.get("kaynak") or "") != "banka_import":
            return {"ok": False, "neden": "kaynak_degil_banka_import", "id": r.get("id"), "plans": []}

    sirali = sorted(
        rows,
        key=lambda r: (str(r.get("tarih") or "")[:10], int(r.get("id") or 0)),
    )
    if borc is not None:
        borc_map = borc if isinstance(borc, dict) else borc_haritasi(borc)
        taban = sozlesme_basi  # test/saf: DB'ye gitme
    else:
        borc_map = _gorunen_borc_satirlari(mid)
        if not isinstance(borc_map, dict):
            borc_map = borc_haritasi(borc_map)
        taban = sozlesme_basi if sozlesme_basi is not None else _sozlesme_ay_basi(mid)
    diger = list(onceki_diger or [])
    batch_onceki: list[dict] = []
    plans: list[dict] = []

    for r in sirali:
        tid = int(r["id"])
        tah = str(r.get("tarih") or "")[:10]
        tutar = round(float(r.get("tutar") or 0), 2)
        eski_ac = r.get("aciklama") or ""
        eski_pay = _pay_list(eski_ac)
        onceki = list(diger) + batch_onceki
        yeni_pay = acik_ay_dagit(
            borc_map,
            tutar,
            tah,
            onceki=onceki,
            sozlesme_basi=taban,
        )
        yeni_ac = _build_aciklama(eski_ac, list(yeni_pay or []))
        degisti = (eski_ac or "").strip() != (yeni_ac or "").strip()
        plans.append({
            "id": tid,
            "mid": mid,
            "tarih": tah,
            "tutar": tutar,
            "makbuz_no": r.get("makbuz_no"),
            "banka_referans_no": r.get("banka_referans_no"),
            "kaynak": r.get("kaynak"),
            "odeme_turu": r.get("odeme_turu"),
            "eski_aciklama": eski_ac,
            "yeni_aciklama": yeni_ac,
            "eski_pay": eski_pay,
            "yeni_pay": list(yeni_pay or []),
            "eski_pay_map": _pay_map(eski_ac),
            "yeni_pay_map": {iso: float(p) for iso, p in (yeni_pay or [])},
            "degisti": degisti,
        })
        batch_onceki.append({
            "tarih": tah,
            "tutar": tutar,
            "id": tid,
            "aciklama": yeni_ac,
            "pay": list(yeni_pay or []),
        })

    inv = _invariant_ok(sirali, plans, borc_map, mid=mid)
    return {
        "ok": not inv,
        "neden": None if not inv else "invariant",
        "invariant_hatalar": inv,
        "mid": mid,
        "plans": plans,
        "n_degisti": sum(1 for p in plans if p["degisti"]),
        "odeme_toplam": round(sum(float(r["tutar"]) for r in sirali), 2),
        "dagitim_toplam": round(
            sum(sum(float(p) for _, p in pl["yeni_pay"]) for pl in plans), 2
        ),
        "ay_toplam": _month_totals([pl["yeni_pay"] for pl in plans]),
        "borc": dict(borc_haritasi(borc_map)),
    }


def _load_rows(mid: int, ids: list[int], oku) -> list[dict]:
    """oku(sql, params) → list[dict]. Sıkı kapsam kontrolü."""
    if not ids:
        raise RuntimeError("ids bos")
    placeholders = ",".join(["%s"] * len(ids))
    sql = f"""
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
        WHERE id IN ({placeholders})
        ORDER BY LEFT(tahsilat_tarihi::text, 10), id
    """
    rows = list(oku(sql, tuple(ids)) or [])
    id_set = set(ids)
    found = {int(r["id"]) for r in rows}
    if found != id_set:
        eksik = sorted(id_set - found)
        fazla = sorted(found - id_set)
        raise RuntimeError(f"id_listesi_uyusmuyor eksik={eksik} fazla={fazla}")
    mids = {int(r["mid"]) for r in rows}
    if len(mids) != 1:
        raise RuntimeError(f"coklu_mid: {sorted(mids)}")
    if mid not in mids:
        raise RuntimeError(f"mid_uyusmuyor beklenen={mid} gelen={sorted(mids)}")
    for r in rows:
        if str(r.get("kaynak") or "") != "banka_import":
            raise RuntimeError(f"kaynak_degil_banka_import id={r.get('id')} kaynak={r.get('kaynak')}")
        if int(r["id"]) not in id_set:
            raise RuntimeError(f"id_listesi_disi id={r.get('id')}")
    return rows


def _load_onceki_diger(mid: int, haric_ids: set[int], max_tarih: str, oku) -> list[dict]:
    """Ödeme gününe kadar diğer tahsilatlar (onarım id'leri hariç)."""
    sql = """
        SELECT id,
               LEFT(tahsilat_tarihi::text, 10) AS tarih,
               ROUND(COALESCE(tutar, 0)::numeric, 2) AS tutar,
               COALESCE(aciklama, '') AS aciklama
        FROM tahsilatlar
        WHERE (musteri_id = %s OR customer_id = %s)
          AND COALESCE(tutar, 0) > 0
          AND LEFT(COALESCE(tahsilat_tarihi::text, ''), 10) <= %s
        ORDER BY LEFT(tahsilat_tarihi::text, 10), id
    """
    rows = list(oku(sql, (int(mid), int(mid), str(max_tarih)[:10])) or [])
    out = []
    for r in rows:
        try:
            rid = int(r["id"])
        except (TypeError, ValueError):
            continue
        if rid in haric_ids:
            continue
        out.append({
            "id": rid,
            "tarih": str(r.get("tarih") or "")[:10],
            "tutar": r.get("tutar"),
            "aciklama": r.get("aciklama") or "",
        })
    return out


def _sync_panel_grid_after(mid: int) -> dict:
    """Marker yazımı sonrası panel + grid'i kanonik map ile hizala."""
    import copy

    from panel_grid_onarim import (
        _cap_panel_by_iso_for_onarim,
        _sync_grid_payload_after_onarim,
    )
    from routes import giris_routes as gr

    gr._invalidate_aylik_grid_payload_mem(mid)
    payload = gr._build_aylik_grid_cache_payload(mid)
    by_iso = gr._panel_by_iso_from_tahsil_map(
        mid, payload, None, trust_grid_odenen=False
    )
    by_iso = _cap_panel_by_iso_for_onarim(by_iso, mid=mid, payload=payload)
    if isinstance(payload, dict):
        payload = copy.deepcopy(payload)
        tmap = gr._aylik_tahsil_tutar_map(mid) or {}
        _sync_grid_payload_after_onarim(payload, by_iso or {}, tmap)
    panel_json = json.dumps(by_iso or {}, ensure_ascii=False)
    grid_json = json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False)
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
        "panel_ay": len(by_iso or {}),
        "grid_ay": len((payload or {}).get("aylar") or []) if isinstance(payload, dict) else 0,
    }


def onar(
    mid: int,
    ids: list[int],
    *,
    dry_run: bool = True,
    etiket: str,
    oku=None,
    calistir=None,
    borc: dict | None = None,
    onceki_diger: list[dict] | None = None,
    force_fail: bool = False,
) -> dict:
    """Ana giriş. dry_run veya ALLOW_PROD_WRITE yok → yazmaz."""
    mid = int(mid)
    ids = sorted(set(int(x) for x in ids))
    out: dict[str, Any] = {
        "mid": mid,
        "ids": ids,
        "dry_run": dry_run,
        "etiket": etiket,
        "yazildi": False,
        "degisiklik_yok": False,
    }

    # Test enjeksiyonu: oku/calistir verilmişse DB'siz
    if oku is None:
        from db import fetch_all

        def oku(sql, params=None):
            return fetch_all(sql, params)

    rows = _load_rows(mid, ids, oku)
    max_t = max(str(r.get("tarih") or "")[:10] for r in rows)
    if onceki_diger is None and borc is None:
        # canlı: borc + onceki DB'den
        try:
            borc_use = _gorunen_borc_satirlari(mid)
        except Exception as ex:
            return {**out, "ok": False, "neden": f"borc_okunamadi:{type(ex).__name__}"}
        try:
            onceki_use = _load_onceki_diger(mid, set(ids), max_t, oku)
        except Exception as ex:
            return {**out, "ok": False, "neden": f"onceki_okunamadi:{type(ex).__name__}"}
    else:
        borc_use = borc if borc is not None else {}
        onceki_use = list(onceki_diger or [])

    plan = hesapla_plan(
        mid, rows, borc=borc_use, onceki_diger=onceki_use,
    )
    out["plan"] = {
        "ok": plan.get("ok"),
        "neden": plan.get("neden"),
        "invariant_hatalar": plan.get("invariant_hatalar"),
        "n_degisti": plan.get("n_degisti"),
        "odeme_toplam": plan.get("odeme_toplam"),
        "dagitim_toplam": plan.get("dagitim_toplam"),
        "ay_toplam": plan.get("ay_toplam"),
        "satirlar": [
            {
                "id": p["id"],
                "tarih": p["tarih"],
                "tutar": p["tutar"],
                "eski_pay": p["eski_pay_map"],
                "yeni_pay": p["yeni_pay_map"],
                "degisti": p["degisti"],
            }
            for p in plan.get("plans") or []
        ],
    }
    if not plan.get("ok"):
        out["ok"] = False
        out["neden"] = plan.get("neden") or "plan_hata"
        return out

    if plan.get("n_degisti", 0) == 0:
        out["ok"] = True
        out["degisiklik_yok"] = True
        out["mesaj"] = "degisiklik yok"
        return out

    yaz = (not dry_run) and (_prod_write_ok() or calistir is not None)
    if not yaz:
        out["ok"] = True
        out["yazildi"] = False
        out["mesaj"] = "dry-run veya ALLOW_PROD_WRITE yok — yazilmadi"
        return out

    # Test enjeksiyonu: gerçek DB açılmaz
    if calistir is not None:
        return _apply_with_calistir(
            mid, plan, etiket, calistir, out, force_fail=force_fail,
        )

    # --- APPLY (canlı) ---
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

            # ensure — okuma/yazmadan önce (txn başı)
            ensure_tahsilat_marker_yedek(lambda sql, params=None: _cal(sql, params))
            cur.execute(SQL_KILIT, (f"banka_marker_onarim:{mid}",))
            if force_fail:
                raise RuntimeError("force_fail")
            for p in plan["plans"]:
                if not p["degisti"]:
                    continue
                cur.execute(
                    f"""
                    INSERT INTO {YEDEK_TABLO}
                        (tahsilat_id, mid, eski_aciklama, eski_marker_json,
                         yeni_aciklama, etiket, islem_tamam)
                    VALUES (%s, %s, %s, %s, %s, %s, FALSE)
                    RETURNING id
                    """,
                    (
                        p["id"], mid, p["eski_aciklama"],
                        json.dumps(p["eski_pay_map"], ensure_ascii=False),
                        p["yeni_aciklama"], etiket,
                    ),
                )
                yid = (cur.fetchone() or {}).get("id")
                cur.execute(
                    """
                    UPDATE tahsilatlar
                    SET aciklama = %s
                    WHERE id = %s
                      AND COALESCE(musteri_id, customer_id) = %s
                      AND COALESCE(kaynak, '') = 'banka_import'
                    """,
                    (p["yeni_aciklama"], p["id"], mid),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"update_rowcount id={p['id']} n={cur.rowcount}")
                # tutar/tarih/ref dokunulmaz — doğrula
                cur.execute(
                    """
                    SELECT ROUND(COALESCE(tutar,0)::numeric,2) AS tutar,
                           LEFT(tahsilat_tarihi::text,10) AS tarih,
                           banka_referans_no, makbuz_no, kaynak, odeme_turu
                    FROM tahsilatlar WHERE id = %s
                    """,
                    (p["id"],),
                )
                chk = cur.fetchone() or {}
                if round(float(chk.get("tutar") or 0), 2) != p["tutar"]:
                    raise RuntimeError("tutar_degisti")
                if str(chk.get("tarih") or "")[:10] != p["tarih"]:
                    raise RuntimeError("tarih_degisti")
                if str(chk.get("banka_referans_no") or "") != str(p.get("banka_referans_no") or ""):
                    raise RuntimeError("ref_degisti")
                if str(chk.get("makbuz_no") or "") != str(p.get("makbuz_no") or ""):
                    raise RuntimeError("makbuz_degisti")
                if str(chk.get("kaynak") or "") != "banka_import":
                    raise RuntimeError("kaynak_degisti")
                audit_yaz(
                    (lambda sql, params=None: _cal(sql, params)),
                    olay="banka_marker_onarim",
                    varlik="tahsilatlar",
                    kayit_id=p["id"],
                    musteri_id=mid,
                    kullanici="banka_marker_onarim",
                    anlik={
                        "etiket": etiket,
                        "eski_pay": p["eski_pay_map"],
                        "yedek_id": yid,
                    },
                    yeni={"yeni_pay": p["yeni_pay_map"], "etiket": etiket},
                )
                cur.execute(
                    f"UPDATE {YEDEK_TABLO} SET islem_tamam = TRUE WHERE id = %s",
                    (yid,),
                )
            if force_fail:
                raise RuntimeError("force_fail")
        # txn commit sonrası panel/grid (marker'lar kalıcı)
        sync = _sync_panel_grid_after(mid)
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
    """Sahte DB / birim test apply yolu. Yazmadan önce hata → hiç yazılmaz."""
    from audit_log import audit_yaz

    try:
        # ensure okumadan/yazmadan önce
        ensure_tahsilat_marker_yedek(calistir)
        calistir(SQL_KILIT, (f"banka_marker_onarim:{mid}",))
        if force_fail:
            raise RuntimeError("force_fail")
        for p in plan["plans"]:
            if not p["degisti"]:
                continue
            calistir(
                f"""INSERT INTO {YEDEK_TABLO}
                    (tahsilat_id, mid, eski_aciklama, eski_marker_json,
                     yeni_aciklama, etiket, islem_tamam)
                VALUES (%s, %s, %s, %s, %s, %s, FALSE)""",
                (
                    p["id"], mid, p["eski_aciklama"],
                    json.dumps(p["eski_pay_map"], ensure_ascii=False),
                    p["yeni_aciklama"], etiket,
                ),
            )
            calistir(
                "UPDATE tahsilatlar SET aciklama = %s WHERE id = %s "
                "AND COALESCE(musteri_id, customer_id) = %s "
                "AND COALESCE(kaynak, '') = 'banka_import'",
                (p["yeni_aciklama"], p["id"], mid),
            )
            audit_yaz(
                calistir,
                olay="banka_marker_onarim",
                varlik="tahsilatlar",
                kayit_id=p["id"],
                musteri_id=mid,
                kullanici="banka_marker_onarim",
                anlik={"etiket": etiket, "eski_pay": p["eski_pay_map"]},
                yeni={"yeni_pay": p["yeni_pay_map"], "etiket": etiket},
            )
            calistir(
                f"UPDATE {YEDEK_TABLO} SET islem_tamam = TRUE WHERE tahsilat_id = %s AND etiket = %s",
                (p["id"], etiket),
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
    """Yedekten eski aciklama'yı birebir geri yazar."""
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
                    "AND COALESCE(musteri_id, customer_id) = %s "
                    "AND COALESCE(kaynak, '') = 'banka_import'",
                    (y["eski_aciklama"], y["tahsilat_id"], mid),
                )
                calistir(
                    f"UPDATE {YEDEK_TABLO} SET geri_yazildi = TRUE WHERE id = %s",
                    (y["id"],),
                )
            return {"mid": mid, "ok": True, "n": len(rows)}
        except Exception as ex:
            return {
                "mid": mid, "ok": False,
                "mesaj": "hiçbir değişiklik kaydedilmedi",
                "hata": str(ex)[:200],
            }

    from db import db

    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute(SQL_KILIT, (f"banka_marker_onarim:{mid}",))
            for y in rows:
                cur.execute(
                    """
                    UPDATE tahsilatlar SET aciklama = %s
                    WHERE id = %s
                      AND COALESCE(musteri_id, customer_id) = %s
                      AND COALESCE(kaynak, '') = 'banka_import'
                    """,
                    (y["eski_aciklama"], y["tahsilat_id"], mid),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"geri_update id={y['tahsilat_id']}")
                cur.execute(
                    f"UPDATE {YEDEK_TABLO} SET geri_yazildi = TRUE WHERE id = %s",
                    (y["id"],),
                )
        try:
            _sync_panel_grid_after(mid)
        except Exception:
            pass
        return {"mid": mid, "ok": True, "n": len(rows)}
    except Exception as ex:
        return {
            "mid": mid, "ok": False,
            "mesaj": "hiçbir değişiklik kaydedilmedi",
            "hata": str(ex)[:200],
        }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Banka_import marker kronolojik onarım")
    p.add_argument("--mids", required=True, help="tek mid (virgüllü çoklu yasak — durur)")
    p.add_argument("--ids", required=True, help="5983-5989 veya 5983,5984,...")
    p.add_argument("--dry-run", action="store_true", default=None)
    p.add_argument("--apply", action="store_true", help="yaz (ALLOW_PROD_WRITE gerekir)")
    p.add_argument("--geri", action="store_true", help="yedekten geri yükle")
    p.add_argument("--etiket", default=None)
    args = p.parse_args(argv)

    mids = _parse_mids(args.mids)
    if len(mids) != 1:
        print("HATA: tek mid gerekli, gelen=", mids)
        return 2
    mid = mids[0]
    ids = _parse_ids(args.ids)
    etiket = args.etiket or datetime.now(timezone.utc).strftime("banka_marker_%Y%m%dT%H%M%SZ")

    if args.geri:
        r = geri_yukle(mid, etiket)
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0 if r.get("ok") else 1

    dry = True
    if args.apply:
        dry = False
    if args.dry_run is True:
        dry = True
    # apply bile olsa ALLOW yoksa yazma
    dry_eff = dry or (not _prod_write_ok())

    r = onar(mid, ids, dry_run=dry_eff, etiket=etiket)
    # PII yok — özet yazdır
    ozet = {
        "mid": r.get("mid"),
        "ids": r.get("ids"),
        "dry_run": r.get("dry_run"),
        "yazildi": r.get("yazildi"),
        "degisiklik_yok": r.get("degisiklik_yok"),
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
