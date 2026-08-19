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

## Kalan İş: Checkpoint 6 (henüz başlanmadı)

Bu checkpoint, GERÇEK dış hizmetlere bağlanmayı gerektiriyor — kullanıcının kendisinin
tamamlaması gereken adımlar var:

### Kullanıcının yapması gerekenler (Claude/Cursor yapamaz):
1. **Alan adı satın alma** — önerilen: Cloudflare Registrar (zaten Cloudflare hesabı
   var, R2 yedekleme için açılmıştı) veya Namecheap
2. **Stripe hesabı açma** — ücretsiz, stripe.com üzerinden
3. **Fiyatlandırma katmanlarını netleştirme** — kullanıcı "müşteri/kayıt sayısına
   göre" fiyatlandırma istedi (örn. 0-100 müşteri = X$/ay, 100-500 = Y$/ay gibi) —
   tam sayılar henüz belirlenmedi

### Kullanıcı + Claude/Cursor birlikte yapacak:
4. **DNS'i Render'a bağlama** — satın alınan alan adı için Render'da "Custom Domain"
   + wildcard subdomain (`*.alanadi.com`) ayarı. `TENANT_APEX_DOMAINS` env
   değişkeninin gerçek alan adıyla güncellenmesi gerekecek (Checkpoint 4'te
   varsayılan `bestofficeerp.com` idi — gerçek alan adı farklıysa değiştirilmeli)
5. **Kayıt formu + Stripe ödeme entegrasyonu** — yeni bir sayfa (fiyatlandırma +
   kayıt formu), Stripe Checkout/Payment Intent entegrasyonu
6. **Stripe webhook** — ödeme başarılı olduğunda otomatik olarak
   `provision_new_tenant()` çağıracak bir webhook endpoint'i
7. **`tenant_provisioning.py`'nin production/Render ortamına uyarlanması** — psql
   subprocess yerine psycopg2 üzerinden DDL çalıştırma (Windows'a bağımlılığı
   kaldırma)
8. **İkinci GERÇEK kiracı testi** — tüm akışın (kayıt → ödeme → otomatik hesap açma →
   giriş) uçtan uca gerçek bir senaryoyla test edilmesi

## Önemli Teknik Notlar (Yeni Bir Sohbette Hatırlanması Gerekenler)

- Ana branch: `main`, repo: `https://github.com/ankeagle20-jpg/BestOfficeERP.git`
- Production: `https://bestofficeerp.onrender.com` (Render, otomatik deploy `main`
  branch'inden)
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
