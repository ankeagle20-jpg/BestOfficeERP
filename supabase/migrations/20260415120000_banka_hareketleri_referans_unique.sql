-- Mükerrer ekstre: dolu referans_no (dekont / işlem ref.) tablo genelinde benzersiz.
-- Boş veya yalnızca boşluk olan satırlar indekse dahil değildir.
-- Not: Aynı referans_no ile birden fazla mevcut satır varsa bu migration hata verir; önce mükerrerleri temizleyin.

ALTER TABLE banka_hareketleri ADD COLUMN IF NOT EXISTS referans_no TEXT;

DROP INDEX IF EXISTS banka_hareketleri_hesap_referans_uidx;

CREATE UNIQUE INDEX IF NOT EXISTS banka_hareketleri_referans_no_uidx
  ON banka_hareketleri (referans_no)
  WHERE referans_no IS NOT NULL AND btrim(referans_no) <> '';
