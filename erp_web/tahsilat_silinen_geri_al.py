# -*- coding: utf-8 -*-
"""Silinen tahsilatı yedekten geri alma CLI.

Varsayılan DRY-RUN (yalnız listeler). Yazma yalnız --apply + ALLOW_PROD_WRITE=1
ile. Geri ekleme AYNI id ile; id tahsilatlar'da zaten varsa durur (hiçbir
şey yazmaz). Başarıda yedek satırı geri_alindi=TRUE işaretlenir, audit
yazılır ve müşteri panel+grid senkronu çalışır.

Kullanım:
    python tahsilat_silinen_geri_al.py --yedek-id 12
    python tahsilat_silinen_geri_al.py --tahsilat-id 6043 --apply
    python tahsilat_silinen_geri_al.py --mid 867 --apply

DİKKAT — silme_yolu='ekstre-kayit-donustur' yedekleri: bu kayıtlar
silinirken yerlerine tediye kaydı oluşturulmuştur. Geri almak eski
tahsilatı tediyenin YANINA tekrar yaratır (çift kayıt riski). Bu yüzden
bu yoldan gelen adaylar ayrıca --donusturulmus-onayla bayrağı ister;
bayrak yoksa uyarı verilip durulur.

Retention (yalnız belge; purge işi ayrı, yazılmadı): geri_alindi=TRUE
yedekler 90 gün, geri alınmamışlar 12 ay saklanır.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from tahsilat_silme import YEDEK_TABLO, ensure_tahsilat_silinen_yedek

_SUTUN_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _prod_write_ok() -> bool:
    return str(os.environ.get("ALLOW_PROD_WRITE") or "").strip() in (
        "1", "true", "TRUE", "yes",
    )


def _adaylar(oku, *, yedek_id=None, tahsilat_id=None, mid=None) -> list:
    kosul = ["COALESCE(geri_alindi, FALSE) = FALSE"]
    params: list = []
    if yedek_id:
        kosul.append("id = %s")
        params.append(int(yedek_id))
    if tahsilat_id:
        kosul.append("tahsilat_id = %s")
        params.append(int(tahsilat_id))
    if mid:
        kosul.append("musteri_id = %s")
        params.append(int(mid))
    rows = oku(
        f"""
        SELECT id, tahsilat_id, musteri_id, silme_yolu, satir_json
        FROM {YEDEK_TABLO}
        WHERE {" AND ".join(kosul)}
        ORDER BY id ASC
        """,
        tuple(params),
    ) or []
    return [dict(r) for r in rows]


def _ozet(rows) -> list:
    out = []
    for r in rows:
        try:
            govde = json.loads(r.get("satir_json") or "{}")
        except (TypeError, ValueError):
            govde = {}
        out.append({
            "yedek_id": r.get("id"),
            "tahsilat_id": r.get("tahsilat_id"),
            "mid": r.get("musteri_id"),
            "silme_yolu": r.get("silme_yolu"),
            "tutar": govde.get("tutar"),
            "tahsilat_tarihi": str(govde.get("tahsilat_tarihi") or "")[:10] or None,
        })
    return out


DONUSTUR_YOLU = "ekstre-kayit-donustur"
DONUSTUR_UYARI = (
    "silme_yolu='ekstre-kayit-donustur' kaydi: geri alma, donusturme ile "
    "olusturulan tediyenin yanina eski tahsilati tekrar yaratir (cift kayit "
    "riski). Devam icin --donusturulmus-onayla bayragi gerekli."
)


def geri_al(
    *,
    yedek_id=None,
    tahsilat_id=None,
    mid=None,
    dry_run: bool = True,
    oku=None,
    calistir=None,
    tahsilat_sutunlar=None,
    donusturulmus_onayla: bool = False,
) -> dict:
    """calistir enjekte edilirse (test) ALLOW kapısı ve DB açılışı atlanır."""
    if not (yedek_id or tahsilat_id or mid):
        return {"ok": False, "neden": "secim gerekli (--yedek-id/--tahsilat-id/--mid)"}
    if oku is None:
        from db import fetch_all

        def oku(sql, params=None):
            return fetch_all(sql, params)

    try:
        rows = _adaylar(oku, yedek_id=yedek_id, tahsilat_id=tahsilat_id, mid=mid)
    except Exception as ex:
        return {"ok": False, "neden": type(ex).__name__, "hata": str(ex)[:200]}
    if not rows:
        return {"ok": True, "dry_run": bool(dry_run), "aday": [], "n": 0, "neden": "yedek yok"}
    ozet = _ozet(rows)
    donustur_ids = [
        int(r["id"]) for r in rows if str(r.get("silme_yolu") or "") == DONUSTUR_YOLU
    ]
    if donustur_ids and not donusturulmus_onayla:
        # Dönüştürülmüş kayıt: bayrak yoksa uyar + dur (dry-run'da da yazılmaz,
        # apply'da da). Bayrakla çağrı tekrarlanmalı.
        sonuc = {
            "ok": bool(dry_run),
            "dry_run": bool(dry_run),
            "aday": ozet,
            "n": len(rows),
            "donusturulmus_yedek_idler": donustur_ids,
            "uyari": DONUSTUR_UYARI,
        }
        if not dry_run:
            sonuc["neden"] = "donusturulmus kayit onayi gerekli (--donusturulmus-onayla)"
        return sonuc
    if dry_run:
        return {"ok": True, "dry_run": True, "aday": ozet, "n": len(rows)}
    if calistir is None and not _prod_write_ok():
        return {"ok": False, "neden": "ALLOW_PROD_WRITE yok", "aday": ozet}

    # id çakışması: herhangi biri tahsilatlar'da varsa dur, hiçbir şey yazma.
    ids = [int(r["tahsilat_id"]) for r in rows]
    var = oku("SELECT id FROM tahsilatlar WHERE id = ANY(%s)", (ids,)) or []
    if var:
        return {
            "ok": False,
            "neden": "id cakismasi",
            "cakisan_idler": sorted(int(v["id"]) for v in var),
            "aday": ozet,
        }

    def _tek(calistir_) -> list:
        ensure_tahsilat_silinen_yedek(calistir_)
        sutunlar = tahsilat_sutunlar
        if sutunlar is None:
            sutunlar = [
                str(r["column_name"])
                for r in (oku(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'tahsilatlar'"
                ) or [])
            ]
        sutunlar = [s for s in sutunlar if _SUTUN_RE.match(str(s))]
        yazilan_midler = []
        for r in rows:
            govde = json.loads(r.get("satir_json") or "{}")
            cols = [c for c in sutunlar if c in govde]
            if "id" not in cols:
                raise RuntimeError("satir_json id icermiyor")
            vals = [govde.get(c) for c in cols]
            calistir_(
                "INSERT INTO tahsilatlar (%s) VALUES (%s)"
                % (", ".join(cols), ", ".join(["%s"] * len(cols))),
                tuple(vals),
            )
            calistir_(
                f"UPDATE {YEDEK_TABLO} SET geri_alindi = TRUE WHERE id = %s",
                (int(r["id"]),),
            )
            try:
                from audit_log import audit_yaz

                audit_yaz(
                    calistir_,
                    olay="tahsilat_silinen_geri_al",
                    varlik="tahsilatlar",
                    kayit_id=int(r["tahsilat_id"]),
                    musteri_id=r.get("musteri_id"),
                    kullanici="tahsilat_silinen_geri_al",
                    anlik={"yedek_id": int(r["id"]), "silme_yolu": r.get("silme_yolu")},
                    yeni={"tutar": govde.get("tutar")},
                )
            except Exception:
                pass
            m = r.get("musteri_id")
            if m and int(m) not in yazilan_midler:
                yazilan_midler.append(int(m))
        # Aynı id geri gelişi seri sequence'i geride bırakmasın.
        try:
            calistir_(
                "SELECT setval(pg_get_serial_sequence('tahsilatlar', 'id'), "
                "(SELECT MAX(id) FROM tahsilatlar))"
            )
        except Exception:
            pass
        return yazilan_midler

    if calistir is not None:
        try:
            midler = _tek(calistir)
            return {"ok": True, "dry_run": False, "n": len(rows), "midler": midler, "aday": ozet}
        except Exception as ex:
            return {"ok": False, "neden": type(ex).__name__, "hata": str(ex)[:200], "aday": ozet}

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

            midler = _tek(_cal)
    except Exception as ex:
        return {"ok": False, "neden": type(ex).__name__, "hata": str(ex)[:200], "aday": ozet}

    sync = {}
    for m in midler:
        try:
            from routes.giris_routes import resync_panel_and_grid_after_tahsil_change

            sync[str(m)] = bool(resync_panel_and_grid_after_tahsil_change(m))
        except Exception:
            sync[str(m)] = False
    return {
        "ok": True, "dry_run": False, "n": len(rows),
        "midler": midler, "panel_grid_sync": sync, "aday": ozet,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Silinen tahsilatı yedekten geri al")
    p.add_argument("--yedek-id", type=int, default=None)
    p.add_argument("--tahsilat-id", type=int, default=None)
    p.add_argument("--mid", type=int, default=None)
    p.add_argument("--apply", action="store_true", help="yaz (ALLOW_PROD_WRITE=1 gerekir)")
    p.add_argument("--dry-run", action="store_true", default=None)
    p.add_argument(
        "--donusturulmus-onayla", action="store_true",
        help="silme_yolu='ekstre-kayit-donustur' yedeklerini geri almayi onayla (cift kayit riski)",
    )
    args = p.parse_args(argv)
    dry = not bool(args.apply)
    if args.dry_run:
        dry = True
    r = geri_al(
        yedek_id=args.yedek_id,
        tahsilat_id=args.tahsilat_id,
        mid=args.mid,
        dry_run=dry or not _prod_write_ok(),
        donusturulmus_onayla=bool(args.donusturulmus_onayla),
    )
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
