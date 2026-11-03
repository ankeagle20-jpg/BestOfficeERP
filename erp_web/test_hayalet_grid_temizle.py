# -*- coding: utf-8 -*-
"""hayalet_grid_temizle: iptal sınıfı ghost grid. Canlı yazma yok."""
from __future__ import annotations

import copy
import json
import os
import sys
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.pop("ALLOW_PROD_WRITE", None)
os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"

FAILS: list[str] = []


def check(name: str, ok: bool, ek="") -> None:
    print(
        ("ok " if ok else "FAIL ")
        + name
        + ((" :: " + str(ek)[:240]) if (ek and not ok) else "")
    )
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
    p = {"aylar": list(aylar), "taban_aylik_net": 200}
    p.update(meta)
    return p


def _ghost_iptal():
    """giriş=kap=2022-07, N=0 → son=2022-06; disk 2022-07.."""
    aylar = [_ay(2022, 7 + i, 240.0) for i in range(5)]
    return _payload(
        aylar,
        kapanis_tarihi="2022-07-26",
        kapanis_sonrasi_borc_ay=0,
        tahsilat_imza="0|0.00|1970-01-01T00:00:00",
    )


def test_a_uygun_dry_listeler():
    import scripts.hayalet_grid_temizle as hg

    disk = _ghost_iptal()
    h = hg.hayalet_uygunluk(
        durum="pasif",
        son=date(2022, 6, 1),
        giris=date(2022, 7, 26),
        kap=date(2022, 7, 26),
        disk_payload=disk,
        tahsil_n=0,
        panel_by_iso={},
        kap_giris_red=False,
        n_val=0,
        n0_aktif=True,
    )
    check("a_skip_false", h.get("skip") is False, h)
    check("a_need_write", h.get("need_write") is True, h)
    check("a_disk_n", h.get("disk_n") == 5, h.get("disk_n"))


def test_b_tahsilatli_atlanir():
    import scripts.hayalet_grid_temizle as hg

    h = hg.hayalet_uygunluk(
        durum="pasif",
        son=date(2022, 6, 1),
        giris=date(2022, 7, 26),
        kap=date(2022, 7, 26),
        disk_payload=_ghost_iptal(),
        tahsil_n=3,
        panel_by_iso={},
        kap_giris_red=False,
        n_val=0,
        n0_aktif=True,
    )
    check("b_skip", h.get("skip") is True)
    check("b_sebep", h.get("sebep") == "tahsilat_var", h.get("sebep"))


def test_c_borc_penceresi_dolu_atlanir():
    import scripts.hayalet_grid_temizle as hg

    # son >= giriş ayı → pencere boş değil
    h = hg.hayalet_uygunluk(
        durum="pasif",
        son=date(2023, 5, 1),
        giris=date(2022, 7, 7),
        kap=date(2023, 6, 30),
        disk_payload=_ghost_iptal(),
        tahsil_n=0,
        panel_by_iso={},
        kap_giris_red=False,
        n_val=0,
        n0_aktif=True,
    )
    check("c_skip", h.get("skip") is True)
    check("c_sebep", h.get("sebep") == "borc_penceresi_bos_degil", h.get("sebep"))


def test_d_kap_giris_gecersiz_atlanir():
    import scripts.hayalet_grid_temizle as hg

    h = hg.hayalet_uygunluk(
        durum="pasif",
        son=date(2025, 7, 1),
        giris=date(2026, 8, 8),
        kap=date(2025, 8, 13),
        disk_payload=_ghost_iptal(),
        tahsil_n=0,
        panel_by_iso={},
        kap_giris_red=True,
        n_val=0,
        n0_aktif=True,
    )
    check("d_skip", h.get("skip") is True)
    check("d_sebep", h.get("sebep") == "kap_giris_gecersiz", h.get("sebep"))


def test_e_idempotent_disk_bos():
    import scripts.hayalet_grid_temizle as hg

    h = hg.hayalet_uygunluk(
        durum="pasif",
        son=date(2022, 6, 1),
        giris=date(2022, 7, 26),
        kap=date(2022, 7, 26),
        disk_payload={"aylar": []},
        tahsil_n=0,
        panel_by_iso=None,
        kap_giris_red=False,
        n_val=0,
        n0_aktif=True,
    )
    check("e_skip", h.get("skip") is True)
    check("e_sebep", h.get("sebep") == "disk_bos_zaten", h.get("sebep"))
    check("e_need_false", h.get("need_write") is False)


def test_f_temizlik_sonrasi_yuzey_ve_yeniden_acilis():
    """Silme sonrası: force ile silinir; build boş ay → 0,01 hayalet yazılmaz; 2. koşu skip."""
    from routes import giris_routes as gr

    mid = 360001
    empty_build = {"aylar": [], "taban_aylik_net": 0}

    # force yokken healthy + PH build → silme engellenir
    healthy = _ghost_iptal()
    with mock.patch.object(gr, "_aylik_grid_cache_payload_from_db", return_value=healthy):
        with mock.patch.object(
            gr, "_build_aylik_grid_cache_payload", return_value=empty_build
        ):
            with mock.patch.object(gr, "execute") as ex:
                with mock.patch.object(gr, "_invalidate_aylik_grid_payload_mem"):
                    blocked = gr._delete_aylik_grid_cache_guarded(mid)
                    check("f_delete_blocked_without_force", blocked is False, blocked)
                    ex.reset_mock()
                    forced = gr._delete_aylik_grid_cache_guarded(
                        mid, force_placeholder_collapse=True
                    )
                    check("f_delete_force_ok", forced is True, forced)
                    check("f_force_execute", ex.call_count >= 1, ex.call_count)

    # Sayfa yeniden: old=None, build boş aylar — 0,01 bandı yazılmamalı
    with mock.patch.object(gr, "_aylik_grid_cache_payload_from_db", return_value=None):
        with mock.patch.object(
            gr, "_build_aylik_grid_cache_payload", return_value=empty_build
        ):
            with mock.patch.object(gr, "_persist_grid_cache_with_panel") as pers:
                out = gr._upsert_aylik_grid_cache(mid)
                if pers.call_count:
                    written = pers.call_args[0][1] if pers.call_args and pers.call_args[0] else None
                    n_pos = 0
                    for a in (written or {}).get("aylar") or []:
                        try:
                            if float(a.get("brut_tutar_kdv") or 0) > 0.05:
                                n_pos += 1
                        except (TypeError, ValueError):
                            pass
                    check("f_persist_hayalet_tl_yok", n_pos == 0, n_pos)
                else:
                    check("f_persist_yok_veya_bos", out in (None, empty_build) or out == {} or (
                        isinstance(out, dict) and not (out.get("aylar") or [])
                    ), out)

    import scripts.hayalet_grid_temizle as hg

    h2 = hg.hayalet_uygunluk(
        durum="pasif",
        son=date(2022, 6, 1),
        giris=date(2022, 7, 26),
        kap=date(2022, 7, 26),
        disk_payload=None,
        tahsil_n=0,
        panel_by_iso=None,
        kap_giris_red=False,
        n_val=0,
        n0_aktif=True,
    )
    check("f_ikinci_calisma_skip", h2.get("need_write") is False)
    check("f_ikinci_sebep", h2.get("sebep") == "disk_bos_zaten", h2.get("sebep"))


def test_g_mevcut_korumalar_degismedi():
    """kısmi regresyon + cap_trim: force=False iken eski davranış."""
    from routes import giris_routes as gr

    old = _payload(
        [_ay(2022, m, 240.0) for m in range(1, 7)]
        + [_ay(2022, m, 240.0) for m in range(7, 13)]
    )
    # yeni: ilk 6 ay 0.01 (kısmi PH), sonrası gerçek
    new = _payload(
        [_ay(2022, m, 0.01) for m in range(1, 7)]
        + [_ay(2022, m, 240.0) for m in range(7, 13)]
    )
    lost = gr._aylik_grid_partial_regression_lost_isos(old, new, date(2022, 12, 1))
    check("g_lost_var", len(lost) >= 1, lost)

    p_trim, p_skip = gr._aylik_grid_partial_placeholder_regression_action(
        999001, new, force_placeholder_collapse=False, old_payload=old
    )
    check(
        "g_partial_aksiyon",
        p_trim is not None or p_skip is True,
        (p_trim is not None, p_skip),
    )

    # cap_trim: TAM PH new + healthy old + son → trim old
    son = date(2022, 8, 1)
    kyc = {
        "durum": "pasif",
        "kapanis_tarihi": date(2022, 6, 30),
        "kapanis_sonrasi_borc_ay": None,
        "sozlesme_tarihi": date(2022, 1, 1),
        "rent_start_date": date(2022, 1, 1),
    }
    new_full_ph = _payload([_ay(2022, m, 0.01) for m in range(1, 13)])
    with mock.patch.object(gr, "_aylik_grid_son_borc_for_mid", return_value=(son, kyc)):
        with mock.patch.object(
            gr, "_aylik_grid_cache_payload_from_db", return_value=old
        ):
            trimmed = gr._aylik_grid_cap_trim_if_placeholder_collapse(
                999002,
                new_full_ph,
                force_placeholder_collapse=False,
                old_payload=old,
            )
            check("g_cap_trim_not_none", trimmed is not None, trimmed)
            if trimmed:
                aylar = trimmed.get("aylar") or []
                check("g_cap_trim_kirpildi", len(aylar) <= 8, len(aylar))
                check(
                    "g_cap_trim_force_none",
                    gr._aylik_grid_cap_trim_if_placeholder_collapse(
                        999002,
                        new_full_ph,
                        force_placeholder_collapse=True,
                        old_payload=old,
                    )
                    is None,
                )


def test_panel_tahsil_atlanir():
    import scripts.hayalet_grid_temizle as hg

    h = hg.hayalet_uygunluk(
        durum="pasif",
        son=date(2022, 6, 1),
        giris=date(2022, 7, 26),
        kap=date(2022, 7, 26),
        disk_payload=_ghost_iptal(),
        tahsil_n=0,
        panel_by_iso={"2022-07-01": {"tahsil": 100.0, "kalan": 0}},
        kap_giris_red=False,
        n_val=0,
        n0_aktif=True,
    )
    check("panel_skip", h.get("skip") is True)
    check("panel_sebep", h.get("sebep") == "panel_tahsil_var", h.get("sebep"))


def test_pre_kap_borc_atlanir():
    import scripts.hayalet_grid_temizle as hg

    # N≠0 → kap dahil pre; diskte kap ayı öncesi ay
    disk = _payload(
        [_ay(2022, 5, 240.0), _ay(2022, 7, 240.0)],
        tahsilat_imza="0|0.00|1970-01-01",
    )
    h = hg.hayalet_uygunluk(
        durum="pasif",
        son=date(2022, 6, 1),
        giris=date(2022, 7, 26),
        kap=date(2022, 7, 26),
        disk_payload=disk,
        tahsil_n=0,
        panel_by_iso={},
        kap_giris_red=False,
        n_val=1,  # N0 kuralı kapalı → kap_dahil=True → Mayıs pre
        n0_aktif=True,
    )
    check("pre_skip", h.get("skip") is True)
    check("pre_sebep", h.get("sebep") == "pre_kap_borc_var", h.get("sebep"))


def main():
    test_a_uygun_dry_listeler()
    test_b_tahsilatli_atlanir()
    test_c_borc_penceresi_dolu_atlanir()
    test_d_kap_giris_gecersiz_atlanir()
    test_e_idempotent_disk_bos()
    test_f_temizlik_sonrasi_yuzey_ve_yeniden_acilis()
    test_g_mevcut_korumalar_degismedi()
    test_panel_tahsil_atlanir()
    test_pre_kap_borc_atlanir()
    if FAILS:
        print("FAILS", FAILS)
        return 1
    print("ALL_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
