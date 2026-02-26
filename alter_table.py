import sqlite3

conn = sqlite3.connect("erp.db")
cur = conn.cursor()

# customers tablosuna rent_start_year sütunu ekle
cur.execute("ALTER TABLE customers ADD COLUMN rent_start_year INTEGER;")

conn.commit()
conn.close()

print("rent_start_year sütunu başarıyla eklendi!")
