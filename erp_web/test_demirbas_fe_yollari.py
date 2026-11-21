# -*- coding: utf-8 -*-
"""Demirbaş FE: /giris/api yolları ↔ url_map + hata görünürlüğü. DB yazmaz."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_", "DEMIRBAS_", "SOZLESME_")):
        os.environ.pop(k, None)

FAILS: list[str] = []
MID = "867"
MARKER = "/* === Demirbaşlar (DEMIRBAS_TABLOSU) === */"


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + ek) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _demirbas_block(src: str) -> str:
    i = src.find(MARKER)
    if i < 0:
        return ""
    k = src.find("function girisDemirbasKaydet", i)
    if k < 0:
        return src[i : i + 25000]
    # kaydet fonksiyonunun kapanışından sonraki ilk top-level sınır
    j = src.find("\n</script>", k)
    j2 = src.find("\n/* ===", k)
    end = len(src)
    if j > k:
        end = min(end, j)
    if j2 > k:
        end = min(end, j2)
    # kaba: kaydet'ten sonra en az 400 karakter
    end = max(end, k + 400)
    return src[i:end]


def _extract_fe_paths(block: str) -> list[str]:
    """GIRIS_DEMIRBAS_API + '...' ifadelerinden somut yollar (mid=867)."""
    m = re.search(r"var\s+GIRIS_DEMIRBAS_API\s*=\s*'([^']+)'", block)
    if not m:
        return []
    prefix = m.group(1).rstrip("/")
    paths: list[str] = []
    # bilinen uçlar (parçalı concat dahil)
    known = (
        "/demirbas-katalog",
        "/demirbas-seri-no",
        "/demirbas-urun",
        "/demirbas-urun-cikar",
        "/demirbas-urun-geri-getir",
        "/demirbas-markalar",
    )
    for lit in known:
        if f"GIRIS_DEMIRBAS_API + '{lit}" in block or f'GIRIS_DEMIRBAS_API + "{lit}' in block:
            p = f"{prefix}{lit}"
            if p not in paths:
                paths.append(p)
    if re.search(
        r"GIRIS_DEMIRBAS_API\s*\+\s*'/musteri/'\s*\+\s*encodeURIComponent\(mid\)\s*\+\s*'/demirbas'",
        block,
    ):
        paths.append(f"{prefix}/musteri/{MID}/demirbas")
    # beklenmeyen başka GIRIS_DEMIRBAS_API + '...' sabitleri
    for lit in re.findall(r"GIRIS_DEMIRBAS_API\s*\+\s*'([^']+)'", block):
        base = lit.split("?")[0]
        if base in known or base in ("/musteri/", "/demirbas"):
            continue
        if base.startswith("/musteri/"):
            continue
        p = prefix + base
        if p not in paths:
            paths.append(p)
    return paths


def _app():
    from auth import login_manager
    from routes.giris_routes import bp

    app = Flask(__name__)
    app.secret_key = "test-demirbas-fe"
    login_manager.init_app(app)
    app.register_blueprint(bp, url_prefix="/giris")
    return app


def test_ayna_ve_sabit():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    b_t = _demirbas_block(tjs)
    b_h = _demirbas_block(html)
    check("block_tjs", bool(b_t) and "GIRIS_DEMIRBAS_API" in b_t)
    check("block_html", bool(b_h) and "GIRIS_DEMIRBAS_API" in b_h)
    check("prefix_tek", "var GIRIS_DEMIRBAS_API = '/giris/api';" in b_t)
    check("prefix_html", "var GIRIS_DEMIRBAS_API = '/giris/api';" in b_h)
    check("bare_api_yok_tjs", "'/api/demirbas" not in b_t and '"/api/demirbas' not in b_t)
    check("bare_api_yok_html", "'/api/demirbas" not in b_h and '"/api/demirbas' not in b_h)
    # selectMusteri erken tetik (form kirli olsa bile)
    sel_i = tjs.find("function selectMusteri(id)")
    sel_chunk = tjs[sel_i : sel_i + 800] if sel_i >= 0 else ""
    check("select_erken", "girisDemirbasYukle(id)" in sel_chunk and "selectedId = id" in sel_chunk)
    check("dedupe_state", "__girisDemirbasYukleState" in b_t)
    check("hata_mesaj", "Demirbaş listesi yüklenemedi" in b_t)
    check("sema_mesaj", "Demirbaş tablosu güncellenmeli" in b_t)
    check("adet_fn", "girisDemirbasAdetDegisti" in b_t and "girisDemirbasAdetNormalize" in b_t)
    check("marka_key", "demirbas_markalar" in b_t and "Muhtelif" in b_t)
    check("marka_taban_sade", "var __girisDemirbasMarkaTaban = ['Muhtelif'];" in b_t)
    check("marka_api_yol", "demirbas-markalar" in b_t)
    check("urun_yeni", "__YENI__" in b_t and "demirbas-urun" in b_t)
    check("ui_adet_th", ">Adet</th>" in html and "Adet / Durum" not in html)
    # ayna: path sabitleri aynı
    check("ayna_paths", _extract_fe_paths(b_t) == _extract_fe_paths(b_h), str(_extract_fe_paths(b_h)))


def test_fe_paths_url_map():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    paths = _extract_fe_paths(_demirbas_block(tjs))
    check("fe_path_adet", len(paths) >= 3, str(paths))
    expect = {
        "/giris/api/demirbas-katalog",
        "/giris/api/demirbas-seri-no",
        "/giris/api/demirbas-urun",
        "/giris/api/demirbas-urun-cikar",
        "/giris/api/demirbas-urun-geri-getir",
        "/giris/api/demirbas-markalar",
        f"/giris/api/musteri/{MID}/demirbas",
    }
    check("fe_path_beklenen", expect.issubset(set(paths)), str(paths))
    app = _app()
    adapter = app.url_map.bind("localhost")
    method_hints = {
        "/giris/api/demirbas-katalog": ("GET",),
        "/giris/api/demirbas-seri-no": ("POST",),
        "/giris/api/demirbas-urun": ("POST",),
        "/giris/api/demirbas-urun-cikar": ("POST",),
        "/giris/api/demirbas-urun-geri-getir": ("POST",),
        "/giris/api/demirbas-markalar": ("GET", "POST"),
        f"/giris/api/musteri/{MID}/demirbas": ("GET", "PUT"),
    }
    for p in paths:
        methods = method_hints.get(p, ("GET", "POST", "PUT"))
        matched = False
        last_err = ""
        for meth in methods:
            try:
                adapter.match(p, method=meth)
                matched = True
                break
            except Exception as e:
                last_err = f"{meth}:{type(e).__name__}"
        check(f"url_map:{p}", matched, last_err)
    # negatif: eski yanlış önek eşleşmesin (kayıtlı olmamalı)
    bad = f"/api/musteri/{MID}/demirbas"
    try:
        adapter.match(bad, method="GET")
        bad_ok = False
    except Exception:
        bad_ok = True
    check("eski_yol_eslesmez", bad_ok)


def test_hata_yollari_node():
    """fetch reddi / aktif:false / aktif:true+tablo_var:false."""
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    block = _demirbas_block(tjs)
    check("node_block", bool(block))
    if not block:
        return
    # Sadece yükleme + API yardımcıları (satır UI'sız) — node'da güvenli eval
    need = [
        "var GIRIS_DEMIRBAS_API",
        "var __girisDemirbasKatalog",
        "var __girisDemirbasDiger",
        "var __girisDemirbasAktif",
        "var __girisDemirbasTabloVar",
        "var __girisDemirbasSemaHazir",
        "var __girisDemirbasYukleState",
        "function girisDemirbasApi",
        "function girisDemirbasSectionGoster",
        "function girisDemirbasMesaj",
        "function girisDemirbasYukle",
    ]
    parts: list[str] = []
    for name in need:
        i = block.find(name)
        check(f"part_{name[:24]}", i >= 0)
        if i < 0:
            return
        # sonraki function/var sınırına kadar
        j = len(block)
        for nxt in ("\nfunction ", "\nvar "):
            k = block.find(nxt, i + max(8, len(name)))
            if k > i and k < j:
                j = k
        parts.append(block[i:j].rstrip() + "\n")
    slim = "\n".join(parts)
    # bağımlı yardımcılar (hata yolu testi için stub)
    slim += "\nfunction girisDemirbasSatirEkle(){}\n"
    slim += "\nfunction girisDemirbasMarkaListeYukle(){ return Promise.resolve(); }\n"
    slim += "\nfunction girisDemirbasMarkaListeHtml(){}\n"
    slim += "\nfunction girisDemirbasDuzenlePanelDoldur(){}\n"
    slim += "\nvar __girisDemirbasMarkaVarsayilan = 'Muhtelif';\n"
    slim += "\nvar __girisDemirbasMarkalar = ['Muhtelif'];\n"
    slim += "\nvar __girisDemirbasMarkaTaban = ['Muhtelif'];\n"
    slim += "\nvar __girisDemirbasGizli = [];\n"
    slim += "\nvar __girisDemirbasGruplar = [];\n"

    js = r"""
const fs = require('fs');
const slim = fs.readFileSync(process.argv[2], 'utf8');
const vm = require('vm');

function jsonRes(status, body) {
  return Promise.resolve({
    status: status,
    ok: status >= 200 && status < 300,
    text: function () { return Promise.resolve(JSON.stringify(body)); }
  });
}

function harness(fetchImpl) {
  const els = {};
  function ensure(id) {
    if (!els[id]) {
      els[id] = { style: { display: 'none' }, textContent: '', innerHTML: '', value: id === 'musteri-id' ? '867' : '' };
    }
    return els[id];
  }
  ['giris_demirbas_section', 'giris_demirbas_mesaj', 'giris_demirbas_tbody', 'musteri-id'].forEach(ensure);
  const sandbox = {
    console: console,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    Promise: Promise,
    JSON: JSON,
    encodeURIComponent: encodeURIComponent,
    String: String,
    Array: Array,
    Object: Object,
    fetch: fetchImpl,
    document: { getElementById: function (id) { return ensure(id); } },
  };
  vm.createContext(sandbox);
  vm.runInContext(slim, sandbox);
  sandbox.__girisDemirbasYukleState = { mid: '', seq: 0, inflight: false, done: false };
  sandbox.girisDemirbasYukle('867');
  return new Promise(function (resolve, reject) {
    var n = 0;
    function tick() {
      n += 1;
      if (n > 200) return reject(new Error('timeout'));
      var st = sandbox.__girisDemirbasYukleState;
      if (st.done) {
        return resolve({
          display: els.giris_demirbas_section.style.display,
          msg: els.giris_demirbas_mesaj.textContent || '',
          aktif: !!sandbox.__girisDemirbasAktif,
        });
      }
      setTimeout(tick, 5);
    }
    setTimeout(tick, 5);
  });
}

(async function () {
  const out = {};
  out.reject = await harness(function () { return Promise.reject(new Error('net')); });
  out.aktif_false = await harness(function (url) {
    if (String(url).indexOf('demirbas-katalog') >= 0)
      return jsonRes(200, { ok: true, aktif: false, tablo_var: false, katalog: [] });
    return jsonRes(200, { ok: true, rows: [] });
  });
  out.tablo_yok = await harness(function (url) {
    if (String(url).indexOf('demirbas-katalog') >= 0)
      return jsonRes(200, {
        ok: true, aktif: true, tablo_var: false, katalog: [],
        mesaj: 'Demirbaş tablosu henüz oluşturulmamış.'
      });
    return jsonRes(200, { ok: true, rows: [] });
  });
  out.http404 = await harness(function () { return jsonRes(404, { ok: false }); });
  let n = 0;
  const els = {};
  function ensure(id) {
    if (!els[id]) els[id] = { style: { display: 'none' }, textContent: '', innerHTML: '', value: '867' };
    return els[id];
  }
  ['giris_demirbas_section', 'giris_demirbas_mesaj', 'giris_demirbas_tbody', 'musteri-id'].forEach(ensure);
  const sandbox = {
    console: console, setTimeout: setTimeout, clearTimeout: clearTimeout,
    Promise: Promise, JSON: JSON, encodeURIComponent: encodeURIComponent,
    String: String, Array: Array, Object: Object,
    fetch: function (url) {
      n += 1;
      if (String(url).indexOf('demirbas-katalog') >= 0)
        return jsonRes(200, { ok: true, aktif: false, tablo_var: false, katalog: [] });
      return jsonRes(200, { ok: true, rows: [] });
    },
    document: { getElementById: function (id) { return ensure(id); } },
  };
  vm.createContext(sandbox);
  vm.runInContext(slim, sandbox);
  sandbox.__girisDemirbasYukleState = { mid: '', seq: 0, inflight: false, done: false };
  sandbox.girisDemirbasYukle('867');
  sandbox.girisDemirbasYukle('867');
  await new Promise(function (r) { setTimeout(r, 50); });
  out.dedupe = { n: n, done: !!sandbox.__girisDemirbasYukleState.done };
  process.stdout.write(JSON.stringify(out));
})().catch(function (e) {
  process.stderr.write(String(e && e.stack || e));
  process.exit(2);
});
"""
    with tempfile.TemporaryDirectory() as td:
        slim_path = Path(td) / "slim.js"
        slim_path.write_text(slim, encoding="utf-8")
        runner = Path(td) / "run_hata.js"
        runner.write_text(js, encoding="utf-8")
        r = subprocess.run(
            ["node", str(runner), str(slim_path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    check("node_exit", r.returncode == 0, (r.stderr or r.stdout or "")[:300])
    if r.returncode != 0:
        return
    try:
        data = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:
        check("node_json", False, str(e)[:120])
        return
    rej = data.get("reject") or {}
    check(
        "hata_reject_gorunur",
        rej.get("display") != "none" and "yüklenemedi" in (rej.get("msg") or ""),
        str(rej),
    )
    af = data.get("aktif_false") or {}
    check("aktif_false_gizli", af.get("display") == "none" and not (af.get("msg") or ""), str(af))
    ty = data.get("tablo_yok") or {}
    check(
        "tablo_yok_gorunur",
        ty.get("display") != "none"
        and ("tablosu" in (ty.get("msg") or "") or "oluşturul" in (ty.get("msg") or "")),
        str(ty),
    )
    h4 = data.get("http404") or {}
    check(
        "http404_gorunur",
        h4.get("display") != "none"
        and "404" in (h4.get("msg") or "")
        and "yüklenemedi" in (h4.get("msg") or ""),
        str(h4),
    )
    dd = data.get("dedupe") or {}
    check("dedupe_tek_istek", dd.get("n") == 1, str(dd))


def main() -> int:
    test_ayna_ve_sabit()
    test_fe_paths_url_map()
    test_hata_yollari_node()
    print("---")
    if FAILS:
        print("FAILS", len(FAILS), FAILS)
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
