import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import fetch_one

mid = int(sys.argv[1]) if len(sys.argv) > 1 else 867
row = fetch_one("SELECT payload FROM musteri_aylik_grid_cache WHERE musteri_id = %s", (mid,)) or {}
payload_raw = row.get("payload")
if not payload_raw:
    print("NO_CACHE")
    raise SystemExit(0)

p = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
aylar = p if isinstance(p, list) else ((p or {}).get("aylar") or [])
print("AY_SAYISI", len(aylar))
for a in aylar[:18]:
    print(
        a.get("yil"),
        a.get("ay"),
        "tutar=", a.get("tutar_kdv_dahil"),
        "brut=", a.get("brut_tutar_kdv"),
        "odenen=", a.get("odenen_tutar_kdv"),
        "kalan=", a.get("kalan_tutar_kdv"),
        "tahsil=", a.get("tahsil_edildi"),
        "kismi=", a.get("kismi_tahsilat"),
    )
