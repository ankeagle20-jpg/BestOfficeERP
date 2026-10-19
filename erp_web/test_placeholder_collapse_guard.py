# -*- coding: utf-8 -*-
"""Placeholder çöküşü guard: upsert/persist/delete skip; bilinçli force; akış mock'ları."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _ph_aylar(n=50, tutar=0.01):
    return {
        "aylar": [
            {
                "yil": 2022 + (i // 12),
                "ay": (i % 12) + 1,
                "tutar_kdv_dahil": tutar,
                "brut_tutar_kdv": tutar,
            }
            for i in range(n)
        ]
    }


def _healthy_aylar(n=50, tutar=785.32):
    return {
        "aylar": [
            {
                "yil": 2022 + (i // 12),
                "ay": (i % 12) + 1,
                "tutar_kdv_dahil": tutar,
                "brut_tutar_kdv": tutar,
            }
            for i in range(n)
        ]
    }


def test_stats_and_flags():
    from routes import giris_routes as gr

    h = _healthy_aylar(50, 785.32)
    p = _ph_aylar(50, 0.01)
    s, n, ph = gr._aylik_grid_payload_brut_stats(h)
    check("healthy_sum", s > 39000 and n == 50, (s, n, ph))
    check("healthy_flag", gr._aylik_grid_payload_is_healthy(h))
    check("healthy_not_ph", not gr._aylik_grid_payload_is_placeholder(h))
    sp, np, php = gr._aylik_grid_payload_brut_stats(p)
    check("ph_sum", abs(sp - 0.5) < 0.01 and np == 50, (sp, np, php))
    check("ph_flag", gr._aylik_grid_payload_is_placeholder(p))
    check("ph_not_healthy", not gr._aylik_grid_payload_is_healthy(p))


def test_326_collapse_upsert_korur():
    from routes import giris_routes as gr

    old = _healthy_aylar(50, 785.324)  # ~39266
    build = _ph_aylar(50, 0.01)
    writes = []
    logs = []

    class _H(logging.Handler):
        def emit(self, record):
            logs.append(record.getMessage())

    h = _H()
    lg = logging.getLogger(gr.__name__)
    lg.addHandler(h)
    try:
        with mock.patch.object(
            gr, "_invalidate_musteri_kyc_grid_mem", return_value=None
        ), mock.patch.object(
            gr, "_build_aylik_grid_cache_payload", return_value=build
        ), mock.patch.object(
            gr, "_aylik_grid_cache_payload_from_db", return_value=old
        ), mock.patch.object(
            gr, "_aylik_grid_freshness_stamp", side_effect=lambda mid, p: p
        ), mock.patch.object(
            gr, "_aylik_grid_cache_skip_refresh_enabled", return_value=False
        ), mock.patch.object(
            gr, "_aylik_grid_freshness_shadow_log_enabled", return_value=False
        ), mock.patch.object(
            gr,
            "_persist_grid_cache_with_panel",
            side_effect=lambda *a, **k: writes.append(a) or a[1],
        ):
            out = gr._upsert_aylik_grid_cache(326)
    finally:
        lg.removeHandler(h)

    check("326_upsert_no_write", writes == [], writes)
    check("326_upsert_returns_old", out is old)
    check(
        "326_log",
        any("placeholder_collapse_skip" in m and "mid=326" in m for m in logs),
        logs[:3],
    )


def test_normal_guncelleme_yazar():
    from routes import giris_routes as gr

    old = _healthy_aylar(12, 1000)
    build = _healthy_aylar(12, 1100)
    writes = []
    with mock.patch.object(
        gr, "_invalidate_musteri_kyc_grid_mem", return_value=None
    ), mock.patch.object(
        gr, "_build_aylik_grid_cache_payload", return_value=build
    ), mock.patch.object(
        gr, "_aylik_grid_cache_payload_from_db", return_value=old
    ), mock.patch.object(
        gr, "_aylik_grid_freshness_stamp", side_effect=lambda mid, p: p
    ), mock.patch.object(
        gr, "_aylik_grid_cache_skip_refresh_enabled", return_value=False
    ), mock.patch.object(
        gr, "_aylik_grid_freshness_shadow_log_enabled", return_value=False
    ), mock.patch.object(
        gr,
        "_persist_grid_cache_with_panel",
        side_effect=lambda mid, payload, **k: writes.append(payload) or payload,
    ):
        out = gr._upsert_aylik_grid_cache(100)
    check("normal_write", len(writes) == 1 and writes[0] is build)
    check("normal_out", out is build)


def test_yeni_musteri_yazar():
    from routes import giris_routes as gr

    build = _healthy_aylar(6, 500)
    writes = []
    with mock.patch.object(
        gr, "_invalidate_musteri_kyc_grid_mem", return_value=None
    ), mock.patch.object(
        gr, "_build_aylik_grid_cache_payload", return_value=build
    ), mock.patch.object(
        gr, "_aylik_grid_cache_payload_from_db", return_value=None
    ), mock.patch.object(
        gr, "_aylik_grid_cache_skip_refresh_enabled", return_value=False
    ), mock.patch.object(
        gr, "_aylik_grid_freshness_shadow_log_enabled", return_value=False
    ), mock.patch.object(
        gr,
        "_persist_grid_cache_with_panel",
        side_effect=lambda mid, payload, **k: writes.append(payload) or payload,
    ):
        out = gr._upsert_aylik_grid_cache(1)
    check("yeni_write", len(writes) == 1)
    check("yeni_out", out is build)


def test_force_placeholder_yazar():
    from routes import giris_routes as gr

    old = _healthy_aylar(50, 800)
    build = _ph_aylar(50, 0.01)
    writes = []
    with mock.patch.object(
        gr, "_invalidate_musteri_kyc_grid_mem", return_value=None
    ), mock.patch.object(
        gr, "_build_aylik_grid_cache_payload", return_value=build
    ), mock.patch.object(
        gr, "_aylik_grid_cache_payload_from_db", return_value=old
    ), mock.patch.object(
        gr, "_aylik_grid_cache_skip_refresh_enabled", return_value=False
    ), mock.patch.object(
        gr, "_aylik_grid_freshness_shadow_log_enabled", return_value=False
    ), mock.patch.object(
        gr,
        "_persist_grid_cache_with_panel",
        side_effect=lambda mid, payload, **k: writes.append(
            k.get("force_placeholder_collapse")
        )
        or payload,
    ):
        out = gr._upsert_aylik_grid_cache(326, force_placeholder_collapse=True)
    check("force_write", len(writes) == 1 and writes[0] is True, writes)
    check("force_out_ph", out is build)


def test_delete_guard_korur():
    from routes import giris_routes as gr

    old = _healthy_aylar(50, 800)
    build = _ph_aylar(50, 0.01)
    deleted = []
    with mock.patch.object(
        gr, "_aylik_grid_cache_payload_from_db", return_value=old
    ), mock.patch.object(
        gr, "_build_aylik_grid_cache_payload", return_value=build
    ), mock.patch.object(
        gr, "execute", side_effect=lambda *a, **k: deleted.append(a[0])
    ), mock.patch.object(
        gr, "_invalidate_aylik_grid_payload_mem", return_value=None
    ):
        ok = gr._delete_aylik_grid_cache_guarded(444)
    check("delete_skipped", ok is False)
    check("delete_no_sql", deleted == [], deleted)


def test_delete_normal_siler():
    from routes import giris_routes as gr

    old = _healthy_aylar(12, 1000)
    build = _healthy_aylar(12, 1050)
    deleted = []
    with mock.patch.object(
        gr, "_aylik_grid_cache_payload_from_db", return_value=old
    ), mock.patch.object(
        gr, "_build_aylik_grid_cache_payload", return_value=build
    ), mock.patch.object(
        gr, "execute", side_effect=lambda *a, **k: deleted.append("del")
    ), mock.patch.object(
        gr, "_invalidate_aylik_grid_payload_mem", return_value=None
    ):
        ok = gr._delete_aylik_grid_cache_guarded(10)
    check("delete_ok", ok is True)
    check("delete_sql", deleted == ["del"], deleted)


def test_akislarda_110_sinifi_korunur():
    """Tahsil/KYC/durum/rebuild-all → upsert skip (pasif KYC boş mock)."""
    from routes import giris_routes as gr

    old = _healthy_aylar(40, 1000)
    build = _ph_aylar(40, 0.01)
    for mid in (326, 444, 222):
        writes = []
        with mock.patch.object(
            gr, "_invalidate_musteri_kyc_grid_mem", return_value=None
        ), mock.patch.object(
            gr, "_build_aylik_grid_cache_payload", return_value=build
        ), mock.patch.object(
            gr, "_aylik_grid_cache_payload_from_db", return_value=old
        ), mock.patch.object(
            gr, "_aylik_grid_cache_skip_refresh_enabled", return_value=False
        ), mock.patch.object(
            gr, "_aylik_grid_freshness_shadow_log_enabled", return_value=False
        ), mock.patch.object(
            gr,
            "_persist_grid_cache_with_panel",
            side_effect=lambda *a, **k: writes.append(1) or a[1],
        ):
            out = gr._upsert_aylik_grid_cache(mid)
        check("akış_mid_%s_skip" % mid, writes == [] and out is old, writes)


def test_kaynak_delete_guarded_wired():
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("tahsil_et_guarded", "_delete_aylik_grid_cache_guarded" in src)
    i = src.find("def api_aylik_tutarlardan_tahsil_et")
    j = src.find("\ndef api_aylik_tutarlardan_tahsilden_cikar", i)
    body = src[i:j]
    check("tahsil_et_uses_guard", "_delete_aylik_grid_cache_guarded" in body)
    check("tahsil_et_no_raw_delete", "DELETE FROM musteri_aylik_grid_cache" not in body)
    b = src.find("def api_aylik_tutarlardan_borclandir")
    be = src.find("\ndef api_aylik_tutarlardan_tahsil_et", b)
    bbody = src[b:be]
    check("borclandir_guard", "_delete_aylik_grid_cache_guarded" in bbody)
    msrc = (ROOT / "routes" / "musteri_routes.py").read_text(encoding="utf-8")
    check("kyc_manuel_guard", "_delete_aylik_grid_cache_guarded" in msrc)


def main():
    test_stats_and_flags()
    test_326_collapse_upsert_korur()
    test_normal_guncelleme_yazar()
    test_yeni_musteri_yazar()
    test_force_placeholder_yazar()
    test_delete_guard_korur()
    test_delete_normal_siler()
    test_akislarda_110_sinifi_korunur()
    test_kaynak_delete_guarded_wired()
    if FAILS:
        print("FAILED", len(FAILS), FAILS)
        return 1
    print("ALL_OK", len(FAILS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
