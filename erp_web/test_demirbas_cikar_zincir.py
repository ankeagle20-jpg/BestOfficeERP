# -*- coding: utf-8 -*-
"""Özel ürün ekle→çıkar zinciri (firma / kullanıcı / iki kullanıcı + mutation)."""
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
    """_demirbas_ui_pref_get/set gerçek zincirini bellek store ile bağla."""

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

    def set_pref(key, prefs, removed=None):
        return gr._demirbas_ui_pref_set(key, prefs, removed=removed)

    patches = [
        patch.object(gr, "_demirbas_ui_pref_get_firma", side_effect=get_firma),
        patch.object(gr, "_demirbas_ui_pref_get_user", side_effect=get_user),
        patch.object(gr, "_demirbas_ui_pref_set_user", side_effect=set_user),
        patch("db.firma_ui_preferences_table_exists", return_value=firma_table),
    ]
    if firma_table:

        def execute(sql, params=None):
            # INSERT/UPDATE firma_ui_preferences
            if params and len(params) >= 2:
                import json as _json

                key, raw = params[0], params[1]
                firma_store[key] = _json.loads(raw) if isinstance(raw, str) else dict(raw)

        patches.append(patch("db.execute", side_effect=execute))
    return patches, set_pref


def _katalog_adlar(d, gr):
    ek, gizli = gr._demirbas_urun_ek_gizli()
    kat = d.demirbas_katalog_birlesik(ek=ek, gizli=gizli)
    return {x["esya"] for x in kat}, ek, gizli


def test_firma_ekle_cikar_geri_kalici():
    import demirbas as d
    import routes.giris_routes as gr

    reload(d)
    reload(gr)
    firma_store: dict = {}
    user_store: dict = {}
    patches, _ = _install_pref_store(
        gr, firma_table=True, firma_store=firma_store, user_store=user_store
    )
    class U:
        id = 7

    with patch.object(gr, "current_user", U()):
        ctx = patches[0]
        for p in patches[1:]:
            ctx = ctx
        # enter all
        entered = [p.__enter__() for p in patches]
        try:
            ad = "deneme"
            grup = "Teknoloji"
            ek_in = d.demirbas_ek_normalize(
                [{"esya": ad, "grup": grup, "kapsam": "oda_ici"}]
            )
            ok = gr._demirbas_ui_pref_set(d.DEMIRBAS_PREF_URUN_EK, {"urunler": ek_in})
            check("firma_ekle_ok", ok is True)
            adlar, ek, gizli = _katalog_adlar(d, gr)
            check("firma_ekle_katalog", ad in adlar, str(adlar))
            check("firma_ekle_ek", any(x["esya"] == ad for x in ek))

            # çıkar = gizle (özel ürün ek'te kalır)
            gizli_in = d.demirbas_gizli_normalize(gizli + [ad])
            ok = gr._demirbas_ui_pref_set(d.DEMIRBAS_PREF_URUN_GIZLI, {"gizli": gizli_in})
            check("firma_cikar_ok", ok is True)
            adlar2, ek2, gizli2 = _katalog_adlar(d, gr)
            check("firma_cikar_katalog_yok", ad not in adlar2, str(adlar2))
            check("firma_cikar_ek_var", any(x["esya"] == ad for x in ek2))
            check("firma_cikar_gizli", any(d.demirbas_tr_casefold(x) == "deneme" for x in gizli2))

            # geri getir
            ok = gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_URUN_GIZLI, {"gizli": []}, removed=[ad]
            )
            check("firma_geri_ok", ok is True)
            adlar3, _ek3, gizli3 = _katalog_adlar(d, gr)
            check("firma_geri_katalog", ad in adlar3)
            check("firma_geri_gizli_bos", not any(d.demirbas_tr_casefold(x) == "deneme" for x in gizli3))

            # kalıcı sil (removed zorunlu)
            ok = gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_URUN_EK, {"urunler": []}, removed=[ad]
            )
            check("firma_kalici_ok", ok is True)
            adlar4, ek4, _g4 = _katalog_adlar(d, gr)
            check("firma_kalici_katalog_yok", ad not in adlar4)
            check("firma_kalici_ek_yok", not any(x["esya"] == ad for x in ek4))

            # aynı ad yeniden eklenebilir
            ok = gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_URUN_EK,
                {"urunler": d.demirbas_ek_normalize([{"esya": ad, "grup": grup, "kapsam": "oda_ici"}])},
            )
            check("firma_yeniden_ekle", ok is True)
            adlar5, _, _ = _katalog_adlar(d, gr)
            check("firma_yeniden_katalog", ad in adlar5)
        finally:
            for p, _ in zip(patches, entered):
                p.__exit__(None, None, None)


def test_user_fallback_ekle_cikar():
    """firma tablosu yok → kullanıcı store; çıkar sonrası katalogda yok."""
    import demirbas as d
    import routes.giris_routes as gr

    reload(d)
    reload(gr)
    firma_store: dict = {}
    user_store: dict = {}
    patches, _ = _install_pref_store(
        gr, firma_table=False, firma_store=firma_store, user_store=user_store
    )
    class U:
        id = 11

    with patch.object(gr, "current_user", U()):
        entered = [p.__enter__() for p in patches]
        try:
            ad = "deneme"
            gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_URUN_EK,
                {
                    "urunler": d.demirbas_ek_normalize(
                        [{"esya": ad, "grup": "Depolama", "kapsam": "oda_ici"}]
                    )
                },
            )
            adlar, _, _ = _katalog_adlar(d, gr)
            check("user_ekle", ad in adlar)
            gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_URUN_GIZLI, {"gizli": [ad]}
            )
            adlar2, ek2, _ = _katalog_adlar(d, gr)
            check("user_cikar_katalog_yok", ad not in adlar2)
            check("user_cikar_ek_var", any(x["esya"] == ad for x in ek2))
            check("user_store_dolu", d.DEMIRBAS_PREF_URUN_GIZLI in user_store)
        finally:
            for p in patches:
                p.__exit__(None, None, None)


def test_iki_kullanici_firma_paylasim():
    import demirbas as d
    import routes.giris_routes as gr

    reload(d)
    reload(gr)
    firma_store: dict = {}
    user_a: dict = {}
    user_b: dict = {}
    patches_a, _ = _install_pref_store(
        gr, firma_table=True, firma_store=firma_store, user_store=user_a
    )

    class UA:
        id = 1

    class UB:
        id = 2

    with patch.object(gr, "current_user", UA()):
        entered = [p.__enter__() for p in patches_a]
        try:
            gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_URUN_EK,
                {
                    "urunler": d.demirbas_ek_normalize(
                        [{"esya": "deneme", "grup": "Teknoloji", "kapsam": "oda_ici"}]
                    )
                },
            )
            gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_URUN_GIZLI, {"gizli": ["deneme"]}
            )
        finally:
            for p in patches_a:
                p.__exit__(None, None, None)

    patches_b, _ = _install_pref_store(
        gr, firma_table=True, firma_store=firma_store, user_store=user_b
    )
    with patch.object(gr, "current_user", UB()):
        entered = [p.__enter__() for p in patches_b]
        try:
            adlar, ek, gizli = _katalog_adlar(d, gr)
            check("b_katalog_yok", "deneme" not in adlar)
            check("b_ek_gorur", any(x["esya"] == "deneme" for x in ek))
            check("b_gizli_gorur", any(d.demirbas_tr_casefold(x) == "deneme" for x in gizli))
            # B geri getirince A de görür
            gr._demirbas_ui_pref_set(
                d.DEMIRBAS_PREF_URUN_GIZLI, {"gizli": []}, removed=["deneme"]
            )
            adlar2, _, _ = _katalog_adlar(d, gr)
            check("b_geri_katalog", "deneme" in adlar2)
        finally:
            for p in patches_b:
                p.__exit__(None, None, None)


def test_ozel_gizli_filtre_mutation():
    """Mutation: ozel gizli filtresi bozulursa test FAIL etmeli (boş geçmesin)."""
    import demirbas as d

    reload(d)
    ek = [{"esya": "deneme", "grup": "Teknoloji", "kapsam": "oda_ici"}]
    gizli = ["deneme"]
    iyi = d.demirbas_katalog_birlesik(ek=ek, gizli=gizli)
    check("mut_once_yok", not any(x["esya"] == "deneme" for x in iyi))

    real = d.demirbas_katalog_birlesik

    def broken(*, ek=None, gizli=None):
        # eski davranış: ozel gizlenmez
        return real(ek=ek, gizli=[])

    d.demirbas_katalog_birlesik = broken  # type: ignore
    try:
        kotu = d.demirbas_katalog_birlesik(ek=ek, gizli=gizli)
        # bozuk fonksiyon ürünü geri getirir → assertion yakalar
        check(
            "mut_bozuk_yakala",
            any(x["esya"] == "deneme" for x in kotu),
            "mutation ürünü göstermeli",
        )
        # asıl koruma: düzgün filtre zorunlu
        check(
            "mut_duzeltme_gerekli",
            any(x["esya"] == "deneme" for x in kotu)
            and not any(x["esya"] == "deneme" for x in iyi),
        )
    finally:
        d.demirbas_katalog_birlesik = real  # type: ignore


def test_kalici_removed_mutation():
    """Mutation: removed yok sayılırsa kalıcı sil birleşimde geri gelir → yakalanır."""
    import demirbas as d

    reload(d)
    disk = {
        "urunler": [{"esya": "deneme", "grup": "Teknoloji", "kapsam": "oda_ici"}]
    }
    incoming = {"urunler": []}
    iyi = d.demirbas_pref_merge_json(
        d.DEMIRBAS_PREF_URUN_EK, disk, incoming, removed=["deneme"]
    )
    check("mut_removed_iyi", not any(x["esya"] == "deneme" for x in iyi["urunler"]))

    real = d.demirbas_pref_merge_json

    def broken(pref_key, disk, incoming, *, removed=None):
        return real(pref_key, disk, incoming, removed=None)

    d.demirbas_pref_merge_json = broken  # type: ignore
    try:
        kotu = d.demirbas_pref_merge_json(
            d.DEMIRBAS_PREF_URUN_EK, disk, incoming, removed=["deneme"]
        )
        check(
            "mut_removed_yakala",
            any(x["esya"] == "deneme" for x in kotu["urunler"]),
            "removed yok sayılınca ürün kalmalı",
        )
    finally:
        d.demirbas_pref_merge_json = real  # type: ignore


def test_panel_ve_fe_wire():
    import demirbas as d

    reload(d)
    panel = d.demirbas_panel_urunler(
        ek=[{"esya": "deneme", "grup": "Teknoloji", "kapsam": "oda_ici"}],
        gizli=["deneme", "Keson"],
    )
    den = next(x for x in panel if x["esya"] == "deneme")
    kes = next(x for x in panel if x["esya"] == "Keson")
    check("panel_ozel_pasif", den["aktif"] is False and den["kaynak"] == "ozel")
    check("panel_hazir_pasif", kes["aktif"] is False and kes["kaynak"] == "hazir")
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("fe_checkbox", "dem-panel-aktif" in tjs and "girisDemirbasUrunAktifDegisti" in tjs)
    check("fe_kalici", "girisDemirbasUrunKaliciSil" in tjs and "kalici: true" in tjs)
    check("fe_baslik_ekle", "girisDemirbasDuzenleBaslikEkleHtml" in tjs)
    # Eski kök neden: onclick="...Cikar("+JSON.stringify → tırnak kırılması
    check(
        "fe_onclick_json_yok",
        "girisDemirbasUrunCikar(' +" not in tjs
        and "JSON.stringify(String(it.esya))" not in tjs,
    )
    check("fe_gizli_bolum_yok", "Gizlenenleri geri getir" not in tjs)
    check("fe_gizli_html_yok", "giris_demirbas_gizli_liste" not in html)
    check("fe_data_esya", "data-esya=" in tjs and "addEventListener('change'" in tjs)
    gr = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("be_panel_payload", "demirbas_panel_urunler" in gr and '"panel"' in gr)


def main() -> int:
    test_firma_ekle_cikar_geri_kalici()
    test_user_fallback_ekle_cikar()
    test_iki_kullanici_firma_paylasim()
    test_ozel_gizli_filtre_mutation()
    test_kalici_removed_mutation()
    test_panel_ve_fe_wire()
    print("---")
    if FAILS:
        print("FAILS", len(FAILS), FAILS)
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
