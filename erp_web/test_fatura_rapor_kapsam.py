# -*- coding: utf-8 -*-
"""Müşteriler raporu kapsamı: tek seçim, sentetik. Canlı veriye yazmaz."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def _grab(html, start, end):
    a = html.find(start)
    b = html.find(end, a + len(start)) if a >= 0 else -1
    if a < 0 or b < 0:
        raise SystemExit("grab fail %s .. %s" % (start, end))
    return html[a:b]


def test_kaynak(html):
    check("durum menu yalniz hepsi aktif pasif", "var opts = [{ v: '', t: 'Hepsi' }, { v: 'aktif', t: 'Aktif' }, { v: 'pasif', t: 'Pasif' }];" in html)
    check("istemci durum elemesi yok", "var fdEff" not in html and "data-fr-durum-sort" not in _grab(html, "function faturaRaporListeAraUygula", "function faturaRaporListeAraTemizle"))
    check("suzgec yalniz pasif yok", "parts.push('yalnız pasif')" not in html and "parts.push('yalnız aktif')" not in html)
    check("temizle tumu secer", "tmTemiz.checked = true" in html)
    check("sayfa boyu kisayolu duruyor", "? 5000 : 50" in html)
    cakis = _grab(html, "function initFaturaRaporFiltreCakisGiris", "initFaturaRaporPasiflrGiris")
    check("kapsam tiklamasi listeyi ister", "faturaRaporYukle()" in cakis and "faturaRaporListeAraUygula" not in cakis)
    check("secili kutu kalkmaz", "t.checked = true" in cakis)
    pasif = _grab(html, "initFaturaRaporPasiflrGiris", "initFaturaRaporCiftOlanlarGiris")
    check("pasif ikinci istek yok", "faturaRaporYukle" not in pasif)
    cift = _grab(html, "initFaturaRaporCiftOlanlarGiris", "initFaturaRaporBizimHesapGiris")
    bizim = _grab(html, "initFaturaRaporBizimHesapGiris", "initFaturaRaporFiltrePersistExtraGiris")
    check("cift olanlar yeniden ister", "faturaRaporYukle()" in cift)
    check("bizim hesap yeniden ister", "faturaRaporYukle()" in bizim)
    yukle = _grab(html, "function faturaRaporYukle()", "function ")
    check("tek kapsam parametresi", yukle.count("faturaRaporKapsamQuerySuffix()") == 1 and "sadece_faturali=1" in yukle)
    check("ay sayfa isteginde", "faturaRaporGirisAyQuerySuffix()" in yukle and "&page=1" in yukle)
    routes = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    ds = routes[routes.find("def _firma_ozet_dataset_from_request"):]
    ds = ds[: ds.find("\ndef ", 10)]
    i_where = ds.find("_fatura_rapor_musteri_where_sql")
    i_page = ds.find("_firma_ozet_sql_paging_queries")
    check("kapsam sayfadan once", 0 < i_where < i_page)


def test_js(html):
    funcs = "\n".join([
        _grab(html, "function faturaRaporKapsamSecimOku", "function faturaRaporFiltreTemizle"),
        _grab(html, "function faturaRaporFirmaFiltreDurumYaz", "function faturaRaporHizmetFiltreIcinTemel"),
        _grab(html, "function faturaRaporFiltreTemizle", "function faturaRaporYukle"),
        _grab(html, "function faturaRaporFirmaSatirSayaclari", "function faturaRaporFiltreBosKarar"),
        _grab(html, "function faturaRaporFiltreBosKarar", "function faturaRaporFirmaDurumSuzgecEtkin"),
    ])
    scenario = r"""
global.window = global;
var els = {};
function bag(id) {
  if (!els[id]) els[id] = { id: id, checked: false, value: '' };
  return els[id];
}
global.document = { getElementById: function (id) { return bag(id); } };
var yuklemeler = [];
function faturaRaporYukle() { yuklemeler.push(faturaRaporKapsamQuerySuffix()); }
function faturaRaporGirisAyQuerySuffix() { return '&giri_aylar=10'; }
function sayfa(total, pageSize, page) {
  var totalPage = total > 0 ? Math.max(1, Math.ceil(total / pageSize)) : page;
  return { page: page, totalPage: totalPage, pageSize: pageSize };
}
function tek(k) {
  var flags = ['tum_musteriler=1', 'sadece_aktif=1', 'pasifleri_dahil=1'].filter(function (f) { return k.indexOf(f) >= 0; });
  return flags.length === 1;
}
function iste(pageNo) {
  var u = '/faturalar/api/fatura-rapor?baslangic=2026-10-01&bitis=2026-10-31' + faturaRaporKapsamQuerySuffix() + faturaRaporGirisAyQuerySuffix() + '&page_size=50&page=1';
  if (pageNo && pageNo !== 1) u = u.replace(/([?&])page=\d+/, '$1page=' + pageNo);
  return u;
}
function ekran(nGor, nYuk, total, pageSize, page) {
  var karar = faturaRaporFiltreBosKarar(nGor, nYuk, total);
  var pg = sayfa(total, pageSize, page);
  return { gosterilen: karar.gosterilen, total: total, mesaj: karar.tabloMesaj, sayfa: pg.page, sayfaSon: pg.totalPage, kapsam: faturaRaporKapsamMetni() };
}
function yalniz(id) {
  ['fatura_rapor_tum_musteri', 'fatura_rapor_pasif_dahil', 'fatura_rapor_sadece_aktif'].forEach(function (x) { bag(x).checked = (x === id); });
}
var out = [];
function adim(name, ok, ek) { out.push({ name: name, ok: !!ok, ek: ek || '' }); }

yalniz('fatura_rapor_tum_musteri');
var u1 = iste(1);
var u2 = iste(2);
var e1 = ekran(50, 50, 58, 50, 1);
adim('ekim tum istek', tek(u1) && u1.indexOf('tum_musteriler=1') >= 0 && u1.indexOf('giri_aylar=10') >= 0 && u1.indexOf('page_size=50') >= 0 && u1.indexOf('page=1') >= 0 && u1.indexOf('sadece_aktif') < 0 && u1.indexOf('pasifleri_dahil') < 0, u1);
adim('ekim tum ekran', e1.gosterilen === 50 && e1.total === 58 && e1.sayfa === 1 && e1.sayfaSon === 2 && e1.mesaj === '' && e1.kapsam.indexOf('t\u00fcm kay\u0131tlar') >= 0, JSON.stringify(e1));
adim('sayfa 2 ayni parametre', u2.indexOf('tum_musteriler=1') >= 0 && u2.indexOf('giri_aylar=10') >= 0 && u2.indexOf('page_size=50') >= 0 && u2.indexOf('page=2') >= 0 && u2.indexOf('page=1') < 0, u2);

yalniz('fatura_rapor_pasif_dahil');
var up = iste(1);
var ep = ekran(45, 45, 45, 50, 1);
adim('pasiflr yeni istek', tek(up) && up.indexOf('pasifleri_dahil=1') >= 0 && up.indexOf('giri_aylar=10') >= 0 && up.indexOf('tum_musteriler') < 0, up);
adim('pasiflr ekran', ep.gosterilen === 45 && ep.total === 45 && ep.sayfaSon === 1 && ep.kapsam.indexOf('Pasiflr') >= 0, JSON.stringify(ep));

yalniz('fatura_rapor_sadece_aktif');
var ua = iste(1);
var ea = ekran(13, 13, 13, 50, 1);
adim('aktif yeni istek', tek(ua) && ua.indexOf('sadece_aktif=1') >= 0 && ua.indexOf('giri_aylar=10') >= 0 && ua.indexOf('pasifleri_dahil') < 0, ua);
adim('aktif eski pasif toplam degil', ea.gosterilen !== 0 && ea.total !== 45 && ea.gosterilen === 13 && ea.total === 13 && ea.kapsam.indexOf('aktif kartlar') >= 0, JSON.stringify(ea));

var sira = [
  ['fatura_rapor_tum_musteri', 50, 58, 't\u00fcm kay\u0131tlar', 2],
  ['fatura_rapor_pasif_dahil', 45, 45, 'Pasiflr', 1],
  ['fatura_rapor_sadece_aktif', 13, 13, 'aktif kartlar', 1],
  ['fatura_rapor_tum_musteri', 50, 58, 't\u00fcm kay\u0131tlar', 2]
];
var siraOk = sira.every(function (s) {
  yalniz(s[0]);
  var e = ekran(s[1], s[1], s[2], 50, 1);
  return e.gosterilen === s[1] && e.total === s[2] && e.sayfaSon === s[4] && e.kapsam.indexOf(s[3]) >= 0 && e.mesaj === '';
});
adim('tum pasif aktif tum tutarli', siraOk);

bag('fatura_rapor_sadece_aktif').checked = true;
bag('fatura_rapor_tum_musteri').checked = true;
bag('fatura_rapor_pasif_dahil').checked = true;
faturaRaporFirmaFiltreDurumYaz('pasif');
if (bag('fatura_rapor_sadece_aktif').checked) {
  bag('fatura_rapor_tum_musteri').checked = false;
  bag('fatura_rapor_pasif_dahil').checked = false;
}
faturaRaporKapsamSutunDurumHizala();
var tb = {
  querySelectorAll: function () {
    var rows = [];
    for (var i = 0; i < 45; i++) rows.push({ classList: { contains: function () { return false; } }, style: { display: '' } });
    return rows;
  }
};
var say = faturaRaporFirmaSatirSayaclari(tb);
var cel = faturaRaporFiltreBosKarar(say.nGor, say.nYuk, 45);
adim('celiskili kayit listeyi bosaltmaz', say.nGor === 45 && cel.tabloMesaj === '' && faturaRaporKapsamQuerySuffix() === '&sadece_aktif=1' && window.__faturaRaporFirmaFiltreDurum === 'aktif', String(say.nGor) + ' ' + faturaRaporKapsamQuerySuffix());

yalniz('fatura_rapor_pasif_dahil');
faturaRaporFirmaFiltreDurumYaz('pasif');
yuklemeler = [];
faturaRaporFiltreTemizle();
adim('filtre temizle tum', bag('fatura_rapor_tum_musteri').checked && !bag('fatura_rapor_pasif_dahil').checked && !bag('fatura_rapor_sadece_aktif').checked && yuklemeler.length === 1 && yuklemeler[0] === '&tum_musteriler=1' && window.__faturaRaporFirmaFiltreDurum === '', JSON.stringify(yuklemeler));

var bad = out.filter(function (x) { return !x.ok; });
out.forEach(function (x) { console.log((x.ok ? 'ok ' : 'FAIL ') + x.name + (x.ok ? '' : (' :: ' + x.ek))); });
if (bad.length) process.exit(1);
"""
    js = funcs + "\n" + scenario
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
        path = f.name
    try:
        r = subprocess.run(["node", path], capture_output=True, text=True, encoding="utf-8")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    sys.stdout.write(r.stdout or "")
    if r.returncode != 0:
        sys.stdout.write(r.stderr or "")
        check("node kapsam senaryolari", False, "exit %s" % r.returncode)
    else:
        check("node kapsam senaryolari", "FAIL " not in (r.stdout or ""))


def test_sunucu():
    from routes.faturalar_routes import _fatura_rapor_musteri_where_sql

    aktif = _fatura_rapor_musteri_where_sql(False, False, True)
    varsayilan = _fatura_rapor_musteri_where_sql(False, False, False)
    tum = _fatura_rapor_musteri_where_sql(False, True, False)
    pasif = _fatura_rapor_musteri_where_sql(True, False, False)
    hepsi = _fatura_rapor_musteri_where_sql(True, True, True)
    check("sadece_aktif where uygulanir", "is_active" in aktif and aktif == varsayilan and aktif == hepsi, aktif[:160])
    check("tum where pasiften farkli", tum != pasif and "NOT (" in pasif and tum != aktif, tum[:160])


if __name__ == "__main__":
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    test_kaynak(html)
    test_js(html)
    test_sunucu()
    if FAILS:
        print("FAIL", ", ".join(FAILS))
        sys.exit(1)
    print("fatura_rapor_kapsam ok")
