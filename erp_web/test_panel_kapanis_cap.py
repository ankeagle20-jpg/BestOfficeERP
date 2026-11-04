# -*- coding: utf-8 -*-
"""Panel kapanış cap: tahsilat + borçlandırma ay listesi / GET kırpma / 400 guard / iptal."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)
for _k in (
    "KAPANIS_HEPSI_SOZLESME_YILI",
    "KAPANIS_N0_KAPANIS_AYI_HARIC",
    "KAPANIS_N_KAPANIS_AYI_DAHIL",
):
    os.environ.pop(_k, None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek="") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _reload_kc():
    import importlib
    import kira_kapanis_cap as kc

    importlib.reload(kc)
    return kc


def test_a_panel_ay_listesi_capte_biter():
    """(a) FE: yıl listesi + ay keys cap'te biter (kaynak metin + cap filtre yardımcıları)."""
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    for label, src in (("tjs", tjs), ("html", html)):
        check(f"a_{label}_cap_fn", "function girisPanelAyKeysCapFiltre" in src)
        check(f"a_{label}_yil_cap", "girisPanelCapExclusiveDate" in src and "yCap" in src)
        check(f"a_{label}_keys_filtre", "girisPanelAyKeysCapFiltre(keys)" in src)
        check(f"a_{label}_borc_panel", "girisAylikSatirYilAyPanelGuncelle" in src and "girisPanelAyKeysCapFiltre" in src)
    check("a_tjs_panel_hafif", "girisTahsilatYilAyKeysFiltered(yilNum)" in tjs)


def test_b_kapanis_oncesi_aylar_ayni():
    """(b) Cap kırpma: son dahil kalır, sonrası düşer; aktifte kırpma yok."""
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    os.environ["KAPANIS_N_KAPANIS_AYI_DAHIL"] = "1"
    _reload_kc()
    from routes import giris_routes as gr

    by = {
        "2024-11-01": {"aylik": 100, "tahsil": 0, "kalan": 100},
        "2024-12-01": {"aylik": 100, "tahsil": 50, "kalan": 50},
        "2025-01-01": {"aylik": 100, "tahsil": 0, "kalan": 100},
        "2025-02-01": {"aylik": 100, "tahsil": 0, "kalan": 100},
    }
    with mock.patch.object(gr, "_musteri_sozlesme_iptal_mi", return_value=False), mock.patch.object(
        gr, "_aylik_grid_son_borc_for_mid", return_value=(date(2024, 12, 1), {})
    ):
        out = gr._panel_by_iso_cap_kirp_okuma(174, by)
    check("b_onceki_kalir", "2024-11-01" in out and "2024-12-01" in out)
    check("b_sonrasi_yok", "2025-01-01" not in out and "2025-02-01" not in out)
    check("b_tahsil_korunur", out["2024-12-01"].get("tahsil") == 50)
    with mock.patch.object(gr, "_musteri_sozlesme_iptal_mi", return_value=False), mock.patch.object(
        gr, "_aylik_grid_son_borc_for_mid", return_value=(None, {})
    ):
        out2 = gr._panel_by_iso_cap_kirp_okuma(1, by)
    check("b_aktif_ayni", out2 == by)
    os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
    os.environ.pop("KAPANIS_N_KAPANIS_AYI_DAHIL", None)
    _reload_kc()


def test_c_aktif_ve_kapansiz_pasif():
    """(c) Cap yoksa guard geç; yıl/cap filtre aktifte no-op metinleri."""
    from routes import giris_routes as gr

    with mock.patch.object(gr, "_aylik_grid_son_borc_for_mid", return_value=(None, {})):
        ok, red = gr._kapanis_cap_sonrasi_satir_red(
            99, [{"yil": 2030, "ay": 1}]
        )
    check("c_guard_yok_cap", ok is True and red is None)
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    check("c_fe_aktif_pasif_ayni_yol", "girisPanelCapExclusiveDate" in tjs)


def test_d_cap_sonrasi_400():
    """(d) Cap sonrası borçlandır/tahsil satırı → 400 Türkçe mesaj."""
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    _reload_kc()
    from flask import Flask
    from flask_login import LoginManager
    from routes import giris_routes as gr

    app = Flask("panel_cap_d")
    app.secret_key = "t"
    LoginManager(app)
    app.register_blueprint(gr.bp, url_prefix="/giris")

    with app.app_context():
        with mock.patch.object(
            gr, "_aylik_grid_son_borc_for_mid", return_value=(date(2024, 12, 1), {})
        ):
            ok, red = gr._kapanis_cap_sonrasi_satir_red(
                174, [{"yil": 2025, "ay": 1, "tutar_kdv_dahil": 100}]
            )
        check("d_red", ok is False and red is not None)
        if red:
            resp, code = red
            data = resp.get_json()
            check("d_400", code == 400)
            check(
                "d_mesaj",
                (data or {}).get("mesaj") == "Kapanış sonrası aylar için işlem yapılamaz.",
            )
        with mock.patch.object(
            gr, "_aylik_grid_son_borc_for_mid", return_value=(date(2024, 12, 1), {})
        ):
            ok2, _ = gr._kapanis_cap_sonrasi_satir_red(
                174, [{"yil": 2024, "ay": 12, "tutar_kdv_dahil": 100}]
            )
        check("d_cap_dahil_ok", ok2 is True)

    # Uçlarda guard çağrısı
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("d_borclandir_guard", "_kapanis_cap_sonrasi_satir_red" in src.split("def api_aylik_tutarlardan_borclandir")[1][:2800])
    check("d_tahsil_guard", "_kapanis_cap_sonrasi_satir_red" in src.split("def api_aylik_tutarlardan_tahsil_et")[1][:2800])
    os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
    _reload_kc()


def test_e_iptal_panel_bos():
    """(e) İptal: GET boş + mesaj; FE paneller boş + mesaj."""
    from routes import giris_routes as gr

    by = {"2024-12-01": {"aylik": 1, "tahsil": 0, "kalan": 1}}
    with mock.patch.object(gr, "_musteri_sozlesme_iptal_mi", return_value=True):
        out = gr._panel_by_iso_cap_kirp_okuma(101, by)
    check("e_kirp_bos", out == {})
    with mock.patch.object(gr, "_musteri_sozlesme_iptal_mi", return_value=True), mock.patch.object(
        gr, "_json_no_cache", side_effect=lambda d, *a, **k: d
    ):
        yanit = gr._tahsilat_panel_detay_okuma_yaniti(101, by)
    check("e_yanit_bos", yanit.get("by_iso") == {})
    check("e_yanit_iptal", yanit.get("iptal") is True)
    check("e_yanit_mesaj", "iptal" in (yanit.get("iptal_mesaj") or "").lower() or "oluşmaz" in (yanit.get("iptal_mesaj") or ""))
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    for label, src in (("tjs", tjs), ("html", html)):
        check(f"e_{label}_paneller", "function girisPanellerIptalUygula" in src)
        check(f"e_{label}_mesaj", "Sözleşme iptal edildi: aylık borç oluşmaz." in src)


def test_f_n_varyasyonlari():
    """(f) N=0,1,2 ve Hepsi: son borç ayı → panel kırpma uyumu."""
    os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    os.environ["KAPANIS_N_KAPANIS_AYI_DAHIL"] = "1"
    kc = _reload_kc()
    from routes import giris_routes as gr

    giris = date(2024, 1, 15)
    kap = date(2024, 9, 10)
    cases = [
        ("n0", 0, date(2024, 8, 1)),
        ("n1", 1, date(2024, 9, 1)),
        ("n2", 2, date(2024, 10, 1)),
        ("hepsi", None, kc.hepsi_sozlesme_yili_son_borc_ayi(giris, kap)),
    ]
    by = {
        f"{y}-{m:02d}-01": {"aylik": 1, "tahsil": 0, "kalan": 1}
        for y, m in [(2024, 7), (2024, 8), (2024, 9), (2024, 10), (2024, 11), (2024, 12)]
    }
    for ad, n, son in cases:
        check(f"f_{ad}_son", son is not None, son)
        with mock.patch.object(gr, "_musteri_sozlesme_iptal_mi", return_value=False), mock.patch.object(
            gr, "_aylik_grid_son_borc_for_mid", return_value=(son, {})
        ):
            out = gr._panel_by_iso_cap_kirp_okuma(1, by)
        last = max(date.fromisoformat(k[:10]) for k in out if not k.startswith("__"))
        check(f"f_{ad}_last", last == son, (last, son))
        check(f"f_{ad}_beyond", all(
            date.fromisoformat(k[:10]) <= son for k in out if not k.startswith("__")
        ))
    for _k in (
        "KAPANIS_HEPSI_SOZLESME_YILI",
        "KAPANIS_N0_KAPANIS_AYI_HARIC",
        "KAPANIS_N_KAPANIS_AYI_DAHIL",
    ):
        os.environ.pop(_k, None)
    _reload_kc()


def test_g_disk_cap_sonrasi_kirpilir():
    """(g) Disk panel cap sonrası ay içerse bile okuma kırpılır (yazma yok)."""
    from routes import giris_routes as gr

    disk = {
        "2024-12-01": {"aylik": 2500, "tahsil": 0, "kalan": 2500},
        "2025-01-01": {"aylik": 2500, "tahsil": 0, "kalan": 2500},
        "2025-11-01": {"aylik": 2500, "tahsil": 0, "kalan": 2500},
    }
    with mock.patch.object(gr, "_musteri_sozlesme_iptal_mi", return_value=False), mock.patch.object(
        gr, "_aylik_grid_son_borc_for_mid", return_value=(date(2024, 12, 1), {})
    ), mock.patch.object(gr, "_json_no_cache", side_effect=lambda d, *a, **k: d):
        yanit = gr._tahsilat_panel_detay_okuma_yaniti(174, disk)
    check("g_tek_ay", list(yanit.get("by_iso") or {}) == ["2024-12-01"])
    check("g_disk_degismedi", "2025-11-01" in disk)


def main():
    test_a_panel_ay_listesi_capte_biter()
    test_b_kapanis_oncesi_aylar_ayni()
    test_c_aktif_ve_kapansiz_pasif()
    test_d_cap_sonrasi_400()
    test_e_iptal_panel_bos()
    test_f_n_varyasyonlari()
    test_g_disk_cap_sonrasi_kirpilir()
    print("---")
    if FAILS:
        print("FAILS", FAILS)
        raise SystemExit(1)
    print("ALL_OK test_panel_kapanis_cap")


if __name__ == "__main__":
    main()
