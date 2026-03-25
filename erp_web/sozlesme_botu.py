import os
from pathlib import Path
import pandas as pd
import openpyxl

# --- YAPILANDIRMA ---
# Dosyaların olduğu yer: Masaüstü/Sözleşmeler
SOZLESME_DIR = Path(os.path.expanduser("~/Desktop/Sözleşmeler"))
# Çıktı dosyasının adı ve yeri (ERP ana klasöründe)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_XLSX = PROJECT_ROOT / "Tum_Sozlesme_Verileri.xlsx"

# Görsellerden (3e913c ve 58f284) teyit edilen hedef satırlar
TARGET_MAP = {
    15: "isim_unvan",
    16: "yetkili",
    17: "tc_kimlik_no",
    18: "vergi_no",
    19: "adres",
    20: "telefon_1",
    21: "telefon_2",
    22: "e_posta",
    32: "aylik_taksit_tutar",
    65: "sozlesme_tarihi", # 51 yerine 65. satırdaki 'Düzenleme Tarihi'
}

# Excel tablosundaki başlıkların Türkçe karşılıkları
COLUMN_NAMES = {
    "file_name": "Excel Dosya İsmi",
    "isim_unvan": "İsim/Ünvan",
    "yetkili": "Yetkili",
    "tc_kimlik_no": "T.C. Kim. No",
    "vergi_no": "Vergi No",
    "adres": "Adres",
    "telefon_1": "Telefon 1",
    "telefon_2": "Telefon 2",
    "e_posta": "E-Mail",
    "aylik_taksit_tutar": "Aylık Taksit",
    "sozlesme_tarihi": "Sözleşme Tarihi"
}

def clean_data(value):
    """Hücredeki veriyi temizler, başlıkları eler."""
    if value is None: return ""
    v = str(value).strip()
    # Eğer hücrede sadece etiket yazıyorsa (Örn: 'Yetkili:') onu veri olarak alma
    skips = ["İsim/ Ünvan", "Yetkili", "T.C. Kim. No", "Vergi No", "Adres", 
             "Telefon 1", "Telefon 2", "E-Mail", "Aylık taksit tutar", 
             "Başlangıç tarihi", "15)Düzenleme tarihi"]
    if v in skips or v.endswith(":") or not v:
        return ""
    return v

def read_contract(file_path):
    """Tek bir sözleşme dosyasını akıllıca okur."""
    data = {"file_name": file_path.name}
    try:
        # data_only=True formülleri değil direkt sonuçları (tutar gibi) alır
        wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
        ws = wb.active
        
        for row_num, key in TARGET_MAP.items():
            found_val = ""
            # B(2)'den J(10)'a kadar tara; birleşik hücrelerde veri herhangi birinde olabilir
            for col_idx in range(2, 11):
                cell_val = ws.cell(row=row_num, column=col_idx).value
                cleaned = clean_data(cell_val)
                if cleaned:
                    found_val = cleaned
                    break
            data[key] = found_val
        wb.close()
        return data
    except Exception as e:
        print(f"⚠️ Hata ({file_path.name}): {e}")
        return None

def main():
    if not SOZLESME_DIR.exists():
        print(f"❌ Klasör bulunamadı: {SOZLESME_DIR}")
        return

    # Tüm alt klasörlerdeki gerçek Excel dosyalarını listele
    all_files = [f for f in SOZLESME_DIR.rglob("*.xlsx") if not f.name.startswith("~$")]
    print(f"📂 {len(all_files)} adet sözleşme dosyası işleniyor...")

    results = []
    for f in all_files:
        processed_data = read_contract(f)
        if processed_data:
            results.append(processed_data)
            print(f"✅ Okundu: {f.name}")

    if results:
        # Veriyi tabloya dök ve Türkçeleştir
        df = pd.DataFrame(results)
        df.rename(columns=COLUMN_NAMES, inplace=True)
        
        # Excel'e kaydet (Dosya açıksa hata verir, kapatmayı unutma kanka!)
        try:
            df.to_excel(OUTPUT_XLSX, index=False)
            print(f"\n🚀 İŞLEM TAMAMLANDI!")
            print(f"📍 Dosya burada: {OUTPUT_XLSX}")
        except PermissionError:
            print(f"\n❌ HATA: '{OUTPUT_XLSX.name}' dosyası açık! Kapatıp tekrar dene.")
    else:
        print("\n🤔 Hiç veri bulunamadı. Dosyaları kontrol et kanka.")

if __name__ == "__main__":
    main()