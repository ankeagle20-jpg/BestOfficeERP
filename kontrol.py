import sqlite3
conn = sqlite3.connect('bestoffice_erp.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
for r in cur.fetchall():
    n = conn.execute(f"SELECT COUNT(*) FROM [{r[0]}]").fetchone()[0]
    print(f"{r[0]}: {n} satir")
conn.close()