# -*- coding: utf-8 -*-
"""Cap kırpma (b′): placeholder yeni + healthy eski + son_borc → eski kırpılıp yazılır."""
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


def _healthy_long():
    # 2021-10..2022-09 (12) + 2022-10..2027-08 (59) ≈ 71; tutarlar: pre 3*2400, b2 9*2400, beyond rest
    aylar = []
    for i in range(71):
        y = 2021 + (9 + i) // 12
        m = (9 + i) % 12 + 1
        aylar.append(_ay(y, m, 2400.0))
    return _payload(aylar, kapanis_tarihi="2021-12-31", kapanis_sonrasi_borc_ay=None)


def _ph_short(n=12, tutar=0.01):
    aylar = []
    for i in range(n):
        y = 2021 + (9 + i) // 12
        m = (9 + i) % 12 + 1
        aylar.append(_ay(y, m, tutar))
    return _payload(aylar)


def test_a_kirp_yazilir():
    """(a) placeholder + healthy + cap → kırpılıp yazılır, tutarlar korunur."""
    os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
    from routes import giris_routes as gr
    import importlib
    import kira_kapanis_cap as kc

    importlib.reload(kc)

    old = _healthy_long()
    build = _ph_short(12)
    son = date(2022, 9, 1)
    kyc = {
        "durum": "pasif",
        "kapanis_tarihi": date(2021, 12, 31),
        "kapanis_sonrasi_borc_ay": None,
        "rent_start_date": date(2021, 10, 20),
    }
    writes = []

    with mock.patch.object(gr, "_invalidate_musteri_kyc_grid_mem"), mock.patch.object(
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
        gr, "_aylik_grid_cache_payload_tahsil_guncelle", side_effect=lambda mid, p: p
    ), mock.patch.object(
        gr, "_ensure_aylik_grid_cache_table"
    ), mock.patch.object(
        gr, "execute", side_effect=lambda *a, **k: writes.append(a)
    ), mock.patch.object(
        gr, "_aylik_grid_mem_set"
    ), mock.patch.object(
        gr, "_invalidate_aylik_grid_payload_mem"
    ):
        out = gr._upsert_aylik_grid_cache(149)

    check("a_yazildi", len(writes) >= 1, len(writes))
    check("a_out_dict", isinstance(out, dict))
    aylar = (out or {}).get("aylar") or []
    check("a_n12", len(aylar) == 12, len(aylar))
    s = round(sum(float(a["tutar_kdv_dahil"]) for a in aylar), 2)
    check("a_sum_28800", abs(s - 28800.0) < 0.01, s)
    check("a_bitis", str((out or {}).get("bitis") or "")[:7] == "2022-09")
    check("a_fp", (out or {}).get("kapanis_ek_fingerprint") == "H12")
    check("a_not_ph", not gr._aylik_grid_payload_is_placeholder(out))
    os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
    importlib.reload(kc)


def test_b_cap_yok_skip():
    """(b) placeholder + healthy + cap yok → skip, yazma yok."""
    os.environ.pop("KAPANIS_HEPSI_SOZLESME_YILI", None)
    from routes import giris_routes as gr

    old = _healthy_long()
    build = _ph_short(12)
    writes = []
    with mock.patch.object(gr, "_invalidate_musteri_kyc_grid_mem"), mock.patch.object(
        gr, "_build_aylik_grid_cache_payload", return_value=build
    ), mock.patch.object(
        gr, "_aylik_grid_cache_payload_from_db", return_value=old
    ), mock.patch.object(
        gr, "_musteri_kyc_grup_for_aylik_grid", return_value={"durum": "pasif", "kapanis_tarihi": date(2021, 12, 31), "kapanis_sonrasi_borc_ay": None, "rent_start_date": date(2021, 10, 20)}
    ), mock.patch.object(
        gr, "kira_son_borc_ayi", return_value=None
    ), mock.patch.object(
        gr, "_aylik_grid_cache_skip_refresh_enabled", return_value=False
    ), mock.patch.object(
        gr, "_aylik_grid_freshness_shadow_log_enabled", return_value=False
    ), mock.patch.object(
        gr,
        "_persist_grid_cache_with_panel",
        side_effect=lambda *a, **k: writes.append(1) or a[1],
    ):
        out = gr._upsert_aylik_grid_cache(149)
    check("b_skip_write", writes == [])
    check("b_out_old", out is old)


def test_c_eski_unhealthy_placeholder_yazar():
    """(c) placeholder yeni + eski healthy değil → eski davranış (yazım)."""
    from routes import giris_routes as gr

    old = _ph_short(50, 0.01)  # unhealthy-ish / placeholder old
    build = _ph_short(12, 0.01)
    writes = []
    with mock.patch.object(gr, "_invalidate_musteri_kyc_grid_mem"), mock.patch.object(
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
        side_effect=lambda mid, payload, **k: writes.append(payload) or payload,
    ):
        out = gr._upsert_aylik_grid_cache(10)
    check("c_write", len(writes) == 1 and writes[0] is build)
    check("c_out", out is build)


def test_d_yeni_healthy_normal_yazim():
    """(d) yeni placeholder değil → normal yazım."""
    from routes import giris_routes as gr

    old = _healthy_long()
    build = _payload([_ay(2022, 1, 1000), _ay(2022, 2, 1000)])
    writes = []
    with mock.patch.object(gr, "_invalidate_musteri_kyc_grid_mem"), mock.patch.object(
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
        side_effect=lambda mid, payload, **k: writes.append(payload) or payload,
    ):
        out = gr._upsert_aylik_grid_cache(11)
    check("d_write", len(writes) == 1 and writes[0] is build)
    check("d_out", out is build)


def test_e_placeholder_asla_kirpma_sonucu_degil():
    """(e) kırpma çıktısı placeholder olamaz; trim helper ph yazmaz."""
    from routes import giris_routes as gr

    old = _healthy_long()
    son = date(2022, 9, 1)
    trimmed = gr._aylik_grid_trim_payload_to_son_borc(old, son, {"kapanis_sonrasi_borc_ay": None})
    check("e_trim_ok", trimmed is not None)
    check("e_not_ph", not gr._aylik_grid_payload_is_placeholder(trimmed))
    # cap_trim_if: new ph + old healthy + son → trimmed, never new
    with mock.patch.object(gr, "_aylik_grid_cache_payload_from_db", return_value=old), mock.patch.object(
        gr, "_musteri_kyc_grup_for_aylik_grid", return_value={"durum": "pasif"}
    ), mock.patch.object(gr, "kira_son_borc_ayi", return_value=son):
        got = gr._aylik_grid_cap_trim_if_placeholder_collapse(149, _ph_short(12))
    check("e_got_trim", got is not None and not gr._aylik_grid_payload_is_placeholder(got))
    check("e_n", len((got or {}).get("aylar") or []) == 12)


def test_f_idempotent():
    """(f) kırpılmış üzerine tekrar kırpma aynı sonucu verir."""
    from routes import giris_routes as gr

    old = _healthy_long()
    son = date(2022, 9, 1)
    t1 = gr._aylik_grid_trim_payload_to_son_borc(old, son, {})
    t2 = gr._aylik_grid_trim_payload_to_son_borc(t1, son, {})
    check("f_n", len(t1["aylar"]) == len(t2["aylar"]) == 12)
    s1 = sum(float(a["tutar_kdv_dahil"]) for a in t1["aylar"])
    s2 = sum(float(a["tutar_kdv_dahil"]) for a in t2["aylar"])
    check("f_sum", abs(s1 - s2) < 0.01 and abs(s1 - 28800) < 0.01, (s1, s2))


def test_g_kenar_n0_ve_yil_sonu():
    """(g) N=0: son=kap ayı; kırpma kap ayına kadar (dahil)."""
    from routes import giris_routes as gr

    aylar = [_ay(2025, 8, 100), _ay(2025, 9, 100), _ay(2025, 10, 100), _ay(2025, 11, 100)]
    old = _payload(aylar, kapanis_tarihi="2025-09-30", kapanis_sonrasi_borc_ay=0)
    son = date(2025, 9, 1)  # N=0
    t = gr._aylik_grid_trim_payload_to_son_borc(old, son, {"kapanis_sonrasi_borc_ay": 0})
    check("g_n2", len(t["aylar"]) == 2, len(t["aylar"]))
    check("g_fp0", t.get("kapanis_ek_fingerprint") == "0")
    # B2 son = kap ayı (çıkış yıl sonu)
    son2 = date(2025, 9, 1)
    t2 = gr._aylik_grid_trim_payload_to_son_borc(old, son2, {"kapanis_sonrasi_borc_ay": None})
    check("g_ys_n2", len(t2["aylar"]) == 2)


def test_tahsilat_degisti_upsert_yolu():
    """tahsilat_degisti → resync → upsert; kırpma aynı yardımcıyı kullanır (kaynak)."""
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("wired_trim_helper", "def _aylik_grid_cap_trim_if_placeholder_collapse" in src)
    check("wired_upsert", "_aylik_grid_cap_trim_if_placeholder_collapse" in src[src.find("def _upsert_aylik_grid_cache"):src.find("def _tenant_cache_part")])
    check("wired_persist", "_aylik_grid_cap_trim_if_placeholder_collapse" in src[src.find("def _persist_grid_cache_with_panel"):src.find("def _kyc_date_iso")])
    check("tahsilat_degisti_resync", "resync_panel_and_grid_after_tahsil_change" in src[src.find("def tahsilat_degisti"):src.find("def tahsilat_degisti") + 2500])


if __name__ == "__main__":
    test_a_kirp_yazilir()
    test_b_cap_yok_skip()
    test_c_eski_unhealthy_placeholder_yazar()
    test_d_yeni_healthy_normal_yazim()
    test_e_placeholder_asla_kirpma_sonucu_degil()
    test_f_idempotent()
    test_g_kenar_n0_ve_yil_sonu()
    test_tahsilat_degisti_upsert_yolu()
    if FAILS:
        print("FAIL", FAILS)
        raise SystemExit(1)
    print("ALL_OK")
