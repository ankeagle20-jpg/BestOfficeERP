# -*- coding: utf-8 -*-
"""Hibrit yılda dahil Kaydet: ≤0,01 TL aynı, ≥0,02 TL 409. Canlı yazma yok."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

from routes import giris_routes as gr  # noqa: E402

FAILS = []
# 883 benzeri: net 52.000, matrah 48.000 → tutar_kdv_dahil 109.600
HIBRIT = {
    "tutar_kdv_dahil": 109600,
    "giris_tip": "hibrit",
    "giris_tutar": None,
    "hibrit_toplam": 100000,
    "hibrit_net": 52000,
    "hibrit_banka": 48000,
}


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:400]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def main():
    check("para_ayni 0", gr._reel_para_ayni(109600, 109600) is True)
    check("para_ayni 0.01", gr._reel_para_ayni(109600, 109600.01) is True)
    check("para_ayni -0.01", gr._reel_para_ayni(109600, 109599.99) is True)
    check("para_ayni 0.02 degil", gr._reel_para_ayni(109600, 109600.02) is False)
    check("para_ayni -0.02 degil", gr._reel_para_ayni(109600, 109599.98) is False)

    k0, _ = gr._reel_upsert_karar(HIBRIT, "dahil", 109600, 109600, None, None, None)
    check("dahil tam ayni korunur", k0 == "oldugu_gibi", k0)
    k1, _ = gr._reel_upsert_karar(HIBRIT, "dahil", 109600.01, 109600.01, None, None, None)
    check("dahil +0.01 korunur", k1 == "oldugu_gibi", k1)
    k2, _ = gr._reel_upsert_karar(HIBRIT, "dahil", 109599.99, 109599.99, None, None, None)
    check("dahil -0.01 korunur", k2 == "oldugu_gibi", k2)
    k3, m3 = gr._reel_upsert_karar(HIBRIT, "dahil", 109600.02, 109600.02, None, None, None)
    check("dahil +0.02 409", k3 == "ret_409" and m3 == gr._REEL_HIBRIT_DAGITIM_MESAJ, (k3, m3))
    k4, m4 = gr._reel_upsert_karar(HIBRIT, "dahil", 109599.98, 109599.98, None, None, None)
    check("dahil -0.02 409", k4 == "ret_409" and m4 == gr._REEL_HIBRIT_DAGITIM_MESAJ, (k4, m4))
    k5, m5 = gr._reel_upsert_karar(HIBRIT, "dahil", 100000, 100000, None, None, None)
    check("dahil buyuk fark 409", k5 == "ret_409", (k5, m5))

    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    i = src.find("def _reel_para_ayni")
    j = src.find("\ndef ", i + 1)
    blok = src[i:j]
    check("tolerans <= 0.01", "<= 0.01" in blok and "< 0.01" not in blok.replace("<= 0.01", ""))
    print("ALLOW", os.environ.get("ALLOW_PROD_WRITE"))
    if FAILS:
        print("FAILS", ",".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
