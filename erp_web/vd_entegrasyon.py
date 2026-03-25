import pandas as pd
from fuzzywuzzy import fuzz
from fuzzywuzzy import process
import os
import re
from pathlib import Path

# --- 1. YARDIMCI FONKSİYONLAR ---
def dosya_yolu_bul(dosya_adi):
    yollari = [Path(__file__).resolve().parent / dosya_adi, Path(__file__).resolve().parent.parent / dosya_adi, Path(os.path.expanduser("~/Desktop")) / dosya_adi]
    for yol in yollari:
        if yol.exists(): return yol
    return None

def isim_normalize_et(metin):
    if pd.isna(metin): return ""
    metin = str(metin).upper()
    metin = metin.replace("İ", "I").replace("Ğ", "G").replace("Ü", "U").replace("Ş", "S").replace("Ö", "O").replace("Ç", "C")
    # Parazit temizliği
    parazitler = ["YENI KURULACAK", "TASF HAL", "TASFIYE HALINDE", "LTD", "STI", "A S", "AS", "LIMITED", "SIRKETI", "SAN", "TIC"]
    for p in parazitler:
        metin = metin.replace(p, "")
    metin = re.sub(r'[^\w\s]', '', metin)
    return " ".join(metin.split())

def ilk_iki_kelime_al(metin):
    temiz = isim_normalize_et(metin)
    k = temiz.split()
    return " ".join(k[:2]) if len(k) >= 2 else "".join(k)

def temizle_vkn(deger):
    if pd.isna(deger): return ""
    return re.sub(r'\D', '', str(deger).split('.')[0])

# --- 2. YÜKLEME ---
sozlesme_path = dosya_yolu_bul("Tum_Sozlesme_Verileri.xlsx")
vergi_path = dosya_yolu_bul("Vergi_Dairesi_Listesi.xlsx")

df_sozlesme = pd.read_excel(sozlesme_path)
df_vergi = pd.read_excel(vergi_path)

for col in ["VD_Vergi_No", "VD_Durum", "VD_Ise_Baslama", "VD_Isi_Birakma"]:
    df_sozlesme[col] = None

# Hızlı arama için sözlükler
mevcut_isimler = {isim_normalize_et(i): i for i in df_sozlesme['İsim/Ünvan'].dropna().unique()}
ilk_iki_haritasi = {ilk_iki_kelime_al(i): i for i in df_sozlesme['İsim/Ünvan'].dropna().unique()}

# --- 3. KADEMELİ EŞLEŞTİRME ---
print("\n🔄 Hibrit Süzgeç Operasyonu Başladı...")

for idx, v_row in df_vergi.iterrows():
    v_vkn = temizle_vkn(v_row['Vergi Kimlik No'])
    v_unvan_orig = str(v_row['Unvan / Şirket Adı'])
    v_temiz = isim_normalize_et(v_unvan_orig)
    v_ilk_iki = ilk_iki_kelime_al(v_unvan_orig)
    
    match = pd.DataFrame()

    # KADEME 1: T.C. / VERGİ NO (Kesin Bilgi)
    if v_vkn:
        mask = (df_sozlesme['T.C. Kim. No'].apply(temizle_vkn) == v_vkn) | \
               (df_sozlesme['Vergi No'].apply(temizle_vkn) == v_vkn)
        match = df_sozlesme[mask]

    # KADEME 2: İLK İKİ KELİME (Marka Uyumu)
    if match.empty and v_ilk_iki:
        if v_ilk_iki in ilk_iki_haritasi:
            gercek_isim = ilk_iki_haritasi[v_ilk_iki]
            match = df_sozlesme[df_sozlesme['İsim/Ünvan'] == gercek_isim]

    # KADEME 3: FUZZY %85 (Esnek Benzerlik)
    if match.empty and v_temiz:
        res = process.extractOne(v_temiz, list(mevcut_isimler.keys()), scorer=fuzz.token_set_ratio)
        if res and res[1] >= 85:
            gercek_isim = mevcut_isimler[res[0]]
            match = df_sozlesme[df_sozlesme['İsim/Ünvan'] == gercek_isim]

    # SONUÇLARI YAZ
    if not match.empty:
        for t_idx in match.index:
            df_sozlesme.at[t_idx, 'VD_Vergi_No'] = v_vkn
            df_sozlesme.at[t_idx, 'VD_Durum'] = v_row['Durum']
            df_sozlesme.at[t_idx, 'VD_Ise_Baslama'] = v_row['İşe Başlama']
            df_sozlesme.at[t_idx, 'VD_Isi_Birakma'] = v_row['İşi Bırakma']
        # print(f"✅ Eşleşti: {v_unvan_orig}")

# --- 4. RAPOR VE KAYIT ---
master_path = Path(__file__).resolve().parent / "BestOffice_Musteri_Master_Liste.xlsx"
final_path = Path(__file__).resolve().parent / "SUPABASE_YUKLEME_LISTESI.xlsx"
eksik_path = Path(__file__).resolve().parent / "SADECE_ESLESMEYENLER.xlsx"

df_sozlesme.to_excel(master_path, index=False)
df_yukleme_hazir = df_sozlesme.dropna(subset=['VD_Vergi_No']).drop_duplicates(subset=['VD_Vergi_No'], keep='last')
df_yukleme_hazir.to_excel(final_path, index=False)
df_eslesmeyenler = df_sozlesme[df_sozlesme['VD_Vergi_No'].isna()]
df_eslesmeyenler.to_excel(eksik_path, index=False)

print(f"\n🔥 FİNAL SONUCU:")
print(f"📍 Supabase'e Hazır Tekil Müşteri: {len(df_yukleme_hazir)}")
print(f"⚠️ Hala Eşleşmeyen Satır Sayısı: {len(df_eslesmeyenler)}")