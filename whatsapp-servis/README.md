# WhatsApp servisi (whatsapp-web.js)

Çok kiracılı Node API. Flask ERP, `WHATSAPP_SERVICE_URL` üzerinden
`/t/{tenantId}/…` yollarını çağırır (`X-WA-Internal-Token` zorunlu).

## Ortam değişkenleri

| Değişken | Varsayılan | Açıklama |
|----------|------------|----------|
| `PORT` | `3001` | HTTP dinleme portu |
| `WA_MAX_CONCURRENT_CHROME` | `2` | Eşzamanlı Puppeteer/Chrome üst sınırı (Render’da 2 önerilir) |
| `WA_IDLE_MS` | `1200000` (20 dk) | Kullanılmayan kiracı oturumunu bellekten kapatma eşiği (`default` hariç) |
| `WA_IDLE_CHECK_MS` | `60000` | Idle süpürme aralığı |
| `WA_HEADLESS` | `false` | `true` / `1` / `new` → headless; yerel varsayılan false |
| `WA_INTERNAL_TOKEN` | `bestoffice-wa-internal` | Flask ile paylaşılan secret; üretimde güçlü değer kullanın |
| `PUPPETEER_EXECUTABLE_PATH` | (otomatik) | Chrome yolu (opsiyonel) |

Flask tarafı (ERP):

| Değişken | Varsayılan | Açıklama |
|----------|------------|----------|
| `WHATSAPP_SERVICE_URL` | `http://127.0.0.1:3001` | Node taban URL |
| `WA_INTERNAL_TOKEN` | aynı varsayılan | Node ile **aynı** secret |

## Render notları

- `WA_HEADLESS=true` kullanın (ekran yok).
- `WA_MAX_CONCURRENT_CHROME=2` (veya daha düşük) — RAM sınırlıdır; tüm kiracıları sürekli açık tutmayın.
- Idle destroy auth diskini silmez; sonraki istekte session yeniden yüklenir.
- Node’u public internete açmayın; yalnızca Flask private network’ten token ile erişsin.
- QR için tarayıcıda `/whatsapp/qr-ac` kullanın (Flask proxy).

## API (özet)

- `GET /health` — token gerekmez
- `GET /t/:tenantId/durum`
- `GET /t/:tenantId/qr-goster`
- `POST /t/:tenantId/kuyruk-ekle` | `kuyruk-toplu-ekle` | `send`
- Eski `/durum`, `/qr-goster`, `/kuyruk-*`, `/send` → **410 Gone**

`tenantId`: `default` veya `tenant_[a-z0-9_]+`
