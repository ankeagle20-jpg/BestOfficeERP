# Bulut Linki — BestOffice ERP

## Ana link (Render)

**https://bestofficeerp.onrender.com**

Bu linki:
- İstediğin **her PC’den** açabilirsin (ev, ofis, başka bilgisayar)
- **Telefon veya tabletten** açabilirsin
- **Sadece internet** yeterli; kendi bilgisayarının açık olması **gerekmez**

---

## ERP tamamen bulutta mı çalışıyor?

**Evet.** Uygulama Render sunucularında çalışıyor. Yani:

- Bu bilgisayar **kapalı** olsa da ERP’ye bu linkten erişirsin
- Sistemi kullanmak için **sadece bu linki** açman yeterli
- Yerel kurulum (BestOffice_Baslat.bat, 127.0.0.1) sadece **yerel/test** içindir; günlük kullanım için bulut linkini kullan

---

## Link IP adresi gibi görünmesin

Render varsayılan olarak **isim.onrender.com** verir (IP değil). Projede servis adı **bestofficeerp** olarak ayarlı; yani link:

**https://bestofficeerp.onrender.com**

Eğer Render’da servis adı farklıysa (ör. `bestoffice-erp`), link `https://bestoffice-erp.onrender.com` olur.  
Render Dashboard → **Settings** → **Service name** kısmından adı **bestofficeerp** yaparsan link tam olarak `https://bestofficeerp.onrender.com` olur.

---

## Kendi alan adın (isteğe bağlı)

**bestofficeerp.com** gibi kendi domain’ini kullanmak istersen:

1. Render Dashboard → Servisini seç → **Settings** → **Custom Domain**
2. **bestofficeerp.com** (veya istediğin alan adı) ekle
3. Domain satın aldığın yerde (GoDaddy, Cloudflare vb.) DNS’e Render’ın verdiği CNAME kaydını ekle

Bunu yapmazsan da **https://bestofficeerp.onrender.com** linki her yerden çalışır.

---

## Siyah ekran / "APPLICATION LOADING" geliyor

Render **ücretsiz planda** servisi ~15 dakika kullanılmadığında **uyutur**. Linke tıkladığında:

1. **Siyah ekranda** "SERVICE WAKING UP", "YOUR APP IS ALMOST LIVE" yazıları çıkar.
2. **1–2 dakika bekle** (veya sayfayı 1–2 dakika sonra **yenile**). Uygulama açılır, giriş sayfası gelir.
3. Sürekli siyah kalıyorsa: **Render Dashboard** → servisini seç → **Logs** sekmesine bak. Orada Python/Flask hatası (DB bağlantısı, env değişkeni vb.) varsa onu düzelt.

**Uygulama hep uyanık kalsın istersen (isteğe bağlı):**  
[UptimeRobot](https://uptimerobot.com) (ücretsiz) ile **https://bestofficeerp.onrender.com** adresini her 5–10 dakikada bir ping’leyebilirsin. Böylece servis uyumaz, siyah ekran çok daha az görülür.

---

## Hızlı erişim

- **Masaüstü:** `scripts\ac_bulut.bat` çift tıkla → Bulut linki açılır  
- **Tarayıcı sık kullanılanlar:** https://bestofficeerp.onrender.com adresini ekle  
- **Mobil:** Aynı linki telefon tarayıcısında sık kullanılanlara ekle

İlk giriş: **admin** / **admin123** (şifreyi ilk girişte değiştir).
