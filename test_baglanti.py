import psycopg2

url = "postgresql://postgres.akieehczrcdgsyssjief:Ankara2026anka@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"

try:
    conn = psycopg2.connect(url, connect_timeout=15)
    print("BAGLANTI BASARILI!")
    conn.close()
except Exception as e:
    print(f"HATA: {e}")