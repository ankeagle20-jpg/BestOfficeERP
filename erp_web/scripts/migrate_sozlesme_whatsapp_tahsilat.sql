-- sozlesme_whatsapp_gonderim: tahsilat makbuzu baglantisi.
-- Uygulama onayi olmadan calistirilmaz. Geri alinabilir.
-- Metin ve PDF bu sutunlara yazilmaz.

ALTER TABLE sozlesme_whatsapp_gonderim
  ADD COLUMN IF NOT EXISTS tahsilat_id INTEGER;

ALTER TABLE sozlesme_whatsapp_gonderim
  ADD COLUMN IF NOT EXISTS makbuz_no TEXT;

CREATE INDEX IF NOT EXISTS sozlesme_whatsapp_gonderim_tahsilat
  ON sozlesme_whatsapp_gonderim (tahsilat_id);

-- Geri alma:
-- DROP INDEX IF EXISTS sozlesme_whatsapp_gonderim_tahsilat;
-- ALTER TABLE sozlesme_whatsapp_gonderim DROP COLUMN IF EXISTS makbuz_no;
-- ALTER TABLE sozlesme_whatsapp_gonderim DROP COLUMN IF EXISTS tahsilat_id;
