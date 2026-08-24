# Payafin / BestOfficeERP Modül Sistemi Yol Haritası

> **Bu dosyanın amacı:** Bu proje, uzun bir planlama oturumunda şekillendi. Yeni bir
> sohbette bu konuya geri dönmek istediğinde, bu dosyayı Claude'a/Cursor'a oku ve
> "modül sistemi planına kaldığımız yerden devam edelim" de — bugüne kadar alınan
> mimari kararlar, riskler, rollout yaklaşımı ve ürünleme önerileri burada toplandı.

> **NOT:** Bu proje HENÜZ BAŞLAMADI (SADECE planlama AŞAMASINDA) - Sprint 1'E
> başlamak İÇİN, kullanıcının ONAYI GEREKİYOR.

## Genel Hedef

Payafin / BestOfficeERP içinde yer alan belirli iş alanlarını, hem:

1. mevcut ERP müşterilerine **ek paket / add-on** olarak,
2. hem de ERP istemeyen müşterilere **tamamen bağımsız ürün** olarak

satılabilecek hale getirmek.

İlk odak modüller:

- `randevu`
- `personnel`
- `attendance`

Ana mimari yaklaşım:

- kısa/orta vadede **aynı tenant provisioning modeli** korunacak
- yani tenant başına **tam şema** kurulmaya devam edecek
- satış/erişim farklılaşması **platform seviyesinde entitlement** ile çözülecek
- daha küçük modül-özel DDL şablonları, ancak ileride ürün-pazar uyumu kanıtlanırsa
  değerlendirilecek

## Seçilen Stratejik Mimari Karar

**İlk aşamada: full-schema provisioning + platform-level module entitlement**

Bu kararın nedeni:

- mevcut kod tabanı çok sayıda tabloyu “var kabul ederek” çalışıyor
- özellikle `randevu`, bugün hâlâ `customers` ve müşteri arama mantığına güçlü bağlı
- `personnel` daha bağımsız olsa da kendi içinde `attendance + izin + QR + otomasyon`
  kümelenmesi barındırıyor
- provisioning’i erken parçalamak, entitlement çözmeden önce ikinci bir platform
  inşa etmek anlamına gelir

Bu yüzden ilk çözülmesi gereken katman:

- **hangi tenant hangi modüle sahip?**

İkinci katman:

- **modül açık değilse route/UI nasıl davranacak?**

Üçüncü katman:

- **modül satış modeli (core/add-on/standalone) nasıl fiyatlanacak?**

## İncelenen Mevcut Modüller

Bu belge kapsamında detaylı analiz edilen iki ana alan:

- `randevu`
- `personel` + `pdovam` (`attendance`)

---

## 1. Randevu — Rota Bazlı Modül Haritası

Ana dosya:

- `erp_web/routes/randevu_routes.py`

Blueprint prefix:

- `/randevu`

### Genel Değerlendirme

`randevu` modülü bugün şu alt katmanlardan oluşuyor:

1. yönetim paneli / takvim ekranları
2. public self-service booking akışı
3. oda/fiyat mantığı
4. reminder / mail / webhook akışları
5. ERP tarafında müşteri ve faturalandırma entegrasyonları

### Ana Bağımlılık Düzeyleri

- `randevular`: çok yüksek
- `customers`: çok yüksek
- `musteri_kyc` / müşteri arama altyapısı: orta-yüksek
- `toplanti_odasi_fiyat`: yüksek
- `faturalandirilacak_hizmetler`: orta-yüksek
- `mail_utils`: orta
- `auth/users`: yönetim ekranlarında yüksek

### Route Haritası

#### `GET /randevu/`

- Amaç: Randevu ana ekranı
- Tablolar: doğrudan SQL yok
- Bağımlılıklar:
  - `randevu/index.html`
  - `login_required`
- Bağımlılık seviyesi:
  - ERP shell: yüksek
  - veri modeli: düşük

#### `GET /randevu/calcom`

- Amaç: Cal.com embed ekranı
- Tablolar: yok
- Bağımlılıklar:
  - `randevu/calcom.html`
  - `CAL_COM_CAL_LINK`
  - auth
- Bağımlılık seviyesi:
  - dış servis: orta
  - ERP veri modeli: düşük

#### `GET /randevu/rapor`

- Amaç: rapor ekranı
- Tablolar: yok
- Bağımlılıklar:
  - `randevu/rapor.html`
  - auth

#### `GET /randevu/panel`

- Amaç: birleşik panel
- Tablolar: yok
- Bağımlılıklar:
  - `randevu/panel.html`
  - auth

#### `GET /randevu/api/list`

- Amaç: tarih aralığında takvim listesi
- Tablolar:
  - `randevular`
  - `customers`
- Bağımlılıklar:
  - müşteri adı / telefon
  - auth
- Bağımlılık seviyesi:
  - `randevular`: çok yüksek
  - `customers`: yüksek

#### `GET /randevu/api/rapor`

- Amaç: toplantı / görüşme raporu
- Tablolar:
  - `randevular`
  - `customers`
- Bağımlılık seviyesi:
  - `randevular`: çok yüksek
  - `customers`: orta-yüksek

#### `GET /randevu/api/musteriler`

- Amaç: müşteri arama / autocomplete
- Tablolar:
  - `customers`
  - dolaylı olarak `musteri_kyc`
- Bağımlılıklar:
  - `utils.musteri_arama.py`
  - auth
- Bağımlılık seviyesi:
  - `customers`: çok yüksek
  - `musteri_kyc`: orta-yüksek

#### `GET /randevu/randevu-al`

- Amaç: ERP içi randevu alma ekranı
- Tablolar: doğrudan yok
- Bağımlılıklar:
  - `randevu/randevu_al.html`
  - `embed=1&musteri_id=...`
  - auth
- Bağımlılık seviyesi:
  - ERP müşteri bağlamı: çok yüksek

#### `GET /randevu/api/musait-slotlar`

- Amaç: uygun slot döndürme
- Tablolar:
  - `randevular`
- Bağımlılık seviyesi:
  - `randevular`: çok yüksek
- Standalone uygunluğu:
  - yüksek

#### `GET /randevu/api/gun-randevulari`

- Amaç: gün içi tüm randevuları listeleme
- Tablolar:
  - `randevular`
  - `customers`
- Bağımlılık seviyesi:
  - `randevular`: çok yüksek
  - `customers`: yüksek

#### `GET /randevu/api/aylik-doluluk`

- Amaç: aylık doluluk raporu
- Tablolar:
  - `randevular`
- Bağımlılık seviyesi:
  - `randevular`: çok yüksek
- Standalone uygunluğu:
  - çok yüksek

#### `GET /randevu/api/odalar`

- Amaç: oda listesi + saatlik ücret
- Tablolar:
  - `toplanti_odasi_fiyat`
- Bağımlılık seviyesi:
  - `toplanti_odasi_fiyat`: çok yüksek

#### `GET /randevu/book`

- Amaç: public booking sayfası
- Tablolar: yok
- Bağımlılıklar:
  - `randevu/book.html`
- Standalone uygunluğu:
  - teoride yüksek, pratikte template ve veri bağımlılığı nedeniyle orta

#### `GET /randevu/api/public/odalar`

- Amaç: public oda listesi
- Tablolar:
  - `toplanti_odasi_fiyat`
- Bağımlılık seviyesi:
  - yüksek

#### `GET /randevu/api/public/slotlar`

- Amaç: public uygun slotlar
- Tablolar:
  - `randevular`
- Bağımlılık seviyesi:
  - çok yüksek

#### `POST /randevu/api/public/ekle`

- Amaç: public self-service randevu oluşturma
- Tablolar:
  - `customers`
  - `toplanti_odasi_fiyat`
  - `randevular`
- Bağımlılıklar:
  - `mail_utils.send_randevu_onay`
  - çakışma kontrolü
- Bağımlılık seviyesi:
  - `customers`: çok yüksek
  - `randevular`: çok yüksek
  - `toplanti_odasi_fiyat`: yüksek

#### `GET /randevu/iptal/<rid>`

- Amaç: public iptal ekranı
- Tablolar:
  - `randevular`
  - `customers`
- Bağımlılıklar:
  - `randevu/iptal.html`

#### `POST /randevu/api/public/iptal/<rid>`

- Amaç: public iptal işlemi
- Tablolar:
  - `randevular`
  - `customers`
- Bağımlılıklar:
  - iptal maili

#### `POST /randevu/api/odalar/guncelle`

- Amaç: oda saatlik ücret güncelleme
- Tablolar:
  - `toplanti_odasi_fiyat`
- Bağımlılıklar:
  - auth

#### `POST /randevu/api/ekle`

- Amaç: yönetim panelinden randevu / görüşme oluşturma
- Tablolar:
  - `customers`
  - `toplanti_odasi_fiyat`
  - `randevular`
- Bağımlılıklar:
  - görüşme kişisi için customer açma
  - recurrence
  - mail
  - webhook
- Bağımlılık seviyesi:
  - `customers`: çok yüksek
  - `randevular`: çok yüksek
  - `toplanti_odasi_fiyat`: yüksek
  - mail/webhook: orta

#### `GET /randevu/api/mevcut-randevu`

- Amaç: müşteri + gün + oda için mevcut randevuyu bulma
- Tablolar:
  - `randevular`
- Kavramsal bağımlılık:
  - müşteri bağlamı

#### `POST /randevu/api/saat-guncelle/<rid>`

- Amaç: saat taşıma / yeniden zamanlama
- Tablolar:
  - `randevular`
  - `toplanti_odasi_fiyat`
- Bağımlılık seviyesi:
  - çok yüksek

#### `POST /randevu/api/sil/<rid>`

- Amaç: kaydı fiziksel silme
- Tablolar:
  - `randevular`
  - `customers`
- Bağımlılıklar:
  - iptal maili
  - webhook

#### `GET /randevu/cron/hatirlatma`

- Amaç: ertesi gün reminder maili
- Tablolar:
  - `randevular`
  - `customers`
- Bağımlılıklar:
  - `mail_utils.send_randevu_hatirlatma`

#### `POST /randevu/api/guncelle/<rid>`

- Amaç: durum güncelleme; `Tamamlandı` ise faturalandırma kuyruğuna ekleme
- Tablolar:
  - `randevular`
  - `faturalandirilacak_hizmetler`
- Bağımlılık seviyesi:
  - `randevular`: çok yüksek
  - ERP faturalama kuyruğu: orta-yüksek

### Randevu için Sonuç

`randevu` bugün:

- çekirdek booking motoru olarak modülerleşmeye uygun
- ama veri modeli olarak hâlâ `customers` bağımlı
- bu yüzden ilk standalone sürümde **tam şema + entitlement** yaklaşımı önerilir

---

## 2. Personel / Attendance — Rota Bazlı Modül Haritası

Ana dosyalar:

- `erp_web/routes/personel_routes.py`
- `erp_web/routes/pdovam_routes.py`

Kavramsal ayrım:

- `personnel`: personel kartı / özlük / izin / yetki
- `attendance`: giriş-çıkış / QR / canlı durum / günlük / aylık devam

### Ana Bağımlılık Düzeyleri

- `personel`: çok yüksek
- `personel_bilgi`: yüksek
- `personel_ozluk`: yüksek
- `personel_izin`: çok yüksek
- `personel_yetki`: orta-yüksek
- `devam_kayitlari`: çok yüksek
- `personel_hareketleri`: çok yüksek
- auth/users: yüksek
- Supabase `personel_devam`: orta

### A. `personel_routes.py`

#### `GET /personel/`

- Amaç: personel ana ekranı
- Tablolar: yok
- Bağımlılıklar:
  - `personel/index.html`
  - auth

#### `GET /personel/api/list`

- Amaç: personel listesi
- Tablolar:
  - `personel`

#### `POST /personel/api/personel/kaydet`

- Amaç: personel create/update
- Tablolar:
  - `personel`

#### `POST /personel/api/personel/ad`

- Amaç: ad güncelleme
- Tablolar:
  - `personel`

#### `POST /personel/api/personel/sil`

- Amaç: personeli ve bağlı kayıtlarını silme
- Tablolar:
  - `personel`
  - `personel_izin`
  - `personel_bilgi`
  - `personel_yetki`
  - `devam_kayitlari`

#### `GET /personel/api/personel/bilgi`

- Amaç: temel personel bilgi / izin hakkı verisi
- Tablolar:
  - `personel_bilgi`

#### `POST /personel/api/personel/bilgi`

- Amaç: personel bilgi kaydetme
- Tablolar:
  - `personel_bilgi`

#### `GET /personel/api/personel/ozluk`

- Amaç: özlük verisi
- Tablolar:
  - `personel_ozluk`
- Bağımlılıklar:
  - admin yetkisi

#### `POST /personel/api/personel/ozluk`

- Amaç: özlük create/update
- Tablolar:
  - `personel_ozluk`
- Bağımlılıklar:
  - admin yetkisi

#### `POST /personel/api/personel/ozluk/sil`

- Amaç: özlük silme
- Tablolar:
  - `personel_ozluk`

#### `GET /personel/api/devam/gunluk`

- Amaç: günlük attendance
- Tablolar:
  - `devam_kayitlari`
  - `personel`

#### `GET /personel/api/devam/aylik`

- Amaç: aylık attendance özeti
- Tablolar:
  - `personel`
  - `devam_kayitlari`

#### `POST /personel/api/devam/kaydet`

- Amaç: manuel attendance kayıt/güncelleme
- Tablolar:
  - `personel`
  - `devam_kayitlari`
- Dolaylı:
  - bulut sync

#### `GET /personel/api/izin/list`

- Amaç: izin listesi
- Tablolar:
  - `personel_izin`

#### `GET /personel/api/izin/qr-bekleyen`

- Amaç: QR kaynaklı bekleyen izinler
- Tablolar:
  - `personel_izin`
- Bağımlılık:
  - attendance/QR

#### `POST /personel/api/izin/qr-onayla`

- Amaç: QR bekleyen izinleri onaylama / birleştirme
- Tablolar:
  - `personel_izin`

#### `POST /personel/api/izin/qr-geri-al`

- Amaç: QR bekleyen izni geri alma
- Tablolar:
  - `personel_izin`

#### `GET /personel/api/izin/ozet`

- Amaç: kişi bazlı izin özeti
- Tablolar:
  - `personel_ozluk`
  - `personel_bilgi`
  - `personel`
  - `personel_izin`
  - `devam_kayitlari`
- Not:
  - attendance verisi izin bakiyesine etki ediyor

#### `GET /personel/api/izin/ozet/liste`

- Amaç: çoklu personel izin özeti
- Tablolar:
  - `personel`
  - `personel_bilgi`
  - `personel_ozluk`
  - `personel_izin`
- Dolaylı:
  - `pdovam_toplam_fark_dk_for_personel()`

#### `POST /personel/api/izin/kaydet`

- Amaç: izin ekleme
- Tablolar:
  - `personel_izin`

#### `POST /personel/api/izin/sil`

- Amaç: izin silme
- Tablolar:
  - `personel_izin`

#### `POST /personel/api/izin/guncelle`

- Amaç: izin güncelleme
- Tablolar:
  - `personel_izin`

#### `POST /personel/api/izin/otomatik-hesapla`

- Amaç: otomatik izin hesabı tetikleme
- Doğrudan route içinde tablo yok
- Servis bağımlılığı:
  - `services.izin_otomatik`
- Dolaylı tablolar:
  - `personel_hareketleri`
  - `personel_izin`

#### `GET /personel/api/izin-pdf/<izin_id>`

- Amaç: izin PDF üretme
- Tablolar:
  - `personel_izin`
  - `personel`
  - `personel_bilgi`
  - `personel_ozluk`
- Dolaylı:
  - `devam_kayitlari`

#### `GET /personel/api/gec/list`

- Amaç: geç kalma listesi
- Tablolar:
  - `devam_kayitlari`
  - `personel`

#### `POST /personel/api/gec/guncelle`

- Amaç: geç dakikayı düzenleme
- Tablolar:
  - `devam_kayitlari`

#### `POST /personel/api/gec/sil`

- Amaç: geç kaydını silme
- Tablolar:
  - `devam_kayitlari`

#### `GET /personel/api/gec/aylik-grid`

- Amaç: aylık geç kalma grid’i
- Tablolar:
  - `personel_bilgi`
  - `personel`
  - `devam_kayitlari`

#### `GET /personel/api/yetki`

- Amaç: personel yetki listesi
- Tablolar:
  - `personel_yetki`

#### `POST /personel/api/yetki`

- Amaç: personel yetkisi kaydetme
- Tablolar:
  - `personel_yetki`

### B. `pdovam_routes.py`

#### `GET /pdovam/api/fark-gun`

- Amaç: tek gün fark hesabı
- Tablolar:
  - `personel`
  - dolaylı `devam_kayitlari`
  - dolaylı `personel_hareketleri`

#### `GET /pdovam/api/hareket-son`

- Amaç: son hareketler
- Tablolar:
  - `personel_hareketleri`

#### `GET /pdovam/api/canli-durum`

- Amaç: anlık durum
- Tablolar:
  - `personel`
  - `personel_hareketleri`

#### `GET /pdovam/`

- Amaç: login gerektirmeyen mobil attendance ekranı
- Tablolar:
  - `personel`
  - `devam_kayitlari`
  - `personel_hareketleri`
- Opsiyonel dış bağımlılık:
  - Supabase `personel_devam`

#### `GET|POST /pdovam/isle/<personel_id>`

- Amaç: QR ile giriş/çıkış
- Tablolar:
  - `personel`
  - `devam_kayitlari`
  - `personel_hareketleri`
- Dış bağımlılık:
  - Supabase `personel_devam`
- Ek bağımlılık:
  - WiFi / yerel ağ politikası

#### `GET /pdovam/api/gunluk`

- Amaç: günlük attendance görünümü
- Tablolar:
  - `personel`
  - `devam_kayitlari`

#### `GET /pdovam/api/aylik`

- Amaç: aylık attendance özeti
- Tablolar:
  - `personel`
  - `devam_kayitlari`

#### `GET /pdovam/api/kayit`

- Amaç: tek devam kaydı
- Tablolar:
  - `devam_kayitlari`

#### `POST /pdovam/api/manuel`

- Amaç: admin manuel giriş/çıkış
- Tablolar:
  - `personel`
  - `devam_kayitlari`
- Dolaylı:
  - bulut sync

#### `POST /pdovam/api/sil`

- Amaç: attendance kaydı silme
- Tablolar:
  - `devam_kayitlari`
- Dolaylı:
  - bulut sync

#### `POST /pdovam/api/saat-utc-duzelt`

- Amaç: saat düzeltme bakım endpoint’i
- Tablolar:
  - `devam_kayitlari`
- Dış veri:
  - Supabase `personel_devam`

#### `GET /pdovam/api/supabase-senkron`

- Amaç: local attendance geçmişini buluta basma
- Tablolar:
  - `devam_kayitlari`
- Dış veri:
  - Supabase `personel_devam`

#### `GET /pdovam/api/schema-kur`

- Amaç: attendance schema bootstrap yardımcı endpoint’i
- Tablolar:
  - `SCHEMA_SQL` ile kurulan pdovam ilgili tablolar

#### `GET /pdovam/qr/tek`

- Amaç: ortak QR üretme
- Tablolar: yok

#### `GET /pdovam/qr/<personel_id>`

- Amaç: kişiye özel QR üretme
- Tablolar: yok

#### `GET /pdovam/qr/bulut/tek`

- Amaç: bulut ortak QR
- Tablolar: yok

#### `GET /pdovam/qr/bulut/<personel_id>`

- Amaç: bulut kişisel QR
- Tablolar: yok

#### `GET /pdovam/qr-yazdir`

- Amaç: tüm personeller için QR baskı ekranı
- Tablolar:
  - `personel`

### Personel / Attendance için Sonuç

Bu alan iki seviyede paketlenebilir:

- `attendance` çekirdeği
- `personnel` / HR çekirdeği

İleri otomasyonlar, ayrıca premiumlaştırılabilir.

---

## 3. Önerilen `module_key` Kataloğu

### A. Platform / satış dışı teknik anahtarlar

- `core_platform`
- `admin_platform`
- `marketing_site`

### B. Çekirdek ERP omurgası

- `core_erp`
- `crm`
- `cari_360`
- `invoicing`
- `collections`
- `banking`
- `offices`
- `products`
- `shipping`
- `expenses`
- `rent_tracking`
- `inflation_indexing`

### C. Yüksek bağımsızlık potansiyelli modüller

- `randevu`
- `personnel`
- `attendance`
- `whatsapp`
- `mobile_access`

### D. Niş / opsiyonel / ileri modüller

- `duplicate_control`
- `listing_automation`
- `messaging_automation`
- `public_booking`
- `leave_automation`
- `attendance_cloud_sync`
- `attendance_qr_printing`
- `document_generation`

### İlk gerçek rollout için önerilen dar liste

- `core_erp`
- `crm`
- `randevu`
- `personnel`
- `attendance`

---

## 4. `core_erp / add-on / standalone` Satış Modeli Matrisi

| module_key | Ana satış modeli | Add-on | Standalone | Not |
|---|---|---:|---:|---|
| `core_erp` | çekirdek ürün | hayır | hayır | ana taşıyıcı |
| `crm` | core içinde | evet | hayır (şimdilik) | Randevu buna bağlı |
| `cari_360` | premium core / add-on | evet | hayır | finansal özet |
| `invoicing` | core içinde | evet | hayır | ERP bağlamı güçlü |
| `collections` | core içinde | evet | hayır | cari/fatura bağımlı |
| `banking` | core add-on | evet | hayır | finans omurgası |
| `offices` | core içinde | evet | hayır | tek başına ürün değeri zayıf |
| `products` | core içinde | evet | hayır | ERP veri omurgası |
| `shipping` | add-on | evet | düşük öncelik | gelecekte değerlendirilebilir |
| `expenses` | add-on | evet | hayır | muhasebe bağlamı güçlü |
| `rent_tracking` | add-on / dikey ürün | evet | ileride olabilir | sektör bazlı |
| `inflation_indexing` | add-on | evet | hayır | destekleyici |
| `randevu` | add-on + standalone | evet | evet | güçlü modüler ürün adayı |
| `personnel` | add-on + standalone | evet | evet | attendance ile ilişkili |
| `attendance` | add-on + standalone | evet | evet | çok güçlü standalone aday |
| `whatsapp` | add-on | evet | belki | başka modülleri güçlendirir |
| `listing_automation` | add-on + standalone | evet | evet | niş ama satılabilir |

### Net ticari öneri

İlk ürünleşme:

1. `core_erp`
2. `randevu`
3. `attendance`
4. `personnel`
5. `whatsapp`

---

## 5. Randevu için Karar Ağacı: `customers` Bağımlılığını Koru vs Ayır

### Bugünkü gerçeklik

`randevu` bugün:

- public booking’te `customers` kaydı açıyor
- yönetim booking’te `customers` kullanıyor
- autocomplete’te `customers + musteri_kyc` kullanıyor
- bazı akışlarda `musteri_id` zorunlu

Yani bugün “müşteriden tamamen bağımsız booking domain’i” yok.

### Yol 1 — `customers` bağımlılığını KORU

Anlamı:

- standalone tenant’a da tam şema ver
- `customers`, Randevu içinde de rehber / katılımcı kaydı gibi işlesin

Artıları:

- en düşük risk
- en hızlı time-to-market
- mevcut route’lar büyük ölçüde korunur
- public booking kırılmaz
- provisioning değişmez

Eksileri:

- standalone Randevu teknik olarak mini CRM ile gelir
- domain dili bulanık kalır
- ileride sade ürün deneyimi için ağır gelebilir

Ne zaman mantıklı?

- hızlı lansman hedefleniyorsa
- ilk standalone müşteri dalgası doğrulanacaksa
- teknik risk minimum tutulmak isteniyorsa

### Yol 2 — `customers` bağımlılığını AYIR

Anlamı:

- ayrı `contact / attendee / booking_contact` domain modeli tasarla
- standalone Randevu bu yeni modele geçsin

Artıları:

- temiz domain sınırı
- daha sade standalone ürün
- müşteri olmayan katılımcılar için doğal model

Eksileri:

- en pahalı yol
- route’ların önemli bölümü etkilenir
- müşteri arama / booking / rapor / iptal / migration karmaşıklaşır

Ne zaman mantıklı?

- standalone Randevu ciddi büyürse
- CRM bağımlılığı ürün önünde engel olmaya başlarsa
- misafir / katılımcı / lead odaklı yeni akışlar çıkarsa

### Karar Ağacı

1. Amaç 3 ay içinde standalone Randevu’yu hızla satmak mı?
   - evet -> `customers` KORU
   - hayır -> 2. soruya geç

2. Standalone ürün, ERP Randevu’dan ciddi biçimde farklı domain istiyor mu?
   - evet -> AYIR
   - hayır -> KORU

3. Teknik ekip şu an ikinci veri modelini taşıyabilecek kapasitede mi?
   - hayır -> KORU
   - evet -> 4. soruya geç

4. Randevu’nun uzun vadeli bağımsız ürün potansiyeli ERP’den daha büyük mü?
   - evet -> orta vadede AYIR
   - hayır -> KORU

### Net öneri

**Bugün için:** `customers` bağımlılığını KORU.  
**Orta vadede:** standalone büyümesi kanıtlanırsa ayrı contact/attendee modeli planla.

---

## 6. Personel için Paketleme Önerisi: `attendance_core` vs `hr_core` vs `hr_premium`

### Paket 1 — `attendance_core`

Temel vaat:

- giriş / çıkış
- QR ile attendance
- günlük / aylık attendance
- canlı durum
- temel geç kalma takibi

Bu pakete girmesi önerilen route’lar:

- `/pdovam/`
- `/pdovam/isle/<personel_id>`
- `/pdovam/api/canli-durum`
- `/pdovam/api/hareket-son`
- `/pdovam/api/fark-gun`
- `/pdovam/api/gunluk`
- `/pdovam/api/aylik`
- `/pdovam/api/kayit`
- `/pdovam/api/manuel`
- `/pdovam/api/sil`
- `/pdovam/qr/tek`
- `/pdovam/qr/<personel_id>`
- `/pdovam/qr-yazdir`
- `/personel/api/devam/gunluk`
- `/personel/api/devam/aylik`
- `/personel/api/devam/kaydet`
- `/personel/api/gec/list`
- `/personel/api/gec/guncelle`
- `/personel/api/gec/sil`
- `/personel/api/gec/aylik-grid`

### Paket 2 — `hr_core`

Temel vaat:

- personel kartı
- temel personel bilgileri
- özlük
- temel izin yönetimi
- iç modül yetkisi

Bu pakete girmesi önerilen route’lar:

- `/personel/`
- `/personel/api/list`
- `/personel/api/personel/kaydet`
- `/personel/api/personel/ad`
- `/personel/api/personel/sil`
- `/personel/api/personel/bilgi`
- `/personel/api/personel/bilgi` (POST)
- `/personel/api/personel/ozluk`
- `/personel/api/personel/ozluk` (POST)
- `/personel/api/personel/ozluk/sil`
- `/personel/api/izin/list`
- `/personel/api/izin/kaydet`
- `/personel/api/izin/sil`
- `/personel/api/izin/guncelle`
- `/personel/api/yetki`
- `/personel/api/yetki` (POST)

### Paket 3 — `hr_premium`

İleri vaat:

- QR’den izin üretme
- otomatik izin hesaplama
- PDF / belge üretimi
- gelişmiş izin özeti
- bulut attendance sync
- operasyonel bakım yardımcıları

Bu pakete girmesi önerilen route’lar:

- `/personel/api/izin/qr-bekleyen`
- `/personel/api/izin/qr-onayla`
- `/personel/api/izin/qr-geri-al`
- `/personel/api/izin/ozet`
- `/personel/api/izin/ozet/liste`
- `/personel/api/izin/otomatik-hesapla`
- `/personel/api/izin-pdf/<izin_id>`
- `/pdovam/api/saat-utc-duzelt`
- `/pdovam/api/supabase-senkron`
- `/pdovam/api/schema-kur`
- `/pdovam/qr/bulut/tek`
- `/pdovam/qr/bulut/<personel_id>`

### Net ticari öneri

Standalone:

1. Attendance Basic -> `attendance_core`
2. HR Basic -> `attendance_core + hr_core`
3. HR Pro -> `attendance_core + hr_core + hr_premium`

ERP add-on:

1. Personel Takip -> `attendance_core`
2. Personel Yönetimi -> `attendance_core + hr_core`
3. İK Premium -> `attendance_core + hr_core + hr_premium`

### Kritik not

Bugünkü kodda `personnel` ile `attendance` teknik olarak sıkı bağlı. Bu yüzden ilk rollout’ta:

- ticari olarak ayrı modüller tanımlansa bile
- `personnel` satılıyorsa `attendance` de zorunlu bundle kabul edilmesi daha güvenli olabilir

---

## 7. Önerilen `public.tenant_module_entitlements` DDL Taslağı

Bu SQL taslağı yalnızca planlama amaçlıdır; henüz uygulanmayacaktır.

```sql
CREATE TABLE IF NOT EXISTS public.tenant_module_entitlements (
    id                  BIGSERIAL PRIMARY KEY,

    tenant_id           BIGINT NOT NULL,
    tenant_slug         TEXT NOT NULL,

    module_key          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'active',

    billing_mode        TEXT NOT NULL DEFAULT 'included',
    source_plan         TEXT,
    source_reference    TEXT,

    starts_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ends_at             TIMESTAMPTZ,
    granted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at          TIMESTAMPTZ,

    granted_by_user_id  BIGINT,
    granted_by_note     TEXT,

    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT tenant_module_entitlements_status_chk
        CHECK (status IN ('trial', 'active', 'suspended', 'expired', 'revoked')),

    CONSTRAINT tenant_module_entitlements_billing_mode_chk
        CHECK (billing_mode IN ('included', 'addon', 'standalone', 'promo', 'manual')),

    CONSTRAINT tenant_module_entitlements_module_key_chk
        CHECK (length(trim(module_key)) > 0),

    CONSTRAINT tenant_module_entitlements_tenant_slug_chk
        CHECK (length(trim(tenant_slug)) > 0),

    CONSTRAINT tenant_module_entitlements_dates_chk
        CHECK (ends_at IS NULL OR ends_at >= starts_at)
);

ALTER TABLE public.tenant_module_entitlements
    ADD CONSTRAINT tenant_module_entitlements_tenant_module_key_uniq
    UNIQUE (tenant_id, module_key);

ALTER TABLE public.tenant_module_entitlements
    ADD CONSTRAINT tenant_module_entitlements_tenant_id_fkey
    FOREIGN KEY (tenant_id)
    REFERENCES public.tenants (id)
    ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS tenant_module_entitlements_tenant_id_idx
    ON public.tenant_module_entitlements (tenant_id);

CREATE INDEX IF NOT EXISTS tenant_module_entitlements_tenant_slug_idx
    ON public.tenant_module_entitlements (tenant_slug);

CREATE INDEX IF NOT EXISTS tenant_module_entitlements_module_key_idx
    ON public.tenant_module_entitlements (module_key);

CREATE INDEX IF NOT EXISTS tenant_module_entitlements_status_idx
    ON public.tenant_module_entitlements (status);

CREATE INDEX IF NOT EXISTS tenant_module_entitlements_active_window_idx
    ON public.tenant_module_entitlements (tenant_id, module_key, starts_at, ends_at)
    WHERE status IN ('trial', 'active');

CREATE INDEX IF NOT EXISTS tenant_module_entitlements_metadata_gin_idx
    ON public.tenant_module_entitlements
    USING GIN (metadata);
```

### Tasarım notları

- `tenant_id` ana bağ
- `tenant_slug` debug ve hızlı lookup için denormalize kopya
- `module_key` teknik anahtar
- `status` ile trial / askıya alma / iptal senaryoları taşınabilir
- `billing_mode` ile included / add-on / standalone ayrımı yapılır

### Opsiyonel ikinci tablo

İlk fazda zorunlu değil ama ileride değerli olabilir:

```sql
CREATE TABLE IF NOT EXISTS public.module_catalog (
    module_key              TEXT PRIMARY KEY,
    module_name             TEXT NOT NULL,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    is_standalone_sellable  BOOLEAN NOT NULL DEFAULT FALSE,
    requires_core_erp       BOOLEAN NOT NULL DEFAULT FALSE,
    default_trial_days      INTEGER,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## 8. Rollout Checklist’i (Faz 0 → Faz 7)

### Faz 0 — Envanter ve ürün kararı

Amaç:

- modül sınırlarını dondurmak

Yapılacaklar:

- route-to-module haritası
- tablo-to-module haritası
- shared bağımlılık listesi
- standalone satılabilir modüller kararı
- `module_key` listesini sabitleme

Bitti kriterleri:

- `module_key` listesi onaylandı
- her route bir modüle atandı
- shared tablolar listelendi
- standalone / add-on ayrımı yazılı hale geldi

### Faz 1 — Entitlement veri modeli

Amaç:

- tenant bazlı ürün sahipliğini platformda tutmak

Yapılacaklar:

- `tenant_module_entitlements` tasarımını kesinleştirmek
- backfill stratejisi
- mevcut tenant’lar için varsayılan entitlement matrisi

Bitti kriterleri:

- veri modeli onaylandı
- mevcut tenant’ların default erişim kuralı netleşti
- rollback kuralı tanımlandı

### Faz 2 — Entitlement okuma katmanı

Amaç:

- “modül aktif mi?” sorusunu tek noktadan çözmek

Yapılacaklar:

- lookup helper
- cache kararı
- response davranışı
- logging / audit yaklaşımı

Bitti kriterleri:

- `module_required("x")` sözleşmesi net
- cache invalidation kuralı yazılı
- performans yaklaşımı onaylandı

### Faz 3 — Backend route guard rollout

Amaç:

- seçili route’larda modül erişim kontrolü

Yapılacaklar:

- pilot modül seçimi
- public route politikası
- admin override gereksinimi

Bitti kriterleri:

- ilk dalga route listesi onaylandı
- public / authenticated davranış farkı netleşti
- test senaryoları tanımlandı

### Faz 4 — UI gating

Amaç:

- kullanıcı yalnız sahip olduğu modülleri görsün

Yapılacaklar:

- menü filtreleme
- dashboard kart filtreleme
- direct URL senaryoları
- upsell / paketinizde yok mesajları

Bitti kriterleri:

- menu-item -> module_key eşlemesi çıktı
- backend guard ile UI davranışı uyumlu
- UX mesajları hazır

### Faz 5 — Pricing ve signup genişlemesi

Amaç:

- modül bazlı fiyatlama ve signup seçimi

Yapılacaklar:

- add-on fiyat kuralları
- standalone fiyat kuralları
- signup’ta modül seçimi
- provision sonrası entitlement seed mantığı

Bitti kriterleri:

- fiyat modeli örnek senaryolarla doğrulandı
- signup / provision / entitlement zinciri yazılı hale geldi
- satış matrisi çıktı

### Faz 6 — Modül bağımsızlaştırma sertleştirmesi

Amaç:

- standalone ürünlerde bağımlılık azaltma

Yapılacaklar:

- Randevu için customer bağımlılığı kararı
- Personel için attendance/personnel coupling azaltma planı
- template/static eksiklerini tamamlama planı
- opsiyonel operasyon endpoint’lerini ayırma

Bitti kriterleri:

- Randevu domain stratejisi netleşti
- Personel / attendance teknik sınırları netleşti
- zorunlu env/dependency listesi hazır

### Faz 7 — İleri seviye provisioning ayrışması

Amaç:

- gerekirse küçük DDL ya da `core + overlay` modeline geçmek

Yapılacaklar:

- modül bazlı DDL varyantları
- migration kombinasyonları
- tenant capability matrix

Bitti kriterleri:

- hangi ürün kombinasyonu hangi tablo setini kurar netleşti
- migration zinciri deterministik hale geldi
- test matrisi kabul edildi

### Fazlar için genel not

Faz 7, ancak standalone ürün hacmi bunu gerçekten gerekli kılarsa düşünülmelidir. İlk çözülmesi gereken problem provisioning küçültme değil, entitlement yönetimidir.

---

## 9. Sprint Planı

### Sprint 1 — Envanter + entitlement temeli

Tahmini süre:

- 5–8 iş günü

Kapsam:

- module catalog kararı
- route / tablo sahipliği haritası
- `tenant_module_entitlements` son tasarımı
- mevcut tenant backfill stratejisi

Ana riskler:

- ürün sınırları netleşmeyebilir
- Randevu customer bağımlılığı küçümsenebilir
- Personel / attendance teknik bağları hafife alınabilir

Bitti kriteri:

- “hangi tenant hangi modülü alabilir?” sorusuna yazılı, net cevap verilebilir

### Sprint 2 — Backend guard + UI gating pilotu

Tahmini süre:

- 7–10 iş günü

Kapsam:

- entitlement lookup katmanı
- backend guard
- menü / UI filtreleme
- pilot modül rollout’u

Önerilen pilot:

- önce `personnel`
- sonra `randevu` yönetim route’ları

Ana riskler:

- backend ve UI davranışı uyumsuz olabilir
- public route’larda yanlış kapama yaşanabilir
- cache / stale entitlement problemi çıkabilir

Bitti kriteri:

- en az bir modül tenant bazlı güvenli aç/kapa yapılabiliyor olmalı

### Sprint 3 — Pricing + signup/provision entegrasyonu

Tahmini süre:

- 8–12 iş günü

Kapsam:

- add-on / standalone fiyat modeli
- signup’ta modül seçimi
- provision sonrası entitlement seed
- satış -> erişim zinciri

Ana riskler:

- ticari model ile entitlement kuralları çelişebilir
- standalone ve ERP akışları karışabilir
- Randevu için domain beklentisi teknik gerçekten daha büyük çıkabilir

Bitti kriteri:

- ürün seçimi -> entitlement -> erişim akışı uçtan uca netleşmiş olmalı

---

## 10. Net Sonuç ve Bugünkü Tavsiye

Bugünkü planlamanın vardığı net kararlar:

1. **İlk aşamada tam şema provisioning korunmalı**
2. **Erişim kontrolü `public` şemadaki entitlement tablosu ile çözülmeli**
3. **İlk pilot modüller: `personnel`, `attendance`, `randevu`**
4. **`randevu` kısa vadede `customers` bağımlılığını korumalı**
5. **`personnel` ticari olarak ayrı paketlenebilir, ama ilk rollout’ta `attendance` ile bundle etmek daha güvenli**
6. **Faz 7’deki küçük DDL / overlay yaklaşımı ancak çok sonra değerlendirilmeli**

## Bilinen küçük RİSKLER

- `public.tenants` ARTIK HEM gerçek SaaS kiracılarını HEM platform sahibini (`public`) İÇERİYOR — İLERİDE “TÜM kiracıları LİSTELE / müşteri SAYISI” GİBİ bir özellik YAZILIRSA, `schema_name ~ '^tenant_'` FİLTRESİ eklenmeyi UNUTMAMALI.
- `backfill_tenant_user_lookup.py` İÇİNDEKİ `_admin_email_for_schema()`, ŞU AN SADECE `tenant_*` formatını İŞLİYOR (`public`’İ BİLİNÇLİ olarak ATLIYOR) — BU davranış İLERİDE değiştirilirse, `public` slug’ının REZERVE olduğunu (login-lookup URL üretiminde SORUN ÇIKARABİLECEĞİNİ) hatırla.

## Bir Sonraki Doğal Adım

Bu proje henüz planlama aşamasında. Sprint 1’e geçmeden önce kullanıcıdan ayrıca açık onay alınmalıdır.

Sprint 1 başladığında ilk üretilecek somut uygulama artefaktları:

- tenant entitlement veri modeli
- route-level guard sözleşmesi
- menu-item -> module_key eşlemesi
- pilot rollout test checklist’i
