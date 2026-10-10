# -*- coding: utf-8 -*-
"""Plan kalıcılığı: reel örtmesi/okuma yolları planı ezmez, plan kutusu isteğe özel, JS plan değerlerini korur.
Canlı DB'ye gitmez (okuma yardımcıları yamalı)."""
import copy
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


from flask import Flask  # noqa: E402

from routes import giris_routes as gr  # noqa: E402
from test_sozlesme_plan import _kyc_grid  # noqa: E402

TUFE = {2024: {8: 10.0}, 2025: {8: 5.0}, 2026: {8: 5.0}}
KYC = _kyc_grid()
MANUEL = {2024: 1500}
PLAN = [{"gecerlilik_ay": "2024-11-01", "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400}]
MID = 424242


def hucre(p):
    return {(a["yil"], a["ay"]): a["tutar_kdv_dahil"] for a in p["aylar"]}


def _yama(planlar):
    """Okuma yardımcılarını DB'siz yamala; eski değerleri döndür."""
    eski = {
        "paket": gr._plan_paket_yukle,
        "manual": gr._musteri_reel_donem_manual_dict_from_db,
        "kyc": gr._musteri_kyc_grup_for_aylik_grid,
        "tufe": gr._tufe_map_by_year_month_cached,
        "rows": gr._ekstre_tahsil_rows_for_musteri,
        "batch": gr._ekstre_tahsil_batch_maps_from_rows,
    }
    gr._plan_paket_yukle = lambda mids: {int(m): {"planlar": planlar, "faturali": [], "belge": {}} for m in mids}
    gr._musteri_reel_donem_manual_dict_from_db = lambda mid: dict(MANUEL)
    gr._musteri_kyc_grup_for_aylik_grid = lambda mid: dict(KYC)
    gr._tufe_map_by_year_month_cached = lambda: TUFE
    gr._ekstre_tahsil_rows_for_musteri = lambda mid: []
    gr._ekstre_tahsil_batch_maps_from_rows = lambda rows: {"marker": {}, "pay": {}}
    return eski


def _geri(eski):
    gr._plan_paket_yukle = eski["paket"]
    gr._musteri_reel_donem_manual_dict_from_db = eski["manual"]
    gr._musteri_kyc_grup_for_aylik_grid = eski["kyc"]
    gr._tufe_map_by_year_month_cached = eski["tufe"]
    gr._ekstre_tahsil_rows_for_musteri = eski["rows"]
    gr._ekstre_tahsil_batch_maps_from_rows = eski["batch"]


def _kayitli_plan_payload():
    """Kayıt anındaki gibi: plansız hesap + reel + plan katmanı (yeniden inşa yolu)."""
    p = gr._aylik_grid_compute(MID, KYC, TUFE, plan_katmani=False)
    gr._aylik_grid_apply_reel_donem_overlay_to_payload(MID, KYC, TUFE, p, manual_reel_by_year=dict(MANUEL), plan_katmani=False)
    gr._plan_katmani_payloada(p, KYC, TUFE, planlar=PLAN, reel=dict(MANUEL))
    return p


def test_okuma_yollari_plani_ezmez():
    eski = _yama(PLAN)
    try:
        saklanan = _kayitli_plan_payload()
        h0 = hucre(saklanan)
        check("saklı: plan öncesi reel, sonrası plan", h0[(2024, 10)] == 1500 and h0[(2024, 11)] == 2400 and h0[(2025, 7)] == 2400)
        # DB-isabeti / bellek-isabeti / ekstre: aynı çıkış fonksiyonu, 1.-2.-3. okuma
        p = copy.deepcopy(saklanan)
        for tur in (1, 2, 3):
            p = gr._aylik_grid_payload_reel_overlay_from_db(MID, p)
            ht = hucre(p)
            check(f"okuma {tur}. tur plan korunur", ht[(2024, 11)] == 2400 and ht[(2025, 7)] == 2400 and ht[(2024, 10)] == 1500)
        check("okuma sonrası plan_var", p.get("plan_var") is True)
        # ekstre yolu (manual_reel_pass ile doğrudan)
        e = copy.deepcopy(saklanan)
        gr._aylik_grid_apply_reel_donem_overlay_to_payload(MID, KYC, TUFE, e, manual_reel_by_year=dict(MANUEL))
        check("ekstre reel örtmesi planı korur", hucre(e)[(2024, 11)] == 2400)
        # kayıt sonrası TÜM okuma yolları aynı sonucu verir (resync çıktısı = sonraki okumalar)
        check("okuma çıktısı = kayıtlı payload", _ozet(p) == _ozet(saklanan))
    finally:
        _geri(eski)


def _ozet(p):
    return [(a["yil"], a["ay"], a["tutar_kdv_dahil"], a["brut_tutar_kdv"]) for a in p["aylar"]]


def test_plansiz_birebir_eski():
    eski = _yama([])
    try:
        for ad, kyc in (("duz", _kyc_grid()), ("hibrit", _kyc_grid(kira_nakit_tutar=400, kira_banka_tutar=600)), ("nakit", _kyc_grid(kira_nakit=True))):
            a = gr._aylik_grid_compute(MID, kyc, TUFE, plan_katmani=False)
            b = copy.deepcopy(a)
            gr._aylik_grid_apply_reel_donem_overlay_ham(MID, kyc, TUFE, a, manual_reel_by_year=dict(MANUEL))
            gr._aylik_grid_apply_reel_donem_overlay_to_payload(MID, kyc, TUFE, b, manual_reel_by_year=dict(MANUEL))
            check("plansız " + ad + ": yeni sarmalayıcı = eski ham", json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str))
        # plansız: ham okuma yolu
        pl = gr._aylik_grid_compute(MID, KYC, TUFE, plan_katmani=False)
        ham = copy.deepcopy(pl)
        gr._aylik_grid_apply_reel_donem_overlay_ham(MID, KYC, TUFE, ham, manual_reel_by_year=dict(MANUEL))
        yeni = gr._aylik_grid_payload_reel_overlay_from_db(MID, copy.deepcopy(pl))
        check("plansız: _reel_overlay_from_db = eski", _ozet(ham) == _ozet(yeni) and "plan_var" not in yeni)
    finally:
        _geri(eski)


def test_plan_kutusu_istek_omurlu():
    app = Flask(__name__)
    sayim = {"plan": 0}
    eski_one, eski_all = gr.fetch_one, gr.fetch_all

    def fetch_one(sql, params=None):
        return {"t": "sozlesme_plan_degisiklik"}

    def fetch_all(sql, params=None):
        if "ANY" in str(sql):
            if sayim["plan"]:
                return [{"musteri_id": MID, "gecerlilik_ay": date(2026, 10, 1), "yeni_net": 1000, "kdv_oran": 20,
                         "yeni_brut": 1200, "nakit_tutar": None, "banka_tutar": None, "id": 1}]
            return []
        return []

    gr.fetch_one, gr.fetch_all = fetch_one, fetch_all
    try:
        gr._plan_kutu_sifirla()
        with app.test_request_context("/a"):
            n1 = len((gr._plan_paket_yukle([MID]).get(MID) or {}).get("planlar") or [])
        sayim["plan"] = 1  # plan kaydedildi (başka iş parçacığı), bu iş parçacığı sıfırlanmadı
        with app.test_request_context("/b"):
            n2 = len((gr._plan_paket_yukle([MID]).get(MID) or {}).get("planlar") or [])
        check("istek1 plansız, istek2 (aynı thread) planı görür", n1 == 0 and n2 == 1, (n1, n2))
        # aynı istek içinde tekrar okuma kutudan (ek sorgu yok)
        with app.test_request_context("/c"):
            gr._plan_paket_yukle([MID])
            sayim["plan"] = 0
            n3 = len((gr._plan_paket_yukle([MID]).get(MID) or {}).get("planlar") or [])
        check("aynı istekte kutu korunur", n3 == 1, n3)
        # bağlamsız (arka plan) kutu ömrü: süre dolunca yenilenir
        sayim["plan"] = 1
        gr._plan_kutu_sifirla()
        k1 = gr._plan_kutu()
        k1["_ts"] -= gr._PLAN_KUTU_OMUR_SN + 1
        k2 = gr._plan_kutu()
        check("bağlamsız kutu süresi dolunca yenilenir", k1 is not k2)
    finally:
        gr.fetch_one, gr.fetch_all = eski_one, eski_all
        gr._plan_kutu_sifirla()


def test_iki_musteri_plan_sizintisi():
    """Plan kutusu müşteri kimliğiyle anahtarlı: A'nın planı, kutu sızsa bile B'ye uygulanamaz."""
    import threading

    A, B = 111, 222
    eski_one, eski_all = gr.fetch_one, gr.fetch_all
    sorgu = {"mids": []}

    def fetch_one(sql, params=None):
        return {"t": "sozlesme_plan_degisiklik"}

    def fetch_all(sql, params=None):
        if "ANY" in str(sql):
            sorgu["mids"].append(list(params[0]))
            return [
                {"musteri_id": A, "gecerlilik_ay": date(2024, 11, 1), "yeni_net": 2000, "kdv_oran": 20,
                 "yeni_brut": 2400, "nakit_tutar": None, "banka_tutar": None}
            ] if A in params[0] else []
        return []

    gr.fetch_one, gr.fetch_all = fetch_one, fetch_all
    app = Flask(__name__)
    sonuc = {}

    def is_parcacigi():
        gr._plan_kutu_sifirla()
        with app.test_request_context("/1"):  # istek 1: yalnız A (planlı) yüklenir → kutu dolar
            sonuc["a1"] = len((gr._plan_paket_yukle([A]).get(A) or {}).get("planlar") or [])
        with app.test_request_context("/2"):  # istek 2 (aynı thread): yalnız B
            p = gr._plan_paket_yukle([B])
            sonuc["b_planlar"] = len((p.get(B) or {}).get("planlar") or [])
            sonuc["b_icinde_a_var"] = A in p
            sonuc["kutu_a_miras"] = A in gr._plan_kutu()["harita"]
        with app.test_request_context("/3"):  # iki müşteri birlikte
            p = gr._plan_paket_yukle([A, B])
            sonuc["karma"] = (len(p[A]["planlar"]), len(p[B]["planlar"]))

    try:
        th = threading.Thread(target=is_parcacigi)
        th.start()
        th.join()
        check("A planlı (istek1)", sonuc.get("a1") == 1, sonuc)
        check("B, A'nın planını almaz (aynı thread, sonraki istek)", sonuc.get("b_planlar") == 0 and not sonuc.get("b_icinde_a_var"), sonuc)
        check("kutu A'yı sonraki isteğe taşımaz", sonuc.get("kutu_a_miras") is False, sonuc)
        check("karma istek: A=1 plan, B=0 plan", sonuc.get("karma") == (1, 0), sonuc)
        # Kutu bilerek sızdırılsa bile (A kutuda, B yok) B'nin payload'ı değişmez
        gr._plan_kutu_sifirla()
        kutu = gr._plan_kutu()
        kutu["tablo"] = True
        kutu["harita"][A] = {"planlar": PLAN, "faturali": [], "belge": {}}
        pb = gr._aylik_grid_compute(B, KYC, TUFE, plan_katmani=False)
        once = _ozet(pb)
        paket_b = gr._plan_paket_yukle([B]).get(B)
        gr._plan_katmani_payloada(pb, KYC, TUFE, planlar=paket_b["planlar"], reel=dict(MANUEL))
        check("sızan kutuda bile B payload'ı değişmez", _ozet(pb) == once and "plan_var" not in pb)
    finally:
        gr.fetch_one, gr.fetch_all = eski_one, eski_all
        gr._plan_kutu_sifirla()


def test_panel_post_bayat_istemci():
    """Bayat istemci eski brütü POST eder: planlı kartta panel plan değerinde kalır; plansızda aynen."""
    saklanan = {}
    eski = {n: getattr(gr, n) for n in (
        "fetch_one", "_plan_paket_yukle", "_aylik_tahsil_tutar_map", "_load_musteri_panel_by_iso",
        "_save_musteri_panel_by_iso", "_read_aylik_grid_cache_payload", "_persist_grid_cache_with_panel",
        "_defer_aylik_grid_cache_rebuild")}
    plan_payload = {"plan_var": True, "aylar": [
        {"yil": 2026, "ay": 8, "tutar_kdv_dahil": 1918.76}, {"yil": 2026, "ay": 10, "tutar_kdv_dahil": 1200.0},
        {"yil": 2027, "ay": 7, "tutar_kdv_dahil": 1200.0}, {"yil": 2027, "ay": 8, "tutar_kdv_dahil": 1582.8}]}
    gr.fetch_one = lambda sql, params=None: {"id": 1}
    gr._aylik_tahsil_tutar_map = lambda mid: {"2026-08-01": 500.0, "2026-10-01": 300.0}
    gr._load_musteri_panel_by_iso = lambda mid, **k: {}
    gr._save_musteri_panel_by_iso = lambda mid, by_iso, prune_no_db_tahsil=False: saklanan.update(by_iso=copy.deepcopy(by_iso))
    gr._persist_grid_cache_with_panel = lambda mid, p=None: p
    gr._defer_aylik_grid_cache_rebuild = lambda mid: None
    istemci = {
        "2026-08-01": {"aylik": 1918.76, "tahsil": 500.0, "kalan": 1418.76},
        "2026-10-01": {"aylik": 1918.76, "tahsil": 300.0, "kalan": 1618.76},  # bayat: plan öncesi brüt
        "2027-08-01": {"aylik": 1700.0, "tahsil": 0.0, "kalan": 0.0},
    }
    app = Flask(__name__)
    f = gr.api_tahsilat_panel_detay.__wrapped__

    def post(plan_var):
        gr._plan_paket_yukle = lambda mids: {int(m): {"planlar": PLAN if plan_var else [], "faturali": [], "belge": {}} for m in mids}
        gr._read_aylik_grid_cache_payload = lambda mid: copy.deepcopy(plan_payload) if plan_var else {"aylar": plan_payload["aylar"]}
        saklanan.clear()
        with app.test_request_context("/x", method="POST", json={"musteri_id": MID, "by_iso": copy.deepcopy(istemci)}):
            f()
        return saklanan.get("by_iso")

    try:
        plan_adi = [dict(PLAN[0], gecerlilik_ay="2026-10-01")]
        PLAN[:] = plan_adi  # plan 2026-10'dan başlar
        s = post(True)
        check("planlı: plan öncesi ay (2026-08) istemci değerinde", s["2026-08-01"]["aylik"] == 1918.76 and s["2026-08-01"]["kalan"] == 1418.76, s)
        check("planlı: bayat 2026-10 brütü plana çekildi", s["2026-10-01"]["aylik"] == 1200.0 and s["2026-10-01"]["kalan"] == 900.0, s)
        check("planlı: 2027-08 sunucu zincirinden", s["2027-08-01"]["aylik"] == 1582.8, s)
        check("planlı: tahsil değişmedi", s["2026-10-01"]["tahsil"] == 300.0)
        d = post(False)
        beklenen = copy.deepcopy(istemci)
        check("plansız: istemci değerleri AYNEN kaydedilir", d == beklenen, d)
        girdi = {"2026-10-01": {"aylik": 1.0, "tahsil": 0.0, "kalan": 0.0}}
        gr._plan_paket_yukle = lambda mids: {int(m): {"planlar": [], "faturali": [], "belge": {}} for m in mids}
        check("plansız: yardımcı girdiyi aynen döndürür", gr._panel_by_iso_plan_duzelt(MID, girdi) is girdi)
    finally:
        for n, v in eski.items():
            setattr(gr, n, v)
        PLAN[:] = [{"gecerlilik_ay": "2024-11-01", "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400}]


def test_js_plan_degerleri_kalir():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("SKIP playwright yok")
        return
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8", errors="replace")

    def fn(ad):
        m = re.search(r"^function " + ad + r"\(.*?^\}", html, flags=re.S | re.M)
        assert m, ad
        return m.group(0)

    kod = "\n".join(fn(a) for a in ("girisPlanGridHucreBrut", "girisPlanYilGecerliAylik", "girisTahsilatYilAylikTek", "girisTahsilatYilBorcTek", "girisPlanYilToplam"))
    # gerçek payload: kayıt sonrası 3 ardışık okuma çıktısı
    eski = _yama(PLAN)
    try:
        p = _kayitli_plan_payload()
        okumalar = []
        for _ in range(3):
            p = gr._aylik_grid_payload_reel_overlay_from_db(MID, p)
            okumalar.append(json.loads(json.dumps(p, default=str)))
    finally:
        _geri(eski)
    sayfa = "<html><body><script>" + kod + """
    window.girisTahsilatYilAyKeysFiltered = function (yil) { var o = []; for (var m = 1; m <= 12; m++) o.push(yil + '-' + m); return o; };
    window.girisTahsilatAyKeyToYilAy = function (k) { var a = String(k).split('-'); return { yil: parseInt(a[0], 10), ay: parseInt(a[1], 10) }; };
    window.girisAylikSatirYilKayitYukle = function () {};
    window.girisAylikSatirYilHesapla = function (y) { return { toplam: 1918.76, kdv_dahil: 1918.76 }; };  /* plansız JS zinciri */
    </script></body></html>"""
    with sync_playwright() as pw:
        try:
            tarayici = pw.chromium.launch(channel="msedge")
        except Exception:
            tarayici = pw.chromium.launch()
        sayfa_o = tarayici.new_page()
        sayfa_o.set_content(sayfa)
        for i, ok in enumerate(okumalar, 1):
            sayfa_o.evaluate("o => { window.__sozlesmelerAylikSonCacheObj = o; }", ok)
            a24 = sayfa_o.evaluate("girisTahsilatYilAylikTek(2024)")
            a25 = sayfa_o.evaluate("girisTahsilatYilAylikTek(2025)")
            b24 = sayfa_o.evaluate("girisTahsilatYilBorcTek(2024)")
            check(f"JS {i}. fetch sonrası yıl 2024/2025 aylık plan", a24 == 2400 and a25 == 2520 or a25 == 2400, (a24, a25))
            check(f"JS {i}. fetch sonrası yıl borcu plan toplamı", b24 > 0 and abs(b24 - sum(v for (y, m), v in hucre(ok).items() if y == 2024)) < 0.02, b24)
        # plansız kart: eski JS zinciri (plan_var yok) aynen
        pl = {"aylar": okumalar[0]["aylar"]}
        sayfa_o.evaluate("o => { window.__sozlesmelerAylikSonCacheObj = o; }", pl)
        check("JS plansız: eski hesap (1918,76)", sayfa_o.evaluate("girisTahsilatYilAylikTek(2024)") == 1918.76)
        tarayici.close()


if __name__ == "__main__":
    test_okuma_yollari_plani_ezmez()
    test_plansiz_birebir_eski()
    test_plan_kutusu_istek_omurlu()
    test_iki_musteri_plan_sizintisi()
    test_panel_post_bayat_istemci()
    test_js_plan_degerleri_kalir()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
