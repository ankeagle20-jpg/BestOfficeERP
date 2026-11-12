# -*- coding: utf-8 -*-
"""Tümü Alacak/Kalan = ay-ay etkin toplam — temiz ortam birim test.

Prod yazma yok. KAPANIS_*/ODA_*/REEL_TUFE_*/TAHSILAT_* tanimsiz.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_")):
        os.environ.pop(k, None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _extract(src: str, name: str) -> str:
    m = re.search(r"function " + re.escape(name) + r"\([^)]*\)\s*\{", src)
    if not m:
        raise RuntimeError("missing " + name)
    i = m.start()
    depth = 0
    j = m.end() - 1
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i : j + 1]
        j += 1
    raise RuntimeError("unclosed " + name)


def test_kaynak_ayna():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    idx = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    for name in (
        "girisTahsilatAyAyEtkinToplamlar",
        "girisTahsilatOzetGuncelle",
        "girisTahsilatYilPanelDoldur",
    ):
        check(f"tjs_{name}", f"function {name}(" in tjs)
        check(f"idx_{name}", f"function {name}(" in idx)
    check(
        "ozet_ayay_kullanir",
        "girisTahsilatAyAyEtkinToplamlar" in _extract(tjs, "girisTahsilatOzetGuncelle"),
    )
    check(
        "panel_ayay_kullanir",
        "girisTahsilatAyAyEtkinToplamlar" in _extract(tjs, "girisTahsilatYilPanelDoldur"),
    )
    chunk = _extract(tjs, "girisTahsilatAyAyEtkinToplamlar")
    check("literal_bs_n_yok", (chr(92) + "n") not in chunk)


def test_node_ay_ay_vs_net():
    """317 tipi: brut 3480 x12, net API 34800 → eski kalan 6960; yeni kalan 0."""
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    body = _extract(tjs, "girisTahsilatAyAyEtkinToplamlar")
    harness = r"""
var window = {
  __girisMusteriToplamTahsilat: 34800,
  __sozlesmelerAylikSonCacheObj: { aylar: [] },
  __tahsilatSatirYilAyDetay: { '2022': {} }
};
for (var m=0; m<12; m++) {
  var y = (m===0) ? 2022 : 2023;
  var mo = (m===0) ? 12 : m;
  var ak = y + '-' + mo;
  window.__tahsilatSatirYilAyDetay['2022'][ak] = { aylik_tutar: 3480, tahsil: 3480, kalan: 0, __db_saved: true };
  window.__sozlesmelerAylikSonCacheObj.aylar.push({
    yil: y, ay: mo, odenen_tutar_kdv: 3480, tahsil_edildi: true
  });
}
function girisTahsilatYilYilListesi(){ return { years: [2022] }; }
function girisTahsilatYilAyKeysFiltered(yy){
  var ks=[]; for (var m=0;m<12;m++){ var y=(m===0)?2022:2023; var mo=(m===0)?12:m; ks.push(y+'-'+mo);} return ks;
}
function girisTahsilatAyKeyToYilAy(ak){
  var p=String(ak).split('-'); return { yil:+p[0], ay:+p[1] };
}
function girisAylikGridCacheAyBrut(y,a){ return 3480; }
function girisTahsilatYilAyKiraTutar(){ return 3480; }
function girisTahsilatYilAyPanelGridKaynagi(yy, ak){
  return { brut: 3480, kalan: 0, odenen: 3480, tahsil: 3480 };
}
""" + body + r"""
var t = girisTahsilatAyAyEtkinToplamlar([2022]);
var eskiKalan = Math.max(0, 41760 - 34800);
var fails=[];
function assert(n,c){ if(!c) fails.push(n+':'+JSON.stringify(t)); }
assert('borc_41760', Math.abs(t.borc-41760)<0.01);
assert('alacak_41760', Math.abs(t.alacak-41760)<0.01);
assert('kalan_0', Math.abs(t.kalan)<0.01);
assert('eski_6960', Math.abs(eskiKalan-6960)<0.01);
if (fails.length){ console.log('FAIL', fails.join('|')); process.exit(1); }
console.log('tumu_ayay_ok', JSON.stringify(t));
"""
    fd, path = tempfile.mkstemp(suffix=".js", prefix="tumu_ayay_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(harness)
        r = subprocess.run(
            ["node", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        check("node_ay_ay", r.returncode == 0, (r.stderr or r.stdout or "")[:200])
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    # Mutasyon: alacak net kullan → kalan 6960 olur, assert fail etmeli
    mut = harness.replace(
        "assert('kalan_0', Math.abs(t.kalan)<0.01);",
        "assert('kalan_0', Math.abs(t.kalan-6960)<0.01);",
        1,
    )
    # force wrong: make function return net-based
    mut = mut.replace(
        "return {\n        borc: Math.round(sumBorc * 100) / 100,\n        alacak: Math.round(sumAlacak * 100) / 100,\n        kalan: Math.round(sumKalan * 100) / 100\n    };",
        "return { borc: 41760, alacak: 34800, kalan: 6960 };",
        1,
    )
    fd2, path2 = tempfile.mkstemp(suffix=".js", prefix="tumu_mut_")
    try:
        with os.fdopen(fd2, "w", encoding="utf-8", newline="\n") as f:
            f.write(mut)
        r2 = subprocess.run(
            ["node", path2],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        # mutasyon kalan_0 assert'i 6960 bekliyor ama wait - I inverted wrong.
        # Better: mut returns wrong values, original asserts should fail
        pass
    finally:
        try:
            os.unlink(path2)
        except OSError:
            pass

    mut2 = harness.replace(
        "return {\n        borc: Math.round(sumBorc * 100) / 100,\n        alacak: Math.round(sumAlacak * 100) / 100,\n        kalan: Math.round(sumKalan * 100) / 100\n    };",
        "return { borc: 41760, alacak: 34800, kalan: 6960 };",
        1,
    )
    fd3, path3 = tempfile.mkstemp(suffix=".js", prefix="tumu_mut2_")
    try:
        with os.fdopen(fd3, "w", encoding="utf-8", newline="\n") as f:
            f.write(mut2)
        r3 = subprocess.run(
            ["node", path3],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        check("mutasyon_net_fail", r3.returncode != 0, "net sapmasi yakalanmali")
    finally:
        try:
            os.unlink(path3)
        except OSError:
            pass


def main():
    test_kaynak_ayna()
    test_node_ay_ay_vs_net()
    if FAILS:
        print("FAIL", len(FAILS))
        for a in FAILS:
            print(" -", a)
        raise SystemExit(1)
    print("test_tumu_ay_ay_kalan PASS")


if __name__ == "__main__":
    main()
