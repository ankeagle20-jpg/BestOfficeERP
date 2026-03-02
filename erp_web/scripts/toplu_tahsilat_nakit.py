#!/usr/bin/env python3
"""
Tüm müşterilere başlangıç yıl/ayından Şubat (dahil) tarihine kadar her ay için
nakit tahsilat kaydı girer. Zaten tahsilatı olan ay atlanır.

Çalıştırma (erp_web klasöründen):
  cd erp_web && python scripts/toplu_tahsilat_nakit.py

Veya proje kökünden:
  python -c "import sys; sys.path.insert(0,'erp_web'); from scripts.toplu_tahsilat_nakit import main; main()"
"""
import os
import sys
from datetime import date

# erp_web'i path'e ekle (script erp_web/scripts/ içinden çalışsın)
_ERP_WEB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ERP_WEB not in sys.path:
    sys.path.insert(0, _ERP_WEB)
os.chdir(_ERP_WEB)

# .env yüklensin
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from db import fetch_all, execute, ensure_customers_rent_columns

MONTHS_TR = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"
]

# Bitiş: Şubat 2026 dahil
END_YEAR = 2026
END_MONTH = 2


def parse_month(m):
    if m is None:
        return 1
    try:
        return int(m)
    except Exception:
        pass
    s = str(m).strip() if m else ""
    if not s:
        return 1
    for idx, name in enumerate(MONTHS_TR, start=1):
        if name.lower() in s.lower():
            return idx
    return 1


def parse_year(y, rent_start_date):
    if y is not None:
        try:
            return int(y)
        except Exception:
            pass
    if rent_start_date:
        s = str(rent_start_date)
        for sep in (".", "-", "/"):
            parts = s.split(sep)
            if len(parts) >= 3:
                try:
                    return int(parts[2].strip()[:4])
                except Exception:
                    pass
    return date.today().year


def main():
    print("Toplu nakit tahsilat (baslangic - Subat dahil)")
    print("Musteri baslangic yil/ay ve ilk kira kullaniliyor; zaten tahsilati olan aylar atlaniyor.\n")

    ensure_customers_rent_columns()

    customers = fetch_all("""
        SELECT id, name, rent_start_date, rent_start_year, rent_start_month, ilk_kira_bedeli
        FROM customers
        ORDER BY name
    """)
    if not customers:
        print("Musteri bulunamadi.")
        return

    inserted = 0
    skipped = 0

    for c in customers:
        cid = c.get("id")
        name = (c.get("name") or "").strip() or "Musteri"
        start_year = parse_year(c.get("rent_start_year"), c.get("rent_start_date"))
        start_month = parse_month(c.get("rent_start_month"))
        tutar = float(c.get("ilk_kira_bedeli") or 0)
        if tutar <= 0:
            continue

        for year in range(start_year, END_YEAR + 1):
            for month_1based in range(1, 13):
                if year == start_year and month_1based < start_month:
                    continue
                if year == END_YEAR and month_1based > END_MONTH:
                    continue

                # Bu müşteri + bu ay için zaten tahsilat var mı?
                existing = fetch_all("""
                    SELECT 1 FROM tahsilatlar
                    WHERE (musteri_id = %s OR customer_id = %s)
                      AND EXTRACT(YEAR FROM (tahsilat_tarihi::date)) = %s
                      AND EXTRACT(MONTH FROM (tahsilat_tarihi::date)) = %s
                    LIMIT 1
                """, (cid, cid, year, month_1based))
                if existing:
                    skipped += 1
                    continue

                # Ayın 15'i tahsilat tarihi
                try:
                    tahsilat_tarihi = date(year, month_1based, 15)
                except ValueError:
                    tahsilat_tarihi = date(year, month_1based, 1)

                aciklama = "Toplu nakit - %s %s" % (MONTHS_TR[month_1based - 1], year)

                execute("""
                    INSERT INTO tahsilatlar (customer_id, tutar, odeme_turu, tahsilat_tarihi, aciklama)
                    VALUES (%s, %s, 'nakit', %s, %s)
                """, (cid, round(tutar, 2), tahsilat_tarihi, aciklama))
                inserted += 1

        print("  ok", name)

    print("\nTamamlandi: %s tahsilat eklendi (nakit), %s ay atlandi (zaten kayit vardi)." % (inserted, skipped))


if __name__ == "__main__":
    main()
