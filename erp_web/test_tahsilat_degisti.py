# -*- coding: utf-8 -*-
"""Tahsilat tek giriş, makbuz istisnası, fazla tutar. Canlı yazma yok."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

from routes import giris_routes as gr  # noqa: E402

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:300]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_degisti_bir_kez():
    calls = {"resync": 0, "ekstre": 0, "inv": 0}

    def resync(mid):
        calls["resync"] += 1
        if calls["resync"] == 1:
            gr.tahsilat_degisti(mid)
        return True

    def ekstre(mid):
        calls["ekstre"] += 1

    def inv(mid, *a, **k):
        calls["inv"] += 1

    old = (
        gr.resync_panel_and_grid_after_tahsil_change,
        gr._cari_ekstre_cache_invalidate_musteri,
        gr._ekstre_invalidate_after_change,
    )
    gr.resync_panel_and_grid_after_tahsil_change = resync
    gr._cari_ekstre_cache_invalidate_musteri = ekstre
    gr._ekstre_invalidate_after_change = inv
    try:
        ok = gr.tahsilat_degisti(7)
        check("degisti resync bir", calls["resync"] == 1, calls)
        check("degisti ekstre mid", calls["ekstre"] == 1, calls)
        check("degisti tam invalidate yok", calls["inv"] == 0 and ok is True)
        ok2 = gr.tahsilat_degisti(7)
        check("degisti ikinci cagri yine bir", calls["resync"] == 2 and ok2 is True, calls)
    finally:
        (
            gr.resync_panel_and_grid_after_tahsil_change,
            gr._cari_ekstre_cache_invalidate_musteri,
            gr._ekstre_invalidate_after_change,
        ) = old


def test_degisti_yedek_invalidate():
    calls = {"inv": 0}

    def resync(mid):
        return False

    def ekstre(mid):
        return None

    def inv(mid, *a, **k):
        calls["inv"] += 1

    old = (
        gr.resync_panel_and_grid_after_tahsil_change,
        gr._cari_ekstre_cache_invalidate_musteri,
        gr._ekstre_invalidate_after_change,
    )
    gr.resync_panel_and_grid_after_tahsil_change = resync
    gr._cari_ekstre_cache_invalidate_musteri = ekstre
    gr._ekstre_invalidate_after_change = inv
    try:
        ok = gr.tahsilat_degisti(8)
        check("resync dusunce invalidate", calls["inv"] == 1 and ok is True, calls)
    finally:
        (
            gr.resync_panel_and_grid_after_tahsil_change,
            gr._cari_ekstre_cache_invalidate_musteri,
            gr._ekstre_invalidate_after_change,
        ) = old


def test_banka_bosalt():
    sqls = []

    def fake_execute(sql, params=()):
        sqls.append((" ".join(str(sql).split()), params))
        return 1

    old_ex = gr.execute
    old_re = gr.resync_panel_and_grid_after_tahsil_change
    old_ek = gr._cari_ekstre_cache_invalidate_musteri
    gr.execute = fake_execute
    gr.resync_panel_and_grid_after_tahsil_change = lambda mid: True
    gr._cari_ekstre_cache_invalidate_musteri = lambda mid: None
    try:
        gr.tahsilat_degisti(9, tahsilat_ids=[11, 11, 0, "x"], banka_bagini_birak=True)
        check("banka update bir", len(sqls) == 1, sqls)
        check(
            "banka any",
            sqls and "tahsilat_id = NULL" in sqls[0][0] and sqls[0][1][0] == [11],
            sqls,
        )
        gr.tahsilat_degisti(9)
        check("banka bayraksiz update yok", len(sqls) == 1, sqls)
    finally:
        gr.execute = old_ex
        gr.resync_panel_and_grid_after_tahsil_change = old_re
        gr._cari_ekstre_cache_invalidate_musteri = old_ek


def test_makbuz_istisna():
    check("manuel", gr._tahsilden_cikar_makbuz_parcasi({"kaynak": "manuel_makbuz", "aciklama": ""}))
    check("banka", gr._tahsilden_cikar_makbuz_parcasi({"kaynak": "banka_import", "aciklama": ""}))
    check("link", gr._tahsilden_cikar_makbuz_parcasi({"kaynak": "odeme_linki", "aciklama": ""}))
    cok = "|AYLIK_PAY|2026-01-01=10||AYLIK_PAY|2026-02-01=20|"
    check("cok pay", gr._tahsilden_cikar_makbuz_parcasi({"kaynak": "", "aciklama": cok}))
    check("bos kaynak", not gr._tahsilden_cikar_makbuz_parcasi({"kaynak": "", "aciklama": "|AYLIK_TAH|2026-01-01|"}))
    tek = {"kaynak": "grid_toplu", "aciklama": "|AYLIK_TAH|2026-01-01||AYLIK_PAY|2026-01-01=100|"}
    check("grid tek pay", not gr._tahsilden_cikar_makbuz_parcasi(tek))
    check(
        "mesaj",
        gr._TAHSILDEN_CIKAR_MAKBUZ_MESAJ
        == "Bu tahsilat bir makbuzun parçası; makbuzu ekstre veya tahsilat raporundan silin",
    )


def _payload_1000():
    return {
        "aylar": [
            {"yil": 2026, "ay": m, "brut_tutar_kdv": 1000}
            for m in range(1, 13)
        ]
    }


def test_fazla_tutar():
    parsed = [
        {"yil": 2026, "ay": m, "tutar": 12000}
        for m in range(1, 13)
    ]
    ret = gr._tahsil_fazla_tutar_ret(parsed, _payload_1000(), False)
    check("12x12000 red", ret and ret["asilan_ay"] == 12, ret)
    check(
        "yillik uyari",
        ret and "Yıllık tutarı aylara bölmeyi unutmuş olabilirsiniz" in ret["mesaj"],
        ret,
    )
    normal = gr._tahsil_fazla_tutar_ret(
        [{"yil": 2026, "ay": 6, "tutar": 1000}], _payload_1000(), False
    )
    check("normal gecer", normal is None)
    kismi = gr._tahsil_fazla_tutar_ret(
        [{"yil": 2026, "ay": 6, "tutar": 400}], _payload_1000(), False
    )
    check("kismi gecer", kismi is None)
    onay = gr._tahsil_fazla_tutar_ret(parsed, _payload_1000(), True)
    check("onay gecer", onay is None)
    check("onay 1", gr._istek_onay_fazla_tutar({"onay_fazla_tutar": "1"}))
    check("onay yok", not gr._istek_onay_fazla_tutar({}))
    esik = gr._tahsil_fazla_esik(1000)
    check("esik 1500", esik == 1500, esik)
    sinir = gr._tahsil_fazla_tutar_ret(
        [{"yil": 2026, "ay": 6, "tutar": 1500}], _payload_1000(), False
    )
    check("esik esit gecer", sinir is None)
    bilinmeyen = gr._tahsil_fazla_tutar_ret(
        [{"yil": 2026, "ay": 6, "tutar": 99999}], {"aylar": []}, False
    )
    check("brut yok red yok", bilinmeyen is None)


def test_kural_a_ayni():
    gr._TAHSILAT_PANEL_DETAY_TABLE_READY = True
    kayitlar = {}

    def fake_fetch(sql, params=()):
        mid = int(params[0])
        return {"by_iso": json.dumps(kayitlar[mid])}

    old = gr.fetch_one
    gr.fetch_one = fake_fetch
    try:
        kayitlar[152] = {
            "2024-08-01": {"aylik": 4992, "tahsil": 600, "kalan": 0}
        }
        out152 = gr._load_musteri_panel_by_iso(
            152,
            ekstre_batch={"marker": {"2024-08-01": 600}, "pay": {}},
        )
        h152 = out152.get("2024-08-01") or {}
        check(
            "152 tahsil 600",
            h152.get("tahsil") == 600 and h152.get("kalan") == 0 and h152.get("aylik") == 4992,
            h152,
        )
        kayitlar[916] = {
            "2026-06-01": {"aylik": 1000, "tahsil": 12000, "kalan": 0}
        }
        out916 = gr._load_musteri_panel_by_iso(
            916,
            ekstre_batch={"marker": {"2026-06-01": 12000}, "pay": {}},
        )
        h916 = out916.get("2026-06-01") or {}
        check(
            "916 tahsil tavani 1000",
            h916.get("tahsil") == 1000 and h916.get("kalan") == 0 and h916.get("aylik") == 1000,
            h916,
        )
    finally:
        gr.fetch_one = old


def test_bayat_ucuz():
    sqls = []
    ham = {"2026-01-01": {"aylik": 1, "tahsil": 0, "kalan": 1}}

    def fake_ham(mid):
        return dict(ham)

    def fake_execute(sql, params=()):
        sqls.append((" ".join(str(sql).split()), params))
        return 1

    old_h = gr._panel_by_iso_ham
    old_e = gr.execute
    gr._panel_by_iso_ham = fake_ham
    gr.execute = fake_execute
    try:
        gr.tahsilat_paneli_bayat_isaretle(3)
        check("bayat bir update", len(sqls) == 1, sqls)
        govde = json.loads(sqls[0][1][0])
        check("bayat anahtar", govde.get("__bayat") == 1, govde)
        check("bayat updated_at yok", "updated_at" not in sqls[0][0])
        ham["__bayat"] = 1
        gr.tahsilat_paneli_bayat_isaretle(3)
        check("bayat ikinci yazmaz", len(sqls) == 1, sqls)
    finally:
        gr._panel_by_iso_ham = old_h
        gr.execute = old_e


def test_yollar_bagli():
    kok = ROOT
    beklenen = {
        "routes/banka_routes.py": ["tahsilat_degisti"],
        "odeme_linki.py": ["tahsilat_degisti"],
        "routes/faturalar_routes.py": ["tahsilat_degisti", "_cari_ekstre_cache_invalidate_musteri", "_banka_tahsilat_id_bosalt"],
        "routes/tahsilat_routes.py": ["_banka_tahsilat_id_bosalt"],
        "routes/musteri_routes.py": ["tahsilat_paneli_bayat_isaretle"],
    }
    for rel, adlar in beklenen.items():
        metin = (kok / rel).read_text(encoding="utf-8")
        for ad in adlar:
            check(rel + " " + ad, ad in metin)
    giris = (kok / "routes/giris_routes.py").read_text(encoding="utf-8")
    check("ekstre sil banka", "_banka_tahsilat_id_bosalt([tid])" in giris)
    check("cikar banka", "_banka_tahsilat_id_bosalt([s.get(\"id\") for s in silinen])" in giris)
    js = (kok / "_t.js").read_text(encoding="utf-8")
    check("js onay", "onay_fazla_tutar" in js and "girisTahsilFazlaTutarOnayMetni" in js)
    check("js cikar mesaj", "Tahsilat kaldırılamadı." in js)


if __name__ == "__main__":
    test_degisti_bir_kez()
    test_degisti_yedek_invalidate()
    test_banka_bosalt()
    test_makbuz_istisna()
    test_fazla_tutar()
    test_kural_a_ayni()
    test_bayat_ucuz()
    test_yollar_bagli()
    if FAILS:
        print("FAIL", len(FAILS), FAILS)
        sys.exit(1)
    print("hepsi gecti")
