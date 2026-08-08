-- A1: Bağımsız arşiv görünürlük alanları (durum aktif/pasif'e dokunmaz)
-- Mevcut satırlar DEFAULT FALSE ile görünür kalır.

ALTER TABLE customers
ADD COLUMN IF NOT EXISTS arsivli BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE customers
ADD COLUMN IF NOT EXISTS arsiv_nedeni TEXT;

ALTER TABLE customers
ADD COLUMN IF NOT EXISTS arsiv_at TIMESTAMPTZ;

ALTER TABLE customers
ADD COLUMN IF NOT EXISTS arsiv_kanonik_id INTEGER;

COMMENT ON COLUMN customers.arsivli IS 'TRUE ise liste/arama/grup varsayılanında gizli; deep-link ile erişilebilir';
COMMENT ON COLUMN customers.arsiv_nedeni IS 'Opsiyonel arşiv nedeni (mukerrer_onay vb.)';
COMMENT ON COLUMN customers.arsiv_at IS 'Arşivlenme zamanı';
COMMENT ON COLUMN customers.arsiv_kanonik_id IS 'Mükerrer arşivde kalacak kanonik customers.id';
