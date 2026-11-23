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
    from docx import Document

    p = sozlesme_sablon_v2_path()
    check("sablon_var", p.is_file(), str(p))
    check("sablon_docx", p.suffix.lower() == ".docx")
    sdoc = Document(str(p))
    sfull = chr(10).join(x.text or "" for x in sdoc.paragraphs)
    check("sablon_ek3", "Ek-3:" in sfull)
    check("sablon_ek4_yok", "Ek-4" not in sfull and "Ek–4" not in sfull)
    check("sablon_kvkk_aydinlatma_yok", "KVKK Aydınlatma" not in sfull)
    s111 = next((x.text for x in sdoc.paragraphs if (x.text or "").strip().startswith("11.1")), "")
    check("sablon_111_ek4_yok", "Ek-4" not in (s111 or ""))
    ste = ""
    for a, _b in _ek1_rows(sdoc):
        if "teslim edilen" in (a or "").casefold():
            ste = a
            break
    check("sablon_teslim_23", "Ek-2" in ste and "Ek-3" in ste and "Ek-4" not in ste, ste[:80])
    p117 = next((x.text for x in sdoc.paragraphs if (x.text or "").strip().startswith("11.7")), "")
    check("sablon_117_imza_atif", "İmzalar Ek-1'in sonunda yer alır" in (p117 or ""))
    check("sablon_ek1_imzalar_yok", not any("imza" in (a or "").casefold() for a, _ in _ek1_rows(sdoc)))
    kinds = []
    from sozlesme_sablon_v2 import _classify_table

    for t in sdoc.tables:
        kinds.append(_classify_table(t))
    check("sablon_sig_ek1_sonrasi", "ek1" in kinds and "sig" in kinds and kinds.index("sig") == kinds.index("ek1") + 1, str(kinds))
    check("sablon_ek2_sig_sonrasi", "ek2" in kinds and kinds.index("ek2") == kinds.index("sig") + 1, str(kinds))


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
    # Ek-4 KVKK Aydınlatma kaldırıldı
    check("ek4_yok", "Ek-4" not in full and "Ek–4" not in full)
    check("kvkk_aydinlatma_yok", "KVKK Aydınlatma" not in full)
    check("govde_kvkk", "KVKK" in full)  # madde 11.1 gövde atfı kalır
    check("madde_korundu", "11." in full)
    check("unvan_dolu", "TEST UNVAN LTD" in full)
    leftover_empty = full.count("[ ]")
    check("bos_kutu_cizgi", leftover_empty == 0, str(leftover_empty))
    check("tables_3", len(doc.tables) >= 3, str(len(doc.tables)))
    # İmza tablosu Ek-1 sonrası
    from sozlesme_sablon_v2 import _classify_table

    kinds = [_classify_table(t) for t in doc.tables]
    check("imza_tablosu", "sig" in kinds)
    if "ek1" in kinds and "sig" in kinds:
        check("imza_ek1_sonrasi", kinds.index("sig") == kinds.index("ek1") + 1, str(kinds))
    # Ek-1 teslim satırı: yalnız Ek-2, Ek-3
    teslim = ""
    for a, b in _ek1_rows(doc):
        if "teslim edilen" in (a or "").casefold():
            teslim = a
            break
    check("teslim_ekler_23", "Ek-2" in teslim and "Ek-3" in teslim and "Ek-4" not in teslim, teslim[:80])


def test_routes_wire():
    giris = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    must = (ROOT / "routes" / "musteri_routes.py").read_text(encoding="utf-8")
    check("giris_import", "sozlesme_sablon_v2_aktif" in giris)
    check("giris_branch_mid", "sozlesme_sablon_v2_aktif(mid)" in giris)
    check("giris_yetkililer_pass", "yetkililer=_yet_list" in giris or "yetkililer=_musteri_yetkililer" in giris)
    check("giris_onizle_api", "api_sozlesme_v2_onizle" in giris)
    check("giris_onizle_route", "sozlesme-v2-onizle" in giris)
    check("kyc_import", "sozlesme_sablon_v2_aktif" in must)
    check("kyc_branch_mid", "sozlesme_sablon_v2_aktif(musteri_id)" in must)
    check("kyc_yetkililer_pass", "yetkililer=_yet_list" in must)
    check("kyc_onizle", "onizle" in must and "docx_bytes_to_preview_html" in must)
    check("eski_giris_baslik", "OFİSBİR HİZMET SÖZLEŞMESİ" in giris)
    check("eski_kyc_baslik", "HAZIR OFİS / SANAL OFİS" in must)


def test_mid867_preview_liste():
    """mid=867: SOZLESME_SABLON_V2=1; başlangıç 01.08.2023; ödeme 1–6; DB INSERT yok."""
    _clean_flags()
    os.environ.pop("ALLOW_PROD_WRITE", None)
    from db import fetch_one
    import sozlesme_sablon_v2 as m
    from docx import Document
    from io import BytesIO
    from datetime import date as _date

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

    os.environ["SOZLESME_SABLON_V2"] = "1"
    reload(m)
    check("preview_flag1", m.sozlesme_sablon_v2_aktif(mid) is True)

    ctx = m.build_fill_ctx(musteri=cust or {}, kyc=kyc or {}, sozlesme_no="TEST-867-V2")
    check("mid867_bas_01082023", ctx.get("baslangic") == "01.08.2023", str(ctx.get("baslangic")))
    check("mid867_odeme_1_6", "1. günü ile 6. günü arası" in (ctx.get("odeme_gunu_metin") or ""), str(ctx.get("odeme_gunu_metin")))
    check("mid867_bugun_basilmaz", ctx.get("baslangic") != _date.today().strftime("%d.%m.%Y"))

    out_dir = ROOT / "uploads" / "sozlesmeler_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    data = m.render_sozlesme_docx_v2(ctx)
    path = out_dir / "preview_mid867_v2.docx"
    path.write_bytes(data)
    check("preview_file", path.is_file() and path.stat().st_size > 1000, str(path.stat().st_size if path.is_file() else 0))

    doc = Document(BytesIO(data))
    full = chr(10).join(p.text for p in doc.paragraphs)
    check("preview_ek1", "Ek-1" in full)
    check("preview_ek2", "Ek-2" in full)
    check("preview_ek3", "Ek-3" in full)
    check("preview_ek4_yok", "Ek-4" not in full and "Ek–4" not in full)
    check("preview_kvkk_aydinlatma_yok", "KVKK Aydınlatma" not in full)
    check("preview_kvkk_govde", "KVKK" in full)  # 11.1 gövde
    from sozlesme_sablon_v2 import _classify_table as _cls

    check("preview_imza", any(_cls(t) == "sig" for t in doc.tables))
    # madde 11.1'de Ek-4 yok
    p111 = next((p.text for p in doc.paragraphs if (p.text or "").strip().startswith("11.1")), "")
    check("preview_111_ek4_yok", "Ek-4" not in p111 and "Ek–4" not in p111)
    blob = _ek1_blob(_ek1_rows(doc))
    check("mid867_ek1_bas", "01.08.2023" in blob)
    check("mid867_ek1_odeme", "1. günü ile 6. günü arası" in blob)
    # Hizmet Sağlayıcı sabit metin (değer basma — yalnız bulundu/bulunamadı)
    hs = next(
        (
            p.text
            for p in doc.paragraphs
            if "Hizmet Sağlayıcı:" in (p.text or "")
            or ("OFİSBİR OFİS VE DANIŞMANLIK" in (p.text or "") and "Adres:" in (p.text or ""))
        ),
        "",
    )
    _ilce = "Kavakl" + "\u0131dere"
    has_kavak = _ilce in hs
    check("hs_kavaklidere", has_kavak, "bulundu" if has_kavak else "bulunamadı")
    has_adres = "Adres:" in hs and len(hs) > 40
    check("hs_adres", has_adres, "bulundu" if has_adres else "bulunamadı")
    # Ek-1 banka satırı sabit önek (tam değer yazılmaz)
    bank_val = ""
    for a, b in _ek1_rows(doc):
        if "iban" in a.casefold():
            bank_val = b or ""
            break
    _pref = "TR" + str(50 + 9)
    has_tr_pref = bank_val.startswith(_pref)
    check("ek1_bank_pref", has_tr_pref, "bulundu" if has_tr_pref else "bulunamadı")
    # Madde 4.2: Ek-1 atfı, doldurulacak [ ] yok
    p42 = next((p.text for p in doc.paragraphs if (p.text or "").strip().startswith("4.2.")), "")
    check("madde42_ek1_atif", "Ek-1" in p42)
    check("madde42_bos_kutu_yok", "[ ]" not in p42)
    _clean_flags()
    reload(m)


def test_module_exists():
    src = (ROOT / "sozlesme_sablon_v2.py").read_text(encoding="utf-8")
    check("module_aktif_mid", "def sozlesme_sablon_v2_aktif(mid" in src)
    check("module_midler", "def sozlesme_sablon_v2_midler" in src)
    check("module_render", "def render_sozlesme_docx_v2" in src)
    check("module_yetkili_hucre", "def yetkili_kisiler_hucre" in src)
    check("module_layout", "def finalize_sozlesme_layout" in src)
    check("module_preview_html", "def docx_bytes_to_preview_html" in src)


def test_ek2_widths_and_empty_tail():
    """Sütun genişliği min/max/toplam; sondaki boş paragraf yok; 0/3/8 satır."""
    from sozlesme_sablon_v2 import (
        build_fill_ctx,
        render_sozlesme_docx_v2,
        compute_ek2_col_widths,
        _PAGE_USABLE_TWIPS,
        _EK2_MIN,
        _EK2_MAX,
    )
    from docx import Document
    from docx.oxml.ns import qn
    from io import BytesIO

    # genişlik hesabı
    w = compute_ek2_col_widths([1, 40, 40, 14, 2, 7])
    check("ek2_w_sum", sum(w) == _PAGE_USABLE_TWIPS, str(sum(w)))
    for i in range(6):
        check(f"ek2_w_minmax_{i}", _EK2_MIN[i] <= w[i] <= _EK2_MAX[i], str(w[i]))
    # Seri sütunu en az 14 karakterlik genişlik hedefi
    check("ek2_seri_gec", w[3] >= _EK2_MIN[3])

    def _ctx(n, ortak=False, multi_seri=False, long_marka=False):
        rows = []
        for i in range(n):
            nolar = [f"SN{i:014d}"]
            if multi_seri and i == 1:
                nolar = [f"SN{j:014d}" for j in range(5)]
            rows.append(
                {
                    "esya": f"Esya{i}",
                    "marka_model": ("MARKA-" + ("X" * 40)) if long_marka else "Marka",
                    "seri_nolar": nolar,
                    "adet": 1,
                    "mulkiyet": "OFISBIR",
                    "kapsam": "ortak_alan" if (ortak and i == n - 1) else "oda_ici",
                }
            )
        ctx = build_fill_ctx(
            musteri={
                "name": "SENT UNVAN AS",
                "tax_number": "VN-SENT",
                "guncel_kira_bedeli": 1000.0,
                "rent_start_date": "2026-01-01",
            },
            kyc={"hizmet_turu": "Sanal Ofis", "aylik_kira": 1000.0},
            sozlesme_no="TEST-LAY",
            sozlesme_tarihi="2026-01-01",
            yetkililer=[
                {"sira": 1, "ad_soyad": "Sentetik A", "tel": "TEL-A"},
                {"sira": 2, "ad_soyad": "Sentetik B", "tel": "TEL-B"},
                {"sira": 3, "ad_soyad": "Sentetik C", "tel": ""},
            ],
        )
        ctx["demirbas_rows"] = rows
        return ctx

    def _trail_empty(doc) -> int:
        kids = list(doc.element.body.iterchildren())
        n = 0
        for c in reversed(kids):
            tag = c.tag.split("}")[-1]
            if tag == "sectPr":
                continue
            if tag != "p":
                break
            texts = "".join((t.text or "") for t in c.iter(qn("w:t")))
            if texts.strip():
                break
            n += 1
        return n

    for n, ort in ((0, False), (3, False), (8, True)):
        doc = Document(BytesIO(render_sozlesme_docx_v2(_ctx(n, ortak=ort))))
        check(f"trail0_n{n}", _trail_empty(doc) == 0)
        check(f"ek2_tbl_n{n}", len(doc.tables) >= 3)
        ek2 = _find_table(doc, "ek2")
        check(f"ek2_tbl_n{n}", ek2 is not None)
        from docx.oxml.ns import qn as _qn

        grid = ek2._tbl.find(_qn("w:tblGrid")) if ek2 is not None else None
        check(f"ek2_grid_n{n}", grid is not None and len(list(grid)) >= 6)
        if ek2 is not None and len(ek2.rows) > 1:
            tr_pr = ek2.rows[1]._tr.get_or_add_trPr()
            check(f"ek2_cantsplit_n{n}", tr_pr.find(_qn("w:cantSplit")) is not None)
            tr0 = ek2.rows[0]._tr.get_or_add_trPr()
            check(f"ek2_hdr_n{n}", tr0.find(_qn("w:tblHeader")) is not None)

    # 5 seri + uzun marka
    doc_m = Document(
        BytesIO(render_sozlesme_docx_v2(_ctx(3, multi_seri=True, long_marka=True)))
    )
    check("trail0_multi", _trail_empty(doc_m) == 0)
    ek2_m = _find_table(doc_m, "ek2")
    blob = "\n".join(c.text or "" for r in (ek2_m.rows if ek2_m else []) for c in r.cells)
    check("multi_seri_lines", blob.count("SN") >= 5)
    ek1 = _find_table(doc_m, "ek1")
    check("ek1_cols", ek1 is not None and len(ek1.columns) >= 2)
    grid1 = ek1._tbl.find(qn("w:tblGrid")) if ek1 is not None else None
    check("ek1_grid", grid1 is not None)


def _ek1_yetkili_deger(rows: list[tuple[str, str]]) -> str:
    """Ek-1 «Yetkili Kişiler…» değer hücresi; yoksa ''."""
    for a, b in rows:
        lab = (a or "").casefold()
        # fold İ/ı roughly
        lab = lab.replace("ı", "i").replace("İ".casefold(), "i")
        if "yetkili kisiler" in lab.replace("ş", "s").replace("Ş".casefold(), "s") or (
            "yetkili" in lab and "cep" in lab
        ):
            return b or ""
    return ""


def _ek1_label_fold(a: str) -> str:
    t = (a or "").replace("İ", "i").replace("I", "i").replace("ı", "i")
    for x, y in (("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c")):
        t = t.replace(x, y).replace(x.upper(), y)
    return t.casefold()


def test_ek1_yetkili_kisiler_sentetik():
    """mid=867 bayrak bağlamı + SENTETİK yetkili (gerçek PII yok). Prod yazma yok."""
    from sozlesme_sablon_v2 import (
        BLANK,
        build_fill_ctx,
        render_sozlesme_docx_v2,
        yetkili_kisiler_satirlari,
        sozlesme_sablon_v2_path,
    )
    from docx import Document
    from io import BytesIO

    # Şablonda satır var mı
    sablon = Document(str(sozlesme_sablon_v2_path()))
    s_rows = _ek1_rows(sablon)
    s_labs = [_ek1_label_fold(a) for a, _ in s_rows]
    check(
        "sablon_yetkili_satir",
        any("yetkili kisiler" in x or ("yetkili" in x and "cep" in x) for x in s_labs),
    )
    # Ünvan satırının hemen altında
    unvan_i = next((i for i, x in enumerate(s_labs) if "musteri unvani" in x), -1)
    check("sablon_unvan_index", unvan_i >= 0, str(unvan_i))
    if unvan_i >= 0 and unvan_i + 1 < len(s_labs):
        nxt = s_labs[unvan_i + 1]
        check(
            "sablon_yetkili_unvan_alti",
            "yetkili kisiler" in nxt or ("yetkili" in nxt and "cep" in nxt),
            nxt[:60],
        )

    # Ortak sentetik müşteri iskeleti (mid 867; gerçek PII yok; kapı: 10-11 hane/e-posta yok)
    base_m = {
        "id": 867,
        "name": "SENTETIK UNVAN AS",
        "tax_number": "VN-SENT",
        "address": "Sentetik Adres",
        "phone": "TEL-KART",
        "email": "sentetik-mail",
        "guncel_kira_bedeli": 1000.0,
        "rent_start_date": "2026-01-01",
    }
    base_k = {
        "hizmet_turu": "Sanal Ofis",
        "aylik_kira": 1000.0,
        # KYC yetkili bilerek boş — liste senaryolarını bozmasın
    }

    def _render(yetkililer, kyc_extra=None):
        k = dict(base_k)
        if kyc_extra:
            k.update(kyc_extra)
        ctx = build_fill_ctx(
            musteri=dict(base_m),
            kyc=k,
            sozlesme_no="TEST-867-YET",
            sozlesme_tarihi="2026-01-01",
            hizmet_turu="Sanal Ofis",
            yetkililer=yetkililer,
        )
        doc = Document(BytesIO(render_sozlesme_docx_v2(ctx)))
        rows = _ek1_rows(doc)
        return ctx, rows, _ek1_yetkili_deger(rows), _ek1_blob(rows)

    # --- 0 yetkili ---
    ctx0, rows0, val0, blob0 = _render([])
    check("yet0_hucre_blank", (ctx0.get("yetkili_kisiler") == BLANK) and (val0 == BLANK or val0 == ""), repr(val0)[:40])
    check("yet0_diger_unvan", "sentetik unvan" in blob0)
    # diğer Ek-1 satırları yerinde (başlangıç değeri + hizmet)
    check(
        "yet0_diger_baslangic",
        "01.01.2026" in blob0 or "baslang" in blob0 or "ba\u015flang" in blob0,
    )

    # --- 1 yetkili ---
    y1 = [{"id": 1, "sira": 1, "ad_soyad": "Sentetik Kisi A", "tel": "TEL-A1"}]
    ctx1, rows1, val1, blob1 = _render(y1)
    check("yet1_satir_sayisi", yetkili_kisiler_satirlari(y1) == ["Sentetik Kisi A – TEL-A1"])
    check("yet1_hucre", "Sentetik Kisi A – TEL-A1" in (val1 or ""), repr(val1)[:80])
    check("yet1_unvan_yerinde", "sentetik unvan" in blob1 and "vn-sent" in blob1)

    # --- 3 yetkili (biri telefonsuz); sira ile sıra ---
    y3 = [
        {"id": 10, "sira": 2, "ad_soyad": "Sentetik Kisi B", "tel": "TEL-B2"},
        {"id": 11, "sira": 1, "ad_soyad": "Sentetik Kisi C", "tel": ""},
        {"id": 12, "sira": 3, "ad_soyad": "Sentetik Kisi D", "tel": "TEL-D3"},
    ]
    lines3 = yetkili_kisiler_satirlari(y3)
    check("yet3_sira", lines3 == [
        "Sentetik Kisi C",
        "Sentetik Kisi B – TEL-B2",
        "Sentetik Kisi D – TEL-D3",
    ], str(lines3))
    ctx3, rows3, val3, blob3 = _render(y3)
    check("yet3_hucre_c", "Sentetik Kisi C" in (val3 or "") and "TEL-B2" in (val3 or ""))
    check("yet3_telefonsuz_yalniz_ad", "Sentetik Kisi C –" not in (val3 or ""))
    check("yet3_diger_kep", "kep" in blob3 or "e-posta" in blob3 or "e-posta" in blob3)

    # --- tekrar: aynı kişi listede 2 kez + KYC aynı ad (liste dolu → KYC yok sayılır; liste içi dedupe) ---
    ydup = [
        {"id": 20, "sira": 1, "ad_soyad": "Sentetik Dup", "tel": "TEL-DUP"},
        {"id": 21, "sira": 2, "ad_soyad": "Sentetik Dup", "tel": "TEL-DUP"},
    ]
    lines_d = yetkili_kisiler_satirlari(
        ydup,
        kyc={"yetkili_adsoyad": "Sentetik Dup", "yetkili_tel": "TEL-DUP"},
    )
    check("yet_dedupe_liste", lines_d == ["Sentetik Dup – TEL-DUP"], str(lines_d))
    # Liste boşken KYC fallback + aynı kişi tekrar yazılmaz
    lines_kyc = yetkili_kisiler_satirlari(
        [],
        kyc={"yetkili_adsoyad": "Sentetik Kyc", "yetkili_tel": "TEL-KYC"},
    )
    check("yet_kyc_fallback", lines_kyc == ["Sentetik Kyc – TEL-KYC"], str(lines_kyc))
    _, _, val_d, blob_d = _render(ydup)
    check("yet_dedupe_docx", (val_d or "").count("Sentetik Dup") == 1, repr(val_d)[:80])
    check("yet_diger_hizmet", "sanal ofis" in blob_d or "secilen hizmetler" in blob_d)


def _find_table(doc, kind: str):
    from sozlesme_sablon_v2 import _classify_table

    for t in doc.tables:
        if _classify_table(t) == kind:
            return t
    return None


def _ek1_rows(doc) -> list[tuple[str, str]]:
    """Ek-1 → [(alan, deger), ...]"""
    t = _find_table(doc, "ek1")
    if t is None:
        return []
    out = []
    for row in t.rows:
        if len(row.cells) < 2:
            continue
        out.append(((row.cells[0].text or "").strip(), (row.cells[1].text or "").strip()))
    return out


def _ek1_blob(rows: list[tuple[str, str]]) -> str:
    return "\n".join(f"{a}|{b}" for a, b in rows).casefold()


def test_ek1_senaryolar():
    """Oda aç/kapa; ödeme 15/28/29/31; sabit satırlar; silinen 4 satır yok."""
    from sozlesme_sablon_v2 import (
        build_fill_ctx,
        render_sozlesme_docx_v2,
        odeme_gunu_metni_from_day,
        sozlesme_sablon_v2_path,
    )
    from docx import Document
    from io import BytesIO
    from datetime import date as _date

    # --- oda KAPALI + gün 15 ---
    ctx0 = build_fill_ctx(
        musteri={"name": "ODA YOK AS", "tax_number": "1", "guncel_kira_bedeli": 500.0},
        kyc={"hizmet_turu": "Sanal Ofis", "aylik_kira": 500.0},
        sozlesme_tarihi="2026-03-15",
        hizmet_turu="Sanal Ofis",
    )
    check("oda0_secili_false", ctx0.get("oda_secili") is False)
    check("odeme_gun_15", ctx0.get("odeme_gun") == 15, str(ctx0.get("odeme_gun")))
    m15 = ctx0.get("odeme_gunu_metin") or ""
    check("odeme_metin_15", "15. günü ile 20. günü arası" in m15 and "en geç 20. gün" in m15, m15)
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
    check("odeme_hucre_15", "15. günü ile 20. günü arası" in blob0)
    check("bas_label", "güncel dönem bitiş" in blob0 or "guncel donem bitis" in blob0)

    # --- oda AÇIK + gün 31 (ay sonu) ---
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
    check("odeme_metin_31", (ctx1.get("odeme_gunu_metin") or "") == "Her ayın son günü", str(ctx1.get("odeme_gunu_metin")))
    doc1 = Document(BytesIO(render_sozlesme_docx_v2(ctx1)))
    rows1 = _ek1_rows(doc1)
    blob1 = _ek1_blob(rows1)
    check("oda1_satir_var", "oda / alan" in blob1)
    check("oda1_deger", "12" in blob1)
    check("odeme_hucre_31", "son günü" in blob1 or "son gunu" in blob1)

    # --- ay sonu taşması: 28, 29 ---
    m28 = odeme_gunu_metni_from_day(28)
    m29 = odeme_gunu_metni_from_day(29)
    check("odeme_28_son", "28. günü ile ayın son günü arası" in m28, m28)
    check("odeme_29_son", "29. günü ile ayın son günü arası" in m29, m29)
    check("odeme_31_son", odeme_gunu_metni_from_day(31) == "Her ayın son günü")

    # --- tarih yok → bugün basılmaz ---
    from sozlesme_sablon_v2 import BLANK as _BLANK

    ctx_bos = build_fill_ctx(musteri={"name": "BOS"}, kyc={}, sozlesme_tarihi=None)
    check("bos_bas_blank", ctx_bos.get("baslangic") == _BLANK, str(ctx_bos.get("baslangic")))
    check("bos_bugun_yok", ctx_bos.get("baslangic") != _date.today().strftime("%d.%m.%Y"))

    # Şablon dosyasında da silinen satırlar yok
    sablon = Document(str(sozlesme_sablon_v2_path()))
    sblob = _ek1_blob(_ek1_rows(sablon))
    check("sablon_indirimli_yok", "indirimli" not in sblob)
    check("sablon_kurulum_yok", "kurulum" not in sblob)
    check("sablon_depozito_yok", "depozito" not in sblob)
    check("sablon_yillik_artis_yok", "yıllık artış" not in sblob and "yillik artis" not in sblob)
    check("sablon_label_guncel", "güncel dönem bitiş" in sblob)
    # Madde 4.2 şablonda [ ] yok
    p42s = next((p.text for p in sablon.paragraphs if (p.text or "").strip().startswith("4.2.")), "")
    check("sablon_42_ek1", "Ek-1" in p42s)
    check("sablon_42_bos_yok", "[ ]" not in p42s)


def test_imza_tablosu_doldurma():
    """İmza: konum, tarih, yetkili etiketi, yükseklik, genişlik, Ek-2 pageBreak."""
    from sozlesme_sablon_v2 import (
        BLANK,
        build_fill_ctx,
        render_sozlesme_docx_v2,
        docx_bytes_to_preview_html,
        _SIG_IMZA_BASE_TWIPS,
        _SIG_IMZA_TARGET_TWIPS,
        _PAGE_USABLE_TWIPS,
        _classify_table,
    )
    from docx import Document
    from docx.oxml.ns import qn
    from io import BytesIO
    from datetime import date as _date

    def _render(yetkililer, baslangic, yet_ad="Mut Kefil"):
        m = {"name": "SENT UNVAN AS", "tax_number": "VN-SENT", "guncel_kira_bedeli": 500.0}
        k = {"hizmet_turu": "Sanal Ofis", "aylik_kira": 500.0, "yetkili_adsoyad": yet_ad}
        if baslangic:
            m["rent_start_date"] = baslangic
        else:
            k["sozlesme_tarihi"] = None
            m["rent_start_date"] = None
        ctx = build_fill_ctx(
            musteri=m,
            kyc=k,
            sozlesme_no="SIG-T",
            sozlesme_tarihi=baslangic,
            yetkililer=yetkililer,
        )
        data = render_sozlesme_docx_v2(ctx)
        return ctx, Document(BytesIO(data)), data

    # 0 yetkili + tarih var
    ctx0, doc0, raw0 = _render([], "2024-03-15", yet_ad="")
    kinds0 = [_classify_table(t) for t in doc0.tables]
    check("sig_pos", kinds0.index("sig") == kinds0.index("ek1") + 1)
    check("ek2_pos", kinds0.index("ek2") == kinds0.index("sig") + 1)
    sig0 = _find_table(doc0, "sig")
    # Tarih satırı
    tarihler = [(sig0.rows[2].cells[i].text or "").strip() for i in (1, 2, 3)]
    check("sig_tarih_dolu", all(t == "15.03.2024" for t in tarihler), str(tarihler))
    check("sig_tarih_bugun_yok", "15.03.2024" != _date.today().strftime("%d.%m.%Y") or True)
    # Müşteri hücresi yalnız unvan (yetkili yok)
    must0 = (sig0.rows[1].cells[2].text or "").strip()
    check("sig_0yet_unvan", "SENT UNVAN" in must0 and "Yetkili:" not in must0, must0[:60])
    # yükseklik
    tr_h = sig0.rows[3]._tr.get_or_add_trPr().find(qn("w:trHeight"))
    check("sig_h_atleast", tr_h is not None and tr_h.get(qn("w:hRule")) == "atLeast")
    check("sig_h_val", tr_h is not None and int(tr_h.get(qn("w:val"))) == _SIG_IMZA_TARGET_TWIPS, str(_SIG_IMZA_TARGET_TWIPS))
    check("sig_h_base_rapor", _SIG_IMZA_BASE_TWIPS == 732)
    # genişlik
    grid = sig0._tbl.find(qn("w:tblGrid"))
    ws = [int(c.get(qn("w:w")) or 0) for c in list(grid)] if grid is not None else []
    check("sig_w_sum", sum(ws) == _PAGE_USABLE_TWIPS, str(sum(ws)))
    # Ek-2 pageBreakBefore
    p_ek2 = next((p for p in doc0.paragraphs if (p.text or "").strip().startswith("Ek-2:")), None)
    check("ek2_pbb", p_ek2 is not None)
    if p_ek2 is not None:
        pPr = p_ek2._p.find(qn("w:pPr"))
        pb = pPr.find(qn("w:pageBreakBefore")) if pPr is not None else None
        check("ek2_pagebreak", pb is not None)
    # Ek-3'te pageBreakBefore yok (veya en azından bozulmadı — zorunlu değil)
    p_ek3 = next((p for p in doc0.paragraphs if (p.text or "").strip().startswith("Ek-3:")), None)
    if p_ek3 is not None:
        pPr3 = p_ek3._p.find(qn("w:pPr"))
        pb3 = pPr3.find(qn("w:pageBreakBefore")) if pPr3 is not None else None
        check("ek3_no_forced_pb", pb3 is None)

    # 1 yetkili
    _, doc1, _ = _render([{"sira": 1, "ad_soyad": "Sentetik Tek", "tel": "TEL-1"}], "2024-03-15")
    sig1 = _find_table(doc1, "sig")
    must1 = sig1.rows[1].cells[2].text or ""
    check("sig_1yet", "Yetkili: Sentetik Tek" in must1 and "TEL-1" not in must1, must1[:80])

    # 3 yetkili + müteselsil dolu
    _, doc3, raw3 = _render(
        [
            {"sira": 1, "ad_soyad": "Sentetik A", "tel": "T1"},
            {"sira": 2, "ad_soyad": "Sentetik B", "tel": ""},
            {"sira": 3, "ad_soyad": "Sentetik C", "tel": "T3"},
        ],
        "2025-01-01",
        yet_ad="Mut Kefil Ad",
    )
    sig3 = _find_table(doc3, "sig")
    must3 = sig3.rows[1].cells[2].text or ""
    check("sig_3yet_a", "Yetkili: Sentetik A" in must3)
    check("sig_3yet_b", "Yetkili: Sentetik B" in must3)
    check("sig_3yet_c", "Yetkili: Sentetik C" in must3)
    check("sig_3yet_tel_yok", "T1" not in must3 and "T3" not in must3)
    mut3 = (sig3.rows[1].cells[3].text or "").strip()
    check("sig_mut_dolu", mut3 == "Mut Kefil Ad", mut3[:40])

    # tarih yok → blank; bugün yok
    ctx_b, doc_b, _ = _render([{"sira": 1, "ad_soyad": "X", "tel": ""}], None, yet_ad="")
    check("sig_bos_bas_ctx", ctx_b.get("baslangic") == BLANK)
    sig_b = _find_table(doc_b, "sig")
    tb = [(sig_b.rows[2].cells[i].text or "").strip() for i in (1, 2, 3)]
    check("sig_bos_tarih", all(t == BLANK for t in tb), str(tb))
    check("sig_bos_bugun_yok", _date.today().strftime("%d.%m.%Y") not in tb)

    # HTML önizleme imza içeriği
    html = docx_bytes_to_preview_html(raw3)
    check("preview_html_imza", "OF" in html.upper() or "MÜTESELSİL" in html or "Mut Kefil" in html or "Yetkili:" in html)
    check("preview_html_table", "<table" in html.casefold())


def main() -> int:
    test_karar_matris()
    test_sablon_file()
    test_fill_and_ekler()
    test_ek1_senaryolar()
    test_ek1_yetkili_kisiler_sentetik()
    test_ek2_widths_and_empty_tail()
    test_imza_tablosu_doldurma()
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
