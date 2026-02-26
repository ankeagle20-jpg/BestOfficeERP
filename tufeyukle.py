import pandas as pd
import sqlite3
import os

def tufe_verilerini_ice_aktar(excel_dosya_yolu):
    # 1. Dosya kontrolü
    if not os.path.exists(excel_dosya_yolu):
        print(f"Hata: '{excel_dosya_yolu}' dosyası bulunamadı! Excel dosyasını proje klasörüne koymayı unutma.")
        return

    try:
        # 2. Excel'i Oku (Görseldeki YIL, AY, TUFE ORANI (%) sütunlarını okur)
        print(f"'{excel_dosya_yolu}' dosyası okunuyor, lütfen bekle...")
        df = pd.read_excel(excel_dosya_yolu)
        
        # 3. Veritabanına Bağlan (Sol taraftaki erp.db dosyası)
        conn = sqlite3.connect('erp.db')
        cursor = conn.cursor()

        # 4. Tabloyu Oluştur (Eğer veritabanında henüz yoksa)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tufe_oranlari (
                yil INTEGER,
                ay TEXT,
                oran REAL,
                PRIMARY KEY (yil, ay)
            )
        ''')

        # 5. Verileri Döngüyle Kaydet
        sayac = 0
        for _, satir in df.iterrows():
            # INSERT OR REPLACE: Eğer aynı yıl ve ay varsa eskisini günceller
            cursor.execute('''
                INSERT OR REPLACE INTO tufe_oranlari (yil, ay, oran)
                VALUES (?, ?, ?)
            ''', (satir['YIL'], satir['AY'], satir['TUFE ORANI (%)']))
            sayac += 1

        conn.commit()
        conn.close()
        print(f"Bitti kanka! Toplam {sayac} adet TÜFE oranı 'erp.db' içine başarıyla işlendi.")
        
    except Exception as e:
        print(f"Bir aksilik çıktı kanka: {e}")

# --- KULLANIM ---
# Excel dosyanın tam adını (uzantısıyla beraber) buraya yaz:
tufe_verilerini_ice_aktar('tufe_verileri.xlsx.xlsx')