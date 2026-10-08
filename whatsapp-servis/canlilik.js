/** Oturum canlılığı. Chrome başlatmaz, numara ve mesaj tutmaz. */

const HEARTBEAT_MS = 60 * 1000;
const HEARTBEAT_TIMEOUT_MS = 10 * 1000;
const TOPARLAMA_MS = 5 * 60 * 1000;
const HEARTBEAT_SERI = 3;
const { tekUcus } = require("./oturum-kural");

function heartbeatSonuc(state, hata, zamanAsimi) {
  if (zamanAsimi) return { canli: false, bozuk_neden: "zaman_asimi" };
  if (hata) return { canli: false, bozuk_neden: "hata" };
  if (String(state || "").toUpperCase() !== "CONNECTED") {
    return { canli: false, bozuk_neden: "durum" };
  }
  return { canli: true, bozuk_neden: "" };
}

function heartbeatUygula(oturum, sonuc, simdi) {
  const canli = Boolean(sonuc && sonuc.canli);
  oturum.sonKontrolMs = simdi;
  oturum.canli = canli;
  oturum.bozuk = !canli;
  oturum.bozukNeden = canli ? "" : String((sonuc && sonuc.bozuk_neden) || "hata");
  if (canli && oturum.status === "ready") oturum.toparlamaDurumu = "hazir";
  return oturum;
}

function planAdim(sonTickMs, simdiMs, bekleMs) {
  const gecen = sonTickMs ? simdiMs - sonTickMs : 0;
  const sicrama = Boolean(sonTickMs) && gecen > bekleMs * 2;
  return { sicrama, gecen };
}

function toparlamaKarar(oturum, simdi, aralikMs) {
  if (!oturum) return { basla: false, durum: "" };
  if (oturum.status === "qr" || oturum.toparlamaDurumu === "qr" || oturum.bozukNeden === "qr") {
    return { basla: false, durum: "qr" };
  }
  if (oturum.status === "baglaniyor" || oturum.status === "starting") {
    return { basla: false, durum: "baglaniyor" };
  }
  if (oturum.sonToparlamaMs && simdi - oturum.sonToparlamaMs < aralikMs) {
    return { basla: false, durum: "bekle" };
  }
  return { basla: true, durum: "baglaniyor" };
}

function durumBakGovde(oturum) {
  if (!oturum) {
    return {
      ok: true,
      bagli: false,
      hazir: false,
      durum: "yok",
      canli: false,
      son_kontrol_ms: 0,
      bozuk_neden: "",
      toparlama_durumu: "",
    };
  }
  const canli = oturum.canli === true;
  const hazir = canli && oturum.ready === true && oturum.status !== "qr" && oturum.status !== "baglaniyor";
  return {
    ok: true,
    bagli: hazir,
    hazir: hazir,
    durum: oturum.status || "",
    canli: canli,
    son_kontrol_ms: Number(oturum.sonKontrolMs || 0),
    bozuk_neden: String(oturum.bozukNeden || ""),
    toparlama_durumu: String(oturum.toparlamaDurumu || ""),
  };
}

function olcGetState(getState, timeoutMs) {
  return new Promise((resolve) => {
    let bitti = false;
    const timer = setTimeout(() => {
      if (bitti) return;
      bitti = true;
      resolve(heartbeatSonuc(null, false, true));
    }, timeoutMs);
    Promise.resolve()
      .then(() => getState())
      .then((state) => {
        if (bitti) return;
        bitti = true;
        clearTimeout(timer);
        resolve(heartbeatSonuc(state, false, false));
      })
      .catch(() => {
        if (bitti) return;
        bitti = true;
        clearTimeout(timer);
        resolve(heartbeatSonuc(null, true, false));
      });
  });
}

async function heartbeatAdim(oturum, getState, simdi, timeoutMs) {
  if (!oturum || !oturum.client) return { kontrol: false, toparla: false };
  if (oturum.status === "qr" || oturum.status === "starting" || oturum.status === "baglaniyor") {
    return { kontrol: false, toparla: false };
  }
  const sonuc = await olcGetState(getState, timeoutMs);
  heartbeatUygula(oturum, sonuc, simdi);
  return { kontrol: true, toparla: !sonuc.canli, sonuc };
}

async function toparlaOturum(oturum, deps) {
  const simdiFn = deps.simdi || (() => Date.now());
  const aralik = deps.aralikMs || TOPARLAMA_MS;
  return tekUcus(deps.ucus, String(oturum.tenantId || "default"), async () => {
    const karar = toparlamaKarar(oturum, simdiFn(), aralik);
    if (!karar.basla) {
      oturum.toparlamaDurumu = karar.durum;
      return { yapildi: false, neden: karar.durum };
    }
    const bas = simdiFn();
    oturum.sonToparlamaMs = bas;
    oturum.canli = false;
    oturum.bozuk = true;
    oturum.status = "baglaniyor";
    oturum.toparlamaDurumu = "baglaniyor";
    if (deps.log) deps.log("toparlama_basladi", String(oturum.tenantId || "default"));
    if (oturum.client && deps.destroy) {
      const eski = oturum.client;
      try {
        await deps.destroy(eski);
      } catch (_err) {
        oturum.bozukNeden = "destroy";
        oturum.toparlamaDurumu = "hata";
        oturum.status = "error";
        if (deps.log) deps.log("toparlama_bitti", "destroy");
        return { yapildi: false, neden: "destroy" };
      }
      if (oturum.client === eski) oturum.client = null;
      oturum.ready = false;
    }
    let sonuc = "hata";
    try {
      sonuc = await deps.initialize(oturum);
    } catch (_err) {
      sonuc = "hata";
    }
    const sure = Math.max(0, simdiFn() - bas);
    if (sonuc === "qr" || oturum.status === "qr") {
      oturum.status = "qr";
      oturum.canli = false;
      oturum.bozuk = true;
      oturum.bozukNeden = "qr";
      oturum.toparlamaDurumu = "qr";
      if (deps.log) deps.log("toparlama_bitti", "qr " + sure);
      return { yapildi: true, neden: "qr", sure: sure };
    }
    if (sonuc === "ready" || oturum.ready) {
      oturum.status = "ready";
      oturum.ready = true;
      oturum.canli = true;
      oturum.bozuk = false;
      oturum.bozukNeden = "";
      oturum.toparlamaDurumu = "hazir";
      oturum.sonKontrolMs = simdiFn();
      if (deps.log) deps.log("toparlama_bitti", "hazir " + sure);
      return { yapildi: true, neden: "hazir", sure: sure };
    }
    oturum.canli = false;
    oturum.bozuk = true;
    oturum.bozukNeden = "hata";
    oturum.toparlamaDurumu = "hata";
    oturum.status = "error";
    if (deps.log) deps.log("toparlama_bitti", "hata " + sure);
    return { yapildi: true, neden: "hata", sure: sure };
  });
}

function gonderimSuruyor(oturum) {
  if (!oturum) return false;
  if (oturum.gonderiliyor) return true;
  return Boolean(oturum.aktifKalem && oturum.aktifKalem.askida);
}

function toparlaEsik(oturum, adim, seri) {
  const once = Math.max(0, Number(seri) || 0);
  if (!adim || !adim.toparla) return { toparla: false, seri: 0 };
  const yeni = once + 1;
  const neden = String((adim.sonuc && adim.sonuc.bozuk_neden) || "");
  if (gonderimSuruyor(oturum) && yeni < HEARTBEAT_SERI && neden !== "zaman_asimi") {
    return { toparla: false, seri: yeni };
  }
  return { toparla: true, seri: yeni };
}

function heartbeatBastir(oturum) {
  if (!oturum) return oturum;
  oturum.bozuk = false;
  oturum.bozukNeden = "";
  oturum.canli = oturum.ready === true && oturum.status === "ready";
  if (oturum.canli) oturum.toparlamaDurumu = "hazir";
  return oturum;
}

module.exports = {
  HEARTBEAT_MS,
  HEARTBEAT_TIMEOUT_MS,
  TOPARLAMA_MS,
  HEARTBEAT_SERI,
  heartbeatSonuc,
  heartbeatUygula,
  planAdim,
  toparlamaKarar,
  durumBakGovde,
  olcGetState,
  heartbeatAdim,
  toparlaOturum,
  gonderimSuruyor,
  toparlaEsik,
  heartbeatBastir,
};
