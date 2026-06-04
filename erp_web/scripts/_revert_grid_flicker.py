# -*- coding: utf-8 -*-
import pathlib
import re

p = pathlib.Path(__file__).resolve().parents[1] / "templates" / "giris" / "index.html"
text = p.read_text(encoding="utf-8")
orig = text

text = text.replace(
    """.sozlesmeler-aylik-grid.sozlesmeler-aylik-grid--panel-bekleniyor {
    visibility: hidden;
}
""",
    "",
)

helpers_pat = re.compile(
    r"function sozlesmelerAylikGridPanelBeklemeAc\(\) \{.*?\n\}\n\n/\*\*\n \* Tahsil \+ grid",
    re.DOTALL,
)
text = helpers_pat.sub("/**\n * Tahsil + grid", text, count=1)

son_pat = re.compile(
    r"function sozlesmelerAylikGridVeriSonBoyama\(midNav, selToken, settled\) \{.*?\n\}\n\n/\*\* Tahsil durumu \+ grid cache",
    re.DOTALL,
)
son_old = r'''function sozlesmelerAylikGridVeriSonBoyama(midNav, selToken, settled) {
    if (selToken != null) {
        if (window.__girisSelectMusteriToken !== selToken || String(selectedId) !== String(midNav)) return;
    } else if (String(selectedId) !== String(midNav)) {
        return;
    }
    if (sozlesmelerOdemeDuzeniManuelMi()) return;
    var val = function (i) {
        var s = settled && settled[i];
        return (s && s.status === 'fulfilled') ? s.value : null;
    };
    var mRes = val(0);
    var tRes = val(1);
    var gRes = val(2);
    var rRes = val(3);
    if (mRes && mRes.ok && mRes.musteri) {
        girisSonMusteriDetayHafifYukleme = false;
        if (typeof girisMusteriKiraFormuUygula === 'function') girisMusteriKiraFormuUygula(mRes.musteri);
    }
    sozlesmeTahsilSetFromJson(tRes);
    window.__girisTahsilDurumBuSecim = true;
    window.__musteriReelDonemTutarlari = (rRes && rRes.ok && rRes.map && typeof rRes.map === 'object') ? rRes.map : {};
    if (typeof sozlesmelerReelDbMapYenile === 'function') sozlesmelerReelDbMapYenile();
    window.__musteriReelDonemDetaylari = (rRes && rRes.ok && rRes.detay_map && typeof rRes.detay_map === 'object') ? rRes.detay_map : {};
    try {
        if (typeof girisAylikSatirYilKayitYukle === 'function') girisAylikSatirYilKayitYukle();
        if (typeof girisAylikSatirYilPanelDoldur === 'function') girisAylikSatirYilPanelDoldur();
    } catch (_eBrGrid) {}
    var cj = gRes;
    var cacheForKismi = (cj && cj.ok && cj.cache) ? cj.cache : null;
    if (cacheForKismi && Array.isArray(cacheForKismi.aylar) && cacheForKismi.aylar.length) {
        if (window.__musteriReelDonemTutarlari && Object.keys(window.__musteriReelDonemTutarlari).length
            && typeof sozlesmelerAylikCacheReelDbEsasla === 'function') {
            cacheForKismi = sozlesmelerAylikCacheReelDbEsasla(cacheForKismi);
        }
        if (sozlesmelerAylikOnbellekFormlaUyumlu(cacheForKismi)) {
            if (typeof girisTahsilatYilAyGridKismiSonSenkron === 'function') {
                girisTahsilatYilAyGridKismiSonSenkron(cacheForKismi);
            }
            sozlesmelerAylikCacheRender(cacheForKismi, true);
        } else {
            sozlesmelerAylikGuncelle();
            cacheForKismi = null;
        }
    } else {
        sozlesmelerAylikGuncelle();
        cacheForKismi = null;
    }
    try {
        if (typeof sozlesmelerReelYilSecimineGoreTutarAlani === 'function') sozlesmelerReelYilSecimineGoreTutarAlani();
        var cacheSon = window.__sozlesmelerAylikSonCacheObj || cacheForKismi;
        if (typeof sozlesmelerReelDbSonBoyaSira === 'function') {
            sozlesmelerReelDbSonBoyaSira(cacheSon);
        }
        if (typeof sozlesmeAylikSecimOzetGuncelle === 'function') sozlesmeAylikSecimOzetGuncelle();
        if (typeof sozlesmelerAylikSecilebilirlikYenile === 'function') sozlesmelerAylikSecilebilirlikYenile();
        if (typeof cariEkstreYukle === 'function' && String(cariEkstreMusteriIdSec()) === String(midNav)) {
            var __ekMid = String(midNav);
            requestAnimationFrame(function () {
                if (String(selectedId) !== __ekMid) return;
                try { cariEkstreYukle({ sessiz: true, dbEsas: true }); } catch (_eEkSon) {}
            });
        }
    } catch (_eSonBoy) {}
    var midK = String(midNav);
    function _sonDuzeltGecikmeli() {
        if (selToken != null && (window.__girisSelectMusteriToken !== selToken || String(selectedId) !== midK)) return;
        if (selToken == null && String(selectedId) !== midK) return;
        try {
            var cacheGuncel = window.__sozlesmelerAylikSonCacheObj || cacheForKismi;
            if (typeof sozlesmelerReelDbSonBoyaSira === 'function') {
                sozlesmelerReelDbSonBoyaSira(cacheGuncel);
            } else if (typeof girisTahsilatYilAyPaneldenTumGridBoyan === 'function' && !sozlesmelerReelDbKayitliMi()) {
                girisTahsilatYilAyPaneldenTumGridBoyan();
            }
        } catch (_eSonBoy2) {}
    }
    setTimeout(_sonDuzeltGecikmeli, 80);
    setTimeout(_sonDuzeltGecikmeli, 280);
}

/** Tahsil durumu + grid cache'''
text = son_pat.sub(son_old, text, count=1)

hizli_pat = re.compile(
    r"/\*\* Önce panel DB, sonra grid \(kısmi tahsil May 1000/500 kaybolmasın\)\. \*/\nfunction sozlesmelerAylikGridPanelSonraCiz\(cacheObj\) \{.*?\nfunction girisTahsilatHizliYuklePanelVeGrid\(cache, mid\) \{.*?\n\}\n/\*\* Açık olan sekme",
    re.DOTALL,
)
hizli_old = r'''/** Önce panel DB, sonra grid (kısmi tahsil May 1000/500 kaybolmasın). */
function girisTahsilatHizliYuklePanelVeGrid(cache, mid) {
    return new Promise(function (resolve) {
        function gridCiz() {
            try {
                if (cache && typeof girisTahsilatYilAyPanelCacheDenDoldur === 'function'
                    && !(typeof girisTahsilatYilAyPanelBellekteTahsilVarMi === 'function' && girisTahsilatYilAyPanelBellekteTahsilVarMi())) {
                    girisTahsilatYilAyPanelCacheDenDoldur(cache);
                }
            } catch (_ePcHy) {}
            try {
                if (typeof girisTahsilatYilAyGridOverridePaneldenSenkron === 'function') {
                    girisTahsilatYilAyGridOverridePaneldenSenkron();
                }
            } catch (_eOvPanHy) {}
            try {
                if (cache && typeof girisTahsilatYilAyGridOverrideFromCacheObj === 'function') {
                    girisTahsilatYilAyGridOverrideFromCacheObj(cache);
                }
            } catch (_eOvHy) {}
            var ok = sozlesmelerAylikCacheRender(cache, true);
            if (ok && typeof sozlesmelerAylikGridFormaGoreSonDuzelt === 'function') {
                sozlesmelerAylikGridFormaGoreSonDuzelt();
            } else if (!(typeof sozlesmelerReelDbKayitliMi === 'function' && sozlesmelerReelDbKayitliMi())
                && typeof girisTahsilatYilAyPaneldenTumGridBoyan === 'function') {
                girisTahsilatYilAyPaneldenTumGridBoyan();
            }
            try {
                if (typeof girisTahsilatYilAyPanelGuncelle === 'function') {
                    var yilAcik = window.__tahsilatSatirYilAcik || {};
                    Object.keys(yilAcik).forEach(function (yk) {
                        if (yilAcik[yk]) girisTahsilatYilAyPanelGuncelle(parseInt(yk, 10), { sunucuPanelYuklendi: true });
                    });
                }
            } catch (_ePanHy) {}
            if (window.__musteriReelDonemTutarlari && Object.keys(window.__musteriReelDonemTutarlari).length) {
                if (typeof sozlesmelerReelDbGridVePanelSonBoya === 'function') sozlesmelerReelDbGridVePanelSonBoya();
            } else if (typeof sozlesmelerReelYilSecimineGoreTutarAlani === 'function') {
                sozlesmelerReelYilSecimineGoreTutarAlani();
            }
            resolve(ok);
        }
        if (mid && typeof girisTahsilatYilAyPanelDbYukle === 'function') {
            girisTahsilatYilAyPanelDbYukle(mid, gridCiz);
        } else {
            gridCiz();
        }
    });
}
/** Açık olan sekme'''
text = hizli_pat.sub(hizli_old, text, count=1)

db_block_pat = re.compile(
    r"/\*\* Makbuz dağıtımı DB panel → __tahsilatSatirYilAyDetay \(yenilemede kaynak\)\. \*/\nfunction girisTahsilatYilAyPanelDbSonucUygula\(musteriId, j, opts\) \{.*?\nfunction girisTahsilatYilAyPanelDbYukle\(musteriId, doneCb, opts\) \{\n    var mid = musteriId",
    re.DOTALL,
)
db_block_old = r'''/** Makbuz dağıtımı DB panel → __tahsilatSatirYilAyDetay (yenilemede kaynak). */
function girisTahsilatYilAyPanelDbYukle(musteriId, doneCb, opts) {
    var mid = musteriId'''
text = db_block_pat.sub(db_block_old, text, count=1)

db_then_pat = re.compile(
    r"Promise\.resolve\(p\)\.then\(function \(j\) \{\n        if \(!girisTahsilatYilAyPanelDbSonucUygula\(mid, j, opts\)\) \{.*?\n    \}\)\.catch\(function \(\) \{\n        if \(doneCb\) doneCb\(null\);\n    \}\);",
    re.DOTALL,
)
db_then_old = r'''Promise.resolve(p).then(function (j) {
        if (!j || !j.ok) {
            if (doneCb) doneCb(null);
            return;
        }
        var byIso = j.by_iso || {};
        var tol = Number(SOZLESME_TAM_ODENDI_TOLERANS) || 0.05;
        window.__tahsilatSatirYilAyDetay = window.__tahsilatSatirYilAyDetay || {};
        Object.keys(byIso).forEach(function (iso) {
            var row = byIso[iso];
            if (!row || typeof row !== 'object') return;
            var m = String(iso).slice(0, 10).match(/^(\d{4})-(\d{2})/);
            if (!m) return;
            var yil = parseInt(m[1], 10);
            var ay = parseInt(m[2], 10);
            if (!yil || !ay) return;
            var ak = (typeof sozlesmeAylikAyKeyFromYilAy === 'function') ? sozlesmeAylikAyKeyFromYilAy(yil, ay) : (yil + '-' + ay);
            var dk = String(yil);
            if (!window.__tahsilatSatirYilAyDetay[dk]) window.__tahsilatSatirYilAyDetay[dk] = {};
            var dLoc = (window.__tahsilatSatirYilAyDetay[dk] || {})[ak];
            var rowDet = {
                aylik_tutar: Math.round((parseFloat(row.aylik) || 0) * 100) / 100,
                tahsil: Math.round((parseFloat(row.tahsil) || 0) * 100) / 100,
                kalan: 0,
                tahsil_tarih: String(row.tahsil_tarih || '').slice(0, 10)
            };
            if (typeof girisTahsilatYilAyPanelSatirNormalize === 'function') {
                girisTahsilatYilAyPanelSatirNormalize(rowDet, yil, ak);
            }
            if (dLoc && (dLoc.__saved || dLoc.__db_saved) && !(opts && opts.zorlaUygula)) {
                var tLoc = Math.round((parseFloat(dLoc.tahsil) || 0) * 100) / 100;
                var tApi = rowDet.tahsil;
                if (tLoc > tol && tApi + tol < tLoc) return;
                if (tApi <= tol && tLoc > tol) { /* sunucu borç — yerel tahsil ezilir */ }
                else if (tLoc > tol && tApi > tol && tLoc > tApi + tol) return;
            }
            window.__tahsilatSatirYilAyDetay[dk][ak] = {
                aylik_tutar: rowDet.aylik_tutar,
                tahsil: rowDet.tahsil,
                kalan: rowDet.kalan,
                tahsil_aktif: rowDet.tahsil > tol || rowDet.kalan > tol,
                tahsil_tarih: rowDet.tahsil_tarih || '',
                __panel_secili: false,
                __saved: true,
                __db_saved: true,
                __locked: true
            };
        });
        try { girisTahsilatYilAyKayitYaz(); } catch (_eDbW) {}
        try {
            if (typeof girisTahsilatYilAyPanelGuncelle === 'function') {
                var yilAcikDb = window.__tahsilatSatirYilAcik || {};
                Object.keys(yilAcikDb).forEach(function (yk) {
                    if (yilAcikDb[yk]) girisTahsilatYilAyPanelGuncelle(parseInt(yk, 10), { sunucuPanelYuklendi: true });
                });
            }
        } catch (_eDbPan) {}
        if (doneCb) doneCb(j);
    }).catch(function () {
        if (doneCb) doneCb(null);
    });'''
if db_then_pat.search(text):
    text = db_then_pat.sub(lambda m: db_then_old, text, count=1)

sel_new = """        var pGrid = girisFetchJsonCached('/giris/api/aylik-grid-cache?musteri_id=' + encodeURIComponent(midNav) + '&skip_match=1', { ttlMs: 0, persistMs: 0, swr: false });
        var pReel = girisFetchJsonCached('/giris/api/reel-donem-tutarlar?musteri_id=' + encodeURIComponent(midNav), { ttlMs: 0, persistMs: 0, swr: false });
        var pPanel = girisFetchJsonCached('/giris/api/tahsilat-panel-detay?musteri_id=' + encodeURIComponent(midNav), { ttlMs: 60000, persistMs: 300000, swr: false });
        try { sozlesmelerAylikGridPanelBeklemeAc(); } catch (_eGridPbAc) {}
        sozlesmeAylikBorclananMusteriDegistir(midNav);"""
sel_old = """        var pGrid = girisFetchJsonCached('/giris/api/aylik-grid-cache?musteri_id=' + encodeURIComponent(midNav) + '&skip_match=1', { ttlMs: 0, persistMs: 0, swr: false });
        var pReel = girisFetchJsonCached('/giris/api/reel-donem-tutarlar?musteri_id=' + encodeURIComponent(midNav), { ttlMs: 0, persistMs: 0, swr: false });
        sozlesmeAylikBorclananMusteriDegistir(midNav);"""
text = text.replace(sel_new, sel_old, 1)
text = text.replace(
    "Promise.allSettled([pMusteri, pTahsil, pGrid, pReel, pPanel]).then(function (rs) {",
    "Promise.allSettled([pMusteri, pTahsil, pGrid, pReel]).then(function (rs) {",
    1,
)

if text == orig:
    raise SystemExit("No flicker changes found to revert")
p.write_text(text, encoding="utf-8")
print("Reverted grid flicker fix only")
