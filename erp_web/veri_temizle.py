import pandas as pd
import os
import re
from pathlib import Path

# --- 1. YARDIMCI FONKSİYONLAR ---
def tel_temizle(tel):
    if pd.isna(tel) or str(tel).lower() in ['nan', 'none', '', 'telefon 1', 'telefon 2', 'telofon 1']:
        return None
    tel = re.sub(r'\D', '', str(tel).strip())
    if not tel: return None
    if tel.startswith('90') and len(tel) >= 12: tel = tel[2:]
    if tel.startswith('0') and len(tel) >= 11: tel = tel[1:]
    tel = tel.lstrip('0')
    return tel[:10]

def veri_robotu(row):
    col_name = 'Excel Dosya İsmi'
    if col_name not in row or pd.isna(row[col_name]):
        return pd.Series(["Bilinmiyor", "Sanal"])
    ham_metin = str(row[col_name]).replace('.xlsx', '')
    kucuk_metin = ham_metin.lower()
    statu = "Sanal"
    if any(x in kucuk_metin for x in ['mükellef', 'mukellef', 'mükellev']): statu = "Mükellef"
    elif 'masa' in kucuk_metin: statu = "Masa"
    elif 'oda' in kucuk_metin: statu = "Oda"
    elif 'ofis' in kucuk_metin: statu = "Ofis"
    pattern = r'(?i)\b(mükellef|mukellef|mükellev|masa|oda|ofis|sanal|sanal ofis)\b'
    temiz_isim = re.sub(pattern, '', ham_metin).strip()
    temiz_isim = re.sub(r'[-–—]', ' ', temiz_isim)
    temiz_isim = ' '.join(temiz_isim.split()).strip(' .-_')
    return pd.Series([temiz_isim, statu])

# --- 2. ANA OPERASYON ---
try:
    base_path = Path(os.getcwd())
    dosya_adi = "Tum_Sozlesme_Verileri.xlsx"
    file_path = base_path / dosya_adi
    if not file_path.exists():
        file_path = base_path.parent / dosya_adi

    if not file_path.exists():
        print(f"❌ Dosya bulunamadı: {dosya_adi}")
        exit()

    print(f"🔄 Operasyon Başlıyor: {file_path}")
    df = pd.read_excel(file_path)

    # --- A - KAYMALARI HAM VERİDE DÜZELT (KRİTİK ADIM) ---
    def kayma_duzelt(row):
        t1_ham = str(row.get('Telefon 1', '')).strip()
        # Eğer Telefon 1 hücresinde sayı DEĞİL DE başlık yazıyorsa
        if any(x in t1_ham for x in ["Telofon", "Telefon"]):
            t2_ham = str(row.get('Telefon 2', '')).strip()
            # Yan tarafta (H sütununda) veri varsa onu alıp G'ye çekiyoruz
            if t2_ham and t2_ham.lower() not in ['nan', 'none', '']:
                return t2_ham
        return t1_ham

    print("🔧 Başlık kaymaları ve sütun hataları gideriliyor...")
    if 'Telefon 1' in df.columns:
        df['Telefon 1'] = df.apply(kayma_duzelt, axis=1)

    # --- B - İSİM VE STATÜ AYIKLAMA ---
    df[['Müşteri Adı', 'Statü']] = df.apply(veri_robotu, axis=1)

    # --- C - TELEFONLARI JİLETLE ---
    for col in ['Telefon 1', 'Telefon 2']:
        if col in df.columns:
            df[col] = df[col].apply(tel_temizle)

    # --- D - YEDEKLEME ---
    mask = (df['Telefon 1'].isna() | (df['Telefon 1'] == '')) & df['Telefon 2'].notna()
    df.loc[mask, 'Telefon 1'] = df.loc[mask, 'Telefon 2']

    # --- E - GEREKSİZLERİ TEMİZLE ---
    df = df[~df['Telefon 1'].astype(str).str.contains('Telofon|Telefon|nan|None', na=False)]
    df = df[df['Telefon 1'].notna() & (df['Telefon 1'] != '')]

    # --- F - VERGİ NO VE SAYISAL TEMİZLİK ---
    if 'Vergi No' in df.columns and 'T.C. Kim. No' in df.columns:
        df['Vergi No'] = df['Vergi No'].fillna(df['T.C. Kim. No'])

    for col in df.columns:
        if any(k in col for k in ['No', 'TC', 'V.N', 'Vergi']):
            df[col] = df[col].astype(str).replace(['nan', 'None', 'NaN', '0.0'], '')
            df[col] = df[col].str.split('.').str[0].str.strip()

    # --- 3. KAYDET ---
    cikti_adi = "MUSTERI_KARTI_YUKLEME_LISTESI.xlsx"
    with pd.ExcelWriter(cikti_adi, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Müşteriler')
        workbook  = writer.book
        worksheet = writer.sheets['Müşteriler']
        text_format = workbook.add_format({'num_format': '@'})
        worksheet.set_column('A:Z', None, text_format)

    print("\n" + "="*30)
    print("🔥 JİLET ÖTESİ OLDU KANKA!")
    print(f"📍 Dosya: {cikti_adi}")
    print(f"📊 Kalan Temiz Kayıt: {len(df)}")
    print("="*30)

except Exception as e:
    print(f"❌ Bir hata çıktı kanka: {e}")