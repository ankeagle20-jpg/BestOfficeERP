/** Sayfa bellegi yok. Tahsilat butonu yalnizca kayit yanitiyla acilir. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const api = require("./static/js/tahsilat-wa-durum");

const src = fs.readFileSync(path.join(__dirname, "static", "js", "tahsilat-wa-durum.js"), "utf8");
assert.strictEqual(src.includes("localStorage"), false);
assert.strictEqual(src.includes("sessionStorage"), false);

const bos = api.bos();
assert.strictEqual(bos.aktif, false);
assert.strictEqual(bos.tahsilatId, 0);

const kayit = {
  tahsilat_id: 55,
  makbuz_no: "260",
  musteri_id: 7,
  tutar: "10",
  tarih: "2026-10-06",
  aciklama: "a",
};
let s = api.kayit(bos, kayit);
assert.strictEqual(s.aktif, true);
assert.strictEqual(s.tahsilatId, 55);
assert.strictEqual(s.makbuzNo, "260");
assert.strictEqual(api.kayit(s, { tahsilat_id: 0 }).aktif, false);
assert.strictEqual(api.kayit(s, {}).tahsilatId, 0);

const kapali = api.pasif(s);
assert.strictEqual(kapali.aktif, false);
assert.strictEqual(kapali.tahsilatId, 0);

s = api.kayit(api.bos(), kayit);
assert.strictEqual(api.musteri(s, 7).aktif, true);
assert.strictEqual(api.musteri(s, "7").aktif, true);
assert.strictEqual(api.musteri(s, 8).aktif, false);
assert.strictEqual(api.musteri(s, 8).tahsilatId, 0);

s = api.kayit(api.bos(), kayit);
assert.strictEqual(api.alan(s, "tutar", "10").aktif, true);
assert.strictEqual(api.alan(s, "tutar", "11").aktif, false);
assert.strictEqual(api.alan(s, "tarih", "2026-10-06").aktif, true);
assert.strictEqual(api.alan(s, "tarih", "2026-10-07").aktif, false);
assert.strictEqual(api.alan(s, "aciklama", "a").aktif, true);
assert.strictEqual(api.alan(s, "aciklama", "b").aktif, false);
assert.strictEqual(api.bos().aktif, false);

console.log("CASE tahsilat wa durum ok");
