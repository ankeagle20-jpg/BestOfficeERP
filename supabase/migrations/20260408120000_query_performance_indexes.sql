-- Sık kullanılan sorgular için indeksler (Render / Supabase pooler ile daha hızlı plan)
-- IF NOT EXISTS: tekrar çalıştırılabilir.
-- Not: musteri_kyc (musteri_id, id DESC) için uygulama ensure_musteri_kyc_latest_lookup_index()
--       zaten idx_musteri_kyc_musteri_id_id_desc ekliyor.
-- Not: COALESCE(fatura_tarihi::date, …) ifadesi indekste timestamptz nedeniyle IMMUTABLE olmayabilir;
--       bu yüzden fatura_tarihi ve vade_tarihi ayrı btree kullanılır (BitmapOr ile birleşebilir).

CREATE INDEX IF NOT EXISTS ix_faturalar_musteri_id
    ON faturalar (musteri_id);

CREATE INDEX IF NOT EXISTS ix_faturalar_fatura_tarihi_id
    ON faturalar (fatura_tarihi NULLS LAST, id);

CREATE INDEX IF NOT EXISTS ix_faturalar_vade_tarihi_id
    ON faturalar (vade_tarihi NULLS LAST, id);

CREATE INDEX IF NOT EXISTS ix_faturalar_odememis_musteri_vade
    ON faturalar (musteri_id, vade_tarihi)
    WHERE COALESCE(durum, '') <> 'odendi' AND vade_tarihi IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_faturalar_musteri_ftarih_ettn
    ON faturalar (musteri_id, fatura_tarihi NULLS LAST, id)
    WHERE ettn IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_tahsilatlar_musteri_tarih
    ON tahsilatlar (musteri_id, tahsilat_tarihi DESC NULLS LAST, id);

CREATE INDEX IF NOT EXISTS ix_tahsilatlar_customer_tarih
    ON tahsilatlar (customer_id, tahsilat_tarihi DESC NULLS LAST, id)
    WHERE customer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_tahsilatlar_fatura_id
    ON tahsilatlar (fatura_id)
    WHERE fatura_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_tahsilatlar_banka_referans_no
    ON tahsilatlar (banka_referans_no)
    WHERE banka_referans_no IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_tahsilatlar_tahsilat_tarihi
    ON tahsilatlar (tahsilat_tarihi DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS ix_kargolar_musteri_created
    ON kargolar (musteri_id, created_at DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS ix_offices_customer_id
    ON offices (customer_id)
    WHERE customer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_customers_name
    ON customers (name);
