import sqlite3, os

for db in ['erp.db', 'bestoffice.db', 'bestoffice_erp.db']:
    if os.path.exists(db):
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        print(f"\n--- {db} Dosyası İnceleniyor ---")
        try:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tablolar = [t[0] for t in cur.fetchall()]
            
            if 'users' in tablolar:
                cur.execute("SELECT username FROM users")
                user_list = cur.fetchall()
                print(f"BULDUM! Kullanıcılar: {user_list}")
            else:
                print(f"Bu dosyada 'users' tablosu yok. Mevcut tablolar: {tablolar}")
        except Exception as e:
            print(f"Hata: {e}")
        conn.close()
    else:
        print(f"{db}: bulunamadi")