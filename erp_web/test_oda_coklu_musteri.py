# -*- coding: utf-8 -*-
"""Aynı odaya çoklu müşteri: bayrak + onay + pasif/iptal + doluluk. Canlı DB yazma yok."""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _clear_flag() -> None:
    os.environ.pop("ODA_COKLU_MUSTERI", None)


def test_bayrak_ve_onay_parse():
    from routes import musteri_routes as mr

    _clear_flag()
    check("bayrak varsayilan kapali", mr.oda_coklu_musteri_acik() is False)
    os.environ["ODA_COKLU_MUSTERI"] = "0"
    check("bayrak 0 kapali", mr.oda_coklu_musteri_acik() is False)
    os.environ["ODA_COKLU_MUSTERI"] = "true"
    check("bayrak true kapali", mr.oda_coklu_musteri_acik() is False)
    os.environ["ODA_COKLU_MUSTERI"] = "1"
    check("bayrak 1 acik", mr.oda_coklu_musteri_acik() is True)
    _clear_flag()

    check("onay yok", mr._oda_cakisma_onay_istendi({}) is False)
    check("onay 1", mr._oda_cakisma_onay_istendi({"oda_cakisma_onay": 1}) is True)
    check("onay true", mr._oda_cakisma_onay_istendi({"oda_cakisma_onay": True}) is True)
    check("onay evet", mr._oda_cakisma_onay_istendi({"oda_cakisma_onay": "evet"}) is True)
    check("onay hayir", mr._oda_cakisma_onay_istendi({"oda_cakisma_onay": "hayir"}) is False)


def test_cakisma_sorgu_filtreleri_statik():
    src = (ROOT / "routes" / "musteri_routes.py").read_text(encoding="utf-8")
    check("aktif filtre", "LOWER(TRIM(COALESCE(durum, ''))) = 'aktif'" in src)
    check("iptal haric", "COALESCE(iptal, FALSE) = FALSE" in src)
    check("haric mid", "AND id <> %s" in src)
    check("onay bayrak kapisi", "oda_coklu_musteri_acik()" in src)
    check("oda_cakisma_onay", "oda_cakisma_onay" in src)
    check("bulk oda yazmaz", "hazir_ofis_oda_no" not in src[src.find("def api_bulk_update") : src.find("def api_bulk_update") + 3500])


def _flask_ctx():
    from flask import Flask

    app = Flask(__name__)
    return app.app_context()


def test_engel_bayrak_kapali_onaysiz():
    from routes import musteri_routes as mr

    _clear_flag()
    mevcut = [{"mid": 10, "ad": "A", "durum": "aktif"}]
    with _flask_ctx():
        resp, code = mr._oda_cakisma_yanit(205, mevcut, onay_mumkun=False)
        body = resp.get_json()
    check("kapali http 409", code == 409)
    check("kapali oda_cakisma yok", body.get("oda_cakisma") is not True)
    check("kapali kod yok", body.get("kod") != "oda_cakisma")
    check("kapali mesaj var", "Oda 205" in (body.get("mesaj") or ""))


def test_engel_bayrak_acik_onaysiz_ve_onayli():
    from routes import musteri_routes as mr

    os.environ["ODA_COKLU_MUSTERI"] = "1"
    mevcut = [{"mid": 10, "ad": "A", "durum": "aktif"}, {"mid": 11, "ad": "B", "durum": "aktif"}]
    with _flask_ctx():
        resp, code = mr._oda_cakisma_yanit(210, mevcut, onay_mumkun=True)
        body = resp.get_json()
    check("acik http 409", code == 409)
    check("acik oda_cakisma", body.get("oda_cakisma") is True)
    check("acik kod", body.get("kod") == "oda_cakisma")
    check("acik mevcut n", len(body.get("mevcut") or []) == 2)
    check("acik onay mumkun", body.get("oda_cakisma_onay_mumkun") is True)

    check("onay istendi", mr._oda_cakisma_onay_istendi({"oda_cakisma_onay": "1"}) is True)
    _clear_flag()
    with _flask_ctx():
        resp2, _ = mr._oda_cakisma_yanit(210, mevcut, onay_mumkun=False)
        body2 = resp2.get_json()
    check("kapali onay bayragi yok", body2.get("oda_cakisma_onay_mumkun") is not True)


def test_pasif_iptal_cakisma_sayilmaz():
    from routes import musteri_routes as mr

    # fetch_all boş dönünce (yalnız pasif/iptal DB'de filtrelenir) çakışma yok
    with mock.patch.object(mr, "fetch_all", return_value=[]):
        out = mr._hazir_oda_aktif_cakismalar(220, haric_mid=1)
        check("pasif/iptal sorgu bos", out == [])

    with mock.patch.object(
        mr,
        "fetch_all",
        return_value=[{"mid": 99, "ad": "X", "durum": "aktif"}],
    ):
        out2 = mr._hazir_oda_aktif_cakismalar(220, haric_mid=1)
        check("aktif cakisma mid", out2 and out2[0]["mid"] == 99)


def test_kendi_oda_guncelleme_haric():
    from routes import musteri_routes as mr

    calls = {}

    def _fa(sql, params=None):
        calls["sql"] = sql
        calls["params"] = params
        return []

    with mock.patch.object(mr, "fetch_all", side_effect=_fa):
        mr._hazir_oda_aktif_cakismalar(201, haric_mid=55)
    check("haric id param", calls.get("params") == (201, 55))
    check("haric sql", "id <> %s" in (calls.get("sql") or ""))


def test_doluluk_api_gruplama():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    fn = src[src.find("def api_hazir_ofis_durum") : src.find("def _iptal_hayalet_grid_temizle")]
    check("doluluk n alani", '"n": n' in fn or "'n': n" in fn)
    check("doluluk musteriler", "musteriler" in fn)
    check("doluluk by_oda", "by_oda" in fn)
    check("doluluk iptal haric", "COALESCE(c.iptal, FALSE) = FALSE" in fn)


def test_fe_oda_cakisma_confirm():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    check("tjs oda_cakisma", "oda_cakisma" in tjs)
    check("tjs oda_cakisma_onay", "oda_cakisma_onay" in tjs)
    check("tjs confirm metin", "Yine de eklemek istiyor musunuz?" in tjs)
    check("tjs ayni odada N", "Aynı odada" in tjs)
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    check("html oda_cakisma", "oda_cakisma" in html)
    check("html oda_cakisma_onay", "oda_cakisma_onay" in html)


def test_bulk_excel_hariç_gerekce():
    """bulk-update ve Excel içe aktarma hazir_ofis_oda_no yazmıyor → aynı kural N/A."""
    bulk = (ROOT / "routes" / "musteri_routes.py").read_text(encoding="utf-8")
    start = bulk.find("def api_bulk_update")
    end = bulk.find("\ndef ", start + 10)
    chunk = bulk[start:end]
    check("bulk oda kolonu yok", "hazir_ofis_oda" not in chunk)
    # Excel export var; import oda unique zorlaması yok (statik)
    check("excel ensure kolon", "ensure_customers_excel_columns" in bulk)


def test_mevcut_kyc_helper_korunur():
    src = (ROOT / "routes" / "musteri_routes.py").read_text(encoding="utf-8")
    check("kyc oda_bosalt false", "musteri_durum_yaz_alanlari(data, kati=False, oda_bosalt=False)" in src)
    check("ensure oda kolon", "ensure_customers_hazir_ofis_oda" in src)


def test_migrasyon_taslak_calistirilmaz():
    script = ROOT / "scripts" / "ensure_oda_coklu_musteri_unique_drop.py"
    check("migrasyon script var", script.is_file())
    txt = script.read_text(encoding="utf-8")
    check("migrasyon bayrak kapisi", "ODA_UNIQUE_DROP_ONAY" in txt)
    check("migrasyon ALLOW", "ALLOW_PROD_WRITE" in txt)
    # Script bayraksız çağrıda 2 dönmeli (DDL yok)
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k not in (
        "ODA_COKLU_MUSTERI", "ALLOW_PROD_WRITE", "ODA_UNIQUE_DROP_ONAY"
    )}
    r = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    check("migrasyon refuse exit 2", r.returncode == 2)


def main():
    # Temiz ortam: ODA_* tanımsız
    for k in list(os.environ):
        if k.startswith("ODA_") or k.startswith("KAPANIS_"):
            os.environ.pop(k, None)

    test_bayrak_ve_onay_parse()
    test_cakisma_sorgu_filtreleri_statik()
    test_engel_bayrak_kapali_onaysiz()
    test_engel_bayrak_acik_onaysiz_ve_onayli()
    test_pasif_iptal_cakisma_sayilmaz()
    test_kendi_oda_guncelleme_haric()
    test_doluluk_api_gruplama()
    test_fe_oda_cakisma_confirm()
    test_bulk_excel_hariç_gerekce()
    test_mevcut_kyc_helper_korunur()
    test_migrasyon_taslak_calistirilmaz()

    if FAILS:
        print("FAILED:", ", ".join(FAILS))
        raise SystemExit(1)
    print("ALL OK test_oda_coklu_musteri")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
