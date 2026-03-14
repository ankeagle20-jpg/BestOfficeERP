# -*- coding: utf-8 -*-
"""Bahar Şahin ve Döndü Hanım personel kayıtlarını tekrar ekler (yoksa)."""
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

from db import fetch_one, execute, execute_returning


def ekle(ad_soyad, giris_tarihi="2020-05-08", yillik_izin=14):
    """Ad_soyad zaten varsa atla, yoksa personel + personel_bilgi ekle."""
    mevcut = fetch_one("SELECT id FROM personel WHERE ad_soyad = %s", (ad_soyad,))
    if mevcut:
        print(f"  '{ad_soyad}' zaten var (id={mevcut['id']}), atlanıyor.")
        return
    row = execute_returning(
        """INSERT INTO personel (ad_soyad, pozisyon, telefon, email, giris_tarihi, mesai_baslangic, mesai_bitis, mac_adres, notlar, is_active)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (ad_soyad, "", "", "", giris_tarihi, "09:00", "18:30", "", "", True)
    )
    if not row:
        print(f"  '{ad_soyad}' eklenemedi.")
        return
    pid = row["id"]
    try:
        execute(
            """INSERT INTO personel_bilgi (personel_id, ise_baslama_tarihi, dogum_tarihi, yillik_izin_hakki, manuel_izin_gun, unvan, departman, tc_no)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (personel_id) DO UPDATE SET
                 ise_baslama_tarihi = EXCLUDED.ise_baslama_tarihi,
                 yillik_izin_hakki = EXCLUDED.yillik_izin_hakki""",
            (pid, giris_tarihi, None, yillik_izin, 0, "", "", "")
        )
    except Exception as e:
        if "personel_id" in str(e) or "unique" in str(e).lower():
            execute(
                """UPDATE personel_bilgi SET ise_baslama_tarihi=%s, yillik_izin_hakki=%s WHERE personel_id=%s""",
                (giris_tarihi, yillik_izin, pid)
            )
        else:
            raise
    print(f"  '{ad_soyad}' eklendi (id={pid}).")


def main():
    print("Personel kayıtları kontrol ediliyor...")
    ekle("Bahar Şahin", giris_tarihi="2020-05-08", yillik_izin=14)
    ekle("Döndü Hanım", giris_tarihi="2020-01-15", yillik_izin=14)
    print("Bitti. Personel sayfasından detayları (TC, doğum tarihi vb.) güncelleyebilirsin.")


if __name__ == "__main__":
    main()
