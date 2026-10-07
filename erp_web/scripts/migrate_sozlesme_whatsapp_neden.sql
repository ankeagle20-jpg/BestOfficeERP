-- sozlesme_whatsapp_gonderim: gonderim hata kodu.
-- Yalniz public. Uygulama onayi olmadan calistirilmaz.
-- Metin, numara ve PDF bu sutuna yazilmaz.

ALTER TABLE public.sozlesme_whatsapp_gonderim
  ADD COLUMN IF NOT EXISTS neden TEXT;

-- Geri alma:
-- ALTER TABLE public.sozlesme_whatsapp_gonderim DROP COLUMN IF EXISTS neden;
