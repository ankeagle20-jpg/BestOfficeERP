# -*- coding: utf-8 -*-
"""Müşteri / musteri_kyc sözleşme tarih alanlarının genel durum raporu."""
import os
import sys

_erp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _erp not in sys.path:
    sys.path.insert(0, _erp)
os.chdir(_erp)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from db import fetch_one, fetch_all


def main():
    t = fetch_one("SELECT COUNT(*) AS n FROM customers")
    print(f"customers (toplam):                         {t['n']}")

    t = fetch_one("SELECT COUNT(*) AS n FROM customers WHERE COALESCE(durum, '') <> 'pasif'")
    print(f"customers (pasif olmayan):                  {t['n']}")

    t = fetch_one("SELECT COUNT(*) AS n FROM musteri_kyc")
    print(f"musteri_kyc (toplam satir):                 {t['n']}")

    t = fetch_one("SELECT COUNT(DISTINCT musteri_id) AS n FROM musteri_kyc")
    print(f"musteri_kyc (farkli musteri sayisi):        {t['n']}")

    t = fetch_one(
        "SELECT COUNT(*) AS n FROM musteri_kyc "
        "WHERE sozlesme_tarihi IS NOT NULL AND BTRIM(sozlesme_tarihi::text) <> ''"
    )
    print(f"kyc: sozlesme_tarihi dolu:                  {t['n']}")

    t = fetch_one(
        "SELECT COUNT(*) AS n FROM musteri_kyc "
        "WHERE sozlesme_bitis IS NOT NULL AND BTRIM(sozlesme_bitis::text) <> ''"
    )
    print(f"kyc: sozlesme_bitis dolu:                   {t['n']}")

    t = fetch_one(
        "SELECT COUNT(*) AS n FROM musteri_kyc "
        "WHERE sozlesme_tarihi IS NOT NULL AND BTRIM(sozlesme_tarihi::text) <> '' "
        "  AND sozlesme_bitis  IS NOT NULL AND BTRIM(sozlesme_bitis::text)  <> ''"
    )
    print(f"kyc: ikisi de dolu:                         {t['n']}")

    print()
    print("EN GUNCEL KYC (musteri_id basina son id):")
    t = fetch_one(
        """
        WITH son AS (
            SELECT DISTINCT ON (musteri_id) musteri_id, id, sozlesme_tarihi, sozlesme_bitis
              FROM musteri_kyc
             ORDER BY musteri_id, id DESC
        )
        SELECT
            COUNT(*) FILTER (WHERE sozlesme_tarihi IS NOT NULL AND BTRIM(sozlesme_tarihi::text) <> '')  AS tarih_dolu,
            COUNT(*) FILTER (WHERE sozlesme_bitis  IS NOT NULL AND BTRIM(sozlesme_bitis::text)  <> '') AS bitis_dolu,
            COUNT(*) FILTER (WHERE sozlesme_tarihi IS NOT NULL AND BTRIM(sozlesme_tarihi::text) <> ''
                               AND sozlesme_bitis  IS NOT NULL AND BTRIM(sozlesme_bitis::text)  <> '') AS ikisi_dolu,
            COUNT(*) AS toplam
          FROM son
        """
    )
    print(f"  son-kyc toplam:                           {t['toplam']}")
    print(f"  son-kyc sozlesme_tarihi dolu:             {t['tarih_dolu']}")
    print(f"  son-kyc sozlesme_bitis dolu:              {t['bitis_dolu']}")
    print(f"  son-kyc ikisi dolu:                       {t['ikisi_dolu']}")

    print()
    print("KYC OLMAYAN MUSTERILER:")
    t = fetch_one(
        """
        SELECT COUNT(*) AS n FROM customers c
         WHERE NOT EXISTS (SELECT 1 FROM musteri_kyc k WHERE k.musteri_id = c.id)
        """
    )
    print(f"  hic kyc kaydi olmayan musteri:            {t['n']}")


if __name__ == "__main__":
    main()
