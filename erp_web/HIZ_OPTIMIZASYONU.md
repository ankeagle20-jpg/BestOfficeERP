# Hız Optimizasyonu — Mobil ve Genel

## Yapılan değişiklikler

1. **Gzip sıkıştırma (Flask-Compress)**  
   HTML ve JSON cevapları sıkıştırılıyor; mobilde veri azalır, sayfa daha hızlı yüklenir.

2. **CDN preconnect**  
   Layout’ta `cdnjs.cloudflare.com` için preconnect eklendi; Bootstrap ve Font Awesome daha erken yüklenir.

3. **UptimeRobot (ayrı kurulum)**  
   Servisi uyandık tutar; ilk tıklamada soğuk başlama beklemezsin.

---

## Mobilde daha hızlı hissetmek için

- **WiFi kullan** — Mobil veri yavaşsa sayfa da yavaş hisseder.
- **Tek sekme** — Aynı anda çok sekme açık olmasın.
- **Tarayıcı önbelleği** — Site verilerini silme; yeniden yükleme yavaşlatır.
- **Render ücretsiz plan** — Sunucu tek çekirdek, paylaşımlı. Ücretli planda CPU/RAM artar, sayfa daha hızlı açılır.

---

## İleride eklenebilecekler

- **Sayfa bazlı lazy loading** — Ağır listeler ilk açılışta 20–30 kayıt, “Daha fazla” ile devamı.
- **API cevaplarında kısa önbellek** — Menü vb. için 1–5 dakika Cache-Control.
- **Bootstrap yerine sadece kullanılan CSS** — Daha küçük CSS (manuel veya build ile).

Deploy sonrası mobilde test et; gzip ve preconnect farkı görebilirsin.
