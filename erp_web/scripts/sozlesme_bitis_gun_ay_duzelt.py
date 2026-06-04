# -*- coding: utf-8 -*-
"""
Tüm müşteri kartlarında (musteri_kyc) sözleşme bitiş tarihinin gün ve ayını,
sözleşme başlangıç tarihinin gün ve ayına eşitler. Bitişin yılı korunur.

Örnek:
    Başlangıç: 14/04/2021  →  Bitiş: 14/05/2027 (yanlış)
    Düzeltme sonrası:         Bitiş: 14/04/2027

Kullanım:
    python scripts/sozlesme_bitis_gun_ay_duzelt.py           # yalnızca göster (dry-run)
    python scripts/sozlesme_bitis_gun_ay_duzelt.py --uygula  # gerçekten güncelle
"""
import os
import sys
from datetime import date, datetime

_erp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _erp not in sys.path:
    sys.path.insert(0, _erp)
os.chdir(_erp)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from db import fetch_all, execute


def _parse_date(s):
    """Hem date nesnesini hem çeşitli metin formatlarını date'e çevirir."""
    if s is None:
        return None
    if isinstance(s, date) and not isinstance(s, datetime):
        return s
    if isinstance(s, datetime):
        return s.date()
    t = str(s).strip()
    if not t:
        return None
    t = t[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def _guvenli_gun_ay_degistir(bit_d, bas_d):
    """bit_d'nin yılını koruyarak gün/ay'ı bas_d'den alır; 29 Şubat gibi
    geçersiz sonuçları en yakın geçerli güne düşürür (örn. 28 Şubat)."""
    y = bit_d.year
    m = bas_d.month
    g = bas_d.day
    while True:
        try:
            return date(y, m, g)
        except ValueError:
            g -= 1
            if g < 1:
                return None


def calistir(uygula):
    rows = fetch_all(
        """
        SELECT id, musteri_id, sozlesme_tarihi, sozlesme_bitis
          FROM musteri_kyc
         WHERE sozlesme_tarihi IS NOT NULL
           AND BTRIM(sozlesme_tarihi::text) <> ''
           AND sozlesme_bitis IS NOT NULL
           AND BTRIM(sozlesme_bitis::text) <> ''
        """
    ) or []

    print(f"Toplam kontrol edilecek kayıt: {len(rows)}")
    degisecek = []
    atlanan_parse = 0
    atlanan_tarih_ayni = 0

    for r in rows:
        bas_d = _parse_date(r.get("sozlesme_tarihi"))
        bit_d = _parse_date(r.get("sozlesme_bitis"))
        if not bas_d or not bit_d:
            atlanan_parse += 1
            continue
        if bas_d.day == bit_d.day and bas_d.month == bit_d.month:
            atlanan_tarih_ayni += 1
            continue
        yeni_bit = _guvenli_gun_ay_degistir(bit_d, bas_d)
        if not yeni_bit:
            continue
        degisecek.append({
            "id": r["id"],
            "musteri_id": r.get("musteri_id"),
            "eski_bas": bas_d.isoformat(),
            "eski_bit": bit_d.isoformat(),
            "yeni_bit": yeni_bit.isoformat(),
        })

    print(f"  Zaten doğru (gün/ay eşleşiyor): {atlanan_tarih_ayni}")
    print(f"  Tarih çözümlenemedi:            {atlanan_parse}")
    print(f"  Düzeltilmesi gereken:           {len(degisecek)}")
    print()

    if not degisecek:
        print("Düzeltilecek kayıt yok.")
        return

    for d in degisecek[:30]:
        print(
            f"  kyc.id={d['id']:<6} musteri_id={d['musteri_id']}  "
            f"bas={d['eski_bas']}  bit={d['eski_bit']}  ->  yeni_bit={d['yeni_bit']}"
        )
    if len(degisecek) > 30:
        print(f"  ... ve {len(degisecek) - 30} kayıt daha.")
    print()

    if not uygula:
        print("(DRY-RUN) Hiçbir kayıt güncellenmedi. Uygulamak için:")
        print("    python scripts/sozlesme_bitis_gun_ay_duzelt.py --uygula")
        return

    print("Güncelleniyor...")
    basarili = 0
    hata = 0
    for d in degisecek:
        try:
            execute(
                "UPDATE musteri_kyc SET sozlesme_bitis = %s WHERE id = %s",
                (d["yeni_bit"], d["id"]),
            )
            basarili += 1
        except Exception as e:
            hata += 1
            print(f"  HATA id={d['id']}: {e}")

    print(f"Tamamlandı. Başarılı: {basarili}  Hata: {hata}")


def main():
    uygula = "--uygula" in sys.argv or "-y" in sys.argv
    calistir(uygula)


if __name__ == "__main__":
    main()
