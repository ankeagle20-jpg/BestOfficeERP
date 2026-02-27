"""
Supabase PostgreSQL Bağlantı Katmanı
"""
import psycopg2
import psycopg2.extras
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from config import Config


def get_conn():
    """Supabase PostgreSQL bağlantısı döndür."""
    return psycopg2.connect(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        dbname=Config.DB_NAME,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        sslmode="require",
        cursor_factory=psycopg2.extras.RealDictCursor
    )


@contextmanager
def db():
    """Context manager: otomatik commit/rollback."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(sql: str, params=()) -> list:
    """Tüm satırları getir."""
    with db() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def fetch_one(sql: str, params=()) -> dict | None:
    """Tek satır getir."""
    with db() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def execute(sql: str, params=()) -> int:
    """INSERT/UPDATE/DELETE - etkilenen satır sayısı döner."""
    with db() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.rowcount


def execute_returning(sql: str, params=()) -> dict | None:
    """INSERT ... RETURNING - yeni satırı döner."""
    with db() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


# ── Şema oluşturma ───────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- Kullanıcılar
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name     TEXT,
    role          TEXT NOT NULL DEFAULT 'personel',
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    last_login    TIMESTAMPTZ
);

-- Müşteriler
CREATE TABLE IF NOT EXISTS customers (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    tax_number  TEXT,
    email       TEXT,
    phone       TEXT,
    address     TEXT,
    office_code TEXT,
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Ofisler
CREATE TABLE IF NOT EXISTS offices (
    id            SERIAL PRIMARY KEY,
    code          TEXT UNIQUE NOT NULL,
    office_type   TEXT NOT NULL,
    office_number TEXT,
    monthly_rent  NUMERIC(10,2) DEFAULT 0,
    status        TEXT DEFAULT 'bos',
    is_active     BOOLEAN DEFAULT TRUE,
    customer_id   INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    notes         TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Faturalar
CREATE TABLE IF NOT EXISTS faturalar (
    id              SERIAL PRIMARY KEY,
    fatura_no       TEXT UNIQUE NOT NULL,
    musteri_id      INTEGER REFERENCES customers(id),
    musteri_adi     TEXT,
    tutar           NUMERIC(12,2) DEFAULT 0,
    kdv_tutar       NUMERIC(12,2) DEFAULT 0,
    toplam          NUMERIC(12,2) DEFAULT 0,
    durum           TEXT DEFAULT 'odenmedi',
    fatura_tarihi   DATE DEFAULT CURRENT_DATE,
    vade_tarihi     DATE,
    notlar          TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Tahsilatlar
CREATE TABLE IF NOT EXISTS tahsilatlar (
    id              SERIAL PRIMARY KEY,
    musteri_id      INTEGER REFERENCES customers(id),
    fatura_id       INTEGER REFERENCES faturalar(id),
    tutar           NUMERIC(12,2) NOT NULL,
    odeme_turu      TEXT DEFAULT 'nakit',
    tahsilat_tarihi DATE DEFAULT CURRENT_DATE,
    aciklama        TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Kargolar
CREATE TABLE IF NOT EXISTS kargolar (
    id              SERIAL PRIMARY KEY,
    musteri_id      INTEGER REFERENCES customers(id),
    tarih           DATE DEFAULT CURRENT_DATE,
    teslim_alan     TEXT,
    kargo_firmasi   TEXT,
    takip_no        TEXT,
    odeme_tutari    NUMERIC(10,2) DEFAULT 0,
    odeme_durumu    TEXT DEFAULT 'odenmedi',
    notlar          TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Ürünler
CREATE TABLE IF NOT EXISTS urunler (
    id              SERIAL PRIMARY KEY,
    urun_adi        TEXT NOT NULL,
    stok_kodu       TEXT UNIQUE NOT NULL,
    birim_fiyat     NUMERIC(12,2) DEFAULT 0,
    stok            INTEGER DEFAULT 0,
    birim           TEXT DEFAULT 'adet',
    aciklama        TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- TÜFE
CREATE TABLE IF NOT EXISTS tufe_verileri (
    id          SERIAL PRIMARY KEY,
    yil         INTEGER NOT NULL,
    ay          TEXT NOT NULL,
    oran        NUMERIC(8,4) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(yil, ay)
);

-- Banka Hesaplar
CREATE TABLE IF NOT EXISTS banka_hesaplar (
    id          SERIAL PRIMARY KEY,
    banka_adi   TEXT NOT NULL,
    hesap_adi   TEXT,
    hesap_no    TEXT,
    iban        TEXT,
    sube        TEXT,
    bakiye      NUMERIC(14,2) DEFAULT 0,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Personel
CREATE TABLE IF NOT EXISTS personel (
    id               SERIAL PRIMARY KEY,
    ad_soyad         TEXT NOT NULL,
    pozisyon         TEXT,
    departman        TEXT,
    telefon          TEXT,
    email            TEXT,
    maas             NUMERIC(12,2) DEFAULT 0,
    is_active        BOOLEAN DEFAULT TRUE,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
"""


def init_schema():
    """Tüm tabloları Supabase'de oluştur."""
    with db() as conn:
        conn.cursor().execute(SCHEMA_SQL)
    print("✅ Supabase şema oluşturuldu.")