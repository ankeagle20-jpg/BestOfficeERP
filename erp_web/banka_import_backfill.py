# -*- coding: utf-8 -*-
"""Kayıtlı banka Excel dosyalarını banka_import_satirlari'na doldur (backfill).

Varsayılan DRY-RUN: dosyaları ve satır durumunu listeler, yazmaz.
Yazma yalnız --apply + ALLOW_PROD_WRITE=1 ile. İdempotent: satırı
güncel (parse_surumu yeterli) dosya atlanır; değilse satırlar
silinip yeniden yazılır. --temizle: satırları siler (dosyalar kalır),
o da --apply + ALLOW ister.

Kullanım:
    python banka_import_backfill.py
    python banka_import_backfill.py --apply
    python banka_import_backfill.py --temizle --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import banka_import_satirlari as bis

DOSYA_TABLO = bis.DOSYA_TABLO


def _prod_write_ok() -> bool:
    return str(os.environ.get("ALLOW_PROD_WRITE") or "").strip() in (
        "1", "true", "TRUE", "yes",
    )


class _Cur:
    """calistir(sql, params) -> cursor benzeri (satirlari_kaydet için)."""

    def __init__(self, calistir):
        self._cal = calistir
        self.rowcount = 0

    def execute(self, sql, params=None):
        self._cal(sql, params)
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return None


def _dosyalar(oku, dosya_id=None):
    kosul = "WHERE id = %s" if dosya_id else ""
    params = (int(dosya_id),) if dosya_id else None
    rows = oku(
        f"SELECT id, ad_gosterim, orijinal_filename "
        f"FROM {DOSYA_TABLO} {kosul} ORDER BY id ASC",
        params,
    ) or []
    return [dict(r) for r in rows]


def backfill(
    *,
    dry_run: bool = True,
    temizle: bool = False,
    dosya_id=None,
    oku=None,
    calistir=None,
    parse_dosya_fn=None,
) -> dict:
    """calistir enjekte edilirse (test) ALLOW kapısı ve DB açılışı atlanır."""
    if oku is None:
        from db import fetch_all

        def oku(sql, params=None):
            return fetch_all(sql, params)

    try:
        bis.ensure_banka_import_satirlari(
            calistir or (lambda sql, params=None: None)
        ) if calistir is not None else None
    except Exception:
        pass

    dosyalar = _dosyalar(oku, dosya_id)
    rapor = []
    for d in dosyalar:
        durum = bis.satir_durumu(oku, d["id"]) if _tablo_var(oku) else {
            "adet": 0, "guncel": False,
        }
        rapor.append({
            "dosya_id": d["id"],
            "ad_gosterim": d.get("ad_gosterim"),
            "satir_adet": durum["adet"],
            "guncel": bool(durum["guncel"]),
            "parse_durumu": d.get("parse_durumu"),
        })

    if dry_run:
        return {"ok": True, "dry_run": True, "temizle": bool(temizle), "dosyalar": rapor}
    if calistir is None and not _prod_write_ok():
        return {"ok": False, "neden": "ALLOW_PROD_WRITE yok", "dosyalar": rapor}

    def _yaz(sql, params=None):
        if calistir is not None:
            return calistir(sql, params)
        from db import execute

        return execute(sql, params) if params is not None else execute(sql)

    yazilan = 0
    atlanan = 0
    hatalar = []
    cur = _Cur(_yaz)
    try:
        bis.ensure_banka_import_satirlari(_yaz)
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "neden": type(ex).__name__, "hata": str(ex)[:200]}

    if temizle:
        for d in dosyalar:
            _yaz(f"DELETE FROM {bis.TABLO} WHERE dosya_id = %s", (int(d["id"]),))
            yazilan += 1
        return {"ok": True, "dry_run": False, "temizle": True,
                "silinen_dosya": yazilan, "dosyalar": rapor}

    if parse_dosya_fn is None:
        parse_dosya_fn = _gercek_parse_dosya
    versiyon = bis.musteri_versiyonu(oku)
    for d, r in zip(dosyalar, rapor):
        if r["guncel"]:
            atlanan += 1
            continue
        try:
            ham, banka, musteriler, ozet = parse_dosya_fn(d, oku)
            n = bis.parse_ve_kaydet_dosya(
                cur, dosya_id=int(d["id"]), banka=banka, ham=ham,
                musteriler=musteriler,
                manual_by_key=_manual_map(ham),
                versiyon=versiyon, ozet=ozet,
            )
            bis.dosya_parse_isaretle(_yaz, int(d["id"]), "ok")
            yazilan += 1
            r["yazilan_satir"] = n
        except Exception as ex:  # noqa: BLE001
            hatalar.append({"dosya_id": d["id"], "hata": str(ex)[:160]})
            try:
                bis.dosya_parse_isaretle(_yaz, int(d["id"]), "hata", hata=str(ex))
            except Exception:
                pass
    return {
        "ok": True, "dry_run": False, "temizle": False,
        "yazilan_dosya": yazilan, "atlanan_guncel": atlanan,
        "hatalar": hatalar, "dosyalar": rapor,
    }


def _tablo_var(oku) -> bool:
    try:
        oku(f"SELECT 1 FROM {bis.TABLO} LIMIT 1")
        return True
    except Exception:
        return False


def _manual_map(ham):
    try:
        from routes.banka_routes import _manual_map_for_ham

        return _manual_map_for_ham(ham)
    except Exception:
        return {}


def _gercek_parse_dosya(dosya_row, oku):
    """(ham, banka_tipi, musteriler) — canlı parse; yalnız --apply yolunda."""
    from db import fetch_one
    from routes.banka_routes import (
        _effective_tahsilat_bank_type,
        _musteriler_akbank_listesi,
    )

    row = fetch_one(
        f"SELECT excel_binary FROM {DOSYA_TABLO} WHERE id = %s",
        (int(dosya_row["id"]),),
    ) or {}
    raw = row.get("excel_binary")
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    banka = _effective_tahsilat_bank_type("AKBANK", dosya_row.get("orijinal_filename"))
    if banka == "TURKIYE_FINANS":
        from services.bank_processor import (
            standard_transactions_to_tahsilat_ham,
            upload_bank_excel,
        )

        txs = upload_bank_excel(raw, banka)
        ham, ozet = standard_transactions_to_tahsilat_ham(txs, yon="gelen")
    else:
        from services.banka_ak_import import (
            dataframe_hareket_satirlari,
            read_akbank_excel,
        )

        df = read_akbank_excel(raw)
        ham, ozet = dataframe_hareket_satirlari(df, yon="gelen")
    return ham, banka, _musteriler_akbank_listesi(), ozet


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Banka import satır backfill")
    p.add_argument("--apply", action="store_true", help="yaz (ALLOW_PROD_WRITE=1 gerekir)")
    p.add_argument("--temizle", action="store_true", help="satırları sil (dosyalar kalır)")
    p.add_argument("--dosya-id", type=int, default=None)
    args = p.parse_args(argv)
    dry = not bool(args.apply)
    r = backfill(
        dry_run=dry or not _prod_write_ok(),
        temizle=bool(args.temizle),
        dosya_id=args.dosya_id,
    )
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
