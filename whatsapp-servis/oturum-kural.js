/** Oturum kuralları. Chrome başlatmaz, numara ve mesaj tutmaz. */

function idleKapanirMi(oturum, simdi, idleMs) {
  if (!oturum) return false;
  if (oturum.tenantId === "default") return false;
  if (oturum.pinKeepAlive) return false;
  if (!oturum.client && !oturum.chromeHeld) return false;
  const kuyruk = oturum.queue || [];
  if (kuyruk.length > 0 || oturum.queueBusy) return false;
  if (oturum.status === "starting" || oturum.initPromise) return false;
  if (simdi - (oturum.lastUsedAt || 0) <= idleMs) return false;
  return true;
}

function tekUcus(harita, anahtar, is) {
  const varOlan = harita.get(anahtar);
  if (varOlan) return varOlan;
  let ucus;
  ucus = Promise.resolve()
    .then(() => is())
    .finally(() => {
      if (harita.get(anahtar) === ucus) harita.delete(anahtar);
    });
  harita.set(anahtar, ucus);
  return ucus;
}

module.exports = { idleKapanirMi, tekUcus };
