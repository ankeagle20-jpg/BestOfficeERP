# -*- coding: utf-8 -*-
"""Bayat yeşil / hayalet yeniden üretimi sertleştirme — FE kaynak + BE kanonikleştirme."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:280]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_fe_sonboyama_panel_once():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    i = html.find("function sozlesmelerAylikGridVeriSonBoyama")
    j = html.find("function sozlesmeAylikGridParalelYukle", i)
    body = html[i:j]
    check("sonboyama_panel_once", "girisTahsilatYilAyPanelDbYukle(midNav" in body, "PanelDbYukle yok")
    check("sonboyama_zorla", "zorlaUygula: true" in body)
    check("sonboyama_persist0", "persistMs: 0" in body and "ttlMs: 0" in body)
    check("sonboyama_grid_sonra", "_sonBoyamaGridCiz" in body)


def test_fe_kayit_persist_kapali():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    i = html.find("function girisTahsilatYilAyKayitYukle")
    j = html.find("function girisTahsilatYilAyPanelBellekteTahsilVarMi", i)
    body = html[i:j]
    check("kayit_yukle_persist0", "persistMs: 0" in body and "ttlMs: 0" in body)
    check("kayit_ver", "GIRIS_TAHSILAT_YIL_AY_KAYIT_VER" in html)
    check("persist_ver", "GIRIS_JSON_PERSIST_VER" in html)
    check("ls_user_dirty_only", "__user_dirty) return" in body or "!d.__user_dirty) return" in body)


def test_fe_odendi_gridden_marker():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    i = html.find("function girisTahsilatYilAyPanelOdendiGriddenSenkron")
    chunk = html[i : i + 2200]
    check("odendi_sunucu_onay", "sunucuOnayOd" in chunk)
    check("odendi_marker", "sozlesmeTahsilMarkerAyAnahtarlari" in chunk)


def test_fe_cache_den_user_dirty():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    i = html.find("function girisTahsilatYilAyPanelCacheDenDoldur")
    chunk = html[i : i + 4500]
    check("cache_den_user_dirty", "__user_dirty" in chunk)
    check("cache_den_no_blind_keep", "yereli koruma KALDIRILDI" in chunk or "bayat yerel yeşili temizle" in chunk)


def test_fe_panel_kaydet_dirty_only():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    i = html.find("function girisTahsilatYilAyPanelDbKaydet")
    j = html.find("function girisTahsilatYilAyPanelEsasSenkron", i)
    body = html[i:j]
    check("kaydet_dirty_filter", "__user_dirty" in body and "__borclandir" in body)
    check("kaydet_empty_skip", "Object.keys(byIso).length" in body)


def test_be_panel_get_no_store():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    i = src.find("def api_tahsilat_panel_detay")
    j = src.find("\ndef api_aylik_tahsil_durum", i)
    body = src[i:j]
    check("get_no_store", "_json_no_cache" in body)
    check("get_no_grid_merge", "_panel_by_iso_from_grid_payload" not in body)
    check("post_no_except_pass", "except Exception:\n            pass" not in body)
    check("post_kanonik_reject", "Panel kanonikleştirme başarısız" in body)


def test_be_post_map0_ham_yesil_yazilmaz():
    from routes import giris_routes as gr
    from flask import Flask

    app = Flask(__name__)
    saved = {}

    def fake_save(mid, by_iso, prune_no_db_tahsil=False):
        saved["by_iso"] = dict(by_iso or {})

    with app.test_request_context(
        "/giris/api/tahsilat-panel-detay",
        method="POST",
        json={
            "musteri_id": 117,
            "by_iso": {
                "2023-09-01": {
                    "aylik": 2300.0,
                    "tahsil": 2300.0,
                    "kalan": 0.0,
                    "tahsil_tarih": "2026-07-24",
                }
            },
        },
    ):
        with mock.patch.object(gr, "fetch_one", return_value={"id": 117}), mock.patch.object(
            gr, "_aylik_tahsil_tutar_map", return_value={}
        ), mock.patch.object(
            gr,
            "_load_musteri_panel_by_iso",
            return_value={
                "2023-09-01": {
                    "aylik": 2300.0,
                    "tahsil": 0.0,
                    "kalan": 2300.0,
                }
            },
        ), mock.patch.object(gr, "_panel_by_iso_plan_duzelt", side_effect=lambda mid, b: b), mock.patch.object(
            gr, "_save_musteri_panel_by_iso", side_effect=fake_save
        ), mock.patch.object(
            gr, "_read_aylik_grid_cache_payload", return_value=None
        ), mock.patch.object(
            gr, "_defer_aylik_grid_cache_rebuild", return_value=None
        ):
            fn = getattr(gr.api_tahsilat_panel_detay, "__wrapped__", gr.api_tahsilat_panel_detay)
            resp = fn()
    # Flask may return (response, status) or response
    if isinstance(resp, tuple):
        response, status = resp[0], resp[1]
    else:
        response, status = resp, getattr(resp, "status_code", 200)
    data = response.get_json() if hasattr(response, "get_json") else {}
    check("post_ok", bool(data.get("ok")), data)
    row = (saved.get("by_iso") or {}).get("2023-09-01") or {}
    check("map0_tahsil0", round(float(row.get("tahsil") or 0), 2) == 0.0, row)
    check("map0_kalan_brut", abs(round(float(row.get("kalan") or 0), 2) - 2300.0) < 0.02, row)


def test_be_post_kanonik_hata_red():
    from routes import giris_routes as gr
    from flask import Flask

    app = Flask(__name__)

    with app.test_request_context(
        "/giris/api/tahsilat-panel-detay",
        method="POST",
        json={
            "musteri_id": 1,
            "by_iso": {"2023-09-01": {"aylik": 100.0, "tahsil": 100.0, "kalan": 0.0}},
        },
    ):
        with mock.patch.object(gr, "fetch_one", return_value={"id": 1}), mock.patch.object(
            gr, "_aylik_tahsil_tutar_map", side_effect=RuntimeError("map boom")
        ), mock.patch.object(gr, "_save_musteri_panel_by_iso") as save_m:
            fn = getattr(gr.api_tahsilat_panel_detay, "__wrapped__", gr.api_tahsilat_panel_detay)
            resp = fn()
    if isinstance(resp, tuple):
        response, status = resp[0], resp[1]
    else:
        response, status = resp, 200
    data = response.get_json() if hasattr(response, "get_json") else {}
    check("kanonik_fail_status", int(status) == 500, status)
    check("kanonik_fail_ok_false", data.get("ok") is False, data)
    check("kanonik_fail_no_save", save_m.call_count == 0, save_m.call_count)


def test_be_save_clear_zero_zero():
    from routes import giris_routes as gr

    stored = {}

    def fake_execute(sql, params=None):
        stored["params"] = params

    with mock.patch.object(gr, "_ensure_tahsilat_panel_detay_table", return_value=None), mock.patch.object(
        gr,
        "_load_musteri_panel_by_iso",
        return_value={
            "2023-09-01": {"aylik": 2300.0, "tahsil": 2300.0, "kalan": 0.0},
            "2023-10-01": {"aylik": 2300.0, "tahsil": 0.0, "kalan": 2300.0},
        },
    ), mock.patch.object(gr, "execute", side_effect=fake_execute):
        gr._save_musteri_panel_by_iso(
            117,
            {
                "2023-09-01": {"aylik": 2300.0, "tahsil": 0.0, "kalan": 0.0},
                "2023-10-01": {"aylik": 2300.0, "tahsil": 0.0, "kalan": 2300.0},
            },
        )
    import json

    payload = json.loads(stored["params"][1])
    check("clear_sep_gone", "2023-09-01" not in payload, payload)
    check("oct_kept_debt", round(float((payload.get("2023-10-01") or {}).get("kalan") or 0), 2) == 2300.0, payload)


def test_trust_default_hala_false():
    from routes import giris_routes as gr

    sig = inspect.signature(gr._panel_by_iso_from_tahsil_map)
    check("trust_still_false", sig.parameters["trust_grid_odenen"].default is False)


if __name__ == "__main__":
    for fn in (
        test_fe_sonboyama_panel_once,
        test_fe_kayit_persist_kapali,
        test_fe_odendi_gridden_marker,
        test_fe_cache_den_user_dirty,
        test_fe_panel_kaydet_dirty_only,
        test_be_panel_get_no_store,
        test_be_post_map0_ham_yesil_yazilmaz,
        test_be_post_kanonik_hata_red,
        test_be_save_clear_zero_zero,
        test_trust_default_hala_false,
    ):
        print("---", fn.__name__)
        fn()
    if FAILS:
        print("FAILED:", FAILS)
        sys.exit(1)
    print("ALL_OK")
