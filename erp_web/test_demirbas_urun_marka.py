# -*- coding: utf-8 -*-
"""Ürün ekle/gizle/geri getir + marka birleşimi. DB yazmaz."""
from __future__ import annotations

import os
import sys
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


def test_tr_casefold_ve_urun():
    import demirbas as d

    reload(d)
    check("fold_Ii", d.demirbas_tr_casefold("İstanbul") == d.demirbas_tr_casefold("istanbul"))
    check("fold_Iı", d.demirbas_tr_casefold("Işık") == d.demirbas_tr_casefold("ışık"))
    check("adi_ok", d.demirbas_urun_adi_temizle("  Keson X  ") == "Keson X")
    check("adi_html", d.demirbas_urun_adi_temizle("<b>Keson</b>") == "Keson")
    check("adi_kisa", d.demirbas_urun_adi_temizle("A") is None)
    check("adi_uzun", d.demirbas_urun_adi_temizle("x" * 41) is None)
    check("grup_ortak", d.demirbas_grup_kapsam("Ortak Alan (oda dışı)") == "ortak_alan")
    check("grup_oda", d.demirbas_grup_kapsam("Teknoloji") == "oda_ici")


def test_katalog_birlesik():
    import demirbas as d

    reload(d)
    base_n = len(d.demirbas_katalog_birlesik())
    check("base_dolu", base_n >= 30, str(base_n))
    gizli = ["Keson", "keson"]  # tek sayılmalı
    bir = d.demirbas_katalog_birlesik(gizli=gizli)
    check("gizli_dus", len(bir) == base_n - 1, f"{len(bir)} vs {base_n}")
    check("keson_yok", not any(x["esya"] == "Keson" for x in bir))
    ek = [{"esya": "Özel Raf", "grup": "Depolama"}]
    bir2 = d.demirbas_katalog_birlesik(ek=ek, gizli=gizli)
    ozel = [x for x in bir2 if x.get("kaynak") == "ozel"]
    check("ozel_1", len(ozel) == 1 and ozel[0]["esya"] == "Özel Raf", str(ozel))
    check("ozel_kapsam", ozel[0]["kapsam"] == "oda_ici")
    # Ortak gruptan kapsam
    ek_o = [{"esya": "Ortak TV Stand", "grup": "Ortak Alan (oda dışı)"}]
    bir3 = d.demirbas_katalog_birlesik(ek=ek_o)
    o2 = [x for x in bir3 if x["esya"] == "Ortak TV Stand"][0]
    check("ozel_ortak", o2["kapsam"] == "ortak_alan" and o2["kaynak"] == "ozel")
    # Gizli hazır aynı ad → ek yok sayılır
    bir4 = d.demirbas_katalog_birlesik(ek=[{"esya": "Keson", "grup": "Depolama"}], gizli=["Keson"])
    check("gizli_hazir_ek_yok", not any(x["esya"] == "Keson" for x in bir4))
    # İ/ı tekrar
    bir5 = d.demirbas_katalog_birlesik(
        ek=[
            {"esya": "İklim Paneli", "grup": "Teknoloji"},
            {"esya": "iklim paneli", "grup": "Teknoloji"},
        ]
    )
    check(
        "tekrar_Ii",
        sum(1 for x in bir5 if d.demirbas_tr_casefold(x["esya"]) == d.demirbas_tr_casefold("İklim Paneli"))
        == 1,
    )


def test_kapsam_ve_seri_yeni():
    import demirbas as d

    reload(d)
    ek = [{"esya": "Özel Raf", "grup": "Depolama", "kapsam": "oda_ici"}]
    check("kapsam_ek", d.demirbas_kapsam_for_esya("Özel Raf", ek=ek) == "oda_ici")
    check("kapsam_hazir", d.demirbas_kapsam_for_esya("Yazıcı") == "ortak_alan")
    onek = d.demirbas_seri_oneki("Özel Raf")
    check("onek_yeni", onek == "OR", onek)
    s = d.demirbas_seri_no_uret("Özel Raf")
    check("seri_yeni", s.startswith("OR") and len(s) == 14, s)


def test_marka_taban():
    import demirbas as d

    reload(d)
    check("taban_tek", d.DEMIRBAS_MARKA_LISTESI_TABAN == ["Muhtelif"])
    check("samsung_yok", "Samsung" not in d.DEMIRBAS_MARKA_LISTESI_TABAN)
    bir = d.demirbas_marka_listesi_birlesik(["Samsung", "samsung", "HP", "Muhtelif"])
    check("kayit_koru", "Samsung" in bir and "HP" in bir, str(bir))
    check("muhtelif_tek", bir.count("Muhtelif") == 1, str(bir))
    check("fold_marka", d.demirbas_tr_casefold("Arçelik") == d.demirbas_tr_casefold("ARÇELİK"))


def test_eski_satir_metin():
    """Çıkarma katalogdan gizler; satır metni bağımsız (simülasyon)."""
    import demirbas as d

    reload(d)
    satir_esya = "Keson"
    satir_seri = "KE123456789012"
    bir = d.demirbas_katalog_birlesik(gizli=["Keson"])
    check("katalogda_yok", not any(x["esya"] == "Keson" for x in bir))
    check("satir_korundu", satir_esya == "Keson" and satir_seri.startswith("KE"))


def test_module_wire_urun():
    db = (ROOT / "demirbas.py").read_text(encoding="utf-8")
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("pref_ek", "demirbas_urunler_ek" in db)
    check("pref_gizli", "demirbas_urunler_gizli" in db)
    check("api_urun", "/api/demirbas-urun" in src)
    check("api_cikar", "/api/demirbas-urun-cikar" in src)
    check("api_geri", "/api/demirbas-urun-geri-getir" in src)
    check("api_marka", "/api/demirbas-markalar" in src)
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    check("fe_yeni", "__YENI__" in tjs and "demirbas-urun" in tjs)
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("fe_duzenle", "girisDemirbasDuzenlePanelAcKapa" in tjs and "Listeyi düzenle" in html)
    dem_block = tjs[tjs.find("/* === Demirbaşlar") :]
    check("fe_marka_api", "demirbas-markalar" in dem_block)
    check("fe_marka_ui_tercih_yok", "/faturalar/api/ui-tercih" not in dem_block)
    db = (ROOT / "db.py").read_text(encoding="utf-8")
    check("firma_pref_ensure", "ensure_firma_ui_preferences_table" in db)
    gr = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("firma_pref_get", "_demirbas_ui_pref_get_firma" in gr)


def main() -> int:
    test_tr_casefold_ve_urun()
    test_katalog_birlesik()
    test_kapsam_ve_seri_yeni()
    test_marka_taban()
    test_eski_satir_metin()
    test_module_wire_urun()
    print("---")
    if FAILS:
        print("FAILS", len(FAILS), FAILS)
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
