"use strict";
const m = require("./static/js/wa-toplu.js");

function esit(ad, ok) {
  if (!ok) {
    console.log("FAIL " + ad);
    process.exit(1);
  }
  console.log("ok " + ad);
}

const acik = m.wtSecimUygula([
  { gorunur: true, secilebilir: true, secili: false },
  { gorunur: false, secilebilir: true, secili: false },
  { gorunur: true, secilebilir: false, secili: false }
], true);
esit("gorunen", acik[0] === true && acik[1] === false && acik[2] === false);

const kapali = m.wtSecimUygula([
  { gorunur: true, secilebilir: true, secili: true },
  { gorunur: false, secilebilir: true, secili: true }
], false);
esit("gizli kalir", kapali[0] === false && kapali[1] === true);

const ozet = m.wtOzet(["05550001111", "905550001111", "05550002222"], 150);
esit("tekil ozet", ozet.secili === 3 && ozet.alici === 2 && ozet.sure_dk === 3 && ozet.gun === 1);

const bir = m.wtOzet(["05550001111"], 150);
esit("1 dakika", bir.alici === 1 && bir.sure_dk === 2 && bir.gun_goster === false
  && /dakika/.test(bir.sure_metin) && !/saat/.test(bir.sure_metin) && !/günde/.test(bir.sure_metin));

const nolar = [];
for (let i = 0; i < 151; i++) nolar.push("90555" + String(1000000 + i).slice(1));
const cok = m.wtOzet(nolar, 150);
esit("151 gun", cok.alici === 151 && cok.gun === 2 && cok.gun_goster === true && /günde/.test(cok.sure_metin));

esit("neden gecersiz", /Mesaj boş/.test(m.wtTaslakNedenMetin("gecersiz")));
esit("neden durum", /Bitir/.test(m.wtTaslakNedenMetin("durum")));

function el(id) {
  const listeners = [];
  const attrs = {};
  return {
    id: id,
    hidden: id === "wtPanel" || id === "wtKampanyaDurum",
    style: {},
    value: id === "wtTavan" ? "150" : (id === "wtSablon" ? "Merhaba test" : ""),
    textContent: "",
    classList: { toggle: function () {}, add: function () {}, remove: function () {} },
    disabled: false,
    checked: false,
    innerHTML: "",
    addEventListener: function (type, fn) { listeners.push({ type: type, fn: fn }); },
    getAttribute: function (k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; },
    setAttribute: function (k, v) { attrs[k] = String(v); },
    appendChild: function () {},
    _listeners: listeners
  };
}

const store = {};
["wtPanel", "wtPanelGecikme", "wtAltGecikme", "wtAltToplu", "wtSablon", "wtSayac",
  "wtTavan", "wtAra", "wtDurum", "wtTumunu", "wtGovde", "wtOzet", "wtTestNo",
  "wtTestSonuc", "wtBaslat", "wtYenile", "wtKaydet", "wtKampanyaDurum",
  "wtTest", "wtDurdur", "wtDevam", "wtBitir"
].forEach(function (id) { store[id] = el(id); });

let fetchSay = 0;
let taslakSay = 0;
let sonTaslakGovde = null;
global.fetch = function (url, opts) {
  const u = String(url);
  if (u.indexOf("toplu-liste") >= 0) {
    fetchSay += 1;
    return Promise.resolve({ json: function () { return Promise.resolve({ satirlar: [] }); } });
  }
  if (u.indexOf("toplu-durum") >= 0) {
    return Promise.resolve({ json: function () { return Promise.resolve({}); } });
  }
  if (u.indexOf("toplu-taslak") >= 0) {
    taslakSay += 1;
    sonTaslakGovde = opts && opts.body ? JSON.parse(opts.body) : null;
    return Promise.resolve({
      status: 200,
      json: function () { return Promise.resolve({ ok: true, id: 9, test_ok: false, durum: "taslak" }); }
    });
  }
  return Promise.resolve({
    status: 200,
    json: function () { return Promise.resolve({ ok: true }); }
  });
};
global.document = {
  getElementById: function (id) { return store[id] || null; },
  querySelector: function () { return null; },
  querySelectorAll: function () { return []; },
  createElement: function () {
    return {
      textContent: "", type: "", disabled: false, checked: false,
      appendChild: function () {}, addEventListener: function () {}
    };
  },
  createTextNode: function (t) { return { text: t }; }
};

m.bagla();
m.bagla();
const clickToplu = store.wtAltToplu._listeners.filter(function (x) { return x.type === "click"; });
esit("tek dinleyici", clickToplu.length === 1 && store.wtPanel.getAttribute("data-wt-bagli") === "1");

function flush() {
  return new Promise(function (resolve) { setImmediate(resolve); });
}

(async function () {
  clickToplu[0].fn();
  await flush();
  await flush();
  const once = fetchSay;
  esit("lazy ilk", once >= 1);
  clickToplu[0].fn();
  await flush();
  await flush();
  esit("lazy ikinci acilis", fetchSay === once);
  const yenile = store.wtYenile._listeners.filter(function (x) { return x.type === "click"; });
  yenile[0].fn();
  await flush();
  await flush();
  esit("yenile fetch", fetchSay === once + 1);

  const kaydet = store.wtKaydet._listeners.filter(function (x) { return x.type === "click"; });
  esit("kaydet dinleyici", kaydet.length === 1);
  const onceTaslak = taslakSay;
  kaydet[0].fn();
  await flush();
  await flush();
  esit("kaydet taslak", taslakSay === onceTaslak + 1 && sonTaslakGovde && sonTaslakGovde.sablon === "Merhaba test");
  esit("kaydet sonuc", store.wtTestSonuc.textContent === "Şablon kaydedildi");

  // bos metin -> gecersiz
  store.wtSablon.value = "";
  global.fetch = function (url, opts) {
    if (String(url).indexOf("toplu-taslak") >= 0) {
      return Promise.resolve({
        status: 400,
        json: function () { return Promise.resolve({ ok: false, neden: "gecersiz" }); }
      });
    }
    return Promise.resolve({ json: function () { return Promise.resolve({}); } });
  };
  kaydet[0].fn();
  await flush();
  await flush();
  esit("bos hata", /Mesaj boş/.test(store.wtTestSonuc.textContent));

  global.fetch = function (url) {
    if (String(url).indexOf("toplu-taslak") >= 0) {
      return Promise.resolve({
        status: 409,
        json: function () { return Promise.resolve({ ok: false, neden: "durum" }); }
      });
    }
    return Promise.resolve({ json: function () { return Promise.resolve({}); } });
  };
  store.wtSablon.value = "dolu";
  kaydet[0].fn();
  await flush();
  await flush();
  esit("durum hata", /Bitir/.test(store.wtTestSonuc.textContent));
})().catch(function (e) {
  console.log("FAIL async", e && e.message);
  process.exit(1);
});
