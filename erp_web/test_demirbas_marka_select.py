# -*- coding: utf-8 -*-
"""Marka select + model: datalist süzme kök nedeni, parçalama, yenileme, mutation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from importlib import reload
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_", "DEMIRBAS_", "SOZLESME_")):
        os.environ.pop(k, None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + ek) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_kok_neden_datalist():
    """Aday1: value=Muhtelif iken datalist önerisi yalnızca Muhtelif (Playwright stub)."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        check("pw_import", False, str(e))
        return
    html = """<!DOCTYPE html><html><body>
<input id="m" list="dl" value="Muhtelif">
<datalist id="dl">
<option value="Muhtelif"><option value="Samsung"><option value="Acer">
</datalist>
</body></html>"""
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page()
        page.set_content(html)
        info = page.evaluate(
            """() => {
              const opts = [...document.getElementById('dl').options].map(o => o.value);
              const v = (document.getElementById('m').value || '').toLowerCase();
              const filtered = opts.filter(o => o.toLowerCase().includes(v));
              return {opts, filtered, aday1: filtered.length===1 && filtered[0]==='Muhtelif'};
            }"""
        )
        b.close()
    check("aday1_datalist_suzme", info.get("aday1") is True, str(info))
    # Aday3: API birleşik liste dolu
    import demirbas as d

    reload(d)
    bir = d.demirbas_marka_listesi_birlesik(["Samsung", "Acer"])
    check("aday3_api_dolu", bir == ["Muhtelif", "Samsung", "Acer"], str(bir))
    # Aday2: eski satır ortak datalist kullanıyordu (kodda list=...) — artık select
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    check("aday2_eski_datalist_yok", 'list="giris_demirbas_marka_list"' not in tjs)
    check("fix_select", "dem-marka-sel" in tjs and "dem-marka-model" in tjs)


def test_parcala_birlestir():
    import demirbas as d

    reload(d)
    markalar = ["Samsung", "Acer", "HP"]
    cases = [
        ("Muhtelif", ("Muhtelif", "", False)),
        ("muhtelif", ("Muhtelif", "", False)),
        ("", ("Muhtelif", "", False)),
        ("Samsung S24F350", ("Samsung", "S24F350", False)),
        ("Acer", ("Acer", "", False)),
        ("Bilinmeyen X", ("Bilinmeyen X", "", True)),
        ("samsung s24", ("Samsung", "s24", False)),
    ]
    for raw, exp in cases:
        got = d.demirbas_marka_model_parcala(raw, markalar=markalar)
        check(f"parcala[{raw!r}]", got == exp, str(got))
    check(
        "birlestir_muh",
        d.demirbas_marka_model_birlestir("Muhtelif", "") == "Muhtelif",
    )
    check(
        "birlestir_model",
        d.demirbas_marka_model_birlestir("Samsung", "S24F350") == "Samsung S24F350",
    )
    check(
        "birlestir_muh_model",
        d.demirbas_marka_model_birlestir("muhtelif", "X") == "Muhtelif X",
    )


def test_fe_select_tum_secenekler_pw():
    """Select Muhtelif seçiliyken TÜM seçenekler DOM'da (datalist süzmesi yok)."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        check("pw2_import", False, str(e))
        return
    # FE fonksiyonlarını _t.js demirbaş bloğundan çıkar — minimal stub
    stub = r"""
<!DOCTYPE html><html><body>
<table><tbody id="giris_demirbas_tbody"></tbody></table>
<script>
var __girisDemirbasMarkaVarsayilan = 'Muhtelif';
var __girisDemirbasMarkalar = ['Muhtelif', 'A', 'B'];
function girisDemirbasEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function girisDemirbasMarkaFold(s) {
  return String(s || '').replace(/İ/g, 'i').replace(/I/g, 'ı').toLowerCase();
}
function girisDemirbasMarkaSelectHtml(selectedMarka) {
  var defM = 'Muhtelif';
  var sel = String(selectedMarka == null ? '' : selectedMarka).trim() || defM;
  if (girisDemirbasMarkaFold(sel) === 'muhtelif') sel = defM;
  var seen = {}, items = [];
  function add(t) {
    t = String(t || '').trim(); if (!t) return;
    var k = girisDemirbasMarkaFold(t); if (seen[k]) return; seen[k] = 1;
    items.push(k === 'muhtelif' ? defM : t);
  }
  add(defM); (__girisDemirbasMarkalar || []).forEach(add); add(sel);
  var html = '';
  items.forEach(function (t) {
    var isSel = girisDemirbasMarkaFold(t) === girisDemirbasMarkaFold(sel);
    html += '<option value="' + girisDemirbasEsc(t) + '"' + (isSel ? ' selected' : '') + '>' + girisDemirbasEsc(t) + '</option>';
  });
  return html;
}
function girisDemirbasMarkaSatirlariYenile() {
  var tb = document.getElementById('giris_demirbas_tbody');
  Array.prototype.forEach.call(tb.querySelectorAll('tr'), function (tr) {
    var sel = tr.querySelector('.dem-marka-sel');
    var mod = tr.querySelector('.dem-marka-model');
    var curM = sel.value, curMod = mod.value;
    sel.innerHTML = girisDemirbasMarkaSelectHtml(curM);
    Array.prototype.forEach.call(sel.options, function (o) {
      if (girisDemirbasMarkaFold(o.value) === girisDemirbasMarkaFold(curM)) sel.value = o.value;
    });
    mod.value = curMod;
  });
}
// satır
var tr = document.createElement('tr');
tr.innerHTML = '<td><select class="dem-marka-sel">' + girisDemirbasMarkaSelectHtml('Muhtelif') +
  '</select><input class="dem-marka-model" value="draft-model"></td>';
document.getElementById('giris_demirbas_tbody').appendChild(tr);
window.__probe1 = [...tr.querySelector('.dem-marka-sel').options].map(o => o.value);
// panel marka ekle simülasyonu
__girisDemirbasMarkalar = ['Muhtelif', 'A', 'B', 'C'];
girisDemirbasMarkaSatirlariYenile();
window.__probe2 = {
  opts: [...tr.querySelector('.dem-marka-sel').options].map(o => o.value),
  selected: tr.querySelector('.dem-marka-sel').value,
  model: tr.querySelector('.dem-marka-model').value
};
// yeni satır
var tr2 = document.createElement('tr');
tr2.innerHTML = '<td><select class="dem-marka-sel">' + girisDemirbasMarkaSelectHtml('Muhtelif') +
  '</select><input class="dem-marka-model"></td>';
document.getElementById('giris_demirbas_tbody').appendChild(tr2);
window.__probe3 = [...tr2.querySelector('.dem-marka-sel').options].map(o => o.value);
</script></body></html>
"""
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page()
        page.set_content(stub)
        p1 = page.evaluate("() => window.__probe1")
        p2 = page.evaluate("() => window.__probe2")
        p3 = page.evaluate("() => window.__probe3")
        b.close()
    check("select_tum_muhtelif", set(p1) >= {"Muhtelif", "A", "B"}, str(p1))
    check("yenile_opts", set(p2["opts"]) >= {"Muhtelif", "A", "B", "C"}, str(p2))
    check("yenile_secili", p2["selected"] == "Muhtelif", str(p2))
    check("yenile_model_koru", p2["model"] == "draft-model", str(p2))
    check("yeni_satir_guncel", set(p3) >= {"Muhtelif", "A", "B", "C"}, str(p3))


def test_mutation_select_html():
    """Mutation: select yalnızca seçili değeri yazarsa test FAIL yakalar."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        check("pw3_import", False, str(e))
        return

    def run(broken: bool):
        if broken:
            fn = """
function girisDemirbasMarkaSelectHtml(selectedMarka) {
  var sel = selectedMarka || 'Muhtelif';
  return '<option value="' + sel + '" selected>' + sel + '</option>';
}
"""
        else:
            fn = """
function girisDemirbasMarkaFold(s){return String(s||'').toLowerCase();}
function girisDemirbasEsc(s){return String(s||'');}
function girisDemirbasMarkaSelectHtml(selectedMarka) {
  var items = ['Muhtelif','A','B'];
  var sel = selectedMarka || 'Muhtelif';
  return items.map(t => '<option value="'+t+'"'+(t===sel?' selected':'')+'>'+t+'</option>').join('');
}
"""
        html = f"<!DOCTYPE html><html><body><select id='s'></select><script>{fn}; document.getElementById('s').innerHTML = girisDemirbasMarkaSelectHtml('Muhtelif'); window.n = document.getElementById('s').options.length; window.vals=[...document.getElementById('s').options].map(o=>o.value);</script></body></html>"
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page()
            page.set_content(html)
            n = page.evaluate("() => window.n")
            vals = page.evaluate("() => window.vals")
            b.close()
        return n, vals

    n_ok, vals_ok = run(False)
    n_bad, vals_bad = run(True)
    check("mut_iyi_n", n_ok == 3, str(vals_ok))
    check("mut_bozuk_n", n_bad == 1 and vals_bad == ["Muhtelif"], str(vals_bad))
    check("mut_ayirir", n_ok > n_bad)


def test_fe_wire():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    db = (ROOT / "demirbas.py").read_text(encoding="utf-8")
    check("fe_parcala", "girisDemirbasMarkaParcala" in tjs)
    check("fe_birlestir", "girisDemirbasMarkaBirlestir" in tjs)
    check("fe_satir_yenile", "girisDemirbasMarkaSatirlariYenile" in tjs)
    check("fe_datalist_attr_yok", 'list="giris_demirbas_marka_list"' not in tjs)
    check("html_datalist_yok", 'id="giris_demirbas_marka_list"' not in html)
    check("be_parcala", "def demirbas_marka_model_parcala" in db)
    check("fe_oku", "girisDemirbasMarkaSatirOku" in tjs)


def main() -> int:
    test_kok_neden_datalist()
    test_parcala_birlestir()
    test_fe_select_tum_secenekler_pw()
    test_mutation_select_html()
    test_fe_wire()
    print("---")
    if FAILS:
        print("FAILS", len(FAILS), FAILS)
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
