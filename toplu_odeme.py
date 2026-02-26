"""
Toplu Kira Ödeme Scripti
- Tüm müşterilerin 2021-2026 arası tüm aylarını ödenmiş olarak işaretler
- Aşağıdaki listede belirtilen ödenmemiş aylar hariç tutulur
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "erp.db"

MONTHS_TR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
             "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

# ── ÖDENMEMİŞ AYLAR LİSTESİ ──
# Format: (isim_anahtar_kelime, yıl, ay_index_1den)
ODENMEMIS = [
    # CENGİZ DEMİRKUNDAK
    ("CENGİZ DEMİRKUNDAK", 2025, 9),
    ("CENGİZ DEMİRKUNDAK", 2025, 10),
    ("CENGİZ DEMİRKUNDAK", 2025, 11),

    # Mehmet Şahin Sigorta Ekspertiz
    ("MEHMET ŞAHİN", 2025, 9),

    # Melih ÇEVİK
    ("MELİH ÇEVİK", 2025, 9),
    ("MELİH ÇEVİK", 2025, 10),
    ("MELİH ÇEVİK", 2025, 11),
    ("MELİH ÇEVİK", 2026, 1),

    # ARONYA KADIN GİRİŞİMİ
    ("ARONYA", 2025, 8),
    ("ARONYA", 2025, 9),
    ("ARONYA", 2025, 10),
    ("ARONYA", 2025, 11),
    ("ARONYA", 2025, 12),

    # OKTAY ACER
    ("OKTAY ACER", 2025, 12),
    ("OKTAY ACER", 2026, 1),
    ("OKTAY ACER", 2026, 2),

    # İNDİMON
    ("İNDİMON", 2025, 9),
    ("İNDİMON", 2025, 10),
    ("İNDİMON", 2025, 11),
    ("İNDİMON", 2025, 12),
    ("İNDİMON", 2026, 1),
    ("İNDİMON", 2026, 2),

    # EKREM ÇAVGA
    ("EKREM ÇAVGA", 2025, 10),
    ("EKREM ÇAVGA", 2025, 11),
    ("EKREM ÇAVGA", 2025, 12),
    ("EKREM ÇAVGA", 2026, 1),
    ("EKREM ÇAVGA", 2026, 2),

    # MESUT ÇAVUŞ
    ("MESUT ÇAVUŞ", 2025, 10),
    ("MESUT ÇAVUŞ", 2025, 11),
    ("MESUT ÇAVUŞ", 2026, 1),
    ("MESUT ÇAVUŞ", 2026, 2),

    # İE MİMARLIK
    ("İE MİMARLIK", 2025, 11),
    ("İE MİMARLIK", 2025, 12),
    ("İE MİMARLIK", 2026, 1),
    ("İE MİMARLIK", 2026, 2),

    # YUNUS EMRE DÜŞMEZ
    ("YUNUS EMRE", 2025, 11),
    ("YUNUS EMRE", 2025, 12),
    ("YUNUS EMRE", 2026, 1),

    # YENİ ANKARA SANAYİ
    ("YENİ ANKARA", 2025, 10),
    ("YENİ ANKARA", 2025, 11),
    ("YENİ ANKARA", 2026, 1),

    # PARADOKS PSİKOLOJİ
    ("PARADOKS", 2025, 11),
    ("PARADOKS", 2025, 12),
    ("PARADOKS", 2026, 1),

    # ÇAM GRUP TURİZM
    ("ÇAM GRUP", 2026, 1),

    # BST MEKANİK ELEKTRİK
    ("BST MEKANİK", 2026, 1),
    ("BST MEKANİK", 2026, 2),

    # GENOİL
    ("GENOİL", 2026, 1),
    ("GENOİL", 2026, 2),

    # ZELİHA KUVVETLİŞİK
    ("ZELİHA", 2026, 1),
    ("ZELİHA", 2026, 2),

    # DURMUŞ FATİH AKGÜL
    ("DURMUŞ FATİH", 2026, 1),
]


def is_odenmemis(customer_name: str, year: int, month_idx: int) -> bool:
    """Bu ay ödenmemiş listesinde mi?"""
    name_upper = customer_name.upper()
    for anahtar, y, m in ODENMEMIS:
        if anahtar.upper() in name_upper and y == year and m == month_idx:
            return True
    return False


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Tüm aktif müşterileri çek
    customers = cursor.execute(
        "SELECT id, name, rent_start_year, rent_start_month, ilk_kira_bedeli FROM customers"
    ).fetchall()

    print(f"Toplam {len(customers)} müşteri bulundu.\n")

    total_inserted = 0
    total_skipped = 0

    for c in customers:
        cid = c["id"]
        name = c["name"]
        start_year = c["rent_start_year"] or 2021
        start_month_name = c["rent_start_month"] or "Ocak"
        ilk_kira = float(c["ilk_kira_bedeli"] or 0)

        # Başlangıç ayını index'e çevir
        try:
            start_month_idx = MONTHS_TR.index(start_month_name) + 1
        except ValueError:
            start_month_idx = 1

        today_year = 2026
        today_month = 2  # Şubat 2026

        for year in range(start_year, today_year + 1):
            for month_idx in range(1, 13):
                # Başlangıç tarihinden önceyi atla
                if year == start_year and month_idx < start_month_idx:
                    continue
                # Bugünden sonrasını atla
                if year == today_year and month_idx > today_month:
                    continue

                # Ödenmemiş listesinde mi?
                if is_odenmemis(name, year, month_idx):
                    total_skipped += 1
                    continue

                month_name = MONTHS_TR[month_idx - 1]

                # Mevcut kaydı kontrol et
                existing = cursor.execute(
                    "SELECT id, amount FROM rent_payments WHERE customer_id=? AND year=? AND month=?",
                    (cid, year, month_name)
                ).fetchone()

                if existing and existing["amount"] > 0:
                    # Zaten veri var, atla
                    continue

                # İlk kiradan büyük bir tutar yok, ilk_kira'yı kullan
                amount = ilk_kira if ilk_kira > 0 else 0

                cursor.execute(
                    """INSERT INTO rent_payments (customer_id, year, month, amount)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(customer_id, year, month)
                       DO UPDATE SET amount = excluded.amount""",
                    (cid, year, month_name, amount)
                )
                total_inserted += 1

        print(f"  ✓ {name}")

    conn.commit()
    conn.close()

    print(f"\n✅ Tamamlandı!")
    print(f"   {total_inserted} ay ödenmiş olarak işaretlendi")
    print(f"   {total_skipped} ay ödenmemiş olarak bırakıldı (listenizden)")


if __name__ == "__main__":
    main()
