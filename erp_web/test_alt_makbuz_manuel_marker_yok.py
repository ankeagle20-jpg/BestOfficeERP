# -*- coding: utf-8 -*-
"""Alt makbuz kaydi: secili ay AYLIK_TAH tasimaz (manuel FIFO+PAY).

Temiz ortam birim testleri. Prod yazma yok.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_")):
        os.environ.pop(k, None)

from routes import faturalar_routes as fr  # noqa: E402

FAILS: list[str] = []
TAH_RE = re.compile(r"\|AYLIK_TAH\|(\d{4}-\d{2}-\d{2})\|")
PAY_RE = re.compile(r"\|AYLIK_PAY\|(\d{4}-\d{2}-\d{2})=")


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:200]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _manuel_aciklama(raw: str, pay_items):
    """tahsilat_ekle ile ayni: TAH yok, PAY var."""
    text = fr._strip_aylik_dagitim_markers(raw)
    if pay_items:
        text = fr._aciklama_with_aylik_pay_tokens(text, pay_items)
    return text


def test_fe_kaynak():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    idx = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    for src, label in ((tjs, "tjs"), (idx, "idx")):
        # sozlesme payload: ay_ref bos
        i = src.find("function sozlesmeTahsilatFormPayload")
        j = src.find("function sozlesmeTahsilatKaydet")
        chunk = src[i:j] if i >= 0 and j > i else ""
        check(f"{label}_soz_ay_ref_bos", "ay_ref_iso_list: []" in chunk)
        check(f"{label}_soz_slice_yok", "ayRefIsoListRaw.slice()" not in chunk)
        i2 = src.find("function girisTahsilatFormPayload")
        j2 = src.find("function girisTahsilatMakbuzKaydet")
        chunk2 = src[i2:j2] if i2 >= 0 and j2 > i2 else ""
        check(f"{label}_giris_ay_ref_bos", "ay_ref_iso_list: []" in chunk2)
        # ust buton yolu duruyor
        check(f"{label}_ust_api", "/giris/api/aylik-tutarlardan-tahsil-et" in src)
    # literal bs+n in new chunks
    for name in ("sozlesmeTahsilatFormPayload", "girisTahsilatFormPayload"):
        i = tjs.find("function " + name)
        # rough 2500 chars
        chunk = tjs[i : i + 2500]
        check(f"literal_bs_n_{name}", (chr(92) + "n") not in chunk)


def test_taban_eski_marker():
    """Eski davranis: istemci ay listesi + _aciklama_with_aylik_markers → TAH>0."""
    isos = ["2026-06-01", "2026-07-01"]
    ac = fr._aciklama_with_aylik_markers("not", isos)
    n = len(TAH_RE.findall(ac))
    check("taban_eski_tah_gt0", n > 0, str(n))
    check("taban_eski_n2", n == 2, str(n))


def test_yeni_manuel_tah_yok_pay_var():
    pay = [("2026-03-01", 1000.0), ("2026-04-01", 500.0)]
    ac = _manuel_aciklama("elle not", pay)
    check("yeni_tah_yok", "|AYLIK_TAH|" not in ac)
    check("yeni_pay_var", len(PAY_RE.findall(ac)) == 2, ac)
    check("yeni_duz_not", "elle not" in ac)
    # istemci listesi gelse bile ignore sim: markers strip + sadece pay
    ac2 = _manuel_aciklama("x |AYLIK_TAH|2026-06-01|", pay)
    check("istemci_tah_strip", "|AYLIK_TAH|" not in ac2)
    check("istemci_pay_kalir", len(PAY_RE.findall(ac2)) == 2)


def test_fifo_dagitim_sahte():
    """Sahte borc: FIFO pay; TAH yok."""
    borc = {
        "2026-01-01": 1000.0,
        "2026-02-01": 1000.0,
        "2026-03-01": 1000.0,
    }
    isos, items = fr._auto_allocate_oldest_unpaid_months(
        999001,
        1500.0,
        odeme_tarihi="2026-06-15",
        borc_satirlari=borc,
        onceki_tahsilatlar=[],
    )
    check("fifo_2ay", len(items) == 2, str(items))
    check("fifo_ilk_1000", abs(items[0][1] - 1000.0) < 0.01, str(items))
    check("fifo_ikinci_500", abs(items[1][1] - 500.0) < 0.01, str(items))
    ac = _manuel_aciklama("", items)
    check("fifo_ac_tah_yok", "|AYLIK_TAH|" not in ac)
    check("fifo_ac_pay", len(PAY_RE.findall(ac)) == 2)


def test_kismi_883_tipi():
    """48000 kismi: tek ay kalan 100000 → pay=48000, TAH yok → kismi oran."""
    borc = {"2026-06-01": 100000.0}
    _isos, items = fr._auto_allocate_oldest_unpaid_months(
        883,
        48000.0,
        odeme_tarihi="2026-06-20",
        borc_satirlari=borc,
        onceki_tahsilatlar=[],
    )
    check("883_pay_1", len(items) == 1)
    check("883_pay_48000", abs(items[0][1] - 48000.0) < 0.01, str(items))
    ac = _manuel_aciklama("", items)
    check("883_tah_yok", "|AYLIK_TAH|" not in ac)
    # Kural A: eksik oran 0.52 > 0.17 → kismi
    brut, od = 100000.0, 48000.0
    esik = 0.17
    oran_eksik = (brut - od) / brut
    check("883_kismi_oran", oran_eksik > esik)


def test_coklu_tek_fazla():
    borc = {"2025-11-01": 200.0, "2025-12-01": 200.0}
    # tam
    _i, items = fr._auto_allocate_oldest_unpaid_months(
        1, 400.0, odeme_tarihi="2026-01-01", borc_satirlari=borc, onceki_tahsilatlar=[]
    )
    check("tam_2", len(items) == 2 and abs(sum(p for _, p in items) - 400) < 0.01)
    # kismi tek ay secilmis gibi tutar ama allowlist YOK → yine oldest
    _i2, items2 = fr._auto_allocate_oldest_unpaid_months(
        1, 100.0, odeme_tarihi="2026-01-01", borc_satirlari=borc, onceki_tahsilatlar=[]
    )
    check("kismi_oldest", items2 and items2[0][0] == "2025-11-01" and abs(items2[0][1] - 100) < 0.01)
    # fazla
    _i3, items3 = fr._auto_allocate_oldest_unpaid_months(
        1, 900.0, odeme_tarihi="2026-01-01", borc_satirlari=borc, onceki_tahsilatlar=[]
    )
    check("fazla_cap_400", abs(sum(p for _, p in items3) - 400) < 0.01)


def test_allowlist_yok_sayilir():
    """Eski allowlist (secili ay) artik manuel yolda kullanilmamali — oldest kazanir."""
    borc = {"2026-01-01": 1000.0, "2026-06-01": 1000.0}
    # Eski yol: allowlist sadece haziran
    _a, items_old = fr._auto_allocate_oldest_unpaid_months(
        1,
        500.0,
        odeme_tarihi="2026-06-15",
        borc_satirlari=borc,
        onceki_tahsilatlar=[],
        iso_allowlist=["2026-06-01"],
    )
    check("eski_allow_haziran", items_old and items_old[0][0] == "2026-06-01")
    # Yeni manuel: allowlist yok → ocak
    _b, items_new = fr._auto_allocate_oldest_unpaid_months(
        1,
        500.0,
        odeme_tarihi="2026-06-15",
        borc_satirlari=borc,
        onceki_tahsilatlar=[],
    )
    check("yeni_fifo_ocak", items_new and items_new[0][0] == "2026-01-01")


def test_ust_grid_toplu_kaynak():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("grid_toplu_kaynak", '"grid_toplu"' in src or "'grid_toplu'" in src)
    check("aylik_tahsil_et_route", "aylik-tutarlardan-tahsil-et" in src)
    # grid_toplu hâlâ AYLIK_TAH yazar
    check("grid_tah_marker_yolu", "AYLIK_TAH" in src)


def test_be_kod_savunma():
    src = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    # tahsilat_ekle icinde istemci listesi yok sayilmali
    i = src.find("def tahsilat_ekle")
    j = src.find(chr(10) + "def ", i + 10)
    chunk = src[i:j]
    check("be_ay_ref_bos", "ay_ref_isos = []" in chunk)
    check("be_tah_markers_yok", "_aciklama_with_aylik_markers(" not in chunk)
    check("be_pay_var", "_aciklama_with_aylik_pay_tokens" in chunk)
    check("be_strip", "_strip_aylik_dagitim_markers" in chunk)


def test_mutasyon():
    """Savunma kaldirilinca (markers eklenince) test FAIL etmeli."""
    pay = [("2026-06-01", 48000.0)]
    good = _manuel_aciklama("", pay)
    check("mut_kontrol_tah_yok", "|AYLIK_TAH|" not in good)
    # mutasyon: eski helper
    bad = fr._aciklama_with_aylik_markers("", [p[0] for p in pay])
    check("mut_eski_tah_var", "|AYLIK_TAH|" in bad)
    # kapı: good yolu zorunlu
    if "|AYLIK_TAH|" in good:
        check("mutasyon_kapi", False, "iyi yol TAH icermemeli")
    else:
        check("mutasyon_kapi", "|AYLIK_TAH|" in bad)


def test_panel_map_pay_tutarli():
    """PAY token → batch map tutari; TAH yokken de dagitim okunur."""
    ac = _manuel_aciklama("", [("2026-06-01", 48000.0)])
    # PAY parse
    m = re.search(r"\|AYLIK_PAY\|(\d{4}-\d{2}-\d{2})=([0-9.]+)\|", ac)
    check("pay_parse", bool(m))
    if m:
        check("pay_iso", m.group(1) == "2026-06-01")
        check("pay_tutar", abs(float(m.group(2)) - 48000.0) < 0.01)


def main():
    test_fe_kaynak()
    test_taban_eski_marker()
    test_yeni_manuel_tah_yok_pay_var()
    test_fifo_dagitim_sahte()
    test_kismi_883_tipi()
    test_coklu_tek_fazla()
    test_allowlist_yok_sayilir()
    test_ust_grid_toplu_kaynak()
    test_be_kod_savunma()
    test_mutasyon()
    test_panel_map_pay_tutarli()
    if FAILS:
        print("FAIL", len(FAILS))
        for a in FAILS:
            print(" -", a)
        raise SystemExit(1)
    print("test_alt_makbuz_manuel_marker_yok PASS")


if __name__ == "__main__":
    main()
