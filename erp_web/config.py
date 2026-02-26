"""
Uygulama Konfigürasyonu
.env dosyasından okunur — asla Git'e gönderme!
"""
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask
    SECRET_KEY = os.environ.get("SECRET_KEY", "degistir-bunu-uretimde")
    SESSION_TYPE = "filesystem"
    
    # Supabase
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")  # service_role key
    
    # PostgreSQL (Supabase DB)
    DB_HOST = os.environ.get("DB_HOST", "")
    DB_PORT = int(os.environ.get("DB_PORT", 5432))
    DB_NAME = os.environ.get("DB_NAME", "postgres")
    DB_USER = os.environ.get("DB_USER", "postgres")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    
    # SUPABASE_DB_URL - PostgreSQL connection string
    SUPABASE_DB_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?sslmode=require"
    
    # Uygulama
    APP_NAME = "OFİSBİR ERP"
    VERSION = "2.0.0"
    DEBUG = os.environ.get("DEBUG", "false").lower() == "true"