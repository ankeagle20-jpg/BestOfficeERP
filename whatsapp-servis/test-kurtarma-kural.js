/** Dongu kurtarma: sahte puppeteer/baslatma ile. Chrome ve ag yok. */
const assert = require("assert");
const {
  MAKS_DENEME,
  hataSinifi,
  kilitAl,
  kilitBirak,
  pidKapatilabilir,
  kurtarmaAkisiCalistir,
} = require("./kurtarma-kural");
const { durumBakGovde } = require("./canlilik");

assert.strictEqual(hataSinifi("The browser is already running for x"), "already_running");
assert.strictEqual(hataSinifi("Protocol error"), "diger");
assert.strictEqual(hataSinifi(""), "yok");
assert.strictEqual(hataSinifi(null), "yok");
assert.strictEqual(MAKS_DENEME, 3);

// PID guvenligi: baska PID'e dokunma
assert.strictEqual(pidKapatilabilir(1234, { kendiPid: 999, profilEslesir: true }), true);
assert.strictEqual(pidKapatilabilir(999, { kendiPid: 999, profilEslesir: true }), false);
assert.strictEqual(pidKapatilabilir(1234, { kendiPid: 999, profilEslesir: false }), false);
assert.strictEqual(pidKapatilabilir(0, { kendiPid: 999, profilEslesir: true }), false);
assert.strictEqual(pidKapatilabilir("x", { kendiPid: 999, profilEslesir: true }), false);

// Ikinci tetikleme kilidi
const sahteOturum = { kurtarmaCalisiyor: false };
assert.strictEqual(kilitAl(sahteOturum), true);
assert.strictEqual(kilitAl(sahteOturum), false);
kilitBirak(sahteOturum);
assert.strictEqual(kilitAl(sahteOturum), true);
kilitBirak(sahteOturum);

function akisaSayacli(baslatFn) {
  const sayac = { baslat: 0, kapat: 0, bekle: 0, durumlar: [] };
  const deps = {
    baslat: async () => {
      sayac.baslat += 1;
      return baslatFn(sayac.baslat);
    },
    kapat: async () => {
      sayac.kapat += 1;
    },
    bekle: async () => {
      sayac.bekle += 1;
    },
    bekleMs: 1,
    durumYaz: (sinif, deneme) => sayac.durumlar.push([sinif, deneme]),
    log: () => {},
  };
  return { deps, sayac };
}

async function senaryolar() {
  // 1) already running -> kapat -> tekrar -> basari
  {
    const { deps, sayac } = akisaSayacli((n) => {
      if (n === 1) throw new Error("The browser is already running for dir");
      return "ok";
    });
    const r = await kurtarmaAkisiCalistir(deps);
    assert.deepStrictEqual(
      { ok: r.ok, deneme: r.deneme, baslat: sayac.baslat, kapat: sayac.kapat, bekle: sayac.bekle },
      { ok: true, deneme: 2, baslat: 2, kapat: 1, bekle: 1 }
    );
  }
  // 2) 3 deneme siniri: sonsuz dongu yok
  {
    const { deps, sayac } = akisaSayacli(() => {
      throw new Error("The browser is already running");
    });
    const r = await kurtarmaAkisiCalistir(deps);
    assert.strictEqual(r.ok, false);
    assert.strictEqual(r.sinif, "already_running");
    assert.strictEqual(sayac.baslat, 3);
    assert.strictEqual(sayac.kapat, 2); // son denemeden sonra kapatma yok
    assert.deepStrictEqual(
      sayac.durumlar.map((d) => d[0]),
      ["already_running", "already_running", "already_running"]
    );
  }
  // 3) baska hata: kapatma yok, tek deneme
  {
    const { deps, sayac } = akisaSayacli(() => {
      throw new Error("Failed to launch the browser process");
    });
    const r = await kurtarmaAkisiCalistir(deps);
    assert.strictEqual(r.ok, false);
    assert.strictEqual(r.sinif, "diger");
    assert.strictEqual(sayac.baslat, 1);
    assert.strictEqual(sayac.kapat, 0);
  }
  // 4) ilk denemede basari: kapatma yok
  {
    const { deps, sayac } = akisaSayacli(() => "ok");
    const r = await kurtarmaAkisiCalistir(deps);
    assert.strictEqual(r.ok, true);
    assert.strictEqual(sayac.baslat, 1);
    assert.strictEqual(sayac.kapat, 0);
  }
  // 5) durum teshis govdesi
  const govde = durumBakGovde({
    tenantId: "default",
    canli: false,
    ready: false,
    status: "error",
    sonKontrolMs: 0,
    bozukNeden: "hata",
    toparlamaDurumu: "toparlama_basarisiz",
    sonToparlamaHataSinifi: "already_running",
    toparlamaDenemeSayisi: 3,
    kurtarmaCalisiyor: false,
  });
  assert.deepStrictEqual(govde.durum_teshis, {
    son_toparlama_hatasi_sinifi: "already_running",
    toparlama_deneme_sayisi: 3,
    kurtarma_calisiyor: false,
  });
}

senaryolar()
  .then(() => {
    console.log("CASE kurtarma kural ok");
  })
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
