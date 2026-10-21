/** Kuyruk idempotency. Chrome ve ağ yok. Telefon, mesaj ve PDF baytı diske yazılmaz. */

const PDF_LIMIT = 2 * 1024 * 1024;
const MEDYA_TIMEOUT_MS = 75 * 1000;
const SONUC_TTL_MS = 2 * 60 * 60 * 1000;
const ACK_PENCERE_MS = 10000;
const FETCH_TOLERANS_MS = 15000;
const FETCH_TIMEOUT_MS = 4000;
const FETCH_LIMIT = 3;

function cozulmusBoyut(veri) {
  const s = String(veri || "").replace(/\s/g, "");
  if (!s || s.length % 4 !== 0) return -1;
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(s)) return -1;
  let pad = 0;
  if (s.endsWith("==")) pad = 2;
  else if (s.endsWith("=")) pad = 1;
  return Math.floor((s.length * 3) / 4) - pad;
}

function ekAyikla(ek) {
  if (!ek || typeof ek !== "object") return { hata: "ek_gecersiz" };
  if (ek.mime !== "application/pdf") return { hata: "ek_gecersiz" };
  const veri = String(ek.veri || "").replace(/\s/g, "");
  const boyut = cozulmusBoyut(veri);
  if (boyut > PDF_LIMIT) return { hata: "ek_cok_buyuk" };
  if (boyut < 5) return { hata: "ek_gecersiz" };
  const ad = String(ek.ad || "");
  if (!/^[0-9]{1,18}\.pdf$/.test(ad)) return { hata: "ek_gecersiz" };
  return { mime: "application/pdf", veri, ad };
}

function kalemEkle(harita, kuyruk, liste) {
  const durumlar = harita || new Map();
  const sira = kuyruk || [];
  let eklenen = 0;
  let tekrar = 0;
  const oge = [];
  for (const item of liste || []) {
    if (!item || !item.telefon || !item.mesaj) continue;
    const anahtar = String(item.anahtar || "").trim().slice(0, 120);
    if (anahtar && durumlar.has(anahtar)) {
      tekrar += 1;
      const mevcut = durumlar.get(anahtar) || {};
      oge.push({ tekrar: true, durum: mevcut.durum || "bekliyor" });
      continue;
    }
    let ek = null;
    if (item.ek) {
      const ayik = ekAyikla(item.ek);
      if (!ayik || ayik.hata) {
        oge.push({ tekrar: false, durum: "basarisiz", kod: (ayik && ayik.hata) || "ek_gecersiz" });
        continue;
      }
      ek = ayik;
    }
    const kayit = {
      telefon: item.telefon,
      mesaj: item.mesaj,
      anahtar,
      durum: "bekliyor",
    };
    if (ek) kayit.ek = ek;
    if (anahtar) durumlar.set(anahtar, kayit);
    sira.push(kayit);
    eklenen += 1;
    oge.push({ tekrar: false, durum: "bekliyor" });
  }
  return { eklenen, tekrar, oge };
}

function gonderSecenek(item) {
  if (!item || !item.ek) return undefined;
  return { caption: String(item.mesaj || ""), sendMediaAsDocument: true };
}

async function gonderGovde(item, send, mediaCtor) {
  const ek = item && item.ek ? item.ek : null;
  try {
    if (ek) {
      const media = mediaCtor(ek.mime, ek.veri, ek.ad);
      return await send(media, gonderSecenek(item));
    }
    return await send(String((item && item.mesaj) || ""));
  } finally {
    if (item) item.ek = null;
  }
}

function sonucSinifla(donus, hata, zamanAsimi, pdf) {
  if (zamanAsimi) return { durum: "basarisiz", kod: "zaman_asimi" };
  if (hata) {
    const m = String((hata && hata.message) || hata || "").toLowerCase();
    if (/session|logout|detached|protocol|target closed|execution context|oturum|not connected/.test(m)) {
      return { durum: "basarisiz", kod: "oturum" };
    }
    return { durum: "basarisiz", kod: pdf ? "medya_hata" : "gonderim_hata" };
  }
  // Sohbet yok: kütüphane null döner, mesaj gitmemiştir.
  if (donus === null) return { durum: "basarisiz", kod: "gonderim_hata" };
  const ham = donus && donus.id;
  const id = ham && typeof ham === "object" ? ham._serialized : ham;
  if (!id) return { durum: "ack_bekle", kod: "" };
  return { durum: "gonderildi", kod: "" };
}

function sohbetKim(msg) {
  if (!msg) return "";
  const to = msg.to;
  if (typeof to === "string" && to) return to;
  if (to && typeof to._serialized === "string") return to._serialized;
  const remote = msg.id && msg.id.remote;
  if (typeof remote === "string" && remote) return remote;
  if (remote && typeof remote._serialized === "string") return remote._serialized;
  return "";
}

function ackOlay(msg, ack, an) {
  const fromMe = Boolean(msg && (msg.fromMe || (msg.id && msg.id.fromMe)));
  return {
    fromMe,
    chatId: fromMe ? sohbetKim(msg) : "",
    ack: Number(ack),
    an: Number(an) || 0,
  };
}

function olusturOlay(msg, an) {
  const fromMe = Boolean(msg && (msg.fromMe || (msg.id && msg.id.fromMe)));
  return { fromMe, chatId: fromMe ? sohbetKim(msg) : "", an: Number(an) || 0 };
}

function ekTur(kim) {
  const s = String(kim || "");
  const at = s.lastIndexOf("@");
  if (at < 0 || at === s.length - 1) return "yok";
  const ek = s.slice(at + 1).toLowerCase();
  if (ek === "c.us" || ek === "lid" || ek === "g.us") return ek;
  return "diger";
}

function kimParca(deger) {
  if (!deger) return "";
  if (typeof deger === "string") return deger;
  if (typeof deger._serialized === "string") return deger._serialized;
  return "";
}

function chatEslesir(chatId, bekleyen) {
  if (!chatId || !bekleyen) return false;
  if (chatId === bekleyen.chatId) return true;
  return Boolean(bekleyen.alias && bekleyen.alias.has(chatId));
}

function kanitSatiri(olayAd, ack, fromMe, bizEk, toEk, remoteEk, eslesme, ms) {
  const ackSayi = Number(ack);
  const ackK = Number.isFinite(ackSayi) ? String(Math.trunc(ackSayi)) : "-";
  const sure = Math.max(0, Math.trunc(Number(ms) || 0));
  return (
    "olay=" + olayAd +
    " ack=" + ackK +
    " fromMe=" + (fromMe ? "1" : "0") +
    " biz=" + bizEk +
    " to=" + toEk +
    " remote=" + remoteEk +
    " eslesme=" + (eslesme ? "1" : "0") +
    " ms=" + sure
  );
}

function ackEslesir(olay, bekleyen) {
  if (!olay || !bekleyen) return false;
  if (!olay.fromMe) return false;
  if (!chatEslesir(olay.chatId, bekleyen)) return false;
  if (!(olay.ack >= 1)) return false;
  const an = olay.an;
  const bas = Number(bekleyen.bas) || 0;
  const pencere = Number(bekleyen.pencereMs) || ACK_PENCERE_MS;
  if (!(an >= bas)) return false;
  if (an - bas > pencere) return false;
  return true;
}

function tekBekleyen(kuyruk, aktif) {
  let n = 0;
  if (aktif && aktif.askida) n += 1;
  for (const item of kuyruk || []) {
    if (item && item.askida) n += 1;
  }
  return n <= 1;
}

function olusturEslesir(olay, bekleyen) {
  if (!olay || !bekleyen) return false;
  if (!olay.fromMe) return false;
  if (!chatEslesir(olay.chatId, bekleyen)) return false;
  const an = olay.an;
  const bas = Number(bekleyen.bas) || 0;
  const pencere = Number(bekleyen.pencereMs) || ACK_PENCERE_MS;
  if (!(an >= bas)) return false;
  if (an - bas > pencere) return false;
  return true;
}

function kanitBaslat(client, chatId, bas, pencereMs, simdiFn, logFn) {
  const pencere = Number(pencereMs) || ACK_PENCERE_MS;
  const simdi = typeof simdiFn === "function" ? simdiFn : () => Date.now();
  const yaz = typeof logFn === "function" ? logFn : null;
  const bekleyen = {
    chatId: String(chatId || ""),
    bas: Number(bas) || 0,
    pencereMs: pencere,
    alias: new Set(),
  };
  if (bekleyen.chatId) bekleyen.alias.add(bekleyen.chatId);
  let kanitVar = false;
  let kapali = false;
  let ozetYazildi = false;
  let olaySayisi = 0;
  let bekleyenResolve = null;
  let zamanlayici = null;
  const adaylar = [];
  const sonuc = () =>
    kanitVar
      ? { durum: "gonderildi", kod: "ack_dogrulandi" }
      : { durum: "belirsiz", kod: "bos_donus_belirsiz" };
  const ozet = () => {
    if (ozetYazildi) return;
    ozetYazildi = true;
    if (yaz) yaz("kanit_ozet olay_sayisi=" + olaySayisi + " eslesme=" + (kanitVar ? "1" : "0"));
  };
  const isle = (ad, msg, ack) => {
    if (kapali) return;
    try {
      const an = simdi();
      const toHam = kimParca(msg && msg.to);
      const remoteHam = kimParca(msg && msg.id && msg.id.remote);
      const fromMe = Boolean(msg && (msg.fromMe || (msg.id && msg.id.fromMe)));
      const eslesme = chatEslesir(toHam, bekleyen) || chatEslesir(remoteHam, bekleyen);
      olaySayisi += 1;
      if (yaz) {
        yaz(kanitSatiri(ad, ack, fromMe, ekTur(bekleyen.chatId), ekTur(toHam), ekTur(remoteHam), eslesme, an - bekleyen.bas));
      }
      const olay = ad === "message_ack" ? ackOlay(msg, ack, an) : olusturOlay(msg, an);
      const oldu = ad === "message_ack" ? ackEslesir(olay, bekleyen) : olusturEslesir(olay, bekleyen);
      if (oldu) kabul();
      else adaylar.push({ tur: ad === "message_ack" ? "ack" : "olustur", olay });
    } catch (_e) {}
  };
  const ackDinle = (msg, ack) => isle("message_ack", msg, ack);
  const olusturDinle = (msg) => isle("message_create", msg, undefined);
  const birak = () => {
    if (kapali) return;
    kapali = true;
    if (zamanlayici) clearTimeout(zamanlayici);
    zamanlayici = null;
    if (client && typeof client.off === "function") {
      client.off("message_ack", ackDinle);
      client.off("message_create", olusturDinle);
    } else if (client && typeof client.removeListener === "function") {
      client.removeListener("message_ack", ackDinle);
      client.removeListener("message_create", olusturDinle);
    }
  };
  const kabul = () => {
    if (kanitVar || kapali) return;
    kanitVar = true;
    if (!bekleyenResolve) return;
    const coz = bekleyenResolve;
    bekleyenResolve = null;
    ozet();
    birak();
    coz(sonuc());
  };
  async function aliasYukle() {
    if (!client || typeof client.getContactLidAndPhone !== "function") return;
    let liste;
    try {
      liste = await client.getContactLidAndPhone([bekleyen.chatId]);
    } catch (_e) {
      return;
    }
    const satir = Array.isArray(liste) ? liste[0] : null;
    if (!satir || typeof satir !== "object") return;
    if (satir.lid) bekleyen.alias.add(String(satir.lid));
    if (satir.pn) bekleyen.alias.add(String(satir.pn));
    for (const a of adaylar) {
      const oldu = a.tur === "ack" ? ackEslesir(a.olay, bekleyen) : olusturEslesir(a.olay, bekleyen);
      if (oldu) kabul();
    }
  }
  let bagli = false;
  if (client && typeof client.on === "function") {
    client.on("message_ack", ackDinle);
    client.on("message_create", olusturDinle);
    bagli = true;
  }
  function bekle() {
    if (!bagli) {
      ozet();
      birak();
      return Promise.resolve({ durum: "belirsiz", kod: "bos_donus_belirsiz" });
    }
    if (kanitVar || kapali) {
      const hazir = sonuc();
      ozet();
      birak();
      return Promise.resolve(hazir);
    }
    const gecen = Math.max(0, simdi() - bekleyen.bas);
    const kalan = Math.max(0, pencere - gecen);
    return new Promise((resolve) => {
      bekleyenResolve = resolve;
      zamanlayici = setTimeout(() => {
        if (kapali) return;
        const coz = bekleyenResolve;
        bekleyenResolve = null;
        ozet();
        birak();
        if (coz) coz(sonuc());
      }, kalan);
    });
  }
  return { bekle, birak, aliasYukle, kanit: () => kanitVar };
}

function zamanMs(ts) {
  const n = Number(ts);
  if (!Number.isFinite(n) || n <= 0) return 0;
  if (n < 1e12) return Math.round(n * 1000);
  return Math.round(n);
}

function fetchDogrula(mesajlar, bas, toleransMs) {
  const alt = (Number(bas) || 0) - (Number(toleransMs) || 0);
  for (const m of mesajlar || []) {
    if (!m || m.fromMe !== true) continue;
    const ts = zamanMs(m.timestamp);
    if (ts > 0 && ts >= alt) return true;
  }
  return false;
}

function fetchKanit(chat, bas, toleransMs, zamanAsimiMs) {
  const ms = Number(zamanAsimiMs) || FETCH_TIMEOUT_MS;
  if (!chat || typeof chat.fetchMessages !== "function") {
    return Promise.resolve({ durum: "belirsiz", kod: "bos_donus_belirsiz" });
  }
  return sureli(() => chat.fetchMessages({ limit: FETCH_LIMIT }), ms).then((paket) => {
    if (!paket || paket.zamanAsimi || paket.hata) {
      return { durum: "belirsiz", kod: "bos_donus_belirsiz" };
    }
    if (fetchDogrula(paket.donus, bas, toleransMs == null ? FETCH_TOLERANS_MS : toleransMs)) {
      return { durum: "gonderildi", kod: "fetch_dogrulandi" };
    }
    return { durum: "belirsiz", kod: "bos_donus_belirsiz" };
  });
}

function ackBekle(client, chatId, bas, pencereMs, simdiFn) {
  return kanitBaslat(client, chatId, bas, pencereMs, simdiFn).bekle();
}

function sureli(is, ms) {
  return new Promise((resolve) => {
    let bitti = false;
    const timer = setTimeout(() => {
      if (bitti) return;
      bitti = true;
      resolve({ zamanAsimi: true });
    }, ms);
    Promise.resolve()
      .then(() => (typeof is === "function" ? is() : is))
      .then((donus) => {
        if (bitti) return;
        bitti = true;
        clearTimeout(timer);
        resolve({ donus: donus });
      })
      .catch((hata) => {
        if (bitti) return;
        bitti = true;
        clearTimeout(timer);
        resolve({ hata: hata });
      });
  });
}

function yariyiGeriAl(kuyruk, item) {
  if (!item) return { eklendi: false, neden: "yok" };
  if (item.dogrulaniyor) return { eklendi: false, neden: "dogrulama" };
  if (item._kapali || item.durum === "gonderildi" || item.durum === "basarisiz" || item.durum === "belirsiz") {
    return { eklendi: false, neden: "bitti" };
  }
  const sira = kuyruk || [];
  if (sira.indexOf(item) >= 0) return { eklendi: false, neden: "zaten" };
  item.kusak = (item.kusak || 0) + 1;
  item.durum = "bekliyor";
  sira.unshift(item);
  return { eklendi: true, neden: "geri" };
}

function gonderimBitir(item, kuyruk, sinif, kusak) {
  const sira = kuyruk || [];
  if (!item || !sinif) return { uygulandi: false };
  const guncel = item.kusak === kusak;
  const basari = sinif.durum === "gonderildi";
  if (item._kapali && !(basari && item.durum !== "gonderildi")) return { uygulandi: false };
  const kapat = () => {
    item.askida = false;
    item._kapali = true;
    const i = sira.indexOf(item);
    if (i >= 0) sira.splice(i, 1);
  };
  if (!guncel && basari) {
    item.durum = "gonderildi";
    item.kod = "";
    kapat();
    return { uygulandi: true };
  }
  if (!guncel && sinif.kod === "zaman_asimi" && item.askida && item.durum !== "gonderildi") {
    item.durum = "basarisiz";
    item.kod = "zaman_asimi";
    kapat();
    return { uygulandi: true };
  }
  if (!guncel) {
    item.askida = false;
    return { uygulandi: false };
  }
  item.durum = sinif.durum;
  item.kod = sinif.kod || "";
  item.askida = false;
  if (sinif.durum === "gonderildi" || sinif.durum === "basarisiz" || sinif.durum === "belirsiz") kapat();
  return { uygulandi: true };
}

function sonucSakla(harita, anahtar, kayit, simdi) {
  const kim = String(anahtar || "").trim();
  if (!harita || !kim) return;
  const once = harita.get(kim) || {};
  once.durum = kayit.durum;
  once.kod = kayit.kod || "";
  once.tur = kayit.tur || once.tur || "metin";
  once.bayt = Math.max(0, parseInt(kayit.bayt, 10) || 0);
  once.sure = Math.max(0, parseInt(kayit.sure, 10) || 0);
  once.sonMs = simdi;
  harita.set(kim, once);
}

function sonucGetir(harita, anahtar, simdi, ttl) {
  const kim = String(anahtar || "").trim();
  if (!harita || !kim || !harita.has(kim)) return { durum: "yok", kod: "" };
  const kayit = harita.get(kim);
  const bitti = kayit.durum === "gonderildi" || kayit.durum === "basarisiz" || kayit.durum === "belirsiz";
  const yas = simdi - Number(kayit.sonMs || 0);
  if (bitti && kayit.sonMs && yas > (ttl || SONUC_TTL_MS)) {
    harita.delete(kim);
    return { durum: "yok", kod: "" };
  }
  return { durum: kayit.durum || "bekliyor", kod: kayit.kod || "" };
}

function logSatiri(anahtar, tur, bayt, durum, kod, sure) {
  const kim = String(anahtar || "").replace(/\d{8,}/g, "[num]").slice(0, 80);
  const turK = tur === "pdf" ? "pdf" : "metin";
  const durumK = durum === "gonderildi" || durum === "belirsiz" ? durum : "basarisiz";
  const kodK = String(kod || "-").toLowerCase().replace(/[^a-z0-9_]/g, "").slice(0, 32) || "-";
  const baytK = Math.max(0, parseInt(bayt, 10) || 0);
  const sureK = Math.max(0, parseInt(sure, 10) || 0);
  return "kim=" + (kim || "-") + " tur=" + turK + " bayt=" + baytK + " sonuc=" + durumK + " kod=" + kodK + " sure=" + sureK;
}

module.exports = {
  kalemEkle,
  ekAyikla,
  gonderGovde,
  gonderSecenek,
  cozulmusBoyut,
  PDF_LIMIT,
  MEDYA_TIMEOUT_MS,
  SONUC_TTL_MS,
  ACK_PENCERE_MS,
  FETCH_TOLERANS_MS,
  FETCH_TIMEOUT_MS,
  sonucSinifla,
  ackOlay,
  ackEslesir,
  olusturOlay,
  olusturEslesir,
  tekBekleyen,
  kanitBaslat,
  fetchDogrula,
  fetchKanit,
  ackBekle,
  sureli,
  yariyiGeriAl,
  gonderimBitir,
  sonucSakla,
  sonucGetir,
  logSatiri,
};
