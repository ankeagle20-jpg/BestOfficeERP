# Payafin Cari — Stok (S1–S6)

Payafin Cari’ye özgü ürün kataloğu ve stok hareketleri. **Ana ERP `urunler` / `urun_routes` / GİB / `faturalar` tablosuna dokunulmaz.**

## İzolasyon

| Dokunulmaz | İzinli |
|---|---|
| `urunler`, `urun_hareketleri`, ana ERP stok UI | Kiracı şeması `ledger_products`, `ledger_stock_movements` |
| `faturalar`, `gib_earsiv` gövdesi | `ledger_invoices` / `ledger_incoming_invoices` + satırlar |
| Ana ERP ürün API | `GET/POST /ledger/api/products`, hareket listesi |

## Tablolar

- **`ledger_products`**: ad (`name` / `name_norm`), birim, varsayılan KDV, `qty_on_hand` (önbellek), `is_active`
- **`ledger_stock_movements`**: `direction` (`in`|`out`), `quantity` (>0), `source_kind` (`outgoing_line`|`incoming_line`|`manual_adjust`), fatura/satır FK’leri, `is_void`

Doğruluk kaynağı hareketlerdir; `qty_on_hand` her yazımda güncellenir. Aktif (`is_void=FALSE`) `outgoing_line_id` / `incoming_line_id` üzerinde kısmi UNIQUE indeksler çift yazımı engeller.

## Aşamalar

| Aşama | Ne |
|---|---|
| **S1** | DDL: `ensure_ledger_product_tables()` |
| **S2** | Ürün CRUD API + Cari «Stok» sekmesi (fatura henüz stok yazmaz) |
| **S3** | Giden `quick-create` (çok satır): satır INSERT sonrası `_apply_stock_for_lines(..., out, outgoing_line)` — aynı `db_txn` |
| **S4** | Gelen `quick-create` **çok satırlı** `lines[]`: `_apply_stock_for_lines(..., in, incoming_line)`. Eski tek-`amount` yolu: satır yok → **stok yok** |
| **S5** | Giden/Gelen satır «açıklama» autocomplete (`GET /ledger/api/products?q=`, 250ms debounce). Backend’e `product_id` gitmez; eşleşme hâlâ açıklama metni (`name_norm`) |
| **S6** | Void ile stok geri alma (bu belge) |

### Yazım formülü (S3/S4)

- Giden satır → hareket `out` → `qty_on_hand -= quantity`
- Gelen çok-satır → hareket `in` → `qty_on_hand += quantity`
- Ürün yoksa `name_norm` ile bulunur veya oluşturulur (`SELECT … FOR UPDATE`)

### Void formülü (S6)

UI «İptal» → `POST /ledger/api/transactions/<tx_id>/void` (atomik `db_txn`):

1. `ledger_transactions.is_void = TRUE`
2. `source_transaction_id = tx` olan giden / gelen faturalar bulunur
3. `_void_stock_movements`: aktif hareketler `FOR UPDATE` → `is_void=TRUE` → qty ters:
   - `out` iptal → `qty_on_hand += quantity` (stok geri artar)
   - `in` iptal → `qty_on_hand -= quantity` (stok geri azalır)

Ayrıca `POST /ledger/api/invoices/<id>/void` (giden fatura iptali) aynı yardımcı ile stok geri alır.

İkinci void: zaten `is_void` hareketler seçilmez → qty **ikinci kez değişmez** (idempotent).

**Kapsam dışı S6:** gelen soft-delete (`is_deleted`), `from-transaction` stok yazımı, tx void’ta fatura `status='void'` zorlama.

## Kod girişleri

- Yardımcılar: `routes/ledger_invoice_routes.py` — `_apply_stock_for_lines`, `_void_stock_movements`
- Tx void: `routes/ledger_routes.py` — `api_transactions_void`
- Giden fatura void: `api_invoices_void`
- Ürün API / UI: `routes/ledger_product_routes.py`, `templates/ledger/index.html` (Stok sekmesi + satır autocomplete)
