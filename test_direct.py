import psycopg2

CLOUD_URL = "postgresql://postgres:Anka1933ofis@db.akieehczrcdgsyssjief.supabase.co:5432/postgres?sslmode=require"

try:
    conn = psycopg2.connect(CLOUD_URL)
    print("DIRECT BAĞLANTI BAŞARILI")
except Exception as e:
    print("DIRECT HATA:", e)