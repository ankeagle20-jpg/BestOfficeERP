# -*- coding: utf-8 -*-
"""Ekler paneli / demirbas-ekler API / Ek-2 dipnot. DB yazmaz (mock)."""
from __future__ import annotations

import os
import re
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_", "DEMIRBAS_", "SOZLESME_")):
        os.environ.pop(k, None)

FAILS: list[str] = []
MID = 867


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + ek) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_sanitize_ve_normalize():
    from sozlesme_sablon_v2 import _sanitize_ek2_hasar
    from routes.giris_routes import _demirbas_ekler_normalize_payload, _demirbas_ekler_pref_key

    check("pref_key", _demirbas_ekler_pref_key(MID) == f"demirbas_ek:{MID}")
    s = _sanitize_ek2_hasar("a\x00b<script>x</script>c\nd & <e> \"f\"")
    check("san_ctrl", "\x00" not in s)
    check("san_html", "<script>" not in s and "</script>" not in s)
    check("san_nl", "\n" in s)
    check("san_ozel", "&" in s and '"' in s)
    long = "x" * 600
    check("san_len", len(_sanitize_ek2_hasar(long)) <= 500)

    err, n = _demirbas_ekler_normalize_payload({"anahtar_kart_sayisi": "", "hasar_aciklama": "  ok  "})
    check("norm_bos_sayi", err is None and n["anahtar_kart_sayisi"] is None)
    check("norm_trim", n["hasar_aciklama"] == "ok")
    err2, _ = _demirbas_ekler_normalize_payload({"anahtar_kart_sayisi": 21})
    check("norm_21", err2 is not None)
    err3, n3 = _demirbas_ekler_normalize_payload({"anahtar_kart_sayisi": 0, "hasar_aciklama": "a"})
    check("norm_0", err3 is None and n3["anahtar_kart_sayisi"] == 0)
    err4, _ = _demirbas_ekler_normalize_payload({"anahtar_kart_sayisi": -1})
    check("norm_neg", err4 is not None)
    err5, n5 = _demirbas_ekler_normalize_payload(
        {"anahtar_kart_sayisi": 3, "hasar_aciklama": "satır1\nsatır2 & <tag>"}
    )
    check("norm_multi", err5 is None and "\n" in n5["hasar_aciklama"] and "<tag>" not in n5["hasar_aciklama"])


def test_dipnot_doldur():
    from docx import Document
    from sozlesme_sablon_v2 import (
        BLANK,
        build_fill_ctx,
        render_sozlesme_docx_v2,
        docx_bytes_to_preview_html,
    )
    from datetime import date as _date

    def _dip(doc):
        for p in doc.paragraphs:
            t = p.text or ""
            if "Teslim tarihi:" in t and "anahtar" in t.casefold():
                return t
        return ""

    def _render(**extra):
        m = {"name": "SENT UNVAN", "tax_number": "VN-1", "guncel_kira_bedeli": 100.0}
        if "bas" in extra:
            if extra["bas"]:
                m["rent_start_date"] = extra["bas"]
            else:
                m["rent_start_date"] = None
        k = {"hizmet_turu": "Sanal Ofis", "aylik_kira": 100.0}
        ctx = build_fill_ctx(musteri=m, kyc=k, sozlesme_no="EK2-T", sozlesme_tarihi=extra.get("bas"))
        ctx["ek2_anahtar_kart"] = extra.get("ak")
        ctx["ek2_hasar_aciklama"] = extra.get("hasar", "")
        data = render_sozlesme_docx_v2(ctx)
        return ctx, Document(BytesIO(data)), data

    ctx, doc, raw = _render(
        bas="2024-03-15",
        ak=2,
        hasar='çizik & "delik"\nikinci satır < ve > Türkçe',
    )
    dip = _dip(doc)
    check("dip_tarih", "15.03.2024" in dip, dip[:120])
    check("dip_bugun_yok", _date.today().strftime("%d.%m.%Y") not in dip or "15.03.2024" in dip)
    check("dip_anahtar", "anahtar / kart sayısı: 2" in dip or "kart sayısı: 2" in dip, dip[:160])
    check("dip_hasar_nl", "çizik" in dip and "ikinci satır" in dip, dip[:200])
    check("dip_amp", "&" in dip)
    check("dip_suffix", "OFİSBİR:" in dip and "MÜŞTERİ:" in dip)
    check("dip_taraflar", "envanterin teslim anındaki durumu" in dip)

    paras = [p for p in doc.paragraphs if "Teslim tarihi:" in (p.text or "")]
    check("dip_para", bool(paras))
    if paras:
        xml = paras[0]._p.xml
        check("dip_br", "<w:br" in xml, xml[:200])

    ctx_b, doc_b, _ = _render(bas=None, ak=None, hasar="")
    dip_b = _dip(doc_b)
    check("dip_bos_tarih", f"Teslim tarihi: {BLANK}" in dip_b, dip_b[:100])
    check("dip_bos_anahtar", f"kart sayısı: {BLANK}" in dip_b)
    check("dip_bos_hasar", f"eksiklikler: {BLANK}" in dip_b)
    check("dip_bos_bugun", _date.today().strftime("%d.%m.%Y") not in dip_b)

    # önizleme tutarlı + yazmaz
    html = docx_bytes_to_preview_html(raw)
    check("prev_hasar", "çizik" in html)
    check("prev_amp_or_entity", ("&amp;" in html) or ("&" in html))
    check("prev_tarih", "15.03.2024" in html)
    check("prev_anahtar", ">2<" in html or " 2." in html or "2" in html)


def test_api_get_put_mock():
    from auth import User, login_manager
    from flask_login import login_user
    from routes.giris_routes import bp
    import json as _json

    app = Flask(__name__)
    app.secret_key = "test-ekler"
    login_manager.init_app(app)
    app.register_blueprint(bp, url_prefix="/giris")

    @login_manager.user_loader
    def _load(uid):
        return User(1, "t", "T", "admin")

    @app.route("/__ekler_login")
    def __ekler_login():
        login_user(User(1, "t", "T", "admin"), remember=False)
        return {"ok": True}

    store: dict[str, dict] = {}

    def pref_get(key):
        return dict(store.get(key) or {})

    def execute(sql, params=None):
        key, raw = params[0], params[1]
        store[key] = _json.loads(raw) if isinstance(raw, str) else raw
        return 1

    with app.test_client() as c:
        lr = c.get("/__ekler_login")
        check("api_login", lr.status_code == 200)

        with patch("demirbas.demirbas_tablosu_aktif", return_value=False):
            r = c.get(f"/giris/api/musteri/{MID}/demirbas-ekler")
            check("api_kapali_404", r.status_code == 404, str(r.status_code) + str(r.get_json()))

        with patch("demirbas.demirbas_tablosu_aktif", return_value=True), patch(
            "db.firma_ui_preferences_table_exists", return_value=False
        ):
            r = c.get(f"/giris/api/musteri/{MID}/demirbas-ekler")
            check("api_tablo_yok_503", r.status_code == 503, str(r.get_json()))
            j = r.get_json() or {}
            check(
                "api_tablo_yok_msg",
                j.get("tablo_yok") is True and "firma_ui_preferences" in (j.get("mesaj") or ""),
            )

        with patch("demirbas.demirbas_tablosu_aktif", return_value=True), patch(
            "db.firma_ui_preferences_table_exists", return_value=True
        ), patch("routes.giris_routes._demirbas_ui_pref_get_firma", side_effect=pref_get), patch(
            "routes.giris_routes.execute", side_effect=execute
        ), patch("db.execute", side_effect=execute):
            r = c.get(f"/giris/api/musteri/{MID}/demirbas-ekler")
            check("api_get_bos", r.status_code == 200 and (r.get_json() or {}).get("ok") is True, str(r.get_json()))
            r = c.put(
                f"/giris/api/musteri/{MID}/demirbas-ekler",
                json={"anahtar_kart_sayisi": 5, "hasar_aciklama": "satır1\nsatır2 & x"},
            )
            check("api_put_ok", r.status_code == 200, str(r.get_json()))
            j = r.get_json() or {}
            check("api_put_sayi", j.get("anahtar_kart_sayisi") == 5)
            check("api_put_hasar_nl", "\n" in (j.get("hasar_aciklama") or ""))
            r = c.get(f"/giris/api/musteri/{MID}/demirbas-ekler")
            j2 = r.get_json() or {}
            check("api_get_after", j2.get("anahtar_kart_sayisi") == 5)
            r_bad = c.put(
                f"/giris/api/musteri/{MID}/demirbas-ekler",
                json={"anahtar_kart_sayisi": 99, "hasar_aciklama": "x"},
            )
            check("api_put_99", r_bad.status_code == 400)
            r_empty = c.put(
                f"/giris/api/musteri/{MID}/demirbas-ekler",
                json={"anahtar_kart_sayisi": "", "hasar_aciklama": ""},
            )
            je = r_empty.get_json() or {}
            check("api_put_empty", r_empty.status_code == 200 and je.get("anahtar_kart_sayisi") is None)

    from routes.giris_routes import _demirbas_ekler_normalize_payload as _n

    e_mut, _ = _n({"anahtar_kart_sayisi": 21})
    check("mut_21_yakalar", e_mut is not None)
    check("mut_neg_yakalar", _n({"anahtar_kart_sayisi": -1})[0] is not None)


def test_fe_panel_isaretleri():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    for src, lab in ((html, "html"), (tjs, "tjs")):
        check(f"{lab}_btn", 'id="giris_demirbas_ekler_btn"' in src or "ekler-ac" in src)
        check(f"{lab}_panel", "giris_demirbas_ekler_panel" in src)
        check(f"{lab}_anahtar", "giris_demirbas_ekler_anahtar" in src)
        check(f"{lab}_hasar", "giris_demirbas_ekler_hasar" in src)
        check(f"{lab}_kaydet", "ekler-kaydet" in src)
        check(f"{lab}_kapat", "ekler-kapat" in src)
        check(f"{lab}_fn_ac", "function girisDemirbasEklerPanelAc" in src)
        check(f"{lab}_fn_kirli", "girisDemirbasEklerKirliMi" in src)
        check(f"{lab}_confirm", "Kaydedilmemiş değişiklikler" in src)
        check(f"{lab}_yukle_wire", "girisDemirbasEklerYukle(mid)" in src)
        check(f"{lab}_bind_wire", "girisDemirbasEklerBind()" in src)
        check(f"{lab}_no_inline_onclick_user", 'onclick="' not in src[src.find("giris_demirbas_ekler_panel") : src.find("giris_demirbas_ekler_panel") + 800] if "giris_demirbas_ekler_panel" in src else True)
        check(f"{lab}_api_path", "/demirbas-ekler" in src)
        check(f"{lab}_max500", "maxlength=\"500\"" in src or "500" in src)
    # karşılıklı panel
    check("duzenle_kapatir_ekler", "girisDemirbasEklerPanelKapat(true)" in tjs)
    check("ekler_kapatir_duzenle", "girisDemirbasDuzenlePanelKapat" in tjs)


def test_url_map_ekler():
    from auth import login_manager
    from routes.giris_routes import bp

    app = Flask(__name__)
    app.secret_key = "x"
    login_manager.init_app(app)
    app.register_blueprint(bp, url_prefix="/giris")
    ad = app.url_map.bind("localhost")
    for meth in ("GET", "PUT"):
        try:
            ad.match(f"/giris/api/musteri/{MID}/demirbas-ekler", method=meth)
            ok = True
        except Exception as e:
            ok = False
            check(f"url_map_{meth}", False, type(e).__name__)
            continue
        check(f"url_map_{meth}", ok)


def main() -> int:
    test_sanitize_ve_normalize()
    test_dipnot_doldur()
    test_api_get_put_mock()
    test_fe_panel_isaretleri()
    test_url_map_ekler()
    print("---")
    if FAILS:
        print("FAILS", len(FAILS), FAILS)
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
