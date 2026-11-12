# -*- coding: utf-8 -*-
"""Manuel makbuz düzenleme: AYLIK_TAH üretilmez / yeniden yazılmaz (yalnız PAY/FIFO).

Temiz ortam. Prod yazma yok. Mutasyon: eski TAH yolu sayaç>0.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_")):
        os.environ.pop(k, None)

from routes import faturalar_routes as fr  # noqa: E402

FAILS: list[str] = []
TAH_RE = re.compile(r"\|AYLIK_TAH\|(\d{4}-\d{2}-\d{2})\|")
PAY_RE = re.compile(r"\|AYLIK_PAY\|(\d{4}-\d{2}-\d{2})=([0-9.]+)\|")


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_taban_eski_tah_uretirdi():
    """(i) Eski davranış: markers helper TAH üretir (sayaç>0)."""
    ac = fr._aciklama_with_aylik_markers("Nisan 2026 Tahsilat N", ["2026-04-01"])
    n = len(TAH_RE.findall(ac))
    check("taban_tah_gt0", n > 0, str(n))
    check("taban_tah_n1", n == 1, str(n))


def test_yeniden_hesapla_manuel_tah_yok():
    """(ii) yeni: boş/eski açıklamalı manuel yenile → TAH=0, PAY var, ay allowlist."""
    eski = (
        "Nisan 2026 Tahsilat N|AYLIK_TAH|2026-04-01| |AYLIK_PAY|2026-04-01=23000.00|"
    )
    with patch(
        "routes.faturalar_routes._auto_allocate_oldest_unpaid_months",
        return_value=(["2026-04-01"], [("2026-04-01", 23000.0)]),
    ) as alloc:
        yeni = fr.yeniden_hesapla_makbuz_dagitim_aciklama(
            910,
            tutar=23000.0,
            tahsilat_tarihi="2026-05-06",
            aciklama_eski=eski,
            haric_tahsilat_id=3762,
            kaynak="manuel_makbuz",
            odeme_turu="nakit",
        )
        allow = (alloc.call_args.kwargs or {}).get("iso_allowlist")
    check("yeni_tah_yok", yeni is not None and "|AYLIK_TAH|" not in (yeni or ""), yeni)
    check("yeni_pay_var", yeni and "|AYLIK_PAY|2026-04-01=23000.00|" in yeni, yeni)
    check("yeni_duz_metin", yeni and "Nisan 2026 Tahsilat N" in yeni, yeni)
    check("allowlist_nisan", allow == ["2026-04-01"], allow)


def test_dolu_aciklama_eski_tah_dusur():
    """(iii) Dolu açıklamada FE birleşik TAH kalmaz / yeniden üretilmez."""
    birlesik = (
        "elle not |AYLIK_TAH|2026-04-01| |AYLIK_PAY|2026-04-01=23000.00|"
    )
    with patch(
        "routes.faturalar_routes._auto_allocate_oldest_unpaid_months",
        return_value=(["2026-04-01"], [("2026-04-01", 23000.0)]),
    ):
        yeni = fr.yeniden_hesapla_makbuz_dagitim_aciklama(
            910,
            tutar=23000.0,
            tahsilat_tarihi="2026-05-06",
            aciklama_eski=birlesik,
            haric_tahsilat_id=3762,
            kaynak="manuel_makbuz",
            odeme_turu="nakit",
        )
    check("dolu_tah_yok", yeni is not None and "|AYLIK_TAH|" not in (yeni or ""), yeni)
    check("dolu_pay_var", yeni and "23000.00" in (yeni or ""), yeni)
    check("dolu_elle", yeni and "elle not" in yeni, yeni)


def test_grid_toplu_degismez():
    """(iv) grid_toplu skip."""
    skip = fr.yeniden_hesapla_makbuz_dagitim_aciklama(
        1,
        tutar=100,
        tahsilat_tarihi="2026-08-01",
        aciklama_eski="x|AYLIK_TAH|2026-08-01|",
        kaynak="grid_toplu",
    )
    check("grid_skip", skip is None)


def test_banka_hala_tah():
    """(iv) banka_import hâlâ TAH yazar (değişmez)."""
    with patch(
        "routes.faturalar_routes._auto_allocate_oldest_unpaid_months",
        return_value=(["2026-06-01"], [("2026-06-01", 1000.0)]),
    ):
        yeni = fr.yeniden_hesapla_makbuz_dagitim_aciklama(
            1,
            tutar=1000.0,
            tahsilat_tarihi="2026-06-15",
            aciklama_eski="Banka tahsilat",
            kaynak="banka_import",
            odeme_turu="havale",
        )
    check("banka_tah_var", yeni and "|AYLIK_TAH|2026-06-01|" in yeni, yeni)
    check("banka_pay_var", yeni and "|AYLIK_PAY|2026-06-01=1000.00|" in yeni, yeni)


def test_guncelle_cagri_yollari():
    """tahsilat_guncelle / ekstre-guncelle manuel için yeniden_hesapla kullanır."""
    fat = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    i = fat.find("def tahsilat_guncelle")
    j = fat.find("\ndef tahsilat_hizmet_turu_guncelle", i)
    chunk = fat[i:j]
    check("guncelle_manuel_yeniden", 'kay_ex == "manuel_makbuz"' in chunk)
    check("guncelle_yeniden_cagri", "yeniden_hesapla_makbuz_dagitim_aciklama" in chunk)
    # manuel branch TAH helper yok (yeniden içinde şartlı)
    i2 = fat.find("def yeniden_hesapla_makbuz_dagitim_aciklama")
    j2 = fat.find("\ndef makbuz_aciklama_sadece_guncelle", i2)
    rh = fat[i2:j2]
    check("rh_manuel_plain", 'if kay == "manuel_makbuz"' in rh)
    giris = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    ig = giris.find("def api_ekstre_tahsilat_guncelle")
    jg = giris.find("\ndef api_ekstre_tahsilat_sil", ig)
    gchunk = giris[ig:jg]
    check("ekstre_manuel_dal", 'kay_ex != "manuel_makbuz"' in gchunk)
    check("ekstre_yeniden", "yeniden_hesapla_makbuz_dagitim_aciklama" in gchunk)


def test_legacy_save_dokunulmadi():
    """api_tahsilat_save: templates/tahsilat çağırıyor → deaktif edilmedi."""
    th = (ROOT / "routes" / "tahsilat_routes.py").read_text(encoding="utf-8")
    check("legacy_route_var", "def api_tahsilat_save" in th)
    tpl = (ROOT / "templates" / "tahsilat" / "index.html").read_text(encoding="utf-8")
    check("legacy_fe_cagiran", "API_TAHSILAT_SAVE" in tpl)
    # deaktif edilmemiş
    i = th.find("def api_tahsilat_save")
    j = th.find("\ndef ", i + 10)
    chunk = th[i:j]
    check("legacy_aktif", "return jsonify" in chunk and "410" not in chunk[:200])


def test_mutasyon():
    """Mutasyon: markers eklenince manuel yol TAH içerirse FAIL kapısı."""
    with patch(
        "routes.faturalar_routes._auto_allocate_oldest_unpaid_months",
        return_value=(["2026-04-01"], [("2026-04-01", 100.0)]),
    ):
        good = fr.yeniden_hesapla_makbuz_dagitim_aciklama(
            1,
            tutar=100.0,
            tahsilat_tarihi="2026-05-01",
            aciklama_eski="x|AYLIK_TAH|2026-04-01|",
            kaynak="manuel_makbuz",
        )
    bad = fr._aciklama_with_aylik_markers("x", ["2026-04-01"])
    check("mut_good_tah_yok", good is not None and "|AYLIK_TAH|" not in good)
    check("mut_bad_tah_var", "|AYLIK_TAH|" in bad)
    if good and "|AYLIK_TAH|" in good:
        check("mutasyon_kapi", False, "manuel yeniden TAH icermemeli")
    else:
        check("mutasyon_kapi", "|AYLIK_TAH|" in bad)


def test_literal_bs_n():
    fat = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    i = fat.find("def yeniden_hesapla_makbuz_dagitim_aciklama")
    j = fat.find("\ndef makbuz_aciklama_sadece_guncelle", i)
    chunk = fat[i:j]
    check("rh_literal_bs_n_yok", (chr(92) + "n") not in chunk)
    i2 = fat.find("def tahsilat_guncelle")
    j2 = fat.find("\ndef tahsilat_hizmet_turu_guncelle", i2)
    check("gun_literal_bs_n_yok", (chr(92) + "n") not in fat[i2:j2])


if __name__ == "__main__":
    test_taban_eski_tah_uretirdi()
    test_yeniden_hesapla_manuel_tah_yok()
    test_dolu_aciklama_eski_tah_dusur()
    test_grid_toplu_degismez()
    test_banka_hala_tah()
    test_guncelle_cagri_yollari()
    test_legacy_save_dokunulmadi()
    test_mutasyon()
    test_literal_bs_n()
    if FAILS:
        print("FAILED", len(FAILS), FAILS)
        sys.exit(1)
    print("ALL_OK", 0)
    sys.exit(0)
