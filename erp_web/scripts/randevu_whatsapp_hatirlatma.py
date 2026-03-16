#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Randevu Yaklaşan Müşteriye WhatsApp Hatırlatması
Çalıştırma: python -m scripts.randevu_whatsapp_hatirlatma [--saat 24] [--dry-run]
- Önümüzdeki N saat içinde başlayacak randevuları bulur.
- Müşteri telefonuna göre wa.me linki üretir (veya isteğe bağlı API ile gönderim).
"""
import os
import sys
import argparse
import urllib.parse
from datetime import datetime, timedelta

# Proje kökü
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_APP", "app.py")

from db import fetch_all

def normalize_phone(phone):
    if not phone:
        return ""
    s = "".join(c for c in str(phone) if c.isdigit())
    if s.startswith("0") and len(s) == 11:
        s = "9" + s
    if not s.startswith("9") and len(s) == 10:
        s = "90" + s
    return s


def main():
    ap = argparse.ArgumentParser(description="Randevu WhatsApp hatırlatma")
    ap.add_argument("--saat", type=int, default=24, help="Önümüzdeki kaç saat içindeki randevular (varsayılan 24)")
    ap.add_argument("--dry-run", action="store_true", help="Sadece listele, gönderme")
    args = ap.parse_args()

    now = datetime.now()
    bitis = now + timedelta(hours=args.saat)

    rows = fetch_all("""
        SELECT r.id, r.baslangic_zamani, r.oda_adi, r.oda, r.durum,
               c.name AS musteri_adi, c.phone
        FROM randevular r
        JOIN customers c ON c.id = r.musteri_id
        WHERE r.baslangic_zamani IS NOT NULL
          AND r.baslangic_zamani >= %s AND r.baslangic_zamani <= %s
          AND COALESCE(r.durum, '') NOT IN ('İptal')
        ORDER BY r.baslangic_zamani
    """, (now, bitis))

    if not rows:
        print("Önümüzdeki {} saat içinde randevu yok.".format(args.saat))
        return

    print("Randevu hatırlatması gönderilecek {} kayıt:\n".format(len(rows)))
    for r in rows:
        bas = r.get("baslangic_zamani")
        if hasattr(bas, "strftime"):
            tarih_saat = bas.strftime("%d.%m.%Y %H:%M")
        else:
            tarih_saat = str(bas)[:16]
        oda = r.get("oda_adi") or r.get("oda") or "Toplantı odası"
        musteri = r.get("musteri_adi") or "Müşteri"
        phone = normalize_phone(r.get("phone"))
        mesaj = (
            "Sayın {}, {} tarihinde {} için randevunuz bulunmaktadır. "
            "Lütfen zamanında katılım sağlayınız.".format(musteri, tarih_saat, oda)
        )
        wa_link = "https://wa.me/{}?text={}".format(phone, urllib.parse.quote(mesaj)) if phone else ""
        print("  - {} | {} | {}".format(tarih_saat, musteri, oda))
        if phone:
            print("    WhatsApp: {}".format(wa_link))
        else:
            print("    (Telefon yok, WhatsApp atlanır)")
        if not args.dry_run and phone:
            # Burada gerçek gönderim yapılabilir (Twilio / resmi API vb.)
            # Şu an sadece linki yazdırıyoruz.
            pass
        print()
    if args.dry_run:
        print("(Dry-run: hiçbir mesaj gönderilmedi)")


if __name__ == "__main__":
    main()
