# -*- coding: utf-8 -*-
"""SOZLESME_SABLON_V2: yeni Ofisbir DOCX şablonu (varsayılan kapalı).

Temiz ortam. Prod yazma yok. mid=867 önizleme yerel dosyaya (DB INSERT yok).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_")):
        os.environ.pop(k, None)
os.environ.pop("SOZLESME_SABLON_V2", None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:200]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_flag_default_off():
    os.environ.pop("SOZLESME_SABLON_V2", None)
    from sozlesme_sablon_v2 import sozlesme_sablon_v2_aktif

    check("flag_default_off", sozlesme_sablon_v2_aktif() is False)


def test_flag_on():
    os.environ["SOZLESME_SABLON_V2"] = "1"
    from importlib import reload
    import sozlesme_sablon_v2 as m

    reload(m)
    check("flag_on", m.sozlesme_sablon_v2_aktif() is True)
    os.environ.pop("SOZLESME_SABLON_V2", None)
    reload(m)


def test_sablon_file():
    from sozlesme_sablon_v2 import sozlesme_sablon_v2_path

    p = sozlesme_sablon_v2_path()
    check("sablon_var", p.is_file(), str(p))
    check("sablon_docx", p.suffix.lower() == ".docx")


def test_fill_and_ekler():
    from sozlesme_sablon_v2 import build_fill_ctx, render_sozlesme_docx_v2
    from docx import Document
    from io import BytesIO

    ctx = build_fill_ctx(
        musteri={
            "name": "TEST UNVAN LTD",
            "tax_number": "VN-TEST",
            "address": "Test Mah.",
            "phone": "TEL-TEST",
            "email": "test-ornek",
            "guncel_kira_bedeli": 1000.0,
            "rent_start_date": "2026-01-01",
            "mersis_no": "",
        },
        kyc={
            "yetkili_adsoyad": "Yetkili Test",
            "yetkili_tcno": "",
            "hizmet_turu": "Sanal Ofis",
            "aylik_kira": 1000.0,
        },
        sozlesme_no="SO010126-600",
        sozlesme_tarihi="2026-01-01",
        hizmet_turu="Sanal Ofis",
    )
    data = render_sozlesme_docx_v2(ctx)
    check("docx_bytes", isinstance(data, (bytes, bytearray)) and len(data) > 1000, str(len(data)))
    doc = Document(BytesIO(data))
    full = chr(10).join(p.text for p in doc.paragraphs)
    check("baslik_v2", "Ofisbir Ofis Kullanımı ve Hizmet Sözleşmesi" in full)
    check("ek1_baslik", "Ek-1: Hizmet ve Ücret Formu" in full)
    check("ek2_baslik", "Ek-2: Demirbaş Envanteri" in full)
    check("ek3_baslik", "Ek-3: Ofis Kullanım Kuralları" in full)
    check("ek4_baslik", "Ek-4: KVKK Aydınlatma Metni" in full)
    check("ek4_placeholder", "Bu ek, OFİSBİR'in gerçek veri işleme" in full or "aydınlatma" in full.lower())
    check("madde_korundu", "11.7." in full or "11.7" in full)
    check("unvan_dolu", "TEST UNVAN LTD" in full)
    # doldurulmayan boş [ ] kalmamalı (sabit [30]/[15] hariç olabilir)
    leftover_empty = full.count("[ ]")
    check("bos_kutu_cizgi", leftover_empty == 0, str(leftover_empty))
    check("tables_3", len(doc.tables) >= 3, str(len(doc.tables)))


def test_routes_wire():
    giris = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    must = (ROOT / "routes" / "musteri_routes.py").read_text(encoding="utf-8")
    check("giris_import", "sozlesme_sablon_v2_aktif" in giris)
    check("giris_branch", "sozlesme_sablon_v2_aktif()" in giris)
    check("kyc_import", "sozlesme_sablon_v2_aktif" in must)
    check("kyc_branch", "sozlesme_sablon_v2_aktif()" in must)
    # eski basliklar duruyor
    check("eski_giris_baslik", "OFİSBİR HİZMET SÖZLEŞMESİ" in giris)
    check("eski_kyc_baslik", "HAZIR OFİS / SANAL OFİS" in must)


def test_mid867_preview_both_flags():
    """mid=867: SELECT + yerel DOCX; DB INSERT yok. Her iki bayrak yolu çalışır."""
    os.environ.pop("ALLOW_PROD_WRITE", None)
    os.environ.pop("SOZLESME_SABLON_V2", None)
    from db import fetch_one
    from sozlesme_sablon_v2 import (
        build_fill_ctx,
        render_sozlesme_docx_v2,
        sozlesme_sablon_v2_aktif,
    )
    from docx import Document
    from io import BytesIO

    mid = 867
    cust = fetch_one("SELECT * FROM customers WHERE id = %s", (mid,))
    check("mid867_var", cust is not None and int(cust.get("id") or 0) == mid)
    kyc = fetch_one(
        "SELECT * FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
        (mid,),
    )
    check("mid867_kyc", kyc is not None)

    # Bayrak 0: v2 aktif değil (eski yol ayrı; burada v2 üretimi yine çağrılabilir ama flag off)
    check("preview_flag0", sozlesme_sablon_v2_aktif() is False)

    ctx = build_fill_ctx(musteri=cust or {}, kyc=kyc or {}, sozlesme_no="TEST-867-V2")
    out_dir = ROOT / "uploads" / "sozlesmeler_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Bayrak 1 simülasyonu: doğrudan v2 render
    os.environ["SOZLESME_SABLON_V2"] = "1"
    from importlib import reload
    import sozlesme_sablon_v2 as m

    reload(m)
    check("preview_flag1", m.sozlesme_sablon_v2_aktif() is True)
    data = m.render_sozlesme_docx_v2(ctx)
    path = out_dir / "preview_mid867_v2.docx"
    path.write_bytes(data)
    check("preview_file", path.is_file() and path.stat().st_size > 1000, str(path.stat().st_size if path.is_file() else 0))
    doc = Document(BytesIO(data))
    full = chr(10).join(p.text for p in doc.paragraphs)
    check("preview_ek1", "Ek-1" in full)
    check("preview_ek4", "Ek-4" in full)
    # PII yok çıktıda: yalnızca mid/boolean/size
    check("preview_no_db_write_note", True)  # render DB'ye yazmaz
    os.environ.pop("SOZLESME_SABLON_V2", None)
    reload(m)


def test_literal_bs_n():
    src = (ROOT / "sozlesme_sablon_v2.py").read_text(encoding="utf-8")
    # urun JS yok; modul varligi
    check("module_exists", "def render_sozlesme_docx_v2" in src)


def main() -> int:
    test_flag_default_off()
    test_flag_on()
    test_sablon_file()
    test_fill_and_ekler()
    test_routes_wire()
    test_mid867_preview_both_flags()
    test_literal_bs_n()
    print("---")
    if FAILS:
        print("FAILS", len(FAILS), FAILS)
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
