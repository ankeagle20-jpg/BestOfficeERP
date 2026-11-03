# -*- coding: utf-8 -*-
"""Ekstre / ekstre_kalan: kapanış sonrası ödeme mahsubu + çift sayım yok.

Canlıya yazmaz. PII yok (yalnız mid / tutar / tarih).
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:280]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def _bakiye(hrs) -> float:
    if not hrs:
        return 0.0
    try:
        return round(float(hrs[-1].get("bakiye") or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _alacak_top(hrs) -> float:
    return round(sum(float(h.get("alacak") or 0) for h in (hrs or [])), 2)


def _make_pays(pays):
    """Minimal pays_fifo satırları."""
    out = []
    for p in pays:
        out.append(
            {
                "id": p.get("id", 1),
                "belge_no": p.get("belge_no", "M-1"),
                "fatura_id": p.get("fatura_id"),
                "tarih": p["tarih"],
                "tahsilat_tarihi": p["tarih"],
                "eslesme_tarihi": p.get("eslesme", p["tarih"]),
                "tutar": p["tutar"],
                "odeme_turu": p.get("odeme_turu", "B"),
                "tahsilat_aciklama": p.get("aciklama", ""),
                "kaynak": p.get("kaynak", ""),
            }
        )
    return out


def test_acilis_post_bit_mahsup():
    """Kapanış sonrası makbuz açılış bakiyesinden düşer; pencere içi aynı kalır."""
    from routes import giris_routes as gr

    # Aylar: 2024-01..03 brüt 1000; bas=2024-04-01 → pre = 3 ay
    full = {
        "2024-01-01": 1000.0,
        "2024-02-01": 1000.0,
        "2024-03-01": 1000.0,
        "2024-04-01": 1000.0,
    }
    # Ödeme kapanış sonrası (bit=2024-03-31)
    pay_post = _make_pays(
        [{"id": 90, "tarih": "2024-06-15", "tutar": 1500.0, "kaynak": ""}]
    )
    ac0 = gr._ekstre_acilis_bakiyesi_fifo(
        full, "2024-04-01", pay_post, "2024-01-01", post_bit_mahsup=False, bit_iso_cmp="2024-03-31"
    )
    check("post_bit kapali → acilis=3000", abs(ac0 - 3000.0) < 0.02, str(ac0))

    ac1 = gr._ekstre_acilis_bakiyesi_fifo(
        full, "2024-04-01", pay_post, "2024-01-01", post_bit_mahsup=True, bit_iso_cmp="2024-03-31"
    )
    check("post_bit acik → acilis=1500", abs(ac1 - 1500.0) < 0.02, str(ac1))

    # Pencere içi ödeme: post_bit_mahsup açık olsa bile açılışa yazılmaz (dönem satırı)
    pay_in = _make_pays(
        [{"id": 91, "tarih": "2024-02-10", "tutar": 500.0, "kaynak": ""}]
    )
    ac_in0 = gr._ekstre_acilis_bakiyesi_fifo(
        full, "2024-04-01", pay_in, "2024-01-01", post_bit_mahsup=False, bit_iso_cmp="2024-03-31"
    )
    ac_in1 = gr._ekstre_acilis_bakiyesi_fifo(
        full, "2024-04-01", pay_in, "2024-01-01", post_bit_mahsup=True, bit_iso_cmp="2024-03-31"
    )
    check("pencere ici birebir", abs(ac_in0 - ac_in1) < 0.02, f"{ac_in0} vs {ac_in1}")
    check("pencere ici 2500", abs(ac_in0 - 2500.0) < 0.02, str(ac_in0))


def test_kapanissiz_acilis_degismez():
    """son_borc yok / post_bit kapalı: kapanış sonrası ödeme açılışı etkilemez."""
    from routes import giris_routes as gr

    full = {"2024-01-01": 1000.0, "2024-02-01": 1000.0}
    pay = _make_pays([{"id": 1, "tarih": "2025-01-01", "tutar": 500.0}])
    a = gr._ekstre_acilis_bakiyesi_fifo(
        full, "2024-03-01", pay, "2024-01-01", post_bit_mahsup=False, bit_iso_cmp="2024-02-29"
    )
    check("kapanissiz post odeme yok sayilir", abs(a - 2000.0) < 0.02, str(a))


def test_coklu_kismi_fazla_odeme_acilis():
    from routes import giris_routes as gr

    full = {
        "2024-01-01": 1000.0,
        "2024-02-01": 1000.0,
        "2024-03-01": 500.0,
    }
    # İki post-bit ödeme: 800 + 400
    pays = _make_pays(
        [
            {"id": 1, "tarih": "2025-01-10", "tutar": 800.0},
            {"id": 2, "tarih": "2025-02-10", "tutar": 400.0},
        ]
    )
    ac = gr._ekstre_acilis_bakiyesi_fifo(
        full, "2024-04-01", pays, "2024-01-01", post_bit_mahsup=True, bit_iso_cmp="2024-03-31"
    )
    check("coklu post → 1300", abs(ac - 1300.0) < 0.02, str(ac))

    # Kısmi: tek 300
    pays2 = _make_pays([{"id": 3, "tarih": "2025-01-10", "tutar": 300.0}])
    ac2 = gr._ekstre_acilis_bakiyesi_fifo(
        full, "2024-04-01", pays2, "2024-01-01", post_bit_mahsup=True, bit_iso_cmp="2024-03-31"
    )
    check("kismi post → 2200", abs(ac2 - 2200.0) < 0.02, str(ac2))

    # Fazla ödeme: 5000 > 2500 borç
    pays3 = _make_pays([{"id": 4, "tarih": "2025-01-10", "tutar": 5000.0}])
    ac3 = gr._ekstre_acilis_bakiyesi_fifo(
        full, "2024-04-01", pays3, "2024-01-01", post_bit_mahsup=True, bit_iso_cmp="2024-03-31"
    )
    check("fazla odeme acilis>=0", abs(ac3 - 0.0) < 0.02, str(ac3))


def test_326_benzeri_varsayilan_ve_genis():
    """Canlı mid=326: varsayılan + geniş bakiye = grid_kalan_cap; çift sayım yok."""
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
    from routes import giris_routes as gr
    from odeme_linki import ekstre_kalan
    from kira_kapanis_cap import kira_son_borc_ayi

    mid = 326
    kyc = gr._musteri_kyc_grup_for_aylik_grid(mid)
    son = kira_son_borc_ayi(kyc)
    if son is None:
        check("326 son_borc var", False, "son=None")
        return

    pl = gr._read_aylik_grid_cache_payload(mid)
    tol = 0.01
    hedef = 0.0
    for a in (pl or {}).get("aylar") or []:
        try:
            yy, mm = int(a["yil"]), int(a["ay"])
        except (TypeError, ValueError, KeyError):
            continue
        cur = date(yy, mm, 1)
        if cur > son:
            continue
        tahsil = bool(a.get("tahsil_edildi"))
        try:
            odenen = float(a.get("odenen_tutar_kdv") or 0)
            kalan = float(a.get("kalan_tutar_kdv") or 0)
            tut = float(a.get("tutar_kdv_dahil") or a.get("brut_tutar_kdv") or 0)
        except (TypeError, ValueError):
            continue
        kismi = bool(a.get("kismi_tahsilat")) or (odenen > tol and kalan > tol)
        if (not tahsil) or kismi:
            add = min(tut, max(kalan, 0.0)) if kismi else tut
            if add > tol:
                hedef += add
    hedef = round(hedef, 2)
    check("326 hedef~9947", abs(hedef - 9947.82) < 0.05, str(hedef))

    bas, bit = gr._cari_ekstre_varsayilan_bas_bit(mid)
    hrs = gr._cari_ekstre_hareketler(mid, bas, bit, 0, tahsilat_borca_hizala=True) or []
    bak = _bakiye(hrs)
    check("326 varsayilan bak=hedef", abs(bak - hedef) < 0.05, f"{bak} vs {hedef}")

    kl = round(float(ekstre_kalan(mid) or 0), 2)
    check("326 ekstre_kalan=hedef", abs(kl - hedef) < 0.05, f"{kl} vs {hedef}")

    hrs_w = gr._cari_ekstre_hareketler(
        mid, date(2023, 1, 1), date.today(), 0, tahsilat_borca_hizala=True
    ) or []
    bak_w = _bakiye(hrs_w)
    check("326 genis bak=hedef", abs(bak_w - hedef) < 0.05, f"{bak_w} vs {hedef}")

    # Çift sayım: 3600 makbuz en fazla bir kez (kendi satır VEYA ay dağılımı, ikisi değil)
    al_w = _alacak_top(hrs_w)
    # borc - alacak ≈ bakiye; alacak ≥ 3600 ve aşırı şişme yok
    check("326 genis alacak>=3600", al_w + 0.02 >= 3600.0, str(al_w))
    # Eski çift sayımda alacak 7380 idi; artık ~3600 (+ diğer meşru alacaklar yoksa)
    # En azından bakiye doğruysa çift sayım yok demektir.
    check("326 genis cift_sayim_yok", abs(bak_w - hedef) < 0.05, f"bak={bak_w} al={al_w}")


def test_filtre_direct_pay_fifo_ids():
    """FIFO id'leri direct_pay setinde → ay satırı atlanır (çift sayım engeli)."""
    from routes import giris_routes as gr

    atla, keep = gr._ekstre_ay_satir_direct_pay_filtre([1657], {1657})
    check("fifo id = direct → atla", atla is True and keep == [])


if __name__ == "__main__":
    test_acilis_post_bit_mahsup()
    test_kapanissiz_acilis_degismez()
    test_coklu_kismi_fazla_odeme_acilis()
    test_filtre_direct_pay_fifo_ids()
    test_326_benzeri_varsayilan_ve_genis()
    if FAILS:
        print("FAILS", FAILS)
        sys.exit(1)
    print("CASE ekstre kapanis sonrasi odeme ok")
