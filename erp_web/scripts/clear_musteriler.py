# -*- coding: utf-8 -*-
"""Tüm müşteri verilerini siler (tahsilat, fatura, kargo, sözleşme, customers)."""
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

from db import clear_all_customers, fetch_one

def main():
    n = fetch_one("SELECT COUNT(*) AS n FROM customers")
    count_before = (n or {}).get("n", 0)
    print(f"Mevcut müşteri sayısı: {count_before}")
    if count_before == 0:
        print("Zaten müşteri yok.")
        return
    print("Tüm müşteri verileri siliniyor...")
    try:
        clear_all_customers()
        n2 = fetch_one("SELECT COUNT(*) AS n FROM customers")
        count_after = (n2 or {}).get("n", 0)
        print(f"Tamamlandı. Kalan müşteri: {count_after}")
        if count_after > 0:
            print("UYARI: Hâlâ kayıt var, bir tablo atlanmış olabilir.")
        else:
            print("Şimdi Excel'den yeniden yükleyebilirsin.")
    except Exception as e:
        print(f"HATA: {e}")
        raise

if __name__ == "__main__":
    main()
