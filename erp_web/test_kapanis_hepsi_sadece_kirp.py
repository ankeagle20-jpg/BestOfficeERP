# -*- coding: utf-8 -*-
"""--sadece-kirp: disk kırpma (rebuild yok). Canlı yazma yok."""
from __future__ import annotations

import copy
import os
import sys
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.pop("ALLOW_PROD_WRITE", None)
os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek="") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _ay(y, m, tutar, **extra):
    d = {
        "yil": y,
        "ay": m,
        "tutar_kdv_dahil": tutar,
        "brut_tutar_kdv": tutar,
        "odenen_tutar_kdv": 0,
        "tahsil_edildi": False,
        "kalan_tutar_kdv": tutar,
    }
    d.update(extra)
    return d


def _payload(aylar, **meta):
    p = {"aylar": list(aylar), "taban_aylik_net": 1000}
    p.update(meta)
    return p


def _healthy_span():
    """2022-01..2024-12 (36 ay); kap=2022-06 → pre 6 ay, son=2023-06 → kırp 18 ay."""
    aylar = []
    for i in range(36):
        y = 2022 + i // 12
        m = i % 12 + 1
        aylar.append(_ay(y, m, 240.0))
    return _payload(
        aylar,
        kapanis_tarihi="2022-06-30",
        kapanis_sonrasi_borc_ay=None,
        baslangic="2022-01-01",
        bitis="2024-12-01",
    )


def _ph_payload(n=12):
    aylar = [_ay(2022, i + 1, 0.01) for i in range(n)]
    return _payload(aylar)


def test_a_pre_kap_degismez():
    import scripts.kapanis_hepsi_yeniden_hesapla as sc
    from routes import giris_routes as gr

    disk = _healthy_span()
    son = date(2023, 6, 1)
    kap = date(2022, 6, 30)
    kyc = {"durum": "pasif", "kapanis_tarihi": kap, "kapanis_sonrasi_borc_ay": None}
    h = sc.sadece_kirp_hesapla(
        disk,
        son,
        kap,
        kyc,
        is_placeholder=gr._aylik_grid_payload_is_placeholder,
        is_healthy=gr._aylik_grid_payload_is_healthy,
        trim_fn=gr._aylik_grid_trim_payload_to_son_borc,
    )
    check("a_no_skip", h.get("skip") is False, h.get("sebep"))
    check("a_pre_ay", h.get("pre_ay") == 6, h.get("pre_ay"))
    check("a_pre_tl", abs(float(h.get("pre_tl") or 0) - 1440.0) < 0.01, h.get("pre_tl"))
    # kırpılmış payload'da pre satırlar birebir
    pre0 = sc._pre_kap_rows(disk, kap)
    pre1 = sc._pre_kap_rows(h.get("trimmed"), kap)
    check("a_pre_rows_eq", pre0 == pre1, (len(pre0), len(pre1)))
    # odenen alanları
    for a in (h.get("trimmed") or {}).get("aylar") or []:
        if int(a["yil"]) == 2022 and int(a["ay"]) <= 6:
            check("a_odenen_0", float(a.get("odenen_tutar_kdv") or 0) == 0.0)
            check("a_brut_240", abs(float(a.get("brut_tutar_kdv") or 0) - 240.0) < 0.01)
            break


def test_b_kap_sonrasi_kirpilir():
    import scripts.kapanis_hepsi_yeniden_hesapla as sc
    from routes import giris_routes as gr

    disk = _healthy_span()
    son = date(2023, 6, 1)
    kap = date(2022, 6, 30)
    kyc = {"durum": "pasif", "kapanis_tarihi": kap}
    h = sc.sadece_kirp_hesapla(
        disk,
        son,
        kap,
        kyc,
        is_placeholder=gr._aylik_grid_payload_is_placeholder,
        is_healthy=gr._aylik_grid_payload_is_healthy,
        trim_fn=gr._aylik_grid_trim_payload_to_son_borc,
    )
    check("b_old_n36", h.get("old_n") == 36, h.get("old_n"))
    check("b_new_n18", h.get("new_n") == 18, h.get("new_n"))  # 2022-01..2023-06
    check("b_kirp_ay", h.get("kirpilan_ay") == 18, h.get("kirpilan_ay"))
    aylar = (h.get("trimmed") or {}).get("aylar") or []
    last = aylar[-1]
    check("b_son_ay", int(last["yil"]) == 2023 and int(last["ay"]) == 6, last)
    # son sonrası yok
    beyond = [a for a in aylar if date(int(a["yil"]), int(a["ay"]), 1) > son]
    check("b_beyond_0", len(beyond) == 0, len(beyond))


def test_c_sagliksiz_islem_yok():
    import scripts.kapanis_hepsi_yeniden_hesapla as sc
    from routes import giris_routes as gr

    disk = _ph_payload(24)
    son = date(2023, 6, 1)
    kap = date(2022, 6, 30)
    h = sc.sadece_kirp_hesapla(
        disk,
        son,
        kap,
        {},
        is_placeholder=gr._aylik_grid_payload_is_placeholder,
        is_healthy=gr._aylik_grid_payload_is_healthy,
        trim_fn=gr._aylik_grid_trim_payload_to_son_borc,
    )
    check("c_skip", h.get("skip") is True)
    check("c_sebep", h.get("sebep") == "disk_sagliksiz_veya_placeholder", h.get("sebep"))
    check("c_no_trim", h.get("trimmed") is None)
    check("c_no_write", h.get("need_write") is False)

    # son_borc yok
    healthy = _healthy_span()
    h2 = sc.sadece_kirp_hesapla(
        healthy,
        None,
        date(2022, 6, 30),
        {},
        is_placeholder=gr._aylik_grid_payload_is_placeholder,
        is_healthy=gr._aylik_grid_payload_is_healthy,
        trim_fn=gr._aylik_grid_trim_payload_to_son_borc,
    )
    check("c_son_yok", h2.get("sebep") == "son_borc_yok", h2.get("sebep"))


def test_d_idempotent():
    import scripts.kapanis_hepsi_yeniden_hesapla as sc
    from routes import giris_routes as gr

    disk = _healthy_span()
    son = date(2023, 6, 1)
    kap = date(2022, 6, 30)
    kyc = {"durum": "pasif", "kapanis_tarihi": kap}
    h1 = sc.sadece_kirp_hesapla(
        disk,
        son,
        kap,
        kyc,
        is_placeholder=gr._aylik_grid_payload_is_placeholder,
        is_healthy=gr._aylik_grid_payload_is_healthy,
        trim_fn=gr._aylik_grid_trim_payload_to_son_borc,
    )
    check("d_need1", h1.get("need_write") is True)
    trimmed = h1.get("trimmed")
    h2 = sc.sadece_kirp_hesapla(
        trimmed,
        son,
        kap,
        kyc,
        is_placeholder=gr._aylik_grid_payload_is_placeholder,
        is_healthy=gr._aylik_grid_payload_is_healthy,
        trim_fn=gr._aylik_grid_trim_payload_to_son_borc,
    )
    check("d_no_skip2", h2.get("skip") is False, h2.get("sebep"))
    check("d_idempotent", h2.get("idempotent_already") is True)
    check("d_need2_0", h2.get("need_write") is False)
    check("d_n_same", h2.get("old_n") == h2.get("new_n") == 18)


def test_e_mevcut_mod_ayni():
    """--sadece-kirp yokken DRY-RUN eski çıktı (mode=DRY-RUN, plan_sample)."""
    import scripts.kapanis_hepsi_yeniden_hesapla as sc

    captured = []

    def fake_j(obj):
        captured.append(obj)

    with mock.patch.object(sc, "_j", side_effect=fake_j), mock.patch(
        "dotenv.load_dotenv"
    ), mock.patch("flask.Flask") as FakeFlask, mock.patch(
        "kira_kapanis_cap.hepsi_sozlesme_yili_aktif", return_value=False
    ), mock.patch(
        "kira_kapanis_cap.kira_son_borc_ayi", return_value=None
    ), mock.patch(
        "routes.giris_routes._musteri_kyc_grup_for_aylik_grid",
        return_value={"durum": "pasif", "kapanis_tarihi": "2022-01-01"},
    ), mock.patch(
        "routes.giris_routes.tahsilat_degisti"
    ) as td, mock.patch.object(
        sys, "argv", ["kapanis_hepsi_yeniden_hesapla.py", "--mid", "51"]
    ):
        FakeFlask.return_value.app_context.return_value.push = lambda: None
        # sadece-kirp çağrılmamalı
        with mock.patch.object(sc, "_run_sadece_kirp") as sk:
            rc = sc.main()
            check("e_rc0", rc == 0)
            check("e_sadece_kirp_cagrilmadi", sk.call_count == 0)
            check("e_tahsilat_degisti_cagrilmadi", td.call_count == 0)
    check("e_json_var", len(captured) >= 1, len(captured))
    mode = (captured[0] or {}).get("mode")
    check("e_mode_dry", mode == "DRY-RUN", mode)
    check("e_mid_n", (captured[0] or {}).get("mid_n") == 1)


def test_a_pre_kap():
    test_a_pre_kap_degismez()


def test_b_kirp():
    test_b_kap_sonrasi_kirpilir()


def test_c_skip():
    test_c_sagliksiz_islem_yok()


def test_d_idemp():
    test_d_idempotent()


def test_e_legacy():
    test_e_mevcut_mod_ayni()


if __name__ == "__main__":
    # Tek tek çağrı için argparse benzeri seçim
    which = (sys.argv[1] if len(sys.argv) > 1 else "all").strip()
    mapping = {
        "a": test_a_pre_kap_degismez,
        "b": test_b_kap_sonrasi_kirpilir,
        "c": test_c_sagliksiz_islem_yok,
        "d": test_d_idempotent,
        "e": test_e_mevcut_mod_ayni,
    }
    if which == "all":
        for fn in mapping.values():
            fn()
    elif which in mapping:
        mapping[which]()
    else:
        print("kullanim: a|b|c|d|e|all")
        raise SystemExit(2)
    if FAILS:
        print("FAIL", FAILS)
        raise SystemExit(1)
    print("ALL_OK")
