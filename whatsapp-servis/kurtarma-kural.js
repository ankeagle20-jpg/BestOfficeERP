/** Dongu kurtarma kurali. Chrome/ag yok; kapatma ve baslatma disaridan verilir.
 * Yalniz servisin kendi Chrome'u kapatilir: PID ancak ebeveyni bu servis
 * sureci ve profil dizini session-<tenant> ile eslesirse kapatilabilir.
 */

const MAKS_DENEME = 3;

function hataSinifi(metin) {
  const m = String(metin || '').toLowerCase();
  if (!m) return 'yok';
  if (m.includes('already running')) return 'already_running';
  return 'diger';
}

function kilitAl(oturum) {
  if (!oturum || oturum.kurtarmaCalisiyor) return false;
  oturum.kurtarmaCalisiyor = true;
  return true;
}

function kilitBirak(oturum) {
  if (oturum) oturum.kurtarmaCalisiyor = false;
}

/** Kapatilabilir PID: sayi, kendi surec degil, profil dogrulamasi gecti. */
function pidKapatilabilir(pid, bilgi) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return false;
  const b = bilgi || {};
  if (n === Number(b.kendiPid)) return false;
  if (b.profilEslesir !== true) return false;
  return true;
}

/**
 * Totarlama akisi: baslat -> only already_running ise kapat+bekle -> yeniden dene.
 * Toplam baslatma denemesi maksDeneme ile sinirli; sonsuz dongu yok.
 * deps: { baslat, kapat, bekle, bekleMs, maksDeneme, durumYaz, log }
 * Donus: { ok, deneme, sinif, hata? } (baslat basari durumunda sonuc)
 */
async function kurtarmaAkisiCalistir(deps) {
  const d = deps || {};
  const maks = Math.max(1, Number(d.maksDeneme) || MAKS_DENEME);
  const bekle =
    typeof d.bekle === 'function'
      ? d.bekle
      : (ms) => new Promise((r) => setTimeout(r, ms));
  let deneme = 0;
  let sonSinif = 'yok';
  while (deneme < maks) {
    deneme += 1;
    try {
      const sonuc = await d.baslat();
      if (d.durumYaz) d.durumYaz('yok', deneme);
      return { ok: true, deneme, sinif: sonSinif, sonuc };
    } catch (err) {
      sonSinif = hataSinifi((err && err.message) || err);
      if (d.durumYaz) d.durumYaz(sonSinif, deneme);
      if (sonSinif !== 'already_running' || deneme >= maks) {
        return { ok: false, deneme, sinif: sonSinif, hata: err };
      }
      if (d.log) d.log('kurtarma_chrome_kapat', 'already_running');
      try {
        await d.kapat();
      } catch (_e) {
        /* kapatma hatasi donguyu bozmaz; sinir yine de isler */
      }
      await bekle(Math.max(0, Number(d.bekleMs) || 0));
    }
  }
  return { ok: false, deneme, sinif: sonSinif };
}

module.exports = {
  MAKS_DENEME,
  hataSinifi,
  kilitAl,
  kilitBirak,
  pidKapatilabilir,
  kurtarmaAkisiCalistir,
};
