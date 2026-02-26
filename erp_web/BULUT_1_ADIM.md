# Tek Adımda Buluta Aç

## Yapman gereken tek şey

**Aşağıdaki "Buluta Aç" linkine tıkla** → Açılan Render sayfasında **repo'nu seç** → **DB_HOST** ve **DB_PASSWORD** yapıştır → **Deploy**'a bas. Bitti.

---

### Buluta Aç (tek tık)

GitHub repo adresin şu formatta olsun: `https://github.com/KULLANICI_ADI/REPO_ADI`

**Link (REPO adresini kendinle değiştir):**  
**https://render.com/deploy?repo=https://github.com/KULLANICI_ADI/REPO_ADI**

Örnek: Repo adresin `https://github.com/ahmet/bestoffice-erp` ise tıklayacağın link:  
**https://render.com/deploy?repo=https://github.com/ahmet/bestoffice-erp**

---

### Açılan sayfada

1. **Connect** / GitHub ile bağla (zaten bağlıysa atla).
2. **Root Directory:** Repo kökünde `erp_web` klasörü varsa `erp_web` yaz, yoksa boş bırak.
3. **Environment Variables** → Sadece şu ikisini ekle:
   - **DB_HOST** → Supabase → Settings → Database → Host
   - **DB_PASSWORD** → Supabase → Settings → Database → Password
4. **Deploy**'a bas.

Birkaç dakika sonra ERP linki hazır; 7/24 açık kalır. İlk giriş: `admin` / `admin123`.
