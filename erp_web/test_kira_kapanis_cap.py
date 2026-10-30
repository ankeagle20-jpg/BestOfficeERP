# -*- coding: utf-8 -*-
"""kira_son_borc_ayi + yüzey cap (N set). Canlı yazma yok. PII yok."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_kira_son_borc_ayi_birim():
    os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
    from kira_kapanis_cap import (
        ay_kira_borc_dahil_mi,
        kapanis_ek_fingerprint,
        kira_borc_cap_exclusive,
        kira_son_borc_ayi,
    )

    # N=0, ay sonu kapanış
    r = {
        "durum": "pasif",
        "kapanis_tarihi": date(2025, 9, 30),
        "kapanis_sonrasi_borc_ay": 0,
    }
    check("n0_son", kira_son_borc_ayi(r) == date(2025, 9, 1), kira_son_borc_ayi(r))
    check("n0_excl", kira_borc_cap_exclusive(r) == date(2025, 10, 1))
    check("n0_dahil_sep", ay_kira_borc_dahil_mi(r, 2025, 9) is True)
    check("n0_haric_oct", ay_kira_borc_dahil_mi(r, 2025, 10) is False)

    # N=0, ay ortası
    r2 = dict(r, kapanis_tarihi=date(2025, 9, 15))
    check("n0_orta_son", kira_son_borc_ayi(r2) == date(2025, 9, 1))

    # N=3
    r3 = dict(r, kapanis_sonrasi_borc_ay=3)
    check("n3_son", kira_son_borc_ayi(r3) == date(2025, 12, 1), kira_son_borc_ayi(r3))
    check("n3_excl", kira_borc_cap_exclusive(r3) == date(2026, 1, 1))
    check("n3_dahil_dec", ay_kira_borc_dahil_mi(r3, 2025, 12) is True)
    check("n3_haric_jan", ay_kira_borc_dahil_mi(r3, 2026, 1) is False)

    # N=NULL Hepsi — bayrak KAPALI → eski (None)
    r_null = dict(r, kapanis_sonrasi_borc_ay=None)
    check("null_son_flag_off", kira_son_borc_ayi(r_null) is None)
    check("null_excl_flag_off", kira_borc_cap_exclusive(r_null) is None)
    check("null_dahil_far_flag_off", ay_kira_borc_dahil_mi(r_null, 2027, 5) is True)

    # aktif
    check("aktif_none", kira_son_borc_ayi({"durum": "aktif", "kapanis_tarihi": date(2025, 1, 1), "kapanis_sonrasi_borc_ay": 0}) is None)

    # kap boş
    check("kap_bos", kira_son_borc_ayi({"durum": "pasif", "kapanis_tarihi": None, "kapanis_sonrasi_borc_ay": 0}) is None)

    # fingerprint NULL ≠ 0; bayrak kapalı → H
    check("fp_h", kapanis_ek_fingerprint(None) == "H")
    check("fp_0", kapanis_ek_fingerprint(0) == "0")
    check("fp_ayri", kapanis_ek_fingerprint(None) != kapanis_ek_fingerprint(0))


def test_hepsi_sozlesme_yili_bayrak():
    """KAPANIS_HEPSI_SOZLESME_YILI=1: sözleşme yılı sonu; N dolu değişmez."""
    os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
    try:
        import importlib
        import kira_kapanis_cap as kc

        importlib.reload(kc)
        from kira_kapanis_cap import (
            hepsi_sozlesme_yili_son_borc_ayi,
            kapanis_ek_fingerprint,
            kira_son_borc_ayi,
        )

        # giriş Ağu-2023, kap Eyl-2025 → Tem-2026
        r = {
            "durum": "pasif",
            "kapanis_tarihi": date(2025, 9, 30),
            "kapanis_sonrasi_borc_ay": None,
            "sozlesme_tarihi": date(2023, 8, 1),
        }
        check("hepsi_agu_eyl", kira_son_borc_ayi(r) == date(2026, 7, 1), kira_son_borc_ayi(r))

        # kap ayı yıl sonu (Tem-2025) → ek borç yok
        r_ys = dict(r, kapanis_tarihi=date(2025, 7, 15))
        check("hepsi_yil_sonu", kira_son_borc_ayi(r_ys) == date(2025, 7, 1), kira_son_borc_ayi(r_ys))

        # 3 ay sonra çıkış (Ağu..Eki) → ilk yıl Tem-2024
        r3 = dict(r, kapanis_tarihi=date(2023, 10, 10))
        check("hepsi_3ay", kira_son_borc_ayi(r3) == date(2024, 7, 1), kira_son_borc_ayi(r3))

        # çıkış tam yıl dönümünde (giriş ayı Ağu-2025) → yeni dönem Tem-2026
        r_don = dict(r, kapanis_tarihi=date(2025, 8, 1))
        check("hepsi_donum", kira_son_borc_ayi(r_don) == date(2026, 7, 1), kira_son_borc_ayi(r_don))

        # ay ortası kapanış
        r_orta = dict(r, kapanis_tarihi=date(2025, 9, 15))
        check("hepsi_orta", kira_son_borc_ayi(r_orta) == date(2026, 7, 1))

        # giriş eksik → eski (None)
        r_eksik = {
            "durum": "pasif",
            "kapanis_tarihi": date(2025, 9, 30),
            "kapanis_sonrasi_borc_ay": None,
            "id": 999001,
        }
        check("hepsi_giris_eksik", kira_son_borc_ayi(r_eksik) is None)

        # rent_start yedeği
        r_rent = {
            "durum": "pasif",
            "kapanis_tarihi": date(2025, 9, 30),
            "kapanis_sonrasi_borc_ay": None,
            "rent_start_date": date(2023, 8, 1),
        }
        check("hepsi_rent", kira_son_borc_ayi(r_rent) == date(2026, 7, 1))

        # N=0 / N=3 değişmedi
        check(
            "hepsi_n0",
            kira_son_borc_ayi(dict(r, kapanis_sonrasi_borc_ay=0)) == date(2025, 9, 1),
        )
        check(
            "hepsi_n3",
            kira_son_borc_ayi(dict(r, kapanis_sonrasi_borc_ay=3)) == date(2025, 12, 1),
        )

        check("fp_h12", kapanis_ek_fingerprint(None) == "H12")
        check("fp_h12_n0", kapanis_ek_fingerprint(0) == "0")
        check(
            "fn_pure",
            hepsi_sozlesme_yili_son_borc_ayi(date(2023, 8, 1), date(2025, 9, 30))
            == date(2026, 7, 1),
        )
    finally:
        os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
        import importlib
        import kira_kapanis_cap as kc

        importlib.reload(kc)


def test_effective_bitis_uses_helper():
    from routes import giris_routes as gr

    kyc = {
        "durum": "pasif",
        "kapanis_tarihi": date(2025, 9, 30),
        "kapanis_sonrasi_borc_ay": 0,
        "sozlesme_bitis": date(2027, 8, 31),
    }
    bit = gr._aylik_grid_effective_bitis(kyc, date(2027, 8, 31))
    check("eff_n0", bit == date(2025, 10, 1), bit)

    kyc_n = dict(kyc, kapanis_sonrasi_borc_ay=None)
    bit_n = gr._aylik_grid_effective_bitis(kyc_n, date(2027, 8, 31))
    check("eff_null_eski", bit_n == date(2027, 8, 31), bit_n)

    kyc_a = dict(kyc, durum="aktif")
    check("eff_aktif", gr._aylik_grid_effective_bitis(kyc_a, date(2027, 8, 31)) == date(2027, 8, 31))


def test_contract_core_n0_ay_sayisi():
    from routes import giris_routes as gr

    kyc = {
        "durum": "pasif",
        "kapanis_tarihi": date(2025, 9, 30),
        "kapanis_sonrasi_borc_ay": 0,
        "sozlesme_tarihi": date(2025, 8, 1),
        "sozlesme_bitis": date(2027, 12, 31),
        "aylik_kira": 1000.0,
        "kira_nakit": False,
        "kdv_oran": 20,
    }
    core = gr._aylik_grid_contract_core(kyc, {})
    check("core_ok", core is not None)
    if not core:
        return
    # Ağu+Eyl = 2 ay (baş=2025-08, bit excl=2025-10-01)
    check("core_ay_n0", int(core.get("ay_sayisi") or 0) == 2, core.get("ay_sayisi"))

    kyc_null = dict(kyc, kapanis_sonrasi_borc_ay=None)
    core_n = gr._aylik_grid_contract_core(kyc_null, {})
    check("core_null_daha_uzun", int((core_n or {}).get("ay_sayisi") or 0) > 2, (core_n or {}).get("ay_sayisi"))


def test_firma_ozet_toplam_cap():
    from routes import giris_routes as gr

    kyc = {
        "durum": "pasif",
        "kapanis_tarihi": date(2025, 9, 30),
        "kapanis_sonrasi_borc_ay": 0,
        "sozlesme_tarihi": date(2025, 8, 1),
        "sozlesme_bitis": date(2027, 12, 31),
        "aylik_kira": 1000.0,
        "kira_nakit": True,
        "kdv_oran": 20,
    }
    # tahsil yok → Ağu+Eyl = 2 ay × 1000
    with mock.patch.object(gr, "firma_ozet_aylik_grid_hucre_kdv_dahil", return_value=1000.0), mock.patch.object(
        gr, "_reel_ay_key_tutar_map_musteri", return_value={}
    ), mock.patch.object(gr, "_aylik_tahsil_edilen_aylar_set_normalized", return_value=set()):
        t, n = gr.firma_ozet_toplam_borc_ve_geciken_ay(
            900001, 2026, 10, {}, kyc, {}, tahsil_n_override=set(), skip_disk_cache_for_months=True
        )
    check("fo_n0_ay", n == 2, n)
    check("fo_n0_tl", abs(t - 2000.0) < 0.01, t)

    kyc_null = dict(kyc, kapanis_sonrasi_borc_ay=None)
    with mock.patch.object(gr, "firma_ozet_aylik_grid_hucre_kdv_dahil", return_value=1000.0), mock.patch.object(
        gr, "_reel_ay_key_tutar_map_musteri", return_value={}
    ):
        t2, n2 = gr.firma_ozet_toplam_borc_ve_geciken_ay(
            900001, 2026, 10, {}, kyc_null, {}, tahsil_n_override=set(), skip_disk_cache_for_months=True
        )
    check("fo_null_daha_cok", n2 > 2, n2)


def test_firma_ozet_cache_payload_cap():
    from routes import giris_routes as gr

    payload = {
        "aylar": [
            {"yil": 2025, "ay": 8, "tutar_kdv_dahil": 100, "brut_tutar_kdv": 100, "tahsil_edildi": False, "kalan_tutar_kdv": 100},
            {"yil": 2025, "ay": 9, "tutar_kdv_dahil": 100, "brut_tutar_kdv": 100, "tahsil_edildi": False, "kalan_tutar_kdv": 100},
            {"yil": 2025, "ay": 10, "tutar_kdv_dahil": 100, "brut_tutar_kdv": 100, "tahsil_edildi": False, "kalan_tutar_kdv": 100},
            {"yil": 2025, "ay": 11, "tutar_kdv_dahil": 100, "brut_tutar_kdv": 100, "tahsil_edildi": False, "kalan_tutar_kdv": 100},
        ]
    }
    kyc = {
        "durum": "pasif",
        "kapanis_tarihi": date(2025, 9, 30),
        "kapanis_sonrasi_borc_ay": 0,
        "aylik_kira": 100,
    }
    with mock.patch.object(gr, "_firma_ozet_cache_payload_usable", return_value=True), mock.patch.object(
        gr, "_firma_ozet_payload_is_placeholder_dominated", return_value=False
    ):
        oz = gr._firma_ozet_ozet_from_grid_cache_payload(payload, 2025, 11, kyc_for_grid=kyc)
    check("cache_cap_ay", oz and oz.get("geciken_ay") == 2, oz)
    check("cache_cap_tl", oz and abs(float(oz.get("toplam_borc") or 0) - 200) < 0.01, oz)

    with mock.patch.object(gr, "_firma_ozet_cache_payload_usable", return_value=True), mock.patch.object(
        gr, "_firma_ozet_payload_is_placeholder_dominated", return_value=False
    ):
        oz_n = gr._firma_ozet_ozet_from_grid_cache_payload(
            payload, 2025, 11, kyc_for_grid=dict(kyc, kapanis_sonrasi_borc_ay=None)
        )
    check("cache_null_4", oz_n and oz_n.get("geciken_ay") == 4, oz_n)


def test_ekstre_ekran_n_set():
    from routes import giris_routes as gr

    kyc = {
        "durum": "pasif",
        "kapanis_tarihi": date(2025, 9, 15),
        "kapanis_sonrasi_borc_ay": 2,
        "sozlesme_tarihi": date(2024, 1, 1),
    }
    with mock.patch.object(gr, "_musteri_kyc_grup_for_aylik_grid", return_value=kyc):
        bas, bit = gr._cari_ekstre_ekran_varsayilan_bas_bit(1, bugun=date(2026, 10, 30))
    # N=2 → Kasım 2025 sonu
    check("ekran_n2_bit", bit == date(2025, 11, 30), bit)
    check("ekran_n2_bas", bas == date(2025, 1, 1), bas)

    kyc0 = dict(kyc, kapanis_sonrasi_borc_ay=0)
    with mock.patch.object(gr, "_musteri_kyc_grup_for_aylik_grid", return_value=kyc0):
        _, bit0 = gr._cari_ekstre_ekran_varsayilan_bas_bit(1, bugun=date(2026, 10, 30))
    check("ekran_n0_bit", bit0 == date(2025, 9, 30), bit0)


def test_cache_invalidation_hook():
    from routes import giris_routes as gr

    src = Path(gr.__file__).read_text(encoding="utf-8")
    check("fn_kapanis_ufku", "def kapanis_borc_ufku_degisti(" in src)
    check("durum_cagirir", "kapanis_borc_ufku_degisti(int(mid))" in src)
    check("kaydet_cagirir", "kapanis_borc_ufku_degisti(int(musteri_id))" in src)
    check("fp_helper", "_kapanis_ek_fingerprint" in src)


def test_sadece_borclu_cap_ozet():
    """Sadece borçlular istemci filtresi: cap sonrası borcu 0 olan satır gizlenir."""
    from routes.faturalar_routes import _firma_ozet_client_filtre_uygula

    satirlar = [
        {"musteri_id": 1, "toplam_borc": 0.0, "aylik_tutar": 1, "firma_adi": "A", "durum_etiket": "Pasif", "hizmet_turu": "S", "liste_ara_blob": "", "grup2": ""},
        {"musteri_id": 2, "toplam_borc": 500.0, "aylik_tutar": 1, "firma_adi": "B", "durum_etiket": "Pasif", "hizmet_turu": "S", "liste_ara_blob": "", "grup2": ""},
    ]
    filt = _firma_ozet_client_filtre_uygula(
        satirlar, q="", grup2=[], hizmet=[], durum="", oda=None, yillar=None, sadece_borclu=True
    )
    ids = [r["musteri_id"] for r in filt]
    check("sadece_borclu_0_gizle", ids == [2], ids)


def test_script_dry_run_exists():
    p = ROOT / "scripts" / "kapanis_hepsi_yeniden_hesapla.py"
    check("script_var", p.is_file())
    txt = p.read_text(encoding="utf-8")
    check("script_dry", "DRY-RUN" in txt and "ALLOW_PROD_WRITE" in txt)
    check("script_tahsilat_degisti", "tahsilat_degisti" in txt)


def main():
    test_kira_son_borc_ayi_birim()
    test_hepsi_sozlesme_yili_bayrak()
    test_effective_bitis_uses_helper()
    test_contract_core_n0_ay_sayisi()
    test_firma_ozet_toplam_cap()
    test_firma_ozet_cache_payload_cap()
    test_ekstre_ekran_n_set()
    test_cache_invalidation_hook()
    test_sadece_borclu_cap_ozet()
    test_script_dry_run_exists()
    if FAILS:
        print("FAILED", len(FAILS), FAILS)
        sys.exit(1)
    print("ALL_OK")


if __name__ == "__main__":
    main()
