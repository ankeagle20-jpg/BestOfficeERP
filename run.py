import os
import sys
import traceback
from pathlib import Path

# Ekran ölçekleme ayarları
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

# Proje yolunu ekle
sys.path.insert(0, str(Path(__file__).parent))

from database import initialize_database, initialize_offices

def main():
    print("1: Veritabanı başlatılıyor...")
    initialize_database()
    initialize_offices()

    try:
        from database import init_izin_db, add_personel_not_column, init_fatura_db, init_kargo_db, init_banka_db, init_giris_db
        init_izin_db()
        add_personel_not_column()
        init_fatura_db()
        init_kargo_db()
        init_banka_db()
        init_giris_db()
        print("[DB] Tüm tablolar hazır.")
    except Exception as e:
        print(f"[DB] Tablo hatası: {e}")
        traceback.print_exc()

    print("2: UI yükleniyor...")
    try:
        from ui import BaseWindow
        print("3: ui.py yüklendi.")
        print("Uygulama açılıyor...")
        print("-" * 40)
        
        # Tkinter kullanımı:
        app = BaseWindow()
        
        # Tkinter'da show() yerine pencere otomatik oluşur 
        # veya bazı tasarımlarda deiconify() gerekebilir.
        # Ama asıl önemli olan mainloop() kısmıdır.
        
        app.mainloop() 
        
    except Exception as e:
        print(f"UI Başlatma Hatası: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()