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

**Yasak:** sessiz GİB; OCR→otomatik fatura; receive/void’tan fatura; `faturalar` yazma; otomatik imza.

---

## API özeti (G1–G9)

| Method | Path | Not |
|---|---|---|
| CRUD | `/ledger/api/parties` | `tax_id`, `tax_office`, `address`, `tax_id_kind` |
| POST | `/ledger/api/invoices/from-transaction` | give+TRY → draft |
| GET/PUT | `/ledger/api/invoices/<id>` | local CRUD |
| POST | `/ledger/api/invoices/<id>/confirm` | → ready + confirmed_at |
| POST | `/ledger/api/invoices/<id>/gib-preview` | JP önizleme (yazmaz) |
| POST | `/ledger/api/invoices/<id>/gib-taslak` | taslak; ETTN |
| GET | `/ledger/api/invoices/<id>/gib-status` | portal durum |
| GET | `/ledger/api/invoices/<id>/gib-html` | HTML |
| POST | `/ledger/api/invoices/<id>/gib-sms-*` | yalnız flag açıkken |
