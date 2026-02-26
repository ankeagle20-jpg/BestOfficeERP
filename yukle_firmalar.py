"""
Oda firmalarını ve mevcut müşterileri DB'ye yükler.
- Listede oda no'su olanlar: Hazır Ofis (HO-xxx) olarak eklenir
- Mevcut müşterilerden eşleşmeyenler: Sanal Ofis (SO-xxxx) olarak eklenir
Çalıştır: python yukle_firmalar.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("erp.db")

# ── Oda firmaları (yukarıdaki listeden) ──────────────────────────────────────
ODA_FIRMALARI = [
    # (ad, oda_no, guncel_kira, baslangic_yil, baslangic_ay)
    ("AV.ONUR CAN TOSUN",           200, 22000,  2025, "Eylul"),
    ("FILISTIN DERNEGI",             201, 23000,  2025, "Nisan"),
    ("MEHMET SAHIN",                 202,  5000,  2024, "Ekim"),
    ("AHMET KAZAR",                  203,  6000,  2022, "Mayis"),
    ("KARECODE DIJITAL",             204, 15000,  2026, "Mart"),
    ("BARIS GULSES",                 205, 10000,  2022, "Mayis"),
    ("BOS",                          206,     0,  None, None),
    ("ZELIHA KUVVETLIISIK",          207, 23000,  2025, "Haziran"),
    ("BATUHAN OZKAN",                208,  8915,  2023, "Aralik"),
    ("OKAN SARIASLAN",               209, 14200,  2026, "Ocak"),
    ("MUSTAFA OZTURK",               210, 13000,  2021, "Haziran"),
    ("MUHAMMED EMIN ALAS",           211,  4500,  2025, "Ekim"),
    ("BOS",                          212,     0,  None, None),
    ("BOS",                          213,     0,  None, None),
    ("BOS",                          214,     0,  None, None),
    ("CAGLAR DONMEZ",                215,  8333,  2025, "Aralik"),
    ("VITA(EMRE GOKTAN)",            216,  8333,  2024, "Kasim"),
    ("BOS",                          217,     0,  None, None),
    ("BOS",                          218,     0,  None, None),
    ("BOS",                          219,     0,  None, None),
    ("BULGU ARASTIRMA MASA",         220,  4000,  2021, "Aralik"),
    ("SELCUK BARLAS",                221, 16000,  2024, "Mayis"),
    ("ECELER DIS TIC (CEYLAN HANIM)",222,  6667,  2025, "Nisan"),
    ("BOS",                          223,     0,  None, None),
    ("POZITIF TANITIM",              224,  9226,  2022, "Kasim"),
    ("GIZEM DOGAN",                  225,  4200,  2023, "Subat"),
    ("HAKAN DEMIREL",                226, 21000,  2025, "Ocak"),
]

def run():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    eklenen = 0; guncellenen = 0; atlanan = 0

    for (ad, oda_no, kira, yil, ay) in ODA_FIRMALARI:
        bos = (ad == "BOS")
        office_code = f"HO-{oda_no}"

        # ── Ofis kaydını güncelle (HO-xxx zaten var) ──
        conn.execute("""
            UPDATE offices SET
                monthly_price = ?,
                status        = ?,
                is_active     = 1
            WHERE code = ?
        """, (kira, "bos" if bos else "dolu", office_code))

        if bos:
            # Boş ofiste müşteri bağlantısını kaldır
            conn.execute("UPDATE offices SET customer_id=NULL, status='bos' WHERE code=?",
                         (office_code,))
            atlanan += 1
            continue

        # ── Müşteri var mı? ──
        existing = conn.execute(
            "SELECT id FROM customers WHERE name=?", (ad,)
        ).fetchone()

        if existing:
            cid = existing["id"]
            conn.execute("""
                UPDATE customers SET
                    current_rent     = ?,
                    rent_start_year  = ?,
                    rent_start_month = ?,
                    office_code      = ?
                WHERE id = ?
            """, (kira, yil, ay or "Ocak", office_code, cid))
            guncellenen += 1
        else:
            conn.execute("""
                INSERT INTO customers (name, current_rent, ilk_kira_bedeli,
                    rent_start_year, rent_start_month, office_code)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (ad, kira, kira, yil, ay or "Ocak", office_code))
            cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            eklenen += 1

        # ── Ofisi müşteriye bağla ──
        conn.execute("""
            UPDATE offices SET customer_id=?, status='dolu', monthly_price=?
            WHERE code=?
        """, (cid, kira, office_code))

    # ── Mevcut müşterileri Sanal Ofis olarak işaretle ──
    # (office_code'u NULL olan, yani hiç ofis atanmamış müşteriler)
    sanal_musteriler = conn.execute("""
        SELECT id, name FROM customers
        WHERE office_code IS NULL OR office_code = ''
    """).fetchall()

    sanal_eklendi = 0
    for m in sanal_musteriler:
        # Yeni SO kodu üret
        son = conn.execute("""
            SELECT code FROM offices WHERE type='Sanal Ofis'
            ORDER BY CAST(SUBSTR(code, INSTR(code,'-')+1) AS INTEGER) DESC LIMIT 1
        """).fetchone()
        if son:
            try:
                son_no = int(son["code"].split("-")[1]) + 1
            except:
                son_no = 2001
        else:
            son_no = 2001
        so_code = f"SO-{son_no}"

        conn.execute("""
            INSERT OR IGNORE INTO offices (code, type, unit_no, monthly_price, status, customer_id, is_active)
            VALUES (?, 'Sanal Ofis', ?, 0, 'dolu', ?, 1)
        """, (so_code, str(son_no), m["id"]))
        conn.execute("UPDATE customers SET office_code=? WHERE id=?", (so_code, m["id"]))
        sanal_eklendi += 1

    conn.commit()
    conn.close()

    print("=" * 50)
    print("FIRMA YUKLEME TAMAMLANDI")
    print(f"  Yeni eklenen oda firmasi  : {eklenen}")
    print(f"  Guncellenen oda firmasi   : {guncellenen}")
    print(f"  Bos oda                   : {atlanan}")
    print(f"  Sanal ofis atanan musteri : {sanal_eklendi}")
    print("=" * 50)

    # Kontrol
    conn2 = sqlite3.connect(DB_PATH)
    conn2.row_factory = sqlite3.Row
    print("\nOfis ozeti:")
    rows = conn2.execute("""
        SELECT type, COUNT(*) as toplam,
               SUM(CASE WHEN status='dolu' THEN 1 ELSE 0 END) as dolu,
               SUM(CASE WHEN status='bos'  THEN 1 ELSE 0 END) as bos
        FROM offices GROUP BY type ORDER BY type
    """).fetchall()
    for r in rows:
        print(f"  {r['type']:25s}: toplam={r['toplam']}, dolu={r['dolu']}, bos={r['bos']}")
    conn2.close()

if __name__ == "__main__":
    run()
