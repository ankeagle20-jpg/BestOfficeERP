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

    bas = sozlesme_tarihi or k.get("sozlesme_tarihi") or m.get("rent_start_date")
    if not bas:
        bas = date.today()
    # bitiş: KYC sozlesme_bitis varsa onu kullan
    bit_raw = k.get("sozlesme_bitis")
    bas_s = _fmt_date(bas)
    bit_s = _fmt_date(bit_raw) if bit_raw else ""
    if not bit_s:
        try:
            if hasattr(bas, "year") and hasattr(bas, "month"):
                bit_s = _fmt_date(date(int(bas.year) + 1, int(bas.month), int(getattr(bas, "day", 1) or 1)))
            else:
                d0 = datetime.strptime(str(bas)[:10], "%Y-%m-%d").date()
                bit_s = _fmt_date(date(d0.year + 1, d0.month, d0.day))
        except Exception:
            bit_s = ""

    ht = _s(hizmet_turu) or _s(k.get("hizmet_turu")) or "Sanal Ofis"

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
        "aylik_tl": _tl_fmt(aylik_f) or BLANK,
        "yillik_tl": _tl_fmt(yillik_f) or BLANK,
        "aylik_kdv_dahil": _tl_fmt(aylik_f * 1.20) if aylik_f > 0 else BLANK,
        "yillik_kdv_dahil": _tl_fmt(yillik_f * 1.20) if yillik_f > 0 else BLANK,
        "vergi_dairesi": vergi_dairesi or BLANK,
        "nusha": "2",
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

    # Avukat / uyarı notları olduğu gibi
    # Sıralı anlamlı doldurmalar
    if "Hizmet Sağlayıcı:" in t or "OFİSBİR OFİS VE DANIŞMANLIK" in t:
        t = _replace_first(t, "Adres: [ ]", f"Adres: {ctx['firma_adres']}")
        t = _replace_first(t, "Vergi Dairesi / Vergi No: [ ]", f"Vergi Dairesi / Vergi No: {ctx['firma_vd_vn']}")
        t = _replace_first(t, "MERSİS No: [ ]", f"MERSİS No: {ctx['firma_mersis']}")

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


def _fill_table_ek1(table, ctx: dict) -> None:
    """Ek-1 (2 sütun Alan/Değer) satırlarını doldur."""
    if len(table.columns) < 2:
        return
    mapping = {
        "müşteri ünvanı": f"{ctx['musteri_unvan']} / {ctx['musteri_tc_vergi']}",
        "sözleşme başlangıç": f"{ctx['baslangic']} – {ctx['bitis']}",
        "seçilen hizmetler": ctx["hizmet_turu"],
        "aylık bedel": f"{ctx['aylik_tl']} TL + KDV" if ctx["aylik_tl"] != BLANK else BLANK,
        "aylık toplam": f"{ctx['aylik_kdv_dahil']} TL" if ctx["aylik_kdv_dahil"] != BLANK else BLANK,
        "yıllık dönem": f"{ctx['yillik_kdv_dahil']} TL" if ctx["yillik_kdv_dahil"] != BLANK else BLANK,
        "kep ve e-posta": ctx["musteri_email"],
    }
    for row in table.rows:
        cells = row.cells
        if len(cells) < 2:
            continue
        label = (cells[0].text or "").strip().lower()
        if not label or label == "alan":
            continue
        val_cell = cells[1]
        cur = (val_cell.text or "").strip()
        # Ödeme günü / faiz gibi zaten [ ] içeren satırlar: boş bırak veya BLANK
        for key, val in mapping.items():
            if key in label:
                # hücre metnini yaz
                for p in val_cell.paragraphs:
                    _set_run_text_preserve(p, val)
                    break
                else:
                    val_cell.text = val
                break
        else:
            # [ ] içeren değer hücrelerini boş çizgiye çevir
            if "[ ]" in cur or "[% ]" in cur:
                new_cur = cur.replace("[ ]", BLANK)
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
