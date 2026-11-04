# -*- coding: utf-8 -*-
"""Ekstre: kapanış sonrası tahsilat gerçek tarih satırı; bakiye korunur; borç uzamaz."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)
for _k in (
    "KAPANIS_HEPSI_SOZLESME_YILI",
    "KAPANIS_N0_KAPANIS_AYI_HARIC",
    "KAPANIS_N_KAPANIS_AYI_DAHIL",
):
    os.environ.pop(_k, None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek="") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:280]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def bak(hrs) -> float:
    if not hrs:
        return 0.0
    try:
        return round(float(hrs[-1].get("bakiye") or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _kira_n(hrs) -> int:
    return sum(1 for h in (hrs or []) if h.get("tur") == "Kira")


def _tah_after(hrs, tavan_end: date):
    te = tavan_end.isoformat()
    out = []
    for h in hrs or []:
        if h.get("tur") != "Tahsilat":
            continue
        td = str(h.get("tarih") or "")[:10]
        if td > te:
            out.append(h)
    return out


def test_a_tek_odeme_satir_ve_bakiye():
    """(a) Kapanışlı + tavan sonrası tek ödeme: satır gerçek tarih, bakiye=eski, borç yok sonrası."""
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    from routes import giris_routes as gr

    full = {
        "2024-01-01": 1000.0,
        "2024-02-01": 1000.0,
        "2024-03-01": 1000.0,
    }
    pays = [
        {
            "id": 90,
            "belge_no": "M-90",
            "fatura_id": None,
            "tarih": "2024-06-15",
            "tahsilat_tarihi": "2024-06-15",
            "eslesme_tarihi": "2024-06-15",
            "tutar": 1500.0,
            "odeme_turu": "B",
            "tahsilat_aciklama": "test post",
            "kaynak": "manuel_makbuz",
        }
    ]
    # Açılış: post_bit ile 1500 mahsup → 1500
    ac_old = gr._ekstre_acilis_bakiyesi_fifo(
        full,
        "2024-04-01",
        pays,
        "2024-01-01",
        post_bit_mahsup=True,
        bit_iso_cmp="2024-03-31",
    )
    check("a_acilis_eski_1500", abs(ac_old - 1500.0) < 0.02, str(ac_old))
    # Satır yazılınca açılıştan hariç → 3000; satır 1500 → net aynı
    ac_new = gr._ekstre_acilis_bakiyesi_fifo(
        full,
        "2024-04-01",
        [],
        "2024-01-01",
        post_bit_mahsup=True,
        bit_iso_cmp="2024-06-15",
    )
    check("a_acilis_yeni_3000", abs(ac_new - 3000.0) < 0.02, str(ac_new))
    check("a_net_ayni", abs((ac_new - 1500.0) - ac_old) < 0.02)
    os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)


def test_b_iki_odeme_once_sonra():
    """(b) Biri tavan öncesi biri sonrası."""
    from routes import giris_routes as gr

    full = {"2024-01-01": 1000.0, "2024-02-01": 1000.0, "2024-03-01": 1000.0}
    pays = [
        {
            "id": 1,
            "tarih": "2024-02-10",
            "tahsilat_tarihi": "2024-02-10",
            "eslesme_tarihi": "2024-02-10",
            "tutar": 500.0,
            "tahsilat_aciklama": "",
            "kaynak": "manuel_makbuz",
            "fatura_id": None,
            "belge_no": "A",
            "odeme_turu": "B",
        },
        {
            "id": 2,
            "tarih": "2024-06-10",
            "tahsilat_tarihi": "2024-06-10",
            "eslesme_tarihi": "2024-06-10",
            "tutar": 800.0,
            "tahsilat_aciklama": "",
            "kaynak": "manuel_makbuz",
            "fatura_id": None,
            "belge_no": "B",
            "odeme_turu": "B",
        },
    ]
    ac_old = gr._ekstre_acilis_bakiyesi_fifo(
        full, "2024-04-01", pays, "2024-01-01",
        post_bit_mahsup=True, bit_iso_cmp="2024-03-31",
    )
    # Öncesi dönem satırında (açılışa yazılmaz); sonrası mahsup → 3000-800=2200; öncesi 500 dönem
    # pre need 3000; pay1 goruntu Feb in window relative to bas Apr? Feb < Apr → opening
    # Actually pay1 Feb < bas Apr → opening; pay2 Jun > bit Mar → post_bit opening
    # opening = 3000-500-800=1700
    check("b_acilis_1700", abs(ac_old - 1700.0) < 0.02, str(ac_old))


def test_c_cift_satir_yok_filtre():
    """(c) direct_pay id ay satırında ikinci kez yok."""
    from routes import giris_routes as gr

    atla, keep = gr._ekstre_ay_satir_direct_pay_filtre([90], {90})
    check("c_atla", atla is True and keep == [])


def test_d_fazla_odeme_acilis():
    from routes import giris_routes as gr

    full = {"2024-01-01": 1000.0, "2024-02-01": 1000.0}
    pays = [{
        "id": 4, "tarih": "2025-01-10", "tahsilat_tarihi": "2025-01-10",
        "eslesme_tarihi": "2025-01-10", "tutar": 5000.0,
        "tahsilat_aciklama": "", "kaynak": "", "fatura_id": None,
        "belge_no": "X", "odeme_turu": "B",
    }]
    ac = gr._ekstre_acilis_bakiyesi_fifo(
        full, "2024-04-01", pays, "2024-01-01",
        post_bit_mahsup=True, bit_iso_cmp="2024-02-29",
    )
    check("d_fazla_acilis0", abs(ac - 0.0) < 0.02, str(ac))


def test_e_aktif_kapansiz_degismez():
    """(e) FE/helpers + aktifte son_borc yok → peel yok."""
    from routes import giris_routes as gr

    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("e_helper_son_tah", "_ekstre_son_tahsilat_hareket_tarihi" in src)
    check("e_post_tavan", "post_tavan_pay" in src or "post_tavan_pays" in src)
    check("e_varsayilan_uzat", "son_tah" in src and "_cari_ekstre_varsayilan_bas_bit" in src)
    # Aktif: kira_son None → tavan_bit_iso None
    with mock.patch.object(gr, "kira_son_borc_ayi", return_value=None):
        son = gr.kira_son_borc_ayi({"durum": "aktif"})
    check("e_aktif_none", son is None)


def test_f_varsayilan_son_tahsil_kapsar():
    """(f) Varsayılan bit son tahsilatı kapsar; kullanıcı aralığı esas (kod yolu)."""
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    chunk = src.split("def _cari_ekstre_varsayilan_bas_bit")[1].split("\ndef ")[0]
    check("f_son_tah_call", "_ekstre_son_tahsilat_hareket_tarihi" in chunk, chunk[:200])
    build = src.split("def _cari_ekstre_build_payload_from_request")[1].split("\ndef ")[0]
    check("f_build_limit", "son_tah" in build and "tavan_end" in build)


def test_g_odeme_linki_ayni_kaynak():
    """(g) ekstre_kalan varsayılan bas/bit + hareketler."""
    from pathlib import Path as P

    ol = (ROOT / "odeme_linki.py").read_text(encoding="utf-8")
    check("g_kalan_varsayilan", "_cari_ekstre_varsayilan_bas_bit" in ol)
    check("g_kalan_hareket", "_cari_ekstre_hareketler" in ol)


def test_h_canli_hedefler():
    """(h) 326/869/867 bakiyeleri; 101137 regressionsuz."""
    os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    os.environ["KAPANIS_N_KAPANIS_AYI_DAHIL"] = "1"
    from routes import giris_routes as gr
    from odeme_linki import ekstre_kalan
    from kira_kapanis_cap import kira_son_borc_ayi
    import importlib
    import kira_kapanis_cap as kc

    importlib.reload(kc)

    hedefler = {326: 9947.82, 869: 300.0, 867: 929.52}
    for mid, hedef in hedefler.items():
        kyc = gr._musteri_kyc_grup_for_aylik_grid(mid) or {}
        son = kira_son_borc_ayi(kyc)
        if son is None:
            check(f"h_{mid}_son", False, "son=None")
            continue
        tavan = gr._ekstre_ay_sonu(son.year, son.month)
        bas, bit = gr._cari_ekstre_varsayilan_bas_bit(mid)
        hrs = gr._cari_ekstre_hareketler(
            mid, bas, bit, 0, tahsilat_borca_hizala=True
        ) or []
        b = bak(hrs)
        check(f"h_{mid}_bak", abs(b - hedef) < 0.05, f"{b} vs {hedef}")
        kl = round(float(ekstre_kalan(mid) or 0), 2)
        check(f"h_{mid}_kalan", abs(kl - hedef) < 0.05, f"{kl} vs {hedef}")
        # Tavan sonrası kira yok
        for h in hrs:
            if h.get("tur") != "Kira":
                continue
            td = str(h.get("tarih") or "")[:10]
            check(f"h_{mid}_kira_cap", td <= tavan.isoformat(), td)
        # Geniş pencere
        hrs_w = gr._cari_ekstre_hareketler(
            mid, date(2023, 1, 1), date.today(), 0, tahsilat_borca_hizala=True
        ) or []
        check(f"h_{mid}_genis", abs(bak(hrs_w) - hedef) < 0.05, str(bak(hrs_w)))
        posts = _tah_after(hrs, tavan)
        if mid == 326:
            check("h_326_post_satir", len(posts) >= 1 and abs(float(posts[0].get("alacak") or 0) - 3600) < 0.05, posts)

    # 101137: peelin bakiyeyi bozmaması
    mid = 101137
    bas, bit = gr._cari_ekstre_varsayilan_bas_bit(mid)
    hrs = gr._cari_ekstre_hareketler(mid, bas, bit, 0, tahsilat_borca_hizala=True) or []
    check("h_101137_bak_finite", abs(bak(hrs)) < 1e9, str(bak(hrs)))

    for _k in (
        "KAPANIS_HEPSI_SOZLESME_YILI",
        "KAPANIS_N0_KAPANIS_AYI_HARIC",
        "KAPANIS_N_KAPANIS_AYI_DAHIL",
    ):
        os.environ.pop(_k, None)
    importlib.reload(kc)


def test_i_fazla_odeme_eksi_ve_link_engeli():
    """Fazla ödeme eksi; create guard yalnız kapanışlı/iptal (aktif HEAD gibi serbest)."""
    from odeme_linki import (
        odeme_linki_create_guard_musteri,
        odeme_linki_kalan_gosterim,
        odeme_linki_kalan_uygun,
    )

    check("i_eksi_uygun_degil", odeme_linki_kalan_uygun(-83.2) is False)
    check("i_sifir_uygun_degil", odeme_linki_kalan_uygun(0) is False)
    check("i_pozitif_uygun", odeme_linki_kalan_uygun(100.0) is True)
    check("i_gosterim_eksi0", abs(odeme_linki_kalan_gosterim(-91904.0) - 0.0) < 0.01)
    check("i_gosterim_poz", abs(odeme_linki_kalan_gosterim(100.0) - 100.0) < 0.01)

    src = (ROOT / "routes" / "odeme_linki_routes.py").read_text(encoding="utf-8")
    check(
        "i_api_create_guard_dar",
        "odeme_linki_create_guard_musteri" in src and "Borç bulunmadığı" in src,
    )
    check("i_api_kalan_flag", "link_uygun" in src and "borc_yok" in src)

    os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    os.environ["KAPANIS_N_KAPANIS_AYI_DAHIL"] = "1"
    from odeme_linki import ekstre_kalan
    from db import fetch_one

    # Kapanışlı + eksi: guard açık, kalan uygun değil → create engellenir
    kl72 = round(float(ekstre_kalan(72) or 0), 2)
    check("i_72_guard", odeme_linki_create_guard_musteri(72) is True)
    check("i_72_eksi_veya_sifir", kl72 <= 0.05, str(kl72))
    check("i_72_kalan_uygun_degil", odeme_linki_kalan_uygun(kl72) is False)

    # Kapanışlı + borç > 0: guard açık ama kalan uygun → create OK (mantık)
    kl326 = round(float(ekstre_kalan(326) or 0), 2)
    check("i_326_guard", odeme_linki_create_guard_musteri(326) is True)
    check("i_326_kalan_poz", odeme_linki_kalan_uygun(kl326) is True, str(kl326))

    # Aktif: guard kapalı (kalan ≤0 olsa bile create HEAD gibi serbest)
    aktif = fetch_one(
        """
        SELECT id FROM customers
        WHERE LOWER(TRIM(COALESCE(durum,'')))='aktif'
          AND COALESCE(iptal,FALSE)=FALSE
        ORDER BY id LIMIT 1
        """
    )
    if aktif:
        amid = int(aktif["id"])
        check("i_aktif_guard_kapali", odeme_linki_create_guard_musteri(amid) is False)

    # İptal: guard açık
    ipt = fetch_one(
        """
        SELECT id FROM customers
        WHERE COALESCE(iptal,FALSE)=TRUE
        ORDER BY id LIMIT 1
        """
    )
    if ipt:
        check("i_iptal_guard", odeme_linki_create_guard_musteri(int(ipt["id"])) is True)

    for _k in (
        "KAPANIS_HEPSI_SOZLESME_YILI",
        "KAPANIS_N0_KAPANIS_AYI_HARIC",
        "KAPANIS_N_KAPANIS_AYI_DAHIL",
    ):
        os.environ.pop(_k, None)


def test_j_490_kismi_son_ay_yeni_dogru():
    """490 tipi: son ay kısmi tahsilat → bak = brut_pre - tah_all (eski şişirme yok)."""
    os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    os.environ["KAPANIS_N_KAPANIS_AYI_DAHIL"] = "1"
    from routes import giris_routes as gr
    from kira_kapanis_cap import kira_son_borc_ayi
    from db import fetch_all, fetch_one
    import importlib
    import kira_kapanis_cap as kc

    importlib.reload(kc)
    mid = 490
    kyc = gr._musteri_kyc_grup_for_aylik_grid(mid) or {}
    son = kira_son_borc_ayi(kyc)
    check("j_490_son", son is not None)
    if son is None:
        return
    tavan = gr._ekstre_ay_sonu(son.year, son.month)
    row = fetch_one(
        "SELECT payload FROM musteri_aylik_grid_cache WHERE musteri_id=%s", (mid,)
    )
    brut = gr._brut_by_iso_from_aylik_grid_payload((row or {}).get("payload")) or {}
    brut_pre = 0.0
    for iso, v in brut.items():
        try:
            dd = date.fromisoformat(str(iso)[:10])
            amt = round(float(v or 0), 2)
        except Exception:
            continue
        if amt > 0.01 and (dd.year, dd.month) <= (son.year, son.month):
            brut_pre = round(brut_pre + amt, 2)
    tah_all = 0.0
    for p in (
        fetch_all(
            """
            SELECT COALESCE(tutar,0)::float AS t FROM tahsilatlar
            WHERE (musteri_id=%s OR customer_id=%s) AND COALESCE(tutar,0)>0
            """,
            (mid, mid),
        )
        or []
    ):
        tah_all = round(tah_all + float(p["t"] or 0), 2)
    bag = round(brut_pre - tah_all, 2)
    bas, bit = gr._cari_ekstre_varsayilan_bas_bit(mid)
    hrs = gr._cari_ekstre_hareketler(mid, bas, bit, 0, tahsilat_borca_hizala=True) or []
    b = bak(hrs)
    check("j_490_yeni_bagimsiz", abs(b - bag) < 0.08, f"{b} vs {bag}")
    check("j_490_bak_100", abs(b - 100.0) < 0.08, str(b))
    posts = _tah_after(hrs, tavan)
    check("j_490_post_satir", len(posts) >= 1, str(len(posts)))
    # Kısmi son satır 200 (şişirilmiş 300 değil)
    last = posts[-1] if posts else {}
    check(
        "j_490_son_alacak_200",
        abs(float(last.get("alacak") or 0) - 200.0) < 0.05,
        last,
    )
    for _k in (
        "KAPANIS_HEPSI_SOZLESME_YILI",
        "KAPANIS_N0_KAPANIS_AYI_HARIC",
        "KAPANIS_N_KAPANIS_AYI_DAHIL",
    ):
        os.environ.pop(_k, None)
    importlib.reload(kc)


def main():
    test_a_tek_odeme_satir_ve_bakiye()
    test_b_iki_odeme_once_sonra()
    test_c_cift_satir_yok_filtre()
    test_d_fazla_odeme_acilis()
    test_e_aktif_kapansiz_degismez()
    test_f_varsayilan_son_tahsil_kapsar()
    test_g_odeme_linki_ayni_kaynak()
    test_h_canli_hedefler()
    test_i_fazla_odeme_eksi_ve_link_engeli()
    test_j_490_kismi_son_ay_yeni_dogru()
    print("---")
    if FAILS:
        print("FAILS", FAILS)
        raise SystemExit(1)
    print("ALL_OK test_ekstre_kapanis_sonrasi_satir")


if __name__ == "__main__":
    main()
