/** Aynı deneme tek öğe. Farklı deneme yeni öğe. Chrome ve ağ yok. */
const assert = require("assert");
const { kalemEkle } = require("./kuyruk-kural");

const harita = new Map();
const kuyruk = [];
const a = kalemEkle(harita, kuyruk, [
  { telefon: "905550000000", mesaj: "a", anahtar: "szwa:d:aaa" },
]);
const b = kalemEkle(harita, kuyruk, [
  { telefon: "905550000000", mesaj: "a", anahtar: "szwa:d:aaa" },
]);
assert.strictEqual(a.eklenen, 1);
assert.strictEqual(a.tekrar, 0);
assert.strictEqual(a.oge[0].durum, "bekliyor");
assert.strictEqual(b.eklenen, 0);
assert.strictEqual(b.tekrar, 1);
assert.strictEqual(b.oge[0].durum, "bekliyor");
assert.strictEqual(kuyruk.length, 1);

kuyruk[0].durum = "gonderildi";
const c = kalemEkle(harita, kuyruk, [
  { telefon: "905550000000", mesaj: "a", anahtar: "szwa:d:aaa" },
]);
assert.strictEqual(c.eklenen, 0);
assert.strictEqual(c.oge[0].durum, "gonderildi");
assert.strictEqual(kuyruk.length, 1);

const d = kalemEkle(harita, kuyruk, [
  { telefon: "905550000000", mesaj: "a", anahtar: "szwa:d:bbb" },
]);
assert.strictEqual(d.eklenen, 1);
assert.strictEqual(kuyruk.length, 2);
assert.strictEqual(JSON.stringify(c.oge[0]).includes("90555"), false);

const { ekAyikla, gonderGovde, gonderSecenek, PDF_LIMIT, sonucSinifla, sureli, yariyiGeriAl, gonderimBitir, sonucSakla, sonucGetir, logSatiri, SONUC_TTL_MS } = require("./kuyruk-kural");
const fs = require("fs");
const path = require("path");
const kuralSrc = fs.readFileSync(path.join(__dirname, "kuyruk-kural.js"), "utf8");
assert.strictEqual(kuralSrc.includes("writeFile"), false);
assert.strictEqual(kuralSrc.includes("appendFile"), false);
assert.strictEqual(kuralSrc.includes("require(\"fs\")"), false);
assert.strictEqual(kuralSrc.includes("require('fs')"), false);

const kucuk = Buffer.from("%PDF-1.4\n").toString("base64");
const medya = { mime: "application/pdf", veri: kucuk, ad: "260.pdf" };
const mh = new Map();
const mq = [];
const m1 = kalemEkle(mh, mq, [{ telefon: "905550000000", mesaj: "m", anahtar: "szwa:d:med", ek: medya }]);
const m2 = kalemEkle(mh, mq, [{ telefon: "905550000000", mesaj: "m", anahtar: "szwa:d:med", ek: medya }]);
assert.strictEqual(m1.eklenen, 1);
assert.strictEqual(m2.eklenen, 0);
assert.strictEqual(m2.tekrar, 1);
assert.strictEqual(mq.length, 1);
assert.strictEqual(JSON.stringify(m1.oge).includes(kucuk), false);
assert.strictEqual(JSON.stringify(m2.oge).includes(kucuk), false);

const kotuMime = kalemEkle(new Map(), [], [{ telefon: "905550000000", mesaj: "m", anahtar: "k1", ek: { mime: "text/plain", veri: kucuk, ad: "260.pdf" } }]);
assert.strictEqual(kotuMime.eklenen, 0);
assert.strictEqual(kotuMime.oge[0].durum, "basarisiz");

const kotuAd = kalemEkle(new Map(), [], [{ telefon: "905550000000", mesaj: "m", anahtar: "k2", ek: { mime: "application/pdf", veri: kucuk, ad: "260 ad.pdf" } }]);
assert.strictEqual(kotuAd.eklenen, 0);

let genis = Math.ceil(((PDF_LIMIT + 1) * 4) / 3);
genis += (4 - (genis % 4)) % 4;
const buyuk = kalemEkle(new Map(), [], [{
  telefon: "905550000000",
  mesaj: "m",
  anahtar: "k3",
  ek: { mime: "application/pdf", veri: "A".repeat(genis), ad: "260.pdf" },
}]);
assert.strictEqual(buyuk.eklenen, 0);
assert.strictEqual(buyuk.oge[0].durum, "basarisiz");
assert.strictEqual(buyuk.oge[0].kod, "ek_cok_buyuk");
assert.strictEqual(kotuMime.oge[0].kod, "ek_gecersiz");
assert.strictEqual(ekAyikla({ mime: "application/pdf", veri: kucuk, ad: "260.pdf" }).ad, "260.pdf");

const ortaHam = Buffer.alloc(160 * 1024, 0x25);
const orta = ortaHam.toString("base64");
const ortaEk = ekAyikla({ mime: "application/pdf", veri: orta, ad: "260.pdf" });
assert.strictEqual(ortaEk.hata, undefined);
assert.strictEqual(ortaEk.ad, "260.pdf");
const yaziKalem = kalemEkle(new Map(), [], [{ telefon: "905550000000", mesaj: "duz", anahtar: "szwa:d:yazi" }]);
assert.strictEqual(yaziKalem.eklenen, 1);
assert.strictEqual(yaziKalem.oge[0].durum, "bekliyor");
assert.strictEqual(yaziKalem.oge[0].kod, undefined);

async function gonderTest() {
  const item = { mesaj: "m", ek: { mime: "application/pdf", veri: kucuk, ad: "260.pdf" } };
  let giden = null;
  await gonderGovde(item, async (icerik, opts) => {
    giden = { ad: icerik.ad, mime: icerik.mime, caption: opts && opts.caption, belge: opts && opts.sendMediaAsDocument };
    return { id: { _serialized: "1" } };
  }, (mime, veri, ad) => ({ mime, veri, ad }));
  assert.strictEqual(item.ek, null);
  assert.strictEqual(giden.ad, "260.pdf");
  assert.strictEqual(giden.mime, "application/pdf");
  assert.strictEqual(giden.caption, "m");
  assert.strictEqual(giden.belge, true);

  const yazi = { mesaj: "sadece" };
  let alinan = null;
  let mediaCagrildi = false;
  await gonderGovde(yazi, async (icerik, opts) => {
    alinan = { icerik, opts };
  }, () => { mediaCagrildi = true; });
  assert.strictEqual(alinan.icerik, "sadece");
  assert.strictEqual(alinan.opts, undefined);
  assert.strictEqual(mediaCagrildi, false);

  const kirik = { mesaj: "m", ek: { mime: "application/pdf", veri: kucuk, ad: "261.pdf" } };
  let dustu = false;
  try {
    await gonderGovde(kirik, async () => { throw new Error("gonderilemedi"); }, (mime, veri, ad) => ({ mime, veri, ad }));
  } catch (_e) {
    dustu = true;
  }
  assert.strictEqual(dustu, true);
  assert.strictEqual(kirik.ek, null);
}

const indexSrc = fs.readFileSync(path.join(__dirname, "index.js"), "utf8");
assert.strictEqual(indexSrc.includes("new MessageMedia"), true);
assert.strictEqual(indexSrc.includes("item.ek = null"), true);
assert.strictEqual(indexSrc.includes("gonderGovde"), true);
assert.strictEqual(kuralSrc.includes("sendMediaAsDocument: true"), true);
assert.strictEqual(indexSrc.includes("kuyruk-sonuc"), true);
assert.strictEqual(indexSrc.includes("yariyiGeriAl"), true);
assert.strictEqual(gonderSecenek({ mesaj: "m" }), undefined);
assert.strictEqual(gonderSecenek({ mesaj: "m", ek: { ad: "1.pdf" } }).sendMediaAsDocument, true);

assert.strictEqual(sonucSinifla({ id: { _serialized: "MID" } }, null, false, true).durum, "gonderildi");
assert.strictEqual(sonucSinifla({}, null, false, true).kod, "bos_donus");
assert.strictEqual(sonucSinifla(null, new Error("Evaluation failed"), false, true).kod, "medya_hata");
assert.strictEqual(sonucSinifla(null, new Error("Evaluation failed"), false, false).kod, "gonderim_hata");
assert.strictEqual(sonucSinifla(null, new Error("Protocol error: Target closed"), false, true).kod, "oturum");
assert.strictEqual(sonucSinifla({ id: { _serialized: "MID" } }, null, true, true).kod, "zaman_asimi");

const satir = logSatiri("szwa:d:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "pdf", 130890, "basarisiz", "medya_hata", 80);
assert.strictEqual(satir.includes("merhaba"), false);
assert.strictEqual(satir.includes("90555"), false);
assert.ok(satir.includes("tur=pdf"));
assert.ok(satir.includes("bayt=130890"));
assert.ok(satir.includes("kod=medya_hata"));
assert.ok(satir.includes("sonuc=basarisiz"));
assert.strictEqual(logSatiri("905551112233", "metin", 0, "gonderildi", "", 10).includes("905551112233"), false);

const sonucHarita = new Map();
sonucSakla(sonucHarita, "szwa:d:abc", { durum: "gonderildi", kod: "", tur: "pdf", bayt: 10, sure: 4 }, 1000);
assert.strictEqual(sonucGetir(sonucHarita, "szwa:d:abc", 1000, SONUC_TTL_MS).durum, "gonderildi");
assert.strictEqual(JSON.stringify(sonucGetir(sonucHarita, "szwa:d:abc", 1000, SONUC_TTL_MS)).includes("telefon"), false);
assert.strictEqual(sonucGetir(sonucHarita, "szwa:d:abc", 1000 + SONUC_TTL_MS + 1, SONUC_TTL_MS).durum, "yok");
assert.strictEqual(sonucGetir(new Map(), "yok", 1, 10).durum, "yok");

const gidenKalem = { durum: "gonderildi", kusak: 1 };
assert.strictEqual(yariyiGeriAl([], gidenKalem).neden, "bitti");
const yarim = { durum: "gonderiliyor", kusak: 1, askida: true, anahtar: "a" };
const sira = [];
assert.strictEqual(yariyiGeriAl(sira, yarim).eklendi, true);
assert.strictEqual(yariyiGeriAl(sira, yarim).neden, "zaten");
assert.strictEqual(sira.length, 1);
assert.strictEqual(yarim.kusak, 2);
const gecBasari = gonderimBitir(yarim, sira, { durum: "gonderildi", kod: "" }, 1);
assert.strictEqual(gecBasari.uygulandi, true);
assert.strictEqual(yarim.durum, "gonderildi");
assert.strictEqual(sira.length, 0);

const takili = { durum: "gonderiliyor", kusak: 1, askida: true };
const sira2 = [];
yariyiGeriAl(sira2, takili);
const zaman = gonderimBitir(takili, sira2, { durum: "basarisiz", kod: "zaman_asimi" }, 1);
assert.strictEqual(zaman.uygulandi, true);
assert.strictEqual(takili.durum, "basarisiz");
assert.strictEqual(takili.kod, "zaman_asimi");
assert.strictEqual(sira2.length, 0);

const kirilan = { durum: "gonderiliyor", kusak: 1, askida: true };
const sira3 = [];
yariyiGeriAl(sira3, kirilan);
const yeniden = gonderimBitir(kirilan, sira3, { durum: "basarisiz", kod: "medya_hata" }, 1);
assert.strictEqual(yeniden.uygulandi, false);
assert.strictEqual(kirilan.durum, "bekliyor");
assert.strictEqual(kirilan.askida, false);
assert.strictEqual(sira3.length, 1);

gonderTest()
  .then(() => sureli(() => new Promise(() => {}), 40))
  .then((paket) => {
    assert.strictEqual(paket.zamanAsimi, true);
    console.log("CASE kuyruk kural ok");
  })
  .catch((err) => {
    console.error("FAIL kuyruk");
    console.error(err && err.message ? err.message : "hata");
    process.exit(1);
  });

