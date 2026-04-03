-- BestOfficeERP — Supabase SQL Editor’da bir kez çalıştırılabilir (idempotent).
-- Uygulama zaten ensure_* ile çoğu sütunu ekler; yetki/network sorununda buradan da uygulayın.

-- Fatura raporu: customers.is_active yoksa 500 hatası (UndefinedColumn)
ALTER TABLE customers ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;

-- Düzenli fatura açılır listesi (Giriş formu)
CREATE TABLE IF NOT EXISTS duzenli_fatura_secenekleri (
    id SERIAL PRIMARY KEY,
    kod TEXT NOT NULL,
    etiket TEXT NOT NULL,
    sira INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(kod)
);
INSERT INTO duzenli_fatura_secenekleri (kod, etiket, sira) VALUES
    ('duzenle', 'Düzenle', 1),
    ('fatura_aylik', 'Fatura Aylık', 2),
    ('fatura_yillik', 'Fatura Yıllık', 3)
ON CONFLICT (kod) DO NOTHING;

-- Aylık sözleşme grid önbelleği (giris_routes ile aynı şema)
CREATE TABLE IF NOT EXISTS musteri_aylik_grid_cache (
    musteri_id INTEGER PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
    payload TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Reel dönem tutarları (sözleşmeler sekmesi)
CREATE TABLE IF NOT EXISTS musteri_reel_donem_tutar (
    musteri_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    donem_yil INTEGER NOT NULL,
    tutar_kdv_dahil NUMERIC(14, 2) NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (musteri_id, donem_yil)
);
