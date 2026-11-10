# -*- coding: utf-8 -*-
"""TAHSILAT_DONEM_GORUNUM bayragi: FE KeysFiltered + backend ayrıştırma.

Varsayılan KAPALI → takvim birebir. Açık + artis≠1 → sozlesmeReelDonemAyKeys.
Z=1 yolu aynı. Prod yazmaz; PII yok. test_panel_grid_onarim ÇALIŞTIRILMAZ.
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
PASS_N = 0
SYN_MID = 900867
HEAD_TABAN = "e088b38"


def check(name: str, ok: bool, ek="") -> None:
    global PASS_N
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if ok:
        PASS_N += 1
    else:
        FAILS.append(name)


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


FE = [
    "girisReelTufeZincirStickyBag",
    "girisReelTufeZincirStickyMidCoz",
    "girisReelTufeZincirCacheStickyUygula",
    "girisReelTufeZincirAktifMi",
    "girisReelTufeZincirApiZ",
    "sozlesmelerReelDonemAnchor",
    "sozlesmeReelDonemAyKeys",
    "girisTahsilatDonemGorunumAktifMi",
    "girisTahsilatArtisAyNum",
    "girisTahsilatYilAyKeysFiltered",
    "girisTahsilatAyKeyToYilAy",
    "girisTahsilatYilAyIsoOneri",
]


def _extract(src: str, names: list[str]) -> str:
    parts = []
    for n in names:
        try:
            parts.append(_fn(src, n))
        except SystemExit:
            if n.startswith("girisReelTufe") or n.startswith("girisTahsilatDonem") or n.startswith("girisTahsilatArtis"):
                continue
            raise
    return "\n".join(parts)


def _prelude(bas: str = "2024-08-15", z: int = 0, flag: int = 0, artis_ay: int = 8) -> str:
    return f"""
var SOZLESME_TAM_ODENDI_TOLERANS = 0.05;
var selectedId = {SYN_MID};
const els = {{}};
function _el(id, val) {{
  els[id] = {{ id: id, value: val == null ? '' : String(val), checked: false, style: {{}},
    getAttribute: function(){{return null;}}, setAttribute: function(){{}},
    querySelector: function(){{return null;}}, querySelectorAll: function(){{return [];}} }};
  return els[id];
}}
global.document = {{ getElementById: function(id){{ return els[id]||null; }}, addEventListener: function(){{}} }};
global.window = global;
global.localStorage = {{ getItem: function(){{return null;}}, setItem: function(){{}}, removeItem: function(){{}} }};
function parseTarihStr(s) {{
  var m = String(s||'').match(/(\\d{{4}})-(\\d{{1,2}})-(\\d{{1,2}})/);
  return m ? new Date(+m[1], +m[2]-1, +m[3]) : null;
}}
function sozlesmeAylikAyKeyFromYilAy(y, a) {{ return y + '-' + a; }}
function girisPanelAyKeysCapFiltre(keys) {{ return keys; }}
_el('sozlesme_baslangic', '{bas}');
_el('sozlesme_bitis', '');
_el('kira_artis_tarihi', '{bas}');
window.__sozlesmelerAylikSonCacheObj = {{
  musteri_id: {SYN_MID},
  reel_tufe_zincir: {z},
  tahsilat_donem_gorunum: {flag},
  artis_ay: {artis_ay},
  baslangic: '{bas}',
  aylar: (function(){{
    var out = [];
    for (var y = 2025; y <= 2027; y++) {{
      for (var m = 1; m <= 12; m++) {{
        out.push({{ yil:y, ay:m, ay_key: y+'-'+m, brut_tutar_kdv: 100, tutar_kdv_dahil: 100 }});
      }}
    }}
    return out;
  }})()
}};
"""


def _node(blok: str, scenario: str, **kw) -> dict:
    harness = _prelude(**kw) + "\n" + blok + "\n" + scenario + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(harness)
        path = f.name
    try:
        r = subprocess.run(
            ["node", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if r.returncode != 0:
            raise SystemExit("node fail: " + (r.stderr or r.stdout or "")[:400])
        line = [ln for ln in (r.stdout or "").splitlines() if ln.strip()][-1]
        return json.loads(line)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _env_clear_flags() -> dict:
    kept = {}
    for k in ("TAHSILAT_DONEM_GORUNUM", "TAHSILAT_DONEM_GORUNUM_MIDLER", "REEL_TUFE_ZINCIR", "REEL_TUFE_ZINCIR_MIDLER"):
        kept[k] = os.environ.get(k)
        os.environ.pop(k, None)
    return kept


def _env_restore(kept: dict) -> None:
    for k, v in kept.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_backend_midler() -> None:
    sys.path.insert(0, str(ROOT))
    from routes import giris_routes as gr

    kept = _env_clear_flags()
    try:
        cases = [
            ("867", {867}),
            ("867,167", {867, 167}),
            ("867, 167", {867}),
            ("abc,867", {867}),
            ("867;167", set()),
            ("", set()),
            ("-867", set()),
            ("86.7", set()),
            ("+867", set()),
            ("867a", set()),
        ]
        for raw, bek in cases:
            if raw == "":
                os.environ.pop("TAHSILAT_DONEM_GORUNUM_MIDLER", None)
                check("midler None→bos", gr.tahsilat_donem_gorunum_midler() == set())
                os.environ["TAHSILAT_DONEM_GORUNUM_MIDLER"] = ""
                check("midler empty→bos", gr.tahsilat_donem_gorunum_midler() == set())
            else:
                os.environ["TAHSILAT_DONEM_GORUNUM_MIDLER"] = raw
                got = gr.tahsilat_donem_gorunum_midler()
                check(f"midler {raw!r}", got == bek, got)
        os.environ["TAHSILAT_DONEM_GORUNUM_MIDLER"] = ",".join(str(i) for i in range(1, 502))
        got501 = gr.tahsilat_donem_gorunum_midler()
        check("midler 501→500", len(got501) == 500, len(got501))
        check("midler 501 no 501", 501 not in got501)
        # geçersiz → kapalı
        os.environ.pop("TAHSILAT_DONEM_GORUNUM", None)
        os.environ["TAHSILAT_DONEM_GORUNUM_MIDLER"] = "xx"
        check("gecersiz midler kapali", gr.tahsilat_donem_gorunum_acik(867) is False)
        os.environ["TAHSILAT_DONEM_GORUNUM"] = "1"
        check("global strip 1", gr.tahsilat_donem_gorunum_acik(1) is True)
        os.environ["TAHSILAT_DONEM_GORUNUM"] = " 1 "
        check("global strip space", gr.tahsilat_donem_gorunum_acik(2) is True)
        os.environ["TAHSILAT_DONEM_GORUNUM"] = "true"
        check("global true kapali", gr.tahsilat_donem_gorunum_acik(3) is False)
        os.environ["TAHSILAT_DONEM_GORUNUM"] = "0"
        os.environ["TAHSILAT_DONEM_GORUNUM_MIDLER"] = "867"
        check("midler list acik", gr.tahsilat_donem_gorunum_acik(867) is True)
        check("midler list diger kapali", gr.tahsilat_donem_gorunum_acik(168) is False)
    finally:
        _env_restore(kept)


def test_bayraklar_bagimsiz() -> None:
    sys.path.insert(0, str(ROOT))
    from routes import giris_routes as gr

    kept = _env_clear_flags()
    try:
        os.environ["REEL_TUFE_ZINCIR"] = "1"
        os.environ.pop("TAHSILAT_DONEM_GORUNUM", None)
        os.environ.pop("TAHSILAT_DONEM_GORUNUM_MIDLER", None)
        check("Z acik donem kapali", gr.reel_tufe_zincir_acik(99) is True and gr.tahsilat_donem_gorunum_acik(99) is False)
        os.environ.pop("REEL_TUFE_ZINCIR", None)
        os.environ["REEL_TUFE_ZINCIR_MIDLER"] = "99"
        os.environ["TAHSILAT_DONEM_GORUNUM"] = "1"
        check("donem acik Z midler ayri", gr.tahsilat_donem_gorunum_acik(1) is True and gr.reel_tufe_zincir_acik(1) is False)
        os.environ.pop("TAHSILAT_DONEM_GORUNUM", None)
        os.environ["TAHSILAT_DONEM_GORUNUM_MIDLER"] = "55"
        os.environ["REEL_TUFE_ZINCIR_MIDLER"] = "66"
        check("midler bagimsiz 55", gr.tahsilat_donem_gorunum_acik(55) is True and gr.reel_tufe_zincir_acik(55) is False)
        check("midler bagimsiz 66", gr.reel_tufe_zincir_acik(66) is True and gr.tahsilat_donem_gorunum_acik(66) is False)
    finally:
        _env_restore(kept)


def test_fe_flag_kapali_takvim() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE)
    scenario = r"""
var k8 = girisTahsilatYilAyKeysFiltered(2026);
window.__sozlesmelerAylikSonCacheObj.artis_ay = 1;
window.__sozlesmelerAylikSonCacheObj.tahsilat_donem_gorunum = 0;
_el('sozlesme_baslangic', '2024-01-10');
_el('kira_artis_tarihi', '2024-01-10');
window.__sozlesmelerAylikSonCacheObj.baslangic = '2024-01-10';
var k1 = girisTahsilatYilAyKeysFiltered(2026);
console.log(JSON.stringify({k8:k8, k1:k1, n8:k8.length, n1:k1.length, f8:k8[0], l8:k8[k8.length-1], f1:k1[0], l1:k1[k1.length-1]}));
"""
    out = _node(blok, scenario, z=0, flag=0, artis_ay=8, bas="2024-08-15")
    check("kapali artis8 takvim Jan", str(out.get("f8") or "").startswith("2026-1"), out)
    check("kapali artis8 takvim Dec", str(out.get("l8") or "").startswith("2026-12"), out)
    check("kapali artis8 n=12", out.get("n8") == 12, out)
    check("kapali artis1 takvim Jan", str(out.get("f1") or "").startswith("2026-1"), out)
    check("kapali artis1 takvim Dec", str(out.get("l1") or "").startswith("2026-12"), out)


def test_fe_flag_acik_donem() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE)
    scenario = r"""
var k = girisTahsilatYilAyKeysFiltered(2026);
console.log(JSON.stringify({k:k,n:k.length,f:k[0],l:k[k.length-1]}));
"""
    out = _node(blok, scenario, z=0, flag=1, artis_ay=8, bas="2024-08-15")
    check("acik artis8 Aug", out.get("f") in ("2026-8", "2026-08"), out)
    check("acik artis8 Jul27", out.get("l") in ("2027-7", "2027-07"), out)
    check("acik artis8 n=12", out.get("n") == 12, out)

    out1 = _node(blok, scenario, z=0, flag=1, artis_ay=1, bas="2024-01-10")
    check("acik artis1 takvim f", str(out1.get("f") or "").startswith("2026-1"), out1)
    check("acik artis1 takvim l", str(out1.get("l") or "").startswith("2026-12"), out1)


def test_fe_z1_ayni() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE)
    scenario = r"""
var k = girisTahsilatYilAyKeysFiltered(2026);
console.log(JSON.stringify({k:k,n:k.length,f:k[0],l:k[k.length-1]}));
"""
    z1_off = _node(blok, scenario, z=1, flag=0, artis_ay=8, bas="2024-08-15")
    z1_on = _node(blok, scenario, z=1, flag=1, artis_ay=8, bas="2024-08-15")
    check("Z1 flag off Aug", z1_off.get("f") in ("2026-8", "2026-08"), z1_off)
    check("Z1 flag on==off", z1_off.get("k") == z1_on.get("k"), {"off": z1_off.get("k"), "on": z1_on.get("k")})


def test_donem_baslangic_yili_etiket() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE)
    scenario = r"""
var k = girisTahsilatYilAyKeysFiltered(2026);
var yillar = [];
(k||[]).forEach(function(ak){
  var ya = girisTahsilatAyKeyToYilAy(ak);
  if (yillar.indexOf(ya.yil)<0) yillar.push(ya.yil);
});
console.log(JSON.stringify({f:k[0], yillar:yillar, label:2026, firstY: parseInt(String(k[0]).split('-')[0],10)}));
"""
    out = _node(blok, scenario, z=0, flag=1, artis_ay=8, bas="2024-08-15")
    check("etiket donem bas yil 2026", out.get("firstY") == 2026 and out.get("label") == 2026, out)
    check("etiket span 2026+2027", out.get("yillar") == [2026, 2027], out)


def test_marker_kaydet_iso() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE)
    scenario = r"""
var k = girisTahsilatYilAyKeysFiltered(2026);
var rows = (k||[]).map(function(ak){
  var iso = girisTahsilatYilAyIsoOneri(ak);
  var ya = girisTahsilatAyKeyToYilAy(ak);
  var isoAy = iso.slice(0,7);
  var keyAy = ya.yil + '-' + String(ya.ay).padStart(2,'0');
  return {ak:ak, iso:iso, eq: isoAy===keyAy};
});
var allEq = rows.every(function(r){ return r.eq; });
console.log(JSON.stringify({n:rows.length, allEq:allEq, sample:rows.slice(0,2)}));
"""
    out = _node(blok, scenario, z=0, flag=1, artis_ay=8, bas="2024-08-15")
    check("marker iso n>0", (out.get("n") or 0) > 0, out)
    check("Kaydet ay-ISO eq", out.get("allEq") is True, out)


def test_cift_gosterim_yok() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE)
    scenario = r"""
var k25 = girisTahsilatYilAyKeysFiltered(2025);
var k26 = girisTahsilatYilAyKeysFiltered(2026);
var set = {};
var cift = [];
[k25,k26].forEach(function(arr){
  (arr||[]).forEach(function(ak){
    if (set[ak]) cift.push(ak); else set[ak]=1;
  });
});
console.log(JSON.stringify({n25:k25.length,n26:k26.length,cift:cift,cift_n:cift.length}));
"""
    out = _node(blok, scenario, z=0, flag=1, artis_ay=8, bas="2024-08-15")
    check("cift yok", out.get("cift_n") == 0, out)
    check("iki donem 12+12", out.get("n25") == 12 and out.get("n26") == 12, out)


def test_taban_reprod_eski_takvim() -> None:
    """Bayrak açıkken eski KeysFiltered (HEAD) Z=0 artis8 → takvim; yeni → Aug-Jul."""
    src = HTML.read_text(encoding="utf-8", errors="replace")
    deps_cur = [
        "girisReelTufeZincirStickyBag",
        "girisReelTufeZincirStickyMidCoz",
        "girisReelTufeZincirCacheStickyUygula",
        "girisReelTufeZincirAktifMi",
        "sozlesmelerReelDonemAnchor",
        "sozlesmeReelDonemAyKeys",
        "girisTahsilatDonemGorunumAktifMi",
        "girisTahsilatArtisAyNum",
        "girisTahsilatYilAyKeysFiltered",
    ]
    blok_cur = _extract(src, deps_cur)
    head_fn = _fn_from_commit(HEAD_TABAN, "girisTahsilatYilAyKeysFiltered")
    head_deps = _extract(
        subprocess.run(
            ["git", "show", f"{HEAD_TABAN}:erp_web/templates/giris/index.html"],
            cwd=str(ROOT.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        ).stdout,
        [
            "girisReelTufeZincirStickyBag",
            "girisReelTufeZincirStickyMidCoz",
            "girisReelTufeZincirCacheStickyUygula",
            "girisReelTufeZincirAktifMi",
            "sozlesmelerReelDonemAnchor",
            "sozlesmeReelDonemAyKeys",
        ],
    )
    scenario = r"""
var k = girisTahsilatYilAyKeysFiltered(2026);
console.log(JSON.stringify({f:k[0],l:k[k.length-1],n:k.length,k:k}));
"""
    out_old = _node(head_deps + "\n" + head_fn, scenario, z=0, flag=1, artis_ay=8, bas="2024-08-15")
    out_new = _node(blok_cur, scenario, z=0, flag=1, artis_ay=8, bas="2024-08-15")
    check("taban eski takvim f", str(out_old.get("f") or "").startswith("2026-1"), out_old)
    check("taban eski takvim l", str(out_old.get("l") or "").startswith("2026-12"), out_old)
    check("taban yeni Aug", out_new.get("f") in ("2026-8", "2026-08"), out_new)
    check("taban yeni Jul", out_new.get("l") in ("2027-7", "2027-07"), out_new)
    check("taban farkli", out_old.get("k") != out_new.get("k"))


def test_mutasyon() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    blok = _extract(src, FE)
    # Kapı kaldır: donemGorunumKeys hep false gibi — flag açikken takvim (FAIL beklenen mutasyon)
    mut1 = blok.replace(
        "if (!z1Keys && (typeof girisTahsilatDonemGorunumAktifMi === 'function') && girisTahsilatDonemGorunumAktifMi(sco))",
        "if (false && !z1Keys && (typeof girisTahsilatDonemGorunumAktifMi === 'function') && girisTahsilatDonemGorunumAktifMi(sco))",
        1,
    )
    scenario = r"""
var k = girisTahsilatYilAyKeysFiltered(2026);
console.log(JSON.stringify({f:k[0],l:k[k.length-1]}));
"""
    out_m1 = _node(mut1, scenario, z=0, flag=1, artis_ay=8, bas="2024-08-15")
    check("mutasyon kapi kaldir → takvim", str(out_m1.get("f") or "").startswith("2026-1"), out_m1)

    mut2 = blok.replace(
        "keys = (typeof sozlesmeReelDonemAyKeys === 'function') ? (sozlesmeReelDonemAyKeys(basStrZ, donemYil) || []) : [];",
        "keys = ['MUT-BOZUK'];",
        1,
    )
    out_m2 = _node(mut2, scenario, z=0, flag=1, artis_ay=8, bas="2024-08-15")
    check("mutasyon donem fn bozulunca FAIL tetik", out_m2.get("f") == "MUT-BOZUK", out_m2)
    # sağlıklı yol hâlâ Aug
    out_ok = _node(blok, scenario, z=0, flag=1, artis_ay=8, bas="2024-08-15")
    check("mutasyon kontrol saglikli Aug", out_ok.get("f") in ("2026-8", "2026-08"), out_ok)


def test_payload_boolean() -> None:
    sys.path.insert(0, str(ROOT))
    from routes import giris_routes as gr

    kept = _env_clear_flags()
    try:
        os.environ["TAHSILAT_DONEM_GORUNUM_MIDLER"] = "867"
        p = {"aylar": []}
        gr._aylik_grid_payload_tahsilat_donem_stamp(867, p)
        check("payload true bool", p.get("tahsilat_donem_gorunum") is True, p)
        gr._aylik_grid_payload_tahsilat_donem_stamp(168, p)
        check("payload false bool", p.get("tahsilat_donem_gorunum") is False, p)
    finally:
        _env_restore(kept)


def main() -> int:
    os.environ.pop("ALLOW_PROD_WRITE", None)
    for k in list(os.environ):
        if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_")) or k.startswith("TAHSILAT_DONEM"):
            # testler kendi set eder; ortam temiz
            if k.startswith("TAHSILAT_DONEM") or k.startswith("REEL_TUFE_") or k.startswith("KAPANIS_") or k.startswith("ODA_"):
                os.environ.pop(k, None)

    test_backend_midler()
    test_bayraklar_bagimsiz()
    test_payload_boolean()
    test_fe_flag_kapali_takvim()
    test_fe_flag_acik_donem()
    test_fe_z1_ayni()
    test_donem_baslangic_yili_etiket()
    test_marker_kaydet_iso()
    test_cift_gosterim_yok()
    test_taban_reprod_eski_takvim()
    test_mutasyon()

    print("---")
    print(f"PASS_N={PASS_N} FAIL_N={len(FAILS)}")
    if FAILS:
        print("FAILS:", ", ".join(FAILS))
        return 1
    if PASS_N <= 0:
        print("FAIL sayac vacuous")
        return 1
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
