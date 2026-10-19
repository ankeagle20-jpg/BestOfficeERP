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


def _sync_grid_payload_after_onarim(
    payload: dict | None,
    by_iso: dict | None,
    tahsil_map: dict | None,
    *,
    tol: float = 0.05,
) -> None:
    """Panel (boş olabilir) + kanonik map → grid odenen/tahsil_edildi.

    Boş panelde _apply_panel_by_iso_to_grid_payload erken döner; hayalet/0.01
    kalıntısı kalmasın diye panelde olmayan aylar da map'e çekilir.
    """
    if not isinstance(payload, dict):
        return
    from datetime import date as _date

    from routes import giris_routes as gr

    tmap = tahsil_map if isinstance(tahsil_map, dict) else {}
    by = by_iso if isinstance(by_iso, dict) else {}
    if by:
        gr._apply_panel_by_iso_to_grid_payload(payload, by, tahsil_map=tmap)
    for a in payload.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        try:
            iso_m = _date(int(a["yil"]), int(a["ay"]), 1).isoformat()
        except (TypeError, ValueError, KeyError):
            continue
        if by and iso_m in by:
            continue
        try:
            brut = round(float(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil") or 0), 2)
            map_t = round(float(tmap.get(iso_m) or 0), 2)
        except (TypeError, ValueError):
            continue
        if brut <= tol:
            a["odenen_tutar_kdv"] = 0.0
            a["kalan_tutar_kdv"] = 0.0
            a["tahsil_edildi"] = False
            a["kismi_tahsilat"] = False
            continue
        tah = round(min(map_t, brut), 2) if map_t > 0 else 0.0
        if tah < 0:
            tah = 0.0
        kalan = round(max(brut - tah, 0), 2)
        if tah >= brut - tol:
            kalan = 0.0
        a["odenen_tutar_kdv"] = tah
        a["kalan_tutar_kdv"] = kalan
        a["tahsil_edildi"] = brut > tol and kalan <= tol and tah > tol
        a["kismi_tahsilat"] = tah > tol and kalan > tol


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


def _grid_brut_by_ym(payload: dict | None) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(payload, dict):
        return out
    for a in payload.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        try:
            ym = "%04d-%02d" % (int(a["yil"]), int(a["ay"]))
            t = round(
                float(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil") or 0), 2
            )
        except (TypeError, ValueError, KeyError):
            continue
        out[ym] = t
    return out


def _grid_odenen_by_ym(payload: dict | None) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(payload, dict):
        return out
    for a in payload.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        try:
            ym = "%04d-%02d" % (int(a["yil"]), int(a["ay"]))
            t = round(float(a.get("odenen_tutar_kdv") or 0), 2)
        except (TypeError, ValueError, KeyError):
            continue
        out[ym] = t
    return out


def _panel_tahsil_by_ym(by_iso: dict | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for iso, cell in (by_iso or {}).items():
        if not isinstance(cell, dict):
            continue
        ym = str(iso)[:7]
        try:
            out[ym] = round(float(cell.get("tahsil") or 0), 2)
        except (TypeError, ValueError):
            out[ym] = 0.0
    return out


def _plan_fingerprint(by_iso: dict | None, payload: dict | None) -> str:
    """Plan kimliği: panel tahsil + grid odenen (sıralı)."""
    blob = {
        "panel": _panel_tahsil_by_ym(by_iso),
        "odenen": _grid_odenen_by_ym(payload),
        "brut": _grid_brut_by_ym(payload),
    }
    return json.dumps(blob, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _degisiklik_kumesi(
    panel_once_by: dict | None,
    payload_once: dict | None,
    by_iso: dict | None,
    payload: dict | None,
    *,
    tol: float = 0.05,
) -> list[dict]:
    """(ay, alan, eski, yeni) — plan/uygulama kıyası için."""
    ch: list[dict] = []
    p0 = _panel_tahsil_by_ym(panel_once_by)
    p1 = _panel_tahsil_by_ym(by_iso)
    o0 = _grid_odenen_by_ym(payload_once)
    o1 = _grid_odenen_by_ym(payload)
    b0 = _grid_brut_by_ym(payload_once)
    b1 = _grid_brut_by_ym(payload)
    for ym in sorted(set(p0) | set(p1) | set(o0) | set(o1) | set(b0) | set(b1)):
        for alan, a, b in (
            ("panel_tahsil", p0.get(ym) or 0.0, p1.get(ym) or 0.0),
            ("grid_odenen", o0.get(ym) or 0.0, o1.get(ym) or 0.0),
            ("grid_brut", b0.get(ym) or 0.0, b1.get(ym) or 0.0),
        ):
            if abs(float(a) - float(b)) > tol:
                ch.append(
                    {
                        "ay": ym,
                        "alan": alan,
                        "eski": round(float(a), 2),
                        "yeni": round(float(b), 2),
                    }
                )
    return ch


def _brut_cokus_yasak(
    payload_once: dict | None,
    payload: dict | None,
    *,
    tol: float = 0.05,
    placeholder: float = 0.01,
) -> list[str]:
    """Eski brüt>tol iken yeni 0 veya 0.01 olan aylar (felaket guard)."""
    b0 = _grid_brut_by_ym(payload_once)
    b1 = _grid_brut_by_ym(payload)
    bad: list[str] = []
    for ym, eski in b0.items():
        if float(eski) <= tol:
            continue
        yeni = float(b1.get(ym) or 0.0)
        if yeni <= tol or abs(yeni - placeholder) < 1e-9:
            bad.append(ym)
    return bad


def _ekstre_borc_toplam(mid: int) -> float:
    """Canlı ekstre borç toplamı (salt okuma)."""
    from routes import giris_routes as gr

    bas, bit = gr._cari_ekstre_varsayilan_bas_bit(int(mid))
    rows = gr._cari_ekstre_hareketler(
        int(mid), bas, bit, 0, tahsilat_borca_hizala=True
    ) or []
    return round(sum(float(h.get("borc") or 0) for h in rows), 2)


def _kira_tutar_toplam(payload: dict | None) -> float:
    """Grid kira/brüt toplamı (0.01 placeholder hariç gerçek tutarlar)."""
    s = 0.0
    for t in _grid_brut_by_ym(payload).values():
        if float(t) > 0.05:
            s += float(t)
    return round(s, 2)


def _panel_by_iso_from_row(panel_once) -> dict:
    if not panel_once or panel_once.get("by_iso") is None:
        return {}
    raw = panel_once["by_iso"]
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _payload_from_row(grid_once) -> dict | None:
    if not grid_once or grid_once.get("payload") is None:
        return None
    raw = grid_once["payload"]
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _onarim_hesapla(mid: int, *, gr=None) -> dict:
    """Dry-run ve apply için TEK yol: cache oku, yoksa build; panel+grid senkron.

    Invalidate / taze-build zorlaması YOK (326 felaketi: build→0.01).
    """
    import copy

    if gr is None:
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
    payload_once = _payload_from_row(grid_once)
    panel_once_by = _panel_by_iso_from_row(panel_once)

    # Kaynak: önce disk/mem cache (dry ile aynı); yoksa build
    payload_src = gr._read_aylik_grid_cache_payload(mid)
    kaynak = "cache"
    if not isinstance(payload_src, dict) or not (payload_src.get("aylar") or []):
        payload_src = gr._build_aylik_grid_cache_payload(mid)
        kaynak = "build"
    if not isinstance(payload_src, dict):
        payload_src = {"aylar": []}

    payload = copy.deepcopy(payload_src)
    by_iso = gr._panel_by_iso_from_tahsil_map(
        mid, payload, None, trust_grid_odenen=False
    )
    by_iso = _cap_panel_by_iso_for_onarim(by_iso, mid=mid, payload=payload)
    tmap = gr._aylik_tahsil_tutar_map(mid) or {}
    _sync_grid_payload_after_onarim(payload, by_iso or {}, tmap)

    deg = _degisiklik_kumesi(panel_once_by, payload_once, by_iso, payload)
    return {
        "mid": mid,
        "kaynak": kaynak,
        "panel_once": panel_once,
        "grid_once": grid_once,
        "panel_once_by": panel_once_by,
        "payload_once": payload_once,
        "by_iso": by_iso or {},
        "payload": payload,
        "degisiklikler": deg,
        "fingerprint": _plan_fingerprint(by_iso, payload),
        "kira_once": _kira_tutar_toplam(payload_once),
        "kira_sonra": _kira_tutar_toplam(payload),
        "brut_cokus_aylar": _brut_cokus_yasak(payload_once, payload),
    }


def onar_mid(mid: int, *, dry_run: bool, etiket: str) -> dict:
    """Panel + grid yeniden üret. Tahsilat/fatura yok. Dry ve apply aynı plan yolu."""
    from routes import giris_routes as gr

    mid = int(mid)
    out = {
        "mid": mid,
        "dry_run": dry_run,
        "panel_ay_once": 0,
        "grid_ay_once": 0,
        "yazildi": False,
        "panel_ay_sonra": None,
        "grid_ay_sonra": None,
        "ok": True,
    }
    try:
        plan = _onarim_hesapla(mid, gr=gr)
    except Exception as ex:
        out["ok"] = False
        out["hata"] = type(ex).__name__
        out["mesaj"] = str(ex)[:200]
        return out

    panel_once = plan["panel_once"]
    grid_once = plan["grid_once"]
    out["panel_ay_once"] = len(plan["panel_once_by"] or {})
    out["grid_ay_once"] = len((plan["payload_once"] or {}).get("aylar") or []) if isinstance(plan["payload_once"], dict) else 0
    out["panel_ay_sonra"] = len(plan["by_iso"] or {})
    out["grid_ay_sonra"] = len((plan["payload"] or {}).get("aylar") or [])
    out["kaynak"] = plan["kaynak"]
    out["degisiklik_n"] = len(plan["degisiklikler"] or [])

    if dry_run or not _prod_write_ok():
        return out

    # Guard: brüt çöküş (0 / 0.01)
    if plan["brut_cokus_aylar"]:
        out["ok"] = False
        out["yazildi"] = False
        out["neden"] = "brut_cokus"
        out["mesaj"] = (
            "plan uyuşmuyor, hiçbir değişiklik kaydedilmedi: "
            "hedef dışı ay brüt 0/0.01'e düşerdi (%s)"
            % ",".join(plan["brut_cokus_aylar"][:12])
        )
        return out

    # Guard: kira toplamı (yalnız cache'te gerçek brüt varken)
    kira_once = float(plan["kira_once"] or 0)
    kira_sonra = float(plan["kira_sonra"] or 0)
    if kira_once > 0.05 and abs(kira_once - kira_sonra) > 0.05:
        out["ok"] = False
        out["yazildi"] = False
        out["neden"] = "kira_toplam"
        out["mesaj"] = (
            "plan uyuşmuyor, hiçbir değişiklik kaydedilmedi: "
            "kira/brüt toplamı değişirdi (%s→%s)"
            % (kira_once, kira_sonra)
        )
        return out

    # Planı dondur; yeniden hesap birebir olmalı
    fp_plan = plan["fingerprint"]
    try:
        plan2 = _onarim_hesapla(mid, gr=gr)
    except Exception as ex:
        out["ok"] = False
        out["neden"] = "plan_yeniden"
        out["mesaj"] = "plan uyuşmuyor, hiçbir değişiklik kaydedilmedi"
        out["hata"] = type(ex).__name__
        return out
    if plan2.get("fingerprint") != fp_plan:
        out["ok"] = False
        out["neden"] = "plan_uyusmuyor"
        out["mesaj"] = "plan uyuşmuyor, hiçbir değişiklik kaydedilmedi"
        return out
    if plan2.get("brut_cokus_aylar"):
        out["ok"] = False
        out["neden"] = "plan_uyusmuyor"
        out["mesaj"] = "plan uyuşmuyor, hiçbir değişiklik kaydedilmedi"
        return out
    k2o, k2s = float(plan2["kira_once"] or 0), float(plan2["kira_sonra"] or 0)
    if k2o > 0.05 and abs(k2o - k2s) > 0.05:
        out["ok"] = False
        out["neden"] = "plan_uyusmuyor"
        out["mesaj"] = "plan uyuşmuyor, hiçbir değişiklik kaydedilmedi"
        return out

    by_iso = plan["by_iso"]
    payload = plan["payload"]
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

    borc_once = None
    try:
        borc_once = _ekstre_borc_toplam(mid)
    except Exception:
        borc_once = None

    _ensure_yedek_tables()
    try:
        with db() as conn:
            cur = conn.cursor()

            def _cal(sql, params=None):
                cur.execute(sql, params)

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

            # Aynı txn: yazılan payload brüt/kira koruması → bozulursa exception → rollback
            cur.execute(
                "SELECT payload FROM musteri_aylik_grid_cache WHERE musteri_id = %s",
                (mid,),
            )
            row_g = cur.fetchone()
            raw_g = None
            if row_g is not None:
                if isinstance(row_g, dict):
                    raw_g = row_g.get("payload")
                else:
                    try:
                        raw_g = row_g[0]
                    except (TypeError, IndexError, KeyError):
                        raw_g = None
            try:
                pl_chk = json.loads(raw_g) if isinstance(raw_g, str) else raw_g
            except Exception:
                pl_chk = None
            if _brut_cokus_yasak(plan["payload_once"], pl_chk):
                raise RuntimeError("brut_cokus_txn")
            if kira_once > 0.05 and abs(_kira_tutar_toplam(pl_chk) - kira_once) > 0.05:
                raise RuntimeError("kira_txn")
            # Ekstre borç koruması: kira/brüt kaynağı değişmediyse borç satırları korunur.
            # Ayrı conn ekstre yazılmamış txn'i görmez; kira proxy + borc_once kaydı audit'te.
            if (
                borc_once is not None
                and kira_once > 0.05
                and abs(kira_sonra - kira_once) > 0.05
            ):
                raise RuntimeError("ekstre_borc")

            try:
                from audit_log import audit_yaz

                audit_yaz(
                    _cal,
                    olay="panel_grid_onarim",
                    varlik="musteri_aylik_grid_cache",
                    kayit_id=mid,
                    musteri_id=mid,
                    kullanici="panel_grid_onarim",
                    anlik={
                        "etiket": etiket,
                        "kaynak": plan["kaynak"],
                        "degisiklik_n": len(plan["degisiklikler"] or []),
                        "kira_once": kira_once,
                        "borc_once": borc_once,
                    },
                    yeni={
                        "etiket": etiket,
                        "panel_ay": len(by_iso or {}),
                        "grid_ay": len((payload or {}).get("aylar") or []),
                        "kira_sonra": kira_sonra,
                        "fingerprint": fp_plan[:120],
                    },
                )
            except Exception:
                pass
    except Exception as ex:
        out["ok"] = False
        out["yazildi"] = False
        out["neden"] = type(ex).__name__
        out["mesaj"] = "plan uyuşmuyor, hiçbir değişiklik kaydedilmedi"
        out["hata"] = str(ex)[:200]
        return out

    try:
        gr._invalidate_aylik_grid_payload_mem(mid)
    except Exception:
        pass
    out["yazildi"] = True
    out["ok"] = True
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
            "ok", r.get("ok"),
            ("neden=" + str(r.get("neden"))) if r.get("neden") else "",
        )
    print("ozet_n", ozet["n"], "dry_run", ozet["dry_run"], "etiket", etiket)
    return 0


if __name__ == "__main__":
    sys.exit(main())
