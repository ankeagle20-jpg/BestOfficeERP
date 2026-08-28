# Payafin Cari (`ledger`) — JSON API referansı (L4)

Bu belge, ilerideki **native mobil** istemciler için `/ledger/api/*` sözleşmesini sabitler.

**Modül anahtarı:** `ledger`  
**URL öneki:** `/ledger`  
**Yetki:** Oturum cookie’si + `tenant_module_entitlements` içinde `ledger` (trial/active).  
Oturum yoksa `/ledger/api/*` → **401** `{ "ok": false, "mesaj": "Oturum gerekli..." }` (HTML redirect yok).  
**Kiracı:** Host / `X-BestOffice-Tenant` ile çözülür (mevcut multi-tenant deseni).

---

## Yanıt sözleşmesi (zorunlu)

| Durum | Gövde | HTTP |
|--------|--------|------|
| Başarı | `{ "ok": true, ... }` | 200 veya 201 |
| Hata | `{ "ok": false, "mesaj": "<insan-okur metin>" }` | 400 / 401 / 403 / 404 / 500 |

- Alan adı **`mesaj`** (Türkçe); `message` kullanılmaz.
- Modül yetkisi yoksa veya kiracı çözülemezse: `403` + `{ok:false, mesaj:...}` (`/api/` yollarında).
- Oturum yoksa (`/ledger/api/*`): `401` + `{ok:false, mesaj:...}` (HTML login redirect yok; native/mobil güvenli).

### İstisna: ekstre PDF

`GET /ledger/api/parties/<id>/statement/pdf`

- **Başarı:** `Content-Type: application/pdf` (ikili gövde; JSON değil).
- **Hata:** yine JSON `{ "ok": false, "mesaj": "..." }`.

---

## Ortak nesneler

### `party`

```json
{
  "id": 1,
  "name": "Ayşe",
  "type": "person",
  "phone": null,
  "email": null,
  "country": null,
  "notes": null,
  "is_active": true,
  "created_at": "...",
  "updated_at": "...",
  "balances": [
    {
      "currency": "TRY",
      "given": 100.0,
      "received": 40.0,
      "balance": 60.0,
      "party_owes_us": true,
      "we_owe_party": false
    }
  ]
}
```

`balance = given − received` (`is_void = false` hareketler). `>0` → taraf bize borçlu.

### `transaction`

```json
{
  "id": 10,
  "party_id": 1,
  "direction": "give",
  "amount": 50.0,
  "currency": "TRY",
  "occurred_at": "...",
  "note": null,
  "created_by": 1,
  "is_void": false,
  "metadata": {},
  "created_at": "..."
}
```

`direction`: `give` | `receive`.

### `group`

```json
{
  "id": 2,
  "name": "Aile",
  "notes": null,
  "created_at": "...",
  "updated_at": "...",
  "member_count": 3
}
```

### `reminder`

```json
{
  "id": 5,
  "party_id": 1,
  "due_at": "...",
  "channel": "in_app",
  "status": "pending",
  "note": null,
  "created_at": "...",
  "updated_at": "..."
}
```

`channel`: `email` | `in_app`. `status`: `pending` | `sent` | `dismissed`.

---

## Endpoints

### Parties

#### `GET /ledger/api/parties`

| Query | Tip | Varsayılan | Açıklama |
|-------|-----|------------|----------|
| `active` | string | `1` | `0`/`false` → pasifler dahil |
| `q` | string | — | ad / telefon / e-posta ILIKE |

**200:**

```json
{ "ok": true, "parties": [ /* party */ ], "count": 0 }
```

#### `POST /ledger/api/parties`

**Body (JSON):**

| Alan | Zorunlu | Açıklama |
|------|---------|----------|
| `name` | evet | |
| `type` | hayır | `person` (varsayılan) \| `company` |
| `phone`, `email`, `country`, `notes` | hayır | |

**201:** `{ "ok": true, "party": { ... } }`  
**403:** kademe limiti (aktif cari sayısı) aşıldı — `mesaj` içinde “kademenizi yükseltin”.  
**400:** doğrulama hataları.

#### `GET /ledger/api/parties/<party_id>`

**200:** `{ "ok": true, "party": { ... }, "transactions": [ /* transaction */ ] }`  
**404:** cari yok.

---

### Transactions

#### `POST /ledger/api/transactions`

| Alan | Zorunlu | Açıklama |
|------|---------|----------|
| `party_id` | evet | int |
| `direction` | evet | `give` \| `receive` |
| `amount` | evet | > 0 |
| `currency` | hayır | 3 harf ISO (varsayılan genelde TRY) |
| `occurred_at` | hayır | ISO tarih/saat |
| `note` | hayır | |
| `metadata` | hayır | nesne |

**201:** `{ "ok": true, "transaction": { ... }, "balances": [ ... ] }`

#### `POST /ledger/api/transactions/<tx_id>/void`

| Alan | Zorunlu | Açıklama |
|------|---------|----------|
| `reason` | hayır | iptal nedeni (metadata’ya yazılır) |

**200:** `{ "ok": true, "transaction": { ... }, "balances": [ ... ], "mesaj"?: "Zaten iptal." }`  
Soft void: `is_void=true`; bakiye hesaplarından düşer.

---

### Groups

#### `GET /ledger/api/groups`

**200:** `{ "ok": true, "groups": [ /* group */ ], "count": N }`

#### `POST /ledger/api/groups`

| Alan | Zorunlu |
|------|---------|
| `name` | evet |
| `notes` | hayır |

**201:** `{ "ok": true, "group": { ... } }`

#### `POST /ledger/api/groups/<group_id>/members`

| Alan | Zorunlu | Açıklama |
|------|---------|----------|
| `party_id` | evet | |
| `action` | hayır | `add` (varsayılan) \| `remove` (`ekle` / `çıkar` eşanlamlı) |

**200:** `{ "ok": true, "action": "add"|"remove", "group_id": N, "party_id": N }`

#### `GET /ledger/api/groups/<group_id>`

**200:**

```json
{
  "ok": true,
  "group": { "...": "..." },
  "members": [ /* party */ ],
  "consolidated_balances": [ /* currency balances SUM */ ]
}
```

---

### Statement

#### `GET /ledger/api/parties/<party_id>/statement`

| Query | Zorunlu |
|-------|---------|
| `from` | evet (`YYYY-MM-DD`) |
| `to` | evet (`YYYY-MM-DD`) |

**200:** `{ "ok": true, "statement": { "party": {...}, "from": "...", "to": "...", "rows": [...], "period_totals": [...], "row_count": N } }`

#### `GET /ledger/api/parties/<party_id>/statement/pdf`

Aynı query + opsiyonel `indir=1` (attachment).  
Başarı: PDF bytes. Hata: JSON `{ok:false, mesaj:...}`.

---

### Reminders

#### `GET /ledger/api/reminders`

| Query | Açıklama |
|-------|----------|
| `status` | filtre (örn. `pending`) |
| `party_id` | filtre |

**200:** `{ "ok": true, "reminders": [ ... ], "count": N }`

#### `POST /ledger/api/reminders`

| Alan | Zorunlu |
|------|---------|
| `party_id` | evet |
| `due_at` | evet (ISO) |
| `channel` | hayır (`email` \| `in_app`) |
| `note` | hayır |

**201:** `{ "ok": true, "reminder": { ... } }`

#### `POST /ledger/api/reminders/<reminder_id>/dismiss`

**200:** `{ "ok": true, "reminder": { ... }, "mesaj"?: "Zaten kapatıldı." }`

---

### Summary (FX)

#### `GET /ledger/api/summary`

| Query | Açıklama |
|-------|----------|
| `display_currency` | opsiyonel; 3 harf ISO. Verilirse USD hub ile çeviri. |

**200:**

```json
{
  "ok": true,
  "by_currency": [ /* ... */ ],
  "converted": null,
  "fx_warnings": []
}
```

`display_currency` ile çeviri **tam başarılı değilse** `converted.balance` / `receivable` / `payable` = `null`, `complete: false`, `fx_warnings` dolu (sessiz yanlış toplam yok).

---

## PWA (L4 — native değil)

| Yol | Açıklama |
|-----|----------|
| `GET /ledger/manifest.webmanifest` | Uygulama adı **Payafin Cari**, `display: standalone`, tema `#0d7a5f` |
| `GET /ledger/sw.js` | Basit service worker; gezinmede ağ yoksa `/ledger/offline` |
| `GET /ledger/offline` | Kullanıcı dostu “bağlantı yok” sayfası |
| `/static/ledger/icon-192.png`, `icon-512.png` | İkonlar |

Tam offline veri senkronu **yok**; yalnızca bağlantı kesilince mesaj.

---

## Mobil istemci notları

1. Tüm JSON yanıtlarında önce `ok` kontrol edin; `false` ise `mesaj` gösterin.  
2. Bakiyeyi istemci tarafında saklamayın; her okumada API’den alın (sunucu canlı SUM).  
3. Void = soft delete; hareket silinmez.  
4. PDF dışında Content-Type `application/json` bekleyin.
