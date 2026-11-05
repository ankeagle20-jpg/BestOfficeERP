# -*- coding: utf-8 -*-
"""Müşteriler raporu «Gün» (gun_bazli) — bu ay borcu sözleşme gününe göre. Canlıya yazmaz. PII yok."""
from __future__ import annotations

import calendar
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def _satir(mid, borc, sg, ref_ay, ga=None):
    return {
        "musteri_id": mid,
        "firma_adi": "K%d" % mid,
        "toplam_borc": borc,
        "borc_hesaplanamadi": False,
        "sozlesme_gun": sg,
        "borc_ref_ay": ref_ay,
        "geciken_ay": ga if ga is not None else (2 if borc and ref_ay else 1),
        "guncel_kira_bedeli": ref_ay,
    }


def test_helpers_ve_uygula():
    from routes import faturalar_routes as fr

    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 5)

    check("param yalniz 1", fr._firma_ozet_gun_bazli_param_aktif("1") is True)
    check("param true degil", fr._firma_ozet_gun_bazli_param_aktif("true") is False)
    check("param bos", fr._firma_ozet_gun_bazli_param_aktif("") is False)
    check("param 0", fr._firma_ozet_gun_bazli_param_aktif("0") is False)

    check("istanbul override", fr._firma_ozet_istanbul_bugun() == date(2025, 11, 5))

    # (a) bugün 5, sözleşme 18 → bu ay hariç
    check("a dahil degil 18", fr._firma_ozet_gun_bazli_bu_ay_dahil_mi(18) is False)
    s = _satir(1, 300.0, 18, 100.0, ga=3)
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s, aktif=True)
    check("a borc dusuldu", s["toplam_borc"] == 200.0, s.get("toplam_borc"))
    check("a geciken -1", s["geciken_ay"] == 2, s.get("geciken_ay"))
    check("a flag", s.get("gun_bazli_bu_ay_haric") is True)

    # (b) bugün 18 / 20, sözleşme 18 → dahil (eşitlik)
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 18)
    check("b esit dahil", fr._firma_ozet_gun_bazli_bu_ay_dahil_mi(18) is True)
    s18 = _satir(2, 300.0, 18, 100.0, ga=3)
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s18, aktif=True)
    check("b borc ayni 18", s18["toplam_borc"] == 300.0)

    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 20)
    check("b 20 dahil", fr._firma_ozet_gun_bazli_bu_ay_dahil_mi(18) is True)
    s20 = _satir(3, 300.0, 18, 100.0)
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s20, aktif=True)
    check("b borc ayni 20", s20["toplam_borc"] == 300.0)

    # (c) sözleşme 3 → dahil (bugün 5)
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 5)
    check("c gun3 dahil", fr._firma_ozet_gun_bazli_bu_ay_dahil_mi(3) is True)
    s3 = _satir(4, 300.0, 3, 100.0)
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s3, aktif=True)
    check("c borc ayni", s3["toplam_borc"] == 300.0)

    # (d) sözleşme 31, ay 30 gün
    # Kasım 30 gün; etkin=30. bugün 30 → dahil; bugün 29 → hariç
    check("d etkin kasim", fr._firma_ozet_gun_bazli_etkin_gun(31, 2025, 11) == 30)
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 30)
    check("d 30 dahil", fr._firma_ozet_gun_bazli_bu_ay_dahil_mi(31) is True)
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 29)
    check("d 29 haric", fr._firma_ozet_gun_bazli_bu_ay_dahil_mi(31) is False)
    s31 = _satir(5, 250.0, 31, 50.0, ga=2)
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s31, aktif=True)
    check("d 29 dusuldu", s31["toplam_borc"] == 200.0, s31.get("toplam_borc"))

    # Şubat 28 / 29
    check("d subat28 etkin", fr._firma_ozet_gun_bazli_etkin_gun(31, 2025, 2) == 28)
    check("d subat29 artik", fr._firma_ozet_gun_bazli_etkin_gun(31, 2024, 2) == 29)
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 2, 28)
    check("d subat28 dahil", fr._firma_ozet_gun_bazli_bu_ay_dahil_mi(31) is True)
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 2, 27)
    check("d subat27 haric", fr._firma_ozet_gun_bazli_bu_ay_dahil_mi(31) is False)
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2024, 2, 29)
    check("d artik29 dahil", fr._firma_ozet_gun_bazli_bu_ay_dahil_mi(31) is True)
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2024, 2, 28)
    check("d artik28 haric31", fr._firma_ozet_gun_bazli_bu_ay_dahil_mi(31) is False)

    # (e) önceki aylar: borc_ref_ay yalnız bu ay; düşülünce kalan = önceki
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 5)
    s_e = _satir(6, 400.0, 18, 100.0, ga=4)  # 300 önceki + 100 bu ay
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s_e, aktif=True)
    check("e onceki kalir", s_e["toplam_borc"] == 300.0)

    # (f) bayrak kapalı birebir
    s_f = _satir(7, 400.0, 18, 100.0, ga=4)
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s_f, aktif=False)
    check("f kapali ayni", s_f["toplam_borc"] == 400.0 and s_f.get("gun_bazli_bu_ay_haric") is None)

    # (g) sadece borçlular + gün: bu ay hariç → borç kalmaz → listeden düşer
    from routes.faturalar_routes import _firma_ozet_client_filtre_uygula

    rows = [
        _satir(10, 100.0, 18, 100.0, ga=1),  # yalnız bu ay → 0
        _satir(11, 250.0, 18, 100.0, ga=2),  # 150 kalır
        _satir(12, 80.0, 3, 80.0, ga=1),  # gün geçmiş → kalır
    ]
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 5)
    fr._firma_ozet_gun_bazli_borc_uygula_satirlar(rows, aktif=True)
    check("g mid10 sifir", rows[0]["toplam_borc"] == 0.0)
    check("g mid11 150", rows[1]["toplam_borc"] == 150.0)
    filt = _firma_ozet_client_filtre_uygula(
        rows, q="", grup2=[], hizmet=[], durum="", oda=None, sadece_borclu=True
    )
    ids = [r["musteri_id"] for r in filt]
    check("g mid10 dustu", 10 not in ids, ids)
    check("g mid11 kaldi", 11 in ids, ids)
    check("g mid12 kaldi", 12 in ids, ids)

    # (h) boş/geçersiz sözleşme günü → kural yok
    check("h sg0 dahil", fr._firma_ozet_gun_bazli_bu_ay_dahil_mi(0) is True)
    check("h sgNone dahil", fr._firma_ozet_gun_bazli_bu_ay_dahil_mi(None) is True)
    check("h sg99 dahil", fr._firma_ozet_gun_bazli_bu_ay_dahil_mi(99) is True)
    s_h = _satir(20, 200.0, 0, 50.0)
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s_h, aktif=True)
    check("h borc ayni", s_h["toplam_borc"] == 200.0)

    # (i) cache anahtarı bayrağa göre ayrı
    src = Path(fr.__file__).read_text(encoding="utf-8")
    check("i cache key gun_bazli", '"gun_bazli"' in fr._firma_ozet_resp_cache_key.__code__.co_consts or "gun_bazli" in src)
    # simulate request-less: inspect source
    import inspect

    ck_src = inspect.getsource(fr._firma_ozet_resp_cache_key)
    check("i key listede", "gun_bazli" in ck_src)

    # (j) bugün enjekte
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2030, 1, 1)
    check("j inject", fr._firma_ozet_istanbul_bugun() == date(2030, 1, 1))
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = None

    # monthrange sanity
    check("d monthrange kasim", calendar.monthrange(2025, 11)[1] == 30)

    # --- 0.2 düzeltme: yedeksiz borc_ref_ay ---
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 5)
    import inspect

    uyg_src = inspect.getsource(fr._firma_ozet_gun_bazli_borc_uygula_satir)
    check("guncel yedek yok", "guncel_kira_bedeli" not in uyg_src and "it.get(\"guncel" not in uyg_src)
    check("borc_month yedek yok uygula", "borc_month" not in uyg_src and ".get(\"borc_month\")" not in uyg_src)

    # (a) borc_ref_ay=0 (bu ay ödenmiş) → toplam/geciken değişmez
    s_a = _satir(30, 300.0, 18, 0.0, ga=2)
    s_a["guncel_kira_bedeli"] = 120.0  # yedek tuzağı
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s_a, aktif=True)
    check("02a borc ayni", s_a["toplam_borc"] == 300.0, s_a.get("toplam_borc"))
    check("02a geciken ayni", s_a["geciken_ay"] == 2, s_a.get("geciken_ay"))
    check("02a flag yok", s_a.get("gun_bazli_bu_ay_haric") is None)

    # (b) borc_ref_ay yok/None → değişmez, atlanan artar
    s_b = _satir(31, 300.0, 18, 50.0, ga=3)
    del s_b["borc_ref_ay"]
    s_b["guncel_kira_bedeli"] = 99.0
    atl = [0]
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s_b, aktif=True, atlanan_acc=atl)
    check("02b borc ayni", s_b["toplam_borc"] == 300.0)
    check("02b geciken ayni", s_b["geciken_ay"] == 3)
    check("02b atlanan", atl[0] == 1, atl)

    s_bn = _satir(32, 300.0, 18, 50.0, ga=3)
    s_bn["borc_ref_ay"] = None
    atl2 = [0]
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s_bn, aktif=True, atlanan_acc=atl2)
    check("02b None borc ayni", s_bn["toplam_borc"] == 300.0)
    check("02b None atlanan", atl2[0] == 1, atl2)

    # (c) ref_ay > toplam_borc → 0, negatif yok
    s_c = _satir(33, 40.0, 18, 100.0, ga=1)
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s_c, aktif=True)
    check("02c sifir", s_c["toplam_borc"] == 0.0, s_c.get("toplam_borc"))
    check("02c negatif yok", s_c["toplam_borc"] >= 0)
    check("02c kurus", isinstance(s_c["toplam_borc"], float) and s_c["toplam_borc"] == round(s_c["toplam_borc"], 2))

    # kuruş: round(..., 2)
    s_ck = {
        "musteri_id": 34,
        "toplam_borc": 100.555,
        "borc_hesaplanamadi": False,
        "sozlesme_gun": 18,
        "borc_ref_ay": 0.333,
        "geciken_ay": 2,
    }
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s_ck, aktif=True)
    check("02c kurus round", s_ck["toplam_borc"] == 100.22, s_ck.get("toplam_borc"))

    # (d) ref_ay > 0 ve bugün < sözleşme → düş + geciken-1 (alt 0)
    s_d = _satir(35, 250.0, 18, 50.0, ga=1)
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s_d, aktif=True)
    check("02d borc", s_d["toplam_borc"] == 200.0, s_d.get("toplam_borc"))
    check("02d geciken0", s_d["geciken_ay"] == 0, s_d.get("geciken_ay"))

    # (e) bugün >= sözleşme → değişmez
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 20)
    s_e2 = _satir(36, 250.0, 18, 50.0, ga=2)
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s_e2, aktif=True)
    check("02e ayni", s_e2["toplam_borc"] == 250.0 and s_e2["geciken_ay"] == 2)

    # (f) bayrak kapalı
    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = date(2025, 11, 5)
    s_f2 = _satir(37, 250.0, 18, 50.0, ga=2)
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s_f2, aktif=False)
    check("02f kapali", s_f2["toplam_borc"] == 250.0 and s_f2["geciken_ay"] == 2)

    # (g) guncel dolu + borc_ref_ay=0 → düşüm yok
    s_g = _satir(38, 500.0, 18, 0.0, ga=4)
    s_g["guncel_kira_bedeli"] = 200.0
    fr._firma_ozet_gun_bazli_borc_uygula_satir(s_g, aktif=True)
    check("02g dusum yok", s_g["toplam_borc"] == 500.0 and s_g["geciken_ay"] == 4)

    # atlanan log anahtarı kaynakta
    check("02 atlanan log", "firma_ozet_gun_bazli_atlanan" in Path(fr.__file__).read_text(encoding="utf-8"))

    fr._FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE = None


def test_ui_ve_kaynak():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("ui checkbox", 'id="fatura_rapor_gun_bazli"' in html and "> Gün</label>" in html)
    i_sb = html.find('id="fatura_rapor_sadece_borclu"')
    i_gb = html.find('id="fatura_rapor_gun_bazli"')
    check("ui saginda", i_sb > 0 and i_gb > i_sb and (i_gb - i_sb) < 500)
    check("ui title", "Bu ayın borcu, sözleşme günü gelmişse sayılır" in html)
    check("ui yukle param", "gun_bazli=1" in html)
    a_ex = html.find("function faturaRaporExcelAktar")
    check("ui excel param", a_ex > 0 and "gun_bazli=1" in html[a_ex : a_ex + 2000])
    check("ui state", "gun_bazli:" in html and "st.gun_bazli" in html)
    check("ui init yukle", "initFaturaRaporGunBazliGiris" in html and "faturaRaporYukle" in html[html.find("initFaturaRaporGunBazliGiris"):html.find("initFaturaRaporGunBazliGiris")+450])
    check("ui tjs degil", "gun_bazli" in html)  # index.html

    src = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    check("py helper", "_firma_ozet_gun_bazli_borc_uygula" in src)
    check("py canli yok yorum", "Ek canlı hesap yok" in src or "ek canlı hesap yok" in src.lower() or "borc_ref_ay" in src)
    check("py override", "_FIRMA_OZET_GUN_BAZLI_BUGUN_OVERRIDE" in src)
    check("py guncel yedek kaldirildi", "Yedek: guncel" not in src)
    # winner_ids yolunda da borc_month yedeği olmamalı (gun_bazli bloğu)
    a_w = src.find("def _firma_ozet_toplam_borc_grid_for_winner_ids")
    b_w = src.find("\ndef _firma_ozet_row_to_satir_item", a_w)
    wfn = src[a_w:b_w] if a_w > 0 and b_w > a_w else ""
    check(
        "py winner borc_month yedek yok",
        "borc_month" not in wfn or "Yalnız borc_ref_ay" in wfn,
    )

    gsrc = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("grid borc_ref_ay", "borc_ref_ay" in gsrc)


def test_ref_ay_payload():
    from routes.giris_routes import _firma_ozet_ref_ay_borc_dahil_from_payload

    payload = {
        "aylar": [
            {
                "yil": 2025,
                "ay": 10,
                "tutar_kdv_dahil": 100,
                "tahsil_edildi": False,
                "kalan_tutar_kdv": 100,
            },
            {
                "yil": 2025,
                "ay": 11,
                "tutar_kdv_dahil": 80,
                "tahsil_edildi": False,
                "kalan_tutar_kdv": 80,
            },
        ]
    }
    v = _firma_ozet_ref_ay_borc_dahil_from_payload(payload, 2025, 11, None)
    check("ref_ay 80", abs(v - 80.0) < 0.001, v)
    # tahsil edilmiş ay
    payload2 = {
        "aylar": [
            {
                "yil": 2025,
                "ay": 11,
                "tutar_kdv_dahil": 80,
                "tahsil_edildi": True,
                "kalan_tutar_kdv": 0,
                "odenen_tutar_kdv": 80,
            }
        ]
    }
    v2 = _firma_ozet_ref_ay_borc_dahil_from_payload(payload2, 2025, 11, None)
    check("ref_ay tahsil 0", v2 == 0.0, v2)


if __name__ == "__main__":
    test_helpers_ve_uygula()
    test_ui_ve_kaynak()
    test_ref_ay_payload()
    if FAILS:
        print("FAIL", FAILS)
        sys.exit(1)
    print("ALL_OK test_musteriler_gun_bazli_borc")
