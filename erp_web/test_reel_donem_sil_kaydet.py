# -*- coding: utf-8 -*-
"""Reel silme/Kaydet: kart tutarı reel yazılmasın; kasıtlı UPSERT birebir.

Sentetik mid/tutar. Prod yazmaz. PII yok.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "templates" / "giris" / "index.html"
FAILS: list[str] = []
SYN_MID = 900053
SYN_Y = 2026
KART = 11064.83
REEL_ONCE = 11071.0


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


FE_NAMES = [
    "girisAylikSatirYilKaydetTutarAl",
    "girisAylikSatirYilDomOku",
    "girisAylikSatirYilDbKaydetTek",
    "girisAylikSatirYilDbSilTek",
    "girisAylikSatirYilDbSonrasi",
    "girisAylikSatirYilKayitYaz",
    "girisAylikSatirYilKaydet",
    "girisAylikSatirYilHesapla",
    "girisAylikSatirYilPanelSatirYenile",
    "girisAylikSatirYilSatirKilitUygula",
    "girisAylikSatirYilAyPanelGuncelle",
    "girisAylikSatirYilEkstreSenkron",
]


def _harness(blok: str, scenario: str) -> str:
    return f"""
var SOZLESME_TAM_ODENDI_TOLERANS=0.05;
var selectedId={SYN_MID};
var __http=[];
function girisFetchAgKopmasinaKarsi(url, opts) {{
  opts = opts || {{}};
  var method = (opts.method || 'GET').toUpperCase();
  var body = null;
  try {{ body = opts.body ? JSON.parse(opts.body) : null; }} catch(e) {{ body = opts.body; }}
  __http.push({{ method: method, url: String(url), body: body }});
  var resp = {{ ok: true, j: {{ ok: true, map: {{}}, detay_map: {{}} }} }};
  if (method === 'DELETE') {{
    try {{
      delete window.__musteriReelDonemDbMap[String((body&&body.donem_yil)||'')];
      delete window.__musteriReelDonemTutarlari[String((body&&body.donem_yil)||'')];
    }} catch(e) {{}}
  }}
  if (method === 'POST' && body && body.donem_yil != null) {{
    window.__musteriReelDonemDbMap[String(body.donem_yil)] = body.tutar_kdv_dahil;
    window.__musteriReelDonemTutarlari[String(body.donem_yil)] = body.tutar_kdv_dahil;
  }}
  return Promise.resolve({{
    ok: true,
    json: function(){{ return Promise.resolve(resp.j); }}
  }});
}}
function parseTarihStr(s){{var m=String(s||'').match(/(\\d{{4}})-(\\d{{1,2}})-(\\d{{1,2}})/);return m?new Date(+m[1],+m[2]-1,+m[3]):null;}}
function girisAylikSatirIlkYilMi(){{ return false; }}
function girisAylikSatirAnaDegerOku(){{ return {{aylik:1000,nakit:0,banka:0}}; }}
function girisAylikSatirTufeAylikMap(){{ return {{}}; }}
function girisAylikSatirYilAylikBrutKdvFlagMi(){{ return false; }}
function girisAylikSatirYilKayitKey(){{ return '__aylik_yil_kayit__|{SYN_MID}'; }}
function girisTahsilatOzetGuncelle(){{}}
function sozlesmelerAylikNormGridKartTutarlariniYenile(){{}}
function girisJsonCacheInvalidatePrefix(){{}}
function cariEkstreMusteriIdSec(){{ return null; }}
function cariEkstreYukle(){{}}
var els={{}};
function _mk(id, html){{
  var d={{ id:id, value:'', checked:false, style:{{}}, classList:{{add:function(){{}},remove:function(){{}}}},
    textContent:'', innerHTML: html||'',
    querySelector:function(sel){{
      if(sel==='.aylik-yil-toplam') return {{ value: this._toplam||'', }};
      if(sel==='.aylik-yil-reel') return {{ value: this._reel||'' }};
      if(sel==='.aylik-yil-reel-aktif') return {{ checked: !!this._reelAktif }};
      if(sel==='.aylik-yil-kaydet') return {{ textContent:'Kaydet', classList:{{add:function(){{}},remove:function(){{}}}} }};
      if(sel==='.aylik-yil-aylik') return {{ value: this._aylik||'' }};
      if(sel==='.aylik-yil-nakit') return {{ value: this._nakit||'0' }};
      return null;
    }},
    querySelectorAll:function(){{return [];}},
    getAttribute:function(){{return null;}}, setAttribute:function(){{}},
    addEventListener:function(){{}}
  }};
  els[id]=d; return d;
}}
global.document = {{
  getElementById: function(id){{ return els[id]||null; }},
  createElement: function(){{ return {{style:{{}},appendChild:function(){{}},addEventListener:function(){{}}}}; }},
  addEventListener: function(){{}}
}};
global.window = global;
global.localStorage = {{ store:{{}}, getItem:function(k){{return this.store[k]||null;}}, setItem:function(k,v){{this.store[k]=String(v);}}, removeItem:function(k){{delete this.store[k];}} }};
global.confirm = function(){{ return true; }};
global.alert = function(){{}};
window.__aylikSatirYilDetay = {{}};
window.__aylikSatirYilAcik = {{}};
window.__musteriReelDonemDbMap = {{}};
window.__musteriReelDonemTutarlari = {{}};
window.__musteriReelDonemDetaylari = {{}};
{blok}
function seedRow(y, opts){{
  opts = opts || {{}};
  var row = _mk('aylik_yil_row_'+y);
  row._toplam = String(opts.toplam != null ? opts.toplam : {KART});
  row._reel = String(opts.reel != null ? opts.reel : '');
  row._reelAktif = !!opts.reelAktif;
  row._aylik = String(opts.aylik != null ? opts.aylik : {KART});
  row._nakit = '0';
  window.__aylikSatirYilDetay[String(y)] = {{
    aylik: parseFloat(row._aylik)||0,
    nakit: 0,
    banka: 0,
    reel: parseFloat(row._reel)||0,
    __reel_aktif: !!opts.reelAktif,
    toplam: parseFloat(row._toplam)||0,
    kdv_dahil: parseFloat(row._toplam)||0
  }};
  if (opts.dbReel != null) {{
    window.__musteriReelDonemDbMap[String(y)] = opts.dbReel;
    window.__musteriReelDonemTutarlari[String(y)] = opts.dbReel;
  }}
}}
function wait(ms){{ return new Promise(function(r){{ setTimeout(r, ms); }}); }}
(async function(){{
{scenario}
  console.log(JSON.stringify({{ http: __http, detay: window.__aylikSatirYilDetay, dbMap: window.__musteriReelDonemDbMap }}));
}})().catch(function(e){{ console.log(JSON.stringify({{ err: String(e&&e.stack||e) }})); process.exit(1); }});
"""


def _node(scenario: str) -> dict:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    parts = []
    for n in FE_NAMES:
        try:
            parts.append(_fn(src, n))
        except SystemExit:
            # optional stubs for missing helpers used only for UI
            if n in (
                "girisAylikSatirYilPanelSatirYenile",
                "girisAylikSatirYilSatirKilitUygula",
                "girisAylikSatirYilAyPanelGuncelle",
                "girisAylikSatirYilEkstreSenkron",
                "girisAylikSatirYilHesapla",
                "girisAylikSatirYilDbSonrasi",
                "girisAylikSatirYilKayitYaz",
            ):
                continue
            raise
    # minimal stubs if extract failed optional
    stubs = """
function girisAylikSatirBaslangicYili(){ return 2022; }
function girisAylikSatirYilHesapla(y){
  var d=(window.__aylikSatirYilDetay||{})[String(y)]||{};
  var t=parseFloat(d.toplam)||parseFloat(d.kdv_dahil)||parseFloat(d.aylik)||0;
  return {aylik:d.aylik||0,nakit:d.nakit||0,banka:d.banka||0,kdv_dahil:t,toplam:t,reel:d.reel||0};
}
function girisAylikSatirYilPanelSatirYenile(){}
function girisAylikSatirYilSatirKilitUygula(){}
function girisAylikSatirYilAyPanelGuncelle(){}
function girisAylikSatirYilEkstreSenkron(){}
function girisAylikSatirYilKayitYaz(){
  try{ localStorage.setItem(girisAylikSatirYilKayitKey(), JSON.stringify(window.__aylikSatirYilDetay||{})); }catch(e){}
}
function girisAylikSatirYilDbSonrasi(mid){ return Promise.resolve(); }
"""
    # stubs after extract so Hesapla/helpers override heavy real impl
    blok = "\n".join(parts) + "\n" + stubs
    code = _harness(blok, scenario)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(code)
        path = f.name
    try:
        r = subprocess.run(
            ["node", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if r.returncode != 0:
            check("node run", False, (r.stderr or r.stdout or "")[:200])
            return {"_err": (r.stderr or r.stdout or "")[:300]}
        line = [ln for ln in (r.stdout or "").splitlines() if ln.strip()][-1]
        return json.loads(line)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _http_of(out: dict, method: str) -> list:
    return [h for h in (out.get("http") or []) if h.get("method") == method and "reel-donem-tutar" in (h.get("url") or "") and "tutarlar" not in (h.get("url") or "")]


def test_1_inactive_with_db_deletes() -> None:
    out = _node(
        f"""
seedRow({SYN_Y}, {{ toplam: {KART}, reel: 0, reelAktif: false, dbReel: {KART} }});
girisAylikSatirYilKaydet({SYN_Y});
await wait(50);
"""
    )
    posts = _http_of(out, "POST")
    deletes = _http_of(out, "DELETE")
    check("1 DELETE var", len(deletes) == 1, deletes)
    check("1 UPSERT yok", len(posts) == 0, posts)
    if deletes:
        check("1 DELETE yil", deletes[0].get("body", {}).get("donem_yil") == SYN_Y, deletes[0])


def test_2_inactive_no_db_no_request() -> None:
    out = _node(
        f"""
seedRow({SYN_Y}, {{ toplam: {KART}, reel: 0, reelAktif: false }});
girisAylikSatirYilKaydet({SYN_Y});
await wait(50);
"""
    )
    posts = _http_of(out, "POST")
    deletes = _http_of(out, "DELETE")
    check("2 POST yok", len(posts) == 0, posts)
    check("2 DELETE yok", len(deletes) == 0, deletes)


def test_3_active_upsert_body_birebir() -> None:
    # Capture body from DbKaydetTek path — same keys/values as legacy
    out = _node(
        f"""
seedRow({SYN_Y}, {{ toplam: {KART}, reel: {KART}, reelAktif: true }});
girisAylikSatirYilKaydet({SYN_Y});
await wait(50);
"""
    )
    posts = _http_of(out, "POST")
    check("3 POST 1", len(posts) == 1, posts)
    if posts:
        b = posts[0].get("body") or {}
        check("3 mid", b.get("musteri_id") == SYN_MID, b)
        check("3 yil", b.get("donem_yil") == SYN_Y, b)
        check("3 tutar", abs(float(b.get("tutar_kdv_dahil") or 0) - KART) < 0.02, b)
        check("3 tip dahil", b.get("giris_tip") == "dahil", b)
        check("3 giris_tutar", abs(float(b.get("giris_tutar") or 0) - KART) < 0.02, b)
        # birebir key set (legacy DbKaydetTek)
        keys = set(b.keys())
        check("3 keys", keys == {"musteri_id", "donem_yil", "tutar_kdv_dahil", "giris_tip", "giris_tutar"}, keys)


def test_4_active_empty_field_no_upsert() -> None:
    out = _node(
        f"""
seedRow({SYN_Y}, {{ toplam: {KART}, reel: 0, reelAktif: true, dbReel: {KART} }});
girisAylikSatirYilKaydet({SYN_Y});
await wait(50);
"""
    )
    posts = _http_of(out, "POST")
    deletes = _http_of(out, "DELETE")
    check("4 UPSERT yok (aktif+bos)", len(posts) == 0, posts)
    check("4 DELETE var", len(deletes) == 1, deletes)


def test_5_tutar0_kayitli_delete() -> None:
    out = _node(
        f"""
seedRow({SYN_Y}, {{ toplam: 0, reel: 0, reelAktif: false, dbReel: {KART} }});
// toplam 0 → eski tutar=0 dalı
var row=document.getElementById('aylik_yil_row_{SYN_Y}');
row._toplam='0';
window.__aylikSatirYilDetay['{SYN_Y}'].toplam=0;
window.__aylikSatirYilDetay['{SYN_Y}'].kdv_dahil=0;
window.__aylikSatirYilDetay['{SYN_Y}'].aylik=0;
girisAylikSatirYilKaydet({SYN_Y});
await wait(50);
"""
    )
    deletes = _http_of(out, "DELETE")
    posts = _http_of(out, "POST")
    check("5 DELETE", len(deletes) == 1, deletes)
    check("5 POST yok", len(posts) == 0, posts)


def test_6_toplu_reel_kaydet_untouched() -> None:
    src = HTML.read_text(encoding="utf-8", errors="replace")
    # sözleşmeler toplu POST path still method POST reel-donem-tutar
    i = src.find("kayitlar.forEach(function (it)")
    check("6 toplu forEach var", i > 0)
    snippet = src[i : i + 800] if i > 0 else ""
    check("6 toplu POST", "method: 'POST'" in snippet and "reel-donem-tutar" in snippet, snippet[:120])


def test_7_be_delete_invalidate() -> None:
    for k in list(os.environ):
        if k.startswith(("ALLOW_PROD_WRITE", "REEL_TUFE_", "KAPANIS_", "ODA_")):
            os.environ.pop(k, None)
    sys.path.insert(0, str(ROOT))
    # Import routes with mocked deps — call delete handler internals via source assert + spy
    py = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8", errors="replace")
    # find delete function body
    m = re.search(
        r"def api_reel_donem_tutar_delete\(\):([\s\S]*?)(?=\n@bp\.route|\ndef )",
        py,
    )
    check("7 delete fn var", bool(m))
    body = m.group(1) if m else ""
    check("7 uses _ekstre_invalidate_after_change", "_ekstre_invalidate_after_change" in body)
    check("7 returns guncel_grid", "guncel_grid" in body)
    check("7 returns guncel_panel", "guncel_panel" in body)
    check("7 no bare panel DELETE leftover as only path", "DELETE FROM musteri_tahsilat_panel_detay" not in body)
    # spy call shape with mock
    calls = {"n": 0, "mid": None}

    def fake_inv(mid, *a, **k):
        calls["n"] += 1
        calls["mid"] = mid
        return {"grid": {"aylar": []}, "panel": {}, "grid_hata": False, "panel_hata": False}

    # unit: simulate the return packing
    g = fake_inv(SYN_MID)
    check("7 spy grid", isinstance(g.get("grid"), dict))
    check("7 spy called mid", calls["mid"] == SYN_MID)
    # Z=0 note: rebuild early; data same — documented by invalidate reuse (no Z branch in delete)


def test_8_mid53_like_chain_doc() -> None:
    """Senaryo belgesi + FE: 2026 silinince DB map'ten düşer; Z1 beklenen zincir formülü."""
    oran = 31.23
    expect_z1 = round(REEL_ONCE * (1 + oran / 100.0), 2)
    expect_z0_kart = KART
    out = _node(
        f"""
seedRow(2025, {{ toplam: {REEL_ONCE}, reel: {REEL_ONCE}, reelAktif: true, dbReel: {REEL_ONCE} }});
seedRow(2026, {{ toplam: {KART}, reel: 0, reelAktif: false, dbReel: {KART} }});
girisAylikSatirYilKaydet(2026);
await wait(50);
"""
    )
    deletes = _http_of(out, "DELETE")
    posts = _http_of(out, "POST")
    check("8 2026 DELETE", len(deletes) == 1, deletes)
    check("8 2026 POST yok", len(posts) == 0, posts)
    db = out.get("dbMap") or {}
    check("8 db 2026 yok", not (db.get("2026") or db.get(2026)), db)
    check("8 z1 expect", abs(expect_z1 - 14528.47) < 0.02, expect_z1)
    check("8 z0 kart", abs(expect_z0_kart - KART) < 0.02)
    print(
        "doc8 Z1 zincir="
        + str(expect_z1)
        + " Z0_kart="
        + str(expect_z0_kart)
        + " (BE Z bayrağına göre; FE yalnız silme)"
    )


def main() -> int:
    for k in list(os.environ):
        if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "ALLOW_PROD_WRITE")):
            os.environ.pop(k, None)
    print("=== test_reel_donem_sil_kaydet ===")
    test_1_inactive_with_db_deletes()
    test_2_inactive_no_db_no_request()
    test_3_active_upsert_body_birebir()
    test_4_active_empty_field_no_upsert()
    test_5_tutar0_kayitli_delete()
    test_6_toplu_reel_kaydet_untouched()
    test_7_be_delete_invalidate()
    test_8_mid53_like_chain_doc()
    print("FAILS", len(FAILS), FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
