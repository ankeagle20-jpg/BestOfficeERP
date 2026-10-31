# -*- coding: utf-8 -*-
"""kapanis_hepsi_yeniden_hesapla audit_yaz imzası. Canlı yazma yok."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_audit_mid_imza():
    import scripts.kapanis_hepsi_yeniden_hesapla as sc

    calls = []

    def fake_audit(calistir, *, olay, varlik, kayit_id, musteri_id, kullanici, anlik, yeni=None, eski_yaz=True):
        calls.append(
            {
                "calistir": calistir,
                "olay": olay,
                "varlik": varlik,
                "kayit_id": kayit_id,
                "musteri_id": musteri_id,
                "kullanici": kullanici,
                "anlik": anlik,
                "yeni": yeni,
            }
        )
        # imza: ilk arg çağrılabilir execute
        check("calistir_callable", callable(calistir))
        return True

    execute = mock.Mock(name="execute")
    with mock.patch("audit_log.audit_yaz", side_effect=fake_audit):
        ok = sc._audit_mid(
            execute,
            149,
            {"mid": 149, "son_borc_ayi": None, "ay_n": 71},
            {"mid": 149, "son_borc_ayi": "2022-09-01", "ay_n": 12},
        )
    check("audit_ok", ok is True)
    check("bir_cagri", len(calls) == 1, calls)
    c = calls[0]
    check("olay", c["olay"] == "kapanis_hepsi_yeniden_hesapla")
    check("kayit_id", c["kayit_id"] == 149)
    check("musteri_id", c["musteri_id"] == 149)
    check("anlik_ay", c["anlik"].get("ay_n") == 71)
    check("yeni_son", c["yeni"].get("son_borc_ayi") == "2022-09-01")
    check("execute_gecildi", c["calistir"] is execute)


def test_eski_yanlis_imza_kirilir():
    """Eski iki-argümanlı çağrı TypeError verir (regresyon)."""
    from audit_log import audit_yaz

    try:
        audit_yaz("kapanis_hepsi_yeniden_hesapla", {"mid_n": 1})
        check("eski_imza_typeerror", False, "TypeError beklenirdi")
    except TypeError:
        check("eski_imza_typeerror", True)


if __name__ == "__main__":
    test_audit_mid_imza()
    test_eski_yanlis_imza_kirilir()
    if FAILS:
        print("FAIL", FAILS)
        raise SystemExit(1)
    print("ALL_OK")
