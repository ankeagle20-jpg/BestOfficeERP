# -*- coding: utf-8 -*-
"""Müşteriler raporu sunucu süzgeci. Canlı veriye yazmaz."""
import os
import re
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


def test_sql():
    from routes.faturalar_routes import (
        _firma_ozet_build_rows_firma_sql,
        _firma_ozet_oda_sql,
        _firma_ozet_sql_paging_queries,
        _firma_ozet_sunucu_suzgec_sql,
    )

    bos_s, bos_p = _firma_ozet_sunucu_suzgec_sql(grup2=[], hizmet=[], durum="")
    check("suzgec yokken sql bos", bos_s == "" and bos_p == [])
    check("oda bos ve excel ayni", _firma_ozet_oda_sql("", excel_mode=False) == ("", []) and _firma_ozet_oda_sql("205", excel_mode=True) == ("", []))
    oda_n, oda_np = _firma_ozet_oda_sql("205", excel_mode=False)
    check("oda numara sayfadan once", " = %s" in oda_n and oda_np == [205])
    oda_h, oda_hp = _firma_ozet_oda_sql("hepsi", excel_mode=False)
    oda_y, oda_yp = _firma_ozet_oda_sql("yok", excel_mode=False)
    check("oda hepsi yok", "IS NOT NULL" in oda_h and oda_hp == [] and "IS NULL" in oda_y and oda_yp == [])

    g_sql, g_params = _firma_ozet_sunucu_suzgec_sql(grup2=["Ofis"], hizmet=[], durum="")
    check("grup2 exists", "grup2_etiketleri" in g_sql and g_params == [["Ofis"], ["Ofis"]])
    h_sql, h_params = _firma_ozet_sunucu_suzgec_sql(grup2=[], hizmet=["Sanal", "__FR_HIZMET_BOS"], durum="")
    check("hizmet ad ve bos", h_sql.count("ANY(%s)") == 2 and "__FR_HIZMET_BOS" not in h_sql and h_params == [["Sanal"], ["Sanal"]])
    d_a, d_ap = _firma_ozet_sunucu_suzgec_sql(grup2=[], hizmet=[], durum="aktif")
    d_p, _ = _firma_ozet_sunucu_suzgec_sql(grup2=[], hizmet=[], durum="pasif")
    d_x, d_xp = _firma_ozet_sunucu_suzgec_sql(grup2=[], hizmet=[], durum="dondurulmus")
    check("durum yalniz aktif pasif", "is_active" in d_a and d_ap == [] and "NOT (" in d_p and d_x == "" and d_xp == [])

    ortak = dict(
        musteri_where="TRUE",
        mk_df_sql="",
        giri_ay_sql="",
        ho_sql="",
        arama_sql="",
        firma_guncel_sql="0",
        hizli=True,
        cift_sql="",
        giri_yil_sql="",
    )
    eski = _firma_ozet_build_rows_firma_sql(**ortak)
    ayni = _firma_ozet_build_rows_firma_sql(**ortak, suzgec_sql="")
    check("suzgec argumani yokken sql ayni", eski == ayni)
    yeni = _firma_ozet_build_rows_firma_sql(**ortak, suzgec_sql=g_sql)
    no_order = re.sub(r"\s+ORDER\s+BY\s+2\s*$", "", yeni.rstrip(), flags=re.I)
    totals, page = _firma_ozet_sql_paging_queries(no_order, False)
    check(
        "grup2 sayfa diliminden once",
        "grup2_etiketleri" in totals
        and "COUNT(*)" in totals
        and "LIMIT %s" not in totals
        and "LIMIT %s OFFSET %s" in page
        and page.find("grup2_etiketleri") < page.find("LIMIT %s OFFSET %s"),
    )


def test_cache():
    from flask import Flask

    from routes.faturalar_routes import _firma_ozet_resp_cache_key

    app = Flask(__name__)
    with app.test_request_context("/faturalar/api/fatura-rapor?gorunum=firma_ozet&page=1&page_size=50&tum_musteriler=1"):
        k_bos = _firma_ozet_resp_cache_key()
    with app.test_request_context("/faturalar/api/fatura-rapor?gorunum=firma_ozet&page=1&page_size=50&tum_musteriler=1&grup2=Ofis&hizmet_turleri=Sanal&durum=aktif"):
        k_dolu = _firma_ozet_resp_cache_key()
    check("bos suzgec anahtari eski", "grup2=" not in k_bos and "durum=" not in k_bos and "tum_musteriler=1" in k_bos)
    check("dolu suzgec anahtari ayri", k_dolu != k_bos and "grup2=Ofis" in k_dolu and "hizmet_turleri=Sanal" in k_dolu and "durum=aktif" in k_dolu)


def test_js():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("sayfa boyu kisayolu duruyor", "? 5000 : 50" in html)
    ara = html[html.find("initFaturaRaporListeAramaGiris"): html.find("initFaturaRaporFirmaSayfalamaButonlari")]
    check("tus aramasi yeni istek kurmaz", "faturaRaporFirmaSunucuSuzgecSuffix" not in ara and "yeni q" in ara)
    yukle = html[html.find("function faturaRaporYukle()"): html.find("function faturaRaporYukle()") + 8000]
    check("liste istegi suzgeci tasir", "faturaRaporFirmaSunucuSuzgecSuffix()" in yukle and "faturaRaporKapsamQuerySuffix()" in yukle)
    check("grup2 hizmet uygula yeniden ister", html.count("PagingFetch('all')") == 2)
    a = html.find("function faturaRaporFirmaSunucuSuzgecSuffix")
    b = html.find("function faturaRaporYukle()", a)
    fn = html[a:b]
    js = fn + r"""
global.document = { getElementById: function () { return null; } };
global.faturaRaporFirmaFiltreGrup2DiziOku = function () { return []; };
global.faturaRaporFirmaFiltreHizmetDiziOku = function () { return []; };
global.faturaRaporFirmaFiltreDurumOku = function () { return ''; };
global.faturaRaporOdaFiltreOku = function () { return null; };
var bos = faturaRaporFirmaSunucuSuzgecSuffix();
global.faturaRaporFirmaFiltreGrup2DiziOku = function () { return ['Ofis']; };
global.faturaRaporFirmaFiltreHizmetDiziOku = function () { return ['Sanal']; };
global.faturaRaporFirmaFiltreDurumOku = function () { return 'aktif'; };
global.faturaRaporOdaFiltreOku = function () { return '205'; };
global.document = { getElementById: function () { return { value: 'ada' }; } };
var dolu = faturaRaporFirmaSunucuSuzgecSuffix();
var u = '/faturalar/api/fatura-rapor?baslangic=2026-10-01&bitis=2026-10-31&tum_musteriler=1&giri_aylar=10&page_size=50&page=1' + dolu;
var u2 = u.replace(/([?&])page=\d+/, '$1page=2');
function say(ad, ok, ek) { console.log((ok ? 'ok ' : 'FAIL ') + ad + (ok ? '' : (' :: ' + ek))); if (!ok) process.exitCode = 1; }
say('bos suzgec ek yok', bos === '', bos);
say('dolu suzgec parametreleri', dolu.indexOf('q=ada') >= 0 && dolu.indexOf('grup2=Ofis') >= 0 && dolu.indexOf('hizmet_turleri=Sanal') >= 0 && dolu.indexOf('durum=aktif') >= 0 && dolu.indexOf('hazir_ofis_oda=205') >= 0, dolu);
say('sayfa 2 kapsam ay suzgec', u2.indexOf('tum_musteriler=1') >= 0 && u2.indexOf('giri_aylar=10') >= 0 && u2.indexOf('page=2') >= 0 && u2.indexOf('grup2=Ofis') >= 0 && u2.indexOf('page=1') < 0, u2);
"""
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
        check("node suzgec", False, "exit %s" % r.returncode)
    else:
        check("node suzgec", "FAIL " not in (r.stdout or ""))


if __name__ == "__main__":
    test_sql()
    test_cache()
    test_js()
    if FAILS:
        print("FAIL", ", ".join(FAILS))
        sys.exit(1)
    print("fatura_rapor_suzgec ok")
