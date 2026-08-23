# BestOfficeERP → Multi-Tenant SaaS Dönüşüm Yol Haritası

> **Bu dosyanın amacı:** Bu proje, çok uzun bir Claude oturumunda başladı. Yeni bir
> sohbette bu projeye devam etmek istediğinde, bu dosyayı Claude'a/Cursor'a oku ve
> "kaldığımız yerden devam edelim" de — tüm bağlam burada.

## Genel Hedef

BestOfficeERP'yi, tek şirket için çalışan bir sistemden, **her müşteri şirketin
kendi izole verisiyle kendi ERP'sini kullanabileceği** bir SaaS ürününe dönüştürmek.
İnternetten kayıt olup, kullanım miktarına (müşteri/kayıt sayısına) göre ödeme yapan
şirketler (İstanbul'dan, Amerika'dan, Avrupa'dan — uluslararası) kendi hesaplarını
açabilecek.

## Seçilen Mimari Karar

**Tek Postgres veritabanı, kiracı başına ayrı şema (schema-per-tenant)**

- Neden: Mevcut kod tabanı zaten şema öneki kullanmadan SQL yazıyor (`FROM customers`
  gibi), bu yüzden `search_path` değiştirerek doğru kiracıya yönlendirme mümkün —
  binlerce SQL sorgusunu tek tek değiştirmeye gerek yok.
- Alternatif (her kiracıya ayrı Supabase projesi) ELENDİ — ücretsiz/Pro planda proje
  sayısı sınırlı, otomasyon karmaşık/pahalı.
- Paylaşılan tablo + `tenant_id` sütunu yaklaşımı da ELENDİ — her SQL sorgusuna
  `WHERE tenant_id=` eklemek unutulursa çapraz kiracı veri sızıntısı riski çok yüksek.

## Tamamlanan Checkpoint'ler (0-5)

### ✅ Checkpoint 0 — Envanter (salt-okuma, hiçbir yazma yok)
- `pg_dump --schema-only --schema=public` ile tam şema dökümü alındı
- 66 tablo, 136 index, 51 sequence, 4 fonksiyon overload, 2 trigger bulundu
- `public.` şemasına sabitlenmiş 3 nokta (db.py içinde) bulundu — yönetilebilir
- 58 adet `ensure_*` fonksiyonu (db.py) — bunlar "gerçek kaynak" değil, pg_dump
  şeması gerçek kaynak
- Süreç-global (process-global) entegrasyonlar listelendi: GİB kimlik bilgileri,
  şirket bilgileri (FIRMA_*), Supabase Storage, WhatsApp oturumu, AI API anahtarları,
  ilan robotu bilgileri — hepsi kiracı bazlı hale getirilmesi gerekiyor
- Eski bir yedek tablo bulundu (`musteri_tahsilat_panel_detay_backup_20260617`) —
  yeni kiracılara KOPYALANMAMALI

### ✅ Checkpoint 1 — `db()` kancası (production'da no-op)
- Commit: `d4dc45a`
- `db.py` içindeki `db()` context manager'a, `g.tenant_schema` Flask değişkeni
  tanımlıysa `SET LOCAL search_path TO <şema>, pg_catalog` çalıştıran bir kanca
  eklendi
- KRİTİK GÜVENLİK: `SET LOCAL` kullanıldı (düz `SET` değil) — bağlantı havuzlama
  ile birlikte kullanıldığında bir kiracının şema bilgisinin sonraki isteğe
  sızmasını önlüyor
- `g.tenant_schema` tanımlı değilse (bugünkü TÜM production istekleri), davranış
  tamamen aynı kalıyor — TAM no-op
- Şema adı regex ile doğrulanıyor (`^tenant_[a-z0-9_]+$`), geçersizse hata fırlatıyor
  (fail-closed, sessizce public'e düşmüyor)
- Yan fayda: `auth.py` içindeki 4 fonksiyon artık havuzlanmış bağlantı kullanıyor
  (önceden çıplak `psycopg2.connect` kullanıyordu)

### ✅ Checkpoint 2 — Boş `tenant_demo` şeması + uçtan uca izolasyon testi
- Checkpoint 0'daki şema dökümü `tenant_demo` için uygulandı (eski yedek tablo hariç)
- Fonksiyonlar/trigger'lar da `tenant_demo` şemasına referans verecek şekilde
  yeniden yazılarak kopyalandı
- UÇTAN UCA TEST: demo admin kullanıcısı oluşturuldu, giriş yapıldı, müşteri eklendi,
  Grid'den fatura oluşturuldu — HEPSİ sadece `tenant_demo` şemasında gerçekleşti
- Production sayıları (customers=956, faturalar=1525, tahsilatlar=4242) hiç değişmedi

### ✅ Checkpoint 3 — Cache + arka plan işleri kiracı bazlı
- Commit: `629fae6`
- Bellek içi cache'ler (`_aylik_grid_payload_mem`, ekstre cache) artık
  `(tenant_schema_veya_None, musteri_id)` şeklinde anahtarlanıyor
- `_defer_aylik_grid_cache_rebuild()` (arka plan thread) artık thread başlamadan
  önce `g.tenant_schema` değerini yakalayıp, yeni thread context'inde yeniden
  uyguluyor — Flask'ın `g` nesnesi thread-local olduğu için bu olmadan kiracı
  bilgisi kaybolurdu
- Production regresyon testleri (mid=53/128/265/100934/100946) SHA seviyesinde
  birebir aynı kaldı

### ✅ Checkpoint 4 — Host/subdomain kimlik sistemi + oturum güvenlik kilidi
- Commit: `1763ec3`
- YENİ dosya: `erp_web/tenant_identity.py`
- Host başlığından subdomain çıkarıp `g.tenant_schema`'ya çeviren mekanizma
- **KRİTİK GÜVENLİK AÇIĞI bulunup düzeltildi:** İlk versiyon, "3+ nokta ile
  ayrılmış her host'un ilk parçasını subdomain sayardı" — bu, GERÇEK production
  adresi olan `bestofficeerp.onrender.com`'u (tam 3 parça) yanlışlıkla
  `tenant_bestofficeerp` şemasına (var olmayan) yönlendirebilirdi, TÜM production'ı
  bozabilirdi
- DÜZELTME (fail-closed): Artık SADECE açıkça tanımlanmış `TENANT_APEX_DOMAINS`
  (env değişkeni, varsayılan: `bestofficeerp.com,bestofficeerp.local`) alan
  adlarının alt alan adları kiracı sayılıyor. `NO_TENANT_HOST_SUFFIXES`
  (varsayılan: `onrender.com`) ve `PUBLIC_HOSTS`
  (varsayılan: `bestofficeerp.onrender.com`) her zaman public kalıyor
- Oturum güvenlik kilidi: login sırasında session'a `tenant_slug` yazılıyor, her
  istekte Host ile karşılaştırılıyor, uyuşmazlıkta 403 + oturum temizleme

### ✅ Checkpoint 5 — Yeniden çağrılabilir kiracı provisioning fonksiyonu
- Commit: `bbf2faf`
- YENİ tablo: `public.tenants` (platform kataloğu: slug, schema_name, plan, status,
  created_at) — veritabanı seviyesinde CHECK constraint'leri var
- YENİ dosya: `erp_web/tenant_provisioning.py` — `provision_new_tenant(slug, ...)`
  fonksiyonu
- Checkpoint 2'de elle yapılan işlem artık tekrar tekrar güvenle çağrılabilir
- Rezerve slug kontrolü, DDL güvenlik kontrolleri (yedek tablo/platform tablosu
  DDL'e sızmasın, information_schema/CREATE EXTENSION reddedilsin), aynı slug ile
  ikinci provizyon engelleniyor (fail-closed)
- Test: `provision_new_tenant("test2")` başarıyla ikinci, bağımsız bir test kiracısı
  oluşturdu — mekanizmanın gerçekten tekrarlanabilir olduğu kanıtlandı
- **BİLİNEN SINIRLAMA:** `psql` yolu şu an Windows'a özel hardcode edilmiş
  (`C:\Program Files\PostgreSQL\17\bin\psql.exe`) — Render/production ortamında
  self-servis kayıt akışı için bu yolun psycopg2 üzerinden çalışacak şekilde
  uyarlanması gerekecek (Checkpoint 6'nın bir parçası)

## ✅ Checkpoint 6 (KISMEN tamamlandı) — Gerçek domain + DNS + uçtan
uca production testi

- Alan adı satın alındı: payafin.com (İhs.com.tr)
- Render'da özel alan adı + wildcard (*.payafin.com) EKLENDİ, DNS
  yapılandırıldı, SSL sertifikası (Let's Encrypt, TLSv1.3) OTOMATIK
  çıkarıldı
- PUBLIC_APP_URL VE TENANT_APEX_DOMAINS ortam DEĞİŞKENLERİ Render'DA
  GÜNCELLENDİ (payafin.com)
- GERÇEK production'DA uçtan uca TEST BAŞARILI: provision_new_tenant("test")
  İLE tenant_test OLUŞTURULDU, https://test.payafin.com ÜZERİNDEN
  GİRİŞ yapıldı, TEST müşterisi EKLENDİ - SADECE tenant_test şemasına
  YAZILDI, public/tenant_demo/tenant_test2 HİÇ ETKİLENMEDİ
- REGRESYON doğrulandı: payafin.com (KÖK) VE bestofficeerp.onrender.com
  (ESKİ adres), HÂLÂ 'public' (ANA şirket) olarak DAVRANIYOR - kiracı
  kullanıcısı BURADA giriş YAPAMIYOR
- www.payafin.com → payafin.com YÖNLENDİRMESİ doğrulandı, güvenlik
  sorunu YOK
- payafin.com KÖK adresi artık Payafin markalı ana sayfayı gösteriyor
  (dünkü OFİSBİR karışıklığı çözüldü — ayrıntı: Payafin Ana Sayfa
  P0-P2 bölümü)

## ✅ Payafin Ana Sayfa (P0-P2, TAMAMEN tamamlandı)

- payafin.com KÖK adresi, ARTIK kendi Payafin markalı ana SAYFASINI
  gösteriyor (bestofficeerp.onrender.com HİÇ etkilenmedi)
- E-posta İLE "hangi kiracıya AİTSİNİZ?" arama VE otomatik yönlendirme
- Commit'ler: c20212e (arama İNDEKSİ), 7ba7703 (login-lookup API),
  6c3f422 (marketing ANA sayfa)

## ✅ Payafin Fiyatlandırma Motoru (P0-P3, TAMAMEN tamamlandı)

Checkpoint 6'nın TİCARİ katmanının İLK parçası - HİBRİT (hacim BAZLI
kademe + kullanıcı BAZLI ek ücret), ÜLKE bazlı DİNAMİK fiyatlandırma
sistemi.

- P0 (commit `0a15b72`): `public.pricing_regions` / `pricing_tiers` /
  `pricing_overage_rules` TABLOLARI + TÜRKİYE İÇİN 5 kademelik seed
  VERİSİ (Başlangıç / Profesyonel / Büyüme / İleri Düzey / Kurumsal,
  2000+ İÇİN otomatik PAKET aşım kuralı)
- P1 (commit `47c872a`): `pricing_engine.py` → `calculate_tenant_bill()`
  — Decimal HASSASİYETLİ, matematiksel DOĞRULANMIŞ hesaplama motoru
- P2 (commit `4a94df2`): `/admin/pricing` YÖNETİM paneli — fiyatları kod
  DEĞİŞTİRMEDEN, CANLI olarak düzenleyebilme, PLATFORM-only host
  KORUMASI (kiracı subdomain'İNDEN erişilemez)
- P3 (commit `e703219`): `GET /api/pricing/public` — herkese AÇIK, kısa
  cache'Lİ, salt-okuma API — fiyatlandırma SAYFASI ve kayıt formu BUNU
  kullanıyor

## ✅ Payafin Kayıt Formu (B0-B3, TAMAMEN tamamlandı - UÇTAN UCA
ÇALIŞIYOR)

Payafin ARTIK GERÇEK bir SaaS ürünü - HERKES internetten https://payafin.com/signup
adresine GİDİP, kendi izole ERP hesabını OLUŞTURABİLİYOR.

- B0 (commit `234c699`): public.tenants metadata GENİŞLETMESİ
  (company_name, country_code) + birleşik rezerve SLUG listesi
- B1 (commit `0aa41c5`): Backend signup API'leri - GET /api/signup/slug-available,
  POST /api/signup (ASENKRON provisioning, Render timeout RİSKİNE
  karşı), GET /api/signup/status (poll) - IP bazlı hız SINIRLAMASI,
  honeypot, e-posta/şifre doğrulaması
- B2a (commit `07e656f`): Provizyon SERTLEŞTİRME - güvenli hata
  mesajları (İÇ detay ASLA sızmaz), enumeration KORUMASI, retry-FROM-failed
  BİLİNÇLİ olarak DESTEKLENMİYOR (kötüye kullanım YÜZEYİ), süre
  loglama
- B3 (commit `cb29aa3`): Frontend /signup SAYFASI - TAM uçtan uca akış:
  form → CANLI slug KONTROLÜ → submit → "hazırlanıyor" ekranı → POLLING
  → BAŞARI (otomatik yönlendirme) VEYA hata mesajı

UÇTAN UCA DOĞRULANDI (Selenium, GERÇEK tarayıcı): form DOLDURULDU,
kayıt OLUŞTU, YENİ kiracının subdomain'İNDE (https://<slug>.payafin.com/login)
GİRİŞ YAPILDI - TAMAMEN otomatik, hiçbir manuel MÜDAHALE olmadan.

## ✅ Şifremi Unuttum + Oturum Güvenliği (R0-R3, ÇEKİRDEK tamamlandı)

- Gerçek Gmail SMTP altyapısı KURULDU VE doğrulandı (payafin.destek@gmail.com)
- HER kiracı şemasında (VE gelecekteki TÜM yeni kiracılarda) password_reset_tokens
  VE users.security_stamp altyapısı
- ARTIK: kullanıcı şifresini SIFIRLADIĞINDA, TÜM cihazlardaki eski
  oturumlar (BENİ hatırla çerezi DAHİL) ANINDA geçersiz KILINIYOR
- KRİTİK iki bulgu bu OTURUMDA yakalanıp DÜZELTİLDİ: (1) base64
  stamp'lerin cookie-safe OLMAMASI, (2) transaction İÇİNDE 'return
  False'un commit'İ ENGELLEMEMESİ (GERÇEK eşzamanlı YARIŞ testiyle
  doğrulandı)
- Commit'ler: 62f48c9 (R1 tablo), 8f31631 (R2 forgot-password),
  34e88d8 (R2.5 stamp altyapısı), 39db536 (R2.6 login BAĞLAMA),
  6413434 (R3 reset-password)

### Kalan iş (düşük risk, İSTEĞE bağlı):
1. Login sayfasına 'Şifremi UNUTTUM' linki (R4)
2. Profil şifre değişiminde de stamp ROTATE (R5)
3. Strict mod geçişi (R2.6-S4) - ESKİ, deploy ÖNCESİ oturumların
   tek seferlik yeniden GİRİŞ yapması GEREKECEK bir bakım penceresi

### Kalan iş (Checkpoint 6'nın SON parçası — ticari/iş katmanı):
1. OAuth/sosyal giriş (Google, Facebook, Instagram, Apple) — kullanıcı
   BİLİNÇLİ olarak SONRAYA BIRAKTI (her platform AYRI geliştirici
   HESABI/onay süreci GEREKTİRİYOR)
2. iyzico (VEYA benzeri Türk ödeme SAĞLAYICISI) başvurusu/entegrasyonu
   — kullanıcı BAŞVURUYU yaptı, CEVAP bekleniyor (Stripe TÜRKİYE'DE
   doğrudan DESTEKLENMİYOR)
3. Ödeme SONRASI, trial'DAN ücretli plana GEÇİŞ akışı
4. Fiyatlandırma landing SAYFASI (payafin.com/fiyatlandirma — dünkü
   `/api/pricing/public`'İ TÜKETEN, GÖRSEL bir sayfa)
5. E-posta doğrulaması (ŞU AN yok — kayıt OLAN kişinin e-postası
   DOĞRULANMIYOR)
6. GERÇEK bir ÖDEME yapan MÜŞTERİYLE tam uçtan uca TEST (kayıt →
   ödeme → ücretli plana geçiş → giriş)

## Önemli Teknik Notlar (Yeni Bir Sohbette Hatırlanması Gerekenler)

- Ana branch: `main`, repo: `https://github.com/ankeagle20-jpg/BestOfficeERP.git`
- Production: `https://bestofficeerp.onrender.com` (Render, otomatik deploy `main`
  branch'inden)
- Alan adı: payafin.com (aktif, DNS+SSL çalışıyor)
- Veritabanı: Supabase (free tier), ~27 MB, PostgreSQL 17.6
- Günlük otomatik yedekleme zaten kurulu: GitHub Actions → Cloudflare R2 (her gece
  00:00 UTC / 03:00 TR saati) — bu, multi-tenant projesinden BAĞIMSIZ, zaten çalışıyor
- `public` şema = mevcut/orijinal şirketin (BestOffice'in kendisi) verisi — bu HİÇ
  taşınmayacak, kendi kiracınız gibi kalacak
- Referans regresyon müşterileri (her checkpoint'te SHA karşılaştırması için
  kullanılıyor): mid=53, 128, 265, 100934, 100946
- Test kiracıları mevcut: `tenant_demo` (Checkpoint 2), `tenant_test2` (Checkpoint 5)
  — bunlar gerçek müşteri değil, test amaçlı, silinebilir

## Nasıl Devam Edilir (Yeni Sohbette)

1. Bu dosyayı (`MULTITENANCY_ROADMAP.md`) Claude'a oku dedirt
2. "Checkpoint 6'ya devam edelim" de
3. Eğer alan adı + Stripe hesabı hazırsa, bu bilgileri paylaş (alan adı ismi,
   Stripe hesabının hazır olduğu)
4. Claude, bugüne kadarki AYNI disiplinle (yedek al → küçük adım → test → onay →
   commit) devam edecek
