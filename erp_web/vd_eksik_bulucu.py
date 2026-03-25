import pandas as pd
from fuzzywuzzy import fuzz
from fuzzywuzzy import process
import os
import re
from pathlib import Path

# --- 1. FONKSİYONLAR ---
def dosya_yolu_bul(dosya_adi):
    yollari = [
        Path(__file__).resolve().parent / dosya_adi,
        Path(__file__).resolve().parent.parent / dosya_adi,
        Path(os.path.expanduser("~/Desktop")) / dosya_adi
    ]
    for yol in yollari:
        if yol.exists(): return yol
    return None

def isim_normalize_et(metin):
    if pd.isna(metin): return ""
    metin = str(metin).upper()
    metin = metin.replace("İ", "I").replace("Ğ", "G").replace("Ü", "U").replace("Ş", "S").replace("Ö", "O").replace("Ç", "C")
    parazitler = ["YENI KURULACAK", "TASF HAL", "TASFIYE HALINDE", "LTD", "STI", "A S", "AS", "LIMITED", "SIRKETI"]
    for p in parazitler:
        metin = metin.replace(p, "")
    metin = re.sub(r'[^\w\s]', '', metin)
    return " ".join(metin.split())

def ilk_iki_kelime_al(metin):
    temiz = isim_normalize_et(metin)
    kelimeler = temiz.split()
    return " ".join(kelimeler[:2]) if len(kelimeler) >= 2 else "".join(kelimeler)

# --- 2. DOSYALARI AKILLI YÜKLE ---
eslesmeyen_path = dosya_yolu_bul("SADECE_ESLESMEYENLER.xlsx")
vergi_path = dosya_yolu_bul("Vergi_Dairesi_Listesi.xlsx")

if not eslesmeyen_path or not vergi_path:
    print("❌ HATA: Dosyalar bulunamadı! Excel isimlerini veya yerlerini kontrol et kanka.")
    exit()

print(f"📂 Esleşmeyenler: {eslesmeyen_path}")
print(f"📂 Vergi Listesi: {vergi_path}")

df_eslesmeyen = pd.read_excel(eslesmeyen_path)
df_vergi = pd.read_excel(vergi_path)

# Vergi listesini hazırla
vergi_isimleri = {isim_normalize_et(isim): isim for isim in df_vergi['Unvan / Şirket Adı'].dropna().unique()}

# --- 3. DEDEKTİF ANALİZİ ---
print("\n🔍 --- EŞLEŞMEYENLER ANALİZ RAPORU (İLK 30) --- 🔍\n")

for idx, row in df_eslesmeyen.head(30).iterrows():
    bizdeki_isim = str(row['İsim/Ünvan'])
    bizdeki_temiz = isim_normalize_et(bizdeki_isim)
    bizdeki_ilk_iki = ilk_iki_kelime_al(bizdeki_isim)
    
    extracted = process.extractOne(bizdeki_temiz, list(vergi_isimleri.keys()), scorer=fuzz.token_set_ratio)
    
    if extracted:
        en_yakin_isim_temiz = extracted[0]
        skor = extracted[1]
        gercek_v_unvan = vergi_isimleri[en_yakin_isim_temiz]
        v_ilk_iki = ilk_iki_kelime_al(gercek_v_unvan)

        print(f"❌ BİZDEKİ: {bizdeki_isim}")
        print(f"   👉 En Yakın Aday: {gercek_v_unvan} (Skor: %{skor})")
        print(f"   💡 Karşılaştırma: Bizdeki ilk 2: [{bizdeki_ilk_iki}] | Vergi D. ilk 2: [{v_ilk_iki}]")
        print("-" * 50)

print("\n✅ Analiz bitti kanka. Sonuçları bekliyorum.")