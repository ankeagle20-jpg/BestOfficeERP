"""
Supabase PostgreSQL Bağlantı Katmanı
Mevcut SQLite database.py fonksiyonları buraya taşınır.
psycopg2 ile doğrudan PostgreSQL bağlantısı kullanır.
"""
import psycopg2
import psycopg2.extras
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
        sslmode="require",          # Supabase SSL zorunlu
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
    with db() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def fetch_one(sql: str, params=()) -> dict | None:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def execute(sql: str, params=()) -> int:
    """INSERT/UPDATE/DELETE — etkilenen satır sayısı döner."""
    with db() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.rowcount


def execute_returning(sql: str, params=()) -> dict | None:
    """INSERT ... RETURNING id — yeni satırı döner."""
    with db() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


# ── Tablo oluşturma ──────────────────────────────────────────────────────────

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

-- Müşteriler (mevcut customers tablosundan)
CREATE TABLE IF NOT EXISTS customers (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
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
    type          TEXT NOT NULL,
    unit_no       TEXT,
    monthly_price NUMERIC(10,2) DEFAULT 0,
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
    barkod          TEXT,
    kargo_firmasi   TEXT,
    durum           TEXT DEFAULT 'beklemede',
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
    stok_miktari    NUMERIC(14,2) DEFAULT 0,
    birim           TEXT DEFAULT 'adet',
    aciklama        TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- TÜFE Verileri
CREATE TABLE IF NOT EXISTS tufe_verileri (
    id          SERIAL PRIMARY KEY,
    year        INTEGER NOT NULL,
    month       TEXT NOT NULL,
    oran        NUMERIC(8,4) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(year, month)
);

-- Müşteri KYC / Giriş (ayrıntılı müşteri kaydı)
CREATE TABLE IF NOT EXISTS musteri_kyc (
    id              SERIAL PRIMARY KEY,
    musteri_id      INTEGER REFERENCES customers(id),
    sirket_unvani   TEXT,
    unvan           TEXT,
    vergi_no        TEXT,
    vergi_dairesi   TEXT,
    mersis_no       TEXT,
    ticaret_sicil_no TEXT,
    kurulus_tarihi  DATE,
    faaliyet_konusu TEXT,
    nace_kodu       TEXT,
    eski_adres      TEXT,
    yeni_adres      TEXT,
    sube_merkez     TEXT DEFAULT 'Merkez',
    yetkili_adsoyad TEXT,
    yetkili_tcno    TEXT,
    yetkili_dogum   DATE,
    yetkili_ikametgah TEXT,
    yetkili_tel     TEXT,
    yetkili_tel2    TEXT,
    yetkili_email   TEXT,
    email           TEXT,
    hizmet_turu     TEXT DEFAULT 'Sanal Ofis',
    aylik_kira      NUMERIC(10,2) DEFAULT 0,
    yillik_kira     NUMERIC(12,2) DEFAULT 0,
    sozlesme_no     TEXT,
    sozlesme_tarihi DATE,
    sozlesme_bitis  DATE,
    evrak_imza_sirkuleri   SMALLINT DEFAULT 0,
    evrak_vergi_levhasi     SMALLINT DEFAULT 0,
    evrak_ticaret_sicil     SMALLINT DEFAULT 0,
    evrak_faaliyet_belgesi  SMALLINT DEFAULT 0,
    evrak_kimlik_fotokopi   SMALLINT DEFAULT 0,
    evrak_ikametgah         SMALLINT DEFAULT 0,
    evrak_kase              SMALLINT DEFAULT 0,
    notlar          TEXT,
    tamamlanma_yuzdesi INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- KYC belgeler (yüklenen dosyalar)
CREATE TABLE IF NOT EXISTS kyc_belgeler (
    id              SERIAL PRIMARY KEY,
    kyc_id          INTEGER NOT NULL REFERENCES musteri_kyc(id) ON DELETE CASCADE,
    belge_tipi      TEXT,
    dosya_adi       TEXT,
    dosya_yolu      TEXT,
    yuklenme_tarihi TIMESTAMPTZ DEFAULT NOW()
);

-- Sözleşmeler
CREATE TABLE IF NOT EXISTS sozlesmeler (
    id              SERIAL PRIMARY KEY,
    musteri_id      INTEGER REFERENCES customers(id),
    sozlesme_no     TEXT UNIQUE,
    musteri_adi     TEXT,
    dosya_yolu      TEXT,
    olusturma_tarihi TIMESTAMPTZ DEFAULT NOW()
);

-- Banka Hesaplar
CREATE TABLE IF NOT EXISTS banka_hesaplar (
    id          SERIAL PRIMARY KEY,
    banka_adi   TEXT NOT NULL,
    hesap_adi   TEXT,
    hesap_no    TEXT,
    iban        TEXT,
    para_birimi TEXT DEFAULT 'TRY',
    bakiye      NUMERIC(14,2) DEFAULT 0,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Banka Hareketleri (ekstre satırları, eşleştirme, masraf takibi)
CREATE TABLE IF NOT EXISTS banka_hareketleri (
    id              SERIAL PRIMARY KEY,
    banka_hesap_id  INTEGER NOT NULL REFERENCES banka_hesaplar(id) ON DELETE CASCADE,
    hareket_tarihi  DATE NOT NULL,
    aciklama        TEXT,
    gonderici       TEXT,
    tutar           NUMERIC(14,2) NOT NULL,
    tip             TEXT DEFAULT 'gelen',
    durum           TEXT DEFAULT 'bekleyen',
    musteri_id      INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    tahsilat_id     INTEGER REFERENCES tahsilatlar(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Personel (devam takip için mesai, mac_adres, notlar)
CREATE TABLE IF NOT EXISTS personel (
    id               SERIAL PRIMARY KEY,
    ad_soyad         TEXT NOT NULL,
    pozisyon         TEXT,
    telefon          TEXT,
    email            TEXT,
    giris_tarihi     DATE,
    mesai_baslangic  TEXT DEFAULT '09:00',
    mac_adres        TEXT,
    notlar           TEXT,
    is_active        BOOLEAN DEFAULT TRUE,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Devam kayıtları (giriş/çıkış, geç kalma)
CREATE TABLE IF NOT EXISTS devam_kayitlari (
    id            SERIAL PRIMARY KEY,
    personel_id   INTEGER NOT NULL REFERENCES personel(id) ON DELETE CASCADE,
    tarih         DATE NOT NULL,
    giris_saati   TEXT,
    cikis_saati   TEXT,
    gec_dakika    INTEGER DEFAULT 0,
    kaynak        TEXT DEFAULT 'manuel',
    UNIQUE(personel_id, tarih)
);

-- Personel ek bilgi (izin hakkı, kıdem)
CREATE TABLE IF NOT EXISTS personel_bilgi (
    personel_id        INTEGER PRIMARY KEY REFERENCES personel(id) ON DELETE CASCADE,
    ise_baslama_tarihi DATE,
    yillik_izin_hakki  INTEGER DEFAULT 14,
    manuel_izin_gun    INTEGER DEFAULT 0,
    unvan              TEXT,
    departman          TEXT,
    tc_no              TEXT,
    gec_kesinti_tipi   TEXT DEFAULT 'izin',
    created_at         TIMESTAMPTZ DEFAULT NOW()
);

-- İzin kullanımı
CREATE TABLE IF NOT EXISTS personel_izin (
    id               SERIAL PRIMARY KEY,
    personel_id      INTEGER NOT NULL REFERENCES personel(id) ON DELETE CASCADE,
    izin_turu        TEXT NOT NULL,
    baslangic_tarihi DATE NOT NULL,
    bitis_tarihi     DATE NOT NULL,
    gun_sayisi       NUMERIC(4,2) DEFAULT 1,
    aciklama         TEXT,
    onay_durumu      TEXT DEFAULT 'onaylandi',
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Yetki alanları (hangi modüllere erişebilir)
CREATE TABLE IF NOT EXISTS personel_yetki (
    id          SERIAL PRIMARY KEY,
    personel_id INTEGER NOT NULL REFERENCES personel(id) ON DELETE CASCADE,
    modul       TEXT NOT NULL,
    yetki       TEXT DEFAULT 'goruntuleme',
    UNIQUE(personel_id, modul)
);
"""


def init_schema():
    """Tüm tabloları Supabase'de oluştur."""
    with db() as conn:
        conn.cursor().execute(SCHEMA_SQL)
    ensure_kyc_columns()
    ensure_customers_tax_number()
    ensure_personel_columns()
    ensure_banka_columns()
    seed_banka_hesaplar()
    seed_urunler()
    print("Supabase schema created.")


def seed_urunler():
    """Varsayılan 3 ürün yoksa ekle (Sanal Ofis, Paylaşımlı Ofis, Hazır Ofis)."""
    n = fetch_one("SELECT COUNT(*) as c FROM urunler")
    if n and (n.get("c") or 0) > 0:
        return
    for ad, kod, fiyat in [
        ("SANAL OFİS", "0001", 0),
        ("HAZIR OFİS", "0002", 24),
        ("PAYLAŞIMLI OFİS", "0003", 0),
    ]:
        execute(
            "INSERT INTO urunler (urun_adi, stok_kodu, birim_fiyat, stok_miktari, birim) VALUES (%s, %s, %s, 0, 'adet')",
            (ad, kod, fiyat),
        )


def ensure_personel_columns():
    """Personel tablosuna devam/izin için sütun ekle."""
    for col, typ in [("mesai_baslangic", "TEXT DEFAULT '09:00'"), ("mac_adres", "TEXT"), ("notlar", "TEXT")]:
        try:
            execute(f"ALTER TABLE personel ADD COLUMN {col} {typ}")
        except Exception:
            pass


def ensure_kyc_columns():
    """Mevcut musteri_kyc tablosuna eksik sütunları ekle (migration)."""
    cols = [
        ("sirket_unvani", "TEXT"),
        ("mersis_no", "TEXT"),
        ("ticaret_sicil_no", "TEXT"),
        ("kurulus_tarihi", "DATE"),
        ("faaliyet_konusu", "TEXT"),
        ("nace_kodu", "TEXT"),
        ("eski_adres", "TEXT"),
        ("yeni_adres", "TEXT"),
        ("sube_merkez", "TEXT DEFAULT 'Merkez'"),
        ("yetkili_tcno", "TEXT"),
        ("yetkili_dogum", "DATE"),
        ("yetkili_ikametgah", "TEXT"),
        ("yetkili_email", "TEXT"),
        ("yillik_kira", "NUMERIC(12,2) DEFAULT 0"),
        ("sozlesme_no", "TEXT"),
        ("evrak_imza_sirkuleri", "SMALLINT DEFAULT 0"),
        ("evrak_vergi_levhasi", "SMALLINT DEFAULT 0"),
        ("evrak_ticaret_sicil", "SMALLINT DEFAULT 0"),
        ("evrak_faaliyet_belgesi", "SMALLINT DEFAULT 0"),
        ("evrak_kimlik_fotokopi", "SMALLINT DEFAULT 0"),
        ("evrak_ikametgah", "SMALLINT DEFAULT 0"),
        ("evrak_kase", "SMALLINT DEFAULT 0"),
        ("notlar", "TEXT"),
        ("tamamlanma_yuzdesi", "INTEGER DEFAULT 0"),
        ("updated_at", "TIMESTAMPTZ DEFAULT NOW()"),
    ]
    for col, typ in cols:
        try:
            execute(f"ALTER TABLE musteri_kyc ADD COLUMN {col} {typ}")
        except Exception:
            pass


def ensure_banka_columns():
    """Banka tablolarına eksik sütun ekle."""
    try:
        execute("ALTER TABLE banka_hesaplar ADD COLUMN IF NOT EXISTS hesap_adi TEXT")
    except Exception:
        try:
            execute("ALTER TABLE banka_hesaplar ADD COLUMN hesap_adi TEXT")
        except Exception:
            pass


def seed_banka_hesaplar():
    """Varsayılan banka hesapları yoksa ekle (Akbank, Halkbank, Türkiye Finans)."""
    n = fetch_one("SELECT COUNT(*) as c FROM banka_hesaplar")
    if n and (n.get("c") or 0) > 0:
        return
    for banka_adi, hesap_adi in [
        ("Akbank", "Akbank Vadesiz"),
        ("Halkbank", "Halkbank Vadesiz"),
        ("Türkiye Finans", "Türkiye Finans Vadesiz"),
    ]:
        execute(
            "INSERT INTO banka_hesaplar (banka_adi, hesap_adi, is_active) VALUES (%s, %s, TRUE)",
            (banka_adi, hesap_adi or banka_adi),
        )


def ensure_customers_tax_number():
    """Customers tablosuna tax_number ekle (yoksa)."""
    try:
        execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS tax_number TEXT")
    except Exception:
        try:
            execute("ALTER TABLE customers ADD COLUMN tax_number TEXT")
        except Exception:
            pass