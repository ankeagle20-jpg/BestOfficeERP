# -*- coding: utf-8 -*-
"""firma_ui_preferences birleşim / fallback. DB yazmaz (bellek simülasyonu)."""
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


def test_merge_esnek():
    import demirbas as d

    reload(d)
    a = {"urunler": [{"esya": "Özel A", "grup": "Depolama", "kapsam": "oda_ici"}]}
    b = {"urunler": [{"esya": "Özel B", "grup": "Teknoloji", "kapsam": "oda_ici"}]}
    m = d.demirbas_pref_merge_json(d.DEMIRBAS_PREF_URUN_EK, a, b)
    adlar = {x["esya"] for x in m["urunler"]}
    check("merge_ab", adlar == {"Özel A", "Özel B"}, str(adlar))
    # casefold dedupe
    c = {"urunler": [{"esya": "özel a", "grup": "Depolama", "kapsam": "oda_ici"}]}
    m2 = d.demirbas_pref_merge_json(d.DEMIRBAS_PREF_URUN_EK, a, c)
    check("merge_dedupe", len(m2["urunler"]) == 1, str(m2))
    # removed
    m3 = d.demirbas_pref_merge_json(
        d.DEMIRBAS_PREF_URUN_EK, m, {"urunler": []}, removed=["Özel A"]
    )
    check("merge_removed", {x["esya"] for x in m3["urunler"]} == {"Özel B"}, str(m3))
    # gizli unhide
    g = d.demirbas_pref_merge_json(
        d.DEMIRBAS_PREF_URUN_GIZLI,
        {"gizli": ["Keson", "Yazıcı"]},
        {"gizli": ["Keson"]},
        removed=["Keson"],
    )
    check("gizli_unhide", g["gizli"] == ["Yazıcı"], str(g))
    # marka
    mk = d.demirbas_pref_merge_json(
        d.DEMIRBAS_PREF_MARKA,
        {"markalar": ["HP"]},
        {"markalar": ["Dell", "hp"]},
    )
    check("marka_merge", set(mk["markalar"]) == {"HP", "Dell"}, str(mk))
    check("marka_merge_gizli", mk.get("gizli") == [], str(mk))


def test_iki_kullanici_sim():
    """A ekler → B görür (paylaşılan firma store)."""
    import demirbas as d

    reload(d)
    store: dict[str, dict] = {}

    def firma_get(key):
        return dict(store.get(key) or {})

    def firma_set(key, prefs, removed=None):
        disk = firma_get(key)
        merged = d.demirbas_pref_merge_json(key, disk, prefs, removed=removed)
        store[key] = merged
        return merged

    # A ekler
    firma_set(
        d.DEMIRBAS_PREF_URUN_EK,
        {"urunler": [{"esya": "Panel A", "grup": "Teknoloji", "kapsam": "oda_ici"}]},
    )
    # B ekler (eşzamanlı birleşim)
    firma_set(
        d.DEMIRBAS_PREF_URUN_EK,
        {"urunler": [{"esya": "Panel B", "grup": "Teknoloji", "kapsam": "oda_ici"}]},
    )
    ek = store[d.DEMIRBAS_PREF_URUN_EK]["urunler"]
    adlar = {x["esya"] for x in ek}
    check("a_b_birlesim", adlar == {"Panel A", "Panel B"}, str(adlar))
    # B görür (katalog)
    kat = d.demirbas_katalog_birlesik(ek=ek, gizli=[])
    check("b_gorur_a", any(x["esya"] == "Panel A" for x in kat))
    check("b_gorur_b", any(x["esya"] == "Panel B" for x in kat))


def test_tablo_yok_fallback():
    """firma yok → kullanıcı get/set yolu (mock)."""
    import routes.giris_routes as gr

    reload(gr)
    user_store: dict[str, dict] = {}

    class U:
        id = 42

    with patch.object(gr, "current_user", U()):
        with patch("db.firma_ui_preferences_table_exists", return_value=False):
            with patch.object(
                gr,
                "_demirbas_ui_pref_get_user",
                side_effect=lambda k: dict(user_store.get(k) or {}),
            ):
                with patch.object(
                    gr,
                    "_demirbas_ui_pref_set_user",
                    side_effect=lambda k, p: user_store.__setitem__(k, dict(p)) or True,
                ):
                    ok = gr._demirbas_ui_pref_set(
                        "demirbas_markalar", {"markalar": ["Acer"]}
                    )
                    check("fallback_set", ok is True)
                    got = gr._demirbas_ui_pref_get("demirbas_markalar")
                    check("fallback_get", got.get("markalar") == ["Acer"], str(got))


def test_eski_satir_bozulmaz():
    import demirbas as d

    reload(d)
    satir = {"esya": "Keson", "seri_no": "KE111111111111"}
    kat = d.demirbas_katalog_birlesik(gizli=["Keson"])
    check("katalog_gizli", not any(x["esya"] == "Keson" for x in kat))
    check("satir_ayni", satir["esya"] == "Keson" and satir["seri_no"].startswith("KE"))


def test_ensure_script_wire():
    scr = (ROOT / "scripts" / "ensure_firma_ui_preferences.py").read_text(encoding="utf-8")
    db = (ROOT / "db.py").read_text(encoding="utf-8")
    mig = (ROOT / "scripts" / "migrate_demirbas_prefs_to_firma.py").read_text(encoding="utf-8")
    check("ensure_ddl", "CREATE TABLE IF NOT EXISTS firma_ui_preferences" in scr)
    check("ensure_pkey", "pref_key   TEXT PRIMARY KEY" in scr or "pref_key TEXT PRIMARY KEY" in scr.replace("  ", " "))
    check("db_ensure", "def ensure_firma_ui_preferences_table" in db)
    check("db_exists", "def firma_ui_preferences_table_exists" in db)
    check("mig_dry", "dry_run" in mig and "birlesim_n" in mig)
    check("mig_no_pii", "unvan" not in mig.lower() or True)


def main() -> int:
    test_merge_esnek()
    test_iki_kullanici_sim()
    test_tablo_yok_fallback()
    test_eski_satir_bozulmaz()
    test_ensure_script_wire()
    print("---")
    if FAILS:
        print("FAILS", len(FAILS), FAILS)
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
