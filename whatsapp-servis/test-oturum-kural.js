/** Boşta kapanma ve tek-uçuş. Chrome ve ağ yok. */
const assert = require("assert");
const { idleKapanirMi, tekUcus } = require("./oturum-kural");

const SIMDI = 1_000_000;
const IDLE = 20 * 60 * 1000;

function oturum(fazla) {
  return Object.assign(
    {
      tenantId: "tenant_a",
      pinKeepAlive: false,
      client: {},
      chromeHeld: true,
      queue: [],
      queueBusy: false,
      status: "ready",
      initPromise: null,
      lastUsedAt: SIMDI - IDLE - 1,
    },
    fazla || {}
  );
}

assert.strictEqual(idleKapanirMi(oturum({ tenantId: "default" }), SIMDI, IDLE), false);
assert.strictEqual(idleKapanirMi(oturum(), SIMDI, IDLE), true);
assert.strictEqual(idleKapanirMi(oturum({ lastUsedAt: SIMDI - 1000 }), SIMDI, IDLE), false);
assert.strictEqual(idleKapanirMi(oturum({ pinKeepAlive: true }), SIMDI, IDLE), false);
assert.strictEqual(idleKapanirMi(oturum({ queue: [{}] }), SIMDI, IDLE), false);
assert.strictEqual(idleKapanirMi(oturum({ client: null, chromeHeld: false }), SIMDI, IDLE), false);

async function tekUcusDenemesi() {
  const harita = new Map();
  let basladi = 0;
  const is = () =>
    new Promise((resolve) => {
      basladi += 1;
      setTimeout(() => resolve("tek"), 40);
    });
  const a = tekUcus(harita, "default", is);
  const b = tekUcus(harita, "default", is);
  const [sol, sag] = await Promise.all([a, b]);
  assert.strictEqual(basladi, 1);
  assert.strictEqual(sol, "tek");
  assert.strictEqual(sag, "tek");
  let ikinci = 0;
  await tekUcus(harita, "default", () => {
    ikinci += 1;
    return "sonra";
  });
  assert.strictEqual(ikinci, 1);
}

tekUcusDenemesi()
  .then(() => {
    console.log("CASE oturum kural ok");
  })
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
