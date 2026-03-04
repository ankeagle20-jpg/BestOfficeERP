#!/usr/bin/env python3
"""Excel'deki Durum (FAAL / TERK) bilgisine göre customers.durum alanını günceller.

Eşleme:
- Durum = 'FAAL'  -> durum = 'aktif'
- Durum = 'TERK'  -> durum = 'pasif'

Eşleştirme Vergi No üzerinden yapılır (hem ham hem normalize edilmiş haliyle).

Çalıştırma (erp_web klasöründen):

    python scripts/update_durum_from_excel.py

"""

import os
import sys

import pandas as pd

# erp_web kökünü path'e ekle
_ERP_WEB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ERP_WEB not in sys.path:
    sys.path.insert(0, _ERP_WEB)

from db import db  # type: ignore


def _norm_tax(val):
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if s.isdigit() and len(s) == 10:
        return s.lstrip("0") or "0"
    return s


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


def main():
    data_dir = os.path.join(_ERP_WEB, "data")
    files = [f for f in os.listdir(data_dir) if f.lower().endswith(".xlsx")]
    if not files:
        print("data/ klasöründe .xlsx dosyası bulunamadı.")
        return
    path = os.path.join(data_dir, files[0])
    print("Excel dosyası:", path)

    df = pd.read_excel(path, engine="openpyxl", header=0)
    # Sütunları küçük harfe çevir
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

    tax_col = find_col(["vergi", "vkn", "tckn", "vergi no", "vergino"])
    durum_col = find_col(["durum", "status", "durumu"])

    if not tax_col or not durum_col:
        print("Vergi No veya Durum sütunu bulunamadı. tax_col=", tax_col, "durum_col=", durum_col)
        return

    updates = []  # (durum, tax_norm, tax_raw)
    for idx, row in df.iterrows():
        raw_tax = _cell(row, tax_col)
        if not raw_tax:
            continue
        tax_norm = _norm_tax(raw_tax)
        durum_raw = _cell(row, durum_col)
        if not durum_raw:
            continue
        v = str(durum_raw).strip().lower()
        if v == "faal":
            durum = "aktif"
        elif v == "terk":
            durum = "pasif"
        else:
            # Diğer değerler şimdilik atlanıyor
            continue
        updates.append((durum, tax_norm, raw_tax.strip()))

    if not updates:
        print("Excel'de FAAL/TERK değeri bulunamadı; güncellenecek kayıt yok.")
        return

    print("Excel'den okunan FAAL/TERK kayıt sayısı:", len(updates))

    updated_rows = 0
    with db() as conn:
        cur = conn.cursor()
        for durum, tax_norm, tax_raw in updates:
            params = []
            where = []
            if tax_norm:
                where.append("TRIM(COALESCE(tax_number,'')) = %s")
                params.append(tax_norm)
            where.append("TRIM(COALESCE(tax_number,'')) = %s")
            params.append(tax_raw)
            sql = "UPDATE customers SET durum=%s WHERE " + " OR ".join(where)
            cur.execute(sql, (durum, *params))
            updated_rows += cur.rowcount or 0

    print("Veritabanında güncellenen müşteri satırı:", updated_rows)


if __name__ == "__main__":
    main()
