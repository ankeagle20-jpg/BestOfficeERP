/** Dongu kurtarma: sahte puppeteer/baslatma ile. Chrome ve ag yok. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const {
  MAKS_DENEME,
  SICRAMA_ESIK_MS,
  KURTARMA_ARA_MS,
  hataSinifi,
  kilitAl,
  kilitBirak,
  pidKapatilabilir,
  kurtarmaAkisiCalistir,
  chromeKokKaydet,
  komutBuProfil,
  yetimSec,
  chromeSatiriOku,
  sicramaKarar,
  yetimUygula,
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

async function sahteYetim(oturum, liste, opt) {
  const o = opt || {};
  const log = [];
  const canli = new Set(
    (liste || []).filter((p) => p.yasiyor !== false).map((p) => p.pid)
  );
  const nazik = [];
  const zorla = [];
  const bekle = [];
  await yetimUygula(oturum, {
    kendiPid: 100,
    simdi: 70000,
    listele: async () => (o.wmi ? { wmiHata: true } : liste),
    log: (e, d) => log.push(e + " " + d),
    nazik: async (pids) => {
      nazik.push.apply(nazik, pids);
      if (o.nazikOlur) pids.forEach((p) => canli.delete(p));
    },
    bekle: async (ms) => {
      bekle.push(ms);
    },
    yasiyor: (pid) => canli.has(pid),
    dogrula: async (pid) =>
      o.dogrula ? o.dogrula(pid) : { profil: true, wmiHata: false },
    zorla: async (pids) => {
      zorla.push.apply(zorla, pids);
      return pids;
    },
    lockKontrol: async () => o.lock || { var: false, tutan: false },
  });
  return { log, nazik, zorla, bekle };
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

  const servisProfil =
    "C:\\svc\\.wwebjs_auth\\session-default\\Default";
  const kisisel =
    "C:\\Users\\X\\AppData\\Local\\Google\\Chrome\\User Data\\Default";
  assert.strictEqual(komutBuProfil(servisProfil, "default"), true);
  assert.strictEqual(komutBuProfil(kisisel, "default"), false);
  assert.strictEqual(komutBuProfil("C:\\svc\\session-default2\\x", "default"), false);
  assert.strictEqual(komutBuProfil("C:\\svc\\session-tenant_a\\x", "default"), false);
  assert.strictEqual(SICRAMA_ESIK_MS, 60000);

  const kayitli = { tenantId: "default", client: { pupBrowser: true }, chromeKokPid: 0 };
  assert.strictEqual(chromeKokKaydet(kayitli, 501, 10000), true);
  kayitli.client = null;
  assert.strictEqual(kayitli.chromeKokPid, 501);
  assert.strictEqual(kayitli.chromeKokMs, 10000);

  const karisik = [
    { pid: 501, ppid: 100, komut: servisProfil, yasiyor: true, ppidYasiyor: true },
    { pid: 900, ppid: 50, komut: kisisel, yasiyor: true, ppidYasiyor: true },
  ];
  const secA = yetimSec(kayitli, karisik, { kendiPid: 100, simdi: 70000 });
  assert.deepStrictEqual(secA.kapat, [501]);
  assert.strictEqual(secA.bulundu[0].yasSn, 60);
  assert.strictEqual(secA.bulundu[0].ebeveyn, "servis");
  assert.strictEqual(secA.kapat.indexOf(900), -1);

  const kayitsiz = { tenantId: "default", chromeKokPid: 0 };
  const yetimler = [
    { pid: 502, ppid: 7, komut: servisProfil, yasiyor: true, ppidYasiyor: false },
    { pid: 900, ppid: 50, komut: kisisel, yasiyor: true, ppidYasiyor: true },
    { pid: 503, ppid: 8, komut: servisProfil, yasiyor: true, ppidYasiyor: true },
  ];
  const secB = yetimSec(kayitsiz, yetimler, { kendiPid: 100, simdi: 70000 });
  assert.deepStrictEqual(secB.kapat, [502]);
  assert.strictEqual(secB.kapat.indexOf(900), -1);
  assert.strictEqual(secB.kapat.indexOf(503), -1);

  const secW = yetimSec(kayitsiz, { wmiHata: true }, { kendiPid: 100 });
  assert.deepStrictEqual(secW.kapat, []);
  assert.strictEqual(secW.atla[0].sebep, "wmi");

  const yanlis = {
    tenantId: "default",
    chromeKokPid: 777,
    chromeKokMs: 10000,
  };
  const secP = yetimSec(
    yanlis,
    [{ pid: 777, ppid: 100, komut: kisisel, yasiyor: true, ppidYasiyor: true }],
    { kendiPid: 100, simdi: 70000 }
  );
  assert.deepStrictEqual(secP.kapat, []);
  assert.strictEqual(secP.atla[0].sebep, "profil");

  const satirlar = chromeSatiriOku("10|100|0\n11|100|1\n12|100|?\n");
  assert.strictEqual(satirlar[0].profil, false);
  assert.strictEqual(satirlar[1].profil, true);
  assert.strictEqual(satirlar[2].wmiHata, true);
  const secSatir = yetimSec(
    { tenantId: "default", chromeKokPid: 0 },
    satirlar.map((p) => Object.assign({ ppidYasiyor: Number(p.ppid) === 100, yasiyor: true }, p)),
    { kendiPid: 100, simdi: 1 }
  );
  assert.deepStrictEqual(secSatir.kapat, [11]);
  assert.strictEqual(secSatir.kapat.indexOf(10), -1);

  const saglikli = { status: "ready", kurtarmaCalisiyor: false };
  assert.deepStrictEqual(sicramaKarar(saglikli, 61000, true, 1), { bak: true, toparla: false });
  assert.deepStrictEqual(sicramaKarar(saglikli, 50000, false, 1), { bak: false, toparla: false });
  assert.strictEqual(sicramaKarar({ status: "idle" }, 61000, false, 1).toparla, true);
  assert.strictEqual(sicramaKarar({ status: "qr" }, 61000, false, 1).toparla, false);
  const basarisiz = {
    status: "error",
    toparlamaDurumu: "toparlama_basarisiz",
    sonKurtarmaMs: 1000,
  };
  assert.strictEqual(sicramaKarar(basarisiz, 61000, false, 1000 + 1000).toparla, false);
  assert.strictEqual(
    sicramaKarar(basarisiz, 61000, false, 1000 + KURTARMA_ARA_MS).toparla,
    true
  );

  const uyg = await sahteYetim(
    kayitli,
    karisik,
    {}
  );
  assert.deepStrictEqual(uyg.nazik, [501]);
  assert.deepStrictEqual(uyg.bekle, [10000]);
  assert.deepStrictEqual(uyg.zorla, [501]);
  assert.strictEqual(uyg.zorla.indexOf(900), -1);
  assert.ok(uyg.log.some((s) => s.indexOf("kurtarma_yetim_bulundu n=1 yas_sn=60 ebeveyn=servis") === 0));
  assert.ok(uyg.log.some((s) => s.indexOf("kurtarma_yetim_kapatildi n=1") === 0));

  const uygB = await sahteYetim(kayitsiz, yetimler, {});
  assert.deepStrictEqual(uygB.zorla, [502]);
  assert.strictEqual(uygB.nazik.indexOf(900), -1);

  const uygW = await sahteYetim(kayitsiz, [], { wmi: true });
  assert.deepStrictEqual(uygW.zorla, []);
  assert.deepStrictEqual(uygW.bekle, []);
  assert.ok(uygW.log.some((s) => s === "kurtarma_pid_atlandi wmi"));

  const uygD = await sahteYetim(kayitli, karisik, {
    dogrula: () => ({ profil: false, wmiHata: false }),
  });
  assert.deepStrictEqual(uygD.zorla, []);
  assert.ok(uygD.log.some((s) => s === "kurtarma_pid_atlandi profil"));
  assert.ok(!uygD.log.some((s) => s.indexOf("kurtarma_yetim_kapatildi") === 0));

  const indexSrc = fs.readFileSync(path.join(__dirname, "index.js"), "utf8");
  assert.ok(indexSrc.includes(".join('\\n')"));
  assert.ok(!indexSrc.includes("ToLower() Get-CimInstance"));

  const uygL = await sahteYetim(kayitli, karisik, {
    lock: { var: true, tutan: false },
    nazikOlur: true,
  });
  assert.deepStrictEqual(uygL.zorla, []);
  assert.ok(uygL.log.some((s) => s === "kurtarma_lock_kaldi tutan_yok"));
}

senaryolar()
  .then(() => {
    console.log("CASE kurtarma kural ok");
  })
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
