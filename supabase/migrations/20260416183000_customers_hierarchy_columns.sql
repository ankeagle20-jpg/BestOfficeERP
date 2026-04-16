-- Konsolide Cari (Hiyerarşik Yapı) - incremental alanlar
ALTER TABLE customers
ADD COLUMN IF NOT EXISTS parent_id UUID;

ALTER TABLE customers
ADD COLUMN IF NOT EXISTS is_group BOOLEAN DEFAULT FALSE;
