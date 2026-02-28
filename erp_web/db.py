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
    year        INTEGER NOT NULL,
    month       TEXT NOT NULL,
    oran        NUMERIC(8,4) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(year, month)
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

-- Sözleşmeler (sözleşme numarası takibi)
CREATE TABLE IF NOT EXISTS sozlesmeler (
    id           SERIAL PRIMARY KEY,
    musteri_id   INTEGER REFERENCES customers(id) ON DELETE CASCADE,
    sozlesme_no  TEXT UNIQUE NOT NULL,
    hizmet_turu  TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Ofisbir İlan Robotu: hazır/sanal ofis ilanları
CREATE TABLE IF NOT EXISTS office_rentals (
    id                    SERIAL PRIMARY KEY,
    ofis_turu             TEXT NOT NULL,
    baslik                TEXT,
    il                    TEXT,
    ilce                  TEXT,
    adres                 TEXT,
    aylik_fiyat           NUMERIC(12,2) DEFAULT 0,
    para_birimi            TEXT DEFAULT 'TRY',
    yasal_adres           BOOLEAN DEFAULT FALSE,
    sekreterya_karsilama   BOOLEAN DEFAULT FALSE,
    posta_takibi          BOOLEAN DEFAULT FALSE,
    toplanti_odasi        BOOLEAN DEFAULT FALSE,
    sinirsiz_cay_kahve    BOOLEAN DEFAULT FALSE,
    fiber_internet        BOOLEAN DEFAULT FALSE,
    numara_0850_tahsisi   BOOLEAN DEFAULT FALSE,
    anlik_bildirim_sistemi BOOLEAN DEFAULT FALSE,
    misafir_agirlama      BOOLEAN DEFAULT FALSE,
    mutfak_erisimi        BOOLEAN DEFAULT FALSE,
    temizlik_hizmeti      BOOLEAN DEFAULT FALSE,
    aciklama              TEXT,
    aciklama_ai           TEXT,
    eids_yetki_no         TEXT,
    status                TEXT DEFAULT 'taslak',
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW()
);
"""


def init_schema():
    """Tüm tabloları Supabase'de oluştur."""
    with db() as conn:
        conn.cursor().execute(SCHEMA_SQL)
        ensure_customers_notes()
        ensure_tahsilatlar_columns()
        ensure_kargolar_durum()
        ensure_faturalar_amount_columns()
        ensure_office_rentals()
    print("✅ Supabase şema oluşturuldu.")


def ensure_office_rentals():
    """office_rentals tablosu yoksa oluştur (migration)."""
    try:
        execute("""
            CREATE TABLE IF NOT EXISTS office_rentals (
                id                    SERIAL PRIMARY KEY,
                ofis_turu             TEXT NOT NULL,
                baslik                TEXT,
                il                    TEXT,
                ilce                  TEXT,
                adres                 TEXT,
                aylik_fiyat           NUMERIC(12,2) DEFAULT 0,
                para_birimi           TEXT DEFAULT 'TRY',
                yasal_adres           BOOLEAN DEFAULT FALSE,
                sekreterya_karsilama  BOOLEAN DEFAULT FALSE,
                posta_takibi          BOOLEAN DEFAULT FALSE,
                toplanti_odasi        BOOLEAN DEFAULT FALSE,
                sinirsiz_cay_kahve    BOOLEAN DEFAULT FALSE,
                fiber_internet        BOOLEAN DEFAULT FALSE,
                numara_0850_tahsisi   BOOLEAN DEFAULT FALSE,
                anlik_bildirim_sistemi BOOLEAN DEFAULT FALSE,
                misafir_agirlama      BOOLEAN DEFAULT FALSE,
                mutfak_erisimi        BOOLEAN DEFAULT FALSE,
                temizlik_hizmeti      BOOLEAN DEFAULT FALSE,
                aciklama              TEXT,
                aciklama_ai           TEXT,
                eids_yetki_no         TEXT,
                status                TEXT DEFAULT 'taslak',
                created_at            TIMESTAMPTZ DEFAULT NOW(),
                updated_at            TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        _ensure_office_rentals_extra_columns()
    except Exception as e:
        print(f"office_rentals: {e}")


def _ensure_office_rentals_extra_columns():
    """office_rentals'a yeni hizmet sütunlarını ekle (migration)."""
    for col in (
        "sinirsiz_cay_kahve", "fiber_internet", "numara_0850_tahsisi",
        "anlik_bildirim_sistemi", "misafir_agirlama", "mutfak_erisimi", "temizlik_hizmeti"
    ):
        try:
            execute(f"ALTER TABLE office_rentals ADD COLUMN IF NOT EXISTS {col} BOOLEAN DEFAULT FALSE")
        except Exception as e:
            print(f"office_rentals.{col}: {e}")


def ensure_customers_notes():
    """Customers tablosuna notes sütunu ekle."""
    try:
        execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS notes TEXT")
    except Exception as e:
        print(f"Notes sütunu zaten var veya hata: {e}")


def ensure_tahsilatlar_columns():
    """tahsilatlar tablosunda musteri_id / fatura_id yoksa ekle (eski şemalar için)."""
    try:
        execute("ALTER TABLE tahsilatlar ADD COLUMN IF NOT EXISTS musteri_id INTEGER REFERENCES customers(id)")
    except Exception as e:
        print(f"tahsilatlar.musteri_id: {e}")
    try:
        execute("ALTER TABLE tahsilatlar ADD COLUMN IF NOT EXISTS fatura_id INTEGER REFERENCES faturalar(id)")
    except Exception as e:
        print(f"tahsilatlar.fatura_id: {e}")
    try:
        execute("ALTER TABLE tahsilatlar ADD COLUMN IF NOT EXISTS makbuz_no TEXT")
    except Exception as e:
        print(f"tahsilatlar.makbuz_no: {e}")
    try:
        execute("ALTER TABLE tahsilatlar ADD COLUMN IF NOT EXISTS cek_detay TEXT")
    except Exception as e:
        print(f"tahsilatlar.cek_detay: {e}")
    try:
        execute("ALTER TABLE tahsilatlar ADD COLUMN IF NOT EXISTS havale_banka TEXT")
    except Exception as e:
        print(f"tahsilatlar.havale_banka: {e}")
    try:
        execute("ALTER TABLE tahsilatlar ADD COLUMN IF NOT EXISTS customer_id INTEGER REFERENCES customers(id)")
    except Exception as e:
        print(f"tahsilatlar.customer_id: {e}")


def ensure_kargolar_durum():
    """kargolar tablosuna durum sütunu ekle (beklemede / teslim_alindi)."""
    try:
        execute("ALTER TABLE kargolar ADD COLUMN IF NOT EXISTS durum TEXT DEFAULT 'beklemede'")
    except Exception as e:
        print(f"kargolar.durum: {e}")


def ensure_faturalar_amount_columns():
    """faturalar tablosunda tutar/toplam yoksa ekle (farklı şemalarda sadece biri olabilir)."""
    try:
        execute("ALTER TABLE faturalar ADD COLUMN IF NOT EXISTS tutar NUMERIC(12,2) DEFAULT 0")
    except Exception as e:
        print(f"faturalar.tutar: {e}")
    try:
        execute("ALTER TABLE faturalar ADD COLUMN IF NOT EXISTS toplam NUMERIC(12,2) DEFAULT 0")
    except Exception as e:
        print(f"faturalar.toplam: {e}")
