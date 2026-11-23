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


def _yetkili_fold(s: str) -> str:
    return _tr_fold(str(s or "").strip())


def yetkili_kisiler_satirlari(
    yetkililer: list | None = None,
    *,
    kyc: dict | None = None,
    musteri: dict | None = None,
) -> list[str]:
    """Yetkili satırları: «Ad Soyad – telefon» (tel yoksa yalnız ad). Tekrarlar elenir.

    Öncelik: musteri_yetkililer listesi (kart «yetkililer» sekmesi /api/musteri).
    Liste boşsa KYC/customers düz alanına düşülür (tek kişi).
    """
    k = kyc or {}
    m = musteri or {}
    rows = [r for r in (yetkililer or []) if isinstance(r, dict)]
    # musteri dict içinden de gelebilir
    if not rows and isinstance(m.get("yetkililer"), list):
        rows = [r for r in m.get("yetkililer") or [] if isinstance(r, dict)]

    out: list[str] = []
    seen: set[tuple[str, str]] = set()

    def _ekle(ad: str, tel: str) -> None:
        ad_t = _s(ad)
        if not ad_t:
            return
        tel_t = _s(tel)
        key = (_yetkili_fold(ad_t), _yetkili_fold(tel_t))
        if key in seen:
            return
        seen.add(key)
        out.append(f"{ad_t} – {tel_t}" if tel_t else ad_t)

    if rows:
        # Tablodaki sıra (sira, id); ikisi de yoksa ad sırası
        def _sk(r: dict):
            try:
                sira = int(r.get("sira") or 0)
            except (TypeError, ValueError):
                sira = 0
            try:
                rid = int(r.get("id") or 0)
            except (TypeError, ValueError):
                rid = 0
            return (sira, rid if rid else 10**12, _yetkili_fold(r.get("ad_soyad") or ""))

        rows = sorted(rows, key=_sk)
        for r in rows:
            _ekle(r.get("ad_soyad"), r.get("tel"))
    else:
        # Liste boş: KYC / customers düz yetkili alanları (tek kişi)
        ad = _s(k.get("yetkili_adsoyad")) or _s(m.get("yetkili_kisi")) or _s(m.get("yetkili_ad"))
        tel = _s(k.get("yetkili_tel")) or _s(m.get("phone"))
        _ekle(ad, tel)
    return out


def yetkili_kisiler_hucre(
    yetkililer: list | None = None,
    *,
    kyc: dict | None = None,
    musteri: dict | None = None,
) -> str:
    lines = yetkili_kisiler_satirlari(yetkililer, kyc=kyc, musteri=musteri)
    return "\n".join(lines) if lines else BLANK


def yetkili_adlari(
    yetkililer: list | None = None,
    *,
    kyc: dict | None = None,
    musteri: dict | None = None,
) -> list[str]:
    """Yetkili adları (telefonsuz); Ek-1 ile aynı kaynak/sıra/dedupe."""
    out: list[str] = []
    for line in yetkili_kisiler_satirlari(yetkililer, kyc=kyc, musteri=musteri):
        ad = (line.split(" – ", 1)[0] if " – " in line else line).strip()
        if ad:
            out.append(ad)
    return out


def build_fill_ctx(
    *,
    musteri: dict | None = None,
    kyc: dict | None = None,
    sozlesme_no: str | None = None,
    sozlesme_tarihi=None,
    hizmet_turu: str | None = None,
    yetkililer: list | None = None,
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

    yet_list = yetkililer
    if yet_list is None and isinstance(m.get("yetkililer"), list):
        yet_list = m.get("yetkililer")
    yet_hucre = yetkili_kisiler_hucre(yet_list, kyc=k, musteri=m)
    yet_ads = yetkili_adlari(yet_list, kyc=k, musteri=m)

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
        "yetkili_kisiler": yet_hucre,
        "yetkili_adlari": yet_ads,
        # Ek-2 dipnot (varsayılan boş → çizgi); route doldurabilir
        "ek2_anahtar_kart": None,
        "ek2_hasar_aciklama": "",
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

    # Ek-2 dipnot: _fill_ek2_dipnot ile doldurulur (burada [ ] ezme)
    if "Teslim tarihi:" in t and (
        "anahtar" in t.casefold() or "kart" in t.casefold()
    ):
        return t

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
    text = "" if val is None else str(val)
    if "\n" not in text:
        for p in cell.paragraphs:
            _set_run_text_preserve(p, text)
            break
        else:
            cell.text = text
        return
    # Çok satır: ilk paragraf + ek paragraflar
    lines = text.split("\n")
    paras = list(cell.paragraphs)
    if not paras:
        cell.text = lines[0]
        paras = list(cell.paragraphs)
    _set_run_text_preserve(paras[0], lines[0])
    for extra in paras[1:]:
        _set_run_text_preserve(extra, "")
    for line in lines[1:]:
        p = cell.add_paragraph()
        p.add_run(line)


def _fill_table_ek1(table, ctx: dict) -> None:
    """Ek-1 (2 sütun Alan/Değer) satırlarını doldur; oda yoksa satırı sil."""
    if len(table.columns) < 2:
        return
    mapping = {
        "musteri unvani": f"{ctx['musteri_unvan']} / {ctx['musteri_tc_vergi']}",
        "yetkili kisiler": ctx.get("yetkili_kisiler") or BLANK,
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


# İmza / Kaşe satırı: şablonda auto (~36.6 pt ≈ 732 twip); hedef 2.5× atLeast
_SIG_IMZA_BASE_TWIPS = 732
_SIG_IMZA_TARGET_TWIPS = int(round(_SIG_IMZA_BASE_TWIPS * 2.5))  # 1830
_SIG_LABEL_MIN, _SIG_LABEL_MAX = 900, 1800


def _set_row_height_at_least(row, twips: int) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tr_pr = row._tr.get_or_add_trPr()
    tr_h = tr_pr.find(qn("w:trHeight"))
    if tr_h is None:
        tr_h = OxmlElement("w:trHeight")
        tr_pr.append(tr_h)
    tr_h.set(qn("w:val"), str(int(twips)))
    tr_h.set(qn("w:hRule"), "atLeast")


def _set_para_keep_next(paragraph, on: bool = True) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    p_pr = paragraph._p.get_or_add_pPr()
    kn = p_pr.find(qn("w:keepNext"))
    if on:
        if kn is None:
            p_pr.append(OxmlElement("w:keepNext"))
    elif kn is not None:
        p_pr.remove(kn)


def _set_page_break_before(paragraph, on: bool = True) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    p_pr = paragraph._p.get_or_add_pPr()
    pb = p_pr.find(qn("w:pageBreakBefore"))
    if on:
        if pb is None:
            p_pr.append(OxmlElement("w:pageBreakBefore"))
    elif pb is not None:
        p_pr.remove(pb)


def compute_sig_col_widths(label_chars: int, content_chars: int) -> list[int]:
    """Etiket dar + 3 eşit imza sütunu; toplam kullanılabilir genişlik."""
    lab = max(_SIG_LABEL_MIN, min(_SIG_LABEL_MAX, _char_width_twips(label_chars)))
    # içerik üç sütuna yayılsın diye alt sınır
    need = max(_char_width_twips(max(content_chars, 8)), 1600)
    rest = _PAGE_USABLE_TWIPS - lab
    each = max(need, rest // 3)
    # sığdır
    if lab + 3 * each > _PAGE_USABLE_TWIPS:
        each = (_PAGE_USABLE_TWIPS - lab) // 3
    widths = [lab, each, each, _PAGE_USABLE_TWIPS - lab - 2 * each]
    return widths


def apply_sig_table_layout(table) -> list[int]:
    if len(table.columns) < 4:
        return []
    lab = 0
    cont = 0
    for row in table.rows:
        if len(row.cells) < 4:
            continue
        lab = max(lab, _max_line_len(row.cells[0].text or ""))
        for ci in (1, 2, 3):
            cont = max(cont, _max_line_len(row.cells[ci].text or ""))
    widths = compute_sig_col_widths(lab, cont)
    _set_table_grid_widths(table, widths)
    for row in table.rows:
        _set_row_cant_split(row)
    _apply_table_font_pt_safe(table, _EK2_FONT_PT)
    return widths


def _fill_signature_table(table, ctx: dict) -> None:
    """İmza tablosu: satır ETİKETİNE göre (Tarih / Ünvan / İmza)."""
    if len(table.columns) < 4:
        return
    unvan = ctx.get("musteri_unvan") or BLANK
    ads = list(ctx.get("yetkili_adlari") or [])
    musteri_lines = [unvan] if unvan and unvan != BLANK else []
    for ad in ads:
        a = _s(ad)
        if a:
            musteri_lines.append(f"Yetkili: {a}")
    if not musteri_lines:
        musteri_lines = [BLANK]
    musteri_blok = "\n".join(musteri_lines)
    bas = ctx.get("baslangic") or BLANK
    if not bas:
        bas = BLANK

    for row in table.rows:
        if len(row.cells) < 4:
            continue
        lab = _tr_fold(row.cells[0].text or "")
        if not lab:
            continue
        if lab.startswith("tarih") or lab == "tarih":
            for ci in (1, 2, 3):
                _set_cell_text(row.cells[ci], bas)
        elif "unvan" in lab or "ad soyad" in lab:
            # OFİSBİR (cells[1]) dokunma
            _set_cell_text(row.cells[2], musteri_blok)
            _set_cell_text(row.cells[3], ctx.get("yetkili_ad") or BLANK)
        elif "imza" in lab:
            _set_row_height_at_least(row, _SIG_IMZA_TARGET_TWIPS)


def _sanitize_ek2_hasar(text: str, *, max_len: int = 500) -> str:
    """Hasar açıklaması: HTML etiketleri + kontrol karakteri temizle; \\n koru; uzunluk kes."""
    s = str(text or "")
    # yalnızca HTML benzeri etiketler (harf ile başlayan); a < b korunur
    s = re.sub(r"</?[A-Za-z][^>]*>", "", s)
    out: list[str] = []
    for ch in s:
        o = ord(ch)
        if ch in ("\n", "\t") or o >= 32:
            out.append(ch)
        elif ch == "\r":
            continue
    cleaned = "".join(out).replace("\r\n", "\n").replace("\r", "\n")
    # XML kaçışı python-docx text API ile; burada yalnızca uzunluk
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned.strip()


def _set_para_text_multiline(paragraph, text: str) -> None:
    """Paragrafa metin yaz; \\n → soft line break (w:br)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    lines = (text or "").split("\n")
    # mevcut run'ları temizle
    for child in list(paragraph._p):
        if child.tag.endswith("}r"):
            paragraph._p.remove(child)
    if not lines:
        paragraph.add_run("")
        return
    run = paragraph.add_run(lines[0])
    for line in lines[1:]:
        br = OxmlElement("w:br")
        run._r.append(br)
        run2 = paragraph.add_run(line)
        run = run2


def _fill_ek2_dipnot(doc, ctx: dict) -> bool:
    """Ek-2 dipnot: teslim tarihi / anahtar / hasar. OFİSBİR/MÜŞTERİ artık 2 hücreli tabloda."""
    for p in doc.paragraphs:
        t = p.text or ""
        if "Teslim tarihi:" not in t:
            continue
        if "anahtar" not in t.casefold() and "kart" not in t.casefold():
            continue
        bas = ctx.get("baslangic") or BLANK
        if not bas:
            bas = BLANK
        ak = ctx.get("ek2_anahtar_kart")
        if ak is None or ak == "":
            anahtar_s = BLANK
        else:
            anahtar_s = str(ak).strip() or BLANK
        hasar = _sanitize_ek2_hasar(ctx.get("ek2_hasar_aciklama") or "")
        hasar_s = hasar if hasar else BLANK
        # İmza çizgileri paragrafta değil; ayrı 2 sütunlu tabloda
        suffix = "Taraflar, envanterin teslim anındaki durumu gösterdiğini kabul eder."
        head = (
            f"Teslim tarihi: {bas}. "
            f"Teslim edilen anahtar / kart sayısı: {anahtar_s}. "
            f"Mevcut hasar veya eksiklikler: "
        )
        _set_para_text_multiline(p, head + hasar_s + ". " + suffix)
        # dipnot + imza tablosu aynı sayfada kalsın
        _set_para_keep_next(p, True)
        return True
    return False


def _is_ek2_teslim_imza_table(table) -> bool:
    """Ek-2 dipnot altı OFİSBİR | MÜŞTERİ (2 hücre) tablosu."""
    if len(table.rows) < 1 or len(table.columns) != 2:
        return False
    a = _tr_fold(table.rows[0].cells[0].text or "")
    b = _tr_fold(table.rows[0].cells[1].text or "")
    return a.startswith("ofisbir") and b.startswith("musteri")


_EK2_IMZA_BEFORE_TWIPS = 240  # ~12 pt ≈ bir satır boşluk (boş paragraf yerine)


def _set_para_spacing_before(paragraph, twips: int) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    p_pr = paragraph._p.get_or_add_pPr()
    sp = p_pr.find(qn("w:spacing"))
    if sp is None:
        sp = OxmlElement("w:spacing")
        p_pr.append(sp)
    sp.set(qn("w:before"), str(int(twips)))


def _set_para_keep_lines(paragraph, on: bool = True) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    p_pr = paragraph._p.get_or_add_pPr()
    kl = p_pr.find(qn("w:keepLines"))
    if on:
        if kl is None:
            p_pr.append(OxmlElement("w:keepLines"))
    elif kl is not None:
        p_pr.remove(kl)


def _set_table_borders_nil(table) -> None:
    """Kenarlıksız tablo (nil)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tbl_pr = table._tbl.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        table._tbl.insert(0, tbl_pr)
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = borders.find(qn(f"w:{edge}"))
        if el is None:
            el = OxmlElement(f"w:{edge}")
            borders.append(el)
        el.set(qn("w:val"), "nil")
        el.set(qn("w:sz"), "0")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "auto")


def _clear_cell_shading(cell) -> None:
    """Hücre dolgusunu kaldır (Docs başlık gri/koyu taşınmasını sil)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "auto")


def _set_cell_valign(cell, val: str = "top") -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    va = tc_pr.find(qn("w:vAlign"))
    if va is None:
        va = OxmlElement("w:vAlign")
        tc_pr.append(va)
    va.set(qn("w:val"), val)


def _set_cell_margins_equal(cell, twips: int = 0) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    mar = tc_pr.find(qn("w:tcMar"))
    if mar is None:
        mar = OxmlElement("w:tcMar")
        tc_pr.append(mar)
    for edge in ("top", "left", "bottom", "right"):
        el = mar.find(qn(f"w:{edge}"))
        if el is None:
            el = OxmlElement(f"w:{edge}")
            mar.append(el)
        el.set(qn("w:w"), str(int(twips)))
        el.set(qn("w:type"), "dxa")


def _normalize_run_plain_black(paragraph, *, font_pt: float) -> None:
    """Kalın/italik/renk farkını sil; siyah + sabit punto."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    half = str(int(round(float(font_pt) * 2)))
    for run in paragraph.runs:
        try:
            run.bold = False
            run.italic = False
            run.underline = False
            run.font.color.rgb = RGBColor(0, 0, 0)
            run.font.size = Pt(font_pt)
        except Exception:
            pass
        r_pr = run._r.get_or_add_rPr()
        # highlight / shading on run
        for tag in ("highlight", "shd", "b", "i", "u", "color"):
            el = r_pr.find(qn(f"w:{tag}"))
            if el is not None and tag in ("highlight", "shd", "b", "i", "u"):
                r_pr.remove(el)
        col = r_pr.find(qn("w:color"))
        if col is None:
            col = OxmlElement("w:color")
            r_pr.append(col)
        col.set(qn("w:val"), "000000")
        for sz_tag in ("sz", "szCs"):
            sz = r_pr.find(qn(f"w:{sz_tag}"))
            if sz is None:
                sz = OxmlElement(f"w:{sz_tag}")
                r_pr.append(sz)
            sz.set(qn("w:val"), half)


def _set_para_spacing_exact(paragraph, *, before: int, after: int = 0, line: int | None = None) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    p_pr = paragraph._p.get_or_add_pPr()
    sp = p_pr.find(qn("w:spacing"))
    if sp is None:
        sp = OxmlElement("w:spacing")
        p_pr.append(sp)
    sp.set(qn("w:before"), str(int(before)))
    sp.set(qn("w:after"), str(int(after)))
    if line is not None:
        sp.set(qn("w:line"), str(int(line)))
        sp.set(qn("w:lineRule"), "auto")
    # girinti sıfır
    ind = p_pr.find(qn("w:ind"))
    if ind is not None:
        p_pr.remove(ind)


def _clear_row_tbl_header(row) -> None:
    """Docs'tan gelen tblHeader (koyu başlık biçimi) kaldır."""
    from docx.oxml.ns import qn

    tr_pr = row._tr.get_or_add_trPr()
    th = tr_pr.find(qn("w:tblHeader"))
    if th is not None:
        tr_pr.remove(th)


def apply_ek2_teslim_imza_layout(table) -> list[int]:
    """2 eşit sütun, çizgisiz, dolgusuz, aynı dikey hiza, spacing before her iki hücrede."""
    if not _is_ek2_teslim_imza_table(table):
        return []
    half = _PAGE_USABLE_TWIPS // 2
    widths = [half, _PAGE_USABLE_TWIPS - half]
    _set_table_grid_widths(table, widths)
    _set_table_borders_nil(table)
    for row in table.rows:
        _clear_row_tbl_header(row)
        _set_row_cant_split(row)
        for cell in row.cells:
            _clear_cell_shading(cell)
            _set_cell_valign(cell, "top")
            _set_cell_margins_equal(cell, 0)
            for p in cell.paragraphs:
                # İKİ hücrede aynı before — aksi halde MÜŞTERİ yukarı kayar
                _set_para_spacing_exact(p, before=_EK2_IMZA_BEFORE_TWIPS, after=0)
                _set_para_keep_lines(p, True)
                try:
                    p.alignment = 0  # LEFT
                except Exception:
                    pass
                _normalize_run_plain_black(p, font_pt=_EK2_FONT_PT)
    _apply_table_font_pt_safe(table, _EK2_FONT_PT)
    return widths


def ensure_ek2_teslim_imza_layout(doc) -> list[int]:
    for t in doc.tables:
        if _is_ek2_teslim_imza_table(t):
            return apply_ek2_teslim_imza_layout(t)
    return []


def _ek2_clone_row(table, template_row) -> None:
    from copy import deepcopy

    table._tbl.append(deepcopy(template_row._tr))


def _ek2_set_row(row, vals: list[str]) -> None:
    for i, val in enumerate(vals):
        if i >= len(row.cells):
            break
        _set_cell_text(row.cells[i], val)


def _fill_table_ek2(table, ctx: dict) -> None:
    """Ek-2 demirbaş envanteri. Satır yoksa şablondaki boş satırlar (en az 5 veri) kalır."""
    if len(table.columns) < 6 or len(table.rows) < 2:
        return
    from demirbas import (
        demirbas_adet_normalize,
        demirbas_marka_normalize,
        demirbas_satirlari_ek2_sirali,
    )

    # Başlık: "Adet / Durum" → "Adet"
    try:
        hdr = table.rows[0]
        for cell in hdr.cells:
            t = (cell.text or "").strip()
            if "adet" in t.casefold():
                _set_cell_text(cell, "Adet")
    except Exception:
        pass

    raw_rows = list(ctx.get("demirbas_rows") or [])
    if not raw_rows:
        # Boş: en az 5 veri satırı olsun (şablon 4 ise 1 ekle)
        while len(table.rows) < 6:  # header + 5
            _ek2_clone_row(table, table.rows[1])
            r = table.rows[-1]
            _ek2_set_row(
                r,
                [str(len(table.rows) - 1), "", "", "", "", "OFİSBİR / Müşteri"],
            )
        return

    ordered = demirbas_satirlari_ek2_sirali(raw_rows)
    oda = [r for r in ordered if (r.get("kapsam") or "oda_ici") != "ortak_alan"]
    ortak = [r for r in ordered if (r.get("kapsam") or "") == "ortak_alan"]

    display: list[dict] = []
    for r in oda:
        display.append({"kind": "item", "row": r})
    if ortak:
        display.append({"kind": "header", "title": "Ortak alan"})
        for r in ortak:
            display.append({"kind": "item", "row": r})

    tmpl = table.rows[1]
    # Mevcut veri satırlarını temizle (header kalsın)
    while len(table.rows) > 1:
        table.rows[1]._tr.getparent().remove(table.rows[1]._tr)

    sira_n = 0
    for ent in display:
        _ek2_clone_row(table, tmpl)
        row = table.rows[-1]
        if ent["kind"] == "header":
            _ek2_set_row(row, ["", ent["title"], "", "", "", ""])
            continue
        sira_n += 1
        r = ent["row"]
        mul = str(r.get("mulkiyet") or "OFISBIR").upper()
        mul_txt = "OFİSBİR" if mul in ("OFISBIR", "OFİSBİR") else "Müşteri"
        nolar = r.get("seri_nolar")
        if not isinstance(nolar, list) or not nolar:
            sn = str(r.get("seri_no") or "").strip()
            nolar = [sn] if sn else []
        nolar = [str(x).strip() for x in nolar if str(x).strip()]
        adet = demirbas_adet_normalize(
            r.get("adet"),
            default=demirbas_adet_normalize(r.get("adet_durum"), default=max(1, len(nolar) or 1)),
        )
        marka = demirbas_marka_normalize(r.get("marka_model"))
        _ek2_set_row(
            row,
            [
                str(sira_n),
                str(r.get("esya") or ""),
                marka,
                "\n".join(nolar),
                str(adet),
                mul_txt,
            ],
        )


# --- Sayfa / tablo düzeni (boş sayfa + sütun sığdırma) ---
# Şablon Letter 12240×15840, kenar 1440 → kullanılabilir genişlik 9360 twip
_PAGE_USABLE_TWIPS = 9360
_EK2_FONT_PT = 9
# Sıra | Eşya | Marka | Seri No | Adet | Mülkiyet
_EK2_MIN = (420, 1100, 1000, 1680, 480, 900)
_EK2_MAX = (720, 3000, 2800, 3200, 720, 1400)
_EK2_PAD = 120  # hücre içi pay
_EK1_MIN = (2200, 2800)
_EK1_MAX = (4200, 7000)


def _para_has_visible_text(p_el) -> bool:
    from docx.oxml.ns import qn

    for t in p_el.iter(qn("w:t")):
        if (t.text or "").strip():
            return True
    # sayfa sonu kırılımı «içerik» sayılır — silme
    for br in p_el.iter(qn("w:br")):
        if br.get(qn("w:type")) == "page":
            return True
    return False


def strip_trailing_empty_paragraphs(doc) -> int:
    """Body sectPr öncesi sondaki boş paragrafları sil (boş son sayfa kök nedeni)."""
    body = doc.element.body
    kids = list(body.iterchildren())
    removed = 0
    # sondan: sectPr'yi koru, boş w:p sil
    for child in reversed(kids):
        tag = child.tag.split("}")[-1]
        if tag == "sectPr":
            continue
        if tag != "p":
            break
        if _para_has_visible_text(child):
            break
        body.remove(child)
        removed += 1
    return removed


def _char_width_twips(n_chars: int, *, font_pt: float = _EK2_FONT_PT) -> int:
    """Ortalama orantılı genişlik tahmini (twip)."""
    n = max(0, int(n_chars))
    return int(n * font_pt * 10) + _EK2_PAD  # ~0.5em ≈ 10 twip/pt


def _max_line_len(text: str) -> int:
    lines = (text or "").split("\n")
    return max((len(ln) for ln in lines), default=0)


def compute_ek2_col_widths(col_max_chars: list[int]) -> list[int]:
    """İçerik karakter uzunluklarına göre 6 sütun; toplam = kullanılabilir genişlik."""
    if len(col_max_chars) < 6:
        col_max_chars = list(col_max_chars) + [0] * (6 - len(col_max_chars))
    raw = []
    for i in range(6):
        w = _char_width_twips(col_max_chars[i])
        w = max(_EK2_MIN[i], min(_EK2_MAX[i], w))
        raw.append(w)
    total = sum(raw)
    usable = _PAGE_USABLE_TWIPS
    if total == usable:
        return raw
    # orantılı ölçekle; min/max'a yeniden kenetle (2 tur)
    for _ in range(3):
        total = sum(raw) or 1
        scale = usable / total
        raw = [max(_EK2_MIN[i], min(_EK2_MAX[i], int(raw[i] * scale))) for i in range(6)]
        if abs(sum(raw) - usable) <= 6:
            break
    # kalan farkı esnek sütunlara (eşya/marka) dağıt
    diff = usable - sum(raw)
    flex = [1, 2]
    i = 0
    while diff != 0 and i < 60:
        idx = flex[i % len(flex)]
        step = 1 if diff > 0 else -1
        nw = raw[idx] + step
        if _EK2_MIN[idx] <= nw <= _EK2_MAX[idx]:
            raw[idx] = nw
            diff -= step
        i += 1
    # hâlâ fark varsa son çare sıra dışındaki geniş sütuna
    diff = usable - sum(raw)
    if diff:
        raw[3] = max(_EK2_MIN[3], min(_EK2_MAX[3], raw[3] + diff))
        diff = usable - sum(raw)
        if diff:
            raw[1] = raw[1] + diff
    return raw


def compute_ek1_col_widths(label_chars: int, value_chars: int) -> list[int]:
    raw = [
        max(_EK1_MIN[0], min(_EK1_MAX[0], _char_width_twips(label_chars))),
        max(_EK1_MIN[1], min(_EK1_MAX[1], _char_width_twips(value_chars))),
    ]
    usable = _PAGE_USABLE_TWIPS
    total = sum(raw) or 1
    raw = [int(w * usable / total) for w in raw]
    diff = usable - sum(raw)
    raw[1] += diff
    return raw


def _set_table_grid_widths(table, widths: list[int]) -> None:
    """tblGrid + hücre tcW sabit genişlik."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tbl = table._tbl
    tbl_pr = tbl.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        tbl.insert(0, tbl_pr)
    # sabit düzen
    layout = tbl_pr.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(sum(widths)))
    tbl_w.set(qn("w:type"), "dxa")

    grid = tbl.find(qn("w:tblGrid"))
    if grid is None:
        grid = OxmlElement("w:tblGrid")
        tbl.insert(1, grid)
    for child in list(grid):
        grid.remove(child)
    for w in widths:
        gc = OxmlElement("w:gridCol")
        gc.set(qn("w:w"), str(int(w)))
        grid.append(gc)

    for row in table.rows:
        cells = row.cells
        # birleşik hücre: aynı _tc birden fazla cells[i] olabilir — tc başına bir kez
        seen = set()
        col_i = 0
        for cell in cells:
            tc = cell._tc
            if id(tc) in seen:
                continue
            seen.add(id(tc))
            # gridSpan
            tc_pr = tc.get_or_add_tcPr()
            span_el = tc_pr.find(qn("w:gridSpan"))
            span = int(span_el.get(qn("w:val"))) if span_el is not None else 1
            w_sum = sum(widths[col_i : col_i + span]) if col_i < len(widths) else widths[-1]
            tc_w = tc_pr.find(qn("w:tcW"))
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                tc_pr.append(tc_w)
            tc_w.set(qn("w:w"), str(int(w_sum)))
            tc_w.set(qn("w:type"), "dxa")
            col_i += span


def _set_row_cant_split(row) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tr = row._tr
    tr_pr = tr.get_or_add_trPr()
    cs = tr_pr.find(qn("w:cantSplit"))
    if cs is None:
        cs = OxmlElement("w:cantSplit")
        tr_pr.append(cs)


def _set_header_row_repeat(row) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tr = row._tr
    tr_pr = tr.get_or_add_trPr()
    th = tr_pr.find(qn("w:tblHeader"))
    if th is None:
        th = OxmlElement("w:tblHeader")
        tr_pr.append(th)


def _apply_table_font_pt_safe(table, pt: float = _EK2_FONT_PT) -> None:
    from docx.shared import Pt

    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                if not p.runs and (p.text or ""):
                    t = p.text
                    # mevcut metni tek run yap
                    for child in list(p._p):
                        if child.tag.endswith("}r"):
                            p._p.remove(child)
                    run = p.add_run(t)
                    run.font.size = Pt(pt)
                else:
                    for run in p.runs:
                        run.font.size = Pt(pt)


def _ek2_collect_max_chars(table) -> list[int]:
    maxima = [0] * 6
    for row in table.rows:
        for i, cell in enumerate(row.cells[:6]):
            maxima[i] = max(maxima[i], _max_line_len(cell.text or ""))
    # Seri No: en az 14 karakterlik tek satır sığsın
    maxima[3] = max(maxima[3], 14)
    # Mülkiyet
    maxima[5] = max(maxima[5], len("OFİSBİR"), len("Müşteri"))
    return maxima


def apply_ek2_table_layout(table) -> list[int]:
    if len(table.columns) < 6:
        return []
    widths = compute_ek2_col_widths(_ek2_collect_max_chars(table))
    _set_table_grid_widths(table, widths)
    if table.rows:
        _set_header_row_repeat(table.rows[0])
    for row in table.rows:
        _set_row_cant_split(row)
    _apply_table_font_pt_safe(table, _EK2_FONT_PT)
    return widths


def apply_ek1_table_layout(table) -> list[int]:
    if len(table.columns) < 2:
        return []
    lab = 0
    val = 0
    for row in table.rows:
        if len(row.cells) < 2:
            continue
        lab = max(lab, _max_line_len(row.cells[0].text or ""))
        val = max(val, _max_line_len(row.cells[1].text or ""))
    widths = compute_ek1_col_widths(lab, val)
    _set_table_grid_widths(table, widths)
    if table.rows:
        _set_header_row_repeat(table.rows[0])
    for row in table.rows:
        _set_row_cant_split(row)
    _apply_table_font_pt_safe(table, _EK2_FONT_PT)
    return widths


def _classify_table(table) -> str:
    """ek1 | sig | ek2 | ek2_imza | other — şablon sırası bağımsız tanıma."""
    if _is_ek2_teslim_imza_table(table):
        return "ek2_imza"
    if len(table.columns) >= 6 and len(table.rows) >= 1:
        h0 = _tr_fold(table.rows[0].cells[0].text or "")
        if h0.startswith("sira") or "esya" in _tr_fold(
            table.rows[0].cells[1].text if len(table.rows[0].cells) > 1 else ""
        ):
            return "ek2"
    if len(table.columns) >= 4 and len(table.rows) >= 2:
        blob = _tr_fold(" ".join((c.text or "") for c in table.rows[0].cells))
        if "ofisbir" in blob or "muteselsil" in blob or "musteri" in blob:
            # imza başlık satırı
            lab = _tr_fold(table.rows[1].cells[0].text or "") if len(table.rows) > 1 else ""
            if "unvan" in lab or "ad soyad" in lab:
                return "sig"
            if "ofisbir" in blob and "muteselsil" in blob:
                return "sig"
    if len(table.columns) >= 2 and len(table.rows) >= 1:
        h0 = _tr_fold(table.rows[0].cells[0].text or "")
        if h0 == "alan" or "musteri unvani" in h0:
            return "ek1"
    return "other"


def ensure_ek2_page_break_before(doc) -> bool:
    """Ek-2 başlığına pageBreakBefore; Ek-3'e dokunma."""
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if t.startswith("Ek-2:") or t.startswith("Ek–2:"):
            _set_page_break_before(p, True)
            return True
    return False


def finalize_sozlesme_layout(doc) -> dict:
    """Boş son paragraf + Ek-1/imza/Ek-2 düzeni + Ek-2 yeni sayfa."""
    removed = strip_trailing_empty_paragraphs(doc)
    ek1_w: list[int] = []
    ek2_w: list[int] = []
    sig_w: list[int] = []
    by_kind = {_classify_table(t): t for t in doc.tables}
    # birden fazla same-kind olursa son kazanır; sırayla uygula
    for t in doc.tables:
        kind = _classify_table(t)
        if kind == "ek1":
            ek1_w = apply_ek1_table_layout(t)
            # Ek-1 son satır keepNext — imza tablosuyla bir arada tutmaya çalış
            try:
                for cell in t.rows[-1].cells:
                    for p in cell.paragraphs:
                        _set_para_keep_next(p, True)
            except Exception:
                pass
        elif kind == "sig":
            sig_w = apply_sig_table_layout(t)
        elif kind == "ek2":
            ek2_w = apply_ek2_table_layout(t)
    ek2_pb = ensure_ek2_page_break_before(doc)
    ek2_imza_w = ensure_ek2_teslim_imza_layout(doc)
    removed += strip_trailing_empty_paragraphs(doc)
    return {
        "trailing_empty_removed": removed,
        "ek1_widths": ek1_w,
        "ek2_widths": ek2_w,
        "sig_widths": sig_w,
        "ek2_imza_widths": ek2_imza_w,
        "ek2_width_sum": sum(ek2_w) if ek2_w else 0,
        "ek1_width_sum": sum(ek1_w) if ek1_w else 0,
        "sig_width_sum": sum(sig_w) if sig_w else 0,
        "ek2_imza_width_sum": sum(ek2_imza_w) if ek2_imza_w else 0,
        "sig_imza_base_twips": _SIG_IMZA_BASE_TWIPS,
        "sig_imza_target_twips": _SIG_IMZA_TARGET_TWIPS,
        "ek2_page_break": ek2_pb,
        "usable_twips": _PAGE_USABLE_TWIPS,
    }


def docx_bytes_to_preview_html(data: bytes) -> str:
    """DOCX → HTML önizleme (mammoth). Disk/DB yazmaz.

    Ek-2 OFİSBİR|MÜŞTERİ satırı: dolgusuz + aynı hiza (Docs başlık CSS'i temizlenir).
    """
    import mammoth
    import re as _re

    result = mammoth.convert_to_html(BytesIO(data))
    html = result.value or ""

    def _scrub_cell(m: _re.Match) -> str:
        chunk = m.group(0)
        up = chunk.upper()
        if "OFİSBİR" not in chunk and "OFISBIR" not in up and "MÜŞTERİ" not in chunk and "MUSTERI" not in up:
            return chunk
        chunk = _re.sub(
            r"background(-color)?\s*:\s*[^;\"']+;?",
            "background:transparent;",
            chunk,
            flags=_re.I,
        )
        chunk = _re.sub(
            r"color\s*:\s*#(fff|ffffff|f2f2f2)\b;?",
            "color:#000000;",
            chunk,
            flags=_re.I,
        )
        chunk = _re.sub(r"font-weight\s*:\s*bold;?", "font-weight:normal;", chunk, flags=_re.I)
        if "vertical-align" not in chunk.casefold():
            if 'style="' in chunk:
                chunk = chunk.replace('style="', 'style="vertical-align:top;', 1)
            else:
                chunk = chunk.replace("<td", '<td style="vertical-align:top"', 1)
        return chunk

    return _re.sub(r"<td\b[^>]*>[\s\S]*?</td>", _scrub_cell, html, flags=_re.I)


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
    _fill_ek2_dipnot(doc, ctx)

    for table in doc.tables:
        # Yeni şablon: Ek-1 → imza → Ek-2 (tanıma ile; indeks bağımsız)
        kind = _classify_table(table)
        if kind == "ek1":
            _fill_table_ek1(table, ctx)
        elif kind == "sig":
            _fill_signature_table(table, ctx)
        elif kind == "ek2":
            _fill_table_ek2(table, ctx)
        elif kind == "ek2_imza":
            # metin şablonda sabit; düzen finalize_sozlesme_layout'ta
            continue
        else:
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        if p.text and "[" in p.text:
                            nt = _fill_paragraph_text(p.text, ctx)
                            if nt != p.text:
                                _set_run_text_preserve(p, nt)

    finalize_sozlesme_layout(doc)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def save_sozlesme_docx_v2(ctx: dict, filepath: str | Path) -> Path:
    data = render_sozlesme_docx_v2(ctx)
    out = Path(filepath)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out
