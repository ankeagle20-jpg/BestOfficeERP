
"""
BestOffice ERP — SQLite → Supabase PostgreSQL TAM MİGRATİON
============================================================
database.py ile birebir eşleştirildi. Tüm 27 tablo, tüm sütunlar.

Kullanım:
  1. Bu dosyayı C:\\Users\\Dell\\Desktop\\BestOfficeERP klasörüne koy
  2. CLOUD_URL içindeki şifreyi gir
  3. pip install psycopg2-binary
  4. python buluta_tasima.py
"""

import sqlite3
import psycopg2
from psycopg2 import extras
import os, sys
from datetime import datetime

# ─── AYARLAR ──────────────────────────────────────────────────────────────────
LOCAL_DB = "erp.db"   # erp.db de varsa otomatik bulur

CLOUD_URL = "postgresql://postgres.akieehczrcdgsyssjief:Ankara2026anka@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"

AYLAR = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran",
          "Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]

# ─── TAM ŞEMA ─────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY, username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL, full_name TEXT,
    role TEXT NOT NULL DEFAULT 'user', is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT, phone TEXT,
    address TEXT, tax_number TEXT, rent_start_date TEXT,
    rent_start_year INTEGER, rent_start_month TEXT DEFAULT 'Ocak',
    ilk_kira_bedeli REAL NOT NULL DEFAULT 0, current_rent REAL NOT NULL DEFAULT 0,
    office_code TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY, name TEXT NOT NULL, sku TEXT UNIQUE,
    unit_price REAL NOT NULL DEFAULT 0, stock_quantity REAL NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS invoices (
    id SERIAL PRIMARY KEY, invoice_number TEXT UNIQUE NOT NULL,
    customer_id INTEGER, issue_date TEXT DEFAULT CURRENT_TIMESTAMP,
    total_amount REAL NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tufe_verileri (
    year INTEGER NOT NULL, month TEXT NOT NULL, oran REAL NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (year, month)
);
CREATE TABLE IF NOT EXISTS rent_payments (
    id SERIAL PRIMARY KEY, customer_id INTEGER NOT NULL,
    year INTEGER NOT NULL, month TEXT NOT NULL, amount REAL NOT NULL DEFAULT 0,
    UNIQUE(customer_id, year, month)
);
CREATE TABLE IF NOT EXISTS tahsilatlar (
    id SERIAL PRIMARY KEY, customer_id INTEGER NOT NULL,
    tutar REAL NOT NULL DEFAULT 0, odeme_turu TEXT NOT NULL DEFAULT 'N',
    tahsilat_tarihi TEXT NOT NULL, aciklama TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS offices (
    id SERIAL PRIMARY KEY, code TEXT UNIQUE NOT NULL, type TEXT NOT NULL,
    unit_no TEXT, monthly_price REAL DEFAULT 0, status TEXT DEFAULT 'bos',
    is_active INTEGER DEFAULT 1, customer_id INTEGER, notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS personeller (
    id SERIAL PRIMARY KEY, ad_soyad TEXT NOT NULL, pozisyon TEXT,
    telefon TEXT, email TEXT, aktif INTEGER DEFAULT 1,
    notlar TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS devam (
    id SERIAL PRIMARY KEY, personel_id INTEGER, tarih TEXT,
    giris_saati TEXT, cikis_saati TEXT, gec_kaldi INTEGER DEFAULT 0,
    gec_dakika INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS personel_izin (
    id SERIAL PRIMARY KEY, personel_id INTEGER NOT NULL,
    izin_turu TEXT NOT NULL, baslangic_tarihi TEXT NOT NULL,
    bitis_tarihi TEXT NOT NULL, gun_sayisi REAL NOT NULL DEFAULT 1,
    yari_gun INTEGER DEFAULT 0, aciklama TEXT,
    onay_durumu TEXT DEFAULT 'bekliyor', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS personel_bilgi (
    personel_id INTEGER PRIMARY KEY, ise_baslama_tarihi TEXT,
    yillik_izin_hakki INTEGER DEFAULT 14, manuel_izin_gun INTEGER DEFAULT 0,
    unvan TEXT, departman TEXT, tc_no TEXT
);
CREATE TABLE IF NOT EXISTS firma_ayar (
    id INTEGER PRIMARY KEY DEFAULT 1, firma_adi TEXT, firma_vkn TEXT,
    firma_adres TEXT, firma_tel TEXT, firma_vergi_dairesi TEXT,
    fatura_seri TEXT DEFAULT 'EA', baslangic_no INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS faturalar (
    id SERIAL PRIMARY KEY, fatura_no TEXT UNIQUE NOT NULL,
    musteri_id INTEGER, musteri_adi TEXT NOT NULL, musteri_vkn TEXT,
    musteri_adres TEXT, fatura_tarihi TEXT NOT NULL, vade_tarihi TEXT,
    fatura_turu TEXT DEFAULT 'SATIŞ', durum TEXT DEFAULT 'taslak',
    toplam_matrah REAL DEFAULT 0, toplam_kdv REAL DEFAULT 0,
    toplam_iskonto REAL DEFAULT 0, genel_toplam REAL DEFAULT 0,
    not_aciklama TEXT, pdf_yolu TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS fatura_kalemleri (
    id SERIAL PRIMARY KEY, fatura_id INTEGER NOT NULL, aciklama TEXT NOT NULL,
    miktar REAL DEFAULT 1, birim TEXT DEFAULT 'Adet', birim_fiyat REAL DEFAULT 0,
    iskonto_oran REAL DEFAULT 0, kdv_oran REAL DEFAULT 20,
    matrah REAL DEFAULT 0, kdv_tutar REAL DEFAULT 0, toplam REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fatura_tahsilat (
    id SERIAL PRIMARY KEY, fatura_id INTEGER NOT NULL, tarih TEXT NOT NULL,
    tutar REAL NOT NULL, odeme_sekli TEXT DEFAULT 'Banka', aciklama TEXT
);
CREATE TABLE IF NOT EXISTS kargolar (
    id SERIAL PRIMARY KEY, musteri_id INTEGER NOT NULL, tarih TEXT NOT NULL,
    teslim_alan TEXT, kargo_firmasi TEXT, takip_no TEXT, notlar TEXT,
    whatsapp_gonderildi INTEGER DEFAULT 0, odeme_tutari REAL DEFAULT 0,
    odeme_durumu TEXT DEFAULT 'odenmedi', fatura_id INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kargo_resimler (
    id SERIAL PRIMARY KEY, kargo_id INTEGER NOT NULL, dosya_yolu TEXT NOT NULL,
    dosya_adi TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS banka_hesaplar (
    id SERIAL PRIMARY KEY, banka_adi TEXT NOT NULL, hesap_adi TEXT,
    iban TEXT, para_birimi TEXT DEFAULT 'TRY', aktif INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS banka_hareketler (
    id SERIAL PRIMARY KEY, hesap_id INTEGER NOT NULL, tarih TEXT NOT NULL,
    aciklama TEXT, tutar REAL NOT NULL, bakiye REAL, tip TEXT DEFAULT 'alacak',
    referans TEXT, gonderen TEXT, eslestirme_durumu TEXT DEFAULT 'eslesmedi',
    musteri_id INTEGER, tahsilat_id INTEGER, kaynak_dosya TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS giris_alanlar (
    id SERIAL PRIMARY KEY, alan_kodu TEXT NOT NULL UNIQUE,
    alan_adi TEXT NOT NULL, kategori TEXT NOT NULL,
    zorunlu INTEGER DEFAULT 1, aktif INTEGER DEFAULT 1, sira INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS musteri_kyc (
    id SERIAL PRIMARY KEY, musteri_id INTEGER, sirket_unvani TEXT,
    vergi_no TEXT, vergi_dairesi TEXT, mersis_no TEXT, ticaret_sicil_no TEXT,
    kurulus_tarihi TEXT, faaliyet_konusu TEXT, nace_kodu TEXT,
    eski_adres TEXT, yeni_adres TEXT, sube_merkez TEXT DEFAULT 'Merkez',
    yetkili_adsoyad TEXT, yetkili_tcno TEXT, yetkili_dogum TEXT,
    yetkili_ikametgah TEXT, yetkili_tel TEXT, yetkili_tel2 TEXT, yetkili_email TEXT,
    ortak1_adsoyad TEXT, ortak1_pay TEXT, ortak2_adsoyad TEXT, ortak2_pay TEXT,
    ortak3_adsoyad TEXT, ortak3_pay TEXT, yabanci_adsoyad TEXT,
    yabanci_uyruk TEXT, yabanci_pasaport TEXT, hizmet_turu TEXT DEFAULT 'Sanal Ofis',
    ofis_kodu TEXT, aylik_kira REAL DEFAULT 0, yillik_kira REAL DEFAULT 0,
    sozlesme_no TEXT, sozlesme_tarihi TEXT, sozlesme_bitis TEXT,
    evrak_imza_sirkuleri INTEGER DEFAULT 0, evrak_vergi_levhasi INTEGER DEFAULT 0,
    evrak_ticaret_sicil INTEGER DEFAULT 0, evrak_faaliyet_belgesi INTEGER DEFAULT 0,
    evrak_kimlik_fotokopi INTEGER DEFAULT 0, evrak_ikametgah INTEGER DEFAULT 0,
    evrak_kase INTEGER DEFAULT 0, notlar TEXT, tamamlanma_yuzdesi INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kyc_belgeler (
    id SERIAL PRIMARY KEY, kyc_id INTEGER NOT NULL, belge_tipi TEXT,
    dosya_yolu TEXT NOT NULL, dosya_adi TEXT,
    yuklenme_tarihi TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sozlesmeler (
    id SERIAL PRIMARY KEY, sozlesme_no TEXT UNIQUE NOT NULL,
    kyc_id INTEGER, musteri_id INTEGER, musteri_adi TEXT, hizmet_turu TEXT,
    dosya_yolu TEXT, olusturma_tarihi TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS web_users (
    id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, email TEXT,
    password_hash TEXT NOT NULL, rol TEXT NOT NULL DEFAULT 'goruntuleme',
    aktif BOOLEAN DEFAULT TRUE, olusturma TIMESTAMPTZ DEFAULT NOW(),
    son_giris TIMESTAMPTZ
);
"""

# ─── TABLO LİSTESİ (bağımlılık sırasına göre) ────────────────────────────────
MIGRATE_TABLES = [
    # Bağımsız tablolar önce
    ("users",            "users"),
    ("customers",        "customers"),
    ("products",         "products"),
    ("offices",          "offices"),
    ("firma_ayar",       "firma_ayar"),
    ("personeller",      "personeller"),
    ("banka_hesaplar",   "banka_hesaplar"),
    ("giris_alanlar",    "giris_alanlar"),
    # FK bağımlı tablolar
    ("invoices",         "invoices"),
    ("tufe_verileri",    "tufe_verileri"),
    ("rent_payments",    "rent_payments"),
    ("tahsilatlar",      "tahsilatlar"),
    ("faturalar",        "faturalar"),
    ("fatura_kalemleri", "fatura_kalemleri"),
    ("fatura_tahsilat",  "fatura_tahsilat"),
    ("kargolar",         "kargolar"),
    ("kargo_resimler",   "kargo_resimler"),
    ("banka_hareketler", "banka_hareketler"),
    ("personel_bilgi",   "personel_bilgi"),
    ("personel_izin",    "personel_izin"),
    ("devam",            "devam"),
    ("musteri_kyc",      "musteri_kyc"),
    ("kyc_belgeler",     "kyc_belgeler"),
    ("sozlesmeler",      "sozlesmeler"),
]


def sq_cols(conn, tablo):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({tablo})")
    return [r[1] for r in cur.fetchall() if r[1].lower() != "id"]


def pg_cols(cur, tablo):
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s", (tablo,))
    return {r[0] for r in cur.fetchall()}


def tablo_var_mi(conn, tablo):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tablo,))
    return bool(cur.fetchone())


def migrate_tablo(local, cloud_cur, sq, pg):
    if not tablo_var_mi(local, sq):
        print(f"   ⏭  {sq:<24} — SQLite'da yok")
        return 0
    cols = sq_cols(local, sq)
    if not cols:
        print(f"   ⚠  {pg:<24} — sütun yok"); return 0
    pg_c = pg_cols(cloud_cur, pg)
    cols = [c for c in cols if c in pg_c]
    if not cols:
        print(f"   ⚠  {pg:<24} — ortak sütun yok"); return 0
    cur = local.cursor()
    cur.execute(f"SELECT {', '.join(cols)} FROM [{sq}]")
    rows = cur.fetchall()
    if not rows:
        print(f"   ○  {pg:<24} — boş"); return 0
    try:
        cloud_cur.execute(f"TRUNCATE TABLE {pg} RESTART IDENTITY CASCADE")
    except Exception:
        cloud_cur.execute(f"DELETE FROM {pg}")
    extras.execute_values(
        cloud_cur,
        f"INSERT INTO {pg} ({', '.join(cols)}) VALUES %s ON CONFLICT DO NOTHING",
        rows, page_size=500)
    print(f"   ✓  {pg:<24} {len(rows):>5} satır")
    return len(rows)


def tufe_yukle(cur, conn):
    AYLIK = {
        (2005,1):9.24,(2005,2):8.69,(2005,3):7.94,(2005,4):8.18,(2005,5):8.70,(2005,6):8.95,(2005,7):7.82,(2005,8):7.91,(2005,9):7.99,(2005,10):7.52,(2005,11):7.61,(2005,12):7.72,
        (2006,1):7.93,(2006,2):8.15,(2006,3):8.16,(2006,4):8.83,(2006,5):9.86,(2006,6):10.12,(2006,7):11.69,(2006,8):10.26,(2006,9):10.55,(2006,10):9.98,(2006,11):9.86,(2006,12):9.65,
        (2007,1):9.93,(2007,2):10.16,(2007,3):10.86,(2007,4):10.72,(2007,5):9.23,(2007,6):8.60,(2007,7):6.90,(2007,8):7.39,(2007,9):7.12,(2007,10):7.70,(2007,11):8.40,(2007,12):8.39,
        (2008,1):8.17,(2008,2):9.10,(2008,3):9.15,(2008,4):9.66,(2008,5):10.74,(2008,6):10.61,(2008,7):12.06,(2008,8):11.77,(2008,9):11.13,(2008,10):11.99,(2008,11):10.76,(2008,12):10.06,
        (2009,1):9.50,(2009,2):7.73,(2009,3):7.89,(2009,4):6.13,(2009,5):5.24,(2009,6):5.73,(2009,7):5.39,(2009,8):5.33,(2009,9):5.27,(2009,10):5.08,(2009,11):5.53,(2009,12):6.53,
        (2010,1):8.19,(2010,2):10.13,(2010,3):9.56,(2010,4):10.19,(2010,5):9.10,(2010,6):8.37,(2010,7):7.58,(2010,8):8.33,(2010,9):9.24,(2010,10):8.62,(2010,11):7.29,(2010,12):6.40,
        (2011,1):4.90,(2011,2):4.16,(2011,3):3.99,(2011,4):4.26,(2011,5):7.17,(2011,6):6.24,(2011,7):6.31,(2011,8):6.65,(2011,9):6.15,(2011,10):7.66,(2011,11):9.48,(2011,12):10.45,
        (2012,1):10.61,(2012,2):10.43,(2012,3):10.43,(2012,4):11.14,(2012,5):8.28,(2012,6):8.87,(2012,7):9.07,(2012,8):8.88,(2012,9):9.19,(2012,10):7.80,(2012,11):6.37,(2012,12):6.16,
        (2013,1):7.31,(2013,2):7.03,(2013,3):7.29,(2013,4):6.13,(2013,5):6.51,(2013,6):8.30,(2013,7):8.88,(2013,8):8.17,(2013,9):7.88,(2013,10):7.71,(2013,11):7.32,(2013,12):7.40,
        (2014,1):7.75,(2014,2):7.89,(2014,3):8.39,(2014,4):9.38,(2014,5):9.66,(2014,6):9.16,(2014,7):9.32,(2014,8):9.54,(2014,9):8.86,(2014,10):8.96,(2014,11):9.15,(2014,12):8.17,
        (2015,1):7.24,(2015,2):7.55,(2015,3):7.61,(2015,4):7.91,(2015,5):8.09,(2015,6):7.20,(2015,7):6.81,(2015,8):7.14,(2015,9):7.95,(2015,10):7.58,(2015,11):8.10,(2015,12):8.81,
        (2016,1):9.58,(2016,2):8.78,(2016,3):7.46,(2016,4):6.57,(2016,5):6.58,(2016,6):7.64,(2016,7):8.79,(2016,8):8.05,(2016,9):7.28,(2016,10):7.16,(2016,11):7.00,(2016,12):8.53,
        (2017,1):9.22,(2017,2):10.13,(2017,3):11.29,(2017,4):11.87,(2017,5):11.72,(2017,6):10.90,(2017,7):9.79,(2017,8):10.68,(2017,9):11.20,(2017,10):11.90,(2017,11):12.98,(2017,12):11.92,
        (2018,1):10.35,(2018,2):10.26,(2018,3):10.23,(2018,4):10.85,(2018,5):12.15,(2018,6):15.39,(2018,7):15.85,(2018,8):17.90,(2018,9):24.52,(2018,10):25.24,(2018,11):21.62,(2018,12):20.30,
        (2019,1):20.35,(2019,2):19.67,(2019,3):19.71,(2019,4):19.50,(2019,5):18.71,(2019,6):15.72,(2019,7):16.65,(2019,8):15.01,(2019,9):9.26,(2019,10):8.55,(2019,11):10.56,(2019,12):11.84,
        (2020,1):12.15,(2020,2):12.37,(2020,3):11.86,(2020,4):10.94,(2020,5):11.39,(2020,6):12.62,(2020,7):11.76,(2020,8):11.77,(2020,9):11.75,(2020,10):11.89,(2020,11):14.03,(2020,12):14.60,
        (2021,1):14.97,(2021,2):15.61,(2021,3):16.19,(2021,4):17.14,(2021,5):16.59,(2021,6):17.53,(2021,7):18.95,(2021,8):19.25,(2021,9):19.58,(2021,10):19.89,(2021,11):21.31,(2021,12):36.08,
        (2022,1):48.69,(2022,2):54.44,(2022,3):61.14,(2022,4):69.97,(2022,5):73.50,(2022,6):78.62,(2022,7):79.60,(2022,8):80.21,(2022,9):83.45,(2022,10):85.51,(2022,11):84.39,(2022,12):64.27,
        (2023,1):57.68,(2023,2):55.18,(2023,3):50.51,(2023,4):43.68,(2023,5):39.59,(2023,6):38.21,(2023,7):47.83,(2023,8):58.94,(2023,9):61.53,(2023,10):61.36,(2023,11):61.98,(2023,12):64.77,
        (2024,1):64.86,(2024,2):67.07,(2024,3):68.50,(2024,4):69.80,(2024,5):75.45,(2024,6):71.60,(2024,7):61.78,(2024,8):51.97,(2024,9):49.38,(2024,10):48.58,(2024,11):47.09,(2024,12):44.38,
        (2025,1):42.12,(2025,2):39.05,(2025,3):38.10,(2025,4):37.86,(2025,5):35.41,(2025,6):35.05,(2025,7):33.52,(2025,8):32.95,(2025,9):33.29,(2025,10):32.87,(2025,11):31.07,(2025,12):30.89,
        (2026,1):30.65,
    }
    rows = [(y, AYLAR[m-1], o) for (y,m),o in AYLIK.items()]
    extras.execute_values(cur,
        "INSERT INTO tufe_verileri (year,month,oran) VALUES %s "
        "ON CONFLICT (year,month) DO UPDATE SET oran=EXCLUDED.oran",
        rows, page_size=200)
    conn.commit()
    print(f"   ✓  tufe_verileri          {len(rows):>5} TÜFE kaydı (2005-2026)")


def main():
    print()
    print("=" * 62)
    print("  BestOffice ERP → Supabase TAM MİGRATİON")
    print(f"  {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print("=" * 62)

    if "BURAYA_SUPABASE_SIFREN" in CLOUD_URL:
        print("\n❌ Supabase şifreni girmedin!")
        print("   CLOUD_URL satırındaki 'BURAYA_SUPABASE_SIFREN' kısmını")
        print("   gerçek şifrenle değiştir.\n"); sys.exit(1)

    # DB bul
    db_yolu = LOCAL_DB
    if not os.path.exists(db_yolu):
        for alt in ["erp.db", "bestoffice.db"]:
            if os.path.exists(alt):
                db_yolu = alt; break
        else:
            print(f"❌ '{LOCAL_DB}' bulunamadı! Scripti BestOfficeERP klasörüne koy.")
            sys.exit(1)

    # SQLite aç
    print(f"\n📂 Veritabanı: {db_yolu}")
    local = sqlite3.connect(db_yolu)
    local.row_factory = sqlite3.Row
    cur = local.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    mevcut = [r[0] for r in cur.fetchall()]
    print(f"   {len(mevcut)} tablo bulundu:")
    for t in mevcut:
        cur.execute(f"SELECT COUNT(*) FROM [{t}]")
        n = cur.fetchone()[0]
        print(f"      • {t:<26} {n:>5} satır")

    # Supabase bağlan
    print("\n🌐 Supabase'e bağlanılıyor...")
    try:
        cloud = psycopg2.connect(CLOUD_URL, connect_timeout=15)
        cloud.autocommit = False
        c = cloud.cursor()
        c.execute("SELECT version()")
        print(f"   ✓ Bağlandı!")
    except Exception as e:
        print(f"❌ Bağlantı hatası: {e}")
        print("\n   → Şifreyi kontrol et: Supabase → Database → Settings → Reset password")
        sys.exit(1)

    # Şema
    print("\n🏗️  Tablolar oluşturuluyor...")
    try:
        c.execute(SCHEMA); cloud.commit()
        print(f"   ✓ {len(MIGRATE_TABLES)} tablo şeması hazır.")
    except Exception as e:
        print(f"❌ Şema hatası: {e}"); cloud.rollback(); sys.exit(1)

    # Taşı
    print(f"\n📦 Veri taşıma ({len(MIGRATE_TABLES)} tablo):\n")
    toplam = 0; hatalar = []
    for sq, pg in MIGRATE_TABLES:
        try:
            n = migrate_tablo(local, c, sq, pg)
            toplam += n; cloud.commit()
        except Exception as e:
            print(f"   ❌ {pg:<24} HATA: {e}")
            hatalar.append((pg, str(e))); cloud.rollback()

    # TÜFE
    print("\n📊 TÜFE verileri (TCMB 2005-2026):")
    try: tufe_yukle(c, cloud)
    except Exception as e: print(f"   ⚠ {e}")

    # Özet
    print()
    print("=" * 62)
    if not hatalar:
        print(f"✅ BAŞARILI! {toplam} satır Supabase'e taşındı.")
    else:
        print(f"⚠️  {toplam} satır taşındı, {len(hatalar)} hata:")
        for t, e in hatalar: print(f"   • {t}: {e[:60]}")
    print("=" * 62)
    print("\nKontrol: https://supabase.com/dashboard/project/akieehczrcdgsyssjief/editor\n")
    local.close(); cloud.close()

if __name__ == "__main__":
    main()
