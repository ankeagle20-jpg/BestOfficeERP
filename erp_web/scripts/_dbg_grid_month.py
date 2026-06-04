import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import fetch_one

mid = int(sys.argv[1]) if len(sys.argv) > 1 else 0
yil = int(sys.argv[2]) if len(sys.argv) > 2 else 2025
ay = int(sys.argv[3]) if len(sys.argv) > 3 else 11

row = fetch_one("SELECT payload FROM musteri_aylik_grid_cache WHERE musteri_id = %s", (mid,)) or {}
payload_raw = row.get("payload")
if not payload_raw:
    print("NO_CACHE")
    raise SystemExit(0)

p = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
aylar = p if isinstance(p, list) else ((p or {}).get("aylar") or [])
for a in aylar:
    try:
        yy = int(a.get("yil") or 0)
        mm = int(a.get("ay") or 0)
    except Exception:
        continue
    if yy == yil and mm == ay:
        print(a)
        break
