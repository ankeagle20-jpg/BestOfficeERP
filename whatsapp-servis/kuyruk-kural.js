/** Kuyruk idempotency. Chrome ve ağ yok. Telefon, mesaj ve PDF baytı diske yazılmaz. */

const PDF_LIMIT = 2 * 1024 * 1024;
const MEDYA_TIMEOUT_MS = 75 * 1000;
const SONUC_TTL_MS = 2 * 60 * 60 * 1000;

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
  const ham = donus && donus.id;
  const id = ham && typeof ham === "object" ? ham._serialized : ham;
  if (!id) return { durum: "basarisiz", kod: "bos_donus" };
  return { durum: "gonderildi", kod: "" };
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
  if (item._kapali || item.durum === "gonderildi" || item.durum === "basarisiz") {
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
  if (sinif.durum === "gonderildi" || sinif.durum === "basarisiz") kapat();
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
  const bitti = kayit.durum === "gonderildi" || kayit.durum === "basarisiz";
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
  const durumK = durum === "gonderildi" ? "gonderildi" : "basarisiz";
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
  sonucSinifla,
  sureli,
  yariyiGeriAl,
  gonderimBitir,
  sonucSakla,
  sonucGetir,
  logSatiri,
};
