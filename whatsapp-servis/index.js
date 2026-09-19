const express = require('express');
const cors = require('cors');
const path = require('path');
const fs = require('fs');
const QRCode = require('qrcode');
const { Client, LocalAuth } = require('whatsapp-web.js');

const PORT = process.env.PORT || 3001;
const AUTH_DATA_PATH = path.join(__dirname, '.wwebjs_auth');
const TENANT_ID_RE = /^(default|tenant_[a-z0-9_]+)$/;

const app = express();
app.use(cors());
app.use(express.json());

/** @type {Map<string, object>} */
const sessions = new Map();

function normalizeTelefon(raw) {
  let phone = String(raw || '').replace(/\D/g, '');
  if (!phone) return '';
  if (phone.startsWith('0')) phone = '90' + phone.slice(1);
  else if (phone.length === 10 && phone.startsWith('5')) phone = '90' + phone;
  else if (!phone.startsWith('90') && phone.length <= 11) phone = '90' + phone.replace(/^0+/, '');
  return phone;
}

function resolveChromeExecutable() {
  const fromEnv = (process.env.PUPPETEER_EXECUTABLE_PATH || '').trim();
  if (fromEnv) return fromEnv;
  const candidates = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    (process.env.LOCALAPPDATA || '') + '\\Google\\Chrome\\Application\\chrome.exe',
  ];
  for (const p of candidates) {
    try {
      if (p && fs.existsSync(p)) return p;
    } catch (_e) {}
  }
  return undefined;
}

const chromeExecutable = resolveChromeExecutable();
if (chromeExecutable) {
  console.log('[WA] Chrome:', chromeExecutable);
} else {
  console.warn('[WA] Sistem Chrome bulunamadı; Puppeteer varsayılanına düşülecek.');
}

function buildPuppeteerOpts() {
  const headlessEnv = String(process.env.WA_HEADLESS || '').trim().toLowerCase();
  let headless = false;
  if (headlessEnv === 'true' || headlessEnv === '1' || headlessEnv === 'yes') headless = true;
  else if (headlessEnv === 'new') headless = 'new';
  // Yerelde varsayılan false (Socket null crash riski); Render'da WA_HEADLESS=true
  const opts = {
    headless,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-extensions',
    ],
  };
  if (chromeExecutable) opts.executablePath = chromeExecutable;
  return opts;
}

/** Bir kerelik: LocalAuth clientId='default' → session-default/ */
function migrateDefaultSessionDir() {
  const oldPath = path.join(AUTH_DATA_PATH, 'session');
  const newPath = path.join(AUTH_DATA_PATH, 'session-default');
  try {
    if (!fs.existsSync(AUTH_DATA_PATH)) {
      fs.mkdirSync(AUTH_DATA_PATH, { recursive: true });
    }
    if (fs.existsSync(oldPath) && !fs.existsSync(newPath)) {
      fs.renameSync(oldPath, newPath);
      console.log('[WA] Migrasyon: .wwebjs_auth/session/ → session-default/');
    } else if (fs.existsSync(oldPath) && fs.existsSync(newPath)) {
      console.warn('[WA] Hem session/ hem session-default/ var; migrasyon atlandı (session-default kullanılıyor).');
    }
  } catch (err) {
    console.error('[WA] session migrasyon hatası:', err && err.message ? err.message : err);
  }
}

function validateTenantId(raw) {
  const id = String(raw || '').trim();
  if (!TENANT_ID_RE.test(id)) return null;
  return id;
}

function touch(session) {
  if (session) session.lastUsedAt = Date.now();
}

function durumPayload(session) {
  return {
    tenant_id: session.tenantId,
    bagli: session.ready,
    qr_bekliyor: Boolean(session.qr) && !session.ready,
    hazir: session.ready,
    kuyruk_uzunlugu: session.queue.length,
    kuyruk_isleniyor: session.queueBusy,
    status: session.status,
  };
}

function getOrCreateSession(tenantId) {
  let session = sessions.get(tenantId);
  if (session) return session;
  session = {
    tenantId,
    client: null,
    ready: false,
    qr: null,
    queue: [],
    queueBusy: false,
    initPromise: null,
    lastUsedAt: Date.now(),
    status: 'idle',
  };
  sessions.set(tenantId, session);
  return session;
}

async function kuyrukIsle(session) {
  if (!session || session.queueBusy || !session.ready || !session.client) return;
  session.queueBusy = true;
  touch(session);
  try {
    while (session.queue.length > 0 && session.ready && session.client) {
      const item = session.queue.shift();
      const phone = normalizeTelefon(item.telefon);
      const message = String(item.mesaj || '').trim();
      if (!phone || !message) {
        item._sonuc = { ok: false, error: 'telefon veya mesaj geçersiz' };
        continue;
      }
      const chatId = phone.endsWith('@c.us') ? phone : `${phone}@c.us`;
      try {
        const result = await session.client.sendMessage(chatId, message);
        item._sonuc = { ok: true, id: result && result.id ? result.id._serialized || null : null };
        console.log(`[WA:${session.tenantId}] Kuyruk gönderildi: ${phone}`);
      } catch (err) {
        item._sonuc = { ok: false, error: err.message || String(err) };
        console.error(`[WA:${session.tenantId}] Kuyruk gönderim hatası:`, err);
      }
      const minBekleme = 20000;
      const maxBekleme = 60000;
      const rastgeleBekleme = minBekleme + Math.floor(Math.random() * (maxBekleme - minBekleme));
      await new Promise((r) => setTimeout(r, rastgeleBekleme));
    }
  } finally {
    session.queueBusy = false;
  }
}

function attachHandlers(session) {
  const client = session.client;
  const tid = session.tenantId;

  client.on('qr', (qr) => {
    session.qr = qr;
    session.ready = false;
    session.status = 'qr';
    touch(session);
    console.log(`\n[WA:${tid}] Yeni QR kodu hazır.`);
    console.log(`[WA:${tid}] Tarayıcıdan açın: http://localhost:${PORT}/t/${tid}/qr-goster\n`);
  });

  client.on('authenticated', () => {
    console.log(`[WA:${tid}] Oturum doğrulandı.`);
  });

  client.on('ready', () => {
    session.ready = true;
    session.qr = null;
    session.status = 'ready';
    touch(session);
    console.log(`[WA:${tid}] WhatsApp bağlantısı hazır.`);
    kuyrukIsle(session);
  });

  client.on('auth_failure', (msg) => {
    session.ready = false;
    session.status = 'error';
    console.error(`[WA:${tid}] Kimlik doğrulama hatası:`, msg);
  });

  client.on('disconnected', (reason) => {
    session.ready = false;
    session.status = 'idle';
    console.warn(`[WA:${tid}] Bağlantı kesildi:`, reason);
  });
}

async function ensureClient(tenantId) {
  const session = getOrCreateSession(tenantId);
  touch(session);
  if (session.client && (session.ready || session.status === 'starting' || session.status === 'qr')) {
    if (session.initPromise) await session.initPromise;
    return session;
  }
  if (session.initPromise) {
    await session.initPromise;
    return session;
  }

  session.status = 'starting';
  session.initPromise = (async () => {
    const client = new Client({
      authStrategy: new LocalAuth({
        clientId: tenantId,
        dataPath: AUTH_DATA_PATH,
      }),
      puppeteer: buildPuppeteerOpts(),
    });
    session.client = client;
    attachHandlers(session);
    console.log(`[WA:${tenantId}] WhatsApp istemcisi başlatılıyor...`);
    await client.initialize();
    return session;
  })();

  try {
    await session.initPromise;
  } catch (err) {
    session.status = 'error';
    session.client = null;
    console.error(
      `[WA:${tenantId}] initialize hatası:`,
      err && err.message ? err.message : err
    );
    throw err;
  } finally {
    session.initPromise = null;
  }
  return session;
}

async function destroySession(tenantId) {
  const session = sessions.get(tenantId);
  if (!session) return;
  try {
    if (session.client) {
      await session.client.destroy();
    }
  } catch (err) {
    console.warn(`[WA:${tenantId}] destroy uyarısı:`, err && err.message ? err.message : err);
  }
  sessions.delete(tenantId);
  console.log(`[WA:${tenantId}] Oturum bellekten kaldırıldı (auth disk korunur).`);
}

function logDeprecatedAlias(routeName) {
  console.warn(`[WA] DEPRECATED alias kullanıldı: ${routeName} → /t/default${routeName === '/status' || routeName === '/health' ? '' : routeName.replace(/^\/(health|status)/, '') || ''} (tenant=default)`);
  console.warn(`[WA] DEPRECATED: ${routeName} — lütfen /t/default/... kullanın`);
}

async function handleQrGoster(session, res) {
  try {
    touch(session);
    if (session.ready) {
      return res.send(`<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <title>WhatsApp Bağlı</title>
  <style>
    body { font-family: sans-serif; text-align: center; padding: 40px; background: #111; color: #eee; }
    h1 { color: #25d366; }
  </style>
</head>
<body>
  <h1>WhatsApp zaten bağlı</h1>
  <p>Tenant: <code>${session.tenantId}</code> — mesaj göndermeye hazırsınız.</p>
</body>
</html>`);
    }

    if (!session.qr) {
      return res.status(503).send(`<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <title>QR Bekleniyor</title>
  <meta http-equiv="refresh" content="3">
  <style>
    body { font-family: sans-serif; text-align: center; padding: 40px; background: #111; color: #eee; }
  </style>
</head>
<body>
  <h1>QR kod henüz hazır değil</h1>
  <p>Tenant: <code>${session.tenantId}</code> — istemci başlatılıyor… Sayfa 3 saniyede yenilenecek.</p>
</body>
</html>`);
    }

    const dataUrl = await QRCode.toDataURL(session.qr, {
      width: 320,
      margin: 2,
      errorCorrectionLevel: 'M',
    });

    res.send(`<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <title>WhatsApp QR</title>
  <meta http-equiv="refresh" content="8">
  <style>
    body { font-family: sans-serif; text-align: center; padding: 32px; background: #0b141a; color: #e9edef; }
    h1 { color: #25d366; font-size: 1.4rem; margin-bottom: 8px; }
    p { color: #8696a0; margin: 8px 0; }
    img { background: #fff; padding: 12px; border-radius: 12px; margin-top: 16px; }
    .hint { font-size: 0.9rem; margin-top: 20px; }
    .fresh { color: #25d366; font-weight: 600; }
  </style>
</head>
<body>
  <h1>WhatsApp QR Kodu</h1>
  <p class="fresh">Tenant: <code>${session.tenantId}</code> — ${new Date().toLocaleString('tr-TR')} — HEMEN tarayın</p>
  <p>Telefonunuzdan <strong>Bağlı Cihazlar → Cihaz Bağla</strong> ile tarayın.</p>
  <img src="${dataUrl}" alt="WhatsApp QR Kodu" width="320" height="320">
  <p class="hint">QR ~20 sn'de değişebilir; sayfa 8 sn'de yenilenir. Eski görüntüyü taramayın.</p>
</body>
</html>`);
  } catch (err) {
    console.error(`[WA:${session.tenantId}] QR sayfası hatası:`, err);
    res.status(500).send('QR oluşturulamadı: ' + (err.message || String(err)));
  }
}

function tenantParam(req, res) {
  const tenantId = validateTenantId(req.params.tenantId);
  if (!tenantId) {
    res.status(400).json({
      ok: false,
      error: 'geçersiz tenantId (default | tenant_[a-z0-9_]+)',
    });
    return null;
  }
  return tenantId;
}

// --- Tenant-scoped routes ---

app.get('/t/:tenantId/durum', async (req, res) => {
  const tenantId = tenantParam(req, res);
  if (!tenantId) return;
  try {
    const session = await ensureClient(tenantId);
    res.json({ ok: true, ...durumPayload(session) });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message || String(err) });
  }
});

app.get('/t/:tenantId/qr-goster', async (req, res) => {
  const tenantId = tenantParam(req, res);
  if (!tenantId) return;
  try {
    const session = await ensureClient(tenantId);
    await handleQrGoster(session, res);
  } catch (err) {
    res.status(500).send('QR hatası: ' + (err.message || String(err)));
  }
});

app.post('/t/:tenantId/kuyruk-ekle', async (req, res) => {
  const tenantId = tenantParam(req, res);
  if (!tenantId) return;
  try {
    const session = await ensureClient(tenantId);
    const telefon = String(req.body.telefon || req.body.phone || '').trim();
    const mesaj = String(req.body.mesaj || req.body.message || '').trim();
    if (!telefon || !mesaj) {
      return res.status(400).json({ ok: false, error: 'telefon ve mesaj zorunlu.' });
    }
    const item = {
      id: Date.now() + '-' + Math.random().toString(36).slice(2, 8),
      telefon,
      mesaj,
      eklendi: new Date().toISOString(),
    };
    session.queue.push(item);
    touch(session);
    if (session.ready) {
      kuyrukIsle(session);
      return res.json({
        ok: true,
        kuyruga_eklendi: true,
        kuyruk_id: item.id,
        ...durumPayload(session),
        mesaj: 'Mesaj kuyruğa alındı, gönderiliyor.',
      });
    }
    res.status(202).json({
      ok: true,
      kuyruga_eklendi: true,
      kuyruk_id: item.id,
      ...durumPayload(session),
      mesaj: 'WhatsApp bağlı değil; QR tarandıktan sonra gönderilecek.',
    });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message || String(err) });
  }
});

app.post('/t/:tenantId/kuyruk-toplu-ekle', async (req, res) => {
  const tenantId = tenantParam(req, res);
  if (!tenantId) return;
  try {
    const session = await ensureClient(tenantId);
    const { liste } = req.body || {};
    if (!Array.isArray(liste) || liste.length === 0) {
      return res.status(400).json({ ok: false, mesaj: 'liste zorunlu (boş olamaz)' });
    }
    let eklenen = 0;
    for (const item of liste) {
      if (!item || !item.telefon || !item.mesaj) continue;
      session.queue.push({ telefon: item.telefon, mesaj: item.mesaj });
      eklenen++;
    }
    touch(session);
    kuyrukIsle(session);
    res.json({ ok: true, eklenen, kuyruk_uzunlugu: session.queue.length, tenant_id: tenantId });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message || String(err) });
  }
});

app.post('/t/:tenantId/send', async (req, res) => {
  const tenantId = tenantParam(req, res);
  if (!tenantId) return;
  try {
    const session = await ensureClient(tenantId);
    if (!session.ready || !session.client) {
      return res.status(503).json({
        ok: false,
        error: 'WhatsApp henüz hazır değil. QR kodu tarayın.',
        tenant_id: tenantId,
      });
    }
    const phone = normalizeTelefon(req.body.phone || req.body.telefon);
    const message = String(req.body.message || req.body.mesaj || '').trim();
    if (!phone || !message) {
      return res.status(400).json({ ok: false, error: 'phone ve message zorunlu.' });
    }
    const chatId = phone.endsWith('@c.us') ? phone : `${phone}@c.us`;
    touch(session);
    const result = await session.client.sendMessage(chatId, message);
    res.json({
      ok: true,
      id: result && result.id ? result.id._serialized || null : null,
      tenant_id: tenantId,
    });
  } catch (err) {
    console.error(`[WA:${tenantId}] Gönderim hatası:`, err);
    res.status(500).json({ ok: false, error: err.message || String(err) });
  }
});

// --- Global health (aktif oturum özeti; tenant-scoped değil) ---

app.get('/health', (_req, res) => {
  const active = [];
  for (const [id, s] of sessions) {
    active.push({ tenant_id: id, ready: s.ready, status: s.status });
  }
  res.json({
    ok: true,
    ready: sessions.has('default') ? sessions.get('default').ready : false,
    active_sessions: active.length,
    sessions: active,
  });
});

app.get('/status', async (_req, res) => {
  logDeprecatedAlias('/status');
  try {
    const session = await ensureClient('default');
    res.json({
      ok: true,
      ready: session.ready,
      has_qr: Boolean(session.qr),
      tenant_id: 'default',
    });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message || String(err) });
  }
});

// --- DEPRECATED aliases → default tenant ---

app.get('/durum', async (_req, res) => {
  logDeprecatedAlias('/durum');
  try {
    const session = await ensureClient('default');
    res.json({ ok: true, ...durumPayload(session), deprecated_alias: true });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message || String(err) });
  }
});

app.get('/qr-goster', async (_req, res) => {
  logDeprecatedAlias('/qr-goster');
  try {
    const session = await ensureClient('default');
    await handleQrGoster(session, res);
  } catch (err) {
    res.status(500).send('QR hatası: ' + (err.message || String(err)));
  }
});

app.post('/kuyruk-ekle', async (req, res) => {
  logDeprecatedAlias('/kuyruk-ekle');
  try {
    const session = await ensureClient('default');
    const telefon = String(req.body.telefon || req.body.phone || '').trim();
    const mesaj = String(req.body.mesaj || req.body.message || '').trim();
    if (!telefon || !mesaj) {
      return res.status(400).json({ ok: false, error: 'telefon ve mesaj zorunlu.' });
    }
    const item = {
      id: Date.now() + '-' + Math.random().toString(36).slice(2, 8),
      telefon,
      mesaj,
      eklendi: new Date().toISOString(),
    };
    session.queue.push(item);
    touch(session);
    if (session.ready) {
      kuyrukIsle(session);
      return res.json({
        ok: true,
        kuyruga_eklendi: true,
        kuyruk_id: item.id,
        ...durumPayload(session),
        deprecated_alias: true,
        mesaj: 'Mesaj kuyruğa alındı, gönderiliyor.',
      });
    }
    res.status(202).json({
      ok: true,
      kuyruga_eklendi: true,
      kuyruk_id: item.id,
      ...durumPayload(session),
      deprecated_alias: true,
      mesaj: 'WhatsApp bağlı değil; QR tarandıktan sonra gönderilecek.',
    });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message || String(err) });
  }
});

app.post('/kuyruk-toplu-ekle', async (req, res) => {
  logDeprecatedAlias('/kuyruk-toplu-ekle');
  try {
    const session = await ensureClient('default');
    const { liste } = req.body || {};
    if (!Array.isArray(liste) || liste.length === 0) {
      return res.status(400).json({ ok: false, mesaj: 'liste zorunlu (boş olamaz)' });
    }
    let eklenen = 0;
    for (const item of liste) {
      if (!item || !item.telefon || !item.mesaj) continue;
      session.queue.push({ telefon: item.telefon, mesaj: item.mesaj });
      eklenen++;
    }
    touch(session);
    kuyrukIsle(session);
    res.json({
      ok: true,
      eklenen,
      kuyruk_uzunlugu: session.queue.length,
      tenant_id: 'default',
      deprecated_alias: true,
    });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message || String(err) });
  }
});

app.post('/send', async (req, res) => {
  logDeprecatedAlias('/send');
  try {
    const session = await ensureClient('default');
    if (!session.ready || !session.client) {
      return res.status(503).json({
        ok: false,
        error: 'WhatsApp henüz hazır değil. QR kodu tarayın.',
      });
    }
    const phone = normalizeTelefon(req.body.phone || req.body.telefon);
    const message = String(req.body.message || req.body.mesaj || '').trim();
    if (!phone || !message) {
      return res.status(400).json({ ok: false, error: 'phone ve message zorunlu.' });
    }
    const chatId = phone.endsWith('@c.us') ? phone : `${phone}@c.us`;
    touch(session);
    const result = await session.client.sendMessage(chatId, message);
    res.json({
      ok: true,
      id: result && result.id ? result.id._serialized || null : null,
      tenant_id: 'default',
      deprecated_alias: true,
    });
  } catch (err) {
    console.error('[WA:default] Gönderim hatası:', err);
    res.status(500).json({ ok: false, error: err.message || String(err) });
  }
});

migrateDefaultSessionDir();

app.listen(PORT, () => {
  console.log(`[API] WhatsApp servisi http://localhost:${PORT}`);
  console.log(`[API] Tenant QR: http://localhost:${PORT}/t/{tenantId}/qr-goster`);
  console.log(`[API] Alias (DEPRECATED): http://localhost:${PORT}/qr-goster → default`);
  // Geriye uyum: default oturumu hemen ayağa kaldır (eski davranış)
  ensureClient('default').catch((err) => {
    console.error('[WA:default] initialize hatası:', err && err.message ? err.message : err);
  });
});
