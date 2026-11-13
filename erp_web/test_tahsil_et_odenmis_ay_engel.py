# -*- coding: utf-8 -*-
"""Ödenmiş aya grid_toplu tahsil-et engeli + makbuz fazla ödeme uyarısı.

Temiz ortam birim testleri. Prod yazma yok. Yerel/sahte DB.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_")):
        os.environ.pop(k, None)

from routes import giris_routes as gr  # noqa: E402
from routes import faturalar_routes as fr  # noqa: E402

FAILS: list[str] = []
TOL = float(gr.AYLIK_GRID_TAM_ODENDI_TOLERANS)


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_helper_kapali_acik_kismi():
    f = gr._tahsil_et_ay_kalan_durum
    payload = {
        "aylar": [
            {
                "yil": 2026,
                "ay": 5,
                "brut_tutar_kdv": 1000.0,
                "odenen_tutar_kdv": 1000.0,
                "kalan_tutar_kdv": 0.0,
                "tahsil_edildi": True,
            },
            {
                "yil": 2026,
                "ay": 6,
                "brut_tutar_kdv": 1000.0,
                "odenen_tutar_kdv": 400.0,
                "kalan_tutar_kdv": 600.0,
                "tahsil_edildi": False,
            },
            {
                "yil": 2026,
                "ay": 7,
                "brut_tutar_kdv": 1000.0,
                "odenen_tutar_kdv": 0.0,
                "kalan_tutar_kdv": 1000.0,
                "tahsil_edildi": False,
            },
        ]
    }
    d5 = f(payload, {}, 2026, 5)
    d6 = f(payload, {}, 2026, 6)
    d7 = f(payload, {}, 2026, 7)
    check("kapali_mayis", d5["kapali"] is True and d5["kalan"] == 0.0, d5)
    check("kismi_haziran", d6["kapali"] is False and abs(d6["kalan"] - 600.0) < TOL, d6)
    check("acik_temmuz", d7["kapali"] is False and abs(d7["kalan"] - 1000.0) < TOL, d7)


def test_kural_a_bandi_kapali():
    """KDV bandı (~brut/1.2) Kural A ile kapalı sayılır → reddedilir."""
    f = gr._tahsil_et_ay_kalan_durum
    brut = 1200.0
    tah = round(brut / 1.2, 2)  # 1000
    payload = {
        "aylar": [
            {
                "yil": 2026,
                "ay": 3,
                "brut_tutar_kdv": brut,
                "odenen_tutar_kdv": tah,
                "kalan_tutar_kdv": round(brut - tah, 2),
                "tahsil_edildi": False,
            }
        ]
    }
    d = f(payload, {}, 2026, 3)
    check("kural_a_kapali", d["kapali"] is True, d)


def test_fe_kaynak_ve_literal():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    idx = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    for src, label in ((tjs, "tjs"), (idx, "idx")):
        check(f"{label}_yeni_tahsil_fn", "function sozlesmelerAyKartYeniTahsilEdilebilirMi" in src)
        check(f"{label}_odenmis_metin", "zaten ödenmiş, tahsilat girilemez" in src)
        check(f"{label}_fazla_fn", "function tahsilatFazlaOdemeUyariGuncelle" in src)
        check(f"{label}_fazla_metin", "Fazla ödeme:" in src and "ileri aylara/alacağa yazılacak" in src)
        # fatura seçimi serbest: TiklanabilirMi / SecimToggle ayrı
        check(f"{label}_fatura_secim_ayri", "sozlesmelerAyKartSecimToggle" in src)
    # literal bs+n in new helper chunks
    for name in (
        "sozlesmelerAyKartYeniTahsilEdilebilirMi",
        "tahsilatFazlaOdemeUyariGuncelle",
        "tahsilatFazlaOdemeUyariMetni",
        "girisTahsilatOnizle",
        "sozlesmeTahsilatOnizle",
    ):
        i = tjs.find("function " + name)
        chunk = tjs[i : i + 3500] if i >= 0 else ""
        check(f"literal_bs_n_{name}", (chr(92) + "n") not in chunk)


def test_be_kaynak_odenmis_neden():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("be_helper", "def _tahsil_et_ay_kalan_durum" in src)
    check("be_neden", '"zaten_odenmis"' in src)
    check("be_mesaj", "ayı zaten ödenmiş, tahsilat girilemez" in src)
    i = src.find("def api_aylik_tutarlardan_tahsil_et")
    j = src.find(chr(10) + "def api_aylik_tutarlardan_tahsilden_cikar", i)
    chunk = src[i:j] if i >= 0 and j > i else ""
    check("be_loop_kapali", "zaten_odenmis" in chunk and "_tahsil_et_ay_kalan_durum" in chunk)
    check("be_kismi_cap", "kalan_ay" in chunk)


def test_onizle_meta_fazla():
    """meta_only JSON: fazla ödeme uyarısı; kaydet davranışı değişmez (yalnız bilgi)."""
    from flask import Flask

    app2 = Flask("t_onizle")
    app2.config["SECRET_KEY"] = "test"
    app2.register_blueprint(fr.bp)

    class _U:
        is_authenticated = True
        is_active = True
        is_anonymous = False
        id = 1

        def get_id(self):
            return "1"

    fn = fr.tahsilat_makbuz_onizle
    # login_required sarmalayıcısını aç
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__

    def _alloc(mid, tutar, **kw):
        t = float(tutar or 0)
        if t >= 1e11:
            return ["2026-01-01"], [("2026-01-01", 1000.0)]
        return ["2026-01-01"], [("2026-01-01", min(t, 1000.0))]

    with mock.patch.object(fr, "_auto_allocate_oldest_unpaid_months", side_effect=_alloc), mock.patch.object(
        fr, "fetch_one", return_value={"name": "X"}
    ), mock.patch.object(fr, "fetch_all", return_value=[]), mock.patch.object(
        fr, "build_makbuz_pdf", return_value=b"%PDF"
    ), mock.patch.object(fr, "_tahsilat_icin_makbuz_no_sec", return_value="1"), mock.patch(
        "flask_login.utils._get_user", return_value=_U()
    ):
        with app2.test_request_context(
            "/faturalar/tahsilat-makbuz-onizle",
            method="POST",
            json={
                "musteri_id": 999001,
                "tutar": 1500,
                "tahsilat_tarihi": "2026-06-01",
                "meta_only": 1,
            },
            headers={"Accept": "application/json"},
        ):
            resp = fn()
            j = resp.get_json(silent=True) if hasattr(resp, "get_json") else {}
            if not j and hasattr(resp, "json"):
                j = resp.json
            check("meta_ok", getattr(resp, "status_code", 200) in (200, None) and (j or {}).get("ok") is True, j)
            check("meta_fazla_500", abs(float((j or {}).get("fazla_odeme_tl") or 0) - 500.0) < 0.06, j)
            check("meta_uyari", "Fazla ödeme:" in str((j or {}).get("fazla_odeme_uyari") or ""), j)
        with app2.test_request_context(
            "/faturalar/tahsilat-makbuz-onizle",
            method="POST",
            json={
                "musteri_id": 999001,
                "tutar": 800,
                "tahsilat_tarihi": "2026-06-01",
                "meta_only": 1,
            },
            headers={"Accept": "application/json"},
        ):
            resp2 = fn()
            j2 = resp2.get_json(silent=True) if hasattr(resp2, "get_json") else {}
            check("meta_fazla_yok", float((j2 or {}).get("fazla_odeme_tl") or 0) <= 0.05, j2)
            check("meta_uyari_bos", not str((j2 or {}).get("fazla_odeme_uyari") or "").strip(), j2)


def test_mutasyon_helper():
    """Mutasyon: kalan>tol iken kapali False kalmalı."""
    f = gr._tahsil_et_ay_kalan_durum
    payload = {
        "aylar": [
            {
                "yil": 2026,
                "ay": 8,
                "brut_tutar_kdv": 2000.0,
                "odenen_tutar_kdv": 0.0,
                "kalan_tutar_kdv": 2000.0,
                "tahsil_edildi": False,
            }
        ]
    }
    d = f(payload, None, 2026, 8)
    check("mut_acik", d["kapali"] is False and d["kalan"] == 2000.0, d)
    # mutate paid
    payload["aylar"][0]["kalan_tutar_kdv"] = 0.0
    payload["aylar"][0]["odenen_tutar_kdv"] = 2000.0
    payload["aylar"][0]["tahsil_edildi"] = True
    d2 = f(payload, None, 2026, 8)
    check("mut_kapali", d2["kapali"] is True, d2)


def test_alt_makbuz_degismedi_imza():
    """(viii) alttan makbuz: TAH yok, PAY var yolu duruyor."""
    src = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    i = src.find("def tahsilat_ekle")
    chunk = src[i : i + 8000]
    check("ekle_manuel_fifo", "_auto_allocate_oldest_unpaid_months" in chunk)
    check("ekle_tah_yok_yorum", "AYLIK_TAH üretilmez" in chunk or "AYLIK_TAH yok" in chunk)
    check("banka_degismez_not", "akbank_commit" not in chunk[0:200])  # smoke


if __name__ == "__main__":
    test_helper_kapali_acik_kismi()
    test_kural_a_bandi_kapali()
    test_fe_kaynak_ve_literal()
    test_be_kaynak_odenmis_neden()
    test_onizle_meta_fazla()
    test_mutasyon_helper()
    test_alt_makbuz_degismedi_imza()
    print("FAILS", len(FAILS), FAILS)
    raise SystemExit(1 if FAILS else 0)
