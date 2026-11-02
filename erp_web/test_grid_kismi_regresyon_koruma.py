# -*- coding: utf-8 -*-
"""Kısmi yer tutucu regresyonu koruması (eski gerçek → yeni ≤ yer tutucu bandı)."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)
os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)

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
        "odenen_tutar_kdv": float(extra.pop("odenen", 0) or 0),
        "tahsil_edildi": False,
        "kalan_tutar_kdv": tutar,
    }
    d.update(extra)
    return d


def _payload(aylar, **meta):
    p = {"aylar": list(aylar), "taban_aylik_net": 1000}
    p.update(meta)
    return p


def _healthy_36():
    """2022-01..2024-12: 36 ay × 2400; ilk 12 ay odenen=100 (korunmalı)."""
    aylar = []
    for i in range(36):
        y = 2022 + i // 12
        m = i % 12 + 1
        od = 100.0 if i < 12 else 0.0
        aylar.append(_ay(y, m, 2400.0, odenen=od))
    return _payload(aylar)


def _partial_ph_build():
    """İlk 12 ay 0,01; sonraki 24 ay gerçek 2400 — tam-ph değil, kısmi regresyon."""
    aylar = []
    for i in range(36):
        y = 2022 + i // 12
        m = i % 12 + 1
        t = 0.01 if i < 12 else 2400.0
        aylar.append(_ay(y, m, t))
    return _payload(aylar)


def _cap_short_real():
    """Cap kısalması: yalnız 12 ay, hepsi gerçek (meşru)."""
    aylar = [_ay(2022, m, 2400.0) for m in range(1, 13)]
    return _payload(aylar, bitis="2022-12-01", kira_son_borc_ayi="2022-12-01")


def _real_diff_build():
    """Ortak aylarda gerçek→gerçek farklı tutar (meşru)."""
    aylar = []
    for i in range(36):
        y = 2022 + i // 12
        m = i % 12 + 1
        aylar.append(_ay(y, m, 3000.0))
    return _payload(aylar)


def _upsert_ctx(gr, *, old, build, son, kyc=None, writes=None):
    writes = writes if writes is not None else []
    kyc = kyc or {
        "durum": "pasif",
        "kapanis_tarihi": date(2022, 6, 30),
        "kapanis_sonrasi_borc_ay": None,
        "rent_start_date": date(2022, 1, 1),
    }
    return mock.patch.object(gr, "_invalidate_musteri_kyc_grid_mem"), mock.patch.object(
        gr, "_build_aylik_grid_cache_payload", return_value=build
    ), mock.patch.object(
        gr, "_aylik_grid_cache_payload_from_db", return_value=old
    ), mock.patch.object(
        gr, "_musteri_kyc_grup_for_aylik_grid", return_value=kyc
    ), mock.patch.object(
        gr, "kira_son_borc_ayi", return_value=son
    ), mock.patch.object(
        gr, "_aylik_grid_cache_skip_refresh_enabled", return_value=False
    ), mock.patch.object(
        gr, "_aylik_grid_freshness_shadow_log_enabled", return_value=False
    ), mock.patch.object(
        gr, "_aylik_grid_cache_payload_tahsil_guncelle", side_effect=lambda mid, p, **k: p
    ), mock.patch.object(
        gr, "_ensure_aylik_grid_cache_table"
    ), mock.patch.object(
        gr, "execute", side_effect=lambda *a, **k: writes.append(a)
    ), mock.patch.object(
        gr, "_aylik_grid_mem_set"
    ), mock.patch.object(
        gr, "_invalidate_aylik_grid_payload_mem"
    )


def test_1_kismi_cap_yok_skip():
    """(1) kısmi yer tutucu + eski healthy + cap yok → skip, yazma yok."""
    from routes import giris_routes as gr

    old = _healthy_36()
    build = _partial_ph_build()
    writes = []
    patches = _upsert_ctx(gr, old=old, build=build, son=None, writes=writes)
    # persist'i de gerçek çalıştır (yazma execute ile izlenir); skip erken döner
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[
        6
    ], patches[7], patches[8], patches[9], patches[10], patches[11]:
        out = gr._upsert_aylik_grid_cache(360)
    check("1_skip_write", writes == [], writes)
    check("1_out_old", out is old)


def test_2_kismi_cap_trim_ok():
    """(2) kısmi + cap + trim ok → eski kırpılır, tutar/odenen korunur."""
    os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
    from routes import giris_routes as gr
    import importlib
    import kira_kapanis_cap as kc

    importlib.reload(kc)

    old = _healthy_36()
    build = _partial_ph_build()
    son = date(2022, 12, 1)
    writes = []
    patches = _upsert_ctx(gr, old=old, build=build, son=son, writes=writes)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[
        6
    ], patches[7], patches[8], patches[9], patches[10], patches[11]:
        out = gr._upsert_aylik_grid_cache(360)
    check("2_yazildi", len(writes) >= 1, len(writes))
    check("2_out_dict", isinstance(out, dict))
    aylar = (out or {}).get("aylar") or []
    check("2_n12", len(aylar) == 12, len(aylar))
    s = round(sum(float(a["tutar_kdv_dahil"]) for a in aylar), 2)
    check("2_sum_28800", abs(s - 28800.0) < 0.01, s)
    od = round(sum(float(a.get("odenen_tutar_kdv") or 0) for a in aylar), 2)
    check("2_odenen_korundu", abs(od - 1200.0) < 0.01, od)
    check("2_not_ph", not gr._aylik_grid_payload_is_placeholder(out))
    check("2_first_real", float(aylar[0]["tutar_kdv_dahil"]) == 2400.0)
    os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
    importlib.reload(kc)


def test_3_kismi_cap_trim_none_skip():
    """(3a) kısmi + cap + trim None → skip. (3b) tam-ph + trim None → skip (335 tipi)."""
    from routes import giris_routes as gr

    old = _healthy_36()
    build = _partial_ph_build()
    son = date(2022, 12, 1)
    writes = []
    patches = _upsert_ctx(gr, old=old, build=build, son=son, writes=writes)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[
        6
    ], patches[7], patches[8], patches[9], patches[10], patches[11], mock.patch.object(
        gr, "_aylik_grid_trim_payload_to_son_borc", return_value=None
    ):
        out = gr._upsert_aylik_grid_cache(335)
    check("3a_skip_write", writes == [], writes)
    check("3a_out_old", out is old)

    # 3b: tüm aylar 0,01 (tam ph) + healthy + cap + trim None → skip (yazma yok)
    build_ph = _payload([_ay(2022, m, 0.01) for m in range(1, 13)])
    writes2 = []
    patches2 = _upsert_ctx(gr, old=old, build=build_ph, son=son, writes=writes2)
    with patches2[0], patches2[1], patches2[2], patches2[3], patches2[4], patches2[
        5
    ], patches2[6], patches2[7], patches2[8], patches2[9], patches2[10], patches2[
        11
    ], mock.patch.object(gr, "_aylik_grid_trim_payload_to_son_borc", return_value=None):
        out2 = gr._upsert_aylik_grid_cache(435)
    check("3b_skip_write", writes2 == [], writes2)
    check("3b_out_old", out2 is old)


def test_4_mesru_kapanis_kisalmasi_yazilir():
    """(4) yeni cap nedeniyle kısa, ortak aylarda tutarlar eşit → yazılır."""
    from routes import giris_routes as gr

    old = _healthy_36()
    build = _cap_short_real()
    son = date(2022, 12, 1)
    writes = []
    patches = _upsert_ctx(gr, old=old, build=build, son=son, writes=writes)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[
        6
    ], patches[7], patches[8], patches[9], patches[10], patches[11]:
        out = gr._upsert_aylik_grid_cache(10)
    check("4_yazildi", len(writes) >= 1)
    check("4_out_build_n", len((out or {}).get("aylar") or []) == 12)
    check("4_first", float((out or {})["aylar"][0]["tutar_kdv_dahil"]) == 2400.0)


def test_5_yeni_gercek_farkli_yazilir():
    """(5) yeni gerçek tutar farklı (>thr → >thr) → yazılır."""
    from routes import giris_routes as gr

    old = _healthy_36()
    build = _real_diff_build()
    writes = []
    patches = _upsert_ctx(gr, old=old, build=build, son=None, writes=writes)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[
        6
    ], patches[7], patches[8], patches[9], patches[10], patches[11]:
        out = gr._upsert_aylik_grid_cache(11)
    check("5_yazildi", len(writes) >= 1)
    check("5_tutar_3000", float((out or {})["aylar"][0]["tutar_kdv_dahil"]) == 3000.0)


def test_6_eski_unhealthy_mevcut_davranis():
    """(6) eski sağlıksız/yok → kısmi koruma tetiklenmez, yeni yazılır."""
    from routes import giris_routes as gr

    old = None
    build = _partial_ph_build()
    writes = []
    patches = _upsert_ctx(gr, old=old, build=build, son=None, writes=writes)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[
        6
    ], patches[7], patches[8], patches[9], patches[10], patches[11]:
        out = gr._upsert_aylik_grid_cache(12)
    check("6_yazildi", len(writes) >= 1)
    check("6_out_build", out is build or float((out or {})["aylar"][0]["tutar_kdv_dahil"]) == 0.01)

    old_ph = _payload([_ay(2022, 1, 0.01) for _ in range(20)])
    writes2 = []
    patches2 = _upsert_ctx(gr, old=old_ph, build=build, son=None, writes=writes2)
    with patches2[0], patches2[1], patches2[2], patches2[3], patches2[4], patches2[
        5
    ], patches2[6], patches2[7], patches2[8], patches2[9], patches2[10], patches2[11]:
        out2 = gr._upsert_aylik_grid_cache(13)
    check("6b_yazildi", len(writes2) >= 1)
    check("6b_ph_first", float((out2 or {})["aylar"][0]["tutar_kdv_dahil"]) == 0.01)


def test_7_idempotent():
    """(7) trim sonucu üzerine tekrar partial action → aynı kırpma / skip yok."""
    from routes import giris_routes as gr

    old = _healthy_36()
    son = date(2022, 12, 1)
    with mock.patch.object(
        gr, "_aylik_grid_cache_payload_from_db", return_value=old
    ), mock.patch.object(
        gr, "_musteri_kyc_grup_for_aylik_grid", return_value={"durum": "pasif"}
    ), mock.patch.object(gr, "kira_son_borc_ayi", return_value=son):
        t1, s1 = gr._aylik_grid_partial_placeholder_regression_action(
            360, _partial_ph_build(), old_payload=old
        )
        check("7_trim1", t1 is not None and not s1)
        t2, s2 = gr._aylik_grid_partial_placeholder_regression_action(
            360, t1, old_payload=old
        )
        # kırpılmış payload ortak aylarda gerçek → lost yok
        check("7_no_retrigger", t2 is None and s2 is False)
        check("7_n", len(t1["aylar"]) == 12)
        t3 = gr._aylik_grid_trim_payload_to_son_borc(t1, son, {})
        check("7_trim_idem", len(t3["aylar"]) == 12)


def test_8_lost_isos_helper():
    """Ay bazlı lost: ortak / cap filtresi."""
    from routes import giris_routes as gr

    old = _healthy_36()
    build = _partial_ph_build()
    lost = gr._aylik_grid_partial_regression_lost_isos(old, build, None)
    check("8_lost_12", len(lost) == 12, len(lost))
    lost_cap = gr._aylik_grid_partial_regression_lost_isos(
        old, build, date(2022, 6, 1)
    )
    check("8_lost_cap6", len(lost_cap) == 6, len(lost_cap))
    # meşru fark
    lost_ok = gr._aylik_grid_partial_regression_lost_isos(
        old, _real_diff_build(), None
    )
    check("8_mesru0", lost_ok == [])
    # meşru kısalma
    lost_short = gr._aylik_grid_partial_regression_lost_isos(
        old, _cap_short_real(), date(2022, 12, 1)
    )
    check("8_short0", lost_short == [], lost_short)


if __name__ == "__main__":
    test_1_kismi_cap_yok_skip()
    test_2_kismi_cap_trim_ok()
    test_3_kismi_cap_trim_none_skip()
    test_4_mesru_kapanis_kisalmasi_yazilir()
    test_5_yeni_gercek_farkli_yazilir()
    test_6_eski_unhealthy_mevcut_davranis()
    test_7_idempotent()
    test_8_lost_isos_helper()
    if FAILS:
        print("FAIL", FAILS)
        raise SystemExit(1)
    print("ALL_OK")
