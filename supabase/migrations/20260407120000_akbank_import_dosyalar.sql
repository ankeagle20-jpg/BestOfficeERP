-- Akbank Excel: ERP içinde saklanan dosyalar (BYTEA)
CREATE TABLE IF NOT EXISTS akbank_import_dosyalar (
    id SERIAL PRIMARY KEY,
    ad_gosterim TEXT NOT NULL UNIQUE,
    orijinal_filename TEXT,
    yuklenme_tarihi TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    excel_binary BYTEA NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_akbank_import_dosyalar_yuklenme ON akbank_import_dosyalar (yuklenme_tarihi DESC);
