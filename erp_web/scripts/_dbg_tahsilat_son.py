import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import fetch_all
mid = int(sys.argv[1]) if len(sys.argv) > 1 else 883
rows = fetch_all(
    """
    SELECT id, musteri_id, customer_id, tutar, tahsilat_tarihi, makbuz_no,
           LEFT(COALESCE(aciklama,''), 200) AS aciklama, created_at
    FROM tahsilatlar
    WHERE (musteri_id = %s OR customer_id = %s)
    ORDER BY id DESC LIMIT 12
    """,
    (mid, mid),
)
for r in rows or []:
    print(r)
