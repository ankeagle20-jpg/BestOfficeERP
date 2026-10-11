# -*- coding: utf-8 -*-
"""Durum kaydı: ortak yazma, önbellek, Sözleşmeler filtresi. Canlı DB yok."""
from datetime import date
from pathlib import Path
import subprocess

from musteri_durum_yaz import musteri_durum_yaz_alanlari, musteri_durum_yaz_json
from utils.musteri_gorunur import (
    musteri_liste_durum_sql,
    musteri_liste_gorunur_sql,
    request_musteri_durum_filtre,
    request_pasifleri_dahil,
)

ROOT = Path(__file__).resolve().parent
FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_yaz_alanlari():
    p = musteri_durum_yaz_alanlari({"durum": "pasif"})
    a = musteri_durum_yaz_alanlari({"durum": "aktif", "kapanis_tarihi": "2024-01-01", "kapanis_sonrasi_borc_ay": 2})
    check("pasif is_active false", p["is_active"] is False)
    check("pasif kapanis bugun", p["kapanis_tarihi"] == date.today())
    check("pasif oda sil varsayilan", p["oda_sil"] is True)
    check("aktif is_active true", a["is_active"] is True)
    check("aktif kapanis None", a["kapanis_tarihi"] is None)
    check("aktif borc ay None", a["kapanis_sonrasi_borc_ay"] is None)
    check("aktif oda kalir", a["oda_sil"] is False)
    p0 = musteri_durum_yaz_alanlari({"durum": "pasif", "kapanis_sonrasi_borc_ay": 0, "kapanis_tarihi": "2026-03-15"})
    check("pasif 0 korunur", p0["kapanis_sonrasi_borc_ay"] == 0)
    check("pasif verilen tarih", p0["kapanis_tarihi"] == date(2026, 3, 15))
    bos = musteri_durum_yaz_alanlari({"durum": "pasif", "kapanis_sonrasi_borc_ay": ""})
    check("bos hepsi", bos["kapanis_sonrasi_borc_ay"] is None)
    check("kati gecersiz", musteri_durum_yaz_alanlari({"durum": "plan"}, kati=True) is None)
    check("kaydet plan->aktif", musteri_durum_yaz_alanlari({"durum": "plan"}, kati=False)["durum"] == "aktif")
    j = musteri_durum_yaz_json(p)
    check("json durum pasif", j["durum"] == "pasif" and j["is_active"] is False)
    kart_p = musteri_durum_yaz_alanlari({"durum": "pasif"}, oda_bosalt=False)
    check("kart pasif oda dokunma", kart_p["oda_sil"] is False)
    check("kart pasif is_active", kart_p["is_active"] is False)
    check("kart pasif durum", kart_p["durum"] == "pasif")
    kart_a = musteri_durum_yaz_alanlari({"durum": "aktif"}, oda_bosalt=False)
    check("kart aktif oda yazmaz", kart_a["oda_sil"] is False)
    check("kart aktif is_active", kart_a["is_active"] is True)
    durum_p = musteri_durum_yaz_alanlari({"durum": "pasif"}, kati=True, oda_bosalt=True)
    check("durum ucu pasif oda sil", durum_p["oda_sil"] is True)
    check("durum ucu pasif is_active", durum_p["is_active"] is False)
    oda_mevcut = 215
    check("kart pasif oda kaydi kalir", (None if kart_p["oda_sil"] else oda_mevcut) == 215)
    check("durum ucu oda kaydi silinir", (None if durum_p["oda_sil"] else oda_mevcut) is None)
    check("kart aktif oda kaydi kalir", (None if kart_a["oda_sil"] else oda_mevcut) == 215)


def test_iki_uc_ayni():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("kaydet helper", "alanlar = musteri_durum_yaz_alanlari(data, kati=False, oda_bosalt=False)" in src)
    check("durum ucu helper", "alanlar = musteri_durum_yaz_alanlari(data, kati=True, oda_bosalt=True)" in src)
    check("kaydet is_active", "is_active = %s" in src and "kart_aktif" in src)
    kaydet = src[src.find("def kaydet"): src.find("def resim_yukle")]
    check("kaydet cevap durum", "musteri_durum_yaz_json(alanlar)" in kaydet)
    check("kaydet oda kolonuna dokunmaz", "hazir_ofis_oda_no" not in kaydet)
    durum_fn = src[src.find("def api_musteri_durum_guncelle"): src.find("def api_tahmini")]
    check("durum ucu oda CASE", "hazir_ofis_oda_no = CASE WHEN %s THEN NULL ELSE hazir_ofis_oda_no END" in durum_fn)
    kyc = (ROOT / "routes" / "musteri_routes.py").read_text(encoding="utf-8")
    check("kyc helper", "musteri_durum_yaz_alanlari(data, kati=False, oda_bosalt=False)" in kyc)
    check("kyc is_active", "is_active=%s" in kyc)
    check("kyc pasif oda korunur", "hazir_ofis_oda_no=CASE WHEN %s THEN hazir_ofis_oda_no ELSE %s END" in kyc)
    check("kyc ayri istek", "/musteriler/api/kyc/kaydet" not in kaydet)


def test_liste_filtre():
    eski = musteri_liste_gorunur_sql("", pasifleri_dahil=False)
    check("eski varsayilan pasif gizler", "<> 'pasif'" in eski)
    hepsi_eski = musteri_liste_gorunur_sql("", pasifleri_dahil=True)
    check("eski pasifleri_dahil hepsi", "pasif" not in hepsi_eski.split("arsivli")[-1] or "<>" not in hepsi_eski)
    check("durum aktif", "<> 'pasif'" in musteri_liste_durum_sql("", durum="aktif"))
    check("durum pasif", "= 'pasif'" in musteri_liste_durum_sql("", durum="pasif"))
    hepsi = musteri_liste_durum_sql("", durum="hepsi")
    check("durum hepsi arsiv", "arsivli" in hepsi and "= 'pasif'" not in hepsi)
    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context("/x"):
        check("durum yok None", request_musteri_durum_filtre() is None)
        check("pasifleri varsayilan false", request_pasifleri_dahil() is False)
    with app.test_request_context("/x?durum=hepsi"):
        check("durum hepsi", request_musteri_durum_filtre() == "hepsi")
    with app.test_request_context("/x?durum=pasif"):
        check("durum pasif q", request_musteri_durum_filtre() == "pasif")


def test_js():
    kod = r"""
const { JSDOM } = (function () { try { return require('jsdom'); } catch (e) { return {}; } })();
const fs = require('fs');
const path = require('path');
const js = fs.readFileSync(path.join(__dirname, 'static/js/musteri_durum.js'), 'utf8');
const store = {};
const sessionStorage = {
    get length() { return Object.keys(store).length; },
    key: function (i) { return Object.keys(store)[i]; },
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem: function (k, v) { store[k] = String(v); },
    removeItem: function (k) { delete store[k]; }
};
global.sessionStorage = sessionStorage;
global.window = {
    __girisJsonCache: new Map(),
    __girisMusteriPrefetched: new Set(['9']),
    __girisMusteriDetayTazeMid: null
};
global.girisJsonCacheInvalidatePrefix = function (prefix) {
    var keys = Array.from(window.__girisJsonCache.keys());
    for (var i = 0; i < keys.length; i++) {
        if (keys[i].indexOf(prefix) === 0) window.__girisJsonCache.delete(keys[i]);
    }
};
eval(js);
const url = '/giris/api/musteri/9';
window.__girisJsonCache.set(url, { ts: Date.now(), data: { ok: true, musteri: { durum: 'aktif', id: 9 } } });
sessionStorage.setItem('__giris_json_persist__' + url, JSON.stringify({ ts: Date.now(), data: { ok: true, musteri: { durum: 'aktif' } } }));
girisMusteriKartOnbellekSil(9);
if (window.__girisJsonCache.has(url)) process.exit(2);
if (sessionStorage.getItem('__giris_json_persist__' + url)) process.exit(3);
if (window.__girisMusteriPrefetched.has('9')) process.exit(4);
if (!girisMusteriDetayTazeMi(9)) process.exit(5);
girisMusteriKartOnbellekYazDurum(9, { durum: 'pasif', kapanis_tarihi: '2026-10-12', is_active: false });
window.__girisJsonCache.set(url, { ts: Date.now(), data: { ok: true, musteri: { durum: 'aktif', id: 9 } } });
girisMusteriKartOnbellekYazDurum(9, { durum: 'pasif', kapanis_tarihi: '2026-10-12', is_active: false, kapanis_sonrasi_borc_ay: 0 });
if (window.__girisJsonCache.get(url).data.musteri.durum !== 'pasif') process.exit(6);
const el = { value: '' };
global.kapanisSonrasiBorcAyDegeri = function (raw) {
    if (raw == null || String(raw).trim() === '') return null;
    var n = parseInt(raw, 10);
    return (!isNaN(n) && n >= 0 && n <= 12) ? n : null;
};
kapanisAySecimYaz(el, { yeniPasif: true }, true);
if (el.value !== '0') process.exit(7);
kapanisAySecimYaz(el, { kapanisAy: null }, true);
if (el.value !== '') process.exit(8);
kapanisAySecimYaz(el, { kapanisAy: 2 }, true);
if (el.value !== '2') process.exit(9);
kapanisAySecimYaz(el, {}, false);
if (el.value !== '') process.exit(10);
const html = fs.readFileSync(path.join(__dirname, 'templates/giris/index.html'), 'utf8');
if (html.indexOf('sozlesmeler-durum-filtre') < 0) process.exit(11);
if (html.indexOf('ttlMs: __musteriTaze ? 0') < 0) process.exit(12);
if (html.indexOf('girisMusteriKartOnbellekSil') < 0) process.exit(13);
if (html.indexOf("durum=' + encodeURIComponent(sozlesmelerDurumFiltreDegeri())") < 0) process.exit(14);
process.exit(0);
"""
    r = subprocess.run(["node", "-e", kod], cwd=str(ROOT), capture_output=True, text=True)
    check("js onbellek ve varsayilan 0", r.returncode == 0, (r.stderr or r.stdout or "")[:200])


def test_html_diger_ekran():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("filtre wrap gizli", 'id="sozlesmeler-durum-filtre-wrap"' in html and "display:none" in html)
    check("switchTab gizler", "_dfw.style.display = (index === 6)" in html)
    check("pasifleri goster duruyor", 'id="giris-pasifleri-dahil"' in html)
    check("senaryo dokunulmadi", "s01_durum" not in html)


def main():
    test_yaz_alanlari()
    test_iki_uc_ayni()
    test_liste_filtre()
    test_js()
    test_html_diger_ekran()
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("musteri_durum_kaydet ok", "checks", 23 + 10 + 9 + 1 + 4)


if __name__ == "__main__":
    main()
