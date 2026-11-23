# -*- coding: utf-8 -*-
"""Panel seçim/Çıkar semantiği: hazır gizle, özel sil, toplu, mutation, zor adlar."""
from __future__ import annotations

import os
import sys
from importlib import reload
from pathlib import Path
from unittest.mock import patch

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


def _install_pref_store(gr, *, firma_table: bool, firma_store: dict, user_store: dict):
    def get_firma(key):
        if not firma_table:
            return None
        if key not in firma_store:
            return {}
        return dict(firma_store[key])

    def get_user(key):
        return dict(user_store.get(key) or {})

    def set_user(key, prefs):
        user_store[key] = dict(prefs)
        return True

    patches = [
        patch.object(gr, "_demirbas_ui_pref_get_firma", side_effect=get_firma),
        patch.object(gr, "_demirbas_ui_pref_get_user", side_effect=get_user),
        patch.object(gr, "_demirbas_ui_pref_set_user", side_effect=set_user),
        patch("db.firma_ui_preferences_table_exists", return_value=firma_table),
    ]
    if firma_table:

        def execute(sql, params=None):
            if params and len(params) >= 2:
                import json as _json

                key, raw = params[0], params[1]
                firma_store[key] = _json.loads(raw) if isinstance(raw, str) else dict(raw)

        patches.append(patch("db.execute", side_effect=execute))
    return patches


def _katalog_adlar(d, gr):
    ek, gizli = gr._demirbas_urun_ek_gizli()
    kat = d.demirbas_katalog_birlesik(ek=ek, gizli=gizli)
    return {x["esya"] for x in kat}, ek, gizli


def test_hazir_gizle_ozel_sil():
    import demirbas as d
    import routes.giris_routes as gr

    reload(d)
    reload(gr)
    firma_store: dict = {}
    user_store: dict = {}
    patches = _install_pref_store(
        gr, firma_table=True, firma_store=firma_store, user_store=user_store
    )

    class U:
        id = 7

    with patch.object(gr, "current_user", U()):
        entered = [p.__enter__() for p in patches]
        try:
            gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_URUN_EK,
                {
                    "urunler": d.demirbas_ek_normalize(
                        [
                            {
                                "esya": 'O\'zel "A" & B',
                                "grup": "Teknoloji",
                                "kapsam": "oda_ici",
                            }
                        ]
                    )
                },
            )
            ok, msg, ozet = gr._demirbas_urunleri_cikar_uygula(["Keson", 'O\'zel "A" & B'])
            check("cikar_ok", ok is True, msg)
            check("cikar_gizle_keson", "Keson" in (ozet.get("gizlenen") or []))
            check(
                "cikar_sil_ozel",
                any('O\'zel "A" & B' == x for x in (ozet.get("silinen") or [])),
                str(ozet),
            )
            adlar, ek, gizli = _katalog_adlar(d, gr)
            check("katalog_keson_yok", "Keson" not in adlar)
            check("katalog_ozel_yok", not any('O\'zel "A" & B' == x for x in adlar))
            check("ek_ozel_yok", not any(x["esya"] == 'O\'zel "A" & B' for x in ek))
            check("gizli_keson", any(d.demirbas_tr_casefold(x) == "keson" for x in gizli))
            # geri getir hazır
            gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_URUN_GIZLI, {"gizli": []}, removed=["Keson"]
            )
            adlar2, _, _ = _katalog_adlar(d, gr)
            check("geri_keson", "Keson" in adlar2)
            # aynı zor ad yeniden eklenebilir
            gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_URUN_EK,
                {
                    "urunler": d.demirbas_ek_normalize(
                        [
                            {
                                "esya": 'O\'zel "A" & B',
                                "grup": "Teknoloji",
                                "kapsam": "oda_ici",
                            }
                        ]
                    )
                },
            )
            adlar3, _, _ = _katalog_adlar(d, gr)
            check("yeniden_zor_ad", 'O\'zel "A" & B' in adlar3)
        finally:
            for p in patches:
                p.__exit__(None, None, None)


def test_user_fallback_ve_iki_kullanici():
    import demirbas as d
    import routes.giris_routes as gr

    reload(d)
    reload(gr)
    # user
    firma_store: dict = {}
    user_store: dict = {}
    patches = _install_pref_store(
        gr, firma_table=False, firma_store=firma_store, user_store=user_store
    )

    class U:
        id = 3

    with patch.object(gr, "current_user", U()):
        entered = [p.__enter__() for p in patches]
        try:
            gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_URUN_EK,
                {
                    "urunler": d.demirbas_ek_normalize(
                        [{"esya": "deneme", "grup": "Depolama", "kapsam": "oda_ici"}]
                    )
                },
            )
            ok, _, ozet = gr._demirbas_urunleri_cikar_uygula(["deneme"])
            check("user_sil", ok and "deneme" in (ozet.get("silinen") or []))
            adlar, ek, _ = _katalog_adlar(d, gr)
            check("user_katalog_yok", "deneme" not in adlar)
            check("user_ek_yok", not any(x["esya"] == "deneme" for x in ek))
        finally:
            for p in patches:
                p.__exit__(None, None, None)

    # iki kullanıcı firma
    firma_store = {}
    patches_a = _install_pref_store(
        gr, firma_table=True, firma_store=firma_store, user_store={}
    )

    class A:
        id = 1

    class B:
        id = 2

    with patch.object(gr, "current_user", A()):
        entered = [p.__enter__() for p in patches_a]
        try:
            gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_URUN_EK,
                {
                    "urunler": d.demirbas_ek_normalize(
                        [{"esya": "PanelX", "grup": "Teknoloji", "kapsam": "oda_ici"}]
                    )
                },
            )
            gr._demirbas_urunleri_cikar_uygula(["Keson"])
        finally:
            for p in patches_a:
                p.__exit__(None, None, None)
    patches_b = _install_pref_store(
        gr, firma_table=True, firma_store=firma_store, user_store={}
    )
    with patch.object(gr, "current_user", B()):
        entered = [p.__enter__() for p in patches_b]
        try:
            adlar, ek, gizli = _katalog_adlar(d, gr)
            check("b_keson_yok", "Keson" not in adlar)
            check("b_panelx_var", "PanelX" in adlar and any(x["esya"] == "PanelX" for x in ek))
            check("b_gizli_keson", any(d.demirbas_tr_casefold(x) == "keson" for x in gizli))
        finally:
            for p in patches_b:
                p.__exit__(None, None, None)


def test_toplu_liste_ve_marka():
    import demirbas as d
    import routes.giris_routes as gr

    reload(d)
    reload(gr)
    firma_store: dict = {}
    patches = _install_pref_store(
        gr, firma_table=True, firma_store=firma_store, user_store={}
    )

    class U:
        id = 9

    with patch.object(gr, "current_user", U()):
        entered = [p.__enter__() for p in patches]
        try:
            gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_URUN_EK,
                {
                    "urunler": d.demirbas_ek_normalize(
                        [{"esya": "Özel1", "grup": "Teknoloji", "kapsam": "oda_ici"}]
                    )
                },
            )
            gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_MARKA,
                d.demirbas_marka_prefs_normalize({"markalar": ["Acer"], "gizli": []}),
            )
            ok_u, _, ozet = gr._demirbas_urunleri_cikar_uygula(["Keson", "Özel1"])
            ok_m, _, sil_m = gr._demirbas_markalari_cikar_uygula(["Acer"])
            check("toplu_urun", ok_u and "Keson" in ozet["gizlenen"] and "Özel1" in ozet["silinen"])
            check("toplu_marka", ok_m and sil_m == ["Acer"])
            kayitli, _g = gr._demirbas_marka_kayitli_gizli()
            check("marka_yok", "Acer" not in kayitli)
            # Muhtelif reddi
            ok0, msg0, _ = gr._demirbas_markalari_cikar_uygula(["Muhtelif"])
            check("muhtelif_kilit", ok0 is False and "Muhtelif" in msg0)
        finally:
            for p in patches:
                p.__exit__(None, None, None)


def test_mutation_removed_ve_ozel_filtre():
    import demirbas as d

    reload(d)
    disk = {
        "urunler": [{"esya": "deneme", "grup": "Teknoloji", "kapsam": "oda_ici"}]
    }
    iyi = d.demirbas_pref_merge_json(
        d.DEMIRBAS_PREF_URUN_EK, disk, {"urunler": []}, removed=["deneme"]
    )
    check("mut_removed_iyi", not any(x["esya"] == "deneme" for x in iyi["urunler"]))
    real = d.demirbas_pref_merge_json

    def broken(pref_key, disk, incoming, *, removed=None):
        return real(pref_key, disk, incoming, removed=None)

    d.demirbas_pref_merge_json = broken  # type: ignore
    try:
        kotu = d.demirbas_pref_merge_json(
            d.DEMIRBAS_PREF_URUN_EK, disk, {"urunler": []}, removed=["deneme"]
        )
        check(
            "mut_removed_yakala",
            any(x["esya"] == "deneme" for x in kotu["urunler"]),
        )
    finally:
        d.demirbas_pref_merge_json = real  # type: ignore

    # hazir gizli filtre
    iyi2 = d.demirbas_katalog_birlesik(gizli=["Keson"])
    check("mut_gizli_iyi", not any(x["esya"] == "Keson" for x in iyi2))
    real2 = d.demirbas_katalog_birlesik

    def broken2(*, ek=None, gizli=None):
        return real2(ek=ek, gizli=[])

    d.demirbas_katalog_birlesik = broken2  # type: ignore
    try:
        kotu2 = d.demirbas_katalog_birlesik(gizli=["Keson"])
        check("mut_gizli_yakala", any(x["esya"] == "Keson" for x in kotu2))
    finally:
        d.demirbas_katalog_birlesik = real2  # type: ignore


def test_fe_secim_ui():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    gr = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("fe_sec_bos", "dem-panel-sec" in tjs and "dem-panel-aktif" not in tjs)
    check("fe_tumunu", "dem-panel-tumunu-sec" in tjs and "indeterminate" in tjs)
    check("fe_secilen_sil", "Seçilenleri sil" in tjs and "demirbas-liste-cikar" in tjs)
    check("fe_cikar_btn", "dem-panel-cikar" in tjs and "girisDemirbasPanelSatirCikar" in tjs)
    check("fe_geri", "dem-panel-geri" in tjs)
    check("fe_muhtelif_disabled", 'data-sabit="1"' in tjs)
    check(
        "fe_onclick_json_yok",
        "JSON.stringify(String(it.esya))" not in tjs
        and "JSON.stringify(String(m))" not in tjs,
    )
    check("fe_bind", "girisDemirbasDuzenlePanelBag" in tjs and "addEventListener" in tjs)
    check("fe_checked_default_yok", "dem-panel-sec\" checked" not in tjs.replace(" ", ""))
    check("be_liste_cikar", "/api/demirbas-liste-cikar" in gr)
    check("fe_ayna_liste", "demirbas-liste-cikar" in html)
    # zor ad escape
    check("fe_esc", "girisDemirbasEsc" in tjs and "data-esya=" in tjs)


def main() -> int:
    test_hazir_gizle_ozel_sil()
    test_user_fallback_ve_iki_kullanici()
    test_toplu_liste_ve_marka()
    test_mutation_removed_ve_ozel_filtre()
    test_fe_secim_ui()
    print("---")
    if FAILS:
        print("FAILS", len(FAILS), FAILS)
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
