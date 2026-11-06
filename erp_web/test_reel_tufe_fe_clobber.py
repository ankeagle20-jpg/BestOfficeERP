# -*- coding: utf-8 -*-
"""Z=1 FE: PanelHtml clobber, NormGrid form-atla, sticky Z.

Gerçek fonksiyonlar templates/giris/index.html'den çıkarılır (node).
Prod yazmaz; commit yok. Test numaraları çalışma anında üretilir.
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


def near(a, b, tol=0.02) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


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


def _fn_from_commit(commit: str, name: str) -> str:
    r = subprocess.run(
        ["git", "show", f"{commit}:erp_web/templates/giris/index.html"],
        cwd=str(ROOT.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if r.returncode != 0:
        raise SystemExit("git show fail " + commit + " " + (r.stderr or "")[:200])
    return _fn(r.stdout, name)


FE_CORE = [
    "girisReelTufeZincirStickyBag",
    "girisReelTufeZincirStickyMidCoz",
    "girisReelTufeZincirCacheStickyUygula",
    "girisReelTufeZincirAktifMi",
    "girisReelTufeZincirApiZ",
    "girisAylikGridCacheAyBrut",
    "girisTahsilatAyKeyToYilAy",
    "girisAylikSatirYilAyMid",
    "girisAylikSatirYilAyDetayMap",
    "girisAylikSatirYilAyKayitKey",
    "girisAylikSatirYilAyVarsayilan",
    "girisAylikSatirYilAyPlanVars",
    "girisAylikSatirYilAyYildanSenkron",
    "girisAylikSatirYilAyHesapla",
    "girisAylikSatirYilAyAdiGoster",
    "girisAylikSatirYilAyId",
    "girisAylikSatirYilAyPanelHtml",
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
    removeItem: function (k) { delete store[k]; }
  };
})();
function setEl(id, value) {
  els[id] = { id: id, value: value == null ? '' : String(value) };
}
function girisAylikSatirYilKayitYukle() {}
function girisAylikSatirYilHesapla(y) {
  return { aylik: 471.73, kdv_dahil: 566.08, toplam: 566.08, nakit: 0, banka: 566.08, reel: 0 };
}
function girisAylikSatirYilReelGoster() { return 0; }
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
        raise RuntimeError("node fail: " + (r.stderr or r.stdout or "")[:900])
    line = (r.stdout or "").strip().splitlines()[-1]
    return json.loads(line)


def _parse_may_toplam(html: str) -> float | None:
    """PanelHtml çıktısından Mayıs 2026 toplam input value."""
    if not html:
        return None
    # rough: find Mayıs satırı block then toplam class value
    import re

    m = re.search(
        r"May[ıi]s 2026[\s\S]*?aylik-yil-ay-toplam[^>]*value=\"([0-9.]+)\"",
        html,
        re.I,
    )
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def test_1_panelhtml_z1_no_clobber():
    html = HTML.read_text(encoding="utf-8")
    for n in FE_CORE:
        check("fn " + n, "function " + n + "(" in html)
    blok = "\n".join(_fn(html, n) for n in FE_CORE)
    scenario = r"""
setEl('sozlesme_baslangic', '2023-05-10');
setEl('kdv_oran', '20');
window.__reelTufeZincirStickyByMid = {};
window.__reelTufeZincirStickyAylarByMid = {};
window.__sozlesmelerAylikSonCacheObj = {
  musteri_id: 379,
  reel_tufe_zincir: 1,
  baslangic: '2023-05-10',
  aylar: [{yil:2026,ay:5,brut_tutar_kdv:680.38,tutar_kdv_dahil:680.38}]
};
window.__aylikSatirYilAyDetay = {};
var ak = '2026-5';
girisAylikSatirYilAyYildanSenkron(2026, [ak]);
var afterSync = Object.assign({}, girisAylikSatirYilAyDetayMap(2026)[ak] || {});
var htmlOut = girisAylikSatirYilAyPanelHtml(2026, [ak]);
var afterPanel = Object.assign({}, girisAylikSatirYilAyDetayMap(2026)[ak] || {});
console.log(JSON.stringify({
  afterSync: afterSync.toplam,
  afterPanel: afterPanel.toplam,
  reelAktif: afterPanel.__reel_aktif,
  html: htmlOut
}));
"""
    out = _node_harness(blok, scenario)
    dom = _parse_may_toplam(out.get("html") or "")
    check("1 afterSync 680.38", near(out.get("afterSync"), 680.38), out)
    check("1 afterPanel 680.38", near(out.get("afterPanel"), 680.38), out)
    check("1 DOM 680.38", near(dom, 680.38), {"dom": dom})
    check("1 not 566.08", not near(out.get("afterPanel"), 566.08), out)


def test_2_normgrid_z1_skips_form():
    html = HTML.read_text(encoding="utf-8")
    blok = "\n".join(
        _fn(html, n)
        for n in (
            "girisReelTufeZincirStickyBag",
            "girisReelTufeZincirStickyMidCoz",
            "girisReelTufeZincirCacheStickyUygula",
            "girisReelTufeZincirAktifMi",
            "sozlesmelerAylikNormGridKartTutarlariniYenile",
        )
    )
    scenario = r"""
var formPaint = 0;
var flatPaint = 0;
window.__sozlesmelerAylikSonCacheObj = { musteri_id: 379, reel_tufe_zincir: 1, aylar: [{yil:2026,ay:5,brut_tutar_kdv:680.38}] };
window.__reelTufeZincirStickyByMid = { '379': 1 };
global.sozlesmelerAylikHucreToplamHaritasi = function () { formPaint++; return { 2026: 566.08 }; };
global.sozlesmelerAylikCacheBrutFlatDisiUygula = function () { flatPaint++; };
global.girisFormIlkDonemKdvDahilTutar = function () { formPaint++; return 180; };
global._sozlesmelerAylikTekGridKartTutarlariniYenile = function () { formPaint++; };
global.girisAylikSatirYilKayitYukle = function () {};
global.parseTarihStr = function (s) { return new Date(s); };
setEl('sozlesme_baslangic', '2023-05-10');
// minimal card so maxYil path not needed due to early return
sozlesmelerAylikNormGridKartTutarlariniYenile();
console.log(JSON.stringify({ formPaint: formPaint, flatPaint: flatPaint, z: girisReelTufeZincirAktifMi() }));
"""
    out = _node_harness(blok, scenario)
    check("2 Z aktif", out.get("z") is True, out)
    check("2 BrutFlat çağrıldı", int(out.get("flatPaint") or 0) >= 1, out)
    check("2 form boyama yok", int(out.get("formPaint") or 0) == 0, out)


def test_3_sticky_z_keeps_680():
    html = HTML.read_text(encoding="utf-8")
    blok = "\n".join(
        _fn(html, n)
        for n in (
            "girisReelTufeZincirStickyBag",
            "girisReelTufeZincirStickyMidCoz",
            "girisReelTufeZincirCacheStickyUygula",
            "girisReelTufeZincirAktifMi",
            "girisAylikGridCacheAyBrut",
        )
    )
    scenario = r"""
window.__reelTufeZincirStickyByMid = {};
window.__reelTufeZincirStickyAylarByMid = {};
selectedId = 379;
var z1 = { musteri_id: 379, reel_tufe_zincir: 1, aylar: [{yil:2026,ay:5,brut_tutar_kdv:680.38,tutar_kdv_dahil:680.38}] };
var applied = girisReelTufeZincirCacheStickyUygula(z1);
window.__sozlesmelerAylikSonCacheObj = applied;
var noz = { musteri_id: 379, aylar: [{yil:2026,ay:5,brut_tutar_kdv:680.38,tutar_kdv_dahil:680.38}] };
var afterNoz = girisReelTufeZincirCacheStickyUygula(noz);
window.__sozlesmelerAylikSonCacheObj = afterNoz;
var z0 = { musteri_id: 379, reel_tufe_zincir: 0, aylar: [{yil:2026,ay:5,brut_tutar_kdv:566.08,tutar_kdv_dahil:566.08}] };
var afterZ0 = girisReelTufeZincirCacheStickyUygula(z0);
window.__sozlesmelerAylikSonCacheObj = afterZ0;
console.log(JSON.stringify({
  sticky: window.__reelTufeZincirStickyByMid['379'],
  zAfterNoz: girisReelTufeZincirAktifMi(afterNoz),
  zAfterZ0: girisReelTufeZincirAktifMi(afterZ0),
  flagNoz: afterNoz.reel_tufe_zincir,
  flagZ0: afterZ0.reel_tufe_zincir,
  mayNoz: girisAylikGridCacheAyBrut(2026, 5, afterNoz),
  mayZ0: girisAylikGridCacheAyBrut(2026, 5, afterZ0)
}));
"""
    out = _node_harness(blok, scenario)
    check("3 sticky=1", int(out.get("sticky") or 0) == 1, out)
    check("3 Z-yok sonrası aktif", out.get("zAfterNoz") is True, out)
    check("3 Z0 sonrası aktif", out.get("zAfterZ0") is True, out)
    check("3 Z-yok flag 1", int(out.get("flagNoz") or 0) == 1, out)
    check("3 Z0 flag 1", int(out.get("flagZ0") or 0) == 1, out)
    check("3 Z-yok may 680.38", near(out.get("mayNoz"), 680.38), out)
    check("3 Z0 may 680.38 (566 ezilmedi)", near(out.get("mayZ0"), 680.38), out)


def test_4_z0_birebir_7800b8b():
    """Z=0: PanelHtml map toplamı 7800b8b ile birebir (aynı fixture)."""
    html = HTML.read_text(encoding="utf-8")
    cur_names = [
        "girisReelTufeZincirStickyBag",
        "girisReelTufeZincirStickyMidCoz",
        "girisReelTufeZincirCacheStickyUygula",
        "girisReelTufeZincirAktifMi",
        "girisReelTufeZincirApiZ",
        "girisAylikGridCacheAyBrut",
        "girisTahsilatAyKeyToYilAy",
        "girisAylikSatirYilAyMid",
        "girisAylikSatirYilAyDetayMap",
        "girisAylikSatirYilAyVarsayilan",
        "girisAylikSatirYilAyPlanVars",
        "girisAylikSatirYilAyYildanSenkron",
        "girisAylikSatirYilAyHesapla",
        "girisAylikSatirYilAyAdiGoster",
        "girisAylikSatirYilAyId",
        "girisAylikSatirYilAyPanelHtml",
    ]
    blok_cur = "\n".join(_fn(html, n) for n in cur_names)
    # 7800b8b: sticky helpers yok; AktifMi eski + PanelHtml eski
    old_aktif = _fn_from_commit("7800b8b", "girisReelTufeZincirAktifMi")
    old_api = _fn_from_commit("7800b8b", "girisReelTufeZincirApiZ")
    old_brut = _fn_from_commit("7800b8b", "girisAylikGridCacheAyBrut")
    old_ya = _fn_from_commit("7800b8b", "girisTahsilatAyKeyToYilAy")
    old_mid = _fn_from_commit("7800b8b", "girisAylikSatirYilAyMid")
    old_map = _fn_from_commit("7800b8b", "girisAylikSatirYilAyDetayMap")
    old_vars = _fn_from_commit("7800b8b", "girisAylikSatirYilAyVarsayilan")
    old_plan = _fn_from_commit("7800b8b", "girisAylikSatirYilAyPlanVars")
    old_sen = _fn_from_commit("7800b8b", "girisAylikSatirYilAyYildanSenkron")
    old_hes = _fn_from_commit("7800b8b", "girisAylikSatirYilAyHesapla")
    old_adi = _fn_from_commit("7800b8b", "girisAylikSatirYilAyAdiGoster")
    old_id = _fn_from_commit("7800b8b", "girisAylikSatirYilAyId")
    old_panel = _fn_from_commit("7800b8b", "girisAylikSatirYilAyPanelHtml")
    blok_old = "\n".join(
        [
            old_aktif,
            old_api,
            old_brut,
            old_ya,
            old_mid,
            old_map,
            old_vars,
            old_plan,
            old_sen,
            old_hes,
            old_adi,
            old_id,
            old_panel,
        ]
    )
    scenario = r"""
setEl('sozlesme_baslangic', '2023-05-10');
setEl('kdv_oran', '20');
window.__reelTufeZincirStickyByMid = {};
window.__reelTufeZincirStickyAylarByMid = {};
window.__sozlesmelerAylikSonCacheObj = {
  musteri_id: 379,
  reel_tufe_zincir: 0,
  baslangic: '2023-05-10',
  aylar: [{yil:2026,ay:5,brut_tutar_kdv:566.08,tutar_kdv_dahil:566.08}]
};
window.__aylikSatirYilAyDetay = {};
girisAylikSatirYilAyYildanSenkron(2026, ['2026-5']);
girisAylikSatirYilAyPanelHtml(2026, ['2026-5']);
var row = girisAylikSatirYilAyDetayMap(2026)['2026-5'] || {};
console.log(JSON.stringify({ toplam: row.toplam, kdv: row.kdv_dahil, z: girisReelTufeZincirAktifMi() }));
"""
    out_cur = _node_harness(blok_cur, scenario)
    out_old = _node_harness(blok_old, scenario)
    check("4 Z0 z false cur", out_cur.get("z") is False, out_cur)
    check("4 Z0 z false old", out_old.get("z") is False, out_old)
    check(
        "4 Z0 toplam birebir",
        near(out_cur.get("toplam"), out_old.get("toplam"))
        and near(out_cur.get("toplam"), 566.08),
        {"cur": out_cur, "old": out_old},
    )
    # NormGrid Z=0: early-return yok — her iki sürümde form yolu çalışır (çağrı sayacı)
    blok_norm_cur = "\n".join(
        _fn(html, n)
        for n in (
            "girisReelTufeZincirStickyBag",
            "girisReelTufeZincirStickyMidCoz",
            "girisReelTufeZincirCacheStickyUygula",
            "girisReelTufeZincirAktifMi",
            "sozlesmelerAylikNormGridKartTutarlariniYenile",
        )
    )
    old_norm = _fn_from_commit("7800b8b", "sozlesmelerAylikNormGridKartTutarlariniYenile")
    old_aktif2 = _fn_from_commit("7800b8b", "girisReelTufeZincirAktifMi")
    scenario_n = r"""
window.__sozlesmelerAylikSonCacheObj = { musteri_id: 379, reel_tufe_zincir: 0, baslangic: '2023-05-10', aylar: [] };
window.__reelTufeZincirStickyByMid = {};
var flatPaint = 0, formPaint = 0;
global.sozlesmelerAylikCacheBrutFlatDisiUygula = function () { flatPaint++; };
global.sozlesmelerAylikHucreToplamHaritasi = function () { formPaint++; return { 2026: 566.08 }; };
global.girisFormIlkDonemKdvDahilTutar = function () { return 180; };
global._sozlesmelerAylikTekGridKartTutarlariniYenile = function () { formPaint++; };
global.girisAylikSatirYilKayitYukle = function () {};
global.parseTarihStr = function (s) { return new Date(s); };
global.sozlesmelerAylikKartDonemYili = function (b,y,m) { return y; };
setEl('sozlesme_baslangic', '2023-05-10');
document.getElementById = function (id) {
  if (id === 'sozlesme_baslangic') return { value: '2023-05-10' };
  if (id === 'sozlesmeler-aylik-grid' || id === 'sozlesmeler-reel-aylik-grid') {
    return { querySelectorAll: function () { return []; } };
  }
  return null;
};
sozlesmelerAylikNormGridKartTutarlariniYenile();
console.log(JSON.stringify({ flatPaint: flatPaint, formPaint: formPaint, z: girisReelTufeZincirAktifMi() }));
"""
    out_nc = _node_harness(blok_norm_cur, scenario_n)
    out_no = _node_harness(old_aktif2 + "\n" + old_norm, scenario_n)
    # Z=0: early-return yok → formPaint>=1; sondaki BrutFlat (eski davranış) flatPaint olabilir.
    # Z=1 early-return olsaydı formPaint=0, flatPaint=1 olurdu.
    check(
        "4 Norm Z0 form yolu cur (early-return degil)",
        int(out_nc.get("formPaint") or 0) >= 1 and out_nc.get("z") is False,
        out_nc,
    )
    check(
        "4 Norm Z0 form yolu old",
        int(out_no.get("formPaint") or 0) >= 1 and out_no.get("z") is False,
        out_no,
    )
    check(
        "4 Norm Z0 formPaint birebir",
        int(out_nc.get("formPaint") or 0) == int(out_no.get("formPaint") or 0),
        {"cur": out_nc, "old": out_no},
    )


def test_5_elle_plan_saved_wins():
    html = HTML.read_text(encoding="utf-8")
    names = list(FE_CORE) + ["girisTahsilatYilAyKiraTutar", "girisTahsilatYilAyPanelAylikCanon"]
    blok = "\n".join(_fn(html, n) for n in names)
    scenario = r"""
setEl('sozlesme_baslangic', '2023-05-10');
setEl('kdv_oran', '20');
window.__reelTufeZincirStickyByMid = {};
window.__sozlesmelerAylikSonCacheObj = {
  musteri_id: 379, reel_tufe_zincir: 1,
  aylar: [
    {yil:2026,ay:5,brut_tutar_kdv:680.38,tutar_kdv_dahil:680.38},
    {yil:2026,ay:6,brut_tutar_kdv:680.38,tutar_kdv_dahil:680.38},
    {yil:2026,ay:7,brut_tutar_kdv:680.38,tutar_kdv_dahil:680.38}
  ]
};
window.__aylikSatirYilAyDetay = {};
var map = girisAylikSatirYilAyDetayMap(2026);
map['2026-5'] = { toplam: 700, kdv_dahil: 700, aylik: 583.33, reel: 700, __reel_aktif: true, __saved: true, __user_dirty: true };
girisAylikSatirYilAyYildanSenkron(2026, ['2026-5','2026-6']);
girisAylikSatirYilAyPanelHtml(2026, ['2026-5','2026-6']);
var elle = (girisAylikSatirYilAyDetayMap(2026)['2026-5'] || {}).toplam;
var gridAy = (girisAylikSatirYilAyDetayMap(2026)['2026-6'] || {}).toplam;
var _plan = girisPlanGridHucreBrut;
girisPlanGridHucreBrut = function (y, m) { return (y === 2026 && m === 7) ? 900 : null; };
window.__aylikSatirYilAyDetay = {};
girisAylikSatirYilAyYildanSenkron(2026, ['2026-7']);
girisAylikSatirYilAyPanelHtml(2026, ['2026-7']);
var planRow = (girisAylikSatirYilAyDetayMap(2026)['2026-7'] || {}).toplam;
girisPlanGridHucreBrut = _plan;
// Dondurma (mevcut kural): plan brüt 0 → PanelAylikCanon/Kira yolu 0 (oncelik testi ile aynı)
girisPlanGridHucreBrut = function () { return 0; };
var donCanon = (typeof girisTahsilatYilAyPanelAylikCanon === 'function')
  ? girisTahsilatYilAyPanelAylikCanon(2026, '2026-6')
  : girisTahsilatYilAyKiraTutar(2026, '2026-6');
// __saved satır PanelHtml sonrası bozulmasın
var savedBefore = 711.11;
window.__aylikSatirYilAyDetay = {};
var mS = girisAylikSatirYilAyDetayMap(2026);
mS['2026-5'] = { toplam: savedBefore, kdv_dahil: savedBefore, aylik: 500, reel: savedBefore, __reel_aktif: true, __saved: true };
girisAylikSatirYilAyPanelHtml(2026, ['2026-5']);
var savedAfter = (girisAylikSatirYilAyDetayMap(2026)['2026-5'] || {}).toplam;
girisPlanGridHucreBrut = _plan;
console.log(JSON.stringify({ elle: elle, gridAy: gridAy, planRow: planRow, donCanon: donCanon, savedAfter: savedAfter }));
"""
    out = _node_harness(blok, scenario)
    check("5 elle 700", near(out.get("elle"), 700), out)
    check("5 unsaved grid 680.38", near(out.get("gridAy"), 680.38), out)
    check("5 plan 900", near(out.get("planRow"), 900), out)
    check("5 dondurma canon 0", out.get("donCanon") is not None and float(out.get("donCanon")) == 0, out)
    check("5 __saved korunur", near(out.get("savedAfter"), 711.11), out)


def test_6_thirteen_response_sim():
    html = HTML.read_text(encoding="utf-8")
    blok = "\n".join(_fn(html, n) for n in FE_CORE)
    scenario = r"""
setEl('sozlesme_baslangic', '2023-05-10');
setEl('kdv_oran', '20');
window.__reelTufeZincirStickyByMid = {};
window.__reelTufeZincirStickyAylarByMid = {};
selectedId = 379;
var seq = [];
for (var i = 1; i <= 13; i++) {
  var c = { musteri_id: 379, aylar: [{yil:2026,ay:5,brut_tutar_kdv:680.38,tutar_kdv_dahil:680.38}] };
  if (i === 3) { /* Z yok */ }
  else if (i === 7) { c.reel_tufe_zincir = 0; c.aylar[0].brut_tutar_kdv = 566.08; c.aylar[0].tutar_kdv_dahil = 566.08; }
  else { c.reel_tufe_zincir = 1; }
  c = girisReelTufeZincirCacheStickyUygula(c);
  window.__sozlesmelerAylikSonCacheObj = c;
  seq.push({ n: i, z: girisReelTufeZincirAktifMi(c), may: girisAylikGridCacheAyBrut(2026,5,c) });
}
window.__aylikSatirYilAyDetay = {};
girisAylikSatirYilAyYildanSenkron(2026, ['2026-5']);
girisAylikSatirYilAyPanelHtml(2026, ['2026-5']);
var borc = (girisAylikSatirYilAyDetayMap(2026)['2026-5'] || {}).toplam;
var grid = girisAylikGridCacheAyBrut(2026, 5);
console.log(JSON.stringify({ seq: seq, borc: borc, grid: grid, z: girisReelTufeZincirAktifMi() }));
"""
    out = _node_harness(blok, scenario)
    check("6 son Z aktif", out.get("z") is True, out)
    check("6 grid 680.38", near(out.get("grid"), 680.38), out)
    check("6 borc 680.38", near(out.get("borc"), 680.38), out)
    # #7 sonrası may hâlâ 680
    seq = out.get("seq") or []
    s7 = next((x for x in seq if x.get("n") == 7), None)
    check("6 #7 sticky may 680.38", s7 and near(s7.get("may"), 680.38), s7)


def main():
    # Temiz ortam bayrakları
    for k in list(os.environ):
        if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_")):
            os.environ.pop(k, None)
    os.environ.pop("ALLOW_PROD_WRITE", None)

    test_1_panelhtml_z1_no_clobber()
    test_2_normgrid_z1_skips_form()
    test_3_sticky_z_keeps_680()
    test_4_z0_birebir_7800b8b()
    test_5_elle_plan_saved_wins()
    test_6_thirteen_response_sim()

    if FAILS:
        print("FAIL count", len(FAILS), FAILS)
        raise SystemExit(1)
    print("CASE reel_tufe_fe_clobber ok")


if __name__ == "__main__":
    main()
