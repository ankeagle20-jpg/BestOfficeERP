/** Kuyruk idempotency. Chrome ve ağ yok. Telefon ve mesaj loglanmaz. */

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
    const kayit = {
      telefon: item.telefon,
      mesaj: item.mesaj,
      anahtar,
      durum: "bekliyor",
    };
    if (anahtar) durumlar.set(anahtar, kayit);
    sira.push(kayit);
    eklenen += 1;
    oge.push({ tekrar: false, durum: "bekliyor" });
  }
  return { eklenen, tekrar, oge };
}

module.exports = { kalemEkle };
