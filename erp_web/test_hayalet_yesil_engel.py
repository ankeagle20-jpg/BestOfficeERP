# -*- coding: utf-8 -*-
"""ADIM 1: hayalet yeşil engeli — trust_grid_odenen + FE seçim + bayrak dokunulmazlığı."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_trust_default_false():
    from routes import giris_routes as gr

    sig = inspect.signature(gr._panel_by_iso_from_tahsil_map)
    default = sig.parameters["trust_grid_odenen"].default
    check("trust_default_False", default is False, default)


def test_map0_eski_odenen_geri_yazilmaz():
    from routes import giris_routes as gr

    payload = {
        "aylar": [
            {
                "yil": 2026,
                "ay": 7,
                "brut_tutar_kdv": 964.91,
                "tutar_kdv_dahil": 964.91,
                "odenen_tutar_kdv": 964.91,
                "kalan_tutar_kdv": 0.0,
                "tahsil_edildi": True,
                "kismi_tahsilat": False,
            }
        ]
    }
    with mock.patch.object(gr, "_aylik_tahsil_tutar_map", return_value={}), mock.patch.object(
        gr, "_ekstre_tahsil_rows_for_musteri", return_value=[]
    ), mock.patch.object(
        gr,
        "_ekstre_tahsil_batch_maps_from_rows",
        return_value={"marker": {}, "pay": {}, "eslesme": {}},
    ), mock.patch.object(gr, "_load_musteri_panel_by_iso", return_value={}):
        by_false = gr._panel_by_iso_from_tahsil_map(
            1, payload, None, trust_grid_odenen=False
        )
        by_true = gr._panel_by_iso_from_tahsil_map(
            1, payload, None, trust_grid_odenen=True
        )
    iso = "2026-07-01"
    check("map0_trustFalse_tahsil0", round(float((by_false.get(iso) or {}).get("tahsil") or 0), 2) == 0.0, by_false)
    check(
        "map0_trustFalse_kalan_brut",
        abs(round(float((by_false.get(iso) or {}).get("kalan") or 0), 2) - 964.91) < 0.02,
        by_false,
    )
    check(
        "map0_trustTrue_hala_odenen",
        abs(round(float((by_true.get(iso) or {}).get("tahsil") or 0), 2) - 964.91) < 0.02,
        by_true,
    )


def test_kismi_map_alti_brut():
    from routes import giris_routes as gr

    payload = {
        "aylar": [
            {
                "yil": 2027,
                "ay": 4,
                "brut_tutar_kdv": 1281.59,
                "tutar_kdv_dahil": 1281.59,
                "odenen_tutar_kdv": 0.0,
                "kalan_tutar_kdv": 1281.59,
            }
        ]
    }
    with mock.patch.object(
        gr, "_aylik_tahsil_tutar_map", return_value={"2027-04-01": 964.91}
    ), mock.patch.object(gr, "_ekstre_tahsil_rows_for_musteri", return_value=[]), mock.patch.object(
        gr,
        "_ekstre_tahsil_batch_maps_from_rows",
        return_value={"marker": {}, "pay": {}, "eslesme": {}},
    ), mock.patch.object(gr, "_load_musteri_panel_by_iso", return_value={}):
        by_iso = gr._panel_by_iso_from_tahsil_map(1, payload, None)
    row = by_iso.get("2027-04-01") or {}
    check("kismi_tahsil", abs(round(float(row.get("tahsil") or 0), 2) - 964.91) < 0.02, row)
    check("kismi_kalan", abs(round(float(row.get("kalan") or 0), 2) - 316.68) < 0.02, row)
    check("kismi_aylik", abs(round(float(row.get("aylik") or 0), 2) - 1281.59) < 0.02, row)


def test_sync_calls_trust_false():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    i = src.find("def sync_musteri_panel_from_tahsil_and_dagitim")
    j = src.find("\ndef resync_panel_and_grid_after_tahsil_change", i)
    body = src[i:j]
    check("sync_trust_False_kw", "trust_grid_odenen=False" in body, body[200:500])


def test_panel_get_no_grid_odenen_merge():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    i = src.find("def api_tahsilat_panel_detay")
    j = src.find("\ndef api_aylik_tahsil_durum", i)
    body = src[i:j]
    # GET: boş panelde grid odenen ile zenginleştirme kaldırıldı
    check(
        "panel_get_no_grid_src_merge",
        "_panel_by_iso_from_grid_payload" not in body,
        "halen grid_payload merge var",
    )


def test_tahsil_durum_marker_aylar():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    i = src.find("def api_aylik_tahsil_durum")
    j = src.find("\ndef api_cari_ekstre_b", i)
    body = src[i:j]
    check("durum_marker_aylar", '"marker_aylar"' in body or "'marker_aylar'" in body)


def test_fe_hayalet_secim():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("fe_marker_decl", "sozlesmeTahsilMarkerAyAnahtarlari" in html)
    check("fe_hayalet_fn", "function sozlesmelerAyKartHayaletYesilMi" in html)
    check("fe_warn", "yeşil görünüyor ama tahsilat kaydı yok" in html)
    # yesil iken hayalet ise seçilebilir
    i = html.find("function sozlesmelerAyKartYeniTahsilEdilebilirMi")
    chunk = html[i : i + 2500]
    check("fe_yesil_hayalet_allow", "sozlesmelerAyKartHayaletYesilMi(card)" in chunk)


def test_mutabakat_bayrak_dokunulmadi():
    src = (ROOT / "mutabakat_okuma.py").read_text(encoding="utf-8")
    check("bayrak_MUTABAKAT_OKUMA", "MUTABAKAT_OKUMA" in src)
    # Bu commit mutabakat_okuma.py değiştirmemeli — git diff ile doğrulanır; burada sabitler dursun
    check("hayalet_sinif_mutabakat", "grid_yesil_map_bos" in (ROOT / "aylik_mutabakat.py").read_text(encoding="utf-8"))


def test_trust_true_cagiran_yok_prod():
    """Üretim yollarında trust_grid_odenen=True bilinçli kalmamalı (yalnız test/araç)."""
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    # True yalnızca parametre tanımı / docstring dışında geçmesin
    hits = []
    for n, line in enumerate(src.splitlines(), 1):
        if "trust_grid_odenen=True" in line and "bool = False" not in line:
            hits.append(n)
    check("giris_routes_no_trust_True", hits == [], hits)


def test_map_cap_min():
    from routes import giris_routes as gr

    payload = {
        "aylar": [
            {
                "yil": 2026,
                "ay": 1,
                "brut_tutar_kdv": 100.0,
                "tutar_kdv_dahil": 100.0,
            }
        ]
    }
    with mock.patch.object(
        gr, "_aylik_tahsil_tutar_map", return_value={"2026-01-01": 150.0}
    ), mock.patch.object(gr, "_ekstre_tahsil_rows_for_musteri", return_value=[]), mock.patch.object(
        gr,
        "_ekstre_tahsil_batch_maps_from_rows",
        return_value={"marker": {}, "pay": {}, "eslesme": {}},
    ), mock.patch.object(gr, "_load_musteri_panel_by_iso", return_value={}):
        by_iso = gr._panel_by_iso_from_tahsil_map(1, payload, None)
    row = by_iso.get("2026-01-01") or {}
    check("map_cap_min_brut", round(float(row.get("tahsil") or 0), 2) == 100.0, row)
    check("map_cap_kalan0", round(float(row.get("kalan") or 0), 2) == 0.0, row)


if __name__ == "__main__":
    for fn in (
        test_trust_default_false,
        test_map0_eski_odenen_geri_yazilmaz,
        test_kismi_map_alti_brut,
        test_sync_calls_trust_false,
        test_panel_get_no_grid_odenen_merge,
        test_tahsil_durum_marker_aylar,
        test_fe_hayalet_secim,
        test_mutabakat_bayrak_dokunulmadi,
        test_trust_true_cagiran_yok_prod,
        test_map_cap_min,
    ):
        print("---", fn.__name__)
        fn()
    if FAILS:
        print("FAILED:", FAILS)
        sys.exit(1)
    print("ALL_OK")
