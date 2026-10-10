# -*- coding: utf-8 -*-
"""867 (canli) ile birebir ayni yapidaki fixture ile GERCEK giris/index.html uzerinde aylik grid testi.

Donem: Agustos 2026 - Temmuz 2027, reel.map["2026"]=1918,76; aktif plan gecerlilik 2026-10 (donemin ORTASI), brut 360.
Beklenen grid: 2026-8/9 = 1918,76 ; 2026-10..2027-7 = 360 ; 2027-8/9 = 474,84.
Iki yol: musteri-kart-bundle (ilk yukleme) ve fallback (aylik-grid-cache + reel-donem-tutarlar + aylik-tahsil-durum).
Her kart yazimi (data-tutar-kdv / data-brut-kdv / .aylik-deger) yazici fonksiyonuyla kaydedilir.
Canli DB'ye ve .env'e gitmez.

YENI senaryo (Secenek B, plan zinciri reel yilla kesilmez): plan 2026-02 net 500 / brut 600, reel {2023:300, 2024:1094.18,
2025:1454.71, 2026:1918.76}, Mart/Nisan 2026 kilitli (1454,71). Sunucu cache'i GERCEK sunucu kodundan uretilir
(_build_aylik_grid_cache_payload); sayfa Agu 2026 - Tem 2027 icin 791,40 gostermeli, 1918,76 hicbir yere yazilmamali.
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    print("SKIP playwright yok")
    raise SystemExit(0)

from test_grid_gercek_sayfa import INIT, _kartlar  # noqa: E402
from test_plan_degistir import _app  # noqa: E402
from test_plan_onizle_tarayici import _auth_yamala  # noqa: E402

BASE = "http://grid867.test"
MID = 867
DIGER = 868
ETIKET = [""]
FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + ETIKET[0] + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _aylar_867():
    """Canli yapi: ay_key SIFIRSIZ ('2026-9'); odeme/borc alanlari canli gibi."""
    out = []
    y, m = 2023, 8
    while (y, m) <= (2027, 9):
        if (y, m) <= (2024, 7):
            t = 300.0 if (y, m) <= (2024, 1) else 720.0
        elif (y, m) <= (2025, 7):
            t = 1094.18
        elif (y, m) <= (2026, 7):
            t = 1454.71
        elif (y, m) <= (2026, 9):
            t = 1918.76
        elif (y, m) <= (2027, 7):
            t = 360.0
        else:
            t = 474.84
        a = {"yil": y, "ay": m, "ay_key": "%d-%d" % (y, m), "tutar_kdv_dahil": t, "brut_tutar_kdv": t,
             "odenen_tutar_kdv": 0.0, "kalan_tutar_kdv": t, "tahsil_edildi": False, "kismi_tahsilat": False,
             "acik_aylik_borc_faturasi": False}
        if (y, m) <= (2025, 6):
            a.update(tahsil_edildi=True, odenen_tutar_kdv=t, kalan_tutar_kdv=0.0)
        elif (y, m) == (2025, 7):
            a.update(kismi_tahsilat=True, odenen_tutar_kdv=round(t - 130.16, 2), kalan_tutar_kdv=130.16)
        if (y, m) >= (2025, 7):
            a["acik_aylik_borc_faturasi"] = True
        out.append(a)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _musteri(mid):
    return {"id": mid, "name": "Test %d" % mid, "durum": "aktif", "odeme_duzeni": "aylik",
            "kira_artis_tarihi": "2026-08-01", "sozlesme_baslangic": "2023-08-01", "sozlesme_tarihi": "2023-08-01",
            "sozlesme_bitis": "2027-08-01", "kira_banka": True, "kira_nakit": False,
            "guncel_kira_bedeli": 600, "ilk_kira_bedeli": 600, "aylik_kira": 600, "kdv_oran": 20}


def _aylar_yeni_sunucudan():
    """Gercek sunucu zinciri (plan 2026-02, kilitli Mart/Nisan) -> grid ayları; tahsilat bayraklari 867 gibi."""
    import test_plan_reel_kesmez as t
    eski = t._yama(t.PLAN, t.REEL)
    try:
        p = t._kayit_sonrasi_payload()
    finally:
        t._geri(eski)
    out = []
    for a in p["aylar"]:
        if (a["yil"], a["ay"]) > (2027, 9):
            continue
        a = dict(a)
        a["ay_key"] = "%d-%d" % (a["yil"], a["ay"])
        tt = a["tutar_kdv_dahil"]
        a.update(odenen_tutar_kdv=0.0, kalan_tutar_kdv=tt, tahsil_edildi=False, kismi_tahsilat=False, acik_aylik_borc_faturasi=False)
        if (a["yil"], a["ay"]) <= (2025, 6):
            a.update(tahsil_edildi=True, odenen_tutar_kdv=tt, kalan_tutar_kdv=0.0)
        elif (a["yil"], a["ay"]) == (2025, 7):
            a.update(kismi_tahsilat=True, odenen_tutar_kdv=round(tt - 130.16, 2), kalan_tutar_kdv=130.16)
        if (a["yil"], a["ay"]) >= (2025, 7):
            a["acik_aylik_borc_faturasi"] = True
        out.append(a)
    return out


def fixture_867(senaryo="eski"):
    cache = {"musteri_id": MID, "plan_var": True, "artis_ay": 8, "baslangic": "2023-08-01", "bitis": "2027-10-01",
             "kapanis_tarihi": None, "kapanis_sonrasi_borc_ay": None, "kira_suresi_ay": 48,
             "kira_banka_tutar": 600.0, "kira_nakit": False, "kira_nakit_tutar": 0.0, "split_kira_odeme": False,
             "taban_aylik_net": 600.0, "compute_rev": 30, "aylar": _aylar_867()}
    if senaryo == "yeni":
        cache["compute_rev"] = 31
        cache["aylar"] = _aylar_yeni_sunucudan()
    reel_map = {"2023": 300.0, "2024": 1094.18, "2025": 1454.71, "2026": 1918.76}
    detay = {k: {"tip": "dahil", "giris_tutar": v, "hibrit": None} for k, v in reel_map.items()}
    tahsil = ["%d-%d" % (a["yil"], a["ay"]) for a in cache["aylar"] if a["tahsil_edildi"]]
    return {
        "musteri": {"ok": True, "musteri": _musteri(MID)},
        "tahsil_durum": {"ok": True, "aylar": tahsil},
        "grid": {"ok": True, "cache": cache},
        "reel": {"ok": True, "map": reel_map, "detay_map": detay},
    }


def fixture_diger():
    """868: plansiz, tek duz tutar (kart degistirip geri donme icin)."""
    aylar = []
    y, m = 2023, 8
    while (y, m) <= (2027, 9):
        aylar.append({"yil": y, "ay": m, "ay_key": "%d-%d" % (y, m), "tutar_kdv_dahil": 500.0, "brut_tutar_kdv": 500.0,
                      "odenen_tutar_kdv": 0.0, "kalan_tutar_kdv": 500.0, "tahsil_edildi": False, "kismi_tahsilat": False})
        m += 1
        if m > 12:
            y, m = y + 1, 1
    cache = {"musteri_id": DIGER, "plan_var": False, "artis_ay": 8, "baslangic": "2023-08-01", "bitis": "2027-10-01",
             "kira_suresi_ay": 48, "kira_banka_tutar": 600.0, "kira_nakit": False, "kira_nakit_tutar": 0.0,
             "taban_aylik_net": 600.0, "compute_rev": 30, "aylar": aylar}
    return {"musteri": {"ok": True, "musteri": _musteri(DIGER)}, "tahsil_durum": {"ok": True, "aylar": []},
            "grid": {"ok": True, "cache": cache}, "reel": {"ok": True, "map": {}, "detay_map": {}}}


def beklenen_867(fx):
    return {a["ay_key"]: a["tutar_kdv_dahil"] for a in fx["grid"]["cache"]["aylar"]
            if (a["yil"], a["ay"]) >= (2026, 8)}


def kosu(yol, html_yol, etiket, kart_degistir=True, dump=None, ls_stale=False, senaryo="eski"):
    """yol: 'bundle' | 'fallback'. (basarisiz_kontroller, kotu_yazim_sayisi, kotu_yazici_dagilimi) doner."""
    global FAILS
    FAILS = []
    ETIKET[0] = etiket + ": "
    fx = {MID: fixture_867(senaryo), DIGER: fixture_diger()}
    beklenen = beklenen_867(fx[MID])
    app = _app()
    app.logger.disabled = True
    _auth_yamala()
    from werkzeug.routing.exceptions import BuildError
    _uf = app.jinja_env.globals["url_for"]

    def uf(endpoint, **values):
        try:
            return _uf(endpoint, **values)
        except BuildError:
            return "/yok/" + str(endpoint)

    app.jinja_env.globals["url_for"] = uf
    app.jinja_env.globals["has_module"] = lambda k: True
    app.jinja_env.globals["is_ledger_only"] = lambda *a, **k: False
    if html_yol:
        import jinja2
        app.jinja_env.loader = jinja2.ChoiceLoader([jinja2.DictLoader({"giris/index.html": Path(html_yol).read_text(encoding="utf-8")}), app.jinja_env.loader])
    client = app.test_client()

    def mid_of(yol_):
        import re
        mm = re.search(r"musteri_id=(\d+)", yol_)
        if mm:
            return int(mm.group(1))
        mm = re.search(r"/giris/api/musteri/(\d+)", yol_)
        return int(mm.group(1)) if mm else MID

    def by_iso(f):
        """Canli tahsilat-panel-detay: sunucu plan duzeltmeli (aylik = plan brutu) kayitli panel satirlari."""
        out = {}
        for a in f["grid"]["cache"]["aylar"]:
            if a["tahsil_edildi"] or a["kismi_tahsilat"] or a.get("acik_aylik_borc_faturasi"):
                out["%04d-%02d-01" % (a["yil"], a["ay"])] = {"aylik": a["tutar_kdv_dahil"], "tahsil": a["odenen_tutar_kdv"], "tahsil_tarih": "2025-01-05" if a["odenen_tutar_kdv"] else ""}
        return out

    def js(route, govde, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(govde))

    def isle(route, request):
        u = request.url[len(BASE):]
        mid = mid_of(u)
        f = fx.get(mid, fx[MID])
        if u.startswith("/giris/api/musteri-kart-bundle"):
            if yol == "fallback":
                js(route, {"ok": False, "mesaj": "bundle kapali (fallback testi)"}, 500)
                return
            js(route, {"ok": True, "musteri_id": mid, "musteri": f["musteri"], "tahsil_durum": f["tahsil_durum"], "grid": f["grid"], "reel": f["reel"]})
            return
        if u.startswith("/giris/api/aylik-grid-cache"):
            js(route, f["grid"])
            return
        if u.startswith("/giris/api/aylik-tahsil-durum"):
            js(route, f["tahsil_durum"])
            return
        if u.startswith("/giris/api/reel-donem-tutarlar"):
            js(route, f["reel"])
            return
        if u.startswith("/giris/api/tahsilat-panel-detay"):
            js(route, {"ok": True, "by_iso": by_iso(f)})
            return
        if u.startswith("/giris/api/musteri/") and "/" not in u[len("/giris/api/musteri/"):].split("?")[0]:
            js(route, f["musteri"])
            return
        if u.startswith("/giris/api/"):
            js(route, {"ok": True, "liste": [], "musteriler": [], "map": {}, "aylar": []})
            return
        r = client.get(u)
        route.fulfill(status=r.status_code, content_type=r.content_type or "text/html", body=r.get_data())

    def dogru(s):
        """Plan/cache beklenen metni ile kartlari karsilastir (2026-8 ve sonrasi, iki grid)."""
        k = _kartlar(s)
        out = {}
        for gid in ("sozlesmeler-aylik-grid", "sozlesmeler-reel-aylik-grid"):
            kk = k[gid]
            yanlis = {a: (kk[a]["metin"], "%.2f" % b) for a, b in beklenen.items()
                      if a in kk and kk[a]["metin"] != "%.2f" % b}
            out[gid] = (yanlis, len(kk))
        return out

    def kontrol(s, ad):
        for gid, (yanlis, n) in dogru(s).items():
            check("%s %s: 2026-8+ plan/cache rakamlarinda (%d kart)" % (ad, gid.split("-")[1], n), n > 20 and not yanlis, dict(list(yanlis.items())[:6]))

    with sync_playwright() as pw:
        try:
            tarayici = pw.chromium.launch(channel="msedge", headless=True)
        except Exception as exc:  # pragma: no cover
            print("SKIP tarayici yok:", str(exc)[:80])
            return None, 0, {}
        s = tarayici.new_page(viewport={"width": 1400, "height": 900})
        hatalar = []
        s.on("pageerror", lambda e: hatalar.append(str(e)[:160]))
        s.add_init_script(INIT)
        if ls_stale:
            # eski oturumdan kalan localStorage panel satirlari: plan oncesi donem-yili reel tutari (1918,76) yazili
            satirlar = {}
            for a in fx[MID]["grid"]["cache"]["aylar"]:
                if (a["yil"], a["ay"]) >= (2026, 8) and (a["yil"], a["ay"]) <= (2027, 7):
                    satirlar[a["ay_key"]] = {"aylik_tutar": 1918.76, "__reel_db_aylik": 1918.76, "tahsil": 0, "kalan": 1918.76,
                                             "tahsil_aktif": False, "tahsil_tarih": "", "__saved": True, "__db_saved": True, "__locked": True}
            s.add_init_script("try { if (!localStorage.getItem('__tahsilat_yil_ay_kayit__|867')) localStorage.setItem('__tahsilat_yil_ay_kayit__|867', %s); } catch (e) {}" % json.dumps(json.dumps({"2026": {k: v for k, v in satirlar.items() if k.startswith("2026-")}, "2027": {k: v for k, v in satirlar.items() if k.startswith("2027-")}})))
        if os.environ.get("TANI"):
            s.add_init_script("""(() => { window.__tani = [];
              const izle = ['sozlesmelerReelTahsilPanelSenkron', 'girisTahsilatYilAyPanelCacheDenDoldur', 'girisTahsilatYilAyPanelHtml', 'girisTahsilatYilAyPanelDbYukle', 'girisTahsilatYilAyKayitYukle', 'girisTahsilatYilAyPanelSatirNormalize'];
              const iv = setInterval(() => { if (typeof sozlesmelerReelTahsilPanelSenkron !== 'function') return; clearInterval(iv);
                izle.forEach(ad => { const f = window[ad]; if (typeof f !== 'function' || f.__sarili) return;
                  const w = function () { const once = ((window.__tahsilatSatirYilAyDetay || {})['2026'] || {})['2026-11'];
                    const o = JSON.stringify(once ? [once.aylik_tutar, once.__reel_db_aylik] : null);
                    const r = f.apply(this, arguments);
                    const son = ((window.__tahsilatSatirYilAyDetay || {})['2026'] || {})['2026-11'];
                    const n = JSON.stringify(son ? [son.aylik_tutar, son.__reel_db_aylik] : null);
                    if (o !== n) window.__tani.push([Math.round(performance.now()), ad, o, n]);
                    return r; };
                  w.__sarili = true; window[ad] = w; }); }, 1); })();""")
        s.route("**/*", isle)
        s.goto(BASE + "/giris/", wait_until="load")
        s.wait_for_function("typeof selectMusteri === 'function'", timeout=20000)
        s.evaluate("selectMusteri(%d)" % MID)
        s.wait_for_function("document.querySelectorAll('#sozlesmeler-aylik-grid .sozlesmeler-ay-kart').length > 20", timeout=20000)
        s.wait_for_timeout(1500)
        kontrol(s, "ilk yukleme")
        if senaryo == "yeni":
            k0 = _kartlar(s)["sozlesmeler-aylik-grid"]
            check("YENI: Subat 2026 = 600", k0.get("2026-2", {}).get("metin") == "600.00", k0.get("2026-2"))
            check("YENI: Mart/Nisan 2026 = 1454,71 (kilitli)", k0.get("2026-3", {}).get("metin") == "1454.71" and k0.get("2026-4", {}).get("metin") == "1454.71")
            check("YENI: Agustos 2026 = 791,40", k0.get("2026-8", {}).get("metin") == "791.40", k0.get("2026-8"))
            check("YENI: Agu 2026 - Tem 2027 hepsi 791,40", all(k0.get(a, {}).get("metin") == "791.40" for a in ["2026-8", "2026-9", "2026-10", "2026-11", "2026-12"] + ["2027-%d" % i for i in range(1, 8)]))
            check("YENI: Agustos 2027 = 1043,86", k0.get("2027-8", {}).get("metin") == "1043.86", k0.get("2027-8"))
        for tur in (1, 2, 3):
            s.evaluate("""() => { try { sozlesmelerReelDbGridVePanelSonBoya(); } catch (e) {} try { sozlesmelerAylikNormGridKartTutarlariniYenile(); } catch (e) {}
                try { sozlesmelerReelKayitliTumYillariGridaUygula(); } catch (e) {} try { sozlesmelerReelDbSonBoyaSira(window.__sozlesmelerAylikSonCacheObj); } catch (e) {}
                try { sozlesmelerReelDonemTutarlariUygula(false); } catch (e) {} }""")
            s.wait_for_timeout(250)
            kontrol(s, "%d. yenileme" % tur)
        s.evaluate("() => { try { sozlesmelerReelDbGridVePanelSonBoya(); } catch (e) {} }")
        s.wait_for_timeout(1500)
        kontrol(s, "gecikmeli tur")
        if senaryo == "yeni":
            k1 = _kartlar(s)["sozlesmeler-aylik-grid"]
            check("YENI gecikmeli: Agustos 2026 = 791,40", k1.get("2026-8", {}).get("metin") == "791.40", k1.get("2026-8"))
        if kart_degistir:
            s.evaluate("selectMusteri(%d)" % DIGER)
            s.wait_for_timeout(2000)
            dk = _kartlar(s)["sozlesmeler-aylik-grid"]
            check("plansiz 868: 2026-10..2027-7 duz 500,00 (davranis ayni)", all(dk.get(a, {}).get("metin") == "500.00" for a in ("2026-10", "2026-12", "2027-3", "2027-7")),
                  {a: dk.get(a, {}).get("metin") for a in ("2026-10", "2026-12", "2027-3", "2027-7")})
            s.evaluate("selectMusteri(%d)" % MID)
            s.wait_for_timeout(2500)
            kontrol(s, "kart degistir-geri don")
        if os.environ.get("TANI"):
            print("TANI", json.dumps(s.evaluate("window.__tani"), ensure_ascii=False)[:1500])
            print("DET", json.dumps(s.evaluate("(window.__tahsilatSatirYilAyDetay || {})['2026'] ? Object.entries(window.__tahsilatSatirYilAyDetay['2026']).slice(0,14).map(([k, v]) => [k, v.aylik_tutar, v.__reel_db_aylik, v.__saved, v.__db_saved]) : null"))[:1500])
        yaz = s.evaluate("window.__yaz")
        if dump:
            Path(dump).write_text(json.dumps(yaz, ensure_ascii=False), encoding="utf-8")
        kotu = {}
        # kusur imzasi: donem-yili reel tutari (1918,76) plan aylarina (2026-10..2027-7) yazilmasi
        for y in yaz:
            if y["tur"] != "metin" or y["deger"] != "1918.76":
                continue
            yy, mm = [int(x) for x in y["ay"].split("-")]
            if ((2026, 8) if senaryo == "yeni" else (2026, 10)) <= (yy, mm) <= (2027, 7):
                kotu[y["kim"]] = kotu.get(y["kim"], 0) + 1
        print("%s: sayfa hatalari %s; toplam yazim %d" % (etiket, hatalar[:2], len(yaz)))
        tarayici.close()
    return list(FAILS), sum(kotu.values()), kotu


def _eski_index(ref, dosya):
    import subprocess
    c = subprocess.run(["git", "show", "%s:erp_web/templates/giris/index.html" % ref], cwd=str(ROOT.parent), capture_output=True)
    if c.returncode != 0:
        return None
    Path(dosya).write_bytes(c.stdout)
    return dosya


def main():
    sonuc = 0
    ref = os.environ.get("ESKI_REF", "ac895a5")
    eski = _eski_index(ref, os.path.join(tempfile.gettempdir(), "_index_%s.html" % ref))
    if os.environ.get("SADECE_ESKI") or not os.environ.get("SADECE_YENI"):
        if eski:
            for yol, ls in (("bundle", False), ("fallback", False), ("bundle", True)):
                f, k, dag = kosu(yol, eski, "ESKI(%s) %s%s" % (ref, yol, "+ls_stale" if ls else ""), dump=os.environ.get("YAZ_DOK_" + yol), ls_stale=ls)
                print("ESKI %s: basarisiz=%d plan_disi_yazim=%d" % (yol, len(f or []), k))
                if not (f and k):
                    print("FAIL eski index (%s) ile %s testi FAIL etmeli" % (ref, yol))
                    sonuc = 1
                for kim, n in sorted(dag.items(), key=lambda x: -x[1])[:8]:
                    print("   %6d  %s" % (n, kim[:230]))
    if os.environ.get("SADECE_ESKI"):
        return 0
    for yol, ls in (("bundle", False), ("fallback", False), ("bundle", True)):
        f, k, dag = kosu(yol, None, "YENI %s%s" % (yol, "+ls_stale" if ls else ""), ls_stale=ls)
        if f is None:
            return 0
        print("YENI %s%s: basarisiz=%d plan_disi_yazim=%d" % (yol, "+ls_stale" if ls else "", len(f), k))
        for kim, n in sorted(dag.items(), key=lambda x: -x[1])[:8]:
            print("   %6d  %s" % (n, kim[:230]))
        if f or k:
            sonuc = 1
    # YENI senaryo (plan 2026-02, Agu 2026 = 791,40): yalniz guncel index.html
    for yol, ls in (("bundle", False), ("fallback", False), ("bundle", True)):
        f, k, dag = kosu(yol, None, "YENI-B %s%s" % (yol, "+ls_stale" if ls else ""), ls_stale=ls, senaryo="yeni")
        if f is None:
            return 0
        print("YENI-B %s%s: basarisiz=%d plan_disi_yazim=%d" % (yol, "+ls_stale" if ls else "", len(f), k))
        for kim, n in sorted(dag.items(), key=lambda x: -x[1])[:8]:
            print("   %6d  %s" % (n, kim[:230]))
        if f or k:
            sonuc = 1
    print("grid_867_gercek " + ("FAIL" if sonuc else "ok"))
    return sonuc


if __name__ == "__main__":
    raise SystemExit(main())
