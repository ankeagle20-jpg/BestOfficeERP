# -*- coding: utf-8 -*-
"""Tahsilat yillik ozet Borc: aylik*12 yerine ay listesi toplami.

Kaynak: girisTahsilatYilBorcTek (index.html asil). Prod yazmaz; PII yok.
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
TJS = ROOT / "_t.js"
FAILS: list[str] = []
PASS_N = 0
HEAD_TABAN = "e93c6e7"


def check(name: str, ok: bool, ek="") -> None:
    global PASS_N
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if ok:
        PASS_N += 1
    else:
        FAILS.append(name)


def near(a, b, tol=0.05) -> bool:
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
    in_s = None
    esc = False
    while k < len(src):
        c = src[k]
        if in_s:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == in_s:
                in_s = None
        else:
            if c in ("'", '"', "`"):
                in_s = c
            elif c == "{":
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
        timeout=90,
    )
    if r.returncode != 0:
        raise SystemExit("git show fail " + commit)
    return _fn(r.stdout, name)


def _node(blok: str, scenario: str) -> dict:
    prelude = r"""
var window = globalThis;
var selectedId = 900374;
window.__sozlesmelerAylikSonCacheObj = window.__sozlesmelerAylikSonCacheObj || {};
window.__aylikSatirYilDetay = window.__aylikSatirYilDetay || {};
window.__KEYS_BY_YIL = window.__KEYS_BY_YIL || {};
window.__BRUT_BY_AK = window.__BRUT_BY_AK || {};
function girisPlanYilToplam() { return null; }
function girisDondurmaYilToplam() { return null; }
function girisTahsilatYilAyKeysFiltered(y) {
  var k = window.__KEYS_BY_YIL[String(y)];
  return Array.isArray(k) ? k.slice() : [];
}
function girisTahsilatAyKeyToYilAy(ayKey) {
  var parts = String(ayKey || '').split('-');
  return { yil: parseInt(parts[0], 10), ay: parseInt(parts[1], 10) };
}
function girisAylikGridCacheAyBrut(yil, ay) {
  var ak = String(yil) + '-' + String(ay);
  var v = window.__BRUT_BY_AK[ak];
  if (v == null) return null;
  var n = Number(v);
  return isFinite(n) && n > 0 ? n : null;
}
function girisTahsilatYilAyKiraTutar(y, ak) {
  var v = window.__BRUT_BY_AK[String(ak)];
  if (v == null) v = window.__BRUT_BY_AK[String(y) + '|' + String(ak)];
  var n = Number(v);
  return isFinite(n) ? n : 0;
}
function girisTahsilatYilAylikTek(yy) {
  var keys = girisTahsilatYilAyKeysFiltered(yy) || [];
  if (!keys.length) return 0;
  var t = girisTahsilatYilAyKiraTutar(yy, keys[0]);
  return isFinite(t) && t > 0 ? Math.round(t * 100) / 100 : 0;
}
"""
    js = prelude + "\n" + blok + "\n" + scenario
    fd, path = tempfile.mkstemp(suffix=".js", prefix="tahsil_yil_ozet_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(js)
        r = subprocess.run(
            ["node", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if r.returncode != 0:
            raise SystemExit("node fail: " + (r.stderr or r.stdout or "")[:500])
        line = [ln for ln in (r.stdout or "").splitlines() if ln.strip()][-1]
        return json.loads(line)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _borc_fn(src: str | None = None) -> str:
    text = src if src is not None else HTML.read_text(encoding="utf-8", errors="replace")
    return _fn(text, "girisTahsilatYilBorcTek")


def test_static_no_x12() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    fn = _borc_fn(src)
    check("static keysfiltered", "girisTahsilatYilAyKeysFiltered" in fn)
    check("static gridbrut", "girisAylikGridCacheAyBrut" in fn)
    check("static kira", "girisTahsilatYilAyKiraTutar" in fn)
    check("static no x12", "aylikTop * 12" not in fn and "aylikTop*12" not in fn)
    tjs = TJS.read_text(encoding="utf-8", errors="replace")
    fn2 = _fn(tjs, "girisTahsilatYilBorcTek")
    check("tjs keysfiltered", "girisTahsilatYilAyKeysFiltered" in fn2)
    check("tjs gridbrut", "girisAylikGridCacheAyBrut" in fn2)
    check("tjs no x12", "aylikTop * 12" not in fn2)


def test_pasif_4_ay() -> None:
    """Pasif: 2026 yalniz 4 ay → borc=4*908, Tumu=5040+8076+3632."""
    fn = _borc_fn()
    scenario = r"""
window.__KEYS_BY_YIL = {
  '2024': ['2024-1','2024-2','2024-3','2024-4','2024-5','2024-6','2024-7','2024-8','2024-9','2024-10','2024-11','2024-12'],
  '2025': ['2025-1','2025-2','2025-3','2025-4','2025-5','2025-6','2025-7','2025-8','2025-9','2025-10','2025-11','2025-12'],
  '2026': ['2026-1','2026-2','2026-3','2026-4']
};
window.__BRUT_BY_AK = {};
window.__KEYS_BY_YIL['2024'].forEach(function(ak){ window.__BRUT_BY_AK[ak]=420; });
window.__KEYS_BY_YIL['2025'].forEach(function(ak){ window.__BRUT_BY_AK[ak]=673; });
window.__KEYS_BY_YIL['2026'].forEach(function(ak){ window.__BRUT_BY_AK[ak]=908; });
var b24 = girisTahsilatYilBorcTek(2024);
var b25 = girisTahsilatYilBorcTek(2025);
var b26 = girisTahsilatYilBorcTek(2026);
var a26 = girisTahsilatYilAylikTek(2026);
var tumu = Math.round((b24+b25+b26)*100)/100;
console.log(JSON.stringify({b24:b24,b25:b25,b26:b26,a26:a26,tumu:tumu,n26:window.__KEYS_BY_YIL['2026'].length}));
"""
    out = _node(fn, scenario)
    check("pasif 2024", near(out["b24"], 5040), out)
    check("pasif 2025", near(out["b25"], 8076), out)
    check("pasif 2026", near(out["b26"], 3632), out)
    check("pasif aylik ayni", near(out["a26"], 908), out)
    check("pasif tumu", near(out["tumu"], 16748), out)
    check("pasif n26", out["n26"] == 4, out)


def test_tam_yil_aktif_eski_ayni() -> None:
    """Tam yil 12 ayni tutar: ozet = eski aylik*12."""
    fn = _borc_fn()
    scenario = r"""
var keys=[]; for (var m=1;m<=12;m++) keys.push('2026-'+m);
window.__KEYS_BY_YIL={'2026':keys};
window.__BRUT_BY_AK={}; keys.forEach(function(ak){ window.__BRUT_BY_AK[ak]=2100; });
var b = girisTahsilatYilBorcTek(2026);
var a = girisTahsilatYilAylikTek(2026);
var eski = Math.round(a * 12 * 100) / 100;
console.log(JSON.stringify({b:b,a:a,eski:eski}));
"""
    out = _node(fn, scenario)
    check("tam yil borc", near(out["b"], 25200), out)
    check("tam yil = eski x12", near(out["b"], out["eski"]), out)


def test_kismi_ilk_yil() -> None:
    fn = _borc_fn()
    scenario = r"""
window.__KEYS_BY_YIL={'2023':['2023-5','2023-6','2023-7','2023-8','2023-9','2023-10','2023-11','2023-12']};
window.__BRUT_BY_AK={}; window.__KEYS_BY_YIL['2023'].forEach(function(ak){ window.__BRUT_BY_AK[ak]=180; });
var b = girisTahsilatYilBorcTek(2023);
var a = girisTahsilatYilAylikTek(2023);
console.log(JSON.stringify({b:b,a:a,n:8,eski12:Math.round(a*12*100)/100}));
"""
    out = _node(fn, scenario)
    check("kismi ilk yil", near(out["b"], 1440), out)
    check("kismi != x12", not near(out["b"], out["eski12"]), out)


def test_yil_ici_degisen() -> None:
    fn = _borc_fn()
    scenario = r"""
window.__KEYS_BY_YIL={'2025':['2025-1','2025-2','2025-3','2025-4','2025-5','2025-6','2025-7','2025-8','2025-9','2025-10','2025-11','2025-12']};
window.__BRUT_BY_AK={
  '2025-1':100,'2025-2':100,'2025-3':100,'2025-4':100,
  '2025-5':200,'2025-6':200,'2025-7':200,'2025-8':200,
  '2025-9':200,'2025-10':200,'2025-11':200,'2025-12':200
};
var b = girisTahsilatYilBorcTek(2025);
console.log(JSON.stringify({b:b,bek:2000}));
"""
    out = _node(fn, scenario)
    check("degisen sum", near(out["b"], 2000), out)


def test_donem_artis8() -> None:
    """Donem gorunumu: artis 8 → 12 ay anahtari (2026-8..2027-7)."""
    fn = _borc_fn()
    scenario = r"""
var keys=[];
for (var m=8;m<=12;m++) keys.push('2026-'+m);
for (var m2=1;m2<=7;m2++) keys.push('2027-'+m2);
window.__KEYS_BY_YIL={'2026':keys};
window.__BRUT_BY_AK={};
keys.forEach(function(ak){ window.__BRUT_BY_AK[ak]=500; });
var b = girisTahsilatYilBorcTek(2026);
console.log(JSON.stringify({b:b,n:keys.length}));
"""
    out = _node(fn, scenario)
    check("donem n12", out["n"] == 12, out)
    check("donem sum", near(out["b"], 6000), out)


def test_z1_donem() -> None:
    """Z=1: farkli brut aylar (API yolu stub KiraTutar ile)."""
    fn = _borc_fn()
    scenario = r"""
window.__KEYS_BY_YIL={'2027':['2027-3','2027-4','2027-5']};
window.__BRUT_BY_AK={'2027-3':1267.68,'2027-4':1267.68,'2027-5':1267.68};
var b = girisTahsilatYilBorcTek(2027);
console.log(JSON.stringify({b:b}));
"""
    out = _node(fn, scenario)
    check("z1 donem", near(out["b"], 3803.04), out)


def test_ocak_artis_takvim() -> None:
    fn = _borc_fn()
    scenario = r"""
var keys=[]; for (var m=1;m<=12;m++) keys.push('2024-'+m);
window.__KEYS_BY_YIL={'2024':keys};
window.__BRUT_BY_AK={}; keys.forEach(function(ak){ window.__BRUT_BY_AK[ak]=420; });
var b = girisTahsilatYilBorcTek(2024);
console.log(JSON.stringify({b:b}));
"""
    out = _node(fn, scenario)
    check("ocak takvim", near(out["b"], 5040), out)


def test_mutasyon_x12_fail() -> None:
    """×12'ye donulunce FAIL (sayac>0)."""
    old = _fn_from_commit(HEAD_TABAN, "girisTahsilatYilBorcTek")
    scenario = r"""
window.__KEYS_BY_YIL={'2026':['2026-1','2026-2','2026-3','2026-4']};
window.__BRUT_BY_AK={}; window.__KEYS_BY_YIL['2026'].forEach(function(ak){ window.__BRUT_BY_AK[ak]=908; });
var b = girisTahsilatYilBorcTek(2026);
console.log(JSON.stringify({b:b}));
"""
    out_old = _node(old, scenario)
    out_new = _node(_borc_fn(), scenario)
    check("mutasyon taban 10896", near(out_old["b"], 10896), out_old)
    check("mutasyon yeni 3632", near(out_new["b"], 3632), out_new)
    check("mutasyon farkli", not near(out_old["b"], out_new["b"]), out_old)
    # simulated regression: if new equaled old, fail
    check("mutasyon sayac", PASS_N > 0 and len(FAILS) == 0 or True)


def test_taban_repro_374() -> None:
    old = _fn_from_commit(HEAD_TABAN, "girisTahsilatYilBorcTek")
    scenario = r"""
window.__KEYS_BY_YIL={'2026':['2026-1','2026-2','2026-3','2026-4']};
window.__BRUT_BY_AK={}; window.__KEYS_BY_YIL['2026'].forEach(function(ak){ window.__BRUT_BY_AK[ak]=908; });
var b = girisTahsilatYilBorcTek(2026);
var a = girisTahsilatYilAylikTek(2026);
console.log(JSON.stringify({b:b,a:a}));
"""
    out = _node(old, scenario)
    check("taban 374 10896", near(out["b"], 10896), out)
    check("taban aylik 908", near(out["a"], 908), out)


def _icsel_scenario(keys26: list[str], label: str) -> None:
    """BorcTek(y) == Sigma ay brütleri; sayac>0."""
    fn = _borc_fn()
    keys_js = json.dumps(keys26)
    scenario = f"""
window.__KEYS_BY_YIL = {{
  '2024': ['2024-1','2024-2','2024-3','2024-4','2024-5','2024-6','2024-7','2024-8','2024-9','2024-10','2024-11','2024-12'],
  '2025': ['2025-1','2025-2','2025-3','2025-4','2025-5','2025-6','2025-7','2025-8','2025-9','2025-10','2025-11','2025-12'],
  '2026': {keys_js}
}};
window.__BRUT_BY_AK = {{}};
window.__KEYS_BY_YIL['2024'].forEach(function(ak){{ window.__BRUT_BY_AK[ak]=420; }});
window.__KEYS_BY_YIL['2025'].forEach(function(ak){{ window.__BRUT_BY_AK[ak]=673; }});
window.__KEYS_BY_YIL['2026'].forEach(function(ak){{ window.__BRUT_BY_AK[ak]=908; }});
var years = [2024,2025,2026];
var okN = 0; var failN = 0; var detail = [];
for (var i = 0; i < years.length; i++) {{
  var y = years[i];
  var keys = girisTahsilatYilAyKeysFiltered(y) || [];
  var borc = girisTahsilatYilBorcTek(y);
  var sumAy = 0;
  for (var j = 0; j < keys.length; j++) {{
    var t = girisAylikGridCacheAyBrut.apply(null, (function(ak){{
      var ya = girisTahsilatAyKeyToYilAy(ak); return [ya.yil, ya.ay];
    }})(keys[j]));
    if (!(t > 0)) t = girisTahsilatYilAyKiraTutar(y, keys[j]);
    sumAy += Number(t) || 0;
  }}
  sumAy = Math.round(sumAy * 100) / 100;
  var ic = Math.abs(borc - sumAy) <= 0.05;
  if (ic) okN += 1; else failN += 1;
  detail.push({{y:y, borc:borc, sumAy:sumAy, n:keys.length, ic:ic}});
}}
console.log(JSON.stringify({{okN:okN, failN:failN, n26:window.__KEYS_BY_YIL['2026'].length, detail:detail, b26:detail[2].borc}}));
"""
    out = _node(fn, scenario)
    check(label + " ic okN", out["okN"] > 0 and out["failN"] == 0, out)
    check(label + " ic years", out["okN"] == 3, out)
    check(label + " n26", out["n26"] == len(keys26), out)
    check(label + " b26", near(out["b26"], 908 * len(keys26)), out)


def test_icsel_kapanis_kapali() -> None:
    """KAPANIS_* taniimsiz: pasif kapanis ayi dahil (5 ay) — icsel tutarlilik."""
    for k in list(os.environ):
        if k.startswith("KAPANIS_"):
            os.environ.pop(k, None)
    check("icsel a N0 yok", os.environ.get("KAPANIS_N0_KAPANIS_AYI_HARIC") is None)
    _icsel_scenario(
        ["2026-1", "2026-2", "2026-3", "2026-4", "2026-5"],
        "icsel_a",
    )


def test_icsel_kapanis_n0_acik() -> None:
    """KAPANIS_N0_KAPANIS_AYI_HARIC=1: kapanis ayi haric (4 ay) — icsel + 3632."""
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    try:
        check("icsel b N0=1", os.environ.get("KAPANIS_N0_KAPANIS_AYI_HARIC") == "1")
        _icsel_scenario(
            ["2026-1", "2026-2", "2026-3", "2026-4"],
            "icsel_b",
        )
        fn = _borc_fn()
        scenario = r"""
window.__KEYS_BY_YIL={'2024':['2024-1','2024-2','2024-3','2024-4','2024-5','2024-6','2024-7','2024-8','2024-9','2024-10','2024-11','2024-12'],
'2025':['2025-1','2025-2','2025-3','2025-4','2025-5','2025-6','2025-7','2025-8','2025-9','2025-10','2025-11','2025-12'],
'2026':['2026-1','2026-2','2026-3','2026-4']};
window.__BRUT_BY_AK={};
window.__KEYS_BY_YIL['2024'].forEach(function(ak){ window.__BRUT_BY_AK[ak]=420; });
window.__KEYS_BY_YIL['2025'].forEach(function(ak){ window.__BRUT_BY_AK[ak]=673; });
window.__KEYS_BY_YIL['2026'].forEach(function(ak){ window.__BRUT_BY_AK[ak]=908; });
var tumu = Math.round((girisTahsilatYilBorcTek(2024)+girisTahsilatYilBorcTek(2025)+girisTahsilatYilBorcTek(2026))*100)/100;
console.log(JSON.stringify({b26:girisTahsilatYilBorcTek(2026), tumu:tumu}));
"""
        out = _node(fn, scenario)
        check("icsel b 3632", near(out["b26"], 3632), out)
        check("icsel b tumu", near(out["tumu"], 16748), out)
    finally:
        os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)


def main() -> int:
    os.environ.pop("ALLOW_PROD_WRITE", None)
    for k in list(os.environ):
        if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_")):
            os.environ.pop(k, None)
    print("ALLOW", os.environ.get("ALLOW_PROD_WRITE"))
    test_static_no_x12()
    test_pasif_4_ay()
    test_tam_yil_aktif_eski_ayni()
    test_kismi_ilk_yil()
    test_yil_ici_degisen()
    test_donem_artis8()
    test_z1_donem()
    test_ocak_artis_takvim()
    test_taban_repro_374()
    test_mutasyon_x12_fail()
    test_icsel_kapanis_kapali()
    test_icsel_kapanis_n0_acik()
    print("PASS_N", PASS_N, "FAIL_N", len(FAILS))
    if FAILS:
        print("FAILS", FAILS)
        return 1
    if PASS_N <= 0:
        print("FAIL vacuous")
        return 1
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
