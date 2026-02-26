"""
Bu scripti database.py ile AYNI KLASÖRE koy ve çalıştır.
Tüm müşteri verilerini siler ve DB'yi sıfırlar.
"""
from database import reset_database

print("DB sıfırlanıyor...")
reset_database()
print("Tamam! Şimdi uygulamayı açıp Excel'den tekrar aktar.")
