#!/usr/bin/env python3
"""Eski akış: data/ içindeki ilk .xlsx ile sınırlı güncelleme.

Müşteri kartı listesi için bunun yerine:

    python yukle_musteri_karti_excel.py --guncelle

Excel'den Yetkili Kişi, Hizmet Türü, Telefon, Başlangıç Tarihi, İlk Kira, Güncel Kira
alanlarını okuyup mevcut customers kayıtlarını günceller.

Eşleştirme Vergi No üzerinden yapılır (normalize edilmiş + ham hali).

Kullanım (erp_web klasöründen):

    python scripts/sync_musteri_fields_from_excel.py

"""

import os
import sys
from datetime import date, datetime
import re

import pandas as pd

# erp_web kökünü path'e ekle
_ERP_WEB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ERP_WEB not in sys.path:
    sys.path.insert(0, _ERP_WEB)

from db import db, ensure_customers_rent_columns, ensure_customers_excel_columns  # type: ignore


def _norm_tax(val):
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if s.isdigit() and len(s) == 10:
        return s.lstrip("0") or "0"
    return s


def _parse_date(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.date() if isinstance(value, datetime) else value
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except Exception:
            continue
    try:
        return pd.to_datetime(s).date()
    except Exception:
        return None


def _parse_money(value) -> float:
    """Türkçe format: 1.200 = 1200 (nokta binlik), 1.234,56 = 1234.56 (virgül ondalık)."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0
    s = s.replace(" ", "").replace("\u00a0", "")
    s = re.sub(r"[^0-9,.-]", "", s)
    if not s:
        return 0.0
    # Virgül varsa Türkçe ondalık: 1.234,56 → 1234.56
    if "," in s:
        if "." in s:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", ".")
    else:
        # Sadece nokta: 1.200 = 1200 (binlik) mı yoksa 1.25 = 1.25 (ondalık) mı?
        # Noktadan sonra tam 3 hane varsa binlik ayracı say (1.200, 1.250)
        if "." in s:
            parts = s.split(".")
            if len(parts) == 2 and len(parts[1]) == 3 and parts[1].isdigit():
                s = s.replace(".", "")  # 1.200 → 1200
            # else: 1.25 gibi ondalık bırak
    try:
        return float(s)
    except Exception:
        return 0.0


def _cell(row, col):
    if col is None:
        return None
    v = row.get(col)
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    s = str(v).strip()
    return s or None


def main() -> None:
    ensure_customers_rent_columns()
    ensure_customers_excel_columns()

    data_dir = os.path.join(_ERP_WEB, "data")
    files = [f for f in os.listdir(data_dir) if f.lower().endswith(".xlsx")]
    if not files:
        print("data/ klasöründe .xlsx dosyası yok.")
        return
    path = os.path.join(data_dir, files[0])
    print("Excel dosyası:", path)

    df = pd.read_excel(path, engine="openpyxl", header=0)

    # Sütunları normalize et
    cols = []
    for i, c in enumerate(df.columns):
        s = (str(c).strip().lower() if c is not None else "") or f"unnamed_{i}"
        cols.append(s)
    df.columns = cols

    def find_col(keys):
        for k in keys:
            for col in df.columns:
                if k in col:
                    return col
        return None

    ad_col = find_col(["ad/ünvan", "ad/unvan", "ad", "ünvan", "unvan"])
    yetkili_col = find_col(["yetkili kişi", "yetkili kisi", "yetkili"])
    hizmet_col = find_col(["hizmet türü", "hizmet turu", "hizmet"])
    email_col = find_col(["e-posta", "email", "eposta", "mail"])
    tel_col = find_col(["telefon"])
    vergi_col = find_col(["vergi no", "vergi", "vkn"])
    bas_tarih_col = find_col(["başlangıç tarihi", "baslangic tarihi"])
    ilk_kira_col = find_col(["ilk kira"])
    guncel_kira_col = find_col(["güncel kira", "guncel kira"])

    if not vergi_col:
        print("Vergi No sütunu bulunamadı, işlem iptal.")
        return

    updates = []
    for _, row in df.iterrows():
        raw_tax = _cell(row, vergi_col)
        if not raw_tax:
            continue
        tax_norm = _norm_tax(raw_tax)
        yetkili = _cell(row, yetkili_col)
        hizmet = _cell(row, hizmet_col)
        tel = _cell(row, tel_col)
        bas_tarih = _parse_date(row.get(bas_tarih_col)) if bas_tarih_col else None
        ilk_kira = _parse_money(row.get(ilk_kira_col)) if ilk_kira_col else 0.0
        guncel = _parse_money(row.get(guncel_kira_col)) if guncel_kira_col else 0.0
        if guncel <= 0 and ilk_kira > 0:
            guncel = ilk_kira
        updates.append((tax_norm, raw_tax.strip(), yetkili, hizmet, tel, bas_tarih, ilk_kira, guncel))

    print("Excel'den okunan satır (Vergi No dolu):", len(updates))

    updated = 0
    with db() as conn:
        cur = conn.cursor()
        for tax_norm, tax_raw, yetkili, hizmet, tel, bas_tarih, ilk_kira, guncel in updates:
            where_clauses = []
            params = []
            if tax_norm:
                where_clauses.append("TRIM(COALESCE(tax_number,'')) = %s")
                params.append(tax_norm)
            where_clauses.append("TRIM(COALESCE(tax_number,'')) = %s")
            params.append(tax_raw)
            sql = (
                "UPDATE customers SET yetkili_kisi=%s,hizmet_turu=%s,phone=%s,"
                "rent_start_date=%s,rent_start_year=%s,rent_start_month=%s,"
                "ilk_kira_bedeli=%s,guncel_kira_bedeli=%s WHERE "
                + " OR ".join(where_clauses)
            )
            year = bas_tarih.year if bas_tarih else None
            month_text = bas_tarih.strftime("%B") if bas_tarih else None
            cur.execute(
                sql,
                (
                    yetkili or None,
                    hizmet or None,
                    tel or None,
                    bas_tarih,
                    year,
                    month_text,
                    ilk_kira or 0.0,
                    guncel or 0.0,
                    *params,
                ),
            )
            updated += cur.rowcount or 0

    print("Güncellenen müşteri satırı:", updated)


if __name__ == "__main__":
    main()
