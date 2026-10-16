# -*- coding: utf-8 -*-
"""Commit F: panel_grid_onarim dry-run / yazma kapısı / yedek geri."""
import json
import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import panel_grid_onarim as onr  # noqa: E402

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_dry_run_varsayilan_yazmaz():
    os.environ.pop("ALLOW_PROD_WRITE", None)
    with mock.patch.object(onr, "fetch_one", side_effect=[
        {"by_iso": json.dumps({"2025-01-01": {"tahsil": 100, "aylik": 100, "kalan": 0}}), "updated_at": None},
        {"payload": json.dumps({"aylar": [{"yil": 2025, "ay": 1}]}), "updated_at": None},
    ]), mock.patch.object(
        onr, "db"
    ) as mdb:
        # dry path should not enter db() write
        from routes import giris_routes as gr
        with mock.patch.object(gr, "_read_aylik_grid_cache_payload", return_value={"aylar": [{"yil": 2025, "ay": 1, "tutar_kdv_dahil": 100}]}), mock.patch.object(
            gr, "_build_aylik_grid_cache_payload", return_value={"aylar": [{"yil": 2025, "ay": 1, "tutar_kdv_dahil": 100}]}
        ), mock.patch.object(
            gr, "_panel_by_iso_from_tahsil_map", return_value={"2025-01-01": {"tahsil": 100, "aylik": 100, "kalan": 0}}
        ), mock.patch.object(
            gr, "_aylik_tahsil_tutar_map", return_value={"2025-01-01": 100.0}
        ), mock.patch.object(
            gr, "_apply_panel_by_iso_to_grid_payload", return_value=None
        ):
            r = onr.onar_mid(867, dry_run=True, etiket="t")
    check("dry yazilmadi", r.get("yazildi") is False, r)
    check("dry panel sonra", r.get("panel_ay_sonra") == 1, r)
    check("db yazma yok", mdb.call_count == 0, mdb.call_count)


def test_apply_without_allow_yazmaz():
    os.environ.pop("ALLOW_PROD_WRITE", None)
    with mock.patch.object(onr, "fetch_one", return_value=None), mock.patch.object(onr, "db") as mdb:
        from routes import giris_routes as gr
        with mock.patch.object(gr, "_read_aylik_grid_cache_payload", return_value={}), mock.patch.object(
            gr, "_build_aylik_grid_cache_payload", return_value={"aylar": []}
        ), mock.patch.object(
            gr, "_panel_by_iso_from_tahsil_map", return_value={}
        ), mock.patch.object(
            gr, "_aylik_tahsil_tutar_map", return_value={}
        ), mock.patch.object(
            gr, "_apply_panel_by_iso_to_grid_payload", return_value=None
        ):
            r = onr.onar_mid(867, dry_run=False, etiket="t")
    check("allow yok yazilmadi", r.get("yazildi") is False, r)
    check("allow yok db yok", mdb.call_count == 0, mdb.call_count)


def test_geri_allow_yok():
    os.environ.pop("ALLOW_PROD_WRITE", None)
    r = onr.geri_yukle(867, "x")
    check("geri reddedildi", r.get("ok") is False and "ALLOW" in str(r.get("neden")), r)


def test_kaynak_tahsilat_dokunmaz():
    src = Path(onr.__file__).read_text(encoding="utf-8")
    for yasak in ("INSERT INTO tahsilatlar", "UPDATE tahsilatlar", "DELETE FROM tahsilatlar", "INSERT INTO faturalar", "UPDATE faturalar"):
        check("yok " + yasak, yasak not in src)


def test_dry_run_cli():
    from routes import giris_routes as gr
    with mock.patch.object(onr, "onar_mid", return_value={
        "mid": 867, "dry_run": True, "panel_ay_once": 2, "grid_ay_once": 3,
        "yazildi": False, "panel_ay_sonra": 2, "grid_ay_sonra": 3,
    }) as m:
        rc = onr.main(["--mids", "867", "--dry-run"])
    check("cli rc0", rc == 0)
    check("cli dry", m.call_args.kwargs.get("dry_run") is True or m.call_args[1].get("dry_run") is True, m.call_args)


def test_cap_402_map_ustunde_tavan():
    """map 1540 / kira 881 → panele 881 yazılır."""
    by = {
        "2026-08-01": {"aylik": 881.0, "tahsil": 1540.18, "kalan": 0.0, "tahsil_tarih": "2026-09-08"},
        "2026-09-01": {"aylik": 881.0, "tahsil": 1540.18, "kalan": 0.0, "tahsil_tarih": "2026-09-08"},
    }
    out = onr._cap_panel_by_iso_for_onarim(by, dondurma_aylar=set())
    check("402 agu tavan", out["2026-08-01"]["tahsil"] == 881.0, out["2026-08-01"])
    check("402 eyl tavan", out["2026-09-01"]["tahsil"] == 881.0, out["2026-09-01"])
    check("402 kalan 0", out["2026-08-01"]["kalan"] == 0.0)


def test_cap_111_panel_sifir_tahsilat_var():
    """panel 0 iken map/tahsil brüt altındaysa doğru dolar."""
    by = {
        "2025-06-01": {"aylik": 2500.0, "tahsil": 0.0, "kalan": 2500.0},
        "2025-07-01": {"aylik": 2500.0, "tahsil": 1200.5, "kalan": 1299.5},
    }
    # onarım panel_from_map sonrası tahsil zaten map'ten gelir
    by2 = {
        "2025-06-01": {"aylik": 2500.0, "tahsil": 2500.0, "kalan": 0.0},
        "2025-07-01": {"aylik": 2500.0, "tahsil": 1200.5, "kalan": 1299.5},
    }
    out = onr._cap_panel_by_iso_for_onarim(by2, dondurma_aylar=set())
    check("111 tam ay", out["2025-06-01"]["tahsil"] == 2500.0, out)
    check("111 kismi", out["2025-07-01"]["tahsil"] == 1200.5, out)
    check("111 kalan", out["2025-07-01"]["kalan"] == 1299.5, out)


def test_cap_kira_sifir_yazilmaz():
    by = {
        "2026-01-01": {"aylik": 0.0, "tahsil": 500.0, "kalan": 0.0},
        "2026-02-01": {"aylik": 800.0, "tahsil": 800.0, "kalan": 0.0},
    }
    out = onr._cap_panel_by_iso_for_onarim(by, dondurma_aylar={"2026-03"})
    check("kira0 yok", "2026-01-01" not in out, out)
    check("kira var", out.get("2026-02-01", {}).get("tahsil") == 800.0, out)
    by_don = {
        "2026-03-01": {"aylik": 800.0, "tahsil": 800.0, "kalan": 0.0},
        "2026-04-01": {"aylik": 800.0, "tahsil": 900.0, "kalan": 0.0},
    }
    out2 = onr._cap_panel_by_iso_for_onarim(by_don, dondurma_aylar={"2026-03"})
    check("dondurma yok", "2026-03-01" not in out2, out2)
    check("dondurma sonrasi tavan", out2["2026-04-01"]["tahsil"] == 800.0, out2)


def test_geri_cap_kullanmaz():
    src = Path(onr.__file__).read_text(encoding="utf-8")
    i = src.find("def geri_yukle")
    j = src.find("\ndef main", i)
    body = src[i:j]
    check("geri cap yok", "_cap_panel_by_iso_for_onarim" not in body, body[:80])


if __name__ == "__main__":
    for fn in (
        test_dry_run_varsayilan_yazmaz,
        test_apply_without_allow_yazmaz,
        test_geri_allow_yok,
        test_kaynak_tahsilat_dokunmaz,
        test_dry_run_cli,
        test_cap_402_map_ustunde_tavan,
        test_cap_111_panel_sifir_tahsilat_var,
        test_cap_kira_sifir_yazilmaz,
        test_geri_cap_kullanmaz,
    ):
        print("---", fn.__name__)
        fn()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
