-- musteri_kyc: Kira artış tarihi (Giriş müşteri formu / KYC kaydet)
-- Uygulama db.ensure_musteri_kyc_columns ile de ekler; bu dosya Supabase SQL Editor veya CLI ile idempotent uygulama içindir.
ALTER TABLE musteri_kyc ADD COLUMN IF NOT EXISTS kira_artis_tarihi DATE;
