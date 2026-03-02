# Müşteriler Dashboard (React + Tailwind)

Referans görsellere göre modern Fintech Müşteriler ekranı. CSS Grid, glassmorphic kartlar, gradient grafik ve h-screen düzeni kullanılır.

## Gereksinimler

- Node.js 18+
- npm veya yarn

## Kurulum

```bash
cd musteriler-dashboard
npm install
npm run dev
```

Tarayıcıda http://localhost:5174 açılır.

## Build

```bash
npm run build
```

`dist/` klasörü Flask uygulamasından statik olarak sunulabilir.

## Mimari

- **Layout:** `h-screen`, CSS Grid, kartlar arası `gap: 1.25rem`
- **Kartlar:** `border-radius: 12px`, `backdrop-filter: blur(10px)`, ince border
- **Renkler:** Deep Midnight Blue arka plan; Cool Blue, neon-green (Wasabi), Persimmon Orange vurgular
- **Veri:** `src/data/mock.js` — gerçek API ile değiştirilebilir

## Bileşenler

- `PageTitle` — Başlık, filtreler, trust badge
- `KpiStrip` — 5 KPI kartı (metrik etiketleri opacity 0.7)
- `ChartPanel` — Tahsilat Performansı gradient çizgi grafiği (Recharts)
- `RiskPanel` — Parlayan hexagon içinde risk değeri (100)
- `ActionButtons` — 4 büyük renkli buton + 6 ince çerçeveli aksiyon kartı
- `CustomerListPanel` — Sağda dar, kaydırılabilir müşteri tablosu
- `FooterStrip` — Kârlılık/Risk/TÜFE/WhatsApp/Excel + yeşil CTA

## Not

"Windows'u Etkinleştir" uyarısı işletim sistemine aittir; uygulama kodu ile kaldırılamaz.
