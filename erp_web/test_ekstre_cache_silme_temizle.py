# -*- coding: utf-8 -*-
"""D2: silme sonrası ekstre API önbelleği (300 sn) temizliği envanteri.

9 silme yolunun her birinde silme sonrası _cari_ekstre_api_cache
boşalmalı. Birim: yardımcı davranışı; kaynak: her yol çağırıyor mu.
DB'ye yazma yok.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

from routes import giris_routes as gr  # noqa: E402

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_yardimci_bosaltir():
    gr._cari_ekstre_api_cache[("x", 867)] = {"ts": 1, "payload": {"a": 1}}
    gr._cari_ekstre_api_cache[("y", 363)] = {"ts": 1, "payload": {"b": 2}}
    gr._ekstre_api_cache_temizle()
    check("yardimci cache bosaltti", gr._cari_ekstre_api_cache == {}, gr._cari_ekstre_api_cache)
    gr._ekstre_api_cache_temizle()
    check("yardimci bosken de calisir", gr._cari_ekstre_api_cache == {})


# yol -> (dosya, silme cagrisindan sonra beklenen temizlik isareti)
YOLLAR = [
    ("faturalar tahsilat-sil", "routes/faturalar_routes.py",
     'silme_yolu="tahsilat-sil"', "_ekstre_api_cache_temizle"),
    ("tahsilat api delete", "routes/tahsilat_routes.py",
     'silme_yolu="tahsilat-delete"', "_ekstre_api_cache_temizle"),
    ("giris ekstre-tahsilat-sil", "routes/giris_routes.py",
     'silme_yolu="ekstre-tahsilat-sil"', "_ekstre_invalidate_after_change"),
    ("giris tahsilden-cikar", "routes/giris_routes.py",
     'silme_yolu="tahsilden-cikar"', "_ekstre_invalidate_after_change"),
    ("giris borclandir fatura-sil-cascade", "routes/giris_routes.py",
     'silme_yolu="fatura-sil-cascade"', "_ekstre_api_cache_temizle"),
    ("giris ekstre-fatura-sil-cascade", "routes/giris_routes.py",
     'silme_yolu="ekstre-fatura-sil-cascade"', "_ekstre_invalidate_after_change"),
    ("faturalar toplu fatura-sil", "routes/faturalar_routes.py",
     'silme_yolu="fatura-toplu-sil-cascade"', "_ekstre_api_cache_temizle"),
    ("banka eslestirme iptal", "routes/banka_routes.py",
     'silme_yolu="banka-eslestirme-iptal"', "_ekstre_api_cache_temizle"),
    ("giris ekstre-kayit-donustur", "routes/giris_routes.py",
     'silme_yolu="ekstre-kayit-donustur"', "_ekstre_invalidate_after_change"),
]


def _temizlik_silmeden_sonra_mi(src, silme_isaret, temizlik_isaret):
    """Silme çağrısının bulunduğu fonksiyonda, çağrıdan SONRA temizlik var mı.

    Kaba ama etkili: silme işaretinin son geçtiği yerden sonraki 4000
    karakter içinde temizlik işareti aranır (aynı handler gövdesi).
    """
    i = src.rfind(silme_isaret)
    if i < 0:
        return False
    return temizlik_isaret in src[i:i + 6000]


def test_yol_envanteri():
    for ad, dosya, silme_isaret, temizlik_isaret in YOLLAR:
        src = (ROOT / dosya).read_text(encoding="utf-8")
        check(f"{ad}: silme ortak fonksiyonda", silme_isaret in src)
        check(f"{ad}: silme sonrasi ekstre temizligi",
              _temizlik_silmeden_sonra_mi(src, silme_isaret, temizlik_isaret))


def test_yardimci_tek_merkez():
    src = (ROOT / "routes/giris_routes.py").read_text(encoding="utf-8")
    check("yardimci tanimli", "def _ekstre_api_cache_temizle" in src)
    check("invalidate de cache temizler", "_cari_ekstre_api_cache.clear()" in src)


def main():
    test_yardimci_bosaltir()
    test_yol_envanteri()
    test_yardimci_tek_merkez()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
