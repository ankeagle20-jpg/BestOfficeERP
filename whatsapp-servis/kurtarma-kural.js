/** Dongu kurtarma kurali. Chrome/ag yok; kapatma ve baslatma disaridan verilir.
 * Yalniz servisin kendi Chrome'u kapatilir: PID ancak ebeveyni bu servis
 * sureci ve profil dizini session-<tenant> ile eslesirse kapatilabilir.
 */

const MAKS_DENEME = 3;
const SICRAMA_ESIK_MS = 60 * 1000;
const KURTARMA_ARA_MS = 5 * 60 * 1000;

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

function chromeKokKaydet(oturum, pid, simdiMs) {
  const n = Number(pid);
  if (!oturum || !Number.isInteger(n) || n <= 0) return false;
  oturum.chromeKokPid = n;
  oturum.chromeKokMs = Number(simdiMs) || 0;
  return true;
}

function profilJetonu(tenantId) {
  return "session-" + String(tenantId || "default");
}

/** Profil dizini tam jeton. session-default2 veya baska user-data-dir eslesmez. */
function komutBuProfil(komut, tenantId) {
  const cmd = String(komut || "").toLowerCase();
  const jeton = profilJetonu(tenantId).toLowerCase();
  if (!cmd || !jeton) return false;
  let i = cmd.indexOf(jeton);
  while (i >= 0) {
    const once = i === 0 ? "" : cmd.charAt(i - 1);
    const sonra = cmd.charAt(i + jeton.length);
    const sinirOnce = !once || /[^a-z0-9_]/.test(once);
    const sinirSonra = !sonra || /[^a-z0-9_]/.test(sonra);
    if (sinirOnce && sinirSonra) return true;
    i = cmd.indexOf(jeton, i + 1);
  }
  return false;
}

function profilDurumu(surec, tenantId) {
  if (!surec || surec.wmiHata) return "wmi";
  if (typeof surec.komut === "string") {
    return komutBuProfil(surec.komut, tenantId) ? "evet" : "hayir";
  }
  if (surec.profil === true) return "evet";
  if (surec.profil === false) return "hayir";
  return "wmi";
}

function ebeveynDurum(surec, kendiPid) {
  if (!surec) return "baska";
  if (Number(surec.ppid) === Number(kendiPid)) return "servis";
  if (surec.ppidYasiyor === false) return "olmus";
  return "baska";
}

/**
 * Kapatilacak kok PID'ler.
 * (a) Kayitli kok yasiyor ve profil eslesiyorsa yalniz o.
 * (b) Kayit yok/olu ise: profil eslesen ve ebeveyni bu node ya da olu olanlar.
 * Kisisel Chrome (profil hayir) listeye girse de kapatilmaz.
 * giris.wmiHata: hicbir PID kapatilmaz.
 */
function yetimSec(oturum, giris, bilgi) {
  const b = bilgi || {};
  const kendi = Number(b.kendiPid);
  const simdi = Number(b.simdi) || 0;
  if (giris && giris.wmiHata && !Array.isArray(giris)) {
    return { kapat: [], atla: [{ pid: 0, sebep: "wmi" }], bulundu: [] };
  }
  const liste = Array.isArray(giris) ? giris : [];
  const tenantId = oturum && oturum.tenantId;
  const kayit = Number(oturum && oturum.chromeKokPid);
  const atla = [];

  function yasSn(pid) {
    if (
      oturum &&
      Number(oturum.chromeKokPid) === Number(pid) &&
      oturum.chromeKokMs &&
      simdi
    ) {
      return Math.max(0, Math.round((simdi - Number(oturum.chromeKokMs)) / 1000));
    }
    return 0;
  }

  function paket(pidler) {
    const bulundu = pidler.map((pid) => {
      const p = liste.find((x) => Number(x.pid) === Number(pid)) || {};
      return { pid: Number(pid), yasSn: yasSn(pid), ebeveyn: ebeveynDurum(p, kendi) };
    });
    return { kapat: pidler.map(Number), atla, bulundu };
  }

  if (Number.isInteger(kayit) && kayit > 0) {
    const bulunan = liste.find((p) => Number(p.pid) === kayit);
    const yasiyor = bulunan ? bulunan.yasiyor !== false : false;
    if (yasiyor && bulunan) {
      const durum = profilDurumu(bulunan, tenantId);
      if (durum === "wmi") {
        atla.push({ pid: kayit, sebep: "wmi" });
        return { kapat: [], atla, bulundu: [] };
      }
      if (durum !== "evet" || !pidKapatilabilir(kayit, { kendiPid: kendi, profilEslesir: true })) {
        atla.push({ pid: kayit, sebep: "profil" });
        return { kapat: [], atla, bulundu: [] };
      }
      return paket([kayit]);
    }
  }

  const kapat = [];
  for (const p of liste) {
    const pid = Number(p.pid);
    if (!Number.isInteger(pid) || pid <= 0) continue;
    const durum = profilDurumu(p, tenantId);
    if (durum === "wmi") {
      atla.push({ pid, sebep: "wmi" });
      continue;
    }
    if (durum !== "evet") continue;
    if (ebeveynDurum(p, kendi) === "baska") continue;
    if (!pidKapatilabilir(pid, { kendiPid: kendi, profilEslesir: true })) {
      atla.push({ pid, sebep: "profil" });
      continue;
    }
    kapat.push(pid);
  }
  return paket(kapat);
}

function chromeSatiriOku(metin) {
  const liste = [];
  for (const satir of String(metin || "").split(/\r?\n/)) {
    const t = satir.trim();
    if (!/^\d+\|\d+\|[01?]$/.test(t)) continue;
    const parca = t.split("|");
    const o = { pid: Number(parca[0]), ppid: Number(parca[1]) };
    if (parca[2] === "?") o.wmiHata = true;
    else o.profil = parca[2] === "1";
    liste.push(o);
  }
  return liste;
}

function sicramaKarar(oturum, gecenMs, canli, simdi) {
  if (!(Number(gecenMs) > SICRAMA_ESIK_MS)) return { bak: false, toparla: false };
  if (!oturum) return { bak: true, toparla: false };
  if (
    oturum.status === "qr" ||
    oturum.status === "starting" ||
    oturum.kurtarmaCalisiyor === true
  ) {
    return { bak: true, toparla: false };
  }
  if (canli === true) return { bak: true, toparla: false };
  const son = Number(oturum.sonKurtarmaMs || 0);
  if (
    oturum.toparlamaDurumu === "toparlama_basarisiz" &&
    son &&
    Number(simdi) &&
    Number(simdi) - son < KURTARMA_ARA_MS
  ) {
    return { bak: true, toparla: false };
  }
  return { bak: true, toparla: true };
}

/**
 * Dogrulanmis koklere nazik kapat, 10 sn, sonra yalniz hala dogrulananlari zorla.
 * deps: listele, log, nazik, bekle, yasiyor, dogrula, zorla, lockKontrol, kendiPid, simdi
 */
async function yetimUygula(oturum, deps) {
  const d = deps || {};
  const log = typeof d.log === "function" ? d.log : () => {};
  let giris = [];
  try {
    giris = await d.listele();
  } catch (_e) {
    giris = { wmiHata: true };
  }
  const sec = yetimSec(oturum, giris, { kendiPid: d.kendiPid, simdi: d.simdi });
  if (sec.bulundu.length) {
    const ilk = sec.bulundu[0];
    log(
      "kurtarma_yetim_bulundu",
      "n=" + sec.bulundu.length + " yas_sn=" + ilk.yasSn + " ebeveyn=" + ilk.ebeveyn
    );
  }
  const sebepler = [];
  for (const a of sec.atla) {
    if (sebepler.indexOf(a.sebep) === -1) sebepler.push(a.sebep);
  }
  for (const sebep of sebepler) log("kurtarma_pid_atlandi", sebep);
  if (!sec.kapat.length) return { kapatilan: [] };
  if (typeof d.nazik === "function") await d.nazik(sec.kapat.slice());
  if (typeof d.bekle === "function") await d.bekle(10000);
  const kalan = [];
  for (const pid of sec.kapat) {
    if (typeof d.yasiyor === "function" && !d.yasiyor(pid)) continue;
    let taze = { profil: true, wmiHata: false };
    if (typeof d.dogrula === "function") taze = (await d.dogrula(pid)) || taze;
    if (taze.wmiHata) {
      log("kurtarma_pid_atlandi", "wmi");
      continue;
    }
    if (!taze.profil) {
      log("kurtarma_pid_atlandi", "profil");
      continue;
    }
    kalan.push(pid);
  }
  let zorlanan = [];
  if (kalan.length && typeof d.zorla === "function") {
    const donen = await d.zorla(kalan.slice());
    zorlanan = Array.isArray(donen) ? donen : kalan.slice();
  }
  const nazikOldu = [];
  for (const pid of sec.kapat) {
    if (kalan.indexOf(pid) !== -1) continue;
    if (typeof d.yasiyor === "function" && d.yasiyor(pid)) continue;
    nazikOldu.push(pid);
  }
  const kapandi = nazikOldu.concat(zorlanan);
  if (kapandi.length) log("kurtarma_yetim_kapatildi", "n=" + kapandi.length);
  if (typeof d.lockKontrol === "function") {
    const k = await d.lockKontrol();
    if (k && k.var && !k.tutan) log("kurtarma_lock_kaldi", "tutan_yok");
  }
  return { kapatilan: kapandi };
}

module.exports = {
  MAKS_DENEME,
  SICRAMA_ESIK_MS,
  KURTARMA_ARA_MS,
  hataSinifi,
  kilitAl,
  kilitBirak,
  pidKapatilabilir,
  kurtarmaAkisiCalistir,
  chromeKokKaydet,
  komutBuProfil,
  yetimSec,
  chromeSatiriOku,
  sicramaKarar,
  yetimUygula,
};
