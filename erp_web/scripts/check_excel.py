# -*- coding: utf-8 -*-
"""Excel müşteri listesini kontrol et: sütunlar, satır sayısı, boş/duplicate."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd

def main():
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    files = [f for f in os.listdir(data_dir) if f.lower().endswith(".xlsx")]
    if not files:
        print("data/ klasöründe .xlsx dosyası yok.")
        return
    path = os.path.join(data_dir, files[0])
    print("Dosya:", path)
    df = pd.read_excel(path, engine="openpyxl", header=0)
    print("Sütunlar:", list(df.columns))
    print("Toplam satır (veri):", len(df))

    cols_lower = [str(c).strip().lower() for c in df.columns]
    name_col = None
    for i, c in enumerate(cols_lower):
        if "ad" in c or "unvan" in c or "ünvan" in c:
            name_col = df.columns[i]
            break
    if name_col is None:
        name_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]
    empty = df[name_col].isna() | (df[name_col].astype(str).str.strip().isin(["", "nan"]))
    print("Ad/Unvan boş satır:", int(empty.sum()))

    tax_col = None
    for i, c in enumerate(cols_lower):
        if "vergi" in c:
            tax_col = df.columns[i]
            break
    if tax_col:
        t = df[tax_col].astype(str).str.strip()
        dolu = t[(t != "") & (t != "nan")]
        print("Vergi No dolu satır:", len(dolu))
        print("Vergi No tekrarlayan (duplicate):", int(dolu.duplicated().sum()))

    print("\nİlk 3 satır (ilk 5 sütun):")
    print(df.iloc[:3, :5].to_string())

if __name__ == "__main__":
    main()
