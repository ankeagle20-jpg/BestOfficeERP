/** Bos donus: ack dogrula, ikinci gonderim yok. Chrome ve ag yok. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const {
  ACK_PENCERE_MS,
  sonucSinifla,
  ackOlay,
  ackEslesir,
  tekBekleyen,
  ackBekle,
  yariyiGeriAl,
  gonderimBitir,
  logSatiri,
} = require("./kuyruk-kural");

const SOHBET = "905550000000@c.us";

function sahte() {
  const din = [];
  return {
    gonderim: 0,
    sendMessage() {
      this.gonderim += 1;
    },
    on(ad, fn) {
      if (ad === "message_ack") din.push(fn);
    },
    off(ad, fn) {
      if (ad !== "message_ack") return;
      const i = din.indexOf(fn);
      if (i >= 0) din.splice(i, 1);
    },
    emit(msg, ack) {
      din.slice().forEach((fn) => fn(msg, ack));
    },
  };
}

function mesaj(to, fromMe) {
  return {
    fromMe: fromMe !== false,
    to,
    get body() {
      throw new Error("metin");
    },
  };
}

async function senaryolar() {
  assert.strictEqual(ACK_PENCERE_MS, 10000);
  assert.strictEqual(sonucSinifla(undefined, null, false, false).durum, "ack_bekle");
  assert.strictEqual(sonucSinifla({}, null, false, false).durum, "ack_bekle");
  assert.strictEqual(sonucSinifla(null, null, false, false).kod, "gonderim_hata");
  assert.strictEqual(sonucSinifla(null, new Error("Evaluation failed"), false, false).kod, "gonderim_hata");
  assert.strictEqual(sonucSinifla(null, new Error("Evaluation failed"), false, true).kod, "medya_hata");
  assert.strictEqual(sonucSinifla({ id: { _serialized: "MID" } }, null, true, false).kod, "zaman_asimi");

  const olay = ackOlay(mesaj(SOHBET, true), 1, 1500);
  assert.strictEqual(olay.chatId, SOHBET);
  assert.strictEqual(olay.fromMe, true);
  assert.strictEqual(ackEslesir(olay, { chatId: SOHBET, bas: 1000, pencereMs: 10000 }), true);
  assert.strictEqual(ackEslesir(ackOlay(mesaj(SOHBET, false), 1, 1500), { chatId: SOHBET, bas: 1000, pencereMs: 10000 }), false);
  assert.strictEqual(ackEslesir(ackOlay(mesaj(SOHBET, true), 0, 1500), { chatId: SOHBET, bas: 1000, pencereMs: 10000 }), false);
  assert.strictEqual(ackEslesir(ackOlay(mesaj("906660000000@c.us", true), 1, 1500), { chatId: SOHBET, bas: 1000, pencereMs: 10000 }), false);
  assert.strictEqual(ackEslesir(olay, { chatId: SOHBET, bas: 5000, pencereMs: 10000 }), false);

  assert.strictEqual(tekBekleyen([], { askida: true }), true);
  assert.strictEqual(tekBekleyen([{ askida: true }], { askida: true }), false);
  assert.strictEqual(tekBekleyen([{ askida: false, durum: "bekliyor" }], { askida: true }), true);

  const c1 = sahte();
  let simdi = 1000;
  const p1 = ackBekle(c1, SOHBET, 1000, 200, () => simdi);
  simdi = 1200;
  c1.emit(mesaj(SOHBET, true), 1);
  const s1 = await p1;
  assert.strictEqual(s1.durum, "gonderildi");
  assert.strictEqual(s1.kod, "ack_dogrulandi");
  assert.strictEqual(c1.gonderim, 0);

  const c2 = sahte();
  simdi = 50000;
  const p2 = ackBekle(c2, SOHBET, 50000, 30, () => simdi);
  simdi = 1200;
  c2.emit(mesaj(SOHBET, true), 1);
  simdi = 50000;
  const s2 = await p2;
  assert.strictEqual(s2.durum, "belirsiz");
  assert.strictEqual(s2.kod, "bos_donus_belirsiz");
  assert.strictEqual(c2.gonderim, 0);

  const c3 = sahte();
  simdi = 80000;
  const p3 = ackBekle(c3, SOHBET, 80000, 40, () => simdi);
  c3.emit(mesaj("906660000000@c.us", true), 2);
  c3.emit(mesaj(SOHBET, true), 0);
  const s3 = await p3;
  assert.strictEqual(s3.kod, "bos_donus_belirsiz");
  assert.strictEqual(c3.gonderim, 0);

  const kalem = { durum: "gonderiliyor", kusak: 1, askida: true, dogrulaniyor: true };
  assert.strictEqual(yariyiGeriAl([], kalem).neden, "dogrulama");
  assert.strictEqual(kalem.durum, "gonderiliyor");
  kalem.dogrulaniyor = false;
  const sira = [];
  const bit = gonderimBitir(kalem, sira, { durum: "belirsiz", kod: "bos_donus_belirsiz" }, 1);
  assert.strictEqual(bit.uygulandi, true);
  assert.strictEqual(kalem.durum, "belirsiz");
  assert.strictEqual(kalem._kapali, true);
  assert.strictEqual(yariyiGeriAl([], kalem).neden, "bitti");

  const satir = logSatiri("szwa:d:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "metin", 0, "belirsiz", "bos_donus_belirsiz", 98);
  assert.ok(satir.includes("sonuc=belirsiz"));
  assert.ok(satir.includes("kod=bos_donus_belirsiz"));
  assert.strictEqual(satir.includes("GIZLI"), false);
  assert.strictEqual(satir.includes(SOHBET), false);

  const indexSrc = fs.readFileSync(path.join(__dirname, "index.js"), "utf8");
  assert.strictEqual(indexSrc.includes("ackBekle"), true);
  assert.strictEqual(indexSrc.includes("dogrulaniyor"), true);
  assert.strictEqual((indexSrc.match(/sendMessage/g) || []).length, 3);
}

senaryolar()
  .then(() => {
    console.log("CASE ack kural ok");
  })
  .catch((err) => {
    console.error("FAIL ack");
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
  });
