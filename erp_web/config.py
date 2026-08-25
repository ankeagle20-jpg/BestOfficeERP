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

    # Platform credentials vault (Fernet) — tek ana anahtar; diğer sırlar DB'de
    CREDENTIALS_MASTER_KEY = os.environ.get("CREDENTIALS_MASTER_KEY", "").strip()
    
    # Supabase
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")  # service_role key
    
    # PostgreSQL (Supabase DB)
    # Öneri: Paneldeki "Connection string" URI'yi .env'e DATABASE_URL=... olarak yapıştırın
    # (db.get_conn önce DATABASE_URL / SUPABASE_DB_URL okur; SSL/pooler uyumu daha iyi olur.)
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

    # Gemini AI (opsiyonel; .env içinde GEMINI_API_KEY=...)
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

    # Browserless.io — uzak Chrome (İlan Robotu Selenium Remote)
    BROWSERLESS_API_KEY = os.environ.get("BROWSERLESS_API_KEY", "").strip()
    # Selenium WebDriver HTTPS endpoint (shared fleet'te genelde Not Implemented / Auth)
    BROWSERLESS_URL = os.environ.get(
        "BROWSERLESS_URL",
        "https://chrome.browserless.io/webdriver",
    ).strip()
    # Playwright/Puppeteer CDP WebSocket host (token bağlanırken ?token= eklenir)
    BROWSERLESS_WS_HOST = os.environ.get(
        "BROWSERLESS_WS_HOST",
        "wss://production-sfo.browserless.io",
    ).strip()

    # E-posta (randevu onay/iptal/hatırlatma)
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", MAIL_USERNAME or "noreply@example.com")
    APP_URL = os.environ.get("APP_URL", "http://127.0.0.1:5000")  # Onay maillerindeki linkler için
    PASSWORD_RESET_TTL_SEC = int(os.environ.get("PASSWORD_RESET_TTL_SEC", "3600"))
    RANDEVU_WEBHOOK_URL = os.environ.get("RANDEVU_WEBHOOK_URL", "").strip()  # Randevu oluştur/iptal webhook
    RANDEVU_BOOKING_TITLE = os.environ.get("RANDEVU_BOOKING_TITLE", "Randevu Al")  # Beyaz etiket: sayfa başlığı
