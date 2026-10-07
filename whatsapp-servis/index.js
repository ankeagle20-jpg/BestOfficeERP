const express = require('express');
const cors = require('cors');
const path = require('path');
const fs = require('fs');
const QRCode = require('qrcode');
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const { idleKapanirMi, tekUcus } = require('./oturum-kural');
const { kalemEkle, gonderGovde } = require('./kuyruk-kural');

const WA_LOG_PATH = path.join(__dirname, 'wa-servis.log');

function waLog(event, detail) {
  const safe = String(detail == null ? '' : detail)
    .replace(/\d{8,}/g, '[num]')
    .replace(/[\r\n]/g, ' ')
    .slice(0, 160);
  const line = `${new Date().toISOString()} ${event}${safe ? ' ' + safe : ''}`;
  try {
    fs.appendFileSync(WA_LOG_PATH, line + '\n');
  } catch (_e) {}
  console.log(`[WA] ${event}${safe ? ' ' + safe : ''}`);
}

function loadLocalEnv() {
  const envPath = path.join(__dirname, '.env');
  let text;
  try {
    text = fs.readFileSync(envPath, 'utf8');
  } catch (err) {
    if (err && err.code === 'ENOENT') return;
    console.error('[WA] .env okunamadı. Servis başlamadı.');
    process.exit(1);
  }
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue;
    let val = trimmed.slice(eq + 1).trim();
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1);
    }
    if (process.env[key] === undefined) process.env[key] = val;
  }
}

loadLocalEnv();

const PORT = process.env.PORT || 3001;
const AUTH_DATA_PATH = path.join(__dirname, '.wwebjs_auth');
const TENANT_ID_RE = /^(default|tenant_[a-z0-9_]+)$/;

/** Eşzamanlı Chrome/Puppeteer üst sınırı (Render önerisi: 2). */
const WA_MAX_CONCURRENT_CHROME = Math.max(
  1,
  parseInt(String(process.env.WA_MAX_CONCURRENT_CHROME || '2'), 10) || 2
);
/** Idle destroy eşiği (ms). Varsayılan 20 dk. Tüm kiracılar (default dahil) tabi. */
const WA_IDLE_MS = Math.max(
  1000,
  parseInt(String(process.env.WA_IDLE_MS || String(20 * 60 * 1000)), 10) || 20 * 60 * 1000
);
/** Idle kontrol periyodu (ms). Varsayılan 60 sn. */
const WA_IDLE_CHECK_MS = Math.max(
  1000,
  parseInt(String(process.env.WA_IDLE_CHECK_MS || '60000'), 10) || 60000
);
/** Flask ile paylaşılan secret. Sabit varsayılan yok; kısa/boş değerle süreç açılmaz. */
const WA_INTERNAL_TOKEN = String(process.env.WA_INTERNAL_TOKEN || '').trim();
if (WA_INTERNAL_TOKEN.length < 32) {
  console.error(
    '[WA] WA_INTERNAL_TOKEN yok, boş veya 32 karakterden kısa. Servis başlamadı.'
  );
  process.exit(1);
}

const app = express();
app.use(cors());
app.use(express.json());

/** Tarayıcıdan doğrudan erişimi engelle — yalnızca Flask (header token) geçer. */
function requireInternalToken(req, res, next) {
  const got = String(req.headers['x-wa-internal-token'] || '').trim();
  if (!WA_INTERNAL_TOKEN || got !== WA_INTERNAL_TOKEN) {
    return res.status(401).json({
      ok: false,
      error: 'Yetkisiz: geçerli X-WA-Internal-Token gerekli.',
    });
  }
  return next();
}

// Tünel ve LAN dahil her yol token ister. /health ve 410 alias'ları da açıkta kalmasın.
app.use(requireInternalToken);

/** @type {Map<string, object>} */
const sessions = new Map();
/** Aynı kiracı için tek Chrome başlatma. */
const ensureUcus = new Map();
/** Şu an Chrome tutan oturum sayısı (starting/qr/ready). */
let activeChromeCount = 0;

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
      '--disable-features=IsolateOrigins,site-per-process',
      '--disable-site-isolation-trials',
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

function durumBak(sessionsMap, tenantId) {
  const session = sessionsMap && sessionsMap.get ? sessionsMap.get(tenantId) : null;
  if (!session) return { ok: true, bagli: false, hazir: false, durum: 'yok' };
  return {
    ok: true,
    bagli: Boolean(session.ready),
    hazir: Boolean(session.ready),
    durum: session.status || '',
  };
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
    chromeHeld: false,
    // Idle destroy'a tabi (default dahil). LocalAuth diski korunur; ihtiyaçta yeniden açılır.
    pinKeepAlive: false,
    gonderimDurum: new Map(),
  };
  sessions.set(tenantId, session);
  return session;
}

function concurrentLimitError() {
  const err = new Error(
    `WhatsApp eşzamanlı oturum limiti doldu (max ${WA_MAX_CONCURRENT_CHROME}). ` +
      'Lütfen daha sonra tekrar deneyin veya kullanılmayan oturumların kapanmasını bekleyin.'
  );
  err.code = 'WA_CONCURRENT_LIMIT';
  err.statusCode = 503;
  return err;
}

function sendEnsureError(res, err, asHtml) {
  const code = err && err.code;
  const status = (err && err.statusCode) || (code === 'WA_CONCURRENT_LIMIT' ? 503 : 500);
  const msg = (err && err.message) || String(err);
  if (asHtml) {
    return res.status(status).send(
      `<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8"><title>WhatsApp</title></head>` +
        `<body style="font-family:sans-serif;padding:40px;background:#111;color:#eee">` +
        `<h1 style="color:#ef5350">WhatsApp servisi meşgul</h1><p>${msg}</p></body></html>`
    );
  }
  return res.status(status).json({
    ok: false,
    error: msg,
    code: code || 'WA_ERROR',
    active_chrome: activeChromeCount,
    max_concurrent_chrome: WA_MAX_CONCURRENT_CHROME,
  });
}

async function kuyrukIsle(session) {
  if (!session || session.queueBusy || !session.ready || !session.client) return;
  session.queueBusy = true;
  touch(session);
  try {
    while (session.queue.length > 0 && session.ready && session.client) {
      const item = session.queue.shift();
      item.durum = 'gonderiliyor';
      const phone = normalizeTelefon(item.telefon);
      const message = String(item.mesaj || '').trim();
      let beklenir = false;
      try {
        if (!phone || !message) {
          item._sonuc = { ok: false, error: 'telefon veya mesaj geçersiz' };
          item.durum = 'basarisiz';
        } else {
          beklenir = true;
          const chatId = phone.endsWith('@c.us') ? phone : `${phone}@c.us`;
          const result = await gonderGovde(item, (icerik, opts) => {
            if (opts) return session.client.sendMessage(chatId, icerik, opts);
            return session.client.sendMessage(chatId, icerik);
          }, (mime, veri, ad) => new MessageMedia(mime, veri, ad));
          item._sonuc = { ok: true, id: result && result.id ? result.id._serialized || null : null };
          item.durum = 'gonderildi';
          console.log(`[WA:${session.tenantId}] Kuyruk gönderildi`);
        }
      } catch (err) {
        item._sonuc = { ok: false, error: err.message || String(err) };
        item.durum = 'basarisiz';
        console.error(`[WA:${session.tenantId}] Kuyruk gönderim hatası:`, err);
      } finally {
        item.ek = null;
      }
      if (!beklenir) continue;
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
    waLog('qr', tid);
  });

  client.on('authenticated', () => {
    waLog('authenticated', tid);
  });

  client.on('ready', () => {
    session.ready = true;
    session.qr = null;
    session.status = 'ready';
    touch(session);
    waLog('ready', tid);
    kuyrukIsle(session);
  });

  client.on('auth_failure', (msg) => {
    session.ready = false;
    session.status = 'error';
    waLog('auth_failure', msg && msg.message ? msg.message : msg);
  });

  client.on('loading_screen', (percent) => {
    waLog('loading_screen', String(percent));
  });

  client.on('disconnected', (reason) => {
    session.ready = false;
    session.status = 'idle';
    const neden = String(reason || '');
    waLog('disconnected', neden || tid);
    if (neden.toUpperCase() === 'LOGOUT') waLog('cikis', tid);
  });
}

async function ensureClient(tenantId) {
  return tekUcus(ensureUcus, String(tenantId), () => ensureClientGovde(tenantId));
}

async function ensureClientGovde(tenantId) {
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

  // Yeni Chrome slotu gerekli — limit doluysa 503
  if (!session.chromeHeld) {
    if (activeChromeCount >= WA_MAX_CONCURRENT_CHROME) {
      console.warn(
        `[WA:${tenantId}] Eşzamanlı limit (${WA_MAX_CONCURRENT_CHROME}), active=${activeChromeCount}`
      );
      throw concurrentLimitError();
    }
    activeChromeCount += 1;
    session.chromeHeld = true;
  }

  session.status = 'starting';
  session.initPromise = (async () => {
    const client = new Client({
      authStrategy: new LocalAuth({
        clientId: tenantId,
        dataPath: AUTH_DATA_PATH,
      }),
      userAgent:
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
      webVersionCache: {
        type: 'remote',
        remotePath:
          'https://raw.githubusercontent.com/wppconnect-team/wa-version/main/html/{version}.html',
        strict: false,
      },
      puppeteer: buildPuppeteerOpts(),
    });
    session.client = client;
    attachHandlers(session);
    waLog('uyandirma', tenantId);
    waLog(
      'initialize_basladi',
      `${tenantId} chrome ${activeChromeCount}/${WA_MAX_CONCURRENT_CHROME} webcache=remote`
    );
    await client.initialize();
    let socketState = '';
    try {
      socketState = await client.getState();
    } catch (_e) {
      socketState = 'okunamadi';
    }
    waLog('initialize_bitti', `${tenantId} durum=${socketState || 'bos'}`);
    return session;
  })();

  try {
    await session.initPromise;
  } catch (err) {
    session.status = 'error';
    session.client = null;
    if (session.chromeHeld) {
      session.chromeHeld = false;
      activeChromeCount = Math.max(0, activeChromeCount - 1);
    }
    waLog(
      'initialize_hata',
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
  if (session.chromeHeld) {
    session.chromeHeld = false;
    activeChromeCount = Math.max(0, activeChromeCount - 1);
  }
  sessions.delete(tenantId);
  console.log(
    `[WA:${tenantId}] Oturum bellekten kaldırıldı (auth disk korunur). chrome=${activeChromeCount}/${WA_MAX_CONCURRENT_CHROME}`
  );
}

async function idleDestroySweep() {
  const now = Date.now();
  const victims = [];
  for (const [id, s] of sessions) {
    if (!idleKapanirMi(Object.assign({ tenantId: id }, s), now, WA_IDLE_MS)) continue;
    victims.push(id);
  }
  for (const id of victims) {
    waLog('idle_destroy', id);
    try {
      await destroySession(id);
    } catch (err) {
      waLog('idle_destroy_hata', err && err.message ? err.message : err);
    }
  }
}

function diskOturumVar(tenantId) {
  const dir = path.join(AUTH_DATA_PATH, 'session-' + tenantId);
  try {
    return fs.existsSync(dir) && fs.readdirSync(dir).length > 0;
  } catch (_err) {
    return false;
  }
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

app.get('/t/:tenantId/durum-bak', (req, res) => {
  const tenantId = tenantParam(req, res);
  if (!tenantId) return;
  res.json(durumBak(sessions, tenantId));
});

app.post('/t/:tenantId/uyandir', async (req, res) => {
  const tenantId = tenantParam(req, res);
  if (!tenantId) return;
  const istenen = parseInt(String((req.body || {}).bekle_ms || ''), 10);
  const bekleMs = Math.min(20000, Math.max(1000, Number.isFinite(istenen) ? istenen : 20000));
  const mevcut = sessions.get(tenantId);
  if ((!mevcut || !mevcut.ready) && !diskOturumVar(tenantId)) {
    waLog('uyandirma_yok', tenantId);
    return res.json({ ok: true, bagli: false, hazir: false, neden: 'qr', qr_bekliyor: true });
  }
  try {
    const session = await ensureClient(tenantId);
    const son = Date.now() + bekleMs;
    while (!session.ready && session.status !== 'qr' && session.status !== 'error' && Date.now() < son) {
      await new Promise((r) => setTimeout(r, 250));
    }
    if (session.ready) {
      waLog('uyandirma_hazir', tenantId);
      return res.json({ ok: true, bagli: true, hazir: true, neden: '' });
    }
    if (session.status === 'qr' || session.qr) {
      waLog('uyandirma_qr', tenantId);
      return res.json({ ok: true, bagli: false, hazir: false, neden: 'qr', qr_bekliyor: true });
    }
    waLog('uyandirma_olmadi', tenantId);
    return res.json({ ok: true, bagli: false, hazir: false, neden: 'oturum' });
  } catch (err) {
    waLog('uyandirma_hata', err && err.message ? err.message : err);
    return res.json({ ok: false, bagli: false, hazir: false, neden: 'oturum' });
  }
});

app.get('/t/:tenantId/durum', async (req, res) => {
  const tenantId = tenantParam(req, res);
  if (!tenantId) return;
  try {
    const session = await ensureClient(tenantId);
    res.json({ ok: true, ...durumPayload(session) });
  } catch (err) {
    return sendEnsureError(res, err, false);
  }
});

app.get('/t/:tenantId/qr-goster', async (req, res) => {
  const tenantId = tenantParam(req, res);
  if (!tenantId) return;
  try {
    const session = await ensureClient(tenantId);
    await handleQrGoster(session, res);
  } catch (err) {
    return sendEnsureError(res, err, true);
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
    return sendEnsureError(res, err, false);
  }
});

async function numaraKayitliMi(session, telefon) {
  const phone = normalizeTelefon(telefon);
  if (!phone || phone.length < 11 || phone.length > 15) {
    return { ok: false, kayitli: false, neden: 'gecersiz' };
  }
  if (!session || !session.ready || !session.client || typeof session.client.isRegisteredUser !== 'function') {
    return { ok: false, kayitli: false, neden: 'hazir_degil' };
  }
  try {
    const kayitli = await session.client.isRegisteredUser(`${phone}@c.us`);
    return { ok: true, kayitli: Boolean(kayitli), neden: '' };
  } catch (_err) {
    return { ok: false, kayitli: false, neden: 'hata' };
  }
}

function kuyrukKalemleri(session, liste) {
  if (!session.gonderimDurum) session.gonderimDurum = new Map();
  return kalemEkle(session.gonderimDurum, session.queue, liste);
}

app.post('/t/:tenantId/numara-kontrol', async (req, res) => {
  const tenantId = tenantParam(req, res);
  if (!tenantId) return;
  try {
    const session = await ensureClient(tenantId);
    const sonuc = await numaraKayitliMi(session, (req.body || {}).telefon);
    res.json(sonuc);
  } catch (err) {
    return sendEnsureError(res, err, false);
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
    const sonuc = kuyrukKalemleri(session, liste);
    touch(session);
    kuyrukIsle(session);
    res.json({
      ok: true,
      eklenen: sonuc.eklenen,
      tekrar: sonuc.tekrar,
      oge: sonuc.oge,
      kuyruk_uzunlugu: session.queue.length,
      tenant_id: tenantId,
    });
  } catch (err) {
    return sendEnsureError(res, err, false);
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
    if (err && err.code === 'WA_CONCURRENT_LIMIT') return sendEnsureError(res, err, false);
    console.error(`[WA:${tenantId}] Gönderim hatası:`, err);
    res.status(500).json({ ok: false, error: err.message || String(err) });
  }
});

// --- Global health (aktif oturum özeti; tenant-scoped değil) ---

app.get('/health', (_req, res) => {
  const active = [];
  for (const [id, s] of sessions) {
    active.push({
      tenant_id: id,
      ready: s.ready,
      status: s.status,
      chrome_held: Boolean(s.chromeHeld),
      pin_keep_alive: Boolean(s.pinKeepAlive),
      last_used_at: s.lastUsedAt || null,
      idle_ms: s.lastUsedAt ? Date.now() - s.lastUsedAt : null,
    });
  }
  res.json({
    ok: true,
    ready: sessions.has('default') ? sessions.get('default').ready : false,
    active_sessions: active.length,
    active_chrome: activeChromeCount,
    max_concurrent_chrome: WA_MAX_CONCURRENT_CHROME,
    idle_ms: WA_IDLE_MS,
    idle_check_ms: WA_IDLE_CHECK_MS,
    sessions: active,
  });
});

/** Eski tenant'sız alias'lar kaldırıldı (Aşama 6) — 410 Gone */
function goneAlias(req, res) {
  return res.status(410).json({
    ok: false,
    error:
      'Bu endpoint kaldırıldı (410 Gone). Tenant-scoped yol kullanın: /t/{tenantId}/…',
    path: req.path,
    ornek: '/t/default/durum',
  });
}

app.get('/status', goneAlias);
app.get('/durum', goneAlias);
app.get('/qr-goster', goneAlias);
app.post('/kuyruk-ekle', goneAlias);
app.post('/kuyruk-toplu-ekle', goneAlias);
app.post('/send', goneAlias);

migrateDefaultSessionDir();

function loopbackIstegi(req) {
  const ip = String((req.socket && req.socket.remoteAddress) || '');
  return ip === '127.0.0.1' || ip === '::1' || ip === '::ffff:127.0.0.1';
}

/** Tünelin baktığı 3001 dışında. QR yalnız bu süreçten, 127.0.0.1:3002. */
const YEREL_QR_PORT = 3002;
const yerelQrApp = express();
yerelQrApp.get('/', async (req, res) => {
  if (!loopbackIstegi(req)) return res.status(404).end();
  try {
    const session = await ensureClient('default');
    return handleQrGoster(session, res);
  } catch (err) {
    return sendEnsureError(res, err, true);
  }
});

function servisiBaslat() {
app.listen(PORT, '127.0.0.1', () => {
  console.log(`[API] WhatsApp servisi http://127.0.0.1:${PORT}`);
  console.log(`[API] Tenant API: http://localhost:${PORT}/t/{tenantId}/…`);
  console.log(`[API] QR (Flask proxy): /whatsapp/qr-ac → /t/{tenantId}/qr-goster`);
  console.log(
    `[WA] Kapasite: max_chrome=${WA_MAX_CONCURRENT_CHROME} idle_ms=${WA_IDLE_MS} idle_check_ms=${WA_IDLE_CHECK_MS} headless=${String(process.env.WA_HEADLESS || 'false')}`
  );
  console.log('[WA] Internal token koruması: AÇIK (X-WA-Internal-Token her yolda zorunlu)');
  setInterval(() => {
    idleDestroySweep().catch((err) => {
      console.warn('[WA] Idle sweep hatası:', err && err.message ? err.message : err);
    });
  }, WA_IDLE_CHECK_MS);
  // Eager start (opsiyonel geriye uyum): default'u hemen ayağa kaldırır.
  // pinKeepAlive yok → WA_IDLE_MS sonra idle destroy edilir; sonraki istekte
  // LocalAuth disk oturumu ile QR'sız yeniden bağlanır.
  ensureClient('default').catch((err) => {
    const msg = err && err.message ? err.message : err;
    waLog('acilis_hata', msg);
    console.error('[WA:default] initialize hatası:', msg);
    process.exit(1);
  });
});

yerelQrApp.listen(YEREL_QR_PORT, '127.0.0.1', () => {
  waLog('yerel_qr', `127.0.0.1:${YEREL_QR_PORT}`);
});
}

module.exports = { kuyrukKalemleri, numaraKayitliMi, durumBak };

if (require.main === module) {
  servisiBaslat();
}
