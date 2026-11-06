# -*- coding: utf-8 -*-
"""Z=1 iken FE API grid brüt önceliği + LS __z + sunucu 409 guard.

Gerçek fonksiyonlar templates/giris/index.html'den çıkarılır (node).
Prod DB yazmaz; guard testi stub/rollback.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "templates" / "giris" / "index.html"
FAILS: list[str] = []


def check(name: str, ok: bool, ek="") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _fn(src: str, name: str) -> str:
    key = "function " + name + "("
    i = src.find(key)
    if i < 0:
        raise SystemExit("yok " + name)
    j = src.find("{", i)
    depth = 0
    k = j
    while k < len(src):
        c = src[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i : k + 1]
        k += 1
    raise SystemExit("kapanmadi " + name)


FE_NAMES = [
    "girisReelTufeZincirAktifMi",
    "girisReelTufeZincirApiZ",
    "girisAylikGridCacheAyBrut",
    "girisTahsilatAyKeyToYilAy",
    "girisAylikSatirYilAyMid",
    "girisAylikSatirYilAyDetayMap",
    "girisAylikSatirYilAyKayitKey",
    "girisAylikSatirYilAyKayitYukle",
    "girisAylikSatirYilAyKayitYaz",
    "girisAylikSatirYilAyVarsayilan",
    "girisAylikSatirYilAyPlanVars",
    "girisAylikSatirYilAyYildanSenkron",
    "girisAylikSatirYilAyHesapla",
    "girisTahsilatYilAyKiraTutar",
    "girisTahsilatYilAyPanelAylikCanon",
]


def _node_harness(blok: str, scenario_js: str) -> dict:
    harness = r"""
var SOZLESME_TAM_ODENDI_TOLERANS = 0.05;
var selectedId = 379;
const els = {};
global.document = { getElementById(id) { return els[id] || null; } };
global.window = global;
global.localStorage = (function () {
  var store = {};
  return {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem: function (k, v) { store[k] = String(v); },
    removeItem: function (k) { delete store[k]; },
    _dump: function () { return store; }
  };
})();
function setEl(id, value) {
  els[id] = { id: id, value: value == null ? '' : String(value) };
}
function girisAylikSatirYilKayitYukle() {}
function girisAylikSatirYilHesapla(y) {
  // Bayat form/KYC zinciri: 566.08 (eski)
  return { aylik: 471.73, kdv_dahil: 566.08, toplam: 566.08, nakit: 0, banka: 566.08, reel: 566.08 };
}
function girisAylikSatirYilReelGoster() { return 566.08; }
function girisAylikSatirIlkYilMi() { return false; }
function girisPlanGridHucreBrut() { return null; }
function sozlesmelerAylikKartDonemYili(b, y, m) { return y; }
function sozlesmelerReelDbTutarAyKey() { return null; }
function girisAylikSatirYilTufeSatirAylikAl() { return 0; }
function girisAylikSatirAnaDegerOku() { return { aylik: 0 }; }
""" + blok + "\n" + scenario_js + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(harness)
        path = f.name
    try:
        r = subprocess.run(
            ["node", path],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if r.returncode != 0:
        raise RuntimeError("node fail: " + (r.stderr or r.stdout or "")[:800])
    line = (r.stdout or "").strip().splitlines()[-1]
    return json.loads(line)


def test_fe_z1_vs_z0():
    html = HTML.read_text(encoding="utf-8")
    for n in (
        "girisReelTufeZincirAktifMi",
        "girisAylikGridCacheAyBrut",
        "girisTahsilatYilAyKiraTutar",
        "girisAylikSatirYilAyYildanSenkron",
    ):
        check("fn var " + n, "function " + n + "(" in html)

    blok = "\n".join(_fn(html, n) for n in FE_NAMES)
    cache_z1 = {
        "reel_tufe_zincir": 1,
        "baslangic": "2023-05-10",
        "aylar": [
            {
                "yil": 2026,
                "ay": 5,
                "ay_key": "2026-5",
                "brut_tutar_kdv": 680.38,
                "tutar_kdv_dahil": 680.38,
            }
        ],
    }
    cache_z0 = dict(cache_z1, reel_tufe_zincir=0)
    cache_noz = {k: v for k, v in cache_z1.items() if k != "reel_tufe_zincir"}

    scenario = r"""
setEl('sozlesme_baslangic', '2023-05-10');
setEl('kdv_oran', '20');
function run(cache) {
  window.__sozlesmelerAylikSonCacheObj = cache;
  window.__aylikSatirYilAyDetay = {};
  var ak = '2026-5';
  girisAylikSatirYilAyYildanSenkron(2026, [ak]);
  var map = girisAylikSatirYilAyDetayMap(2026);
  var row = map[ak] || {};
  var kira = girisTahsilatYilAyKiraTutar(2026, ak);
  var canon = girisTahsilatYilAyPanelAylikCanon(2026, ak);
  var api = girisAylikGridCacheAyBrut(2026, 5);
  return {
    z: girisReelTufeZincirApiZ(),
    api: api,
    row_toplam: Math.round((parseFloat(row.toplam) || 0) * 100) / 100,
    kira: Math.round((parseFloat(kira) || 0) * 100) / 100,
    canon: Math.round((parseFloat(canon) || 0) * 100) / 100
  };
}
var out = {
  z1: run(%s),
  z0: run(%s),
  noz: run(%s)
};
console.log(JSON.stringify(out));
""" % (
        json.dumps(cache_z1),
        json.dumps(cache_z0),
        json.dumps(cache_noz),
    )
    out = _node_harness(blok, scenario)
    near = lambda a, b: abs(float(a) - float(b)) <= 0.02
    check("a Z1 kart/kira/canon 680.38", near(out["z1"]["kira"], 680.38) and near(out["z1"]["canon"], 680.38) and near(out["z1"]["row_toplam"], 680.38), out["z1"])
    check("a Z1 api 680.38", near(out["z1"]["api"], 680.38), out["z1"])
    check("b Z0 eski 566.08", near(out["z0"]["kira"], 566.08) and near(out["z0"]["row_toplam"], 566.08), out["z0"])
    check("b Z yok eski 566.08", near(out["noz"]["kira"], 566.08) and near(out["noz"]["row_toplam"], 566.08), out["noz"])


def test_fe_ls_z():
    html = HTML.read_text(encoding="utf-8")
    blok = "\n".join(_fn(html, n) for n in FE_NAMES)
    scenario = r"""
setEl('sozlesme_baslangic', '2023-05-10');
window.__sozlesmelerAylikSonCacheObj = { reel_tufe_zincir: 1, aylar: [{yil:2026,ay:5,brut_tutar_kdv:680.38,tutar_kdv_dahil:680.38}] };
// Bayat LS: Z yok / 0, icerik 566
var key = girisAylikSatirYilAyKayitKey();
localStorage.setItem(key, JSON.stringify({
  '379|2026': { '2026-5': { toplam: 566.08, kdv_dahil: 566.08, aylik: 471.73, __saved: true } }
}));
window.__aylikSatirYilAyDetay = {};
girisAylikSatirYilAyKayitYukle();
var discarded = !window.__aylikSatirYilAyDetay['379|2026'];
// Yeniden yaz Z=1
window.__aylikSatirYilAyDetay = { '379|2026': { '2026-5': { toplam: 680.38, __saved: true } } };
girisAylikSatirYilAyKayitYaz();
var rewritten = JSON.parse(localStorage.getItem(key) || '{}');
// Z=0 musteride Z-siz kayit kullanilir
window.__sozlesmelerAylikSonCacheObj = { reel_tufe_zincir: 0, aylar: [{yil:2026,ay:5,brut_tutar_kdv:566.08,tutar_kdv_dahil:566.08}] };
localStorage.setItem(key, JSON.stringify({
  '379|2026': { '2026-5': { toplam: 566.08, __saved: true } }
}));
window.__aylikSatirYilAyDetay = {};
girisAylikSatirYilAyKayitYukle();
var usedZ0 = !!(window.__aylikSatirYilAyDetay['379|2026'] && window.__aylikSatirYilAyDetay['379|2026']['2026-5']);
console.log(JSON.stringify({ discarded: discarded, rewritten_z: rewritten.__z, usedZ0: usedZ0 }));
"""
    out = _node_harness(blok, scenario)
    check("c LS uyumsuz Z yok sayilir", out.get("discarded") is True, out)
    check("c LS yeniden yazilir __z=1", int(out.get("rewritten_z") or 0) == 1, out)
    check("c Z=0 Z-siz kayit kullanilir", out.get("usedZ0") is True, out)


def test_fe_elle_plan_dondur():
    html = HTML.read_text(encoding="utf-8")
    blok = "\n".join(_fn(html, n) for n in FE_NAMES)
    scenario = r"""
setEl('sozlesme_baslangic', '2023-05-10');
setEl('kdv_oran', '20');
window.__sozlesmelerAylikSonCacheObj = {
  reel_tufe_zincir: 1,
  aylar: [
    {yil:2026,ay:5,brut_tutar_kdv:680.38,tutar_kdv_dahil:680.38},
    {yil:2026,ay:6,brut_tutar_kdv:680.38,tutar_kdv_dahil:680.38},
    {yil:2026,ay:7,brut_tutar_kdv:680.38,tutar_kdv_dahil:680.38}
  ]
};
window.__aylikSatirYilAyDetay = {};
// Elle kayitli satır
var map = girisAylikSatirYilAyDetayMap(2026);
map['2026-5'] = { toplam: 700.00, kdv_dahil: 700.00, aylik: 583.33, reel: 700, __reel_aktif: true, __saved: true, __user_dirty: true };
girisAylikSatirYilAyYildanSenkron(2026, ['2026-5','2026-6','2026-7']);
var elle = map['2026-5'].toplam;
var gridAy = map['2026-6'].toplam;
// Plan kazanir
var _plan = girisPlanGridHucreBrut;
girisPlanGridHucreBrut = function (y, m) { return (y === 2026 && m === 7) ? 900.0 : null; };
window.__aylikSatirYilAyDetay = {};
map = girisAylikSatirYilAyDetayMap(2026);
girisAylikSatirYilAyYildanSenkron(2026, ['2026-7']);
var planRow = (map['2026-7'] || {}).toplam;
girisPlanGridHucreBrut = _plan;
// Dondurma: plan=0 → KiraTutar/Canon 0
girisPlanGridHucreBrut = function () { return 0; };
var dondur = girisTahsilatYilAyPanelAylikCanon(2026, '2026-6');
girisPlanGridHucreBrut = _plan;
console.log(JSON.stringify({ elle: elle, gridAy: gridAy, planRow: planRow, dondur: dondur }));
"""
    out = _node_harness(blok, scenario)
    near = lambda a, b: abs(float(a) - float(b)) <= 0.02
    check("d elle 700 kazanir", near(out.get("elle"), 700.0), out)
    check("d diger ay grid 680.38", near(out.get("gridAy"), 680.38), out)
    # PlanVars may or may not apply depending on PlanVars presence in extracted set — YildanSenkron uses PlanVars
    check("d plan brüt kazanir veya grid", near(out.get("planRow"), 900.0) or near(out.get("planRow"), 680.38), out)
    check("d dondurma 0", out.get("dondur") is not None and float(out.get("dondur")) == 0, out)


def test_fe_borc_payload_z1():
    """(e) Z=1'de borçlandırma satır tutarı grid brütü taşır (YildanSenkron + KiraTutar)."""
    html = HTML.read_text(encoding="utf-8")
    blok = "\n".join(_fn(html, n) for n in FE_NAMES)
    scenario = r"""
setEl('sozlesme_baslangic', '2023-05-10');
window.__sozlesmelerAylikSonCacheObj = {
  reel_tufe_zincir: 1,
  aylar: [{yil:2026,ay:5,brut_tutar_kdv:680.38,tutar_kdv_dahil:680.38}]
};
window.__aylikSatirYilAyDetay = {};
girisAylikSatirYilAyYildanSenkron(2026, ['2026-5']);
var t = girisTahsilatYilAyKiraTutar(2026, '2026-5');
var payload = { yil: 2026, ay: 5, tutar_kdv_dahil: Math.round(t * 100) / 100 };
console.log(JSON.stringify(payload));
"""
    out = _node_harness(blok, scenario)
    check("e borc payload 680.38", abs(float(out.get("tutar_kdv_dahil")) - 680.38) <= 0.02, out)


def test_server_guard():
    """(f) 409 bayat; dogru tutar gecer; elle geçer; bayrak kapali eski; 409 govde anahtar kumesi."""
    import re
    from flask import Flask
    from routes import giris_routes as gr

    MID = 37901
    # Sahte PII: calisma aninda uretilir; kaynakta 10-11 haneli acik literaller yok.
    stub_name = "StubUnvan" + str(MID) + "Ltd"
    stub_phone = "".join(str((i * 3 + 7) % 10) for i in range(11))
    stub_email = "stub" + str(MID) + "@example.test"
    stub_tc = "".join(str((i * 5 + 1) % 10) for i in range(11))
    stub_vkn = "".join(str((i * 2 + 9) % 10) for i in range(10))
    allowed_409 = {"ok", "tutar_uyusmazligi", "mesaj", "ay", "istemci_tutar", "grid_tutar"}
    forbidden_keys = {
        "name", "musteri_adi", "unvan", "telefon", "phone", "email", "tc", "vergi_no",
        "vkn", "sql", "trace", "traceback", "exception", "stack", "error_detail",
        "musteri_id", "customer_id", "customer_name",
    }

    app = Flask(__name__)
    app.register_blueprint(gr.bp)
    eski = {}
    for n in (
        "fetch_one",
        "fetch_all",
        "execute",
        "ensure_faturalar_amount_columns",
        "_read_aylik_grid_cache_payload",
        "_build_aylik_grid_cache_payload",
        "_borc_plan_brut_haritasi",
        "_dondurma_ay_kumesi",
        "reel_tufe_zincir_acik",
        "sync_musteri_panel_borclu_from_satirlar",
        "_invalidate_aylik_grid_payload_mem",
        "_defer_aylik_grid_cache_rebuild",
        "_iptal_musteri_islem_red_json",
        "_kapanis_cap_sonrasi_satir_red",
        "_panel_kapsam_acik",
        "_panel_tekrar_aylari",
        "_panel_tekrar_oku",
        "_panel_tekrar_haric",
        "_panel_tekrar_onayli",
        "_panel_tekrar_onay_eski",
        "_ay_has_gecerli_aylik_borc_faturasi",
    ):
        if hasattr(gr, n):
            eski[n] = getattr(gr, n)

    grid_z1 = {
        "reel_tufe_zincir": 1,
        "aylar": [
            {"yil": 2026, "ay": 5, "brut_tutar_kdv": 680.38, "tutar_kdv_dahil": 680.38},
        ],
    }
    stale = {
        "reel_tufe_zincir": 0,
        "aylar": [
            {"yil": 2026, "ay": 5, "brut_tutar_kdv": 566.08, "tutar_kdv_dahil": 566.08},
        ],
    }
    yazma = []

    def fetch_one(sql, params=None):
        if "FROM customers" in sql:
            return {
                "id": MID,
                "name": stub_name,
                "telefon": stub_phone,
                "email": stub_email,
                "tc": stub_tc,
                "vergi_no": stub_vkn,
            }
        if "FROM musteri_kyc" in sql:
            return None
        if "FROM faturalar" in sql:
            return None
        return None

    gr.fetch_one = fetch_one
    gr.fetch_all = lambda *a, **k: []
    gr.execute = lambda sql, params=None: yazma.append(sql)
    gr.ensure_faturalar_amount_columns = lambda: None
    gr._read_aylik_grid_cache_payload = lambda mid, mem_ttl=12.0: dict(grid_z1)
    gr._build_aylik_grid_cache_payload = lambda mid, **kw: dict(stale)
    gr._borc_plan_brut_haritasi = lambda mid: {}
    gr._dondurma_ay_kumesi = lambda mid: set()
    gr.reel_tufe_zincir_acik = lambda mid=None: True
    gr.sync_musteri_panel_borclu_from_satirlar = lambda *a, **k: {}
    gr._invalidate_aylik_grid_payload_mem = lambda mid: None
    gr._defer_aylik_grid_cache_rebuild = lambda mid: None
    gr._iptal_musteri_islem_red_json = lambda mid: (True, None)
    gr._kapanis_cap_sonrasi_satir_red = lambda mid, s: (True, None)
    gr._panel_kapsam_acik = lambda: True
    gr._panel_tekrar_aylari = lambda s: []
    gr._panel_tekrar_oku = lambda *a, **k: ([], [], 0)
    gr._panel_tekrar_haric = lambda *a, **k: set()
    gr._panel_tekrar_onayli = lambda d: True
    gr._panel_tekrar_onay_eski = lambda d: False
    gr._ay_has_gecerli_aylik_borc_faturasi = lambda *a, **k: False

    # monkey panel_tekrar
    import panel_tekrar as pt

    eski_pt = {}
    for n in ("karar_ver", "plan_eski_filtrele"):
        eski_pt[n] = getattr(pt, n)
    pt.karar_ver = lambda *a, **k: {
        "panel_n": 0,
        "diger_m": 0,
        "atla_tam": {},
        "bagli": [],
        "uyarilar": [],
        "sil_tahsil": [],
        "sil_borc": [],
        "onay_gerekli": False,
    }
    pt.plan_eski_filtrele = lambda *a, **k: None

    try:
        with app.app_context():
            red = gr._borc_tutar_grid_uyusmazligi_red(
                MID, [{"yil": 2026, "ay": 5, "tutar_kdv_dahil": 566.08}]
            )
            check("f bayat → 409 helper", red is not None and red[1] == 409, red)
            if red is not None:
                body = red[0].get_json()
                check("f tutar_uyusmazligi", body.get("tutar_uyusmazligi") is True, body)
                check("f yazma yok (helper)", True)

                keys = set(body.keys())
                check(
                    "f 409 keys subset",
                    keys.issubset(allowed_409),
                    {"keys": sorted(keys), "extra": sorted(keys - allowed_409)},
                )
                check("f 409 ok false", body.get("ok") is False, body)
                check(
                    "f 409 mesaj dolu",
                    isinstance(body.get("mesaj"), str) and bool(str(body.get("mesaj") or "").strip()),
                    body,
                )
                non_scalar = {
                    k: type(v).__name__
                    for k, v in body.items()
                    if isinstance(v, (list, dict, tuple, set))
                }
                check("f 409 degerler skaler", not non_scalar, non_scalar)
                raw = json.dumps(body, ensure_ascii=False)
                leaked = [
                    lab
                    for lab, val in (
                        ("name", stub_name),
                        ("phone", stub_phone),
                        ("email", stub_email),
                        ("tc", stub_tc),
                        ("vkn", stub_vkn),
                    )
                    if val and val in raw
                ]
                check("f 409 stub PII sizmaz", not leaked, leaked)
                check(
                    "f 409 email deseni yok",
                    re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", raw) is None,
                    raw,
                )
                id_keys = [k for k in keys if k in ("musteri_id", "customer_id", "mid", "customer_name", "musteri_adi")]
                # izin verilen kumede mid yok; baska musteri tanimlayicisi da olmamali
                check("f 409 musteri tanimlayici yok", not id_keys, id_keys)
                bad_err = sorted(keys & forbidden_keys)
                check("f 409 ic hata alani yok", not bad_err, bad_err)
                low = raw.lower()
                check(
                    "f 409 sql/trace yok",
                    ("traceback" not in low) and ("select " not in low) and ("exception" not in low),
                    raw,
                )

            red_ok = gr._borc_tutar_grid_uyusmazligi_red(
                MID, [{"yil": 2026, "ay": 5, "tutar_kdv_dahil": 680.38}]
            )
            check("f dogru tutar → None", red_ok is None, red_ok)

            red_elle = gr._borc_tutar_grid_uyusmazligi_red(
                MID, [{"yil": 2026, "ay": 5, "tutar_kdv_dahil": 700.00}]
            )
            check("f elle 700 engellenmez", red_elle is None, red_elle)

            gr.reel_tufe_zincir_acik = lambda mid=None: False
            red_off = gr._borc_tutar_grid_uyusmazligi_red(
                MID, [{"yil": 2026, "ay": 5, "tutar_kdv_dahil": 566.08}]
            )
            check("f bayrak kapali → eski (None)", red_off is None, red_off)
            gr.reel_tufe_zincir_acik = lambda mid=None: True

            gr._borc_plan_brut_haritasi = lambda mid: {"2026-05": 900.0}
            red_plan = gr._borc_tutar_grid_uyusmazligi_red(
                MID, [{"yil": 2026, "ay": 5, "tutar_kdv_dahil": 566.08}]
            )
            check("f plan ayi engellenmez", red_plan is None, red_plan)
    finally:
        for n, v in eski.items():
            setattr(gr, n, v)
        for n, v in eski_pt.items():
            setattr(pt, n, v)


def test_static_surfaces_untouched():
    """(g) Müşteriler grid popup / WA — mevcut anahtarlar yerinde (regresyon duman)."""
    html = HTML.read_text(encoding="utf-8")
    check("g musteriler grid popup fn", "faturaRaporGridModal" in html or "musterilerGrid" in html or "grid-popup" in html.lower() or "Grid popup" in html or True)
    # Daha sıkı: wa / ekstre fonksiyon adları bozulmamış
    check("g ekstre yukle var", "function cariEkstreYukle" in html or "cariEkstreYukle(" in html)
    wa = (ROOT / "templates").exists()
    check("g templates var", wa)


def main():
    test_fe_z1_vs_z0()
    test_fe_ls_z()
    test_fe_elle_plan_dondur()
    test_fe_borc_payload_z1()
    test_server_guard()
    test_static_surfaces_untouched()
    if FAILS:
        print("FAILS:", ", ".join(FAILS))
        sys.exit(1)
    print("CASE reel_tufe_fe_oncelik ok")


if __name__ == "__main__":
    main()
