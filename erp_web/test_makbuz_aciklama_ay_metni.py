# -*- coding: utf-8 -*-
"""Makbuz aciklama: hangi aylarin odemesi Turkce metni.

Temiz ortam birim testleri. Prod yazma yok.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_")):
        os.environ.pop(k, None)

from routes import faturalar_routes as fr  # noqa: E402

FAILS: list[str] = []
PAY_RE = re.compile(r"\|AYLIK_PAY\|(\d{4}-\d{2}-\d{2})=")
TAH_RE = re.compile(r"\|AYLIK_TAH\|")


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_taban_eski_bos():
    """(i) Eski yol: aciklama strip + PAY; otomatik ay metni yok (taban sayac=0)."""
    pay = [("2026-03-01", 1000.0)]
    ac = fr._strip_aylik_dagitim_markers("")
    if pay:
        ac = fr._aciklama_with_aylik_pay_tokens(ac, pay)
    # Metin yok — sadece PAY
    plain = fr._strip_aylik_dagitim_markers(ac)
    check("taban_plain_bos", plain == "", plain)
    check("taban_pay_var", len(PAY_RE.findall(ac)) == 1, ac)
    # Mutasyon: yeni helper metin uretir (sayac>0)
    metin = fr._makbuz_ay_odeme_metni(pay)
    check("mutasyon_metin_gt0", len(metin) > 0, metin)


def test_senaryolar():
    """(ii) tek ay / art arda 3 / kesintili / kismi / fazla / hepsi ileri."""
    # tek ay
    m1 = fr._makbuz_ay_odeme_metni([("2026-04-01", 10000.0)])
    check("tek_ay", m1 == "Nisan 2026 kira bedeli tahsilatı", m1)

    # art arda 3 ay (Aralik-Subat yil gecisi)
    m3 = fr._makbuz_ay_odeme_metni(
        [
            ("2026-12-01", 1000.0),
            ("2027-01-01", 1000.0),
            ("2027-02-01", 1000.0),
        ]
    )
    check(
        "art_arda_3",
        m3 == "Aralık 2026 – Şubat 2027 kira bedeli tahsilatı (3 ay)",
        m3,
    )

    # kesintili
    mk = fr._makbuz_ay_odeme_metni(
        [("2026-03-01", 1000.0), ("2026-05-01", 1000.0)]
    )
    check(
        "kesintili",
        mk == "Mart 2026, Mayıs 2026 kira bedeli tahsilatı",
        mk,
    )

    # kismi
    mp = fr._makbuz_ay_odeme_metni(
        [{"iso": "2026-03-01", "tutar": 12000.0, "kalan": 25000.0}]
    )
    check("kismi", "Mart 2026 (kısmi: 12.000,00 TL)" == mp, mp)

    # fazla
    mf = fr._makbuz_ay_odeme_metni(
        [("2026-03-01", 1000.0)], fazla_tl=500.0
    )
    check("fazla_icinde", "Fazla: 500,00 TL ileri ödeme" in mf, mf)
    check("fazla_ay_var", "Mart 2026 kira bedeli tahsilatı" in mf, mf)

    # acik ay yok — tamamı ileri
    mi = fr._makbuz_ay_odeme_metni([], fazla_tl=7500.0)
    check("hepsi_ileri", mi == "Fazla: 7.500,00 TL ileri ödeme", mi)


def test_elle_ezilmez_ve_isaret():
    """(iii)(iv) elle metin korunur; PAY eklenir; TAH yok."""
    elle = "özel not kullanıcı"
    pay = [("2026-06-01", 2000.0), ("2026-07-01", 500.0)]
    plain = fr._strip_aylik_dagitim_markers(elle)
    ac = fr._aciklama_with_aylik_pay_tokens(plain, pay)
    check("elle_korunur", elle in ac, ac)
    check("elle_tah_yok", not TAH_RE.search(ac), ac)
    check("elle_pay_2", len(PAY_RE.findall(ac)) == 2, ac)
    # Oneri metni PAY ile carpismaz (ayri alan)
    oneri = fr._makbuz_ay_odeme_metni(pay, kalan_map={"2026-07-01": 1000.0})
    ac2 = fr._aciklama_with_aylik_pay_tokens(oneri, pay)
    check("oneri_plain", oneri in fr._strip_aylik_dagitim_markers(ac2), ac2)
    check("oneri_tah_yok", "|AYLIK_TAH|" not in ac2, ac2)
    check("oneri_pay", len(PAY_RE.findall(ac2)) == 2, ac2)


def test_duzenle_yeniden_hesapla_metin_koru():
    """(vi) manuel yeniden_hesapla duz metni bozmaz; TAH yok."""
    from unittest.mock import patch

    eski = (
        "Mart 2026 kira bedeli tahsilatı "
        "|AYLIK_PAY|2026-03-01=1000.00|"
    )
    with patch(
        "routes.faturalar_routes._auto_allocate_oldest_unpaid_months",
        return_value=(["2026-03-01"], [("2026-03-01", 1000.0)]),
    ):
        yeni = fr.yeniden_hesapla_makbuz_dagitim_aciklama(
            55,
            tutar=1000.0,
            tahsilat_tarihi="2026-04-01",
            aciklama_eski=eski,
            haric_tahsilat_id=1,
            kaynak="manuel_makbuz",
            odeme_turu="nakit",
        )
    check("duzenle_tah_yok", yeni is not None and "|AYLIK_TAH|" not in (yeni or ""), yeni)
    check(
        "duzenle_metin",
        yeni is not None and "Mart 2026 kira bedeli tahsilatı" in (yeni or ""),
        yeni,
    )
    check("duzenle_pay", yeni is not None and "|AYLIK_PAY|" in (yeni or ""), yeni)


def test_grid_toplu_degismez():
    """(vii) grid_toplu yolu None (degismez)."""
    skip = fr.yeniden_hesapla_makbuz_dagitim_aciklama(
        1,
        tutar=100,
        tahsilat_tarihi="2026-08-01",
        aciklama_eski="x|AYLIK_TAH|2026-08-01|",
        kaynak="grid_toplu",
    )
    check("grid_skip", skip is None)


def test_fe_kaynak():
    """FE: helper + dokundu + oneri wire; index/_t ayna; literal bs+n yok yeni blokta."""
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    idx = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    for src, label in ((tjs, "tjs"), (idx, "idx")):
        check(f"{label}_fn_metin", "function tahsilMakbuzAyOdemeMetni" in src)
        check(f"{label}_fn_fifo", "function tahsilMakbuzFifoAyKalemleri" in src)
        check(f"{label}_fn_oneri", "function tahsilMakbuzAciklamaOneriGuncelle" in src)
        check(f"{label}_dokundu", "tahsilatAciklamaDokunduIsaretle" in src)
        check(f"{label}_wire_giris", "tahsilMakbuzAciklamaOneriGuncelle('giris_tahsilat')" in src)
        check(f"{label}_wire_soz", "tahsilMakbuzAciklamaOneriGuncelle('sozlesme_tahsilat')" in src)
        # Duzenle dokundu
        i = src.find("function tahsilMakbuzFormuRaporSatirdanDoldur")
        chunk = src[i : i + 2200] if i >= 0 else ""
        check(f"{label}_duzenle_dokundu", "tahsilatAciklamaDokunduIsaretle(prefix)" in chunk)

    # Yeni helper govdelerinde literal tersbolu+n yok (sonraki fn'ye tasmasin)
    def _fn_body(src: str, name: str) -> str:
        key = "function " + name
        i = src.find(key)
        if i < 0:
            return ""
        j = src.find(chr(10) + "function ", i + len(key))
        return src[i:j] if j > i else src[i : i + 2500]

    for nm in (
        "tahsilMakbuzAyOdemeMetni",
        "tahsilMakbuzAciklamaOneriGuncelle",
        "tahsilMakbuzFifoAyKalemleri",
        "tahsilatAciklamaDokunduMu",
    ):
        chunk = _fn_body(tjs, nm)
        check("literal_bs_n_" + nm, (chr(92) + "n") not in chunk, nm)


def main() -> int:
    test_taban_eski_bos()
    test_senaryolar()
    test_elle_ezilmez_ve_isaret()
    test_duzenle_yeniden_hesapla_metin_koru()
    test_grid_toplu_degismez()
    test_fe_kaynak()
    print("---")
    if FAILS:
        print("FAILS", len(FAILS), FAILS)
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
