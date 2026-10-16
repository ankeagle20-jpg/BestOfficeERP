# -*- coding: utf-8 -*-
"""Ekstre: sözleşme günü vs ay başı marker — Ara tahsilat satırı + Tahsil et UX alanları."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_sozlesme_ay_karsilastirma():
    from routes import giris_routes as gr

    check(
        "ayni_ay_elenmez",
        gr._ekstre_tarih_sozlesme_ayindan_once_mi("2022-12-01", "2022-12-07") is False,
    )
    check(
        "onceki_ay_elenir",
        gr._ekstre_tarih_sozlesme_ayindan_once_mi("2022-11-01", "2022-12-07") is True,
    )
    check(
        "sonraki_ay_ok",
        gr._ekstre_tarih_sozlesme_ayindan_once_mi("2023-01-01", "2022-12-07") is False,
    )
    check(
        "bos_false",
        gr._ekstre_tarih_sozlesme_ayindan_once_mi("", "2022-12-07") is False,
    )
    # Eski gün-bazlı kural (bug): 2022-12-01 < 2022-12-07 → elenirdi
    check(
        "eski_gun_kurali_elenirdi",
        ("2022-12-01" < "2022-12-07") is True,
    )


def test_legacy_hizala_false_donem_basi_tahsilat_satiri():
    """hizala=0: sözleşme günü 7 iken ay başı marker satırda görünür; Devir çift saymaz."""
    from routes import giris_routes as gr

    mid = 900117
    bas, bit = date(2022, 12, 1), date(2023, 1, 31)
    soz = date(2022, 12, 7)

    def fake_kyc(_mid):
        return {
            "sozlesme_tarihi": soz.isoformat(),
            "aylik_kira": 2300.0,
            "kira_nakit": True,
            "kdv_oran": 20,
            "durum": "aktif",
        }

    tahsil_db = [
        {
            "id": 3710,
            "belge_no": "Makbuz-3710",
            "fatura_id": None,
            "tarih": date(2022, 12, 1),
            "eslesme_tarihi": date(2022, 12, 1),
            "tutar": 2300.0,
            "odeme_turu": "B",
            "tahsilat_aciklama": "Aralık 2022 Tahsilat B|AYLIK_TAH|2022-12-01|",
            "kaynak": "",
        },
        {
            "id": 3711,
            "belge_no": "Makbuz-3711",
            "fatura_id": None,
            "tarih": date(2023, 1, 1),
            "eslesme_tarihi": date(2023, 1, 1),
            "tutar": 2300.0,
            "odeme_turu": "B",
            "tahsilat_aciklama": "Ocak 2023 Tahsilat B|AYLIK_TAH|2023-01-01|",
            "kaynak": "",
        },
    ]
    tahsil_rows = [
        {
            "id": r["id"],
            "tutar": r["tutar"],
            "aciklama": r["tahsilat_aciklama"],
            "tahsilat_tarihi": r["tarih"],
            "fatura_tarihi": None,
            "kaynak": "",
        }
        for r in tahsil_db
    ]

    grid_payload = {
        "aylar": [
            {
                "yil": 2022,
                "ay": 12,
                "ay_key": "2022-12",
                "brut_tutar_kdv": 2300.0,
                "tutar_kdv_dahil": 2300.0,
                "odenen_tutar_kdv": 2300.0,
                "kalan_tutar_kdv": 0.0,
                "tahsil_edildi": True,
            },
            {
                "yil": 2023,
                "ay": 1,
                "ay_key": "2023-1",
                "brut_tutar_kdv": 2300.0,
                "tutar_kdv_dahil": 2300.0,
                "odenen_tutar_kdv": 2300.0,
                "kalan_tutar_kdv": 0.0,
                "tahsil_edildi": True,
            },
        ]
    }

    def fake_fetch_all(sql, params=None, *a, **k):
        s = str(sql or "")
        if "FROM tahsilatlar" in s or "from tahsilatlar" in s.lower():
            return list(tahsil_db)
        return []

    with mock.patch.object(gr, "_musteri_kyc_grup_for_aylik_grid", side_effect=fake_kyc), mock.patch.object(
        gr, "_ekstre_tahsil_rows_for_musteri", return_value=tahsil_rows
    ), mock.patch.object(gr, "_read_aylik_grid_cache_payload", return_value=grid_payload), mock.patch.object(
        gr, "_build_aylik_grid_cache_payload", return_value=grid_payload
    ), mock.patch.object(gr, "_load_musteri_panel_by_iso", return_value={}), mock.patch.object(
        gr, "_dondurma_ay_kumesi", return_value=set()
    ), mock.patch.object(gr, "_aylik_tutar_fatura_meta_by_iso", return_value={}), mock.patch.object(
        gr, "_ekstre_mukerrer_onay_fatura_rows", return_value=([], {})
    ), mock.patch.object(gr, "_musteri_reel_donem_manual_dict_from_db", return_value={}), mock.patch.object(
        gr, "fetch_one", side_effect=lambda *a, **k: {"calisma_sekli": "sirali"}
    ), mock.patch.object(gr, "fetch_all", side_effect=fake_fetch_all), mock.patch.object(
        gr, "_aylik_grid_cache_payload_tahsil_guncelle", return_value=None
    ), mock.patch.object(gr, "_save_musteri_panel_by_iso", return_value=None):
        h0 = gr._cari_ekstre_hareketler(
            mid, bas, bit, 2300.0, tahsilat_borca_hizala=False
        ) or []
        h1 = gr._cari_ekstre_hareketler(
            mid, bas, bit, 2300.0, tahsilat_borca_hizala=True
        ) or []

    def _has_dec_tahsil(hs):
        return any(
            (x.get("tur") == "Tahsilat")
            and str(x.get("tarih") or "").startswith("2022-12")
            and float(x.get("alacak") or 0) > 1
            for x in hs
        )

    check(
        "legacy_dec_tahsil_var",
        _has_dec_tahsil(h0),
        [(x.get("tarih"), x.get("tur"), x.get("alacak")) for x in h0],
    )
    check("hizala_dec_tahsil_var", _has_dec_tahsil(h1))

    # Dönem başı = sözleşme ayı → Devir yok (çift sayım yok)
    dev0 = [x for x in h0 if x.get("tur") == "Devir"]
    check("legacy_devir_yok", len(dev0) == 0, dev0)

    borc = sum(float(x.get("borc") or 0) for x in h0 if x.get("tur") == "Kira")
    alacak = sum(float(x.get("alacak") or 0) for x in h0 if x.get("tur") == "Tahsilat")
    check("kira_tahsil_denk", abs(borc - alacak) < 0.05, {"borc": borc, "alacak": alacak})

    # Bakiye değişmez: hizala açık/kapalı net aynı
    def _net(hs):
        return round(
            sum(float(x.get("borc") or 0) - float(x.get("alacak") or 0) for x in hs),
            2,
        )

    check("bakiye_hizala_ayni", abs(_net(h0) - _net(h1)) < 0.05, {"h0": _net(h0), "h1": _net(h1)})


def test_tahsil_et_mesaj_alanlari_kaynak():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    i = src.find("def api_aylik_tutarlardan_tahsil_et")
    j = src.find("\ndef _aylik_grid_parse_ay_listesi", i)
    body = src[i:j]
    check("mesaj_ekstre_donem", "ekstre_donem_onerisi" in body)
    check("mesaj_panel_yil", "panel_yil_onerisi" in body)
    check("mesaj_atlanan_detay", "atlanan_detay" in body)
    check("mesaj_aralik_metni", "Ekstrede görmek için dönemi şu aralığa alın" in body)


def test_fe_ekstre_donem_uygula():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("fe_helper", "function girisEkstreDonemOnerisiniUygula" in html)
    check("fe_panel_yil", "function girisTahsilEtSonrasiPanelYil" in html)
    check("fe_confirm_donem", "Ekstre dönemini" in html and "yapıp yenilemek ister misiniz" in html)


if __name__ == "__main__":
    for fn in (
        test_sozlesme_ay_karsilastirma,
        test_legacy_hizala_false_donem_basi_tahsilat_satiri,
        test_tahsil_et_mesaj_alanlari_kaynak,
        test_fe_ekstre_donem_uygula,
    ):
        print("---", fn.__name__)
        fn()
    if FAILS:
        print("FAILED:", FAILS)
        sys.exit(1)
    print("ALL_OK")
