# -*- coding: utf-8 -*-
"""Demirbaş seri no + katalog + bayrak. Temiz ortam. DB yazmaz."""
from __future__ import annotations

import os
import re
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
_SERI = re.compile(r"^[A-Z]{2,4}[0-9]{12}$")


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + ek) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_bayrak():
    import demirbas as d

    reload(d)
    check("flag0_kapali", d.demirbas_tablosu_aktif() is False)
    check("flag0_mid867", d.demirbas_tablosu_aktif(867) is False)
    os.environ["DEMIRBAS_TABLOSU_MIDLER"] = "867"
    reload(d)
    check("liste867", d.demirbas_tablosu_aktif(867) is True)
    check("liste_baska", d.demirbas_tablosu_aktif(1) is False)
    os.environ["DEMIRBAS_TABLOSU"] = "1"
    reload(d)
    check("flag1_herkes", d.demirbas_tablosu_aktif(1) is True)
    os.environ.pop("DEMIRBAS_TABLOSU", None)
    os.environ.pop("DEMIRBAS_TABLOSU_MIDLER", None)
    reload(d)


def test_katalog():
    import demirbas as d

    fe = d.demirbas_katalog_fe()
    check("katalog_dolu", len(fe) >= 30, str(len(fe)))
    gruplar = {x["grup"] for x in fe}
    check("grup_calisma", "Çalışma Alanı" in gruplar)
    check("grup_ortak", any("Ortak" in g for g in gruplar))
    check("kapsam_ortak", d.demirbas_kapsam_for_esya("Yazıcı") == "ortak_alan")
    check("kapsam_oda", d.demirbas_kapsam_for_esya("Keson") == "oda_ici")
    check("diger_const", "Diğer" in d.DEMIRBAS_DIGER)


def test_seri_oneki_ve_uretim():
    import demirbas as d

    cases = [
        ("Keson", "KE"),
        ("Çöp kovası", "CK"),
        ("Cam yazı tahtası", "CYT"),
        ("Toplantı odası TV", "TOT"),
        ("Mini PC", "MP"),
        ("Masaüstü bilgisayar", "MB"),
        ("Açık raf", "AR"),
        ("X", "XX"),
    ]
    check("onek_4", d.demirbas_seri_oneki("Bir iki üç dört") == "BIUD")
    for esya, expect in cases:
        got = d.demirbas_seri_oneki(esya)
        check(f"onek_{esya}", got == expect, f"got={got} expect={expect}")

    # elle yazılan
    check("onek_elle", d.demirbas_seri_oneki("Özel raf ünitesi")[:2].isalpha())

    class FakeRng:
        def __init__(self):
            self.n = 0

        def randrange(self, n):
            self.n = (self.n + 1) % 10
            return self.n

    rng = FakeRng()
    s = d.demirbas_seri_no_uret("Çöp kovası", rng=rng)
    check("seri_regex", bool(_SERI.fullmatch(s)), s)
    check("seri_onek_ck", s.startswith("CK"), s[:4])

    # Türkçe
    for esya in ("Çalışma masası", "Yönetici masası", "Şişe", "İklim"):
        o = d.demirbas_seri_oneki(esya)
        check(f"tr_{esya}", bool(re.fullmatch(r"[A-Z]{2,4}", o)), o)

    # çakışma yeniden deneme
    seen = set()

    def exists(seri):
        if len(seen) < 3:
            seen.add(seri)
            return True
        return seri in seen

    s2 = d.demirbas_seri_no_uret_benzersiz("Keson", exists_fn=exists, rng=FakeRng())
    check("cakisma_ok", bool(_SERI.fullmatch(s2)), s2)


def test_ek2_siralama():
    import demirbas as d

    rows = [
        {"esya": "Yazıcı", "kapsam": "ortak_alan", "sira": 1},
        {"esya": "Keson", "kapsam": "oda_ici", "sira": 2},
        {"esya": "Monitör", "kapsam": "oda_ici", "sira": 3},
    ]
    ordered = d.demirbas_satirlari_ek2_sirali(rows)
    check("sirala_oda_once", ordered[0]["esya"] == "Keson" or ordered[0]["kapsam"] == "oda_ici")
    check("sirala_ortak_son", ordered[-1]["kapsam"] == "ortak_alan")


def test_module_wire():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("api_katalog", "/api/demirbas-katalog" in src)
    check("api_seri", "/api/demirbas-seri-no" in src)
    check("api_mid", "/demirbas" in src and "api_musteri_demirbas" in src)
    db = (ROOT / "db.py").read_text(encoding="utf-8")
    check("ensure_fn", "def ensure_musteri_demirbas_table" in db)
    check("ensure_not_init", "ensure_musteri_demirbas_table()" not in (ROOT / "db.py").read_text(encoding="utf-8").split("def ensure_musteri_demirbas_table")[0])
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("ui_section", "giris_demirbas_section" in html)
    check("ui_after_grup2", html.find("giris_grup2_btn") < html.find("giris_demirbas_section"))


def main() -> int:
    test_bayrak()
    test_katalog()
    test_seri_oneki_ve_uretim()
    test_ek2_siralama()
    test_module_wire()
    print("---")
    if FAILS:
        print("FAILS", len(FAILS), FAILS)
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
