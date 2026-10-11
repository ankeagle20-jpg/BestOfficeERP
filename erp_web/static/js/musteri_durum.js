/* Müşteri durum kaydı: önbellek, yeni pasif varsayılanı, Sözleşmeler durum filtresi. */
var GIRIS_JSON_PERSIST_ON_EK = "__giris_json_persist__";

function girisJsonPersistInvalidatePrefix(prefix) {
    if (!prefix) return;
    try {
        var sil = [];
        for (var i = 0; i < sessionStorage.length; i++) {
            var k = sessionStorage.key(i);
            if (!k || k.indexOf(GIRIS_JSON_PERSIST_ON_EK) !== 0) continue;
            var url = k.slice(GIRIS_JSON_PERSIST_ON_EK.length);
            if (url.indexOf(prefix) === 0) sil.push(k);
        }
        for (var j = 0; j < sil.length; j++) sessionStorage.removeItem(sil[j]);
    } catch (_eP) {}
}

function girisMusteriKartOnbellekSil(mid) {
    if (mid == null || mid === "") return;
    var p = "/giris/api/musteri/" + String(mid);
    if (typeof girisJsonCacheInvalidatePrefix === "function") girisJsonCacheInvalidatePrefix(p);
    girisJsonPersistInvalidatePrefix(p);
    try {
        if (window.__girisMusteriPrefetched) window.__girisMusteriPrefetched.delete(String(mid));
    } catch (_ePf) {}
    window.__girisMusteriDetayTazeMid = String(mid);
}

function girisMusteriKartOnbellekYazDurum(mid, alanlar) {
    if (mid == null || mid === "" || !alanlar || !window.__girisJsonCache) return;
    var prefix = "/giris/api/musteri/" + String(mid);
    try {
        window.__girisJsonCache.forEach(function (entry, url) {
            if (String(url).indexOf(prefix) !== 0) return;
            var data = entry && entry.data;
            if (!data || !data.musteri) return;
            data.musteri.durum = alanlar.durum;
            data.musteri.kapanis_tarihi = alanlar.kapanis_tarihi || "";
            data.musteri.kapanis_sonrasi_borc_ay = alanlar.kapanis_sonrasi_borc_ay;
            data.musteri.is_active = alanlar.is_active;
        });
    } catch (_eY) {}
}

function girisMusteriDetayTazeMi(mid) {
    return !!(window.__girisMusteriDetayTazeMid && String(window.__girisMusteriDetayTazeMid) === String(mid));
}

function kapanisAySecimYaz(aySel, opts, showKap) {
    if (!aySel) return;
    if (!showKap) {
        aySel.value = "";
        return;
    }
    opts = opts || {};
    var kapAy = (typeof kapanisSonrasiBorcAyDegeri === "function")
        ? kapanisSonrasiBorcAyDegeri(opts.kapanisAy)
        : null;
    if (opts.yeniPasif && (kapAy === null || kapAy === undefined)) {
        aySel.value = "0";
        return;
    }
    aySel.value = (kapAy !== null && kapAy !== undefined) ? String(kapAy) : "";
}

function sozlesmelerDurumFiltreDegeri() {
    var el = document.getElementById("sozlesmeler-durum-filtre");
    if (!el) return "aktif";
    var v = String(el.value || "aktif").toLowerCase();
    if (v === "hepsi" || v === "tumu" || v === "all") return "hepsi";
    if (v === "pasif") return "pasif";
    return "aktif";
}

function sozlesmelerDurumFiltreAktifMi() {
    var tab = (typeof getActiveTabIndex === "function") ? getActiveTabIndex() : -1;
    return tab === 6 && !!document.getElementById("sozlesmeler-durum-filtre");
}

if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        girisJsonPersistInvalidatePrefix: girisJsonPersistInvalidatePrefix,
        girisMusteriKartOnbellekSil: girisMusteriKartOnbellekSil,
        girisMusteriKartOnbellekYazDurum: girisMusteriKartOnbellekYazDurum,
        girisMusteriDetayTazeMi: girisMusteriDetayTazeMi,
        kapanisAySecimYaz: kapanisAySecimYaz,
        sozlesmelerDurumFiltreDegeri: sozlesmelerDurumFiltreDegeri,
        sozlesmelerDurumFiltreAktifMi: sozlesmelerDurumFiltreAktifMi,
        GIRIS_JSON_PERSIST_ON_EK: GIRIS_JSON_PERSIST_ON_EK
    };
}
