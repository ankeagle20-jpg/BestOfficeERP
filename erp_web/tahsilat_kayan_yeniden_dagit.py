# -*- coding: utf-8 -*-
"""CLI: kayan tahsilat yeniden dağıtımı (banka_marker_onarim kalıbı).

Varsayılan --dry-run. --apply yalnız ALLOW_PROD_WRITE=1 ile yazar.
Yalnız marker/aciklama; tutar/tarih/makbuz/ref/kaynak değişmez.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from tahsilat_kayan import (
    geri_yukle_kayan,
    tahsilat_kayan_yeniden_dagit,
    _prod_write_ok,
)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Kayan tahsilat yeniden dağıtımı")
    p.add_argument("--mid", type=int, required=True)
    p.add_argument("--dry-run", action="store_true", default=None)
    p.add_argument("--apply", action="store_true", help="yaz (ALLOW_PROD_WRITE gerekir)")
    p.add_argument("--geri", action="store_true", help="yedekten geri yükle")
    p.add_argument("--etiket", default=None)
    p.add_argument("--fp", default=None, help="beklenen plan parmak izi (apply)")
    args = p.parse_args(argv)

    mid = int(args.mid)
    etiket = args.etiket or datetime.now(timezone.utc).strftime(
        "kayan_%Y%m%dT%H%M%SZ"
    )

    if args.geri:
        r = geri_yukle_kayan(mid, etiket)
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0 if r.get("ok") else 1

    dry = True
    if args.apply:
        dry = False
    if args.dry_run is True:
        dry = True
    dry_eff = dry or (not _prod_write_ok())

    r = tahsilat_kayan_yeniden_dagit(
        mid,
        dry_run=dry_eff,
        etiket=etiket,
        tetik="cli",
        beklenen_fp=args.fp,
    )
    ozet = {
        "mid": r.get("mid"),
        "dry_run": r.get("dry_run"),
        "yazildi": r.get("yazildi"),
        "degisiklik_yok": r.get("degisiklik_yok"),
        "ok": r.get("ok"),
        "neden": r.get("neden"),
        "mesaj": r.get("mesaj"),
        "etiket": etiket,
        "parmak_izi": r.get("parmak_izi"),
        "plan": r.get("plan"),
    }
    print(json.dumps(ozet, ensure_ascii=False, indent=2, default=str))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
