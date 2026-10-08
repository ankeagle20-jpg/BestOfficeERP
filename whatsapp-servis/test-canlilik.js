/** Canlılık ve toparlama. Chrome, ağ ve gerçek oturum yok. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const {
  HEARTBEAT_MS,
  heartbeatSonuc,
  heartbeatAdim,
  planAdim,
  durumBakGovde,
  toparlaOturum,
  TOPARLAMA_MS,
  toparlaEsik,
  HEARTBEAT_SERI,
} = require("./canlilik");

function bos() {
  return {
    tenantId: "default",
    client: { getState() { return "CONNECTED"; } },
    ready: true,
    status: "ready",
    canli: true,
    bozuk: false,
    bozukNeden: "",
    toparlamaDurumu: "hazir",
    sonKontrolMs: 0,
    sonToparlamaMs: 0,
    qr: null,
  };
}

async function heartbeatDenemesi() {
  const bagli = bos();
  let okuma = 0;
  const iyi = await heartbeatAdim(bagli, () => {
    okuma += 1;
    return "CONNECTED";
  }, 5000, 50);
  assert.strictEqual(iyi.kontrol, true);
  assert.strictEqual(iyi.toparla, false);
  assert.strictEqual(bagli.canli, true);
  assert.strictEqual(bagli.bozuk, false);
  const govde = durumBakGovde(bagli);
  assert.strictEqual(okuma, 1);
  assert.strictEqual(govde.hazir, true);
  assert.strictEqual(govde.canli, true);
  assert.strictEqual(govde.son_kontrol_ms, 5000);

  const bozuk = bos();
  const hata = await heartbeatAdim(bozuk, () => {
    throw new Error("kapali");
  }, 6000, 50);
  assert.strictEqual(hata.toparla, true);
  assert.strictEqual(bozuk.canli, false);
  assert.strictEqual(bozuk.bozukNeden, "hata");
  assert.strictEqual(durumBakGovde(bozuk).hazir, false);

  const yavas = bos();
  const zaman = await heartbeatAdim(yavas, () => new Promise(() => {}), 7000, 30);
  assert.strictEqual(zaman.sonuc.bozuk_neden, "zaman_asimi");
  assert.strictEqual(yavas.canli, false);
  assert.strictEqual(heartbeatSonuc("OPENING", false, false).bozuk_neden, "durum");

  let spy = 0;
  const client = {
    getState() {
      spy += 1;
      return "CONNECTED";
    },
  };
  const oturum = bos();
  oturum.client = client;
  oturum.canli = false;
  oturum.ready = true;
  const bak = durumBakGovde(oturum);
  assert.strictEqual(spy, 0);
  assert.strictEqual(bak.hazir, false);
  assert.strictEqual(bak.bagli, false);
  assert.strictEqual(bak.canli, false);
}

function sicramaDenemesi() {
  const bas = 1_000_000;
  assert.strictEqual(planAdim(0, bas, HEARTBEAT_MS).sicrama, false);
  assert.strictEqual(planAdim(bas, bas + HEARTBEAT_MS, HEARTBEAT_MS).sicrama, false);
  const sic = planAdim(bas, bas + HEARTBEAT_MS * 3, HEARTBEAT_MS);
  assert.strictEqual(sic.sicrama, true);
  assert.ok(sic.gecen > HEARTBEAT_MS * 2);
}

async function toparlamaDenemesi() {
  const ucus = new Map();
  let kur = 0;
  let saat = 10_000_000;
  const oturum = bos();
  oturum.canli = false;
  oturum.bozuk = true;
  oturum.bozukNeden = "hata";
  const deps = {
    ucus,
    simdi: () => saat,
    aralikMs: TOPARLAMA_MS,
    log() {},
    destroy() {
      return new Promise((resolve) => setTimeout(resolve, 40));
    },
    initialize() {
      kur += 1;
      return new Promise((resolve) => setTimeout(() => resolve("ready"), 40));
    },
  };
  const a = toparlaOturum(oturum, deps);
  const b = toparlaOturum(oturum, deps);
  const [sol, sag] = await Promise.all([a, b]);
  assert.strictEqual(kur, 1);
  assert.strictEqual(sol.neden, "hazir");
  assert.strictEqual(sag.neden, "hazir");
  assert.strictEqual(oturum.canli, true);
  assert.strictEqual(durumBakGovde(oturum).hazir, true);

  kur = 0;
  oturum.canli = false;
  oturum.bozuk = true;
  oturum.status = "ready";
  const tekrar = await toparlaOturum(oturum, deps);
  assert.strictEqual(kur, 0);
  assert.strictEqual(tekrar.neden, "bekle");
  assert.strictEqual(oturum.toparlamaDurumu, "bekle");

  saat += TOPARLAMA_MS + 1;
  deps.initialize = () => {
    kur += 1;
    oturum.status = "qr";
    return "qr";
  };
  const qr = await toparlaOturum(oturum, deps);
  assert.strictEqual(kur, 1);
  assert.strictEqual(qr.neden, "qr");
  assert.strictEqual(oturum.status, "qr");
  saat += TOPARLAMA_MS * 3;
  const yagmur = await toparlaOturum(oturum, deps);
  assert.strictEqual(kur, 1);
  assert.strictEqual(yagmur.neden, "qr");
  assert.strictEqual(durumBakGovde(oturum).hazir, false);
}

function kaynakDenemesi() {
  const src = fs.readFileSync(path.join(__dirname, "index.js"), "utf8");
  const i = src.indexOf("function durumBak");
  const j = src.indexOf("function durumPayload");
  assert.ok(i > 0 && j > i);
  const govde = src.slice(i, j);
  assert.strictEqual(govde.includes("getState"), false);
  assert.ok(govde.includes("durumBakGovde"));
  const bak = src.indexOf("app.get('/t/:tenantId/durum-bak'");
  const uy = src.indexOf("app.post('/t/:tenantId/uyandir'");
  assert.ok(bak > 0 && uy > bak);
  const bakGovde = src.slice(bak, uy);
  assert.strictEqual(bakGovde.includes("getState"), false);
  assert.strictEqual(bakGovde.includes("heartbeatAdim"), false);
  const durum = src.indexOf("app.get('/t/:tenantId/durum'");
  const qr = src.indexOf("app.get('/t/:tenantId/qr-goster'");
  assert.ok(durum > 0 && qr > durum);
  assert.ok(src.slice(durum, qr).includes("durumCanli"));
  const fn = src.indexOf("async function durumCanli");
  assert.ok(fn > 0 && src.slice(fn, fn + 800).includes("heartbeatAdim"));
  assert.ok(src.includes("heartbeatKarar"));
  assert.ok(src.includes("yariyiGeriAl"));
  assert.ok(src.includes("kuyruk-sonuc"));
}

function esikDenemesi() {
  const adim = { toparla: true, sonuc: { bozuk_neden: "hata" } };
  const oturum = { gonderiliyor: true, ready: true, status: "ready" };
  const bir = toparlaEsik(oturum, adim, 0);
  assert.strictEqual(bir.toparla, false);
  assert.strictEqual(bir.seri, 1);
  assert.strictEqual(toparlaEsik(oturum, adim, 1).toparla, false);
  assert.strictEqual(toparlaEsik(oturum, adim, HEARTBEAT_SERI - 1).toparla, true);
  const zaman = toparlaEsik(oturum, { toparla: true, sonuc: { bozuk_neden: "zaman_asimi" } }, 0);
  assert.strictEqual(zaman.toparla, true);
  assert.strictEqual(toparlaEsik({ gonderiliyor: false }, adim, 0).toparla, true);
  assert.strictEqual(toparlaEsik(oturum, { toparla: false }, 2).seri, 0);
}

heartbeatDenemesi()
  .then(() => sicramaDenemesi())
  .then(() => toparlamaDenemesi())
  .then(() => kaynakDenemesi())
  .then(() => esikDenemesi())
  .then(() => {
    console.log("CASE canlilik ok");
  })
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
