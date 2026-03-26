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
        connect_timeout=5,
        cursor_factory=psycopg2.extras.RealDictCursor
    )


@contextmanager
def db():
    """Context manager: otomatik commit/rollback."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except BaseException:
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
    sevk_adresi     TEXT,
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
    kdv_orani       INTEGER DEFAULT 20,
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
        ensure_customers_musteri_adi()
        ensure_customers_rent_columns()
        ensure_customers_excel_columns()
        ensure_customers_quick_edit_columns()
        ensure_customers_durum()
        ensure_customers_kapanis_tarihi()
        ensure_customers_cari_columns()
        ensure_customer_financial_profile()
        ensure_customers_balance_trigger()
        ensure_cari_360_tables()
        ensure_tahsilatlar_columns()
        ensure_kargolar_durum()
        ensure_faturalar_amount_columns()
        ensure_musteri_kyc_columns()
        ensure_hizmet_turleri_table()
        ensure_office_rentals()
        ensure_crm_leads()
        ensure_personel_extra_columns()
        ensure_personel_bilgi_dogum_tarihi()
        ensure_personel_izin_onay_durumu()
        ensure_personel_izin_saat_sayisi()
        ensure_personel_ozluk()
        ensure_personel_ozluk_izin_columns()
        ensure_contracts_engine()
    print("✅ Supabase şema oluşturuldu.")


def ensure_personel_extra_columns():
    """personel tablosuna mesai, giris_tarihi, mac_adres, notlar sütunlarını ekle (varsa dokunma)."""
    for col, ctype in (
        ("mesai_baslangic", "TEXT"),
        ("mesai_bitis", "TEXT"),
        ("giris_tarihi", "DATE"),
        ("mac_adres", "TEXT"),
        ("notlar", "TEXT"),
    ):
        try:
            execute(f"ALTER TABLE personel ADD COLUMN IF NOT EXISTS {col} {ctype}")
        except Exception as e:
            print(f"personel.{col}: {e}")


def ensure_personel_bilgi_dogum_tarihi():
    """personel_bilgi tablosuna dogum_tarihi (4857 yaş istisnası için) ekler."""
    try:
        execute("ALTER TABLE personel_bilgi ADD COLUMN IF NOT EXISTS dogum_tarihi DATE")
    except Exception as e:
        print(f"personel_bilgi.dogum_tarihi: {e}")


def ensure_personel_izin_onay_durumu():
    """personel_izin tablosuna onay_durumu ekler (yoksa). Mevcut kayıtlara dokunulmaz (NULL kalır)."""
    try:
        execute("ALTER TABLE personel_izin ADD COLUMN IF NOT EXISTS onay_durumu TEXT DEFAULT 'bekliyor'")
    except Exception as e:
        print(f"personel_izin.onay_durumu: {e}")


def ensure_personel_izin_saat_sayisi():
    """personel_izin tablosuna saatlik izin için saat_sayisi ekler (günlük izinde 0)."""
    try:
        execute("ALTER TABLE personel_izin ADD COLUMN IF NOT EXISTS saat_sayisi INTEGER DEFAULT 0")
    except Exception as e:
        print(f"personel_izin.saat_sayisi: {e}")


def ensure_personel_ozluk():
    """Özlük / detay bilgileri tablosu (sadece admin)."""
    try:
        execute("""
            CREATE TABLE IF NOT EXISTS personel_ozluk (
                personel_id INTEGER PRIMARY KEY REFERENCES personel(id) ON DELETE CASCADE,
                tc_kimlik TEXT, dogum_tarihi DATE, dogum_yeri TEXT, medeni_durum TEXT, esi_calisiyor TEXT, cocuk_sayisi INTEGER,
                cinsiyet TEXT, kan_grubu TEXT, ikametgah TEXT, cep_telefon TEXT, mac_adres TEXT, email TEXT, acil_kisi TEXT,
                ise_giris_tarihi DATE, departman TEXT, unvan TEXT, gorev_tanimi TEXT, calisma_sekli TEXT, ucret_bilgisi TEXT,
                iban TEXT, yemek_yol_yardim TEXT, ogrenim_durumu TEXT, mezun_okul_bolum TEXT, yabanci_dil TEXT,
                adli_sicil TEXT, saglik_raporu TEXT, ikametgah_belgesi TEXT, diploma TEXT, nufus_kayit TEXT, askerlik_durum TEXT,
                notlar TEXT, updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    except Exception as e:
        print(f"personel_ozluk: {e}")


def ensure_personel_ozluk_izin_columns():
    """personel_ozluk tablosuna izin hakediş/kalan (gün+saat) sütunlarını ekler."""
    for col in ("izin_hakedis_gun", "izin_hakedis_saat", "izin_kalan_gun", "izin_kalan_saat"):
        try:
            execute(f"ALTER TABLE personel_ozluk ADD COLUMN IF NOT EXISTS {col} INTEGER")
        except Exception as e:
            print(f"personel_ozluk.{col}: {e}")


def ensure_crm_leads():
    """CRM Lead tablosu: potansiyel müşteriler ve satış pipeline alanları."""
    try:
        execute(
            """
            CREATE TABLE IF NOT EXISTS crm_leads (
                id SERIAL PRIMARY KEY,
                ad_soyad TEXT NOT NULL,
                firma_adi TEXT,
                telefon TEXT,
                email TEXT,
                sektor TEXT,
                hizmet_turu TEXT,
                lead_durumu TEXT,
                lead_skoru INTEGER DEFAULT 0,
                ilk_gorusme DATE,
                son_gorusme DATE,
                takip_tarihi DATE,
                sorumlu_satis TEXT,
                notlar TEXT
            )
            """
        )
    except Exception as e:
        print(f"crm_leads: {e}")


def ensure_musteri_kyc_columns():
    """Supabase musteri_kyc tablosunu web tarafındaki KYC şemasına yaklaştırır.

    Not: migrate_to_supabase.py içindeki ilk şema daha sade; buradaki kolonlar
    API'nin beklediği alanlarla uyumlu olacak şekilde sonradan eklenir.
    Var olan kolonlara dokunulmaz.
    """
    columns = (
        ("sirket_unvani", "TEXT"),
        ("musteri_adi", "TEXT"),
        ("unvan", "TEXT"),
        ("email", "TEXT"),
        ("mersis_no", "TEXT"),
        ("ticaret_sicil_no", "TEXT"),
        ("kurulus_tarihi", "DATE"),
        ("faaliyet_konusu", "TEXT"),
        ("nace_kodu", "TEXT"),
        ("eski_adres", "TEXT"),
        ("yeni_adres", "TEXT"),
        ("sube_merkez", "TEXT"),
        ("yetkili_tcno", "TEXT"),
        ("yetkili_dogum", "DATE"),
        ("yetkili_ikametgah", "TEXT"),
        ("yillik_kira", "NUMERIC(12,2)"),
        ("sozlesme_no", "TEXT"),
        ("evrak_imza_sirkuleri", "INTEGER DEFAULT 0"),
        ("evrak_vergi_levhasi", "INTEGER DEFAULT 0"),
        ("evrak_ticaret_sicil", "INTEGER DEFAULT 0"),
        ("evrak_faaliyet_belgesi", "INTEGER DEFAULT 0"),
        ("evrak_kimlik_fotokopi", "INTEGER DEFAULT 0"),
        ("evrak_ikametgah", "INTEGER DEFAULT 0"),
        ("evrak_kase", "INTEGER DEFAULT 0"),
        ("notlar", "TEXT"),
        ("tamamlanma_yuzdesi", "INTEGER DEFAULT 0"),
        ("created_at", "TIMESTAMPTZ DEFAULT NOW()"),
        ("updated_at", "TIMESTAMPTZ DEFAULT NOW()"),
        ("kira_artis_tarihi", "DATE"),
    )
    for col, ctype in columns:
        try:
            execute(f"ALTER TABLE musteri_kyc ADD COLUMN IF NOT EXISTS {col} {ctype}")
        except Exception as e:
            print(f"musteri_kyc.{col}: {e}")


def ensure_contracts_engine():
    """Sözleşme / taksit / hukuk motoru tablolarını oluştur."""
    try:
        # Ana sözleşme tablosu
        execute(
            """
            CREATE TABLE IF NOT EXISTS contracts (
                id                  SERIAL PRIMARY KEY,
                musteri_id          INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                cari_kodu           TEXT,
                sozlesme_no         TEXT,
                baslangic_tarihi    DATE NOT NULL,
                bitis_tarihi        DATE,
                sure_ay             INTEGER,
                aylik_kira          NUMERIC(12,2) NOT NULL,
                toplam_tutar        NUMERIC(14,2),
                para_birimi         TEXT DEFAULT 'TRY',
                odeme_gunu          INTEGER,              -- ayın kaçıncı günü
                depozito            NUMERIC(12,2),
                gecikme_faizi_orani NUMERIC(6,2),
                yillik_artis_orani  NUMERIC(6,2),
                muacceliyet_var     BOOLEAN DEFAULT FALSE,
                durum               TEXT DEFAULT 'aktif',  -- aktif / gecikmeli / ihtar / avukatlik / kapandi
                aciklama            TEXT,
                created_at          TIMESTAMPTZ DEFAULT NOW(),
                updated_at          TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    except Exception as e:
        print(f"contracts: {e}")

    try:
        # Taksit planı tablosu
        execute(
            """
            CREATE TABLE IF NOT EXISTS contract_installments (
                id               SERIAL PRIMARY KEY,
                contract_id      INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
                musteri_id       INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                taksit_no        INTEGER NOT NULL,
                vade_tarihi      DATE NOT NULL,
                tutar            NUMERIC(12,2) NOT NULL,
                odeme_durumu     TEXT DEFAULT 'planlandi',  -- planlandi / tahakkuk / odendi / gecikmis / icrada
                odenen_tutar     NUMERIC(12,2) DEFAULT 0,
                kalan_tutar      NUMERIC(12,2) DEFAULT 0,
                tahakkuk_tarihi  DATE,
                odeme_tarihi     DATE,
                created_at       TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    except Exception as e:
        print(f"contract_installments: {e}")

    try:
        # Hukuki süreç tablosu
        execute(
            """
            CREATE TABLE IF NOT EXISTS legal_cases (
                id              SERIAL PRIMARY KEY,
                musteri_id      INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                contract_id     INTEGER REFERENCES contracts(id) ON DELETE SET NULL,
                durum           TEXT,         -- ihtar / arabuluculuk / icra / dava / tahsil / kapandi
                aciklama        TEXT,
                toplam_borc     NUMERIC(14,2),
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                updated_at      TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    except Exception as e:
        print(f"legal_cases: {e}")


def ensure_potansiyel_musteriler():
    """Potansiyel müşteri havuzu: teklif aşamasındakiler + hatırlatma tarihleri.

    NOT: Eski sürümler için geriye dönük; yeni CRM için crm_leads kullanılmaktadır.
    """
    try:
        execute(
            """
            CREATE TABLE IF NOT EXISTS potansiyel_musteriler (
                id SERIAL PRIMARY KEY,
                ad TEXT NOT NULL,
                telefon TEXT,
                paket TEXT,
                gorusme_notu TEXT,
                hatirlatma_tarihi DATE,
                durum TEXT DEFAULT 'düşünüyor',
                kaynak TEXT,
                converted_customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
                last_reminder_sent_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    except Exception as e:
        print(f"potansiyel_musteriler: {e}")



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


def ensure_customers_musteri_adi():
    """Şirket ünvanından ayrı kısa / görünen müşteri adı (opsiyonel)."""
    try:
        execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS musteri_adi TEXT")
    except Exception as e:
        print(f"customers.musteri_adi: {e}")


def ensure_customers_notes():
    """Customers tablosuna notes ve ev_adres sütunlarını ekle."""
    try:
        execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS notes TEXT")
    except Exception as e:
        print(f"Notes sütunu zaten var veya hata: {e}")
    try:
        execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS ev_adres TEXT")
    except Exception as e:
        print(f"customers.ev_adres: {e}")


def ensure_customers_rent_columns():
    """Customers tablosuna kira başlangıç ve ilk/güncel kira sütunları ekle (toplu tahsilat için)."""
    for col, typ in (
        ("rent_start_date", "DATE"),
        ("rent_start_year", "INTEGER"),
        ("rent_start_month", "TEXT DEFAULT 'Ocak'"),
        ("ilk_kira_bedeli", "NUMERIC(12,2) DEFAULT 0"),
        ("guncel_kira_bedeli", "NUMERIC(12,2) DEFAULT 0"),
    ):
        try:
            execute(f"ALTER TABLE customers ADD COLUMN IF NOT EXISTS {col} {typ}")
        except Exception as e:
            print(f"customers.{col}: {e}")


def ensure_customers_excel_columns():
    """Excel'den gelen ekstra alanlar: yetkili_kisi, hizmet_turu, phone2, yetkili_tcno."""
    for col, typ in (
        ("yetkili_kisi", "TEXT"),
        ("hizmet_turu", "TEXT"),
        ("phone2", "TEXT"),
        ("yetkili_tcno", "TEXT"),
    ):
        try:
            execute(f"ALTER TABLE customers ADD COLUMN IF NOT EXISTS {col} {typ}")
        except Exception as e:
            print(f"customers.{col}: {e}")


def ensure_customers_quick_edit_columns():
    """Hızlı bilgi düzenleme: manuel_borc, son_odeme_tarihi (cari kart / listede gösterim)."""
    for col, typ in (
        ("manuel_borc", "NUMERIC(12,2)"),
        ("son_odeme_tarihi", "DATE"),
    ):
        try:
            execute(f"ALTER TABLE customers ADD COLUMN IF NOT EXISTS {col} {typ}")
        except Exception as e:
            print(f"customers.{col}: {e}")


def ensure_hizmet_turleri_table():
    """Sözleşme / müşteri formunda seçilebilir hizmet türleri (kullanıcı yeni ekleyebilir)."""
    try:
        execute(
            """
            CREATE TABLE IF NOT EXISTS hizmet_turleri (
                id SERIAL PRIMARY KEY,
                ad TEXT NOT NULL,
                sira INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(ad)
            )
            """
        )
    except Exception as e:
        print(f"hizmet_turleri CREATE: {e}")
    varsayilan = (
        ("Sanal Ofis", 1),
        ("Hazır Ofis", 2),
        ("Paylaşımlı Ofis", 3),
        ("Paylaşımlı Masa", 4),
    )
    for ad, sira in varsayilan:
        try:
            execute(
                "INSERT INTO hizmet_turleri (ad, sira) VALUES (%s, %s) ON CONFLICT (ad) DO NOTHING",
                (ad, sira),
            )
        except Exception as e:
            print(f"hizmet_turleri seed {ad}: {e}")


def ensure_customers_cari_columns():
    """Cari kart: vergi_dairesi, mersis_no, nace_kodu, ofis_tipi, tebligat_adresi."""
    for col, typ in (
        ("vergi_dairesi", "TEXT"),
        ("mersis_no", "TEXT"),
        ("nace_kodu", "TEXT"),
        ("ofis_tipi", "TEXT"),
        ("tebligat_adresi", "TEXT"),
    ):
        try:
            execute(f"ALTER TABLE customers ADD COLUMN IF NOT EXISTS {col} {typ}")
        except Exception as e:
            print(f"customers.{col}: {e}")


def ensure_customer_financial_profile():
    """Cari kart finansal profil: risk limiti, vade günü, tahmini ödeme günü, karlılık, hukuki eşik, mutabakat, notlar."""
    try:
        execute("""
            CREATE TABLE IF NOT EXISTS customer_financial_profile (
                id                      SERIAL PRIMARY KEY,
                musteri_id              INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                risk_limit              NUMERIC(14,2),
                vade_gunu               INTEGER DEFAULT 5,
                odeme_tercihi           TEXT,
                gecikme_faiz_orani      NUMERIC(6,2),
                stopaj_durumu            TEXT,
                tahmini_odeme_gunu      INTEGER,
                yillik_karlilik_endeksi NUMERIC(12,2),
                hukuki_esk_puan         INTEGER DEFAULT 0,
                mutabakat_tarihi        DATE,
                ic_not                  TEXT,
                hukuki_surec            TEXT,
                created_at              TIMESTAMPTZ DEFAULT NOW(),
                updated_at              TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(musteri_id)
            )
        """)
    except Exception as e:
        print(f"customer_financial_profile: {e}")


def ensure_customers_balance_trigger():
    """Customers.current_balance için yürüyen bakiye trigger'ı oluştur.

    Mantık: current_balance = SUM(faturalar.toplam/tutar) - SUM(tahsilatlar.tutar)
    (sadece ilgili musteri_id için).
    """
    try:
        execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS current_balance NUMERIC(14,2) DEFAULT 0")
    except Exception as e:
        print(f"customers.current_balance: {e}")
    # Trigger fonksiyonu: NEW/OLD üzerinden musteri_id alır
    try:
        execute(
            """
            CREATE OR REPLACE FUNCTION fn_update_customer_balance()
            RETURNS trigger AS $$
            DECLARE
                v_borc NUMERIC(14,2);
                v_alacak NUMERIC(14,2);
                v_mid INTEGER;
            BEGIN
                v_mid := COALESCE(NEW.musteri_id, OLD.musteri_id);
                IF v_mid IS NULL THEN
                    RETURN NULL;
                END IF;
                SELECT COALESCE(SUM(COALESCE(toplam, tutar, 0)), 0)
                  INTO v_borc
                  FROM faturalar
                 WHERE musteri_id = v_mid;
                SELECT COALESCE(SUM(tutar), 0)
                  INTO v_alacak
                  FROM tahsilatlar
                 WHERE musteri_id = v_mid;
                UPDATE customers
                   SET current_balance = COALESCE(v_borc,0) - COALESCE(v_alacak,0)
                 WHERE id = v_mid;
                RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    except Exception as e:
        print(f"fn_update_customer_balance: {e}")
    # Faturalar trigger
    try:
        execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger WHERE tgname = 'trg_faturalar_update_balance'
                ) THEN
                    CREATE TRIGGER trg_faturalar_update_balance
                    AFTER INSERT OR UPDATE OR DELETE ON faturalar
                    FOR EACH ROW
                    EXECUTE FUNCTION fn_update_customer_balance();
                END IF;
            END$$;
            """
        )
    except Exception as e:
        print(f"trg_faturalar_update_balance: {e}")
    # Tahsilatlar trigger
    try:
        execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger WHERE tgname = 'trg_tahsilatlar_update_balance'
                ) THEN
                    CREATE TRIGGER trg_tahsilatlar_update_balance
                    AFTER INSERT OR UPDATE OR DELETE ON tahsilatlar
                    FOR EACH ROW
                    EXECUTE FUNCTION fn_update_customer_balance();
                END IF;
            END$$;
            """
        )
    except Exception as e:
        print(f"trg_tahsilatlar_update_balance: {e}")


def ensure_randevular_takip_columns():
    """Randevu Takip: baslangic/bitis, toplam_ucret, pakete_dahil_mi, durum, randevu_tipi, tekrarlayan, hatirlatma."""
    for col, sql in [
        ("baslangic_zamani", "ALTER TABLE randevular ADD COLUMN IF NOT EXISTS baslangic_zamani TIMESTAMPTZ"),
        ("bitis_zamani", "ALTER TABLE randevular ADD COLUMN IF NOT EXISTS bitis_zamani TIMESTAMPTZ"),
        ("toplam_ucret", "ALTER TABLE randevular ADD COLUMN IF NOT EXISTS toplam_ucret NUMERIC(12,2) DEFAULT 0"),
        ("pakete_dahil_mi", "ALTER TABLE randevular ADD COLUMN IF NOT EXISTS pakete_dahil_mi BOOLEAN DEFAULT FALSE"),
        ("durum", "ALTER TABLE randevular ADD COLUMN IF NOT EXISTS durum TEXT DEFAULT 'Beklemede'"),
        ("oda_adi", "ALTER TABLE randevular ADD COLUMN IF NOT EXISTS oda_adi TEXT"),
        ("randevu_tipi", "ALTER TABLE randevular ADD COLUMN IF NOT EXISTS randevu_tipi TEXT DEFAULT 'randevu'"),
        ("recurrence_rule", "ALTER TABLE randevular ADD COLUMN IF NOT EXISTS recurrence_rule TEXT"),
        ("recurrence_end_date", "ALTER TABLE randevular ADD COLUMN IF NOT EXISTS recurrence_end_date DATE"),
        ("parent_id", "ALTER TABLE randevular ADD COLUMN IF NOT EXISTS parent_id INTEGER"),
        ("reminder_sent", "ALTER TABLE randevular ADD COLUMN IF NOT EXISTS reminder_sent BOOLEAN DEFAULT FALSE"),
    ]:
        try:
            execute(sql)
        except Exception as e:
            print(f"randevular.{col}: {e}")


def ensure_cari_360_tables():
    """360° Cari Kart: randevular, iletisim_log, audit_log, cari_belgeler."""
    try:
        execute("""
            CREATE TABLE IF NOT EXISTS randevular (
                id SERIAL PRIMARY KEY,
                musteri_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                randevu_tarihi DATE NOT NULL,
                saat TIME,
                oda TEXT,
                sure_dakika INTEGER,
                ucret NUMERIC(12,2) DEFAULT 0,
                faturalandi BOOLEAN DEFAULT FALSE,
                personel_id INTEGER,
                notlar TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    except Exception as e:
        print(f"randevular: {e}")
    ensure_randevular_takip_columns()
    try:
        execute("""
            CREATE TABLE IF NOT EXISTS toplanti_odasi_fiyat (
                oda_adi TEXT PRIMARY KEY,
                saatlik_ucret NUMERIC(12,2) NOT NULL DEFAULT 0,
                aciklama TEXT
            )
        """)
    except Exception as e:
        print(f"toplanti_odasi_fiyat: {e}")
    try:
        execute("""
            CREATE TABLE IF NOT EXISTS faturalandirilacak_hizmetler (
                id SERIAL PRIMARY KEY,
                kaynak TEXT NOT NULL DEFAULT 'randevu',
                kaynak_id INTEGER NOT NULL,
                musteri_id INTEGER REFERENCES customers(id),
                aciklama TEXT,
                tutar NUMERIC(12,2) DEFAULT 0,
                islendi BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    except Exception as e:
        print(f"faturalandirilacak_hizmetler: {e}")
    try:
        execute("""
            CREATE TABLE IF NOT EXISTS iletisim_log (
                id SERIAL PRIMARY KEY,
                musteri_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                kanal TEXT NOT NULL,
                konu TEXT,
                icerik TEXT,
                personel_id INTEGER,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    except Exception as e:
        print(f"iletisim_log: {e}")
    try:
        execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                tablo_adi TEXT,
                kayit_id INTEGER,
                islem TEXT,
                eski_deger TEXT,
                yeni_deger TEXT,
                user_id INTEGER,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    except Exception as e:
        print(f"audit_log: {e}")
    try:
        execute("""
            CREATE TABLE IF NOT EXISTS cari_belgeler (
                id SERIAL PRIMARY KEY,
                musteri_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                belge_turu TEXT NOT NULL,
                dosya_adi TEXT,
                dosya_yolu TEXT,
                versiyon INTEGER DEFAULT 1,
                yukleyen_id INTEGER,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    except Exception as e:
        print(f"cari_belgeler: {e}")


def ensure_customers_kapanis_tarihi():
    """Şirket pasifken kapanış tarihi (customers.durum = pasif ile birlikte)."""
    try:
        execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS kapanis_tarihi DATE")
    except Exception as e:
        print(f"customers.kapanis_tarihi: {e}")


def ensure_customers_durum():
    """Customers tablosuna durum sütunu ekle; Excel'deki faal/terk değerlerini aktif/pasif'e çevirir.

    - Mevcut durum='faal' → 'aktif'
    - Mevcut durum='terk' → 'pasif'
    - Durumu boş olanlar için, notes içinde 'DurumExcel: faal/terk' varsa oradan doldurur.
    """
    try:
        execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS durum TEXT")
    except Exception as e:
        print(f"customers.durum: {e}")
        return
    # Eski kayıtlarda doğrudan durum alanı kullanılmışsa normalize et
    try:
        execute("UPDATE customers SET durum = 'aktif' WHERE LOWER(TRIM(COALESCE(durum,''))) = 'faal'")
    except Exception as e:
        print(f"customers.durum faal→aktif: {e}")
    try:
        execute("UPDATE customers SET durum = 'pasif' WHERE LOWER(TRIM(COALESCE(durum,''))) = 'terk'")
    except Exception as e:
        print(f"customers.durum terk→pasif: {e}")
    # Daha önce reset_ve_import_musteriler.py ile gelenler için notes içindeki 'DurumExcel:' bilgisini kullan
    try:
        execute(
            """
            UPDATE customers
            SET durum = CASE
                WHEN LOWER(notes) LIKE %s THEN 'aktif'
                WHEN LOWER(notes) LIKE %s THEN 'pasif'
                ELSE durum
            END
            WHERE (durum IS NULL OR TRIM(COALESCE(durum,'')) = '')
              AND notes IS NOT NULL
              AND LOWER(notes) LIKE %s;
            """,
            ("%durumexcel:%faal%", "%durumexcel:%terk%", "%durumexcel:%"),
        )
    except Exception as e:
        print(f"customers.durum notes→durum: {e}")


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
    """faturalar tablosunda tutar/toplam/kdv_tutar yoksa ekle (farklı şemalarda sadece biri olabilir)."""
    try:
        execute("ALTER TABLE faturalar ADD COLUMN IF NOT EXISTS tutar NUMERIC(12,2) DEFAULT 0")
    except Exception as e:
        print(f"faturalar.tutar: {e}")
    try:
        execute("ALTER TABLE faturalar ADD COLUMN IF NOT EXISTS kdv_tutar NUMERIC(12,2) DEFAULT 0")
    except Exception as e:
        print(f"faturalar.kdv_tutar: {e}")
    try:
        execute("ALTER TABLE faturalar ADD COLUMN IF NOT EXISTS toplam NUMERIC(12,2) DEFAULT 0")
    except Exception as e:
        print(f"faturalar.toplam: {e}")
    try:
        execute("ALTER TABLE faturalar ADD COLUMN IF NOT EXISTS notlar TEXT")
    except Exception as e:
        print(f"faturalar.notlar: {e}")
    try:
        execute("ALTER TABLE faturalar ADD COLUMN IF NOT EXISTS satirlar_json TEXT")
    except Exception as e:
        print(f"faturalar.satirlar_json: {e}")
    try:
        execute("ALTER TABLE faturalar ADD COLUMN IF NOT EXISTS sevk_adresi TEXT")
    except Exception as e:
        print(f"faturalar.sevk_adresi: {e}")


def clear_all_customers():
    """
    Tüm müşteri verilerini ve müşteriye bağlı kayıtları siler (geri alınamaz).
    Sıra: tahsilatlar -> faturalar -> kargolar -> musteri_kyc -> banka_hareketleri -> offices -> sozlesmeler -> customers.
    """
    with db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM tahsilatlar")
        cur.execute("DELETE FROM faturalar")
        cur.execute("DELETE FROM kargolar")
        try:
            cur.execute("DELETE FROM musteri_kyc")
        except Exception:
            pass
        try:
            cur.execute("UPDATE banka_hareketleri SET musteri_id = NULL, tahsilat_id = NULL WHERE musteri_id IS NOT NULL")
        except Exception:
            pass
        cur.execute("UPDATE offices SET customer_id = NULL WHERE customer_id IS NOT NULL")
        cur.execute("DELETE FROM sozlesmeler")
        cur.execute("DELETE FROM customers")
    return True
