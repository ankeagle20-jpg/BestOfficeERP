# -*- coding: utf-8 -*-
"""Hibrit dağılım geri yazma. Varsayılan dry-run. --apply yazmadan önce repo dışına yedekler.

Tahsilat eşitleme ve kasa yazımı yok.
"""
import argparse
import json
import os
import sys

_erp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _erp not in sys.path:
    sys.path.insert(0, _erp)
os.chdir(_erp)
os.environ.pop("ALLOW_PROD_WRITE", None)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from reel_hibrit_geri import reel_hibrit_geri_yaz  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mid", type=int, required=True)
    ap.add_argument("--yil", type=int, required=True)
    ap.add_argument("--toplam", type=float, required=True)
    ap.add_argument("--net", type=float, required=True)
    ap.add_argument("--banka", type=float, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sonuc = reel_hibrit_geri_yaz(
        args.mid, args.yil, args.toplam, args.net, args.banka, apply=bool(args.apply)
    )
    print(json.dumps({k: v for k, v in sonuc.items() if k != "eski"}, ensure_ascii=False, default=str))
    return 0 if sonuc.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
