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

console.log("CASE kuyruk kural ok");
