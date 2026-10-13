# -*- coding: utf-8 -*-
"""Müşteriler raporu Yıllar süzgeci. Canlı veritabanına yazmaz, şema değiştirmez."""
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:180]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def _satir(mid, yil_ay, tutar, borc, durum):
    return {
        "musteri_id": mid,
        "firma_adi": "K%d" % mid,
        "giris_tarihi": yil_ay,
        "aylik_tutar": tutar,
        "toplam_borc": borc,
        "durum_etiket": durum,
        "hizmet_turu": "Sanal",
        "liste_ara_blob": "",
        "grup2": "",
        "hazir_ofis_oda_no": None,
    }


FIKSTUR = [
    _satir(1, "2024-03-01", 100, 10, "Aktif"),
    _satir(2, "2025-03-15", 200, 20, "Aktif"),
    _satir(3, "", 50, 5, "Aktif"),
    _satir(4, "2024-07-01", 80, 8, "Pasif"),
    _satir(5, "2024-03-01", 40, 4, "Aktif"),
    _satir(6, "2026-01-10", 10, 1, "Aktif"),
]


def birim():
    from routes.faturalar_routes import (
        _FIRMA_OZET_GIRIS_TARIHI_SQL,
        _fatura_rapor_giris_yili_sql,
        _fatura_rapor_giris_yillari_parse,
        _firma_ozet_build_rows_firma_sql,
        _firma_ozet_client_filtre_uygula,
    )
    import inspect
    from routes.faturalar_routes import _firma_ozet_dataset_from_request

    check("parse yok", _fatura_rapor_giris_yillari_parse(None) is None)
    check("parse bos", _fatura_rapor_giris_yillari_parse("  ") is None)
    check("parse yok kelime", _fatura_rapor_giris_yillari_parse("yok") == [])
    check("parse sirali", _fatura_rapor_giris_yillari_parse("2025,2024,2024") == [2024, 2025])
    check("parse gecersiz", _fatura_rapor_giris_yillari_parse("abc") == [])
    bos, pb = _fatura_rapor_giris_yili_sql(None)
    check("sql hepsi parca yok", bos == "" and pb == [])
    yok, py = _fatura_rapor_giris_yili_sql([])
    check("sql secim yok", "FALSE" in yok and py == [])
    sql, params = _fatura_rapor_giris_yili_sql([2024, 2025])
    check(
        "sql ayni giris ifadesi",
        "EXTRACT(YEAR FROM" in sql and _FIRMA_OZET_GIRIS_TARIHI_SQL in sql and params == [2024, 2025],
    )
    ay_src = inspect.getsource(_firma_ozet_dataset_from_request)
    check(
        "ay ve yil ayni ifade adi",
        "EXTRACT(MONTH FROM ({_FIRMA_OZET_GIRIS_TARIHI_SQL})" in ay_src
        and "_fatura_rapor_giris_yili_sql" in ay_src,
    )
    check("yil yardimcisi ddl yok", "CREATE " not in inspect.getsource(_fatura_rapor_giris_yili_sql).upper() and "ALTER " not in inspect.getsource(_fatura_rapor_giris_yili_sql).upper())
    kurulu = _firma_ozet_build_rows_firma_sql(
        musteri_where="TRUE",
        mk_df_sql="",
        giri_ay_sql="",
        ho_sql="",
        arama_sql="",
        firma_guncel_sql="0",
        hizli=True,
        giri_yil_sql=sql,
    )
    düz = _firma_ozet_build_rows_firma_sql(
        musteri_where="TRUE",
        mk_df_sql="",
        giri_ay_sql="",
        ho_sql="",
        arama_sql="",
        firma_guncel_sql="0",
        hizli=True,
    )
    check("sorgu yil parcasi", "EXTRACT(YEAR FROM" in kurulu and "EXTRACT(YEAR FROM" not in düz)

    def suz(yillar, durum=""):
        return _firma_ozet_client_filtre_uygula(
            FIKSTUR, q="", grup2=[], hizmet=[], durum=durum, oda=None, yillar=yillar
        )

    hepsi = suz(None)
    check("excel hepsi ayni satir", [r["musteri_id"] for r in hepsi] == [1, 2, 3, 4, 5, 6])
    check("excel hepsi toplam", sum(r["aylik_tutar"] for r in hepsi) == 480 and sum(r["toplam_borc"] for r in hepsi) == 48)
    y24 = suz([2024])
    check("excel tek yil", [r["musteri_id"] for r in y24] == [1, 4, 5])
    check("excel bos tarih gizlenir", all(r["giris_tarihi"] for r in y24))
    yiki = suz([2024, 2025])
    check("excel cok yil", [r["musteri_id"] for r in yiki] == [1, 2, 4, 5])
    check("excel secim yok bos", suz([]) == [])
    check("excel yil ve pasif", [r["musteri_id"] for r in suz([2024], "pasif")] == [4])


def _qs(url):
    return parse_qs(urlparse(url).query)


def _gorunen(s):
    return s.evaluate(
        """() => {
            const rows = [];
            document.querySelectorAll('#fatura_rapor_tbody tr').forEach(tr => {
                if (tr.style.display === 'none') return;
                rows.push({
                    id: tr.getAttribute('data-musteri-id'),
                    y: (tr.getAttribute('data-fr-tarih') || '').slice(0, 4),
                    tut: Number(tr.getAttribute('data-fr-tutar-n') || 0),
                    borc: Number(tr.getAttribute('data-fr-borc-n') || 0),
                    durum: tr.getAttribute('data-fr-durum-sort') || ''
                });
            });
            const el = document.getElementById('fatura_rapor_durum');
            const sec = document.querySelectorAll('.fatura-rapor-firma-sec:checked').length;
            return {rows: rows, durum: el ? el.textContent : '', sec: sec};
        }"""
    )


def arayuz():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        check("playwright", False, "yok")
        return
    import auth
    from jinja2 import ChoiceLoader, DictLoader
    from werkzeug.routing.exceptions import BuildError
    from test_plan_degistir import _app
    from test_plan_onizle_tarayici import _auth_yamala

    app = _app()
    app.logger.disabled = True
    _auth_yamala()
    _uf = app.jinja_env.globals.get("url_for")

    def uf(endpoint, **values):
        try:
            if _uf:
                return _uf(endpoint, **values)
        except BuildError:
            pass
        return "/yok/" + str(endpoint)

    app.jinja_env.globals["url_for"] = uf
    app.jinja_env.globals["has_module"] = lambda k: True
    app.jinja_env.globals["is_ledger_only"] = lambda *a, **k: False
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    app.jinja_env.loader = ChoiceLoader([DictLoader({"giris/index.html": html}), app.jinja_env.loader])
    client = app.test_client()
    istekler = []

    def yil_deger(iso):
        s = str(iso or "")
        return int(s[:4]) if len(s) >= 4 and s[:4].isdigit() else None

    def kapsam(qs):
        rows = list(FIKSTUR)
        if "1" in qs.get("pasifleri_dahil", []):
            rows = [r for r in rows if "pasif" in r["durum_etiket"].lower()]
        elif "1" in qs.get("sadece_aktif", []):
            rows = [r for r in rows if "pasif" not in r["durum_etiket"].lower()]
        aylar = []
        for parca in qs.get("giri_aylar", []):
            for p in parca.split(","):
                if p.strip().isdigit():
                    aylar.append(int(p))
        if aylar:
            rows = [r for r in rows if yil_deger(r["giris_tarihi"]) and int(r["giris_tarihi"][5:7]) in aylar]
        if "1" in qs.get("cift_olanlar", []):
            gunler = {}
            for r in rows:
                gunler.setdefault(r["giris_tarihi"], []).append(r)
            rows = [r for r in rows if r["giris_tarihi"] and 2 <= len(gunler.get(r["giris_tarihi"], [])) <= 3]
        yillar = sorted({yil_deger(r["giris_tarihi"]) for r in rows if yil_deger(r["giris_tarihi"])})
        sec = qs.get("giri_yillar", [None])[0]
        if sec == "yok":
            rows = []
        elif sec:
            istenen = {int(x) for x in sec.split(",") if x.strip().isdigit()}
            rows = [r for r in rows if yil_deger(r["giris_tarihi"]) in istenen]
        return rows, yillar

    def isle(route, request):
        u = request.url.split("yil.test")[-1]
        yol = u.split("?")[0]
        if yol.startswith("/faturalar/api/fatura-rapor"):
            istekler.append(u)
            qs = _qs(u)
            rows, yillar = kapsam(qs)
            govde = {
                "ok": True,
                "gorunum": "firma_ozet",
                "musteri_kapsam": "pasif" if "1" in qs.get("pasifleri_dahil", []) else "aktif",
                "giri_aylar_filtre": [int(x) for parca in qs.get("giri_aylar", []) for x in parca.split(",") if x.strip().isdigit()],
                "giri_yillar_filtre": None if "giri_yillar" not in qs else ([] if qs.get("giri_yillar", [""])[0] == "yok" else [int(x) for x in qs.get("giri_yillar", [""])[0].split(",") if x.strip().isdigit()]),
                "giris_yillari": yillar,
                "ay_referans": {"etiket": "Donem"},
                "satirlar": rows,
                "page": 1,
                "page_size": 50,
                "has_more": False,
                "total_count": len(rows),
            }
            route.fulfill(status=200, content_type="application/json", body=json.dumps(govde))
            return
        if "/firma-ozet-excel" in yol:
            istekler.append(u)
            route.fulfill(status=200, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", body=b"PK")
            return
        if yol.startswith("/static/"):
            hedef = ROOT / yol[len("/static/") :]
            if hedef.is_file():
                route.fulfill(status=200, body=hedef.read_bytes())
                return
        if yol.startswith("/giris"):
            r = client.get(u)
            route.fulfill(status=r.status_code, content_type=r.content_type or "text/html", body=r.get_data())
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "liste": [], "turler": []}))

    def bekle_istek(s, once):
        for _ in range(50):
            if len(istekler) > once:
                return istekler[-1]
            s.wait_for_timeout(100)
        return ""

    def yukle(s):
        once = len(istekler)
        s.evaluate("faturaRaporYukle()")
        return bekle_istek(s, once)

    def ac(s):
        s.evaluate("switchTab(7)")
        s.wait_for_selector("#fatura_rapor_giris_yil_btn", timeout=15000)
        url = yukle(s)
        s.wait_for_function("document.querySelectorAll('#fatura_rapor_tbody tr').length >= 5", timeout=8000)
        return url

    with sync_playwright() as pw:
        try:
            tarayici = pw.chromium.launch(channel="msedge", headless=True)
        except Exception as exc:
            check("tarayici", False, type(exc).__name__)
            return
        s = tarayici.new_page(viewport={"width": 1400, "height": 900})
        s.add_init_script("if (!sessionStorage.getItem('yiltest')) { localStorage.clear(); sessionStorage.setItem('yiltest', '1'); }")
        hatalar = []
        s.on("pageerror", lambda e: hatalar.append(str(e)[:160]))
        s.route("**/*", isle)
        s.goto("http://yil.test/giris/", wait_until="load")
        s.wait_for_function("typeof faturaRaporYukle === 'function' && typeof faturaRaporGirisYilTumu === 'function'", timeout=20000)
        ilk = ac(s)
        g = _gorunen(s)
        check("hepsi istek filtresiz", "giri_yillar" not in _qs(ilk))
        check("hepsi alti satir", [r["id"] for r in g["rows"]] == ["1", "2", "3", "4", "5", "6"])
        check("hepsi toplam", sum(r["tut"] for r in g["rows"]) == 480 and sum(r["borc"] for r in g["rows"]) == 48)
        check("hepsi yil tumu", "Yıllar: tümü" in g["durum"] and "Tekil müşteri: 6" in g["durum"])
        check("bos tarih hepsinde", any(r["id"] == "3" and r["y"] == "" for r in g["rows"]))

        s.click("#fatura_rapor_giris_yil_btn")
        s.wait_for_selector("#fatura_rapor_giris_yil_pop:not([hidden])")
        yillar = s.evaluate("() => Array.from(document.querySelectorAll('[data-giris-yil]')).map(el => el.getAttribute('data-giris-yil'))")
        check("yillar dinamik artan", yillar == ["2024", "2025", "2026"])
        notu = s.locator(".fatura-rapor-giris-yil-not").inner_text()
        check("bos tarih notu", "tüm yıllar" in notu)
        tablo = s.locator("#fatura_rapor_tablo_scroll")
        tablo.evaluate("el => { el.style.visibility = 'hidden'; }")
        out = Path(os.environ.get("TEMP") or ".") / "fatura_rapor_yillar.png"
        s.locator("#fatura_rapor_giris_yil_pop").screenshot(path=str(out))
        tablo.evaluate("el => { el.style.visibility = ''; }")
        check("ekran goruntusu", out.is_file() and out.stat().st_size > 500, str(out))

        s.evaluate("document.querySelector('.fatura-rapor-firma-sec').click()")
        check("secim var", _gorunen(s)["sec"] == 1)
        s.evaluate("document.querySelector('[data-giris-yil=\"2026\"]').click()")
        s.wait_for_function("() => document.getElementById('fatura_rapor_durum').textContent.indexOf('Yıllar: 2024, 2025') >= 0", timeout=8000)
        g = _gorunen(s)
        check("yil degisince secim temiz", g["sec"] == 0)
        check("cok yil", [r["id"] for r in g["rows"]] == ["1", "2", "4", "5"] and "Yıllar: 2024, 2025" in g["durum"])
        check("cok yil bos gizli", all(r["id"] != "3" for r in g["rows"]))
        s.evaluate("document.querySelector('[data-giris-yil=\"2025\"]').click()")
        s.wait_for_function("() => { const d = document.getElementById('fatura_rapor_durum'); return d && d.textContent.indexOf('Yıllar: 2024') >= 0 && d.textContent.indexOf('2025') < 0; }", timeout=8000)
        g = _gorunen(s)
        son = istekler[-1]
        check("tek yil istek", _qs(son).get("giri_yillar") == ["2024"])
        check("tek yil satir", [r["id"] for r in g["rows"]] == ["1", "4", "5"])
        check("tek yil toplam", sum(r["tut"] for r in g["rows"]) == 220 and sum(r["borc"] for r in g["rows"]) == 22)
        check("tek yil bos gizli", all(r["id"] != "3" for r in g["rows"]))
        check("tek yil durum", "Yıllar: 2024" in g["durum"] and "Tekil müşteri: 3" in g["durum"])
        s.evaluate("document.querySelector('[data-giris-yil=\"2025\"]').click()")
        s.wait_for_function("() => document.getElementById('fatura_rapor_durum').textContent.indexOf('Yıllar: 2024, 2025') >= 0", timeout=8000)

        s.evaluate("faturaRaporGirisAyToggle(3)")
        s.wait_for_function("() => document.getElementById('fatura_rapor_durum').textContent.indexOf('Yıllar: 2024, 2025') >= 0 && document.querySelectorAll('#fatura_rapor_tbody tr').length >= 1", timeout=8000)
        g = _gorunen(s)
        son = istekler[-1]
        check("yil ve ay and istek", _qs(son).get("giri_yillar") == ["2024,2025"] and _qs(son).get("giri_aylar") == ["3"])
        check("yil ve ay and satir", [r["id"] for r in g["rows"]] == ["1", "2", "5"])

        s.evaluate("faturaRaporGirisAyTumu()")
        s.wait_for_timeout(400)
        s.evaluate("document.getElementById('fatura_rapor_pasif_dahil').click()")
        s.wait_for_function("() => document.getElementById('fatura_rapor_durum').textContent.indexOf('Tekil müşteri: 1') >= 0", timeout=8000)
        g = _gorunen(s)
        check("yil ve pasif", _qs(istekler[-1]).get("pasifleri_dahil") == ["1"] and [r["id"] for r in g["rows"]] == ["4"])

        s.evaluate(
            """() => {
                if (typeof faturaRaporFirmaFiltreDurumYaz === 'function') faturaRaporFirmaFiltreDurumYaz('');
                document.getElementById('fatura_rapor_pasif_dahil').checked = false;
                document.getElementById('fatura_rapor_tum_musteri').checked = false;
                document.getElementById('fatura_rapor_sadece_aktif').checked = true;
            }"""
        )
        yukle(s)
        s.wait_for_function("() => document.getElementById('fatura_rapor_durum').textContent.indexOf('Tekil müşteri: 3') >= 0", timeout=8000)
        g = _gorunen(s)
        check("yil ve aktif", _qs(istekler[-1]).get("sadece_aktif") == ["1"] and [r["id"] for r in g["rows"]] == ["1", "2", "5"])

        s.evaluate(
            """() => {
                document.getElementById('fatura_rapor_sadece_aktif').checked = false;
                document.getElementById('fatura_rapor_pasif_dahil').checked = false;
                document.getElementById('fatura_rapor_tum_musteri').checked = true;
            }"""
        )
        yukle(s)
        s.wait_for_function("() => document.getElementById('fatura_rapor_durum').textContent.indexOf('Tekil müşteri: 4') >= 0", timeout=8000)
        g = _gorunen(s)
        check("yil ve tum", _qs(istekler[-1]).get("tum_musteriler") == ["1"] and [r["id"] for r in g["rows"]] == ["1", "2", "4", "5"])

        s.evaluate("document.getElementById('fatura_rapor_cift_olanlar').click()")
        s.wait_for_function("() => document.getElementById('fatura_rapor_durum').textContent.indexOf('Tekil müşteri: 2') >= 0", timeout=8000)
        g = _gorunen(s)
        check("yil ve cift", _qs(istekler[-1]).get("cift_olanlar") == ["1"] and [r["id"] for r in g["rows"]] == ["1", "5"])

        s.evaluate("document.getElementById('fatura_rapor_cift_olanlar').click()")
        s.evaluate("document.getElementById('fatura_rapor_duzenli_fatura').value = 'fatura_aylik'")
        url = yukle(s)
        s.wait_for_timeout(300)
        check(
            "duzenli fatura ile yil",
            _qs(url).get("duzenli_fatura") == ["fatura_aylik"] and _qs(url).get("giri_yillar") == ["2024,2025"],
        )

        s.evaluate("() => { const pop = document.getElementById('fatura_rapor_giris_yil_pop'); if (pop.hidden) document.getElementById('fatura_rapor_giris_yil_btn').click(); }")
        s.click("#fatura_rapor_giris_yil_temizle")
        s.wait_for_function("() => document.getElementById('fatura_rapor_durum').textContent.indexOf('Yıllar: seçilmedi') >= 0", timeout=8000)
        g = _gorunen(s)
        check("temizle bos", g["rows"] == [] and _qs(istekler[-1]).get("giri_yillar") == ["yok"] and g["sec"] == 0)

        s.evaluate("() => { const pop = document.getElementById('fatura_rapor_giris_yil_pop'); if (pop.hidden) document.getElementById('fatura_rapor_giris_yil_btn').click(); }")
        s.click("#fatura_rapor_giris_yil_tumu")
        s.wait_for_function("() => document.getElementById('fatura_rapor_durum').textContent.indexOf('Yıllar: tümü') >= 0 && document.getElementById('fatura_rapor_durum').textContent.indexOf('Tekil müşteri: 6') >= 0", timeout=8000)
        g = _gorunen(s)
        check("tumu yeniden ayni", "giri_yillar" not in _qs(istekler[-1]) and [r["id"] for r in g["rows"]] == ["1", "2", "3", "4", "5", "6"])
        check("tumu toplam ayni", sum(r["tut"] for r in g["rows"]) == 480 and sum(r["borc"] for r in g["rows"]) == 48)

        s.evaluate("document.querySelector('[data-giris-yil=\"2025\"]').click()")
        s.wait_for_function("() => document.getElementById('fatura_rapor_durum').textContent.indexOf('Yıllar: 2024, 2026') >= 0", timeout=8000)
        s.evaluate("document.querySelector('[data-giris-yil=\"2026\"]').click()")
        s.wait_for_function("() => document.getElementById('fatura_rapor_durum').textContent.indexOf('Yıllar: 2024') >= 0 && document.getElementById('fatura_rapor_durum').textContent.indexOf('Yıllar: 2024,') < 0", timeout=8000)
        kayit = s.evaluate("() => JSON.parse(localStorage.getItem('bestoffice_fatura_rapor_filtre_v1') || '{}')")
        check("ayar kaydi", kayit.get("giris_yillar") == [2024])
        once = len([u for u in istekler if "firma-ozet-excel" in u])
        s.evaluate("faturaRaporExcelAktar()")
        excel = ""
        for _ in range(30):
            bulunan = [u for u in istekler if "firma-ozet-excel" in u]
            if len(bulunan) > once:
                excel = bulunan[-1]
                break
            s.wait_for_timeout(100)
        check("excel filtreli", _qs(excel).get("giri_yillar") == ["2024"] and "giri_aylar" not in _qs(excel))

        s.goto("http://yil.test/giris/", wait_until="load")
        s.wait_for_function("typeof faturaRaporGirisYillarOku === 'function'", timeout=20000)
        s.wait_for_function("() => { const a = faturaRaporGirisYillarOku(); return a && a.length === 1 && a[0] === 2024; }", timeout=8000)
        url = yukle(s)
        s.wait_for_function("document.querySelectorAll('#fatura_rapor_tbody tr').length >= 1", timeout=8000)
        s.click("#fatura_rapor_giris_yil_btn")
        isaret = s.evaluate("() => Array.from(document.querySelectorAll('[data-giris-yil]')).map(el => [el.getAttribute('data-giris-yil'), el.checked])")
        check("ayar geri yukle istek", _qs(url).get("giri_yillar") == ["2024"])
        check("ayar geri yukle kutu", isaret == [["2024", True], ["2025", False], ["2026", False]], str(isaret))
        yil_hata = [h for h in hatalar if "GirisYil" in h or "giris_yil" in h]
        check("yil betigi hatasiz", not yil_hata, yil_hata[:1])
        tarayici.close()


def main():
    birim()
    arayuz()
    if FAILS:
        print("FAIL", len(FAILS))
        raise SystemExit(1)
    print("fatura_rapor_yillar ok")


if __name__ == "__main__":
    main()
