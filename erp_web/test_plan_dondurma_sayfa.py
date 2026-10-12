# -*- coding: utf-8 -*-
"""Gercek giris sayfasi: dondurulan ay 3 yenileme ve gecikmeli turda 0 kalir. Canli DB yok."""
import test_grid_867_gercek as t

AY = (2026, 11)
ANAHTAR = "2026-11"


def _panel(s):
    return s.evaluate(
        """() => {
            var det = window.__tahsilatSatirYilAyDetay || {};
            var yil = det['2026'] || {};
            var row = yil['2026-11'];
            if (!row) return null;
            return row.aylik_tutar;
        }"""
    )


def _ek(s, ad):
    kart = t._kartlar(s).get("sozlesmeler-aylik-grid", {}).get(ANAHTAR) or {}
    t.check(ad + " grid dondurma 0", kart.get("metin") == "0.00", kart)
    panel = _panel(s)
    t.check(ad + " tahsilat panel dondurma 0", panel == 0, panel)


def main():
    eski_fx = t.fixture_867

    def fixture(senaryo="eski"):
        fx = eski_fx(senaryo)
        for a in fx["grid"]["cache"]["aylar"]:
            if (a["yil"], a["ay"]) == AY:
                a["tutar_kdv_dahil"] = 0.0
                a["brut_tutar_kdv"] = 0.0
                a["kalan_tutar_kdv"] = 0.0
                a["tahsil_edildi"] = False
                a["kismi_tahsilat"] = False
        fx["grid"]["cache"]["dondurma_var"] = True
        fx["grid"]["cache"]["dondurulan_aylar"] = ["2026-11"]
        return fx

    t.fixture_867 = fixture
    try:
        sonuc = t.kosu("bundle", None, "dondurma-sayfa", kart_degistir=False, senaryo="eski", ek_kontrol=_ek)
    finally:
        t.fixture_867 = eski_fx
    if sonuc[0] is None and not t.FAILS:
        print("SKIP sayfa")
        return
    if t.FAILS:
        print("FAIL", len(t.FAILS))
        for ad in t.FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("plan_dondurma_sayfa ok")


if __name__ == "__main__":
    main()
