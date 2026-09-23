# Payafin Cari — GİB e-Arşiv sözleşmesi (G0)

Bu belge, Payafin Cari’nin GİB **e-Arşiv** taslağı üretmesi için veri eşlemesi ve izolasyon kurallarını sabitler.

**Kanal:** mevcut `BestOfficeGIBManager` (e-Arşiv / `eArsivPortal`). Gerçek e-Fatura (UBL) kapsam dışı.  
**Kaynak kod:** `erp_web/ledger_gib_adapter.py` → `BestOfficeGIBManager.fatura_taslak_olustur` / `gib_dispatch_jp_onizle` (sınıf **değiştirilmeden** çağrılır).

---

## İzolasyon

| Dokunulmaz | İzinli |
|---|---|
| `faturalar` tablosu / satırları | Kiracı şemasında `ledger_*` DDL |
| `gib_earsiv.py` gövdesi / `BestOfficeGIBManager` edit | `from gib_earsiv import BestOfficeGIBManager` → yalnız çağrı |
| `build_fatura_data_from_db` (faturalar.id) | `build_fatura_data_from_ledger` (ledger_invoices) |
| `faturalar_routes.py`, `customers`, ana ERP UI | `ledger_routes.py`, `templates/ledger/`, `ledger_gib_adapter.py` |

---

## `fatura_data` eşleme (ledger → GİB)

`fatura_taslak_olustur` düz dict bekler (nested `alici` / `kalemler` yok).

| GİB anahtarı | Ledger kaynağı |
|---|---|
| `vkn` | `ledger_parties.tax_id` |
| `ad` / `soyad` | `type=person` → `name` boşlukla split |
| `unvan` | `type=company` → `name` |
| `vd` | `ledger_parties.tax_office` |
| `adres` | `ledger_parties.address` |
| `telefon` / `email` | party `phone` / `email` |
| `tarih` | `ledger_invoices.invoice_date` → `GG/MM/YYYY` |
| `saat` | gönderim anı `HH:MM:SS` |
| `items[]` | `ledger_invoice_lines` → `{name, quantity, unit_price, tax_rate}` |
| `hizmet_adi` / `birim_fiyat` / `kdv_orani` | ilk satır |
| `toplam` | `ledger_invoices.grand_total` |
| `note` | `ledger_invoices.note` (max 1500) |
| `mevcut_ettn` | `ledger_invoices.gib_ettn` (yeniden gönderim) |
| `mevcut_belge_no` | `ledger_invoices.gib_belge_no` (GIB… ise) |
| `iban` | `""` |
| `irsaliye_modu` | `false` (v1) |

**Kullanılmaz:** `build_fatura_data_from_db`.

---

## Durum makinesi (`ledger_invoices.status`)

`draft` → `ready` (insan onayı / `confirmed_at`) → `gib_taslak` (ETTN) → `gib_imzalandi` (SMS; feature flag)  
Alternatif: `void`, `failed`.

---

## İnsan kapıları (zorunlu)

1. give → draft dönüşümü (K1)  
2. Vergi kimliği (tax_id 10/11) (K2)  
3. Kalem + KDV (K3)  
4. JP önizleme onayı (K4)  
5. Ayrı “GİB’e taslak gönder” (K5)  
6. SMS imza — `LEDGER_GIB_SMS_ENABLED=1` olmadan kapalı (K6)  
7. `GIB_TEST` / prod bilinci (K7)

**Yasak:** sessiz GİB; OCR→otomatik fatura; receive/void’tan **giden** fatura; `faturalar` yazma; otomatik imza.

---

## Giden / Gelen Fatura — iki yol (Q1–Q4)

Payafin Cari detay ekranında fatura oluşturmanın **iki ayrı** yolu vardır. İkisi de `faturalar` tablosuna ve ana ERP GİB UI’sına dokunmaz.

| Yol | UI | API | Bakiye | Ne zaman |
|---|---|---|---|---|
| **Üst buton (atomik kısayol)** | «Giden Fatura» / «Gelen Fatura» (Verdim \| Aldım \| Ekstre yanında) | `POST /ledger/api/invoices/quick-create` | **Değişir** (give↑ / receive↓) | Kullanıcı tek adımda hem hareket hem belge ister |
| **Satır buton (ikincil)** | Hareket satırındaki «Giden Fatura» / «Gelen Fatura» | Giden: `POST …/from-transaction` · Gelen: `POST …/incoming-invoices` | **Değişmez** (hareket zaten var) | Önce Verdim/Aldım kaydedilmiş; sonradan belge eklenir |

### Üst — atomik kısayol (`quick-create`)

1. **Giden:** JSON `direction=give` → tek DB transaction içinde `ledger_transactions(give)` + `ledger_invoices` (`source_transaction_id` = yeni tx) + satır(lar). Gövde: ya eski tek alan (`amount` = KDV dahil + `tax_rate` + `description`) ya da çok satır `lines[]` (`description`, `quantity`, `unit_price` = KDV hariç, `tax_rate`); `lines` doluysa eski tutar alanları yok sayılır; tx tutarı = `grand_total`. Sonra mevcut Giden paneli (Önizle / Onayla / GİB taslak / Durum / HTML).
2. **Gelen:** multipart `direction=receive` + görsel → tek DB transaction içinde `ledger_transactions(receive)` → R2 yükleme → `ledger_incoming_invoices` (`source_transaction_id` = yeni tx; `amount` = KDV dahil grand). İsteğe bağlı `lines` = JSON string `[{description, quantity, unit_price, tax_rate}, …]` → `ledger_incoming_invoice_lines`; doluysa `amount` yok sayılır (otorite = satırlar). Eski tek `amount` yolu satır yazmaz (geriye uyum). Hata olursa DB rollback; R2’ye yazılmışsa best-effort silme. GİB yok.

Form notu: *«Hareket ve fatura birlikte oluşur; bakiye güncellenir.»*

### Satır — mevcut harekete belge

1. **Giden:** Yalnızca `give` + TRY, void değil; zaten aktif fatura varsa **409**.
2. **Gelen:** Yalnızca `receive` + TRY; dosya zorunlu; aynı `source_transaction_id` için aktif gelen kayıt varsa **409**. UI’da belge varken satır «Gelen belge» + Görüntüle gösterir (ikinci «Gelen Fatura» butonu yok).

### Çakışma / çift fatura

Aynı hareket için ikinci aktif giden veya gelen fatura **oluşturulamaz** (kısmi unique + API 409). Üst yoldan oluşan kayıtlarda `source_transaction_id` dolu olduğu için satır yolu o harekette ikinci belgeyi reddeder.

---

## API özeti (G1–G9 + Q1–Q2)

| Method | Path | Not |
|---|---|---|
| CRUD | `/ledger/api/parties` | `tax_id`, `tax_office`, `address`, `tax_id_kind` |
| POST | `/ledger/api/invoices/quick-create` | Atomik: give+JSON (`amount` veya `lines[]`) → tx+`ledger_invoices`; receive+multipart (`amount` veya `lines` JSON + file) → tx+R2+`ledger_incoming_invoices` (+ isteğe bağlı `ledger_incoming_invoice_lines`) |
| POST | `/ledger/api/invoices/from-transaction` | Mevcut give+TRY → draft (satır yolu) |
| GET/PUT | `/ledger/api/invoices/<id>` | local CRUD |
| POST | `/ledger/api/invoices/<id>/confirm` | → ready + confirmed_at |
| POST | `/ledger/api/invoices/<id>/gib-preview` | JP önizleme (yazmaz) |
| POST | `/ledger/api/invoices/<id>/gib-taslak` | taslak; ETTN |
| GET | `/ledger/api/invoices/<id>/gib-status` | portal durum |
| GET | `/ledger/api/invoices/<id>/gib-html` | HTML |
| POST | `/ledger/api/invoices/<id>/gib-sms-*` | yalnız flag açıkken |
| POST | `/ledger/api/incoming-invoices` | Mevcut receive’e gelen belge (satır yolu; GİB yok) |
| GET | `/ledger/api/incoming-invoices?party_id=` | Liste |
| GET | `/ledger/api/incoming-invoices/<id>` | Presign görüntü URL |
