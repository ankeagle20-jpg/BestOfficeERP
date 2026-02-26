"""
bestoffice_erp.db → Supabase PostgreSQL Tam Migration
------------------------------------------------------
Kullanım:
  1. Bu dosyayı bestoffice_erp.db ile aynı klasöre koy
  2. CLOUD_URL içindeki şifreyi gir
  3. python migrate_to_supabase.py
"""

import sqlite3
import psycopg2
from psycopg2 import extras
import sys
from datetime import datetime

# ─── AYARLAR ────────────────────────────────────────────────────────────────
LOCAL_DB = "bestoffice_erp.db"

# Supabase bağlantı URL - şifreni buraya yaz
# Bu şekilde tek parça halinde yazmayı dene kanka
# Kanka bu format daha direkt bir bağlantı sağlar
# Şifrenin olduğu yeri dikkatli yaz kanka, @ işareti sonda kalmalı

SCHEMA = """
-- Müşteriler (ana tablo - mevcut sisteminizden)
CREATE TABLE IF NOT EXISTS musteriler (
    id               SERIAL PRIMARY KEY,
    ad_unvan         TEXT,
    vergi_no         TEXT,
    baslangic_tarihi TEXT,
    ilk_kira         REAL,
    gercek_kira      REAL,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Customers (İngilizce alias - bazı modüller bunu kullanır)
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
    id             SERIAL PRIMARY KEY,
    code           TEXT UNIQUE,
    type           TEXT,
    unit_no        TEXT,
    monthly_price  REAL DEFAULT 0,
    status         TEXT DEFAULT 'bos',
    is_active      INTEGER DEFAULT 1,
    customer_id    INTEGER,
    notes          TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- Faturalar
CREATE TABLE IF NOT EXISTS faturalar (
    id             SERIAL PRIMARY KEY,
    fatura_no      TEXT UNIQUE,
    musteri_id     INTEGER,
    musteri_adi    TEXT,
    tutar          REAL DEFAULT 0,
    kdv_tutar      REAL DEFAULT 0,
    toplam         REAL DEFAULT 0,
    durum          TEXT DEFAULT 'odenmedi',
    fatura_tarihi  TEXT,
    vade_tarihi    TEXT,
    notlar         TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- Tahsilatlar
CREATE TABLE IF NOT EXISTS tahsilatlar (
    id               SERIAL PRIMARY KEY,
    musteri_id       INTEGER,
    fatura_id        INTEGER,
    tutar            REAL NOT NULL DEFAULT 0,
    odeme_turu       TEXT DEFAULT 'nakit',
    tahsilat_tarihi  TEXT,
    aciklama         TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Kargolar
CREATE TABLE IF NOT EXISTS kargolar (
    id             SERIAL PRIMARY KEY,
    musteri_id     INTEGER,
    barkod         TEXT,
    kargo_firmasi  TEXT,
    durum          TEXT DEFAULT 'beklemede',
    odeme_tutari   REAL DEFAULT 0,
    odeme_durumu   TEXT DEFAULT 'odenmedi',
    fatura_id      INTEGER,
    notlar         TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- Kargo resimleri
CREATE TABLE IF NOT EXISTS kargo_resimleri (
    id         SERIAL PRIMARY KEY,
    kargo_id   INTEGER,
    dosya_yolu TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- TÜFE Verileri
CREATE TABLE IF NOT EXISTS tufe_verileri (
    id         SERIAL PRIMARY KEY,
    year       INTEGER NOT NULL,
    month      TEXT NOT NULL,
    oran       REAL NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(year, month)
);

-- Personel
CREATE TABLE IF NOT EXISTS personel (
    id            SERIAL PRIMARY KEY,
    ad_soyad      TEXT NOT NULL,
    pozisyon      TEXT,
    telefon       TEXT,
    email         TEXT,
    giris_tarihi  TEXT,
    aktif         INTEGER DEFAULT 1,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Personel izin
CREATE TABLE IF NOT EXISTS personel_izin (
    id           SERIAL PRIMARY KEY,
    personel_id  INTEGER,
    izin_turu    TEXT,
    baslangic    TEXT,
    bitis        TEXT,
    aciklama     TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Banka hesaplar
CREATE TABLE IF NOT EXISTS banka_hesaplar (
    id          SERIAL PRIMARY KEY,
    banka_adi   TEXT NOT NULL,
    hesap_no    TEXT,
    iban        TEXT,
    para_birimi TEXT DEFAULT 'TRY',
    bakiye      REAL DEFAULT 0,
    aktif       INTEGER DEFAULT 1,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Banka hareketler
CREATE TABLE IF NOT EXISTS banka_hareketler (
    id          SERIAL PRIMARY KEY,
    hesap_id    INTEGER,
    tarih       TEXT,
    tutar       REAL,
    turu        TEXT,
    aciklama    TEXT,
    karsi_taraf TEXT,
    eslestirme  TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Müşteri KYC / Giriş formu
CREATE TABLE IF NOT EXISTS musteri_kyc (
    id               SERIAL PRIMARY KEY,
    musteri_id       INTEGER,
    unvan            TEXT,
    vergi_no         TEXT,
    vergi_dairesi    TEXT,
    yetkili_adsoyad  TEXT,
    yetkili_tel      TEXT,
    yetkili_tel2     TEXT,
    email            TEXT,
    hizmet_turu      TEXT,
    aylik_kira       REAL,
    sozlesme_tarihi  TEXT,
    sozlesme_bitis   TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Sözleşmeler
CREATE TABLE IF NOT EXISTS sozlesmeler (
    id                SERIAL PRIMARY KEY,
    musteri_id        INTEGER,
    sozlesme_no       TEXT UNIQUE,
    musteri_adi       TEXT,
    dosya_yolu        TEXT,
    olusturma_tarihi  TIMESTAMPTZ DEFAULT NOW()
);

-- Giriş alanları (dinamik form tanımları)
CREATE TABLE IF NOT EXISTS giris_alanlar (
    id          SERIAL PRIMARY KEY,
    alan_adi    TEXT,
    alan_tipi   TEXT,
    zorunlu     INTEGER DEFAULT 0,
    sira        INTEGER DEFAULT 0,
    aktif       INTEGER DEFAULT 1
);

-- Kullanıcılar (web girişi için)
CREATE TABLE IF NOT EXISTS users (
    id             SERIAL PRIMARY KEY,
    username       TEXT UNIQUE NOT NULL,
    email          TEXT,
    password_hash  TEXT NOT NULL,
    rol            TEXT NOT NULL DEFAULT 'kullanici',
    aktif          BOOLEAN DEFAULT TRUE,
    olusturma      TIMESTAMPTZ DEFAULT NOW(),
    son_giris      TIMESTAMPTZ
);
"""


# ─── TAŞINACAK TABLOLAR ──────────────────────────────────────────────────────
# (sqlite_tablo, postgres_tablo, [sütunlar])
# Sütun listesi None ise tüm sütunlar otomatik alınır
MIGRATE_TABLES = [
    ("musteriler",      "musteriler",     None),
    ("customers",       "customers",      None),
    ("offices",         "offices",        None),
    ("faturalar",       "faturalar",      None),
    ("tahsilatlar",     "tahsilatlar",    None),
    ("kargolar",        "kargolar",       None),
    ("kargo_resimleri", "kargo_resimleri",None),
    ("tufe_verileri",   "tufe_verileri",  None),
    ("personel",        "personel",       None),
    ("personel_izin",   "personel_izin",  None),
    ("banka_hesaplar",  "banka_hesaplar", None),
    ("banka_hareketler","banka_hareketler",None),
    ("musteri_kyc",     "musteri_kyc",    None),
    ("sozlesmeler",     "sozlesmeler",    None),
    ("giris_alanlar",   "giris_alanlar",  None),
]


def get_sqlite_tables(conn):
    """SQLite'daki tüm tabloları döndür."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [r[0] for r in cur.fetchall()]


def get_sqlite_columns(conn, table):
    """SQLite tablosunun sütun adlarını döndür (id hariç)."""
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = cur.fetchall()
    # id sütununu atla (PostgreSQL SERIAL ile otomatik artar)
    return [c[1] for c in cols if c[1].lower() != "id"]


def migrate_table(local_conn, cloud_cur, sqlite_tablo, pg_tablo):
    """Tek bir tabloyu taşır. Başarıyla taşınan satır sayısını döndürür."""
    local_cur = local_conn.cursor()

    # SQLite'da bu tablo var mı?
    local_cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (sqlite_tablo,))
    if not local_cur.fetchone():
        print(f"   ⏭  {sqlite_tablo} → SQLite'da yok, atlandı.")
        return 0

    # Sütunları al
    cols = get_sqlite_columns(local_conn, sqlite_tablo)
    if not cols:
        print(f"   ⚠  {sqlite_tablo} → Sütun bulunamadı.")
        return 0

    # Verileri çek
    local_cur.execute(f"SELECT {', '.join(cols)} FROM {sqlite_tablo}")
    rows = local_cur.fetchall()

    if not rows:
        print(f"   ○  {pg_tablo} → Boş tablo (0 satır).")
        return 0

    # Önce hedef tabloyu temizle (yeniden çalıştırılabilirlik için)
    cloud_cur.execute(f"TRUNCATE TABLE {pg_tablo} RESTART IDENTITY CASCADE")

    # Toplu insert
    col_str = ", ".join(cols)
    extras.execute_values(
        cloud_cur,
        f"INSERT INTO {pg_tablo} ({col_str}) VALUES %s",
        rows,
        page_size=500
    )

    print(f"   ✓  {pg_tablo:<22} → {len(rows):>5} satır taşındı.")
    return len(rows)


def main():
    print()
    print("=" * 55)
    print("  BestOffice ERP → Supabase PostgreSQL Migration")
    print(f"  {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print("=" * 55)

    # Bağlantılar
    print("\n📂 SQLite açılıyor:", LOCAL_DB)
    try:
        local_conn = sqlite3.connect(LOCAL_DB)
    except Exception as e:
        print(f"❌ SQLite bağlantı hatası: {e}"); sys.exit(1)

    sqlite_tablolar = get_sqlite_tables(local_conn)
    print(f"   → {len(sqlite_tablolar)} tablo bulundu: {', '.join(sqlite_tablolar)}")

    print("\n🌐 Supabase'e bağlanılıyor...")
    try:
        CLOUD_URL = "postgresql://postgres.akieehczrcdgsyssjief:Anka1970anka@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"
        cloud_conn = psycopg2.connect(CLOUD_URL)
        cloud_cur = cloud_conn.cursor()
        cloud_cur.execute("SELECT version()")
        ver = cloud_cur.fetchone()[0][:40]
        print(f"   ✓ Bağlantı OK: {ver}...")

    except Exception as e:
        print(f"❌ Supabase bağlantı hatası: {e}")
        print("   → URL'yi ve şifreyi kontrol et!")
        sys.exit(1)

    # Şema oluştur
    print("\n🏗️  Tablolar oluşturuluyor...")
    try:
        cloud_cur.execute(SCHEMA)
        cloud_conn.commit()
        print("   ✓ Şema hazır.")
    except Exception as e:
        print(f"❌ Şema hatası: {e}"); sys.exit(1)

    # Tabloları taşı
    print("\n📦 Veri taşıma başlıyor...\n")
    toplam = 0
    hatalar = []

    for sqlite_tbl, pg_tbl, _ in MIGRATE_TABLES:
        try:
            n = migrate_table(local_conn, cloud_cur, sqlite_tbl, pg_tbl)
            toplam += n
        except Exception as e:
            print(f"   ❌ {pg_tbl}: {e}")
            hatalar.append((pg_tbl, str(e)))
            cloud_conn.rollback()

    # Commit
    try:
        cloud_conn.commit()
    except Exception as e:
        print(f"\n❌ Commit hatası: {e}")
        sys.exit(1)

    # TÜFE verisini AYLIK_YILLIK'den de ekle (kodda gömülü)
    print("\n📊 Gömülü TÜFE verileri ekleniyor...")
    try:
        _tufe_ekle(cloud_cur, cloud_conn)
    except Exception as e:
        print(f"   ⚠ TÜFE ekleme atlandı: {e}")

    # Özet
    print()
    print("=" * 55)
    if hatalar:
        print(f"⚠️  Tamamlandı — {toplam} satır taşındı, {len(hatalar)} hata:")
        for tbl, err in hatalar:
            print(f"   • {tbl}: {err}")
    else:
        print(f"✅ BAŞARILI — Toplam {toplam} satır Supabase'e taşındı!")
    print("=" * 55)
    print()
    print("Sonraki adım:")
    print("  Web uygulamasını başlatmak için app.py'ı çalıştır.")
    print()

    local_conn.close()
    cloud_conn.close()


def _tufe_ekle(cloud_cur, cloud_conn):
    """kira_senaryo.py'daki gömülü TÜFE verilerini DB'ye ekle."""
    AYLIK_YILLIK = {
        (2005,1):9.24,(2005,2):8.69,(2005,3):7.94,(2005,4):8.18,(2005,5):8.70,
        (2005,6):8.95,(2005,7):7.82,(2005,8):7.91,(2005,9):7.99,(2005,10):7.52,
        (2005,11):7.61,(2005,12):7.72,
        (2006,1):7.93,(2006,2):8.15,(2006,3):8.16,(2006,4):8.83,(2006,5):9.86,
        (2006,6):10.12,(2006,7):11.69,(2006,8):10.26,(2006,9):10.55,(2006,10):9.98,
        (2006,11):9.86,(2006,12):9.65,
        (2007,1):9.93,(2007,2):10.16,(2007,3):10.86,(2007,4):10.72,(2007,5):9.23,
        (2007,6):8.60,(2007,7):6.90,(2007,8):7.39,(2007,9):7.12,(2007,10):7.70,
        (2007,11):8.40,(2007,12):8.39,
        (2008,1):8.17,(2008,2):9.10,(2008,3):9.15,(2008,4):9.66,(2008,5):10.74,
        (2008,6):10.61,(2008,7):12.06,(2008,8):11.77,(2008,9):11.13,(2008,10):11.99,
        (2008,11):10.76,(2008,12):10.06,
        (2009,1):9.50,(2009,2):7.73,(2009,3):7.89,(2009,4):6.13,(2009,5):5.24,
        (2009,6):5.73,(2009,7):5.39,(2009,8):5.33,(2009,9):5.27,(2009,10):5.08,
        (2009,11):5.53,(2009,12):6.53,
        (2010,1):8.19,(2010,2):10.13,(2010,3):9.56,(2010,4):10.19,(2010,5):9.10,
        (2010,6):8.37,(2010,7):7.58,(2010,8):8.33,(2010,9):9.24,(2010,10):8.62,
        (2010,11):7.29,(2010,12):6.40,
        (2011,1):4.90,(2011,2):4.16,(2011,3):3.99,(2011,4):4.26,(2011,5):7.17,
        (2011,6):6.24,(2011,7):6.31,(2011,8):6.65,(2011,9):6.15,(2011,10):7.66,
        (2011,11):9.48,(2011,12):10.45,
        (2012,1):10.61,(2012,2):10.43,(2012,3):10.43,(2012,4):11.14,(2012,5):8.28,
        (2012,6):8.87,(2012,7):9.07,(2012,8):8.88,(2012,9):9.19,(2012,10):7.80,
        (2012,11):6.37,(2012,12):6.16,
        (2013,1):7.31,(2013,2):7.03,(2013,3):7.29,(2013,4):6.13,(2013,5):6.51,
        (2013,6):8.30,(2013,7):8.88,(2013,8):8.17,(2013,9):7.88,(2013,10):7.71,
        (2013,11):7.32,(2013,12):7.40,
        (2014,1):7.75,(2014,2):7.89,(2014,3):8.39,(2014,4):9.38,(2014,5):9.66,
        (2014,6):9.16,(2014,7):9.32,(2014,8):9.54,(2014,9):8.86,(2014,10):8.96,
        (2014,11):9.15,(2014,12):8.17,
        (2015,1):7.24,(2015,2):7.55,(2015,3):7.61,(2015,4):7.91,(2015,5):8.09,
        (2015,6):7.20,(2015,7):6.81,(2015,8):7.14,(2015,9):7.95,(2015,10):7.58,
        (2015,11):8.10,(2015,12):8.81,
        (2016,1):9.58,(2016,2):8.78,(2016,3):7.46,(2016,4):6.57,(2016,5):6.58,
        (2016,6):7.64,(2016,7):8.79,(2016,8):8.05,(2016,9):7.28,(2016,10):7.16,
        (2016,11):7.00,(2016,12):8.53,
        (2017,1):9.22,(2017,2):10.13,(2017,3):11.29,(2017,4):11.87,(2017,5):11.72,
        (2017,6):10.90,(2017,7):9.79,(2017,8):10.68,(2017,9):11.20,(2017,10):11.90,
        (2017,11):12.98,(2017,12):11.92,
        (2018,1):10.35,(2018,2):10.26,(2018,3):10.23,(2018,4):10.85,(2018,5):12.15,
        (2018,6):15.39,(2018,7):15.85,(2018,8):17.90,(2018,9):24.52,(2018,10):25.24,
        (2018,11):21.62,(2018,12):20.30,
        (2019,1):20.35,(2019,2):19.67,(2019,3):19.71,(2019,4):19.50,(2019,5):18.71,
        (2019,6):15.72,(2019,7):16.65,(2019,8):15.01,(2019,9):9.26,(2019,10):8.55,
        (2019,11):10.56,(2019,12):11.84,
        (2020,1):12.15,(2020,2):12.37,(2020,3):11.86,(2020,4):10.94,(2020,5):11.39,
        (2020,6):12.62,(2020,7):11.76,(2020,8):11.77,(2020,9):11.75,(2020,10):11.89,
        (2020,11):14.03,(2020,12):14.60,
        (2021,1):14.97,(2021,2):15.61,(2021,3):16.19,(2021,4):17.14,(2021,5):16.59,
        (2021,6):17.53,(2021,7):18.95,(2021,8):19.25,(2021,9):19.58,(2021,10):19.89,
        (2021,11):21.31,(2021,12):36.08,
        (2022,1):48.69,(2022,2):54.44,(2022,3):61.14,(2022,4):69.97,(2022,5):73.50,
        (2022,6):78.62,(2022,7):79.60,(2022,8):80.21,(2022,9):83.45,(2022,10):85.51,
        (2022,11):84.39,(2022,12):64.27,
        (2023,1):57.68,(2023,2):55.18,(2023,3):50.51,(2023,4):43.68,(2023,5):39.59,
        (2023,6):38.21,(2023,7):47.83,(2023,8):58.94,(2023,9):61.53,(2023,10):61.36,
        (2023,11):61.98,(2023,12):64.77,
        (2024,1):64.86,(2024,2):67.07,(2024,3):68.50,(2024,4):69.80,(2024,5):75.45,
        (2024,6):71.60,(2024,7):61.78,(2024,8):51.97,(2024,9):49.38,(2024,10):48.58,
        (2024,11):47.09,(2024,12):44.38,
        (2025,1):42.12,(2025,2):39.05,(2025,3):38.10,(2025,4):37.86,(2025,5):35.41,
        (2025,6):35.05,(2025,7):33.52,(2025,8):32.95,(2025,9):33.29,(2025,10):32.87,
        (2025,11):31.07,(2025,12):30.89,
        (2026,1):30.65,
    }
    AYLAR = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran",
              "Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]
    rows = [(yil, AYLAR[ay-1], oran) for (yil,ay), oran in AYLIK_YILLIK.items()]
    extras.execute_values(
        cloud_cur,
        """INSERT INTO tufe_verileri (year, month, oran) VALUES %s
           ON CONFLICT (year, month) DO UPDATE SET oran = EXCLUDED.oran""",
        rows, page_size=200
    )
    cloud_conn.commit()
    print(f"   ✓ {len(rows)} TÜFE kaydı eklendi.")


if __name__ == "__main__":
    main()
