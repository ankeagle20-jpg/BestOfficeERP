#!/usr/bin/env python3
"""Müşteri verilerini sıfırla + Excel'den yeniden içe aktar.

Adımlar:
- Tüm müşteri / sözleşme / fatura / tahsilat verilerini temizler
- Verilen Excel'den müşterileri içe aktarır
- "Durum" = "Terk" olanları pasif, diğerlerini aktif işaretler
- Şubat 2026 (dahil) öncesi tüm faturaları "odendi" yapar

Çalıştırma (proje kökünden):
  python -c "import sys; sys.path.insert(0,'erp_web'); from scripts.reset_ve_import_musteriler import main; main('data/musteriler_import.xlsx')"

veya erp_web klasöründen:
  cd erp_web
  python scripts/reset_ve_import_musteriler.py data/musteriler_import.xlsx

NOT: Excel dosyası yoksa veya okunamazsa HİÇBİR veri silinmez.
"""

import os
import sys
from datetime import date, datetime
from typing import Tuple

# erp_web'i sys.path'e ekle ve çalışma klasörünü ayarla
_ERP_WEB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ERP_WEB not in sys.path:
    sys.path.insert(0, _ERP_WEB)
os.chdir(_ERP_WEB)

# .env yüklensin (Supabase ayarları için)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import pandas as pd

from db import db, ensure_customers_rent_columns


END_YEAR = 2026
END_MONTH = 2  # Şubat


def _parse_date(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except Exception:
            continue
    try:
        # pandas tarih objesi gibi parse et
        return pd.to_datetime(s).date()
    except Exception:
        return None


def _parse_money(value) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0
    # Türkçe format: 1.234,56
    s = s.replace(" ", "").replace("\u00a0", "")
    if "," in s and s.count(",") == 1 and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0


def _ensure_customers_is_active_column():
    """customers tablosuna is_active BOOLEAN sütunu ekle (yoksa)."""
    from db import execute  # lazy import, sys.path hazır
    try:
        execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")
    except Exception as e:
        print(f"[WARN] customers.is_active eklenemedi: {e}")


def reset_veri():
    """Tüm müşteri / sözleşme / fatura / tahsilat / KYC verilerini temizle."""
    print("[1] Mevcut müşteri ve finans verileri temizleniyor...")
    with db() as conn:
        cur = conn.cursor()
        # Çocuk tablolardan başlayarak sil
        for sql in [
            "DELETE FROM tahsilatlar",
            "DELETE FROM faturalar",
            "DELETE FROM sozlesmeler",
            "DELETE FROM musteri_kyc",
            "DELETE FROM kyc_belgeler",
            "DELETE FROM kargolar",
            "DELETE FROM customers",
        ]:
            try:
                cur.execute(sql)
            except Exception as e:
                print(f"[WARN] {sql} calistirilamadi: {e}")
    print("    -> Tüm ilgili kayıtlar silindi (varsa).")


def _only_digits(val: str | int | float | None) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val)
    return "".join(ch for ch in s if ch.isdigit())


def import_musteriler_from_excel(excel_path: str) -> Tuple[int, int]:
    """Excel'den müşterileri içe aktar.

    Returns: (toplam_musteri, pasif_sayisi)
    """
    print(f"[2] Excel okunuyor: {excel_path}")
    df = pd.read_excel(excel_path)
    # Kolon adlarını normalize et
    df.columns = [str(c).strip() for c in df.columns]

    required = [
        "Ad/Unvan",
        "Hizmet Türü",
        "E-posta",
        "Telefon",
        "TC Kimlik No",
        "Vergi No",
        "Başlangıç Tarihi",
        "Başlangıç Yılı",
        "Başlangıç Ayı",
        "İlk Kira",
        "Güncel Kira",
        "Durum",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"Eksik kolon(lar): {', '.join(missing)}")

    ensure_customers_rent_columns()
    _ensure_customers_is_active_column()

    toplam = 0
    pasif = 0

    with db() as conn:
        cur = conn.cursor()
        for idx, row in df.iterrows():
            satir_no = idx + 2  # başlık 1. satır varsayımı
            name = str(row.get("Ad/Unvan") or "").strip()
            if not name:
                raise ValueError(f"Satır {satir_no}: Ad/Unvan zorunlu alandır.")

            hizmet_turu = str(row.get("Hizmet Türü") or "").strip() or "Sanal Ofis"
            email = str(row.get("E-posta") or "").strip() or None
            if not email:
                raise ValueError(f"Satır {satir_no}: E-posta (Yetkili E-posta) zorunlu alandır.")

            # --- Strict doğrulamalar (Vergi No / TC / Telefon) ---
            raw_tc = row.get("TC Kimlik No")
            tc_no = _only_digits(raw_tc)
            if tc_no and len(tc_no) != 11:
                raise ValueError(f"Satır {satir_no}: TC Kimlik No 11 hane olmalıdır (değer: {raw_tc!r})")

            raw_vergi = row.get("Vergi No")
            vergi_no = _only_digits(raw_vergi)
            if not vergi_no:
                raise ValueError(f"Satır {satir_no}: Vergi No zorunlu alandır.")
            if len(vergi_no) != 10:
                raise ValueError(f"Satır {satir_no}: Vergi No 10 hane olmalıdır (değer: {raw_vergi!r})")

            raw_tel = row.get("Telefon")
            tel_digits = _only_digits(raw_tel)
            if tel_digits.startswith("0"):
                tel_digits = tel_digits[1:]
            if tel_digits and len(tel_digits) != 10:
                raise ValueError(
                    f"Satır {satir_no}: Telefon numarasını başında 0 olmadan 10 hane olarak giriniz (değer: {raw_tel!r})"
                )
            phone = tel_digits or None
            durum_raw = str(row.get("Durum") or "").strip()
            is_pasif = durum_raw.lower() == "terk"
            is_active = not is_pasif

            bas_tarih = _parse_date(row.get("Başlangıç Tarihi"))
            bas_yil = row.get("Başlangıç Yılı")
            try:
                bas_yil = int(bas_yil) if bas_yil is not None and not pd.isna(bas_yil) else (bas_tarih.year if bas_tarih else None)
            except Exception:
                bas_yil = bas_tarih.year if bas_tarih else None

            bas_ay = row.get("Başlangıç Ayı")
            bas_ay_str = str(bas_ay).strip() if bas_ay is not None and not pd.isna(bas_ay) else None

            ilk_kira = _parse_money(row.get("İlk Kira"))
            guncel_kira = _parse_money(row.get("Güncel Kira")) or ilk_kira

            tax_number = vergi_no or tc_no or None
            notes_parts = []
            if tc_no:
                notes_parts.append(f"TC: {tc_no}")
            if vergi_no:
                notes_parts.append(f"VergiNo: {vergi_no}")
            if durum_raw:
                notes_parts.append(f"DurumExcel: {durum_raw}")
            notes = "; ".join(notes_parts) if notes_parts else None

            # Customers insert
            cur.execute(
                """
                INSERT INTO customers (name, tax_number, email, phone, address, notes,
                                       rent_start_date, rent_start_year, rent_start_month,
                                       ilk_kira_bedeli, is_active)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    name,
                    tax_number,
                    email,
                    phone,
                    None,
                    notes,
                    bas_tarih,
                    bas_yil,
                    bas_ay_str,
                    guncel_kira or ilk_kira or 0.0,
                    is_active,
                ),
            )
            row_c = cur.fetchone()
            musteri_id = row_c["id"] if isinstance(row_c, dict) else row_c[0]

            # KYC / sözleşme kaydı (basit)
            try:
                cur.execute(
                    """
                    INSERT INTO musteri_kyc (
                        musteri_id, sirket_unvani, vergi_no, hizmet_turu,
                        aylik_kira, yillik_kira, sozlesme_tarihi, sozlesme_bitis, notlar
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        musteri_id,
                        name,
                        tax_number,
                        hizmet_turu,
                        guncel_kira or ilk_kira or 0.0,
                        (guncel_kira or ilk_kira or 0.0) * 12,
                        bas_tarih.isoformat() if bas_tarih else None,
                        None,
                        notes,
                    ),
                )
            except Exception as e:
                print(f"[WARN] musteri_kyc eklenemedi (id={musteri_id}): {e}")

            toplam += 1
            if is_pasif:
                pasif += 1

    return toplam, pasif


def kapat_gecmis_tahakkuklar():
    """Şubat 2026 (dahil) öncesi tüm faturaları 'odendi' yap."""
    print("[3] Şubat 2026 (dahil) öncesi tüm faturalar 'odendi' yapılıyor...")
    bitis = date(END_YEAR, END_MONTH, 28)
    from db import execute  # lazy import
    try:
        n = execute(
            """
            UPDATE faturalar
            SET durum = 'odendi'
            WHERE vade_tarihi IS NOT NULL
              AND (vade_tarihi::date) <= %s
            """,
            (bitis,),
        )
        print(f"    -> {n} fatura 'odendi' olarak güncellendi.")
    except Exception as e:
        print(f"[WARN] Faturalar guncellenemedi: {e}")


def main(excel_path: str | None = None):
    if excel_path is None and len(sys.argv) > 1:
        excel_path = sys.argv[1]
    if excel_path is None:
        excel_path = os.path.join("data", "musteriler_import.xlsx")

    if not os.path.isabs(excel_path):
        excel_path = os.path.join(_ERP_WEB, excel_path)

    if not os.path.exists(excel_path):
        print(f"[HATA] Excel dosyası bulunamadı: {excel_path}")
        print("       -> Hiçbir veri silinmedi. Dosya yolunu kontrol edip tekrar deneyin.")
        return

    # Önce Excel okunabilir mi emin olalım
    try:
        _ = pd.read_excel(excel_path, nrows=5)
    except Exception as e:
        print(f"[HATA] Excel okunamıyor: {e}")
        print("       -> Hiçbir veri silinmedi.")
        return

    # 1) Mevcut veriyi sil
    reset_veri()

    # 2) Yeni müşterileri içe aktar
    toplam, pasif = import_musteriler_from_excel(excel_path)

    # 3) Geçmiş tahakkukları kapat
    kapat_gecmis_tahakkuklar()

    # 4) Özet
    print("\n[4] ÖZET")
    print(f"  - Sisteme eklenen toplam müşteri sayısı: {toplam}")
    print(f"  - 'Terk' olduğu için PASİF işaretlenen müşteri sayısı: {pasif}")
    print("  - Şubat 2026 sonuna kadar olan tüm faturalar 'odendi' olarak işaretleme denemesi yapıldı.")


if __name__ == "__main__":
    main()
