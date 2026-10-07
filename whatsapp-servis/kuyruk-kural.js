/** Kuyruk idempotency. Chrome ve ağ yok. Telefon, mesaj ve PDF baytı diske yazılmaz. */

const PDF_LIMIT = 2 * 1024 * 1024;

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
  if (!ek || typeof ek !== "object") return null;
  if (ek.mime !== "application/pdf") return null;
  const veri = String(ek.veri || "").replace(/\s/g, "");
  const boyut = cozulmusBoyut(veri);
  if (boyut < 5 || boyut > PDF_LIMIT) return null;
  const ad = String(ek.ad || "");
  if (!/^[0-9]{1,18}\.pdf$/.test(ad)) return null;
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
      ek = ekAyikla(item.ek);
      if (!ek) {
        oge.push({ tekrar: false, durum: "basarisiz" });
        continue;
      }
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

async function gonderGovde(item, send, mediaCtor) {
  const ek = item && item.ek ? item.ek : null;
  try {
    if (ek) {
      const media = mediaCtor(ek.mime, ek.veri, ek.ad);
      return await send(media, { caption: String(item.mesaj || "") });
    }
    return await send(String((item && item.mesaj) || ""));
  } finally {
    if (item) item.ek = null;
  }
}

module.exports = { kalemEkle, ekAyikla, gonderGovde, cozulmusBoyut, PDF_LIMIT };
