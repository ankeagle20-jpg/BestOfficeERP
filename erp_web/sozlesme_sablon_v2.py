# -*- coding: utf-8 -*-
"""Ofisbir sözleşme şablonu v2 (DOCX).

Bayrak: SOZLESME_SABLON_V2 (varsayılan kapalı / 0) → herkese v2.
Liste: SOZLESME_SABLON_V2_MIDLER (virgüllü mid; boş/tanımsız = kimse).
Karar: bayrak=1 → v2; değilse mid listede ise v2; aksi halde eski şablon.
Prod'da bayrak/listeyi açmayın.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

BLANK = "________"

_SABLON_DIR = Path(__file__).resolve().parent / "sozlesme_sablonlari"
_SABLON_V2 = _SABLON_DIR / "ofisbir_sozlesme_v2.docx"

# Sözleşme metnindeki varsayılan süre/oran kutuları — doldurulmaz, olduğu gibi kalır.
_KEEP_LITERAL = {
    "[30]",
    "[15]",
    "[7]",
    "[2]",
    "[% ]",
}


def sozlesme_sablon_v2_global_acik() -> bool:
    raw = str(os.environ.get("SOZLESME_SABLON_V2") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def sozlesme_sablon_v2_midler() -> set[int]:
    """SOZLESME_SABLON_V2_MIDLER → geçerli mid kümesi; boşluk/geçersiz atlanır."""
    raw = os.environ.get("SOZLESME_SABLON_V2_MIDLER")
    if raw is None:
        return set()
    out: set[int] = set()
    for part in str(raw).split(","):
        tok = part.strip()
        if not tok:
            continue
        try:
            mid = int(tok)
        except (TypeError, ValueError):
            continue
        if mid > 0:
            out.add(mid)
    return out


def sozlesme_sablon_v2_aktif(mid=None) -> bool:
    """Tek karar noktası: global bayrak veya mid listesi."""
    if sozlesme_sablon_v2_global_acik():
        return True
    if mid is None or mid == "":
        return False
    try:
        mid_i = int(mid)
    except (TypeError, ValueError):
        return False
    if mid_i <= 0:
        return False
    return mid_i in sozlesme_sablon_v2_midler()


def sozlesme_sablon_v2_path() -> Path:
    return _SABLON_V2


def _tl_fmt(v: Any) -> str:
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_date(v: Any) -> str:
    if v is None or v == "":
        return ""
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%d.%m.%Y")
        except Exception:
            return ""
    s = str(v).strip()
    if not s:
        return ""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        return s[:10]


def _parse_date(v: Any) -> date | None:
    """Kart/KYC tarihini date'e çevir; boş/geçersiz → None (bugün uydurma)."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    for fmt, n in (("%Y-%m-%d", 10), ("%d.%m.%Y", 10), ("%d/%m/%Y", 10)):
        try:
            return datetime.strptime(s[:n], fmt).date()
        except Exception:
            continue
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _anniversary_on(year: int, bas: date) -> date:
    import calendar

    last = calendar.monthrange(year, bas.month)[1]
    return date(year, bas.month, min(bas.day, last))


def _guncel_donem_bitis(bas: date, bugun: date | None = None) -> date | None:
    """Başlangıç yıldönümüne göre içinde bulunulan 1 yıllık dönemin bitiş tarihi."""
    if not bas:
        return None
    bugun = bugun or date.today()
    a_this = _anniversary_on(bugun.year, bas)
    if bugun < a_this:
        return a_this
    return _anniversary_on(bugun.year + 1, bas)


def odeme_gunu_metni_from_day(d: int | None) -> str:
    """Belge ödeme günü metni (ERP tahsilat gününe dokunmaz)."""
    if d is None:
        return BLANK
    try:
        d_i = int(d)
    except (TypeError, ValueError):
        return BLANK
    if d_i < 1 or d_i > 31:
        return BLANK
    if d_i == 31:
        return "Her ayın son günü"
    end = d_i + 5
    if end > 31:
        return (
            f"Her ayın {d_i}. günü ile ayın son günü arası (en geç ayın son günü)"
        )
    return (
        f"Her ayın {d_i}. günü ile {end}. günü arası (en geç {end}. gün)"
    )


def _s(v: Any) -> str:
    return str(v or "").strip()


def _or_blank(v: Any) -> str:
    t = _s(v)
    return t if t else BLANK


def build_fill_ctx(
    *,
    musteri: dict | None = None,
    kyc: dict | None = None,
    sozlesme_no: str | None = None,
    sozlesme_tarihi=None,
    hizmet_turu: str | None = None,
) -> dict:
    """Müşteri + KYC + firma_profil → şablon doldurma bağlamı (PII rapora yazılmaz)."""
    m = musteri or {}
    k = kyc or {}

    firma = {}
    try:
        from firma_profil import pdf_firma_varsa

        firma = pdf_firma_varsa() or {}
    except Exception:
        firma = {}

    unvan = (
        _s(k.get("sirket_unvani"))
        or _s(k.get("unvan"))
        or _s(m.get("name"))
    )
    vergi_no = _s(k.get("vergi_no")) or _s(m.get("tax_number"))
    vergi_dairesi = _s(k.get("vergi_dairesi")) or _s(m.get("vergi_dairesi"))
    mersis = _s(k.get("mersis_no"))
    adres = (
        _s(k.get("yeni_adres"))
        or _s(k.get("eski_adres"))
        or _s(m.get("tebligat_adresi"))
        or _s(m.get("address"))
        or _s(m.get("ev_adres"))
    )
    tel = _s(k.get("yetkili_tel")) or _s(m.get("phone"))
    email = _s(k.get("yetkili_email")) or _s(k.get("email")) or _s(m.get("email"))
    yet_ad = _s(k.get("yetkili_adsoyad"))
    yet_tc = _s(k.get("yetkili_tcno"))
    mersis = mersis or _s(m.get("mersis_no"))

    aylik = k.get("aylik_kira")
    if aylik in (None, "", 0, 0.0):
        for key in ("guncel_kira_bedeli", "current_rent", "reel_kira_bedeli", "ilk_kira_bedeli", "aylik_kira"):
            if m.get(key) not in (None, "", 0, 0.0):
                aylik = m.get(key)
                break
    try:
        aylik_f = float(aylik or 0)
    except (TypeError, ValueError):
        aylik_f = 0.0
    yillik = k.get("yillik_kira")
    try:
        yillik_f = float(yillik or 0)
    except (TypeError, ValueError):
        yillik_f = 0.0
    if yillik_f <= 0 and aylik_f > 0:
        yillik_f = aylik_f * 12

    # Başlangıç: kart rent_start_date → KYC sozlesme_tarihi → (opsiyonel) çağıran; bugün YOK
    bas = _parse_date(m.get("rent_start_date"))
    if bas is None:
        bas = _parse_date(k.get("sozlesme_tarihi"))
    if bas is None:
        bas = _parse_date(sozlesme_tarihi)
    bas_s = _fmt_date(bas) if bas else ""
    # Güncel dönem bitiş: başlangıç yıldönümü (kart tarihi yoksa boş)
    bit_d = _guncel_donem_bitis(bas) if bas else None
    bit_s = _fmt_date(bit_d) if bit_d else ""

    ht = _s(hizmet_turu) or _s(k.get("hizmet_turu")) or "Sanal Ofis"

    # Oda seçimi: hazir_ofis_oda_no (KYC/customers) veya hizmet_turu «hazır ofis»
    oda_no = k.get("hazir_ofis_oda_no")
    if oda_no in (None, "", 0, "0"):
        oda_no = m.get("hazir_ofis_oda_no")
    try:
        oda_no_i = int(oda_no) if oda_no not in (None, "") else 0
    except (TypeError, ValueError):
        oda_no_i = 0
    ht_l = ht.casefold()
    for a, b in (("ı", "i"), ("İ", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c")):
        ht_l = ht_l.replace(a, b)
    hazir_ofis = ("hazir" in ht_l and "ofis" in ht_l)
    oda_secili = bool(oda_no_i > 0 or hazir_ofis)
    oda_metin = ""
    if oda_secili:
        if oda_no_i > 0:
            oda_metin = f"Oda No: {oda_no_i}"
        else:
            oda_metin = ht

    # Belge ödeme günü: başlangıç günü d … d+5 (ERP odeme_gunu değişmez)
    odeme_gun = int(bas.day) if bas else None
    odeme_gunu_metin = odeme_gunu_metni_from_day(odeme_gun)

    firma_vd = _s(firma.get("vergi_dairesi"))
    firma_vn = _s(firma.get("vergi_no"))
    firma_vd_vn = " / ".join([x for x in (firma_vd, firma_vn) if x]) or BLANK

    return {
        "sozlesme_no": _s(sozlesme_no) or _s(k.get("sozlesme_no")),
        "firma_adres": _s(firma.get("adres")) or BLANK,
        "firma_vd_vn": firma_vd_vn,
        "firma_mersis": BLANK,  # firma_profil'de MERSİS yok
        "musteri_unvan": unvan or BLANK,
        "musteri_tc_vergi": vergi_no or BLANK,
        "musteri_mersis": mersis or BLANK,
        "musteri_adres": adres or BLANK,
        "musteri_tel": tel or BLANK,
        "musteri_email": email or BLANK,
        "yetkili_ad": yet_ad or BLANK,
        "yetkili_tc": yet_tc or BLANK,
        "hizmet_turu": ht,
        "baslangic": bas_s or BLANK,
        "bitis": bit_s or BLANK,
        "baslangic_bitis": (
            f"{bas_s or BLANK} / {bit_s or BLANK}"
        ),
        "aylik_tl": _tl_fmt(aylik_f) or BLANK,
        "yillik_tl": _tl_fmt(yillik_f) or BLANK,
        "aylik_kdv_dahil": _tl_fmt(aylik_f * 1.20) if aylik_f > 0 else BLANK,
        "yillik_kdv_dahil": _tl_fmt(yillik_f * 1.20) if yillik_f > 0 else BLANK,
        "vergi_dairesi": vergi_dairesi or BLANK,
        "nusha": "2",
        "oda_secili": oda_secili,
        "oda_metin": oda_metin,
        "odeme_gunu_metin": odeme_gunu_metin,
        "odeme_gun": odeme_gun,
    }


def _replace_first(text: str, needle: str, value: str) -> str:
    if needle not in text:
        return text
    return text.replace(needle, value, 1)


def _fill_paragraph_text(text: str, ctx: dict) -> str:
    """Paragraf metnindeki köşeli alanları bağlamla doldur; kalan boş [ ] → BLANK."""
    if not text:
        return text
    t = text

    # Hizmet Sağlayıcı (adres/VD/VN/MERSİS): şablonda sabit — firma_profil ile EZME
    if "Hizmet Sağlayıcı:" in t or (
        "OFİSBİR OFİS VE DANIŞMANLIK" in t and "Adres:" in t
    ):
        return t

    if t.strip().startswith("Müşteri:") or "Müşteri: [" in t:
        t = t.replace("[ad-soyad / ticaret unvanı]", ctx["musteri_unvan"], 1)
        # sırayla boş kutular
        for key in (
            "musteri_tc_vergi",
            "musteri_mersis",
            "musteri_adres",
            "musteri_tel",
            "musteri_email",
        ):
            t = _replace_first(t, "[ ]", ctx[key])

    if "Yetkili ve Müteselsil Borçlu:" in t:
        t = t.replace("[ad-soyad]", ctx["yetkili_ad"], 1)
        t = _replace_first(t, "[ ]", ctx["yetkili_tc"])

    if "Sözleşme [" in t and "nüsha" in t:
        t = _replace_first(t, "[ ]", ctx["nusha"])

    if "Teslim tarihi:" in t:
        t = _replace_first(t, "[ ]", BLANK)  # teslim tarihi bilinmiyor
        t = _replace_first(t, "[ ]", BLANK)  # anahtar
        t = _replace_first(t, "[ ]", BLANK)  # hasar

    if "teminat bedeli" in t.lower() or "Depozito/teminat" in t or "depozito/teminat" in t:
        # "Kalan tutar ... [ ] iş günü"
        pass

    # Kalan boş doldurulabilir [ ] → çizgi; sabit sayılar korunur
    def _blank_sub(m: re.Match) -> str:
        tok = m.group(0)
        if tok in _KEEP_LITERAL:
            return tok
        if tok.startswith("[Avukat:") or tok.startswith("[Bu bölüm"):
            return tok
        if tok in ("[ad-soyad]", "[ad-soyad / ticaret unvanı]"):
            return BLANK
        if tok == "[ ]" or re.fullmatch(r"\[\s*\]", tok):
            return BLANK
        if re.fullmatch(r"\[%\s*\]", tok):
            return tok  # faiz oranı bilinmiyorsa olduğu gibi
        return tok

    t = re.sub(r"\[[^\]]*\]", _blank_sub, t)
    return t


def _set_run_text_preserve(paragraph, new_text: str) -> None:
    """Paragraf metnini tek run'a indirger (stil kaybı minimal)."""
    if paragraph.text == new_text:
        return
    if not paragraph.runs:
        paragraph.add_run(new_text)
        return
    paragraph.runs[0].text = new_text
    for r in paragraph.runs[1:]:
        r.text = ""


def _tr_fold(s: str) -> str:
    """Türkçe karakterleri ASCII'ye yaklaştırıp casefold (İ/I güvenli)."""
    t = (s or "").replace("İ", "i").replace("I", "i").replace("ı", "i")
    for a, b in (("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c"),
                 ("Ş", "s"), ("Ğ", "g"), ("Ü", "u"), ("Ö", "o"), ("Ç", "c")):
        t = t.replace(a, b)
    return t.casefold()


def _delete_table_row(table, row_idx: int) -> None:
    row = table.rows[row_idx]
    row._tr.getparent().remove(row._tr)


def _set_cell_text(cell, val: str) -> None:
    for p in cell.paragraphs:
        _set_run_text_preserve(p, val)
        break
    else:
        cell.text = val


def _fill_table_ek1(table, ctx: dict) -> None:
    """Ek-1 (2 sütun Alan/Değer) satırlarını doldur; oda yoksa satırı sil."""
    if len(table.columns) < 2:
        return
    mapping = {
        "musteri unvani": f"{ctx['musteri_unvan']} / {ctx['musteri_tc_vergi']}",
        "sozlesme baslangic": ctx.get("baslangic_bitis")
        or f"{ctx['baslangic']} / {ctx['bitis']}",
        "secilen hizmetler": ctx["hizmet_turu"],
        "aylik bedel": f"{ctx['aylik_tl']} TL + KDV" if ctx["aylik_tl"] != BLANK else BLANK,
        "aylik toplam": f"{ctx['aylik_kdv_dahil']} TL" if ctx["aylik_kdv_dahil"] != BLANK else BLANK,
        "yillik donem": f"{ctx['yillik_kdv_dahil']} TL" if ctx["yillik_kdv_dahil"] != BLANK else BLANK,
        "odeme gunu": ctx.get("odeme_gunu_metin") or BLANK,
        "kep ve e-posta": ctx["musteri_email"],
    }
    # Sabit metinli satırlar (şablonda yazılı; üzerine yazma)
    skip_fill = (
        "kullanim gun",
        "fesih bildirim",
        "gecikme faizi",
        "iban",  # şablondaki sabit banka satırı — dokunma
    )
    oda_secili = bool(ctx.get("oda_secili"))
    oda_metin = ctx.get("oda_metin") or ""
    # Önce oda satırını sil (yoksa), sonra doldur — index kayması için sondan
    oda_del = []
    for i, row in enumerate(table.rows):
        if len(row.cells) < 2:
            continue
        lab = _tr_fold(row.cells[0].text or "")
        if "oda / alan" in lab or lab.startswith("oda / alan") or "oda / alan" in lab:
            if not oda_secili:
                oda_del.append(i)
    for i in reversed(oda_del):
        _delete_table_row(table, i)

    for row in table.rows:
        cells = row.cells
        if len(cells) < 2:
            continue
        label_raw = (cells[0].text or "").strip()
        label = _tr_fold(label_raw)
        if not label or label == "alan":
            continue
        if any(s in label for s in skip_fill):
            continue
        val_cell = cells[1]
        cur = (val_cell.text or "").strip()

        if "oda / alan" in label:
            if oda_secili:
                _set_cell_text(val_cell, oda_metin or BLANK)
            continue

        matched = False
        for key, val in mapping.items():
            if key in label:
                _set_cell_text(val_cell, val)
                matched = True
                break
        if matched:
            continue
        # [ ] içeren değer hücrelerini boş çizgiye çevir
        if "[ ]" in cur or "[% ]" in cur:
            for p in val_cell.paragraphs:
                if p.text:
                    _set_run_text_preserve(p, _fill_paragraph_text(p.text, ctx))


def _fill_signature_table(table, ctx: dict) -> None:
    """İmza tablosu: ünvan satırı."""
    if len(table.rows) < 2:
        return
    # R1: Ünvan / Ad Soyad — OFİSBİR | MÜŞTERİ | MÜTESELSİL
    row = table.rows[1]
    cells = row.cells
    if len(cells) >= 4:
        if cells[2].paragraphs:
            _set_run_text_preserve(cells[2].paragraphs[0], ctx["musteri_unvan"])
        if cells[3].paragraphs:
            _set_run_text_preserve(cells[3].paragraphs[0], ctx["yetkili_ad"])


def render_sozlesme_docx_v2(ctx: dict, *, sablon_path: Path | None = None) -> bytes:
    """Şablonu doldurup DOCX baytları döndürür. DB yazmaz."""
    from docx import Document

    path = Path(sablon_path or _SABLON_V2)
    if not path.is_file():
        raise FileNotFoundError(f"v2 sablon yok: {path}")

    doc = Document(str(path))
    for para in doc.paragraphs:
        if not para.text:
            continue
        new_t = _fill_paragraph_text(para.text, ctx)
        if new_t != para.text:
            _set_run_text_preserve(para, new_t)

    for ti, table in enumerate(doc.tables):
        # TABLE0 imza, TABLE1 Ek-1, TABLE2 Ek-2 envanter
        if ti == 0:
            _fill_signature_table(table, ctx)
        elif ti == 1:
            _fill_table_ek1(table, ctx)
        else:
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        if p.text and "[" in p.text:
                            nt = _fill_paragraph_text(p.text, ctx)
                            if nt != p.text:
                                _set_run_text_preserve(p, nt)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def save_sozlesme_docx_v2(ctx: dict, filepath: str | Path) -> Path:
    data = render_sozlesme_docx_v2(ctx)
    out = Path(filepath)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out
