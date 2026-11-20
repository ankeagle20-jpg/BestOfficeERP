# -*- coding: utf-8 -*-
"""SOZLESME_SABLON_V2 + SOZLESME_SABLON_V2_MIDLER.

Temiz ortam. Prod yazma yok. mid=867 onizleme yerel (DB INSERT yok).
"""
from __future__ import annotations

import os
import sys
from importlib import reload
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_")):
        os.environ.pop(k, None)
os.environ.pop("SOZLESME_SABLON_V2", None)
os.environ.pop("SOZLESME_SABLON_V2_MIDLER", None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:200]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _clean_flags() -> None:
    os.environ.pop("SOZLESME_SABLON_V2", None)
    os.environ.pop("SOZLESME_SABLON_V2_MIDLER", None)


def test_karar_matris():
    """bayrak0+liste bos=eski; liste 867+mid867=yeni; baska mid=eski; bayrak1=herkes."""
    import sozlesme_sablon_v2 as m

    _clean_flags()
    reload(m)
    check("flag0_liste_bos_mid_yok", m.sozlesme_sablon_v2_aktif() is False)
    check("flag0_liste_bos_mid867", m.sozlesme_sablon_v2_aktif(867) is False)

    os.environ["SOZLESME_SABLON_V2"] = "0"
    os.environ["SOZLESME_SABLON_V2_MIDLER"] = "867"
    reload(m)
    check("flag0_liste867_mid867", m.sozlesme_sablon_v2_aktif(867) is True)
    check("flag0_liste867_mid_baska", m.sozlesme_sablon_v2_aktif(1) is False)
    check("flag0_liste867_mid_yok", m.sozlesme_sablon_v2_aktif() is False)

    os.environ["SOZLESME_SABLON_V2"] = "1"
    os.environ.pop("SOZLESME_SABLON_V2_MIDLER", None)
    reload(m)
    check("flag1_herkes_mid_yok", m.sozlesme_sablon_v2_aktif() is True)
    check("flag1_herkes_mid867", m.sozlesme_sablon_v2_aktif(867) is True)
    check("flag1_herkes_mid1", m.sozlesme_sablon_v2_aktif(1) is True)

    # bosluk / gecersiz
    os.environ["SOZLESME_SABLON_V2"] = "0"
    os.environ["SOZLESME_SABLON_V2_MIDLER"] = " 867 , abc , , 0 , -3 , 42 "
    reload(m)
    mids = m.sozlesme_sablon_v2_midler()
    check("midler_set", mids == {867, 42}, str(sorted(mids)))
    check("midler_dayanik_867", m.sozlesme_sablon_v2_aktif(867) is True)
    check("midler_dayanik_42", m.sozlesme_sablon_v2_aktif(42) is True)
    check("midler_dayanik_abc_yok", m.sozlesme_sablon_v2_aktif(99) is False)

    _clean_flags()
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
    leftover_empty = full.count("[ ]")
    check("bos_kutu_cizgi", leftover_empty == 0, str(leftover_empty))
    check("tables_3", len(doc.tables) >= 3, str(len(doc.tables)))


def test_routes_wire():
    giris = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    must = (ROOT / "routes" / "musteri_routes.py").read_text(encoding="utf-8")
    check("giris_import", "sozlesme_sablon_v2_aktif" in giris)
    check("giris_branch_mid", "sozlesme_sablon_v2_aktif(mid)" in giris)
    check("kyc_import", "sozlesme_sablon_v2_aktif" in must)
    check("kyc_branch_mid", "sozlesme_sablon_v2_aktif(musteri_id)" in must)
    check("eski_giris_baslik", "OFİSBİR HİZMET SÖZLEŞMESİ" in giris)
    check("eski_kyc_baslik", "HAZIR OFİS / SANAL OFİS" in must)


def test_mid867_preview_liste():
    """mid=867: bayrak0+liste ile yeni; DB INSERT yok."""
    _clean_flags()
    os.environ.pop("ALLOW_PROD_WRITE", None)
    from db import fetch_one
    import sozlesme_sablon_v2 as m

    reload(m)
    mid = 867
    cust = fetch_one("SELECT * FROM customers WHERE id = %s", (mid,))
    check("mid867_var", cust is not None and int(cust.get("id") or 0) == mid)
    kyc = fetch_one(
        "SELECT * FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
        (mid,),
    )
    check("mid867_kyc", kyc is not None)
    check("preview_flag0_liste_bos", m.sozlesme_sablon_v2_aktif(mid) is False)

    os.environ["SOZLESME_SABLON_V2_MIDLER"] = "867"
    reload(m)
    check("preview_liste867", m.sozlesme_sablon_v2_aktif(mid) is True)

    ctx = m.build_fill_ctx(musteri=cust or {}, kyc=kyc or {}, sozlesme_no="TEST-867-V2")
    out_dir = ROOT / "uploads" / "sozlesmeler_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    data = m.render_sozlesme_docx_v2(ctx)
    path = out_dir / "preview_mid867_v2.docx"
    path.write_bytes(data)
    check("preview_file", path.is_file() and path.stat().st_size > 1000, str(path.stat().st_size if path.is_file() else 0))
    from docx import Document
    from io import BytesIO

    doc = Document(BytesIO(data))
    full = chr(10).join(p.text for p in doc.paragraphs)
    check("preview_ek1", "Ek-1" in full)
    check("preview_ek4", "Ek-4" in full)
    _clean_flags()
    reload(m)


def test_module_exists():
    src = (ROOT / "sozlesme_sablon_v2.py").read_text(encoding="utf-8")
    check("module_aktif_mid", "def sozlesme_sablon_v2_aktif(mid" in src)
    check("module_midler", "def sozlesme_sablon_v2_midler" in src)
    check("module_render", "def render_sozlesme_docx_v2" in src)


def _ek1_rows(doc) -> list[tuple[str, str]]:
    """TABLE1 Ek-1 → [(alan, deger), ...]"""
    if len(doc.tables) < 2:
        return []
    out = []
    for row in doc.tables[1].rows:
        if len(row.cells) < 2:
            continue
        out.append(((row.cells[0].text or "").strip(), (row.cells[1].text or "").strip()))
    return out


def _ek1_blob(rows: list[tuple[str, str]]) -> str:
    return "\n".join(f"{a}|{b}" for a, b in rows).casefold()


def test_ek1_senaryolar():
    """Oda aç/kapa; ödeme günü 15/31/3; sabit satırlar; silinen 4 satır yok."""
    from sozlesme_sablon_v2 import build_fill_ctx, render_sozlesme_docx_v2
    from docx import Document
    from io import BytesIO

    # --- oda KAPALI (Sanal Ofis, oda_no yok) ---
    ctx0 = build_fill_ctx(
        musteri={"name": "ODA YOK AS", "tax_number": "1", "guncel_kira_bedeli": 500.0},
        kyc={"hizmet_turu": "Sanal Ofis", "aylik_kira": 500.0},
        sozlesme_tarihi="2026-03-15",
        hizmet_turu="Sanal Ofis",
    )
    check("oda0_secili_false", ctx0.get("oda_secili") is False)
    check("odeme_gun_15", ctx0.get("odeme_gun") == 15, str(ctx0.get("odeme_gun")))
    check("odeme_metin_15", "15." in (ctx0.get("odeme_gunu_metin") or ""), str(ctx0.get("odeme_gunu_metin")))
    doc0 = Document(BytesIO(render_sozlesme_docx_v2(ctx0)))
    rows0 = _ek1_rows(doc0)
    blob0 = _ek1_blob(rows0)
    check("oda0_satir_yok", "oda / alan" not in blob0, blob0[:300])
    check("silinen_yillik_artis_yok", "yıllık artış" not in blob0 and "yillik artis" not in blob0)
    check("silinen_indirimli_yok", "indirimli" not in blob0)
    check("silinen_kurulum_yok", "kurulum" not in blob0)
    check("silinen_depozito_yok", "depozito" not in blob0)
    check("sabit_kullanim", "hafta içi 09:00" in blob0 or "hafta ici 09:00" in blob0)
    check("sabit_fesih", "30 gün" in blob0 or "30 gun" in blob0)
    check("sabit_gecikme", "tcmb avans" in blob0)
    check("odeme_hucre_15", "15." in blob0 and ("ödeme" in blob0 or "odeme" in blob0))

    # --- oda AÇIK (hazir_ofis_oda_no) ---
    ctx1 = build_fill_ctx(
        musteri={
            "name": "ODA VAR AS",
            "tax_number": "2",
            "guncel_kira_bedeli": 800.0,
            "hazir_ofis_oda_no": 12,
        },
        kyc={"hizmet_turu": "Hazır Ofis", "aylik_kira": 800.0, "hazir_ofis_oda_no": 12},
        sozlesme_tarihi="2026-01-31",
        hizmet_turu="Hazır Ofis",
    )
    check("oda1_secili", ctx1.get("oda_secili") is True)
    check("oda1_metin", "12" in (ctx1.get("oda_metin") or ""), str(ctx1.get("oda_metin")))
    check("odeme_gun_31", ctx1.get("odeme_gun") == 31, str(ctx1.get("odeme_gun")))
    doc1 = Document(BytesIO(render_sozlesme_docx_v2(ctx1)))
    rows1 = _ek1_rows(doc1)
    blob1 = _ek1_blob(rows1)
    check("oda1_satir_var", "oda / alan" in blob1)
    check("oda1_deger", "12" in blob1)
    check("odeme_hucre_31", "31." in blob1)

    # --- gün 3 ---
    ctx3 = build_fill_ctx(
        musteri={"name": "GUN3", "tax_number": "3", "guncel_kira_bedeli": 100.0},
        kyc={"hizmet_turu": "Sanal Ofis"},
        sozlesme_tarihi="2026-05-03",
    )
    check("odeme_gun_3", ctx3.get("odeme_gun") == 3, str(ctx3.get("odeme_gun")))
    check("odeme_metin_3", "3." in (ctx3.get("odeme_gunu_metin") or ""))
    doc3 = Document(BytesIO(render_sozlesme_docx_v2(ctx3)))
    blob3 = _ek1_blob(_ek1_rows(doc3))
    check("odeme_hucre_3", "3." in blob3)

    # Şablon dosyasında da silinen satırlar yok
    from sozlesme_sablon_v2 import sozlesme_sablon_v2_path

    sablon = Document(str(sozlesme_sablon_v2_path()))
    sblob = _ek1_blob(_ek1_rows(sablon))
    check("sablon_indirimli_yok", "indirimli" not in sblob)
    check("sablon_kurulum_yok", "kurulum" not in sblob)
    check("sablon_depozito_yok", "depozito" not in sblob)
    check("sablon_yillik_artis_yok", "yıllık artış" not in sblob and "yillik artis" not in sblob)


def main() -> int:
    test_karar_matris()
    test_sablon_file()
    test_fill_and_ekler()
    test_ek1_senaryolar()
    test_routes_wire()
    test_mid867_preview_liste()
    test_module_exists()
    print("---")
    if FAILS:
        print("FAILS", len(FAILS), FAILS)
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
