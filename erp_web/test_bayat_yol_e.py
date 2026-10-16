# -*- coding: utf-8 -*-
"""Commit E: banka prune / borclandir persist / manuel map_cap — unit + kaynak taraması."""
import ast
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_apply_makbuz_kaynak_prune():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    # apply_makbuz fonksiyon gövdesinde prune çağrısı
    i = src.find("def apply_makbuz_dagitim_to_panel_db")
    j = src.find("\ndef _effective_panel_tahsil", i)
    body = src[i:j]
    check("apply prune_no_db", "prune_no_db_tahsil=True" in body, body[-200:])
    check("apply panel_from_map", "_panel_by_iso_from_tahsil_map" in body)
    check("apply tahsil_guncelle", "_aylik_grid_cache_payload_tahsil_guncelle" in body)


def test_borclandir_persist():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    i = src.find("def api_aylik_tutarlardan_borclandir")
    j = src.find("\ndef api_aylik_tutarlardan_tahsil_et", i)
    body = src[i:j]
    check("borclandir persist", "_persist_grid_cache_with_panel" in body, "yok")


def test_sync_manuel_map_cap():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    i = src.find("def sync_musteri_panel_from_tahsil_and_dagitim")
    j = src.find("\ndef resync_panel_and_grid_after_tahsil_change", i)
    body = src[i:j]
    check("sync tahsil_map apply", "tahsil_map=tmap" in body or "tahsil_map=tmap" in body.replace(" ", ""))
    check("sync tahsil_guncelle", "_aylik_grid_cache_payload_tahsil_guncelle" in body)


def test_apply_prune_cagrilir_mock():
    from routes import giris_routes as gr

    calls = {"prune": 0, "save": []}

    def fake_panel_from_map(mid, payload, tahsilat_tarihi=None, trust_grid_odenen=True):
        calls["prune"] += 1
        return {"2025-01-01": {"aylik": 100.0, "tahsil": 100.0, "kalan": 0.0}}

    def fake_save(mid, by_iso, prune_no_db_tahsil=False):
        calls["save"].append(bool(prune_no_db_tahsil))

    with mock.patch.object(gr, "_load_musteri_panel_by_iso", return_value={}), mock.patch.object(
        gr, "_read_aylik_grid_cache_payload", return_value={"aylar": [{"yil": 2025, "ay": 1, "tutar_kdv_dahil": 100, "brut_tutar_kdv": 100}]}
    ), mock.patch.object(
        gr, "_build_aylik_grid_cache_payload", return_value={"aylar": [{"yil": 2025, "ay": 1, "tutar_kdv_dahil": 100, "brut_tutar_kdv": 100}]}
    ), mock.patch.object(
        gr, "_panel_by_iso_from_tahsil_map", side_effect=fake_panel_from_map
    ), mock.patch.object(
        gr, "_save_musteri_panel_by_iso", side_effect=fake_save
    ), mock.patch.object(
        gr, "_aylik_tahsil_tutar_map", return_value={"2025-01-01": 100.0}
    ), mock.patch.object(
        gr, "_aylik_grid_cache_payload_tahsil_guncelle", side_effect=lambda mid, p, **k: p
    ), mock.patch.object(
        gr, "_apply_panel_by_iso_to_grid_payload", return_value=None
    ), mock.patch.object(
        gr, "_ensure_aylik_grid_cache_table", return_value=None
    ), mock.patch.object(
        gr, "_invalidate_aylik_grid_payload_mem", return_value=None
    ), mock.patch.object(
        gr, "execute", return_value=None
    ), mock.patch.object(
        gr, "_month_brut_from_grid_payload", return_value=100.0
    ):
        gr.apply_makbuz_dagitim_to_panel_db(
            1, [{"iso": "2025-01-01", "tutar": 50.0}], tahsilat_tarihi="2025-01-15"
        )
    check("prune cagrildi", calls["prune"] >= 1, calls)
    check("save prune True", True in calls["save"], calls["save"])


def test_mutabakat_tavan_sunum():
    import aylik_mutabakat as am

    row = {
        "tahsil_panel": 2160.0,
        "tahsil_map": 2160.0,
        "tahsil_map_ham": 2750.0,
        "kira": 2160.0,
        "grid_tutar": 2160.0,
        "grid_durum": "odendi",
        "ekstre_tahsil_satir": 2160.0,
        "ekstre_borc": 2160.0,
        "borc_fatura": 2160.0,
    }
    uys = am.siniflandir(row)
    kodlar = {u["kod"]: u["ciddiyet"] for u in uys}
    check("tavan sinif", "map_aylik_tavan" in kodlar, kodlar)
    check("tavan sunum etiketi", kodlar.get("map_aylik_tavan") == "sunum")
    check("panel_map yok", "panel_map" not in kodlar, kodlar)


if __name__ == "__main__":
    for fn in (
        test_apply_makbuz_kaynak_prune,
        test_borclandir_persist,
        test_sync_manuel_map_cap,
        test_apply_prune_cagrilir_mock,
        test_mutabakat_tavan_sunum,
    ):
        print("---", fn.__name__)
        fn()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
