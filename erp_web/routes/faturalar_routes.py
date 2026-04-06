from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, send_file, Response, abort
from flask_login import login_required, current_user
from functools import wraps
from datetime import datetime, date, timedelta
from db import (
    fetch_all,
    fetch_one,
    execute,
    execute_returning,
    ensure_faturalar_amount_columns,
    ensure_auto_invoice_tables,
    ensure_customers_durum,
    ensure_customers_is_active,
    ensure_duzenli_fatura_secenekleri_table,
    ensure_musteri_kyc_columns,
    ensure_musteri_kyc_hazir_ofis_oda_no,
    ensure_musteri_kyc_latest_lookup_index,
    ensure_user_ui_preferences_table,
    ensure_customers_rent_columns,
    ensure_customers_hazir_ofis_oda,
)
from utils.text_utils import turkish_lower
from utils.musteri_arama import customers_arama_sql_3_plus_tax, customers_arama_params_5
import os
import io
import re
import json
import uuid
from urllib.parse import urlencode
import logging
import math
from reportlab.lib.pagesizes import A5, A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


def _fatura_pdf_debug():
    return os.environ.get("FATURA_PDF_DEBUG", "").lower() in ("1", "true", "yes")


def _register_arial():
    """Türkçe karakter için Arial fontlarını kaydet (fatura/makbuz PDF)."""
    if getattr(_register_arial, "_done", False):
        return
    win = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or "C:\\Windows"
    fonts_dir = os.path.join(win, "Fonts")
    for f in ("arial.ttf", "Arial.ttf", "ARIAL.TTF"):
        p = os.path.join(fonts_dir, f)
        if os.path.isfile(p):
            try:
                pdfmetrics.registerFont(TTFont("Arial", p))
                break
            except Exception:
                pass
    for f in ("arialbd.ttf", "Arial Bold.ttf"):
        p = os.path.join(fonts_dir, f)
        if os.path.isfile(p):
            try:
                pdfmetrics.registerFont(TTFont("Arial-Bold", p))
                break
            except Exception:
                pass
    if "Arial" not in pdfmetrics.getRegisteredFontNames():
        for path in ["/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
            if os.path.isfile(path):
                try:
                    pdfmetrics.registerFont(TTFont("Arial", path))
                    break
                except Exception:
                    pass
    _register_arial._done = True


def _resolve_ettn_for_pdf(raw_ettn, fatura_id=None):
    """
    ETTN: Sadece geçerli UUID string'i olduğu gibi (büyük harf) kullanılır.
    Aksi halde (boş, Önizleme-ETTN, yazım hatası, ASCII O vb.) sentetik UUID — metin eşleştirmeye güvenilmez.
    Kayıtlı fatura (id var): uuid5 → aynı faturada PDF her seferinde aynı; önizlemede (id yok): uuid4.
    """
    v = (raw_ettn or "").strip()
    if v:
        try:
            cleaned = v.replace("{", "").replace("}", "").strip()
            return str(uuid.UUID(cleaned)).upper()
        except (ValueError, TypeError, AttributeError):
            pass
    if fatura_id is not None:
        try:
            return str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"bestoffice-erp:fatura:{int(fatura_id)}")
            ).upper()
        except (TypeError, ValueError):
            pass
    return str(uuid.uuid4()).upper()


def _normalize_vergi_dairesi(vd):
    """Vergi dairesi adını sadeleştir: 'KAVAKLIDERE VERGİ DAİRESİ MÜD.' -> 'KAVAKLIDERE'."""
    s = str(vd or "").strip()
    if not s:
        return "—"
    # En güvenlisi: "VERGI" kökünden sonrasını at (Unicode farklılıklarına dayanıklı).
    su = s.upper().replace("İ", "I")
    idx = su.find("VERG")
    if idx > 0:
        s = s[:idx].strip()
    # Kalan olası resmi ekleri temizle.
    s = re.sub(r"\bDA[İI]RES[İI]\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bM[ÜU]D(?:[ÜU]RL[ÜU][ĞG][ÜU]|\.?)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bBA[ŞS]KANLI[ĞG][İI]\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,.-")
    return s or "—"


def _resolve_gib_logo_path():
    """GİB logosu: cwd + __file__ tabanlı adaylar; sonuç işlem ömrü boyunca önbelleklenir."""
    if getattr(_resolve_gib_logo_path, "_done", False):
        return getattr(_resolve_gib_logo_path, "_path", None)
    dbg = _fatura_pdf_debug()
    forced = os.path.join(os.getcwd(), "BestOfficeERP", "assets", "gib_logo.png")
    if dbg:
        print(
            f"[fatura PDF] GIB zorunlu yol: {forced} "
            f"exists={os.path.exists(forced)} isfile={os.path.isfile(forced)}"
        )
    if os.path.isfile(forced):
        _resolve_gib_logo_path._done = True
        _resolve_gib_logo_path._path = forced
        return forced
    cwd = os.getcwd()
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(cwd, "BestOfficeERP", "assets", "gib_logo.png"),
        os.path.join(cwd, "assets", "gib_logo.png"),
        os.path.abspath(os.path.join(here, "..", "..", "assets", "gib_logo.png")),
        os.path.abspath(os.path.join(here, "..", "static", "gib_logo.png")),
    ]
    for name in ("gib_logo.PNG", "gib_logo.jpg", "gib_logo.jpeg"):
        candidates.append(os.path.join(cwd, "BestOfficeERP", "assets", name))
        candidates.append(os.path.abspath(os.path.join(here, "..", "..", "assets", name)))
    seen = set()
    result = None
    for p in candidates:
        if not p or p in seen:
            continue
        seen.add(p)
        ex = os.path.isfile(p)
        if dbg:
            print(f"[fatura PDF] GIB logo yolu: {p} exists={ex}")
        if ex:
            result = p
            break
    if result is None and dbg:
        print("[fatura PDF] GIB logo bulunamadı (tüm adaylar denendi).")
    _resolve_gib_logo_path._done = True
    _resolve_gib_logo_path._path = result
    return result


# GİB logosu: en-boy + tek ImageReader örneği (her PDF’de dosyayı yeniden açmayı önler)
_gib_logo_aspect_cache = {}
_gib_logo_reader_cache = {}


def _gib_logo_reader(gib_path):
    if not gib_path:
        return None
    r = _gib_logo_reader_cache.get(gib_path)
    if r is not None:
        return r
    try:
        from reportlab.lib.utils import ImageReader

        r = ImageReader(gib_path)
        _gib_logo_reader_cache[gib_path] = r
        return r
    except Exception:
        return None


def _gib_logo_aspect_ratio(gib_path):
    if not gib_path:
        return 1.0
    cached = _gib_logo_aspect_cache.get(gib_path)
    if cached is not None:
        return cached
    reader = _gib_logo_reader(gib_path)
    if reader is None:
        ar = 1.0
    else:
        try:
            iw, ih = reader.getSize()
            ar = iw / float(ih or 1)
        except Exception:
            ar = 1.0
    _gib_logo_aspect_cache[gib_path] = ar
    return ar


def _gib_dogrulama_qr_url(fatura, sender_vkn, alici_vkn, ettn):
    """
    e-Arşiv / e-Fatura önizleme için GİB portal tabanlı doğrulama URL'si (QR içeriği).
    Kamuya açık sorgu ekranı: earsivportal.efatura.gov.tr
    """
    def _dig(x):
        return re.sub(r"\D", "", str(x or ""))

    try:
        toplam = float(fatura.get("genel_toplam") or fatura.get("toplam") or 0)
    except (TypeError, ValueError):
        toplam = 0.0
    try:
        kdv = float(fatura.get("kdv_toplam") or fatura.get("kdv_tutar") or 0)
    except (TypeError, ValueError):
        kdv = 0.0
    tarih = str(fatura.get("fatura_tarihi") or "")[:10]
    q = {
        "ettn": str(ettn or "").strip(),
        "vknoGonderen": _dig(sender_vkn),
        "vknoAlici": _dig(alici_vkn),
        "faturaNo": str(fatura.get("fatura_no") or "").strip(),
        "tarih": tarih,
        "tutar": f"{toplam:.2f}",
        "kdvToplam": f"{kdv:.2f}",
    }
    base = "https://earsivportal.efatura.gov.tr/earsiv-fatura-sorgula"
    url = f"{base}?{urlencode(q)}"
    if len(url) > 450:
        url = f"{base}?{urlencode({'ettn': q['ettn'], 'vknoGonderen': q['vknoGonderen'], 'vknoAlici': q['vknoAlici'], 'tarih': tarih})}"
    return url


bp = Blueprint('faturalar', __name__, url_prefix='/faturalar')

# Tahsilat makbuzu firma bilgileri (.env'den FIRMA_VERGI_DAIRESI, FIRMA_VERGI_NO eklenebilir)
FIRMA_UNVAN = os.environ.get("FIRMA_UNVAN", "OFİSBİR OFİS VE DANIŞMANLIK HİZMETLERİ ANONİM ŞİRKETİ")
FIRMA_ADRES = os.environ.get("FIRMA_ADRES", "KAVAKLIDERE MAH. ESAT CADDESİ NO:12 KAPI NO:1 ÇANKAYA / Ankara / Türkiye")
FIRMA_TELEFON = os.environ.get("FIRMA_TELEFON", "+90 549 590 79 10")
FIRMA_WEB = os.environ.get("FIRMA_WEB", "www.ofisbir.com.tr")
FIRMA_VERGI_DAIRESI = os.environ.get("FIRMA_VERGI_DAIRESI", "Kavaklıdere")
FIRMA_VERGI_NO = os.environ.get("FIRMA_VERGI_NO", "6340871926")
UPLOAD_MUSTERI_DOSYALARI = "uploads/musteri_dosyalari"

AYLAR = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 
         'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']


def tutar_yaziya(tutar):
    """Tutarı Türkçe yazıya çevirir (örn: 1200 -> 'Bin İki Yüz Türk Lirası'; 1000-1999 arası 'Bir Bin' değil 'Bin')."""
    if tutar is None:
        return "Sıfır Türk Lirası"
    try:
        t = round(float(tutar), 2)
    except (TypeError, ValueError):
        return str(tutar) + " Türk Lirası"
    if t == 0:
        return "Sıfır Türk Lirası"

    birler = ["", "Bir", "İki", "Üç", "Dört", "Beş", "Altı", "Yedi", "Sekiz", "Dokuz"]
    onlar = ["", "On", "Yirmi", "Otuz", "Kırk", "Elli", "Altmış", "Yetmiş", "Seksen", "Doksan"]
    yuzler = ["", "Yüz", "İki Yüz", "Üç Yüz", "Dört Yüz", "Beş Yüz", "Altı Yüz", "Yedi Yüz", "Sekiz Yüz", "Dokuz Yüz"]
    scale_names = ["", "Bin", "Milyon", "Milyar", "Trilyon"]

    def three_digits(d):
        if d <= 0:
            return ""
        y, o, b = d // 100, (d // 10) % 10, d % 10
        chunks = []
        if y:
            chunks.append(yuzler[y])
        if o:
            chunks.append(onlar[o])
        if b:
            chunks.append(birler[b])
        return " ".join(chunks).strip()

    k = int(t)
    kuruş = int(round((t - k) * 100))
    if kuruş >= 100:
        k += 1
        kuruş = 0

    if k == 0:
        yazi = "Sıfır"
    else:
        gruplar = []
        kk = k
        while kk > 0:
            gruplar.append(kk % 1000)
            kk //= 1000
        parts_out = []
        for i in range(len(gruplar) - 1, -1, -1):
            d = gruplar[i]
            if d == 0:
                continue
            word = three_digits(d)
            if i == 0:
                parts_out.append(word)
            else:
                scale = scale_names[i] if i < len(scale_names) else "?"
                if i == 1 and d == 1:
                    parts_out.append("Bin")
                elif d == 1:
                    parts_out.append("Bir " + scale)
                else:
                    parts_out.append(word + " " + scale)
        yazi = " ".join(parts_out).replace("  ", " ").strip()

    yazi = yazi or "Sıfır"
    return yazi + " Türk Lirası" + (f" {kuruş:02}/100" if kuruş else "")


def _turkish_upper(s):
    """Türkçe büyük harf: i->İ, ı->I sonra upper."""
    return s.replace("i", "İ").replace("ı", "I").upper()


def tutar_yaziya_gib(tutar):
    """GİB e-Arşiv formatı: YALNIZ:#BİRYÜZ...TÜRKLİRASI80/100# (büyük harf, bitişik)."""
    if tutar is None:
        return "YALNIZ:#SIFIRTÜRKLİRASIDIR#"
    try:
        t = float(tutar)
    except (TypeError, ValueError):
        return "YALNIZ:#" + str(tutar) + "TÜRKLİRASIDIR#"
    s = tutar_yaziya(tutar)
    if not s or s.strip() == "":
        return "YALNIZ:#SIFIRTÜRKLİRASIDIR#"
    s = s.strip()
    kuruş_suffix = ""
    if " " in s and "/" in s:
        parts = s.rsplit(" ", 1)
        if len(parts) == 2 and "/" in parts[1]:
            kuruş_suffix = parts[1]
            s = parts[0]
    if s.endswith(" Türk Lirası"):
        s = s[:-len(" Türk Lirası")].strip()
    main = _turkish_upper(s).replace(" ", "")
    if kuruş_suffix:
        return "YALNIZ:#" + main + "TÜRKLİRASI" + kuruş_suffix + "#"
    return "YALNIZ:#" + main + "TÜRKLİRASIDIR#"


def get_next_makbuz_no(yil):
    """Yıla göre bir sonraki makbuz numarasını döner (örn: 2026 -> 2026-0001)."""
    row = fetch_one(
        "SELECT makbuz_no FROM tahsilatlar WHERE makbuz_no LIKE %s ORDER BY id DESC LIMIT 1",
        (f"{yil}-%",)
    )
    if not row or not row.get("makbuz_no"):
        return f"{yil}-0001"
    try:
        num = int(row["makbuz_no"].split("-")[-1])
        return f"{yil}-{num + 1:04d}"
    except (ValueError, IndexError):
        return f"{yil}-0001"


def _tarih_saat_str(tarih_val, created_at=None):
    """tahsilat_tarihi ve created_at'ten '27.02.2026 Saat: 14:35' formatı."""
    tarih_str = "—"
    saat_str = ""
    if created_at:
        if hasattr(created_at, "strftime"):
            saat_str = created_at.strftime("%H:%M")
        else:
            try:
                dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                saat_str = dt.strftime("%H:%M")
            except Exception:
                pass
    if tarih_val:
        if hasattr(tarih_val, "strftime"):
            tarih_str = tarih_val.strftime("%d.%m.%Y")
        else:
            try:
                s = str(tarih_val)[:10]
                if len(s) == 10 and s[4] == "-":
                    y, m, d = s.split("-")
                    tarih_str = f"{d}.{m}.{y}"
                else:
                    tarih_str = s
            except Exception:
                tarih_str = str(tarih_val)[:10]
    if saat_str:
        return f"{tarih_str} Saat: {saat_str}"
    return tarih_str


# A5: 148mm x 210mm (ReportLab A5 = 420x595 pt)
A5_W_MM, A5_H_MM = 148, 210
MARGIN_MM, RIGHT_MM = 15, A5_W_MM - 15

# A4 ölçüleri (fatura için)
A4_W_MM, A4_H_MM = 210, 297


def _wrap_header(canvas, text, max_width_pt, font_name, font_size=9):
    """Başlık metnini sütun genişliğine göre satırlara böler (GİB tarzı okunaklı)."""
    if not text or max_width_pt <= 0:
        return [text or ""]
    canvas.setFont(font_name, font_size)
    words = text.replace("/", " / ").split()
    lines = []
    current = []
    current_w = 0
    for w in words:
        w_w = canvas.stringWidth(w + " ", font_name, font_size)
        if current and current_w + w_w > max_width_pt:
            lines.append(" ".join(current))
            current = [w]
            current_w = w_w
        else:
            current.append(w)
            current_w += w_w
    if current:
        lines.append(" ".join(current))
    return lines if lines else [text]


def _get_cek_list(tahsilat):
    """tahsilat içinden cek_detay'ı liste olarak döner."""
    raw = tahsilat.get("cek_detay")
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw) if isinstance(raw, str) else []
    except (json.JSONDecodeError, TypeError):
        return []


def build_makbuz_pdf(tahsilat, musteri_adi, fatura_no=None, banka_hesaplar=None):
    """Tahsilat makbuzu A5 PDF'ini oluşturur, bytes döner."""
    _register_arial()
    font_name = "Arial" if "Arial" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    font_bold = "Arial-Bold" if "Arial-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold"
    buf = io.BytesIO()
    w_pt, h_pt = A5  # 420, 595
    c = canvas.Canvas(buf, pagesize=A5)
    c.setTitle("Tahsilat Makbuzu")
    h = h_pt

    y = 15
    c.setFont(font_bold, 12)
    c.drawCentredString(w_pt / 2, h - y * mm, "TAHSİLAT MAKBUZU")
    y += 8
    c.setFont(font_name, 8)
    makbuz_no = tahsilat.get("makbuz_no") or f"{datetime.now().year}-????"
    tarih_saat = _tarih_saat_str(tahsilat.get("tahsilat_tarihi"), tahsilat.get("created_at"))
    c.drawCentredString(w_pt / 2, h - y * mm, f"Belge No: {makbuz_no}   Tarih: {tarih_saat}")
    y += 6
    c.line(MARGIN_MM * mm, h - y * mm, RIGHT_MM * mm, h - y * mm)
    y += 8

    c.setFont(font_name, 9)
    c.drawString(MARGIN_MM * mm, h - y * mm, "Tahsildar (Firma):")
    c.drawString(52 * mm, h - y * mm, (FIRMA_UNVAN or "")[:55])
    y += 5
    c.drawString(MARGIN_MM * mm, h - y * mm, "Vergi Dairesi:")
    c.drawString(52 * mm, h - y * mm, (FIRMA_VERGI_DAIRESI or "—")[:40])
    y += 5
    c.drawString(MARGIN_MM * mm, h - y * mm, "Vergi No:")
    c.drawString(52 * mm, h - y * mm, (FIRMA_VERGI_NO or "—")[:20])
    y += 8

    c.drawString(MARGIN_MM * mm, h - y * mm, "Ödeme Yapan (Müşteri):")
    c.drawString(52 * mm, h - y * mm, (musteri_adi or "—")[:50])
    y += 5
    tutar = float(tahsilat.get("tutar") or 0)
    tutar_fmt = f"{tutar:,.2f}".replace(",", " ").replace(".", ",") + " TL"
    c.drawString(MARGIN_MM * mm, h - y * mm, "Ödenen Tutar:")
    c.drawString(52 * mm, h - y * mm, tutar_fmt)
    y += 5
    c.drawString(MARGIN_MM * mm, h - y * mm, "Yazıyla:")
    c.setFont(font_name, 8)
    c.drawString(52 * mm, h - y * mm, (tutar_yaziya(tutar) or "")[:55])
    c.setFont(font_name, 9)
    y += 8

    odeme = (tahsilat.get("odeme_turu") or "nakit").lower()
    c.drawString(MARGIN_MM * mm, h - y * mm, "Ödeme Türü:")
    nakit = "[x]" if odeme == "nakit" else "[ ]"
    havale = "[x]" if odeme in ("havale", "eft", "banka") else "[ ]"
    kart = "[x]" if odeme in ("kredi_karti", "kart", "kredi kartı") else "[ ]"
    cek = "[x]" if odeme == "cek" else "[ ]"
    c.drawString(52 * mm, h - y * mm, f"{nakit} Nakit  {havale} Havale/EFT  {kart} K.Kartı  {cek} Çek")
    y += 5
    havale_banka = (tahsilat.get("havale_banka") or "").strip()
    if havale_banka and odeme in ("havale", "eft", "banka"):
        c.drawString(MARGIN_MM * mm, h - y * mm, "Havale yapılan banka/hesap:")
        c.drawString(52 * mm, h - y * mm, havale_banka[:55])
        y += 5
    y += 5

    cek_list = _get_cek_list(tahsilat)
    if odeme == "cek" and cek_list:
        c.setFont(font_bold, 8)
        c.drawString(MARGIN_MM * mm, h - y * mm, "Çek detayları")
        y += 5
        col_w = (RIGHT_MM - MARGIN_MM) / 7
        cols = ["SIRA", "ÇEK NO", "HESAP NO", "BANKA", "ŞUBE", "VADE", "TUTAR"]
        for i, col in enumerate(cols):
            c.drawString((MARGIN_MM + i * col_w) * mm, h - y * mm, col[:10])
        y += 4
        c.setFont(font_name, 7)
        for idx, row in enumerate(cek_list[:8], 1):
            cek_no = str(row.get("cek_no") or "")[:12]
            hesap = str(row.get("hesap_no") or "")[:10]
            banka = str(row.get("banka") or "")[:12]
            sube = str(row.get("sube") or "")[:8]
            vade = str(row.get("vade") or "")[:10]
            t = row.get("tutar")
            tutar_str = f"{float(t):,.2f}" if t is not None else ""
            c.drawString((MARGIN_MM + 0 * col_w) * mm, h - y * mm, str(idx))
            c.drawString((MARGIN_MM + 1 * col_w) * mm, h - y * mm, cek_no)
            c.drawString((MARGIN_MM + 2 * col_w) * mm, h - y * mm, hesap)
            c.drawString((MARGIN_MM + 3 * col_w) * mm, h - y * mm, banka)
            c.drawString((MARGIN_MM + 4 * col_w) * mm, h - y * mm, sube)
            c.drawString((MARGIN_MM + 5 * col_w) * mm, h - y * mm, vade)
            c.drawString((MARGIN_MM + 6 * col_w) * mm, h - y * mm, tutar_str)
            y += 4
        cek_toplam = sum(float(r.get("tutar") or 0) for r in cek_list)
        y += 2
        c.drawString((MARGIN_MM + 4 * col_w) * mm, h - y * mm, "GENEL TOPLAM:")
        c.drawString((MARGIN_MM + 6 * col_w) * mm, h - y * mm, f"{cek_toplam:,.2f} TL")
        y += 6
        c.setFont(font_name, 9)

    aciklama = (tahsilat.get("aciklama") or "").strip() or "—"
    c.drawString(MARGIN_MM * mm, h - y * mm, "Açıklama:")
    y += 4
    c.setFont(font_name, 8)
    for chunk in (aciklama[i:i+48] for i in range(0, len(aciklama), 48)):
        c.drawString(MARGIN_MM * mm, h - y * mm, chunk)
        y += 4
    if fatura_no:
        c.drawString(MARGIN_MM * mm, h - y * mm, f"İlgili Fatura: {fatura_no}")
        y += 4
    y += 4

    if banka_hesaplar:
        c.setFont(font_bold, 8)
        c.drawString(MARGIN_MM * mm, h - y * mm, "Ödeme yapılabilecek hesaplarımız (TL IBAN):")
        y += 4
        c.setFont(font_name, 7)
        for b in banka_hesaplar[:5]:
            ad = (b.get("banka_adi") or "") + " - " + (b.get("hesap_adi") or "")
            iban = (b.get("iban") or "")[:32]
            c.drawString(MARGIN_MM * mm, h - y * mm, (ad or "—")[:40])
            y += 3
            c.drawString(MARGIN_MM * mm, h - y * mm, iban or "—")
            y += 4
        y += 2
        c.setFont(font_name, 9)

    c.line(MARGIN_MM * mm, h - y * mm, RIGHT_MM * mm, h - y * mm)
    y += 6
    c.drawString(MARGIN_MM * mm, h - y * mm, "Düzenleyen (İmza/Kaşe):")
    y += 5
    c.drawString(MARGIN_MM * mm, h - y * mm, (FIRMA_UNVAN or "") + " Yetkilisi")
    y += 4
    c.line(MARGIN_MM * mm, h - y * mm, 55 * mm, h - y * mm)

    c.save()
    buf.seek(0)
    return buf.getvalue()


def _pdf_irsaliye_modu(fatura):
    """Belge irsaliye akışından mı? (API bool, notlar etiketi veya fatura_tipi yedekleri)."""
    if not isinstance(fatura, dict):
        return False
    v = fatura.get("irsaliye_modu")
    if v is True:
        return True
    if v is False or v is None:
        pass
    else:
        if str(v).strip().lower() in ("1", "true", "yes", "on"):
            return True
    notlar = fatura.get("notlar")
    if notlar and "IRSALIYE_MODU" in str(notlar):
        return True
    ft = turkish_lower(str(fatura.get("fatura_tipi") or "")).replace(" ", "").replace("-", "")
    if "irsaliye" in ft:
        return True
    return False


def build_fatura_pdf(fatura, musteri, satirlar, preview=False):
    """e-Arşiv tarzı A4 fatura / e-irsaliye PDF.

    preview=True: kayıt öncesi form önizlemesi — QR üretimi atlanır (daha hızlı).
    """
    _register_arial()
    font_name = "Arial" if "Arial" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    font_bold = "Arial-Bold" if "Arial-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold"
    buf = io.BytesIO()
    w_pt, h_pt = A4
    irsaliye_modu = _pdf_irsaliye_modu(fatura)
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("İrsaliye Önizleme" if irsaliye_modu else "Fatura Önizleme")

    # Kenarlar
    margin = 10 * mm
    right = (A4_W_MM - 10) * mm

    # Tarih: gönderici üst çift çizgisinin 3 mm üstü, sol hiza = şirket unvanı (left_x) — çizim sender_top sonrası
    now = datetime.now()
    ust_tarih = fatura.get("fatura_tarihi_str") or now.strftime("%d.%m.%Y %H:%M")

    # --- Kurumsal üst/alt bloklar (örnek görsel gibi çift çizgi) ---
    def _double_hr(y_pt, line_w=0.8, gap_pt=1.6):
        """Tam sayfa genişliğinde çift yatay çizgi."""
        c.setLineWidth(line_w)
        c.line(margin, y_pt, right, y_pt)
        c.line(margin, y_pt - gap_pt, right, y_pt - gap_pt)

    def _double_hr_segment(x1_pt, x2_pt, y_pt, line_w=0.8, gap_pt=1.6):
        """Belirli bir yatay aralıkta (ör: sadece şirket bloğu genişliğinde) çift çizgi."""
        c.setLineWidth(line_w)
        c.line(x1_pt, y_pt, x2_pt, y_pt)
        c.line(x1_pt, y_pt - gap_pt, x2_pt, y_pt - gap_pt)

    def _wrap_text(text, max_w_pt, font_nm, font_sz):
        """Kelime bazlı wrap + break-word."""
        t = (text or "").strip()
        if not t:
            return [""]
        c.setFont(font_nm, font_sz)
        words = t.split()
        if not words:
            return [t]
        out = []
        cur = ""
        for w in words:
            cand = (cur + " " + w).strip() if cur else w
            if c.stringWidth(cand, font_nm, font_sz) <= max_w_pt:
                cur = cand
                continue
            if cur:
                out.append(cur)
                cur = ""
            if c.stringWidth(w, font_nm, font_sz) <= max_w_pt:
                cur = w
            else:
                chunk = ""
                for ch in w:
                    cand2 = chunk + ch
                    if c.stringWidth(cand2, font_nm, font_sz) <= max_w_pt:
                        chunk = cand2
                    else:
                        if chunk:
                            out.append(chunk)
                        chunk = ch
                cur = chunk
        if cur:
            out.append(cur)
        return out or [t]

    def _sanitize_addr_text(text):
        """Adres bloğunda placeholder satırları tek satırda topla."""
        out = []
        placeholders = []
        for raw in str(text or "").split("\n"):
            ln = (raw or "").strip()
            if not ln:
                continue
            n = (
                ln.lower()
                .replace("ı", "i")
                .replace("İ", "i")
                .replace("ş", "s")
                .replace("ğ", "g")
                .replace("ü", "u")
                .replace("ö", "o")
                .replace("ç", "c")
                .replace(".", "")
                .replace(":", "")
                .strip()
            )
            # Değeri girilmemiş placeholder satırları biriktir: No:, Kapı No:, / Türkiye, Web Sitesi:
            if n in ("no", "kapi no", "/ turkiye", "turkiye", "web sitesi", "web sitesi /"):
                placeholders.append(ln)
                continue
            out.append(ln)
        if placeholders:
            out.append(" ".join(placeholders))
        return "\n".join(out)

    def _draw_labeled_line(x_pt, y_pt, label, value, label_w_pt, value_max_w_pt=None):
        c.setFont(font_name, 8)
        c.drawString(x_pt, y_pt, (label or "")[:30])
        if value_max_w_pt:
            v_lines = _wrap_text(value, value_max_w_pt, font_name, 8)
            c.drawString(x_pt + label_w_pt, y_pt, (v_lines[0] if v_lines else "")[:95])
        else:
            c.drawString(x_pt + label_w_pt, y_pt, (value or "")[:95])

    company = (fatura.get("current_user_company") or fatura.get("company") or {}) if isinstance(fatura, dict) else {}
    sender_unvan = (company.get("unvan") or FIRMA_UNVAN or "").strip()
    sender_adres = _sanitize_addr_text((company.get("adres") or FIRMA_ADRES or "").strip())
    sender_tel = (company.get("telefon") or FIRMA_TELEFON or "").strip() or "—"
    sender_web = (company.get("web") or FIRMA_WEB or "").strip() or "—"
    sender_vd = _normalize_vergi_dairesi(company.get("vergi_dairesi") or FIRMA_VERGI_DAIRESI or "")
    sender_vkn = (company.get("vkn") or FIRMA_VERGI_NO or "").strip() or "—"

    client = (fatura.get("active_invoice_client") or fatura.get("client") or {}) if isinstance(fatura, dict) else {}
    # mevcut musteri dict'i ile birleştir (varsa client öncelikli)
    must = {}
    must.update(musteri or {})
    must.update(client or {})
    alici_unvan = (must.get("unvan") or must.get("sirket_unvani") or must.get("name") or fatura.get("musteri_adi") or "Müşteri").strip()
    alici_adres = _sanitize_addr_text((must.get("yeni_adres") or must.get("address") or "").strip()) or "—"
    sevk_adresi_pdf = (
        str(fatura.get("sevk_adresi") or fatura.get("sevk_adres") or must.get("sevk_adresi") or "")
        .replace("\r\n", "\n")
        .strip()
    )
    alici_vd = _normalize_vergi_dairesi(must.get("vergi_dairesi") or "")
    alici_vkn = str(must.get("vergi_no") or must.get("tax_number") or "—").strip()
    pdf_ettn = _resolve_ettn_for_pdf(fatura.get("ettn"), fatura.get("id"))

    # Gönderici bloğu (sol şirket | gutter GİB logosu | sağ meta/QR)
    sender_top = h_pt - 14 * mm
    gutter = 10 * mm
    left_col_w = (right - margin) * 0.40
    right_col_w = (right - margin) - left_col_w - gutter
    left_x = margin
    right_x = margin + left_col_w + gutter
    gutter_pad = 1.5 * mm
    gutter_left = left_x + left_col_w + gutter_pad
    gutter_right = right_x - gutter_pad
    gutter_cx = (gutter_left + gutter_right) / 2.0

    alici_block_pad_x = 2 * mm
    alici_inner_right = left_x + left_col_w - alici_block_pad_x

    # Orta üst: GİB logosu + belge türü — koordinatlar burada; çizim kalemler öncesi (üst katman)
    gib_path = _resolve_gib_logo_path()
    _gib_forced = os.path.join(os.getcwd(), "BestOfficeERP", "assets", "gib_logo.png")
    if _fatura_pdf_debug():
        print(
            f"[fatura PDF] GIB zorunlu yol: {_gib_forced} "
            f"exists={os.path.exists(_gib_forced)} isfile={os.path.isfile(_gib_forced)}"
        )
    if os.path.isfile(_gib_forced):
        gib_path = _gib_forced
    if irsaliye_modu:
        doc_subtitle = "e-İRSALİYE"
    else:
        ft_low = turkish_lower(str(fatura.get("fatura_tipi") or ""))
        if "efatura" in ft_low.replace(" ", "") or ft_low in ("efatura", "e-fatura"):
            doc_subtitle = "e-FATURA"
        else:
            doc_subtitle = "e-Arşiv Fatura"
    doc_subtitle_pdf = doc_subtitle

    # GİB logosu: sender_bottom sonrası OFİSBİR üst çizgisi … VKN alt çift çizgi arası + sayfa ortası
    _dhr_gap_pt = 1.6
    # Çift çizgi altından sonraki blok üst çizgisine kadar boşluk (≈ eski sender_bottom - 18); SAYIN/SEVK aynı
    BLOCK_GAP_BELOW_DOUBLE_PT = 18 - _dhr_gap_pt
    BLOCK_GAP_MM = BLOCK_GAP_BELOW_DOUBLE_PT  # eski isim kaldıysa NameError önleme (pt cinsinden aynı değer)
    ar = _gib_logo_aspect_ratio(gib_path)
    GIB_LOGO_H_FIXED = 25 * mm
    GIB_LOGO_H_MAX = GIB_LOGO_H_FIXED  # geriye uyum (eski kod / kopyalar)
    GIB_LOGO_LEFT_FROM_PAGE = 85 * mm  # sayfa sol kenarından logo solu (statik)
    GIB_LOGO_SIDE_CLEAR = 10 * mm  # gönderici sağı ve QR solu ile minimum boşluk
    GIB_LOGO_GAP_AFTER_SENDER = 20 * mm  # eski sabit adı (yerel/karışık kopya NameError önleme)
    SUBTITLE_BELOW_LOGO_MM = 3 * mm
    logo_h = GIB_LOGO_H_FIXED
    logo_draw_w = GIB_LOGO_H_FIXED * ar
    logo_ll_x = 0.0
    logo_ll_y = 0.0
    sub_baseline = 0.0

    # QR kare boyutu (ETTN hizalı meta üstünde)
    qr_size = min(26 * mm, max(20 * mm, right_col_w * 0.88))
    qr_x = 0.0
    qr_y = 0.0

    qr_pil_image = None
    if not preview:
        try:
            import qrcode
            import qrcode.constants

            qr_data = _gib_dogrulama_qr_url(fatura, sender_vkn, alici_vkn, pdf_ettn)
            try:
                qr = qrcode.QRCode(
                    version=1,
                    box_size=3,
                    border=0,
                    error_correction=qrcode.constants.ERROR_CORRECT_M,
                )
                qr.add_data(qr_data)
                qr.make(fit=True)
                qr_pil_image = qr.make_image(fill_color="black", back_color="white")
                if _fatura_pdf_debug():
                    print(f"[fatura PDF] QR PIL (v1, M), veri uzunluğu={len(qr_data)}")
            except Exception as e_v1:
                if _fatura_pdf_debug():
                    print(f"[fatura PDF] QR v1 başarısız ({e_v1}), auto version.")
                qr = qrcode.QRCode(
                    version=None,
                    box_size=3,
                    border=0,
                    error_correction=qrcode.constants.ERROR_CORRECT_M,
                )
                qr.add_data(qr_data)
                qr.make(fit=True)
                qr_pil_image = qr.make_image(fill_color="black", back_color="white")
                if _fatura_pdf_debug():
                    print(f"[fatura PDF] QR PIL (auto, M), veri uzunluğu={len(qr_data)}")
        except Exception as e_qr:
            if _fatura_pdf_debug():
                print(f"[fatura PDF] QR üretilemedi: {e_qr}")
            qr_pil_image = None

    # Tarih: üst çift çizginin 3 mm üstü, unvan ile aynı sol hiza (overlay’de tekrar basılır)
    date_header_y = sender_top + 3 * mm
    c.setFont(font_name, 8)
    c.drawString(left_x, date_header_y, ust_tarih)

    _double_hr_segment(left_x, left_x + left_col_w, sender_top)
    sender_y = sender_top - 12

    # Sol sütun: şirket bilgileri (wrap'li)
    c.setFont(font_bold, 9.5)
    for ln in _wrap_text(sender_unvan, left_col_w, font_bold, 9.5)[:2]:
        c.drawString(left_x, sender_y, ln[:110])
        sender_y -= 12
    c.setFont(font_name, 8)
    addr_lines = []
    for raw_ln in sender_adres.split("\\n"):
        raw_ln = (raw_ln or "").strip()
        if not raw_ln:
            continue
        addr_lines.extend(_wrap_text(raw_ln, left_col_w, font_name, 8))
    for ln in addr_lines[:3]:
        c.drawString(left_x, sender_y, ln[:120])
        sender_y -= 11

    label_w = 28 * mm
    value_w = max(20, left_col_w - label_w)
    _draw_labeled_line(left_x, sender_y, "Tel:", sender_tel, label_w, value_max_w_pt=value_w)
    sender_y -= 11
    _draw_labeled_line(left_x, sender_y, "Web Sitesi:", sender_web, label_w, value_max_w_pt=value_w)
    sender_y -= 11
    _draw_labeled_line(left_x, sender_y, "Vergi Dairesi:", sender_vd, label_w, value_max_w_pt=value_w)
    sender_y -= 11
    # VKN satırı için y konumunu ayrı değişkende tut (çizgiyi buna yapıştıracağız)
    vkn_y = sender_y
    _draw_labeled_line(left_x, vkn_y, "VKN:", sender_vkn, label_w, value_max_w_pt=value_w)

    # Sağ meta tablo: start_y sonrası çizilir; logo/QR üst katman _flush_gib_overlay ile
    try:
        from datetime import datetime as _dt
        ft_raw = fatura.get("fatura_tarihi") or now.strftime("%Y-%m-%d")
        try:
            dt_ft = _dt.strptime(str(ft_raw)[:10], "%Y-%m-%d")
        except Exception:
            dt_ft = now
        irs_tarih = fatura.get("irsaliye_tarihi") or dt_ft.strftime("%d-%m-%Y")
        irs_saat = fatura.get("irsaliye_saati") or now.strftime("%H:%M:%S")
        sevk_tarih = fatura.get("sevk_tarihi") or irs_tarih
        sevk_saat = fatura.get("sevk_saati") or irs_saat
        yil = dt_ft.year
        import re as _re
        num_raw = "".join(_re.findall(r"\d", str(fatura.get("fatura_no") or ""))) or "1"
        num_val = int(num_raw[-9:]) if num_raw else 1
        irs_no = f"GRS{yil}{num_val:09d}"
    except Exception:
        dt_ft = now
        irs_tarih = now.strftime("%d-%m-%Y")
        irs_saat = now.strftime("%H:%M:%S")
        sevk_tarih = irs_tarih
        sevk_saat = irs_saat
        irs_no = "GRS" + now.strftime("%Y") + "000000001"

    fat_tarih_dm = dt_ft.strftime("%d-%m-%Y")
    fat_saat = (fatura.get("fatura_saati") or fatura.get("irsaliye_saati") or now.strftime("%H:%M:%S"))
    fat_tip_disp = (str(fatura.get("fatura_tipi") or "SATIŞ")).strip().upper() or "SATIŞ"
    fat_no_goster = (str(fatura.get("fatura_no") or "").strip() or irs_no)[:32]

    if irsaliye_modu:
        meta_lines = [
            ("Özelleştirme No", "TR1.2.1"),
            ("Senaryo", "TEMELIRSALIYE"),
            ("İrsaliye Tipi", (fatura.get("irsaliye_tipi") or "SEVK")),
            ("İrsaliye No", irs_no),
            ("İrsaliye Tarihi", irs_tarih),
            ("İrsaliye Zamanı", irs_saat),
            ("Sevk Tarihi", sevk_tarih),
            ("Sevk Zamanı", sevk_saat),
        ]
    else:
        meta_lines = [
            ("Özelleştirme No", "TR1.2"),
            ("Senaryo", "EARSIVFATURA"),
            ("Fatura Tipi", fat_tip_disp[:24]),
            ("Fatura No", fat_no_goster),
            ("Fatura Tarihi", fat_tarih_dm),
            ("Fatura Zamanı", str(fat_saat)[:16]),
        ]

    # Gönderici bloğu alt çizgisi: sadece sol sütunda; meta kutu sağda olduğu için
    # kutu altı (box_top - box_height) ile sınırlamak VKN ile çizgi arasında büyük boşluk yaratıyordu.
    # Üst çift çizginin ilk çizgisi VKN baseline'ının ~2 pt (~0,7 mm) altında.
    gap_below_vkn_pt = 2
    sender_bottom = vkn_y - gap_below_vkn_pt
    _double_hr_segment(left_x, left_x + left_col_w, sender_bottom)

    # GİB: yükseklik 25 mm; dikey orta = üst çift (sender_top) … alt çift alt çizgisi (sender_bottom - gap)
    band_high_y = sender_top
    band_low_y = sender_bottom - _dhr_gap_pt
    logo_cy = (band_high_y + band_low_y) / 2.0
    logo_w_nat = logo_h * ar
    max_logo_w = (right - margin) * 0.46
    logo_draw_w = min(logo_w_nat, max_logo_w)

    # Alıcı bloğu (header ile aynı genişlikte, sol sınırlı)
    # Boşluk: BLOCK_GAP_BELOW_DOUBLE_PT (GİB bölümünde tanımlı)
    SAYIN_TOP_PAD_BELOW_DOUBLE_PT = 14
    sender_hr_lower_y = sender_bottom - _dhr_gap_pt
    y_pt = sender_hr_lower_y - BLOCK_GAP_BELOW_DOUBLE_PT
    y_sayin_hr_top = y_pt
    _double_hr_segment(left_x, left_x + left_col_w, y_pt)
    y_pt -= SAYIN_TOP_PAD_BELOW_DOUBLE_PT
    c.setFont(font_bold, 9.5)
    c.drawString(margin, y_pt, "SAYIN")
    y_pt -= 12
    c.setFont(font_bold, 8.5)
    c.drawString(margin, y_pt, alici_unvan[:95])
    y_pt -= 12
    c.setFont(font_name, 8)
    for ln in str(alici_adres).split("\\n"):
        if not ln.strip():
            continue
        c.drawString(margin, y_pt, ln.strip()[:105])
        y_pt -= 11
    _draw_labeled_line(margin, y_pt, "Vergi Dairesi:", alici_vd, label_w)
    y_pt -= 11
    _draw_labeled_line(margin, y_pt, "VKN/TCKN:", alici_vkn, label_w)
    y_pt -= 10
    sayin_bottom_hr_top = y_pt
    sayin_inner_top_y = y_sayin_hr_top - _dhr_gap_pt
    sayin_inner_height_pt = sayin_inner_top_y - sayin_bottom_hr_top
    _double_hr_segment(left_x, left_x + left_col_w, y_pt)

    # Sevk adresi (alıcı adresinden farklı; boşsa blok çizilmez)
    # Üst boşluk: gönderici–SAYIN ile aynı (BLOCK_GAP_BELOW_DOUBLE_PT)
    ettn_anchor_hr_top = sayin_bottom_hr_top
    if sevk_adresi_pdf:
        sevk_val_x = margin
        wrap_max = max(40, alici_inner_right - sevk_val_x)
        sevk_lines = []
        for raw_ln in sevk_adresi_pdf.split("\n"):
            raw_ln = (raw_ln or "").strip()
            if not raw_ln:
                continue
            sevk_lines.extend(_wrap_text(raw_ln, wrap_max, font_name, 8))
        if sevk_lines:
            sayin_hr_lower_y = sayin_bottom_hr_top - _dhr_gap_pt
            y_sevk_open_top = sayin_hr_lower_y - BLOCK_GAP_BELOW_DOUBLE_PT
            _double_hr_segment(left_x, left_x + left_col_w, y_sevk_open_top)
            y_sevk_title = y_sevk_open_top - SAYIN_TOP_PAD_BELOW_DOUBLE_PT
            c.setFont(font_bold, 8.5)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(left_x, y_sevk_title, "SEVK ADRESİ:")
            last_txt_baseline = y_sevk_title
            c.setFont(font_name, 8)
            y_sevk_line = y_sevk_title - 12
            for ln in sevk_lines:
                c.drawString(sevk_val_x, y_sevk_line, (ln or "")[:220])
                last_txt_baseline = y_sevk_line
                y_sevk_line -= 11
            top_sevk_inner_y = y_sevk_open_top - _dhr_gap_pt
            y_close_for_min_height = top_sevk_inner_y - sayin_inner_height_pt
            y_close_after_text = last_txt_baseline - 5 * mm
            y_sevk_close_top = min(y_close_after_text, y_close_for_min_height)
            _double_hr_segment(left_x, left_x + left_col_w, y_sevk_close_top)
            ettn_anchor_hr_top = y_sevk_close_top

    # ETTN satırı (kurumsal tasarım: alıcı / sevk bloğu alt çizgisinin hemen altında; QR ile aynı kod)
    ettn = pdf_ettn
    ettn_baseline_y = ettn_anchor_hr_top - 14
    ettn_fs = 8.5
    ettn_lbl = "ETTN:"
    c.setFont(font_bold, ettn_fs)
    ettn_lbl_w = c.stringWidth(ettn_lbl, font_bold, ettn_fs)
    c.drawString(margin, ettn_baseline_y, ettn_lbl)
    c.setFont(font_name, ettn_fs)
    ettn_val_x = margin + ettn_lbl_w + c.stringWidth(" ", font_name, ettn_fs)
    c.drawString(ettn_val_x, ettn_baseline_y, str(ettn)[:48])

    # Mal/Hizmet tablosu — GİB tarzı: başlık üst border, çok satırlı okunaklı başlıklar
    table_width = right - margin
    # Tablo üst çizgisi ETTN'e yapışık: önceki min(..., h_pt - 95mm) tabloyu sayfada aşağı sabitliyor,
    # ETTN ile tablo arasında büyük beyaz boşluk bırakıyordu.
    descent_below_baseline_pt = 2.5
    gap_ettn_to_table_top_pt = 2.5
    start_y = ettn_baseline_y - descent_below_baseline_pt - gap_ettn_to_table_top_pt
    header_row_h = 26   # pt, başlıkları daha kompakt tut
    row_h = 10.5        # pt, veri + boş satırlar
    cell_pad_vert = 3   # pt, satır altı boşluk
    data_text_offset = 0.5 * mm
    summary_row_h = 10.5   # pt, özet kutusu satırları
    GAP_TABLE_TO_SUMMARY = 1 * mm
    GAP_SUMMARY_TO_YALNIZ = 2.5 * mm
    header_line_h = 10  # pt, başlık satırı aralığı
    header_font_ascent = 8   # 9pt font yaklaşık ascent (dikey ortalama için)
    c.setFont(font_bold, 9)
    # GİB e-Arşiv fatura başlıkları (dar sütunlarda kısa metin, taşma olmasın)
    cols = [
        "Sıra No",
        "Mal Hizmet",
        "Miktar",
        "Birim",
        "Birim Fiyat",
        "İsk./Artt. Oranı",
        "İsk./Artt. Tutarı",
        "KDV Oranı",
        "KDV Tutarı",
        "Mal Hizmet Tutarı",
    ]
    # Sütun oranları (toplam 180): uzun ürün adında "Mal Hizmet" kolonu dinamik genişlesin.
    w_ratios = [8, 46, 11, 11, 18, 13, 21, 12, 20, 20]
    try:
        max_name_len = 0
        for srow in (satirlar or []):
            nm = str((srow or {}).get("ad") or (srow or {}).get("hizmet_urun_adi") or "").strip()
            if len(nm) > max_name_len:
                max_name_len = len(nm)
        # 34 karakter üzerini uzun kabul et; Mal/Hizmet'e +8'e kadar alan ver.
        extra = min(8, max(0, int((max_name_len - 34) / 6)))
        if extra > 0:
            w_ratios[1] += extra
            # Alanı toplamdan düşmek için daraltılabilir kolonlar: tutar/iskonto/kdv.
            for idx in (9, 8, 6, 5, 4, 7):
                if extra <= 0:
                    break
                min_w = 11 if idx in (5, 7) else 14
                can_take = max(0, w_ratios[idx] - min_w)
                take = min(extra, can_take)
                if take > 0:
                    w_ratios[idx] -= take
                    extra -= take
    except Exception:
        pass
    widths = [table_width * (r / 180.0) for r in w_ratios]
    table_right = margin + table_width

    # Sağ meta + QR: son satır baseline = ETTN ile aynı Y; sağ kenar = table_right (start_y ile konum yok)
    meta_row_min_pt = 11.5
    meta_font_size = 6.5
    box_height = max(28 * mm, len(meta_lines) * meta_row_min_pt)
    box_width = min(62 * mm, max(28 * mm, table_right - right_x))
    box_left = table_right - box_width
    n_meta = len(meta_lines)
    row_h_meta = box_height / float(n_meta)
    box_top = ettn_baseline_y + row_h_meta * (n_meta - 0.5) + 0.32 * meta_font_size
    qr_meta_gap = 2 * mm
    qr_y_min = box_top + qr_meta_gap
    # QR üst kenarı = gönderici üst çift çizgisi (sender_top); sağ = table_right
    qr_size = max(16 * mm, sender_top - qr_y_min)
    qr_y = sender_top - qr_size
    if qr_y < qr_y_min:
        qr_y = qr_y_min
        qr_size = max(16 * mm, sender_top - qr_y)
    qr_x = table_right - qr_size

    # GİB logo x/y: qr_size burada kesinleşti; 85 mm + 10 mm gönderici/QR payı
    _sender_block_right = left_x + left_col_w
    _logo_x_min = _sender_block_right + GIB_LOGO_SIDE_CLEAR
    _logo_x_max = qr_x - GIB_LOGO_SIDE_CLEAR - logo_draw_w
    if _logo_x_max < _logo_x_min:
        logo_ll_x = _logo_x_min
    else:
        logo_ll_x = max(_logo_x_min, min(GIB_LOGO_LEFT_FROM_PAGE, _logo_x_max))
    logo_ll_y = logo_cy - logo_h / 2.0
    sub_baseline = logo_ll_y - SUBTITLE_BELOW_LOGO_MM

    box_pad_x = 2.2 * mm
    gutter_cols = 1.4 * mm
    label_col_frac = 0.42
    label_col_right = box_left + box_width * label_col_frac
    lbl_max_w = label_col_right - box_left - box_pad_x - gutter_cols
    val_max_w = (box_left + box_width - box_pad_x) - label_col_right - gutter_cols
    val_draw_right_x = box_left + box_width - box_pad_x

    def _meta_fit_sizes(txt, max_w_pt, sizes_tuple):
        t = str(txt or "")
        for fs in sizes_tuple:
            c.setFont(font_name, fs)
            if c.stringWidth(t, font_name, fs) <= max_w_pt:
                return t, fs
        fs = sizes_tuple[-1]
        c.setFont(font_name, fs)
        ell = "…"
        if c.stringWidth(ell, font_name, fs) > max_w_pt:
            return "", fs
        tt = t
        while len(tt) > 0 and c.stringWidth(tt + ell, font_name, fs) > max_w_pt:
            tt = tt[:-1]
        return (tt + ell) if tt else "", fs

    c.rect(box_left, box_top - box_height, box_width, box_height)
    for idx, (lbl, val) in enumerate(meta_lines):
        y0 = box_top - row_h_meta * idx
        c.line(box_left, y0, box_left + box_width, y0)
        cell_mid_y = y0 - row_h_meta / 2
        baseline_y = cell_mid_y - 0.32 * meta_font_size

        if idx == n_meta - 1:
            baseline_y = ettn_baseline_y
            c.setFont(font_name, meta_font_size)
            c.drawString(box_left + box_pad_x, baseline_y, (lbl or "")[:28])
            c.drawRightString(val_draw_right_x, baseline_y, (str(val) or "")[:22])
            continue

        s_lbl, fs_l = _meta_fit_sizes(lbl, lbl_max_w, (meta_font_size, 6.0))
        c.setFont(font_name, fs_l)
        c.drawString(box_left + box_pad_x, baseline_y, s_lbl)

        s_val, fs_v = _meta_fit_sizes(val, val_max_w, (meta_font_size, 6.0, 5.5))
        c.setFont(font_name, fs_v)
        c.drawRightString(val_draw_right_x, baseline_y, s_val)

    # Başlık üst border (GİB gibi)
    c.line(margin, start_y, table_right, start_y)

    cell_pad_h = 5   # yatay boşluk, yazı dikey sınırlara değmesin
    x = margin
    for i, col in enumerate(cols):
        w_pt = widths[i]
        max_w = max(18, w_pt - cell_pad_h * 2)  # taşma olmaması için sıkı sığdırma
        lines = _wrap_header(c, col, max_w, font_bold, 9)
        block_h = len(lines) * header_line_h
        # Blok merkezi = satır merkezi; üst/alt çizgiye değmesin
        first_baseline = start_y - (header_row_h / 2) - header_font_ascent + (block_h / 2)
        for j, line in enumerate(lines):
            # Hücre içinde soldan padding, taşan satır çizilmesin
            c.drawString(x + cell_pad_h, first_baseline - j * header_line_h, line)
        x += w_pt
    c.line(margin, start_y - header_row_h, table_right, start_y - header_row_h)

    # Veri satırları — ilk satır (1 Sanal ofis...) daha yüksek, yazı üst/alt çizgiye değmesin
    c.setFont(font_name, 8.5)
    y_line = start_y - header_row_h - 5
    sira = 1
    pad = 5

    def _wrap_cell_text(text, max_width_pt, font_nm, font_sz):
        """Hücre metni: normal wrap + break-word (uzun kelimeyi böler)."""
        t = (text or "").strip()
        if not t:
            return [""]
        c.setFont(font_nm, font_sz)
        words = t.split()
        if not words:
            return [t]
        lines = []
        cur = ""
        for w in words:
            candidate = (cur + " " + w).strip() if cur else w
            if c.stringWidth(candidate, font_nm, font_sz) <= max_width_pt:
                cur = candidate
                continue
            if cur:
                lines.append(cur)
                cur = ""
            # break-word: tek kelime bile sığmıyorsa parçala
            if c.stringWidth(w, font_nm, font_sz) <= max_width_pt:
                cur = w
            else:
                chunk = ""
                for ch in w:
                    cand2 = chunk + ch
                    if c.stringWidth(cand2, font_nm, font_sz) <= max_width_pt:
                        chunk = cand2
                    else:
                        if chunk:
                            lines.append(chunk)
                        chunk = ch
                cur = chunk
        if cur:
            lines.append(cur)
        return lines or [t]

    def _force_lines_to_max_width(lines, max_width_pt, font_nm, font_sz):
        """Kelime sarması bazen genişliği aşar (Türkçe glif / font ölçümü); satırı güvenli biçimde böl.
        Mümkünse boşluk/tire üzerinden bölünür (AĞUSTOS → AĞUSTO|S gibi hatalı harf kırığını önler)."""
        c.setFont(font_nm, font_sz)
        mw = max(8.0, float(max_width_pt) - 3.0)
        out = []
        for ln in list(lines or [""]):
            s = ln or ""
            while s:
                if c.stringWidth(s, font_nm, font_sz) <= mw:
                    out.append(s)
                    break
                lo, hi = 1, len(s)
                best = 1
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if c.stringWidth(s[:mid], font_nm, font_sz) <= mw:
                        best = mid
                        lo = mid + 1
                    else:
                        hi = mid - 1
                take = max(1, best)
                # Kelime ortasında kırma: geriye doğru boşluk, tire veya em-dash ara
                if take < len(s) and s[take - 1] not in (" ", "\t"):
                    cut = -1
                    for sep in (" ", "\t", "—", "-", "/"):
                        p = s.rfind(sep, 0, take)
                        if p >= 1:
                            cand = s[:p].rstrip()
                            if cand and c.stringWidth(cand, font_nm, font_sz) <= mw:
                                cut = p
                                break
                    if cut > 0:
                        take = cut
                chunk = s[:take].rstrip()
                if not chunk:
                    chunk = s[:1]
                    take = 1
                out.append(chunk)
                s = s[take:].lstrip()
        return out if out else [""]

    # Logo + alt başlık + QR + tarih: en son (z-order); tarih/ başlık kaybolmasın diye tekrar
    def _flush_gib_overlay():
        c.saveState()
        try:
            c.setFillColorRGB(0, 0, 0)
            c.setFont(font_name, 9)
            c.drawString(left_x, date_header_y, ust_tarih)
            if gib_path and os.path.isfile(gib_path):
                try:
                    _gib_r = _gib_logo_reader(gib_path)
                    if _gib_r is not None:
                        c.drawImage(
                            _gib_r,
                            logo_ll_x,
                            logo_ll_y,
                            width=logo_draw_w,
                            height=logo_h,
                            preserveAspectRatio=True,
                            mask="auto",
                        )
                    else:
                        c.drawImage(
                            gib_path,
                            logo_ll_x,
                            logo_ll_y,
                            width=logo_draw_w,
                            height=logo_h,
                            preserveAspectRatio=True,
                            mask="auto",
                        )
                    if _fatura_pdf_debug():
                        print(f"[fatura PDF] GIB logo drawImage tamam: {gib_path}")
                except Exception as ex_logo:
                    if _fatura_pdf_debug():
                        print(f"[fatura PDF] GIB logo drawImage hata: {ex_logo}")
            else:
                if _fatura_pdf_debug():
                    print("[fatura PDF] GIB logo dosyası yok")
            c.setFillColorRGB(0, 0, 0)
            c.setStrokeColorRGB(0, 0, 0)
            c.setFont(font_bold, 12)
            sub_txt = doc_subtitle_pdf or ""
            logo_cx = logo_ll_x + logo_draw_w / 2.0
            _sr = left_x + left_col_w
            _half_avail = min(logo_cx - _sr - GIB_LOGO_SIDE_CLEAR, qr_x - GIB_LOGO_SIDE_CLEAR - logo_cx)
            _max_tw = max(24 * mm, 2.0 * max(0, _half_avail))
            while sub_txt and c.stringWidth(sub_txt, font_bold, 12) > _max_tw:
                sub_txt = sub_txt[:-1]
            if sub_txt != doc_subtitle_pdf and sub_txt:
                sub_txt = sub_txt.rstrip() + "…"
            display_sub = sub_txt or (doc_subtitle_pdf or "")[:28]
            c.drawCentredString(logo_cx, sub_baseline, display_sub)
            if qr_pil_image is not None:
                try:
                    c.drawInlineImage(
                        qr_pil_image,
                        qr_x,
                        qr_y,
                        width=qr_size,
                        height=qr_size,
                        preserveAspectRatio=True,
                    )
                    if _fatura_pdf_debug():
                        print("[fatura PDF] QR drawInlineImage tamam")
                except Exception as ex_qr:
                    if _fatura_pdf_debug():
                        print(f"[fatura PDF] QR drawInlineImage hata: {ex_qr}, ImageReader deneniyor.")
                    try:
                        from reportlab.lib.utils import ImageReader

                        _b = io.BytesIO()
                        qr_pil_image.save(_b, format="PNG")
                        _b.seek(0)
                        c.drawImage(
                            ImageReader(_b),
                            qr_x,
                            qr_y,
                            width=qr_size,
                            height=qr_size,
                            preserveAspectRatio=True,
                            mask="auto",
                        )
                        if _fatura_pdf_debug():
                            print("[fatura PDF] QR drawImage (PNG buffer) tamam")
                    except Exception as ex2:
                        if _fatura_pdf_debug():
                            print(f"[fatura PDF] QR drawImage yedek hata: {ex2}")
            else:
                if _fatura_pdf_debug():
                    print("[fatura PDF] QR PIL yok — çizilmedi (konsola bakın)")
        finally:
            c.restoreState()

    _flush_gib_overlay()

    for s in satirlar:
        if y_line < 50 * mm:
            c.showPage()
            y_line = h_pt - 50 * mm
            c.setFont(font_name, 9)
        miktar = float(s.get("miktar") or 0)
        birim_fiyat = float(s.get("birim_fiyat") or 0)
        isk_oran = float(s.get("iskonto_orani") or 0)
        isk_tutar_giris = s.get("iskonto_tutar")
        kdv = float(s.get("kdv_orani") or 0)
        brut = miktar * birim_fiyat
        if isk_tutar_giris is not None and float(isk_tutar_giris) > 0:
            isk_tutar = min(float(isk_tutar_giris), brut)
        else:
            isk_tutar = brut * isk_oran / 100.0
        # Yüzde kolonu için gerçek oranı her zaman brüt ve iskonto tutarından hesapla (16,68 yerine 1668 hatasını engelle)
        isk_yuzde = (isk_tutar / brut * 100.0) if brut > 0 else isk_oran
        net = brut - isk_tutar
        kdv_tutar = net * kdv / 100.0
        mal_tutar = net

        mal_hizmet_text = (s.get("ad") or s.get("hizmet_urun_adi") or "")[:200]
        # "Mal Hizmet" sütunu: wrap + satır yüksekliği her satırda metne göre (üst/alt padding + satır aralığı)
        mal_col_idx = 1
        mal_col_w = widths[mal_col_idx]
        mal_max_w = max(10, mal_col_w - (pad * 2) - 6)  # clip + metin ölçümü için pay
        mal_lines = _wrap_cell_text(mal_hizmet_text, mal_max_w, font_name, 9)
        mal_lines = _force_lines_to_max_width(mal_lines, mal_max_w, font_name, 9)
        # 9pt font: baseline aralığı + descender; alt/üst çizgiye değmesin
        mal_line_leading = 12.5  # pt (satır başına toplam dikey pay)
        mal_pad_top = 10  # üst border + büyük harf ascent
        mal_pad_bottom = 9  # alt border ile son satır descender arası
        min_row_h_pt = row_h
        n_mal = max(1, len(mal_lines))
        current_row_h = max(
            min_row_h_pt,
            mal_pad_top + mal_pad_bottom + n_mal * mal_line_leading,
        )

        values = [
            str(sira),
            "",  # Mal Hizmet'i aşağıda çok satır çizeceğiz
            f"{miktar:.2f}",
            s.get("birim") or "Adet",
            f"{birim_fiyat:.2f}",
            f"{isk_yuzde:.2f}",
            f"{isk_tutar:.2f}",
            f"{kdv:.2f}",
            f"{kdv_tutar:.2f}",
            f"{mal_tutar:.2f}",
        ]
        x = margin
        row_top = y_line
        # Diğer sütunlar: satır içinde dikey ortala
        draw_y_center = row_top - (current_row_h / 2) - 3
        for i, val in enumerate(values):
            w_pt = widths[i]
            if i == mal_col_idx:
                # Çok satırlı "Mal Hizmet" — clip ile komşu sütuna taşmayı kes; yükseklik padding + leading ile hesaplı
                cell_bottom_y = row_top - current_row_h
                c.saveState()
                _clip = c.beginPath()
                _clip.rect(x, cell_bottom_y, w_pt, current_row_h)
                c.clipPath(_clip, stroke=0, fill=0)
                # İlk baseline: üst iç boşluktan sonra (~9pt font ascent payı)
                y_txt = row_top - mal_pad_top
                for ln in mal_lines:
                    c.drawString(x + pad, y_txt, (ln or "")[:200])
                    y_txt -= mal_line_leading
                c.restoreState()
            else:
                if i in (0, 2, 4, 5, 6, 8, 9):
                    c.drawRightString(x + w_pt - pad, draw_y_center, val)
                else:
                    c.drawString(x + pad, draw_y_center, val)
            x += w_pt
        c.line(margin, row_top - current_row_h, table_right, row_top - current_row_h)
        y_line = row_top - current_row_h
        sira += 1

    # Veri sonrası boş ızgara satırları (GİB tarzı dolu tablo görünümü)
    _n_sum_rows = 5
    _yalniz_h_pre = 14  # pt
    _box_h_sm = _n_sum_rows * summary_row_h + 8
    # Özet kutusunu sayfanın alt bandına sabitle: tek satır olsa bile tablo ızgarası aşağıya kadar dolsun.
    _summary_box_y_fixed = 8 * mm + _yalniz_h_pre + GAP_SUMMARY_TO_YALNIZ
    _summary_top_fixed = _summary_box_y_fixed + _box_h_sm
    _table_bottom_min = _summary_top_fixed + GAP_TABLE_TO_SUMMARY
    # Boş ızgara: tek satırda bile ortadaki alan dolu görünsün.
    # Hedef satır sayısına göre boş satır yüksekliğini adaptif belirle.
    TARGET_EMPTY_ROWS = 14 if preview else 24
    available_h = max(0.0, float(y_line - _table_bottom_min))
    if available_h > 0:
        empty_row_h = max(5.8, min(float(row_h), available_h / float(TARGET_EMPTY_ROWS)))
    else:
        empty_row_h = max(5.8, min(float(row_h), 8.0))
    c.saveState()
    c.setLineWidth(0.5)
    try:
        _extra_empty = 0
        # Önizlemede de ızgara sayfa sonuna kadar dolsun.
        _extra_empty_cap = 800
        while y_line - empty_row_h >= _table_bottom_min and y_line - empty_row_h >= 50 * mm:
            ny = y_line - empty_row_h
            c.line(margin, ny, table_right, ny)
            y_line = ny
            _extra_empty += 1
            if _extra_empty >= _extra_empty_cap:
                break
    finally:
        c.restoreState()

    # Tablo sonu: son yatay çizgi ile aynı y
    table_bottom = y_line

    genel_toplam = float(fatura.get("genel_toplam") or fatura.get("toplam") or 0)
    ara_toplam = float(fatura.get("ara_toplam") or 0)
    toplam_isk = float(fatura.get("toplam_iskonto") or 0)
    kdv_toplam = float(fatura.get("kdv_toplam") or 0)
    summary_box_w = table_width * 0.45
    summary_box_x = margin + table_width - summary_box_w
    label_w_pt = summary_box_w * 0.68
    val_w_pt = summary_box_w - label_w_pt

    def _fmt_tl(v):
        return f"{float(v):,.2f} TL".replace(",", "X").replace(".", ",").replace("X", ".")

    labels_vals = [
        ("Mal Hizmet Toplam Tutarı", ara_toplam),
        ("Toplam İskonto", toplam_isk),
        ("Hesaplanan KDV", kdv_toplam),
        ("Vergiler Dahil Toplam Tutar", genel_toplam),
        ("Ödenecek Tutar", genel_toplam),
    ]
    n_summary_rows = len(labels_vals)
    row_heights = [(n_summary_rows * summary_row_h + 8) / float(n_summary_rows)] * n_summary_rows
    box_h = sum(row_heights)
    # Özet kutusu sabit alt konumda.
    box_y = _summary_box_y_fixed
    summary_top = box_y + box_h
    yalniz_box_h = 14  # pt
    yalniz_box_y = box_y - GAP_SUMMARY_TO_YALNIZ - yalniz_box_h

    # Dikey sütun çizgileri (başlıktan kalem tablosu sonuna)
    x_pos = margin
    c.line(x_pos, start_y, x_pos, table_bottom)
    for w in widths:
        x_pos += w
        c.line(x_pos, start_y, x_pos, table_bottom)

    # Alt toplam — ReportLab Table: tam dış kutu + ızgara (sol kenar dahil)
    summary_data = [[(lbl or "")[:55], _fmt_tl(val)] for lbl, val in labels_vals]
    sum_table = Table(
        summary_data,
        colWidths=[label_w_pt, val_w_pt],
        rowHeights=row_heights,
    )
    sum_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    sum_table.wrapOn(c, summary_box_w, box_h)
    sum_table.drawOn(c, summary_box_x, box_y)

    # GİB: Yazı ile tutar — özet kutusunun altına ~2,5 mm, tam genişlik kutu
    yalniz_text = tutar_yaziya_gib(genel_toplam)
    yalniz_font_size = 8.5
    c.setFont(font_name, yalniz_font_size)
    c.rect(margin, yalniz_box_y, table_width, yalniz_box_h)
    # Metni kutu içinde sola hizalı, dikey ortada (kenara yapışmasın)
    yalniz_pad_left = 2 * mm
    yalniz_baseline = yalniz_box_y + (yalniz_box_h / 2) - (yalniz_font_size * 0.35)
    c.drawString(margin + yalniz_pad_left, yalniz_baseline, (yalniz_text or "")[:120])

    c.save()
    return buf.getvalue()


def faturalar_gerekli(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated_function


def _opt_customer_id(v):
    """Form/JSON'dan gelen müşteri id: boş string veya geçersiz → None (PG integer hatasını önler)."""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _row_serializable(row):
    """Dict satırındaki date/time/datetime değerlerini string yapar (tojson hatasını önler)."""
    if not row:
        return row
    d = dict(row)
    for k, v in list(d.items()):
        if v is None:
            continue
        if hasattr(v, "strftime"):
            d[k] = v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "hour") else v.strftime("%Y-%m-%d")
    return d


def _fatura_satirlar_hesapla(fatura):
    """Fatura kaydından satır kırılımı (PDF önizleme ile aynı mantık)."""
    toplam = float(fatura.get("toplam") or 0)
    satirlar = []
    try:
        raw_j = fatura.get("satirlar_json")
        if raw_j:
            raw = json.loads(raw_j) if isinstance(raw_j, str) else raw_j
            for s in (raw if isinstance(raw, list) else []):
                miktar = float(s.get("miktar") or 0)
                birim_fiyat = float(s.get("birim_fiyat") or 0)
                isk_oran = float(s.get("iskonto_orani") or 0)
                isk_tutar_giris = s.get("iskonto_tutar")
                kdv_oran = float(s.get("kdv_orani") or 0)
                brut = miktar * birim_fiyat
                if isk_tutar_giris is not None and float(isk_tutar_giris or 0) > 0:
                    isk_tutar = min(float(isk_tutar_giris), brut)
                else:
                    isk_tutar = brut * isk_oran / 100.0
                net = brut - isk_tutar
                kdv_tutar = net * kdv_oran / 100.0
                satirlar.append({
                    "ad": (s.get("ad") or s.get("mal_hizmet") or "Hizmet").strip() or "Hizmet",
                    "miktar": miktar,
                    "birim": (s.get("birim") or "Adet").strip() or "Adet",
                    "birim_fiyat": birim_fiyat,
                    "iskonto_orani": isk_oran,
                    "iskonto_tutar": isk_tutar,
                    "kdv_orani": kdv_oran,
                    "kdv_tutar": kdv_tutar,
                    "mal_tutar": net,
                    "satir_toplam": net + kdv_tutar,
                })
    except Exception:
        pass
    if not satirlar:
        tutar = float(fatura.get("tutar") or 0)
        kdv_tutar = float(fatura.get("kdv_tutar") or 0)
        satirlar = [{
            "ad": "Hizmet",
            "miktar": 1,
            "birim": "Ay",
            "birim_fiyat": tutar,
            "iskonto_orani": 0,
            "iskonto_tutar": 0,
            "kdv_orani": (kdv_tutar / tutar * 100) if tutar else 20,
            "kdv_tutar": kdv_tutar,
            "mal_tutar": tutar,
            "satir_toplam": toplam,
        }]
    return satirlar


def _fatura_rapor_aktif_musteri_sql_durum_kosulu():
    """customers.durum: pasif / kapalı kayıtları rapor kapsamı dışı bırak."""
    return """
        (
            c.durum IS NULL
            OR TRIM(COALESCE(c.durum, '')) = ''
            OR LOWER(TRIM(c.durum)) NOT IN (
                'pasif', 'terk', 'kapandi', 'kapandı', 'kapalı', 'kapali', 'kapanmış', 'kapanmis'
            )
        )
    """


def _fatura_rapor_query_truthy(val) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on", "evet")


def _fatura_rapor_duzenli_fatura_norm(raw) -> str:
    s = str(raw or "").strip().lower()
    if not s or s in ("tum", "tumu", "all", "hepsi"):
        return ""
    s = s.replace("-", "_")
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s


# Tekil müşteri listesi: giriş tarihi (KYC söz. / rent_start / created_at) — SELECT ve ay filtresinde aynı ifade
_FIRMA_OZET_GIRIS_TARIHI_SQL = """
COALESCE(
    CASE
        WHEN mk.sozlesme_tarihi IS NULL THEN NULL
        WHEN BTRIM(mk.sozlesme_tarihi::text) = '' THEN NULL
        WHEN BTRIM(mk.sozlesme_tarihi::text) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
            THEN (SUBSTRING(BTRIM(mk.sozlesme_tarihi::text) FROM 1 FOR 10))::date
        WHEN BTRIM(mk.sozlesme_tarihi::text) ~ '^[0-9]{1,2}\\.[0-9]{1,2}\\.[0-9]{4}'
            THEN TO_DATE(
                REGEXP_REPLACE(BTRIM(mk.sozlesme_tarihi::text), ' .*$', ''),
                'DD.MM.YYYY'
            )
        WHEN BTRIM(mk.sozlesme_tarihi::text) ~ '^[0-9]{1,2}-[0-9]{1,2}-[0-9]{4}'
            THEN TO_DATE(
                REGEXP_REPLACE(BTRIM(mk.sozlesme_tarihi::text), ' .*$', ''),
                'DD-MM-YYYY'
            )
        ELSE NULL
    END,
    c.rent_start_date::date,
    c.created_at::date
)
""".strip()


def _fatura_rapor_giris_aylari_parse(raw) -> list | None:
    """giri_aylar=1,3,12 → benzersiz 1..12. Boş / geçersiz → None (filtre yok)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    seen: set[int] = set()
    out: list[int] = []
    for part in s.replace(";", ",").split(","):
        p = part.strip()
        if not p:
            continue
        try:
            m = int(p)
        except ValueError:
            continue
        if m < 1 or m > 12 or m in seen:
            continue
        seen.add(m)
        out.append(m)
    out.sort()
    return out or None


def _fatura_rapor_musteri_where_sql(pasifleri_dahil: bool, tum_musteriler: bool) -> str:
    """Müşteri kartı listesi için WHERE parçası (fatura yok satırları).

    - Varsayılan: yalnız aktif kart (is_active) ve customers.durum pasif benzeri değil.
    - Pasifler Dahil: pasif durum ve is_active=FALSE kayıtlar da listelenir (API pasifte ikisini de günceller).
    - Tüm Müşteriler: kapsam süzgeci yok (TRUE).
    """
    if tum_musteriler:
        return "TRUE"
    if pasifleri_dahil:
        # Önceki hata: yalnızca durum süzgecini kaldırıp is_active=TRUE bırakıyordu; pasif kartlar hiç görünmüyordu.
        return "TRUE"
    durum_sql = _fatura_rapor_aktif_musteri_sql_durum_kosulu().strip()
    base = "COALESCE(c.is_active, TRUE) = TRUE"
    return f"{base} AND {durum_sql}"


_AYLAR_TR_FATURA_RAPOR = (
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)


def _fatura_rapor_firma_ozet_mi(val) -> bool:
    s = str(val or "").strip().lower()
    return s in ("firma", "firma_ozet", "ozet", "1", "true", "evet")


def _firma_ozet_durum_etiket(row: dict) -> str:
    """Kart durumu: Aktif / Pasif (is_active + customers.durum)."""
    md = (row.get("musteri_durum") or "").strip().lower()
    if md == "pasif":
        return "Pasif"
    try:
        ia = row.get("is_active_kart")
        if ia is False:
            return "Pasif"
        if ia is not None and str(ia).strip().lower() in ("0", "false", "f", "no", "off"):
            return "Pasif"
    except Exception:
        pass
    return "Aktif"


_FIRMA_OZET_DEDUPE_UNVAN_SUFFIX = re.compile(
    r"\s*[.,]?\s*("
    r"a\.?\s*ş\.?|aş\.?|"
    r"ltd\.?|limited|"
    r"şti\.?|"
    r"san\.?\s*ve\s*tic\.?|sanayi\s*ve\s*ticaret|"
    r"holding"
    r")\s*\.?\s*$",
    re.IGNORECASE,
)


def _firma_ozet_normalize_vergi_no(val) -> str | None:
    """10 / 11 haneli VKN veya T.C. — aynı numaralı kartları tek satırda birleştirmek için."""
    if val is None:
        return None
    s = re.sub(r"\D", "", str(val).strip())
    if len(s) in (10, 11) and s.isdigit():
        return s
    return None


def _firma_ozet_unvan_cekirdek_anahtar(firma_adi: str) -> str:
    """Ünvan sonundaki A.Ş. / ŞTİ vb. kırpılmış, boşluk birleştirilmiş TR küçük harf anahtar."""
    raw = (firma_adi or "").strip()
    if not raw or raw == "—":
        return ""
    t = turkish_lower(raw)
    t = re.sub(r"\s+", " ", t).strip()
    for _ in range(4):
        t2 = _FIRMA_OZET_DEDUPE_UNVAN_SUFFIX.sub("", t).strip(" .,-")
        if t2 == t:
            break
        t = t2
    return t


def _firma_ozet_dedupe_grup_anahtari(it: dict) -> str:
    """Önce vergi no; yoksa çekirdek ünvan; yoksa tekil müşteri id."""
    tax = _firma_ozet_normalize_vergi_no(it.get("_dedupe_vergi"))
    if tax:
        return f"vn:{tax}"
    core = _firma_ozet_unvan_cekirdek_anahtar(it.get("firma_adi") or "")
    if core:
        return f"nm:{core}"
    try:
        return f"\x00id:{int(it.get('musteri_id') or 0)}"
    except (TypeError, ValueError):
        return "\x00id:0"


def _firma_ozet_dedupe_satirlar(satirlar: list) -> list:
    """Aynı vergi no veya aynı çekirdek ünvana sahip satırlardan birini bırak: önce Aktif, sonra en küçük musteri_id."""
    if len(satirlar) < 2:
        return satirlar
    buckets: dict[str, list] = {}
    order: list[str] = []
    for it in satirlar:
        k = _firma_ozet_dedupe_grup_anahtari(it)
        if k not in buckets:
            order.append(k)
            buckets[k] = []
        buckets[k].append(it)
    out: list = []
    for k in order:
        group = buckets[k]
        if len(group) == 1:
            out.append(group[0])
            continue

        def _aktif_mi(x: dict) -> bool:
            return (x.get("durum_etiket") or "").strip().lower() != "pasif"

        aktifler = [x for x in group if _aktif_mi(x)]
        pool = aktifler if aktifler else group

        def _mid(x: dict) -> int:
            try:
                return int(x.get("musteri_id") or 0)
            except (TypeError, ValueError):
                return 0

        out.append(min(pool, key=_mid))
    out.sort(key=lambda x: turkish_lower((x.get("firma_adi") or "").strip()))
    return out


def _firma_ozet_aylik_kdv_dahil(aylik_kira, kira_nakit, kdv_oran) -> float:
    """Giriş formu ile aynı: net aylık × (1+KDV) veya nakit ise tutar aynı (KDV dahil)."""
    try:
        a = float(aylik_kira or 0)
    except (TypeError, ValueError):
        a = 0.0
    if not math.isfinite(a) or a <= 0:
        return 0.0
    kn = bool(kira_nakit)
    try:
        kdv = float(kdv_oran) if kdv_oran is not None else 20.0
    except (TypeError, ValueError):
        kdv = 20.0
    if not math.isfinite(kdv):
        kdv = 20.0
    if kn:
        return round(a, 2)
    return round(a * (1.0 + kdv / 100.0), 2)


UI_PREF_KEY_FIRMA_MUSTERI = "firma_musteri_liste"


@bp.route("/api/ui-tercih", methods=["GET"])
@faturalar_gerekli
def api_ui_tercih_get():
    """Kullanıcıya özel UI ayarı (ör. müşteri listesi sütunları)."""
    ensure_user_ui_preferences_table()
    key = (request.args.get("key") or UI_PREF_KEY_FIRMA_MUSTERI).strip() or UI_PREF_KEY_FIRMA_MUSTERI
    if len(key) > 120:
        return jsonify({"ok": False, "mesaj": "Geçersiz anahtar."}), 400
    row = fetch_one(
        "SELECT pref_json FROM user_ui_preferences WHERE user_id = %s AND pref_key = %s",
        (current_user.id, key),
    )
    if not row or row.get("pref_json") is None:
        return jsonify({"ok": True, "prefs": None})
    prefs = row.get("pref_json")
    if isinstance(prefs, str):
        try:
            prefs = json.loads(prefs)
        except Exception:
            prefs = {}
    return jsonify({"ok": True, "prefs": prefs})


@bp.route("/api/ui-tercih", methods=["POST"])
@faturalar_gerekli
def api_ui_tercih_post():
    ensure_user_ui_preferences_table()
    data = request.get_json(silent=True) or {}
    key = str(data.get("key") or UI_PREF_KEY_FIRMA_MUSTERI).strip() or UI_PREF_KEY_FIRMA_MUSTERI
    if len(key) > 120:
        return jsonify({"ok": False, "mesaj": "Geçersiz anahtar."}), 400
    prefs = data.get("prefs")
    if prefs is None or not isinstance(prefs, dict):
        return jsonify({"ok": False, "mesaj": "prefs (nesne) gerekli."}), 400
    raw = json.dumps(prefs, ensure_ascii=False)
    if len(raw) > 32000:
        return jsonify({"ok": False, "mesaj": "Tercih verisi çok büyük."}), 400
    execute(
        """
        INSERT INTO user_ui_preferences (user_id, pref_key, pref_json, updated_at)
        VALUES (%s, %s, %s::jsonb, NOW())
        ON CONFLICT (user_id, pref_key) DO UPDATE SET
            pref_json = EXCLUDED.pref_json,
            updated_at = NOW()
        """,
        (current_user.id, key, raw),
    )
    return jsonify({"ok": True})


@bp.route('/api/fatura-rapor')
@faturalar_gerekli
def api_fatura_rapor():
    """Tarih aralığında kesilen faturaların satır bazlı dökümü (müşteri, tutarlar)."""
    bugun = date.today()
    first_this = bugun.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    bas_default = last_prev.replace(day=1)
    bas_s = (request.args.get("baslangic") or "").strip()
    bit_s = (request.args.get("bitis") or "").strip()
    try:
        bas = datetime.strptime(bas_s[:10], "%Y-%m-%d").date() if len(bas_s) >= 10 else bas_default
    except Exception:
        bas = bas_default
    try:
        bit = datetime.strptime(bit_s[:10], "%Y-%m-%d").date() if len(bit_s) >= 10 else bugun
    except Exception:
        bit = bugun
    if bas > bit:
        bas, bit = bit, bas
    sadece_faturali = str(request.args.get("sadece_faturali") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
        "evet",
    )
    pasifleri_dahil = _fatura_rapor_query_truthy(request.args.get("pasifleri_dahil"))
    tum_musteriler = _fatura_rapor_query_truthy(request.args.get("tum_musteriler"))
    cift_olanlar = _fatura_rapor_query_truthy(request.args.get("cift_olanlar"))
    duzenli_fatura = _fatura_rapor_duzenli_fatura_norm(request.args.get("duzenli_fatura"))
    gorunum_firma = _fatura_rapor_firma_ozet_mi(request.args.get("gorunum"))
    if gorunum_firma:
        # Tekil müşteri listesi: faturalar tablosu gerekmez; şema kontrolleri ve sorgu hafifletildi.
        ensure_customers_is_active()
        ensure_customers_durum()
        ensure_customers_rent_columns()
        ensure_customers_hazir_ofis_oda()
        ensure_musteri_kyc_columns()
        ensure_musteri_kyc_hazir_ofis_oda_no()
        ensure_musteri_kyc_latest_lookup_index()
        ref = bugun
        ay_y, ay_m = ref.year, ref.month
        ay_etiket = f"{_AYLAR_TR_FATURA_RAPOR[ay_m - 1]} {ay_y}"
        musteri_where = _fatura_rapor_musteri_where_sql(pasifleri_dahil, tum_musteriler)
        mk_df_sql = ""
        mk_df_params = []
        if duzenli_fatura:
            mk_df_sql = (
                "AND COALESCE(NULLIF(LOWER(TRIM(mk.duzenli_fatura)), ''), 'duzenle') = %s"
            )
            mk_df_params.append(duzenli_fatura)
        giris_aylar_filtre = _fatura_rapor_giris_aylari_parse(request.args.get("giri_aylar"))
        giri_ay_sql = ""
        if giris_aylar_filtre:
            # IN (%s,…) — psycopg2’de ANY(%s::int[]) bazı sürümlerde/uzak DB’de güvenilir bağlanmıyor.
            _gph = ", ".join(["%s"] * len(giris_aylar_filtre))
            giri_ay_sql = (
                f" AND (EXTRACT(MONTH FROM ({_FIRMA_OZET_GIRIS_TARIHI_SQL})::date))::int IN ({_gph})"
            )
            mk_df_params.extend(giris_aylar_filtre)
        hazir_oda_raw = (request.args.get("hazir_ofis_oda") or request.args.get("hazir_oda") or "").strip()
        hazir_oda_filtre = None
        if hazir_oda_raw:
            try:
                _hz = int(hazir_oda_raw)
                if 201 <= _hz <= 230:
                    hazir_oda_filtre = _hz
            except (TypeError, ValueError):
                pass
        ho_sql = ""
        if hazir_oda_filtre is not None:
            ho_sql = " AND COALESCE(mk.hazir_ofis_oda_no, c.hazir_ofis_oda_no) = %s"
            mk_df_params.append(hazir_oda_filtre)
        rows_firma = fetch_all(
            f"""
            SELECT c.id,
                   c.tax_number,
                   COALESCE(NULLIF(TRIM(c.musteri_adi), ''), NULLIF(TRIM(c.name), ''), '—') AS firma_adi,
                   TRIM(COALESCE(c.durum, '')) AS musteri_durum,
                   COALESCE(c.is_active, TRUE) AS is_active_kart,
                   COALESCE(
                       NULLIF(TRIM(mk.hizmet_turu), ''),
                       NULLIF(TRIM(c.hizmet_turu), ''),
                       ''
                   ) AS rapor_hizmet_turu,
                   COALESCE(mk.hazir_ofis_oda_no, c.hazir_ofis_oda_no) AS hazir_ofis_oda_no,
                   ({_FIRMA_OZET_GIRIS_TARIHI_SQL}) AS giris_raw,
                   mk.sozlesme_tarihi AS kyc_soz_bas,
                   mk.sozlesme_bitis AS kyc_soz_bit,
                   mk.kira_artis_tarihi AS kyc_kira_artis,
                   mk.kira_suresi_ay AS kyc_kira_suresi_ay,
                   mk.aylik_kira, mk.kira_nakit, mk.kdv_oran,
                   CASE
                       WHEN mk.aylik_kira IS NOT NULL AND mk.aylik_kira > 0 THEN mk.aylik_kira
                       ELSE COALESCE(c.guncel_kira_bedeli, c.ilk_kira_bedeli, mk.aylik_kira)
                   END AS firma_grid_aylik_net
            FROM customers c
            LEFT JOIN (
                SELECT DISTINCT ON (musteri_id)
                    musteri_id,
                    sozlesme_tarihi,
                    sozlesme_bitis,
                    kira_artis_tarihi,
                    kira_suresi_ay,
                    aylik_kira,
                    kira_nakit,
                    kdv_oran,
                    duzenli_fatura,
                    hizmet_turu,
                    hazir_ofis_oda_no
                FROM musteri_kyc
                ORDER BY musteri_id, id DESC
            ) mk ON mk.musteri_id = c.id
            WHERE {musteri_where}
              {mk_df_sql}{giri_ay_sql}{ho_sql}
            ORDER BY 2
            """,
            tuple(mk_df_params),
        ) or []
        from routes.giris_routes import (
            _ensure_musteri_reel_donem_tutar_table,
            firma_ozet_aylik_grid_hucre_kdv_dahil,
            _tufe_map_by_year_month,
            _aylik_grid_coerce_date,
        )

        _ensure_musteri_reel_donem_tutar_table()
        mids = [int(r["id"]) for r in rows_firma if r.get("id") is not None]
        reel_by_mid = {}
        if mids:
            rrows = fetch_all(
                """
                SELECT musteri_id, donem_yil, tutar_kdv_dahil
                FROM musteri_reel_donem_tutar
                WHERE musteri_id = ANY(%s)
                """,
                (mids,),
            ) or []
            for rr in rrows:
                try:
                    mid = int(rr["musteri_id"])
                    reel_by_mid.setdefault(mid, {})[int(rr["donem_yil"])] = float(rr.get("tutar_kdv_dahil") or 0)
                except (TypeError, ValueError):
                    pass
        tufe_map = _tufe_map_by_year_month()
        satirlar_firma = []
        for row in rows_firma:
            gid = row.get("id")
            firma = (row.get("firma_adi") or "").strip() or "—"
            mdur = (row.get("musteri_durum") or "").strip() or None
            raw_soz_bas = row.get("kyc_soz_bas")
            raw_soz_bit = row.get("kyc_soz_bit")
            giris_sql = row.get("giris_raw")
            bas_parsed = _aylik_grid_coerce_date(raw_soz_bas)
            bit_parsed = _aylik_grid_coerce_date(raw_soz_bit)
            # Ham KYC metni (21-02-2022 vb.) parse edilebiliyorsa öncelikli; yoksa SQL COALESCE (created_at yanlış olabiliyordu).
            soz_bas_eff = bas_parsed or giris_sql or raw_soz_bas
            soz_bit_eff = bit_parsed if bit_parsed is not None else raw_soz_bit
            if isinstance(soz_bas_eff, datetime):
                giris_iso = soz_bas_eff.date().isoformat()[:10]
            elif isinstance(soz_bas_eff, date):
                giris_iso = soz_bas_eff.isoformat()[:10]
            else:
                gr = giris_sql
                giris_iso = str(gr)[:10] if gr is not None and str(gr).strip() else ""
            kyc_for_grid = {
                "sozlesme_tarihi": soz_bas_eff,
                "sozlesme_bitis": soz_bit_eff,
                "aylik_kira": row.get("firma_grid_aylik_net"),
                "kira_artis_tarihi": row.get("kyc_kira_artis"),
                "kira_suresi_ay": row.get("kyc_kira_suresi_ay"),
                "kira_nakit": row.get("kira_nakit"),
                "kdv_oran": row.get("kdv_oran"),
            }
            try:
                gid_int = int(gid)
            except (TypeError, ValueError):
                gid_int = 0
            if gid_int <= 0:
                atut = 0.0
            else:
                # Disk önbelleği yok (satır KYC + kart kirası ile canlı TÜFE grid).
                # Ekranda Aylık kutularına reel dönem + TÜFE (sozlesmelerReelDonemTutarlariUygula) basıldığı için reel önce.
                atut = firma_ozet_aylik_grid_hucre_kdv_dahil(
                    gid_int,
                    ay_y,
                    ay_m,
                    tufe_map,
                    kyc_for_grid,
                    None,
                    reel_by_mid.get(gid_int, {}),
                    skip_disk_cache=True,
                    skip_reel_overlay=False,
                )
            item = {
                "musteri_id": gid,
                "firma_adi": firma,
                "giris_tarihi": giris_iso,
                "aylik_tutar": atut,
                "hizmet_turu": ((row.get("rapor_hizmet_turu") or "").strip() or "—"),
                "durum_etiket": _firma_ozet_durum_etiket(row),
                "_dedupe_vergi": row.get("tax_number"),
            }
            _ho = row.get("hazir_ofis_oda_no")
            if _ho is not None and str(_ho).strip() != "":
                try:
                    item["hazir_ofis_oda_no"] = int(_ho)
                except (TypeError, ValueError):
                    item["hazir_ofis_oda_no"] = _ho
            try:
                ko_raw = row.get("kdv_oran")
                if ko_raw is not None and str(ko_raw).strip() != "":
                    item["kdv_oran"] = int(round(float(ko_raw)))
            except (TypeError, ValueError):
                pass
            if mdur and pasifleri_dahil:
                item["musteri_durum"] = mdur
            satirlar_firma.append(item)
        satirlar_firma.sort(key=lambda x: turkish_lower((x.get("firma_adi") or "").strip()))
        if not cift_olanlar:
            satirlar_firma = _firma_ozet_dedupe_satirlar(satirlar_firma)
        for _it in satirlar_firma:
            _it.pop("_dedupe_vergi", None)
        toplam_aylik = 0.0
        for it in satirlar_firma:
            try:
                v = float(it.get("aylik_tutar") or 0)
                if math.isfinite(v):
                    toplam_aylik += v
            except (TypeError, ValueError):
                pass
        toplam_aylik = round(toplam_aylik, 2)
        kapsam_etiket = "tum_kayitlar" if tum_musteriler else ("pasif_dahil" if pasifleri_dahil else "aktif")
        return jsonify({
            "ok": True,
            "gorunum": "firma_ozet",
            "baslangic": bas.isoformat(),
            "bitis": bit.isoformat(),
            "duzenli_fatura": duzenli_fatura or "",
            "musteri_kapsam": kapsam_etiket,
            "pasifleri_dahil": pasifleri_dahil,
            "tum_musteriler": tum_musteriler,
            "cift_olanlar": cift_olanlar,
            "sadece_faturali": False,
            "giri_aylar_filtre": giris_aylar_filtre or [],
            "hazir_ofis_oda_filtre": hazir_oda_filtre,
            "ay_referans": {"y": ay_y, "m": ay_m, "etiket": ay_etiket},
            "satirlar": satirlar_firma,
            "ozet": {
                "fatura_adedi": 0,
                "satir_adedi": len(satirlar_firma),
                "kesilen_fatura_satir_sayisi": 0,
                "musteri_kapsam_adedi": len(satirlar_firma),
                "donemde_faturasiz_musteri": 0,
                "toplam_satir_kdv_dahil": toplam_aylik,
            },
        })
    ensure_faturalar_amount_columns()
    ensure_customers_is_active()
    ensure_customers_durum()
    # Tarih filtresi: bazı kayıtlarda fatura_tarihi boş kalabiliyor (GİB sonrası vb.);
    # vade_tarihi veya oluşturulma tarihi ile yedeklenir; aksi halde raporda görünmezler.
    df_sql = ""
    df_params = []
    if duzenli_fatura:
        df_sql = """
        AND COALESCE(
            NULLIF(LOWER(TRIM((
                SELECT mk.duzenli_fatura
                FROM musteri_kyc mk
                WHERE mk.musteri_id = CAST(f.musteri_id AS INTEGER)
                ORDER BY mk.id DESC
                LIMIT 1
            ))), ''),
            'duzenle'
        ) = %s
        """
        df_params.append(duzenli_fatura)
    rows_raw = fetch_all(
        f"""
        SELECT f.id, f.fatura_no, f.fatura_tarihi, f.vade_tarihi,
               f.musteri_id, f.musteri_adi,
               COALESCE(
                   NULLIF(TRIM(c.musteri_adi), ''),
                   NULLIF(TRIM(c.name), ''),
                   NULLIF(TRIM(f.musteri_adi), ''),
                   '—'
               ) AS musteri_adi_goster,
               f.tutar, f.kdv_tutar, f.toplam, f.satirlar_json, f.durum, f.ettn
        FROM faturalar f
        LEFT JOIN customers c ON CAST(f.musteri_id AS INTEGER) = c.id
        WHERE (
            (
                COALESCE(
                        f.fatura_tarihi::date,
                        f.vade_tarihi::date
                      ) >= %s
                AND COALESCE(
                        f.fatura_tarihi::date,
                        f.vade_tarihi::date
                      ) <= %s
            )
            OR (
                COALESCE(
                        f.fatura_tarihi::date,
                        f.vade_tarihi::date
                      ) IS NULL
                AND NULLIF(TRIM(COALESCE(f.ettn::text, '')), '') IS NOT NULL
            )
        )
        {df_sql}
        ORDER BY COALESCE(
                f.fatura_tarihi::date,
                f.vade_tarihi::date
              ) ASC NULLS LAST, f.id ASC
        """,
        tuple([bas, bit] + df_params),
    ) or []
    satirlar_out = []
    for f in rows_raw:
        fdict = dict(f)
        line_items = _fatura_satirlar_hesapla(fdict)
        ft = fdict.get("fatura_tarihi")
        ftarih = ""
        if ft is not None and str(ft).strip():
            ftarih = str(ft)[:10]
        elif fdict.get("vade_tarihi") is not None and str(fdict.get("vade_tarihi") or "").strip():
            ftarih = str(fdict.get("vade_tarihi"))[:10]
        else:
            ftarih = ""
        m_ad = (fdict.get("musteri_adi_goster") or fdict.get("musteri_adi") or "").strip() or "—"
        mid = fdict.get("musteri_id")
        fn = (fdict.get("fatura_no") or "").strip()
        gid = fdict.get("id")
        genel = round(float(fdict.get("toplam") or 0), 2)
        ettn = (str(fdict.get("ettn") or "").strip() or None)
        for idx, ln in enumerate(line_items, start=1):
            st = float(ln.get("satir_toplam") or 0)
            satirlar_out.append({
                "fatura_id": gid,
                "fatura_no": fn,
                "fatura_tarihi": ftarih,
                "musteri_id": mid,
                "musteri_adi": m_ad,
                "satir_no": idx,
                "satir_aciklama": ln.get("ad") or "Hizmet",
                "miktar": round(float(ln.get("miktar") or 0), 4),
                "birim": (ln.get("birim") or "Adet").strip(),
                "birim_fiyat": round(float(ln.get("birim_fiyat") or 0), 2),
                "iskonto_tutar": round(float(ln.get("iskonto_tutar") or 0), 2),
                "kdv_orani": round(float(ln.get("kdv_orani") or 0), 2),
                "mal_tutar": round(float(ln.get("mal_tutar") or 0), 2),
                "kdv_tutar": round(float(ln.get("kdv_tutar") or 0), 2),
                "satir_toplam": round(st, 2),
                "fatura_genel_toplam": genel,
                "ettn": ettn,
            })
    kesilen_fatura_satir_sayisi = len(satirlar_out)
    musteri_kapsam_adedi = 0
    donemde_faturasiz_musteri = 0
    if not sadece_faturali:
        musteri_where = _fatura_rapor_musteri_where_sql(pasifleri_dahil, tum_musteriler)
        mk_df_sql = ""
        mk_df_params = []
        if duzenli_fatura:
            mk_df_sql = """
            AND COALESCE(
                NULLIF(LOWER(TRIM((
                    SELECT mk.duzenli_fatura
                    FROM musteri_kyc mk
                    WHERE mk.musteri_id = c.id
                    ORDER BY mk.id DESC
                    LIMIT 1
                ))), ''),
                'duzenle'
            ) = %s
            """
            mk_df_params.append(duzenli_fatura)
        aktif_musteriler = fetch_all(
            f"""
            SELECT c.id,
                   COALESCE(NULLIF(TRIM(c.musteri_adi), ''), NULLIF(TRIM(c.name), ''), '—') AS musteri_adi_goster,
                   TRIM(COALESCE(c.durum, '')) AS musteri_durum
            FROM customers c
            WHERE {musteri_where}
              {mk_df_sql}
            ORDER BY 2
            """,
            tuple(mk_df_params),
        ) or []
        musteri_kapsam_adedi = len(aktif_musteriler)
        musteri_faturali = set()
        for s in satirlar_out:
            mid = s.get("musteri_id")
            if mid is None:
                continue
            try:
                musteri_faturali.add(int(mid))
            except (TypeError, ValueError):
                continue
        for row in aktif_musteriler:
            try:
                cid = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            if cid in musteri_faturali:
                continue
            donemde_faturasiz_musteri += 1
            m_ad = (row.get("musteri_adi_goster") or "—").strip() or "—"
            mdur = (row.get("musteri_durum") or "").strip() or None
            ph = {
                "fatura_id": None,
                "fatura_no": "—",
                "fatura_tarihi": "",
                "musteri_id": cid,
                "musteri_adi": m_ad,
                "satir_no": None,
                "satir_aciklama": "Seçilen dönemde fatura kaydı yok",
                "miktar": 0.0,
                "birim": "—",
                "birim_fiyat": 0.0,
                "iskonto_tutar": 0.0,
                "kdv_orani": 0.0,
                "mal_tutar": 0.0,
                "kdv_tutar": 0.0,
                "satir_toplam": 0.0,
                "fatura_genel_toplam": 0.0,
                "ettn": None,
                "musteri_doldurma": True,
            }
            if mdur:
                ph["musteri_durum"] = mdur
            satirlar_out.append(ph)
        satirlar_out.sort(
            key=lambda s: (
                turkish_lower((s.get("musteri_adi") or "").strip()),
                str(s.get("fatura_tarihi") or "9999-12-31"),
                int(s.get("fatura_id") or 0),
                int(s.get("satir_no") or 0),
            )
        )
    toplam_satir = round(sum(s["satir_toplam"] for s in satirlar_out), 2)
    kapsam_etiket = "tum_kayitlar" if tum_musteriler else ("pasif_dahil" if pasifleri_dahil else "aktif")
    return jsonify({
        "ok": True,
        "baslangic": bas.isoformat(),
        "bitis": bit.isoformat(),
        "sadece_faturali": sadece_faturali,
        "duzenli_fatura": duzenli_fatura or "",
        "musteri_kapsam": kapsam_etiket,
        "pasifleri_dahil": pasifleri_dahil,
        "tum_musteriler": tum_musteriler,
        "satirlar": satirlar_out,
        "ozet": {
            "fatura_adedi": len(rows_raw),
            "satir_adedi": len(satirlar_out),
            "kesilen_fatura_satir_sayisi": kesilen_fatura_satir_sayisi,
            "musteri_kapsam_adedi": musteri_kapsam_adedi,
            "donemde_faturasiz_musteri": donemde_faturasiz_musteri,
            "toplam_satir_kdv_dahil": toplam_satir,
        },
    })


@bp.route("/api/duzenli-fatura-secenekleri")
@faturalar_gerekli
def api_duzenli_fatura_secenekleri():
    ensure_duzenli_fatura_secenekleri_table()
    rows = fetch_all(
        "SELECT kod, etiket FROM duzenli_fatura_secenekleri ORDER BY sira NULLS LAST, etiket"
    ) or []
    secenekler = []
    for r in rows:
        kod = str(r.get("kod") or "").strip()
        etiket = str(r.get("etiket") or "").strip()
        if not kod:
            continue
        secenekler.append({"kod": kod, "etiket": etiket or kod})
    return jsonify({"ok": True, "secenekler": secenekler})


@bp.route("/api/fatura-sil-toplu", methods=["POST"])
@faturalar_gerekli
def api_fatura_sil_toplu():
    """Fatura raporundan seçilen faturaları siler; önce tahsilatlar, sonra fatura. ETTN’li (GİB kesilmiş) faturalar silinmez."""
    ensure_faturalar_amount_columns()
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}
    raw = data.get("fatura_ids")
    if not isinstance(raw, list):
        return jsonify({"ok": False, "mesaj": "fatura_ids listesi gerekli."}), 400
    ids = []
    seen = set()
    for x in raw:
        try:
            i = int(x)
            if i > 0 and i not in seen:
                seen.add(i)
                ids.append(i)
        except (TypeError, ValueError):
            continue
    if not ids:
        return jsonify({"ok": False, "mesaj": "Geçerli fatura id yok."}), 400
    if len(ids) > 500:
        return jsonify({"ok": False, "mesaj": "En fazla 500 fatura birden silinebilir."}), 400

    silinen = []
    atlanan = []
    for fid in ids:
        row = fetch_one(
            """SELECT id, fatura_no,
                      NULLIF(TRIM(COALESCE(ettn::text, '')), '') AS ettn_val
               FROM faturalar WHERE id = %s""",
            (fid,),
        )
        if not row:
            atlanan.append({"id": fid, "neden": "bulunamadi"})
            continue
        if row.get("ettn_val"):
            atlanan.append(
                {
                    "id": fid,
                    "fatura_no": row.get("fatura_no"),
                    "neden": "ettn_var",
                }
            )
            continue
        execute("DELETE FROM tahsilatlar WHERE fatura_id = %s", (fid,))
        rc = execute("DELETE FROM faturalar WHERE id = %s", (fid,))
        if rc:
            silinen.append({"id": fid, "fatura_no": row.get("fatura_no")})
        else:
            atlanan.append({"id": fid, "neden": "silinemedi"})
    msg = f"{len(silinen)} fatura silindi."
    if atlanan:
        msg += f" {len(atlanan)} kayıt atlandı (ETTN’li veya bulunamadı)."
    return jsonify({"ok": True, "silinen": silinen, "atlanan": atlanan, "mesaj": msg})


@bp.route('/')
@bp.route('/finans')
@faturalar_gerekli
def index():
    """Finans ana sayfası - Faturalar ve Tahsilatlar sekmeleri (/faturalar/ ve /faturalar/finans)"""
    return render_template('faturalar/finans.html')


@bp.route('/yeni')
@faturalar_gerekli
def yeni_fatura():
    """Yeni fatura oluşturma ekranı. ?musteri_id= ile gelirse seçili müşteri bilgileri forma doldurulur.
    Sözleşmeler gridinden: ?birim_fiyat=&kdv=&satir_aciklama= (net birim fiyat; KDV satıra uygulanır).
    Müşteri listesi (KDV dahil aylık): ?birim_fiyat_kdv_dahil=&kdv= → matrah otomatik hesaplanır."""
    now = datetime.now()
    secili_musteri = None
    default_hizmet_urun = ""
    default_birim_fiyat = 0
    default_kdv_orani = 20
    satir_aciklama_url = (request.args.get("satir_aciklama") or "").strip()

    bf_q = request.args.get("birim_fiyat")
    url_birim_set = False
    if bf_q is not None and str(bf_q).strip() != "":
        try:
            default_birim_fiyat = float(str(bf_q).replace(",", "."))
            url_birim_set = True
        except (TypeError, ValueError):
            default_birim_fiyat = 0

    kdv_q = request.args.get("kdv")
    if kdv_q is not None and str(kdv_q).strip() != "":
        try:
            k = int(float(str(kdv_q).replace(",", ".")))
            if k in (0, 1, 10, 20):
                default_kdv_orani = k
        except (TypeError, ValueError):
            pass

    # Müşteri listesi (firma_ozet) aylık sütunu KDV dahil; form birim fiyatı KDV hariç matrah.
    bf_kdv_dahil_q = request.args.get("birim_fiyat_kdv_dahil")
    if (not url_birim_set) and bf_kdv_dahil_q is not None and str(bf_kdv_dahil_q).strip() != "":
        try:
            gross = float(str(bf_kdv_dahil_q).replace(",", "."))
            if gross > 0:
                kpct = float(default_kdv_orani)
                default_birim_fiyat = round(gross / (1.0 + kpct / 100.0), 4)
                url_birim_set = True
        except (TypeError, ValueError):
            pass

    musteri_id = request.args.get('musteri_id', type=int)
    if musteri_id:
        cust = fetch_one(
            "SELECT id, name, musteri_adi, address, tax_number FROM customers WHERE id = %s",
            (musteri_id,),
        )
        if cust:
            kyc = fetch_one(
                "SELECT yeni_adres, vergi_dairesi, vergi_no, hizmet_turu, aylik_kira FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
                (musteri_id,),
            )
            address = (kyc and (kyc.get("yeni_adres") or kyc.get("address"))) or cust.get("address") or ""
            vergi_dairesi = (kyc and kyc.get("vergi_dairesi")) or ""
            if not vergi_dairesi:
                cust_vd = fetch_one("SELECT vergi_dairesi FROM customers WHERE id = %s", (musteri_id,))
                if cust_vd and cust_vd.get("vergi_dairesi"):
                    vergi_dairesi = (cust_vd.get("vergi_dairesi") or "").strip()
            vergi_no = (kyc and kyc.get("vergi_no")) or cust.get("tax_number") or ""
            if isinstance(vergi_no, float):
                vergi_no = str(int(vergi_no)) if vergi_no == int(vergi_no) else str(vergi_no)
            ma = (cust.get("musteri_adi") or "").strip()
            nm = (cust.get("name") or "").strip()
            secili_musteri = {
                "id": cust["id"],
                "name": cust.get("name") or "",
                "musteri_adi": ma or None,
                "display_label": (nm + " · " + ma) if ma else nm,
                "address": address,
                "vergi_dairesi": vergi_dairesi,
                "vergi_no": str(vergi_no) if vergi_no else "",
            }
            if kyc:
                default_hizmet_urun = (kyc.get("hizmet_turu") or "Sanal Ofis").strip() or "Sanal Ofis"
                if not url_birim_set:
                    try:
                        default_birim_fiyat = float(kyc.get("aylik_kira") or 0)
                    except (TypeError, ValueError):
                        default_birim_fiyat = 0
    _pk = (request.args.get("prefill_key") or "").strip()
    fatura_prefill_key = _pk if re.match(r"^[a-zA-Z0-9_-]{12,140}$", _pk) else ""
    default_gib_fatura_id = request.args.get("gib_fatura_id", type=int)
    if default_gib_fatura_id is not None and default_gib_fatura_id < 1:
        default_gib_fatura_id = None
    # GİB paneli: tek HTTP ile son fatura ID (JS beklemeden dolu gelsin)
    _son_gib = fetch_one("SELECT id FROM faturalar ORDER BY id DESC LIMIT 1") or {}
    try:
        _sid = _son_gib.get("id")
        son_fatura_id_for_gib = int(_sid) if _sid is not None else None
    except (TypeError, ValueError):
        son_fatura_id_for_gib = None
    if son_fatura_id_for_gib is not None and son_fatura_id_for_gib < 1:
        son_fatura_id_for_gib = None

    return render_template('faturalar/yeni_fatura.html',
                           bugun=now.strftime("%Y-%m-%d"),
                           saat=now.strftime("%H:%M"),
                           secili_musteri=_row_serializable(secili_musteri) if secili_musteri else None,
                           default_hizmet_urun=default_hizmet_urun,
                           default_birim_fiyat=default_birim_fiyat,
                           default_kdv_orani=default_kdv_orani,
                           satir_aciklama_url=satir_aciklama_url,
                           fatura_prefill_key=fatura_prefill_key,
                           default_gib_fatura_id=default_gib_fatura_id,
                           son_fatura_id_for_gib=son_fatura_id_for_gib)


@bp.route('/irsaliye/yeni')
@faturalar_gerekli
def yeni_irsaliye():
    """Yeni irsaliye oluşturma ekranı. Fatura ekranını irsaliye modu ile kullanır."""
    now = datetime.now()
    musteri_id = request.args.get('musteri_id', type=int)
    secili_musteri = None
    default_hizmet_urun = ""
    default_birim_fiyat = 0
    if musteri_id:
        cust = fetch_one(
            "SELECT id, name, musteri_adi, address, tax_number FROM customers WHERE id = %s",
            (musteri_id,),
        )
        if cust:
            kyc = fetch_one(
                "SELECT yeni_adres, vergi_dairesi, vergi_no, hizmet_turu, aylik_kira FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
                (musteri_id,),
            )
            address = (kyc and (kyc.get("yeni_adres") or kyc.get("address"))) or cust.get("address") or ""
            vergi_dairesi = (kyc and kyc.get("vergi_dairesi")) or ""
            if not vergi_dairesi:
                cust_vd = fetch_one("SELECT vergi_dairesi FROM customers WHERE id = %s", (musteri_id,))
                if cust_vd and cust_vd.get("vergi_dairesi"):
                    vergi_dairesi = (cust_vd.get("vergi_dairesi") or "").strip()
            vergi_no = (kyc and kyc.get("vergi_no")) or cust.get("tax_number") or ""
            if isinstance(vergi_no, float):
                vergi_no = str(int(vergi_no)) if vergi_no == int(vergi_no) else str(vergi_no)
            ma = (cust.get("musteri_adi") or "").strip()
            nm = (cust.get("name") or "").strip()
            secili_musteri = {
                "id": cust["id"],
                "name": cust.get("name") or "",
                "musteri_adi": ma or None,
                "display_label": (nm + " · " + ma) if ma else nm,
                "address": address,
                "vergi_dairesi": vergi_dairesi,
                "vergi_no": str(vergi_no) if vergi_no else "",
            }
            if kyc:
                default_hizmet_urun = (kyc.get("hizmet_turu") or "Sanal Ofis").strip() or "Sanal Ofis"
                try:
                    default_birim_fiyat = float(kyc.get("aylik_kira") or 0)
                except (TypeError, ValueError):
                    default_birim_fiyat = 0
    # Aynı fatura ekranını, irsaliye modu bilgisiyle kullanıyoruz
    return render_template(
        'faturalar/yeni_fatura.html',
        bugun=now.strftime("%Y-%m-%d"),
        saat=now.strftime("%H:%M"),
        secili_musteri=_row_serializable(secili_musteri) if secili_musteri else None,
        default_hizmet_urun=default_hizmet_urun,
        default_birim_fiyat=default_birim_fiyat,
        default_kdv_orani=20,
        satir_aciklama_url="",
        fatura_prefill_key="",
        irsaliye_modu=True,
        default_gib_fatura_id=None,
        son_fatura_id_for_gib=None,
    )


@bp.route('/faturalar')
@faturalar_gerekli
def faturalar():
    """Faturalar sekmesi. Tarih aralığı varsayılan: bu ayın 1'i - bugün."""
    today = date.today()
    first_of_month = today.replace(day=1)
    baslangic_str = request.args.get('baslangic', first_of_month.isoformat())
    bitis_str = request.args.get('bitis', today.isoformat())
    try:
        baslangic = datetime.strptime(baslangic_str[:10], '%Y-%m-%d').date()
        bitis = datetime.strptime(bitis_str[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        baslangic, bitis = first_of_month, today
    if baslangic > bitis:
        baslangic, bitis = bitis, baslangic
    yil = request.args.get('yil', today.year, type=int)
    ay_str = request.args.get('ay', '')
    ofis_kodu = request.args.get('ofis', '')
    ay_no = None
    if ay_str:
        try:
            ay_no = AYLAR.index(ay_str) + 1
        except ValueError:
            ay_no = None
    if ay_no:
        # Ay filtresinde tarih aralığını seçilen yılın tamamına çekiyoruz;
        # aksi halde varsayılan "bu ay" aralığı Mart gibi geçmiş ayları gizliyor.
        baslangic = date(yil, 1, 1)
        bitis = date(yil, 12, 31)
    
    ofisler = fetch_all("SELECT DISTINCT code FROM offices WHERE COALESCE(is_active::int, 1) = 1 ORDER BY code")
    
    sql = """
        SELECT f.*,
               c.name AS customer_name,
               COALESCE(NULLIF(TRIM(c.name), ''), NULLIF(TRIM(f.musteri_adi), ''), '—') AS musteri_adi_goster
        FROM faturalar f
        LEFT JOIN customers c ON CAST(f.musteri_id AS INTEGER) = c.id
        WHERE (f.fatura_tarihi::date) >= %s AND (f.fatura_tarihi::date) <= %s
    """
    params = [baslangic, bitis]
    
    if ofis_kodu:
        sql += " AND f.ofis_kodu = %s"
        params.append(ofis_kodu)

    # Ay seçilmişse (örn. Mart), seçilen yıl + ay kayıtlarını getir.
    if ay_no:
        sql += " AND EXTRACT(YEAR FROM (f.fatura_tarihi::date)) = %s"
        sql += " AND EXTRACT(MONTH FROM (f.fatura_tarihi::date)) = %s"
        params.extend([yil, ay_no])

    # Ay filtresinde ödenenler (kira ödemesi gelenler) üstte görünsün.
    if ay_no:
        sql += " ORDER BY CASE WHEN COALESCE(f.durum, '') = 'odendi' THEN 0 ELSE 1 END, (f.fatura_tarihi::date) DESC"
    else:
        sql += " ORDER BY (f.fatura_tarihi::date) DESC"
    
    faturalar_raw = fetch_all(sql, tuple(params))
    faturalar = [_row_serializable(f) for f in (faturalar_raw or [])]

    if ay_no:
        # Ay bazında "kesilecek" görünümünde:
        # - 0 TL satırları ele
        # - Aynı müşteriyi tek satıra indir (en güncel kayıt)
        tekil = []
        gorulen = set()
        for f in faturalar:
            try:
                if float(f.get('toplam') or 0) <= 0:
                    continue
            except Exception:
                continue
            key = f.get('musteri_id') or (f.get('musteri_adi_goster') or f.get('customer_name') or f.get('musteri_adi') or '').strip().lower()
            if not key or key in gorulen:
                continue
            gorulen.add(key)
            tekil.append(f)
        faturalar = sorted(
            tekil,
            key=lambda x: (
                (x.get('musteri_adi_goster') or x.get('customer_name') or x.get('musteri_adi') or '').strip().lower()
            )
        )
    
    toplam_tutar = sum(f.get('toplam') or 0 for f in faturalar)
    toplam_odenen = sum(f.get('toplam') or 0 for f in faturalar if f.get('durum') == 'odendi')
    toplam_kalan = toplam_tutar - toplam_odenen
    
    ofisler_list = list(ofisler or [])
    ofisler = [_row_serializable(o) for o in ofisler_list]
    yillar = list(range(today.year, today.year - 6, -1))
    musteriler = fetch_all("SELECT id, name, musteri_adi FROM customers ORDER BY name LIMIT 500")
    musteriler = [_row_serializable(m) for m in (musteriler or [])]
    
    return render_template('faturalar/faturalar_tab.html',
                         yil=yil,
                         ay=ay_str,
                         baslangic=baslangic.isoformat(),
                         bitis=bitis.isoformat(),
                         ofis_kodu=ofis_kodu,
                         aylar=AYLAR,
                         yillar=yillar,
                         ofisler=ofisler,
                         musteriler=musteriler,
                         faturalar=faturalar,
                         toplam_tutar=toplam_tutar,
                         toplam_odenen=toplam_odenen,
                         toplam_kalan=toplam_kalan)


@bp.route('/tahsilatlar')
@faturalar_gerekli
def tahsilatlar():
    """Tahsilatlar sekmesi"""
    yil = request.args.get('yil', datetime.now().year, type=int)
    
    tahsilatlar_raw = fetch_all("""
        SELECT t.*, c.name as musteri_adi, f.fatura_no
        FROM tahsilatlar t
        LEFT JOIN customers c ON COALESCE(t.customer_id, t.musteri_id) = c.id
        LEFT JOIN faturalar f ON t.fatura_id = f.id
        WHERE EXTRACT(YEAR FROM (t.tahsilat_tarihi::date)) = %s
        ORDER BY (t.tahsilat_tarihi::date) DESC
    """, (yil,))
    tahsilatlar_list = [_row_serializable(t) for t in (tahsilatlar_raw or [])]
    
    toplam = sum(t.get('tutar') or 0 for t in tahsilatlar_list)
    
    return render_template('faturalar/tahsilatlar_tab.html',
                         yil=yil,
                         tahsilatlar=tahsilatlar_list,
                         toplam=toplam)


def _next_fatura_no(prefix="INV"):
    """Yıla göre artan fatura numarası üret (örn: INV2026000001)."""
    yil = datetime.now().year
    like = f"{prefix}{yil}%"
    row = fetch_one("SELECT fatura_no FROM faturalar WHERE fatura_no LIKE %s ORDER BY id DESC LIMIT 1", (like,))
    if not row or not row.get("fatura_no"):
        return f"{prefix}{yil}000001"
    no = str(row["fatura_no"])
    try:
        tail = int(no[-6:])
        return f"{prefix}{yil}{tail+1:06d}"
    except Exception:
        return f"{prefix}{yil}000001"


_AY_ADLARI_TR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
                 "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


def _auto_inv_settings():
    ensure_auto_invoice_tables()
    s = fetch_one("SELECT * FROM auto_invoice_settings ORDER BY id LIMIT 1") or {}
    return {
        "enabled": bool(s.get("enabled")),
        "run_day": int(s.get("run_day") or 1),
        "run_hour": int(s.get("run_hour") or 9),
        "send_gib": bool(s.get("send_gib")),
        "auto_sms_code": (s.get("auto_sms_code") or "").strip(),
    }


def _auto_month_amount_from_cache(musteri_id, run_month_date):
    try:
        from routes.giris_routes import _build_aylik_grid_cache_payload
    except Exception:
        _build_aylik_grid_cache_payload = None
    if _build_aylik_grid_cache_payload:
        payload = _build_aylik_grid_cache_payload(int(musteri_id))
        if payload and isinstance(payload.get("aylar"), list):
            key = f"{run_month_date.year}-{run_month_date.month}"
            for a in payload["aylar"]:
                if str(a.get("ay_key")) == key:
                    try:
                        v = float(a.get("tutar_kdv_dahil") or 0)
                        if v > 0:
                            return round(v, 2)
                    except Exception:
                        pass
    kyc = fetch_one(
        "SELECT aylik_kira FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
        (musteri_id,),
    ) or {}
    net = float(kyc.get("aylik_kira") or 0)
    return round(net * 1.2, 2) if net > 0 else 0.0


def _auto_month_amount_resolved(musteri_id, run_month_date):
    """Aylık tutarı cache -> kyc -> son fatura toplam fallback zinciriyle bul."""
    try:
        v = float(_auto_month_amount_from_cache(musteri_id, run_month_date) or 0)
    except Exception:
        v = 0.0
    if v > 0:
        return round(v, 2)

    # KYC/cache boş ise müşterinin son pozitif toplamlı faturasını baz al.
    last_inv = fetch_one(
        """
        SELECT toplam
        FROM faturalar
        WHERE musteri_id = %s
          AND COALESCE(toplam, 0) > 0
        ORDER BY (fatura_tarihi::date) DESC, id DESC
        LIMIT 1
        """,
        (musteri_id,),
    ) or {}
    try:
        v2 = float(last_inv.get("toplam") or 0)
    except Exception:
        v2 = 0.0
    if v2 > 0:
        return round(v2, 2)

    # Son fallback: customers.ilk_kira_bedeli (net) üzerinden KDV dahil.
    c = fetch_one(
        "SELECT ilk_kira_bedeli FROM customers WHERE id = %s",
        (musteri_id,),
    ) or {}
    try:
        net = float(c.get("ilk_kira_bedeli") or 0)
    except Exception:
        net = 0.0
    return round(net * 1.2, 2) if net > 0 else 0.0


def _auto_invoice_create_for_customer(musteri_id, run_month_date):
    marker = f"|AUTO_INV|{run_month_date.strftime('%Y-%m')}|"
    mevcut = fetch_one(
        """SELECT id, fatura_no FROM faturalar
           WHERE musteri_id = %s
             AND COALESCE(notlar, '') LIKE %s
           ORDER BY id DESC LIMIT 1""",
        (musteri_id, f"%{marker}%"),
    )
    if mevcut:
        return {"status": "exists", "fatura_id": mevcut.get("id"), "fatura_no": mevcut.get("fatura_no")}
    cust = fetch_one("SELECT id, name FROM customers WHERE id = %s", (musteri_id,))
    if not cust:
        return {"status": "error", "error": "Müşteri bulunamadı."}
    toplam = _auto_month_amount_resolved(musteri_id, run_month_date)
    if toplam <= 0:
        return {"status": "skip", "error": "Aylık tutar bulunamadı veya 0."}
    fatura_no = _next_fatura_no()
    ay_ad = _AY_ADLARI_TR[run_month_date.month - 1]
    notlar = f"{ay_ad} {run_month_date.year} otomatik kira faturası {marker}"
    row = execute_returning(
        """INSERT INTO faturalar (
               fatura_no, musteri_id, musteri_adi, tutar, kdv_tutar, toplam,
               durum, fatura_tarihi, vade_tarihi, notlar
           ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           RETURNING id""",
        (
            fatura_no,
            musteri_id,
            cust.get("name") or "Müşteri",
            round(toplam / 1.2, 2),
            round(toplam - (toplam / 1.2), 2),
            toplam,
            "odenmedi",
            run_month_date,
            run_month_date,
            notlar,
        ),
    )
    return {"status": "created", "fatura_id": (row or {}).get("id"), "fatura_no": fatura_no, "toplam": toplam}


def run_auto_invoice_cycle(force=False, run_date=None):
    ensure_auto_invoice_tables()
    now = run_date or date.today()
    settings = _auto_inv_settings()
    if (not force) and (not settings["enabled"]):
        return {"ok": True, "skipped": True, "mesaj": "Otomatik fatura kapalı."}
    if (not force) and int(now.day) != int(settings["run_day"]):
        return {"ok": True, "skipped": True, "mesaj": "Bugün planlanan gün değil."}
    period_key = now.strftime("%Y-%m")
    var = fetch_one("SELECT id, status FROM auto_invoice_runs WHERE period_key = %s", (period_key,))
    if var and str(var.get("status") or "").lower() == "success" and not force:
        return {"ok": True, "skipped": True, "mesaj": "Bu dönem zaten çalıştırılmış."}

    run_row = execute_returning(
        """INSERT INTO auto_invoice_runs (period_key, run_date, status, started_at)
           VALUES (%s, %s, 'running', NOW())
           ON CONFLICT (period_key)
           DO UPDATE SET status='running', started_at=NOW(), finished_at=NULL, message=NULL
           RETURNING id""",
        (period_key, now),
    )
    run_id = (run_row or {}).get("id")
    success_count = 0
    fail_count = 0
    send_gib = settings["send_gib"]
    auto_sms = settings["auto_sms_code"]
    gib = None
    if send_gib:
        try:
            from gib_earsiv import BestOfficeGIBManager, build_fatura_data_from_db
            gib = BestOfficeGIBManager()
        except Exception:
            gib = None
            send_gib = False

    musteri_rows = fetch_all(
        """
        SELECT c.id
        FROM customers c
        WHERE LOWER(COALESCE(c.durum, 'aktif')) != 'pasif'
          AND EXISTS (SELECT 1 FROM musteri_kyc k WHERE k.musteri_id = c.id)
        ORDER BY c.id
        """
    ) or []
    for mr in musteri_rows:
        mid = int(mr.get("id"))
        try:
            created = _auto_invoice_create_for_customer(mid, now.replace(day=1))
            if created.get("status") in ("skip", "exists"):
                execute(
                    """INSERT INTO auto_invoice_items (run_id, musteri_id, fatura_id, period_key, status, error_message)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (run_id, mid, created.get("fatura_id"), period_key, created.get("status"), created.get("error")),
                )
                continue
            if created.get("status") != "created":
                fail_count += 1
                execute(
                    """INSERT INTO auto_invoice_items (run_id, musteri_id, period_key, status, error_message)
                       VALUES (%s,%s,%s,'error',%s)""",
                    (run_id, mid, period_key, created.get("error") or "Fatura oluşturulamadı."),
                )
                continue
            fatura_id = int(created.get("fatura_id"))
            gib_uuid = None
            item_status = "created"
            err = None
            if send_gib and gib and gib.is_available():
                try:
                    from gib_earsiv import build_fatura_data_from_db
                    f_data = build_fatura_data_from_db(fatura_id, fetch_one)
                    gib_uuid = gib.fatura_taslak_olustur(f_data)
                    item_status = "gib_draft" if gib_uuid else "gib_fail"
                    if gib_uuid and auto_sms:
                        ok_sms = gib.sms_onay_ve_imzala(gib_uuid, auto_sms)
                        item_status = "gib_signed" if ok_sms else "gib_sms_fail"
                except Exception as ge:
                    item_status = "gib_fail"
                    err = str(ge)
            execute(
                """INSERT INTO auto_invoice_items (run_id, musteri_id, fatura_id, period_key, status, gib_uuid, error_message)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (run_id, mid, fatura_id, period_key, item_status, gib_uuid, err),
            )
            success_count += 1
        except Exception as e:
            fail_count += 1
            execute(
                """INSERT INTO auto_invoice_items (run_id, musteri_id, period_key, status, error_message)
                   VALUES (%s,%s,%s,'error',%s)""",
                (run_id, mid, period_key, str(e)),
            )

    execute(
        """UPDATE auto_invoice_runs
           SET status=%s, finished_at=NOW(), success_count=%s, fail_count=%s, message=%s
           WHERE id=%s""",
        ("success" if fail_count == 0 else "partial", success_count, fail_count, f"{success_count} başarılı, {fail_count} hatalı", run_id),
    )
    return {"ok": True, "run_id": run_id, "success_count": success_count, "fail_count": fail_count}


@bp.route('/api/tutar-yazi')
@faturalar_gerekli
def tutar_yazi_api():
    """Genel toplamı Türkçe yazıya çevir (frontend için)."""
    tutar = request.args.get("tutar", "0").replace(",", ".")
    try:
        val = float(tutar)
    except ValueError:
        val = 0.0
    return jsonify({"yazi": tutar_yaziya(val)})


@bp.route('/fatura-onizleme', methods=['POST'])
@faturalar_gerekli
def fatura_onizleme():
    """Form verileriyle A4 fatura PDF önizlemesi (kaydetmeden)."""
    try:
        data = request.get_json() or {}
        satirlar = data.get("satirlar") or []
        irsaliye_modu = str(data.get("irsaliye_modu") or "").lower() in ("1", "true", "yes")
        musteri_id = _opt_customer_id(data.get("musteri_id"))
        musteri = {}
        if musteri_id:
            m = fetch_one("SELECT id, name, address, tax_number FROM customers WHERE id = %s", (musteri_id,))
            if m:
                musteri = dict(m)
        ara_toplam = float(data.get("ara_toplam") or 0)
        toplam_iskonto = float(data.get("toplam_iskonto") or 0)
        kdv_toplam = float(data.get("kdv_toplam") or 0)
        genel_toplam = float(data.get("toplam") or 0)
        fatura_tarihi = data.get("fatura_tarihi") or datetime.now().strftime("%Y-%m-%d")
        try:
            dt = datetime.strptime(fatura_tarihi[:10], "%Y-%m-%d")
            fatura_tarihi_str = dt.strftime("%d.%m.%Y")
        except Exception:
            fatura_tarihi_str = fatura_tarihi
        # Önizlemede boş numara için DB sorgusu yapma (her tıklamada gecikme)
        _fn = (data.get("fatura_no") or "").strip()
        if not _fn:
            _fn = f"ÖN-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        fatura = {
            "fatura_no": _fn,
            "fatura_tarihi": fatura_tarihi,
            "fatura_tarihi_str": fatura_tarihi_str,
            "fatura_tipi": data.get("fatura_tipi") or "satis",
            "fatura_saati": (data.get("fatura_saati") or "").strip() or None,
            "musteri_adi": data.get("musteri_adi"),
            "notlar": data.get("notlar"),
            "ara_toplam": ara_toplam,
            "toplam_iskonto": toplam_iskonto,
            "kdv_toplam": kdv_toplam,
            "genel_toplam": genel_toplam,
            "toplam": genel_toplam,
            "irsaliye_modu": irsaliye_modu,
            "sevk_adresi": (data.get("sevk_adresi") or "").strip() or None,
        }
        pdf_bytes = build_fatura_pdf(fatura, musteri, satirlar, preview=True)
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": "inline; filename=Fatura_Onizleme.pdf",
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


# --- Aylık kira / hizmet: aynı müşteri + aynı takvim ayı mükerrer fatura engeli ---
_AY_PARSE_VARIANTS = [
    (1, ("ocak",)),
    (2, ("şubat", "subat")),
    (3, ("mart",)),
    (4, ("nisan",)),
    (5, ("mayıs", "mayis")),
    (6, ("haziran",)),
    (7, ("temmuz",)),
    (8, ("ağustos", "agustos")),
    (9, ("eylül", "eylul")),
    (10, ("ekim",)),
    (11, ("kasım", "kasim")),
    (12, ("aralık", "aralik")),
]


def _satir_adindan_ay_yil_cikar(ad):
    """Satır açıklamasından (örn. 'Hizmet bedeli — NİSAN 2026') (yıl, ay) çiftleri."""
    tl = turkish_lower(ad or "")
    out = []
    for mno, variants in _AY_PARSE_VARIANTS:
        for vn in variants:
            if vn not in tl:
                continue
            idx = tl.find(vn)
            chunk = tl[max(0, idx - 16) : idx + len(vn) + 22]
            for my in re.finditer(r"(20\d{2})", chunk):
                try:
                    y = int(my.group(1))
                    if 1990 <= y <= 2100:
                        out.append((y, mno))
                except (TypeError, ValueError):
                    pass
            break
    return out


def _fatura_kira_donemleri_topla(satirlar, fatura_tarihi_iso, notlar):
    """Ay birimi, satır metni ve |AYLIK_TUTAR| işaretinden (yıl, ay) kümesi."""
    donemler = set()
    for m in re.finditer(r"\|AYLIK_TUTAR\|(\d{4}-\d{2}-\d{2})\|", notlar or ""):
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            donemler.add((d.year, d.month))
        except Exception:
            pass
    ay_birimi = False
    if isinstance(satirlar, list):
        for s in satirlar:
            if not isinstance(s, dict):
                continue
            ad = (s.get("ad") or s.get("name") or s.get("hizmet") or "").strip()
            birim = (s.get("birim") or "").strip().lower()
            if birim == "ay":
                ay_birimi = True
            for pair in _satir_adindan_ay_yil_cikar(ad):
                donemler.add(pair)
    if ay_birimi and not donemler and fatura_tarihi_iso:
        try:
            d = datetime.strptime(str(fatura_tarihi_iso)[:10], "%Y-%m-%d").date()
            donemler.add((d.year, d.month))
        except Exception:
            pass
    return donemler


def _musteri_icin_ayda_baska_fatura(musteri_id, y, m, exclude_fatura_id=None):
    """Aynı müşteri için verilen ay/yılda (fatura_tarihi ayı) başka fatura var mı."""
    try:
        mid = int(musteri_id)
    except (TypeError, ValueError):
        return None
    ay1 = date(y, m, 1)
    sql = """
        SELECT id, fatura_no, fatura_tarihi, ettn
        FROM faturalar
        WHERE musteri_id = %s
          AND DATE_TRUNC('month', COALESCE(fatura_tarihi::date, vade_tarihi::date)) = DATE_TRUNC('month', %s::date)
    """
    params = [mid, ay1]
    if exclude_fatura_id is not None:
        sql += " AND id <> %s"
        params.append(int(exclude_fatura_id))
    sql += " ORDER BY id DESC LIMIT 1"
    return fetch_one(sql, tuple(params))


@bp.route("/api/secilen-aylar-fatura-kontrol", methods=["POST"])
@faturalar_gerekli
def api_secilen_aylar_fatura_kontrol():
    """Sözleşme gridinden «Seçilenlerden fatura oluştur» öncesi kontrol.

    Kural: Sadece GİB'den imzalı (ettn dolu) faturalar mükerrer sayılır.
    ERP içi taslak/deneme (ettn boş) faturalar için engel çıkarılmaz.
    """
    try:
        data = request.get_json() or {}
    except Exception:
        data = {}
    musteri_id = _opt_customer_id(data.get("musteri_id"))
    if not musteri_id:
        return jsonify({"ok": False, "mesaj": "musteri_id gerekli."}), 400
    aylar = data.get("aylar")
    if not isinstance(aylar, list) or not aylar:
        return jsonify({"ok": False, "mesaj": "aylar listesi gerekli."}), 400
    cakismalar = []
    seen = set()
    for raw in aylar:
        if not isinstance(raw, dict):
            continue
        try:
            yv = int(raw.get("yil"))
            mv = int(raw.get("ay"))
        except (TypeError, ValueError):
            continue
        if mv < 1 or mv > 12 or yv < 1990 or yv > 2100:
            continue
        k = (yv, mv)
        if k in seen:
            continue
        seen.add(k)
        dup = _musteri_icin_ayda_baska_fatura(int(musteri_id), yv, mv)
        if dup:
            # Sadece GİB imzalı faturaları çakışma say (ettn dolu)
            ettn = (dup.get("ettn") or "").strip()
            if not ettn:
                continue
            cakismalar.append({
                "yil": yv,
                "ay": mv,
                "fatura_no": dup.get("fatura_no"),
                "fatura_id": dup.get("id"),
            })
    return jsonify({"ok": True, "cakismalar": cakismalar})


@bp.route('/fatura-ekle', methods=['POST'])
@faturalar_gerekli
def fatura_ekle():
    """Yeni fatura ekle (satır detaylarıyla)."""
    try:
        ensure_faturalar_amount_columns()
        data = request.get_json() or {}
        satirlar = data.get("satirlar") or []
        irsaliye_modu = str(data.get("irsaliye_modu") or "").lower() in ("1", "true", "yes")
        ara_toplam = float(data.get("ara_toplam") or 0)
        toplam_iskonto = float(data.get("toplam_iskonto") or 0)
        kdv_toplam = float(data.get("kdv_toplam") or 0)
        toplam = float(data.get("toplam") or 0)

        # Eğer frontend toplamları göndermediyse satırlardan hesapla
        if satirlar and toplam == 0:
            ara_toplam = 0.0
            toplam_iskonto = 0.0
            kdv_toplam = 0.0
            for s in satirlar:
                miktar = float(s.get("miktar") or 0)
                birim_fiyat = float(s.get("birim_fiyat") or 0)
                isk_oran = float(s.get("iskonto_orani") or 0)
                isk_tutar_giris = s.get("iskonto_tutar")
                kdv = float(s.get("kdv_orani") or 0)
                brut = miktar * birim_fiyat
                if isk_tutar_giris is not None and float(isk_tutar_giris) > 0:
                    isk_tutar = min(float(isk_tutar_giris), brut)
                else:
                    isk_tutar = brut * isk_oran / 100.0
                net = brut - isk_tutar
                kdv_tutar = net * kdv / 100.0
                ara_toplam += brut
                toplam_iskonto += isk_tutar
                kdv_toplam += kdv_tutar
            toplam = ara_toplam - toplam_iskonto + kdv_toplam

        tutar = ara_toplam - toplam_iskonto
        kdv_tutar = kdv_toplam

        raw_fat_tarih = (data.get("fatura_tarihi") or "").strip()
        if not raw_fat_tarih:
            fatura_tarihi = datetime.now().strftime("%Y-%m-%d")
        elif re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}$", raw_fat_tarih):
            g, a, y = raw_fat_tarih.split(".")
            fatura_tarihi = f"{y}-{a.zfill(2)}-{g.zfill(2)}"
        else:
            fatura_tarihi = raw_fat_tarih[:10]

        vade_tarihi = (data.get("vade_tarihi") or "").strip() or None
        if vade_tarihi and len(vade_tarihi) == 10 and vade_tarihi[2] == ".":
            g, a, y = vade_tarihi.split(".")
            vade_tarihi = f"{y}-{a.zfill(2)}-{g.zfill(2)}"

        fatura_no = (data.get("fatura_no") or "").strip() or _next_fatura_no()
        musteri_id = _opt_customer_id(data.get("musteri_id"))
        musteri_adi = (data.get("musteri_adi") or "").strip()
        # Cari ekstre tarafında kayıtlar musteri_id ile toplandığı için,
        # id boş gelirse isimden eşleyip otomatik doldur.
        if not musteri_id and musteri_adi:
            try:
                m = fetch_one(
                    """
                    SELECT id, name
                    FROM customers
                    WHERE LOWER(TRIM(COALESCE(name, ''))) = LOWER(TRIM(%s))
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (musteri_adi,),
                )
                if m and m.get("id"):
                    musteri_id = int(m.get("id"))
                    musteri_adi = (m.get("name") or musteri_adi).strip()
            except Exception:
                pass

        if musteri_id:
            try:
                mid = int(musteri_id)
                donemler = _fatura_kira_donemleri_topla(satirlar, fatura_tarihi, data.get("notlar"))
                if donemler:
                    for yy, mm in sorted(donemler):
                        dup = _musteri_icin_ayda_baska_fatura(mid, yy, mm)
                        if dup:
                            fn = dup.get("fatura_no") or ""
                            fid = dup.get("id")
                            return jsonify({
                                "ok": False,
                                "mesaj": (
                                    f"Bu müşteri için {mm:02d}.{yy} dönemi zaten faturalandırılmış "
                                    f"(Kayıt: {fn or ('#' + str(fid))}). "
                                    "Aynı ay için mükerrer fatura oluşturulamaz."
                                ),
                            }), 409
            except Exception as ex_dup:
                logging.getLogger(__name__).exception("fatura mükerrer ay kontrolü: %s", ex_dup)

        sevk_adresi_kayit = (data.get("sevk_adresi") or "").strip() or None
        execute(
            """
            INSERT INTO faturalar (
                fatura_no, musteri_id, musteri_adi, tutar, kdv_tutar,
                toplam, durum, fatura_tarihi, vade_tarihi, notlar, sevk_adresi
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                fatura_no,
                musteri_id,
                musteri_adi,
                tutar,
                kdv_tutar,
                toplam,
                data.get("durum") or "odenmedi",
                fatura_tarihi,
                vade_tarihi,
                data.get("notlar"),
                sevk_adresi_kayit,
            ),
        )
        row = fetch_one("SELECT id FROM faturalar WHERE fatura_no = %s ORDER BY id DESC LIMIT 1", (fatura_no,))
        fatura_id = row.get("id") if row else None
        if fatura_id and satirlar:
            try:
                execute("UPDATE faturalar SET satirlar_json = %s WHERE id = %s", (json.dumps(satirlar), fatura_id))
            except Exception:
                pass
        if fatura_id and irsaliye_modu:
            try:
                execute(
                    "UPDATE faturalar SET notlar = COALESCE(notlar,'') || ' | IRSALIYE_MODU' WHERE id = %s",
                    (fatura_id,),
                )
            except Exception:
                pass

        earsiv_payload = {
            "fatura_no": fatura_no,
            "fatura_tarihi": fatura_tarihi,
            "musteri_id": musteri_id,
            "musteri_adi": musteri_adi,
            "fatura_tipi": data.get("fatura_tipi") or "satis",
            "satirlar": satirlar,
            "ara_toplam": ara_toplam,
            "toplam_iskonto": toplam_iskonto,
            "kdv_toplam": kdv_toplam,
            "genel_toplam": toplam,
            "yazi_ile": tutar_yaziya(toplam),
            "irsaliye_modu": irsaliye_modu,
        }

        return jsonify({"ok": True, "mesaj": "Fatura eklendi!", "fatura_id": fatura_id, "earsiv": earsiv_payload})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route('/onizleme/<int:fatura_id>')
@faturalar_gerekli
def fatura_onizleme_ekran(fatura_id):
    """Kaydedilmiş faturanın e-Arşiv PDF önizlemesi ("Fatura Oluştur" sonrası)."""
    ensure_faturalar_amount_columns()
    fatura_row = fetch_one(
        "SELECT id, fatura_no, fatura_tarihi, musteri_id, musteri_adi, tutar, kdv_tutar, toplam, notlar, satirlar_json, sevk_adresi, ettn FROM faturalar WHERE id = %s",
        (fatura_id,),
    )
    if not fatura_row:
        abort(404)
    fatura = dict(fatura_row)
    musteri_id = fatura.get("musteri_id")
    musteri = {"name": fatura.get("musteri_adi"), "address": "", "tax_number": "", "vergi_dairesi": ""}
    if musteri_id:
        cust = fetch_one("SELECT id, name, address, tax_number FROM customers WHERE id = %s", (musteri_id,))
        if cust:
            musteri["name"] = cust.get("name") or musteri["name"]
            musteri["address"] = (cust.get("address") or "").strip()
            musteri["tax_number"] = str(cust.get("tax_number") or "").strip()
        vd_row = fetch_one("SELECT vergi_dairesi FROM customers WHERE id = %s", (musteri_id,))
        if vd_row and vd_row.get("vergi_dairesi"):
            musteri["vergi_dairesi"] = (vd_row.get("vergi_dairesi") or "").strip()
        kyc = fetch_one(
            "SELECT vergi_dairesi, vergi_no, yeni_adres FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
            (musteri_id,),
        )
        if kyc:
            if kyc.get("vergi_dairesi"):
                musteri["vergi_dairesi"] = (kyc.get("vergi_dairesi") or "").strip()
            if kyc.get("vergi_no"):
                musteri["tax_number"] = str(kyc.get("vergi_no") or "").strip()
            if kyc.get("yeni_adres"):
                musteri["address"] = (kyc.get("yeni_adres") or "").strip() or musteri["address"]

    ft = fatura.get("fatura_tarihi")
    if hasattr(ft, "strftime"):
        fatura_tarihi_str = ft.strftime("%d.%m.%Y")
    else:
        s = str(ft or "")[:10]
        if s and len(s) == 10 and s[4] == "-":
            fatura_tarihi_str = f"{s[8:10]}.{s[5:7]}.{s[0:4]}"
        else:
            fatura_tarihi_str = s or "--"
    fatura["fatura_tarihi_str"] = fatura_tarihi_str
    toplam = float(fatura.get("toplam") or 0)

    satirlar = []
    try:
        if fatura.get("satirlar_json"):
            raw = json.loads(fatura["satirlar_json"])
            for s in (raw if isinstance(raw, list) else []):
                miktar = float(s.get("miktar") or 0)
                birim_fiyat = float(s.get("birim_fiyat") or 0)
                isk_oran = float(s.get("iskonto_orani") or 0)
                isk_tutar_giris = s.get("iskonto_tutar")
                kdv_oran = float(s.get("kdv_orani") or 0)
                brut = miktar * birim_fiyat
                if isk_tutar_giris is not None and float(isk_tutar_giris or 0) > 0:
                    isk_tutar = min(float(isk_tutar_giris), brut)
                else:
                    isk_tutar = brut * isk_oran / 100.0
                net = brut - isk_tutar
                kdv_tutar = net * kdv_oran / 100.0
                satirlar.append({
                    "ad": (s.get("ad") or s.get("mal_hizmet") or "Hizmet").strip() or "Hizmet",
                    "miktar": miktar,
                    "birim": (s.get("birim") or "Adet").strip() or "Adet",
                    "birim_fiyat": birim_fiyat,
                    "iskonto_orani": isk_oran,
                    "iskonto_tutar": isk_tutar,
                    "kdv_orani": kdv_oran,
                    "kdv_tutar": kdv_tutar,
                    "mal_tutar": net,
                    "satir_toplam": net + kdv_tutar,
                })
    except Exception:
        pass
    if not satirlar:
        tutar = float(fatura.get("tutar") or 0)
        kdv_tutar = float(fatura.get("kdv_tutar") or 0)
        satirlar = [{
            "ad": "Hizmet",
            "miktar": 1,
            "birim": "Ay",
            "birim_fiyat": tutar,
            "iskonto_orani": 0,
            "iskonto_tutar": 0,
            "kdv_orani": (kdv_tutar / tutar * 100) if tutar else 20,
            "kdv_tutar": kdv_tutar,
            "mal_tutar": tutar,
            "satir_toplam": toplam,
        }]

    ara_toplam = sum(s.get("miktar", 0) * s.get("birim_fiyat", 0) for s in satirlar)
    toplam_iskonto = sum(
        (s.get("iskonto_tutar") if s.get("iskonto_tutar") is not None else (s.get("miktar", 0) * s.get("birim_fiyat", 0) * (s.get("iskonto_orani") or 0) / 100.0))
        for s in satirlar
    )
    kdv_toplam = sum(s.get("kdv_tutar", 0) for s in satirlar)
    ettn = (fatura.get("ettn") or "").strip()
    if (not ettn) and fatura.get("notlar"):
        for part in (fatura.get("notlar") or "").split("|"):
            if "ETTN:" in part or "GİB ETTN:" in part:
                ettn = part.split(":")[-1].strip() or ettn
                break

    irsaliye_modu = False
    if fatura.get("notlar") and "IRSALIYE_MODU" in (fatura.get("notlar") or ""):
        irsaliye_modu = True

    fatura_pdf_dict = {
        "id": fatura.get("id"),
        "fatura_no": fatura.get("fatura_no"),
        "fatura_tarihi": fatura.get("fatura_tarihi"),
        "fatura_tarihi_str": fatura_tarihi_str,
        "fatura_tipi": (fatura.get("fatura_tipi") or "satis"),
        "musteri_adi": fatura.get("musteri_adi"),
        "notlar": fatura.get("notlar"),
        "ara_toplam": ara_toplam,
        "toplam_iskonto": toplam_iskonto,
        "kdv_toplam": kdv_toplam,
        "genel_toplam": toplam,
        "toplam": toplam,
        "ettn": ettn,
        "irsaliye_modu": irsaliye_modu,
        "sevk_adresi": (fatura.get("sevk_adresi") or "").strip() or None,
    }

    pdf_bytes = build_fatura_pdf(fatura_pdf_dict, musteri, satirlar)
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=Fatura_{fatura.get('fatura_no') or fatura_id}.pdf",
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@bp.route('/tahsilat-ekle', methods=['POST'])
@faturalar_gerekli
def tahsilat_ekle():
    """Yeni tahsilat ekle; makbuz no üretir, A5 PDF oluşturup müşteri dosyasına kaydeder."""
    try:
        data = request.get_json()
        musteri_id = data.get('musteri_id')
        musteri_adi = (data.get('musteri_adi') or '').strip()
        musteri_phone = (data.get('musteri_phone') or '').strip()
        if not musteri_id:
            if not musteri_adi:
                return jsonify({'ok': False, 'mesaj': 'Müşteri seçiniz veya ad giriniz.'}), 400
            # Yeni, hızlı müşteri kaydı oluştur (sadece ad + telefon)
            yeni = execute_returning(
                """
                INSERT INTO customers (name, phone, created_at)
                VALUES (%s, %s, NOW())
                RETURNING id, name
                """,
                (musteri_adi, musteri_phone or None),
            )
            musteri_id = (yeni or {}).get('id')
            if not musteri_id:
                return jsonify({'ok': False, 'mesaj': 'Yeni müşteri oluşturulamadı.'}), 500
        tutar = float(data.get('tutar') or 0)
        cek_detay_raw = data.get('cek_detay')
        cek_list = cek_detay_raw if isinstance(cek_detay_raw, list) else []
        odeme_turu = (data.get('odeme_turu') or 'nakit').strip().lower().replace(" ", "_")
        if odeme_turu == 'cek' and cek_list:
            tutar = sum(float(c.get("tutar") or 0) for c in cek_list)
        if tutar <= 0:
            return jsonify({'ok': False, 'mesaj': 'Tutar 0\'dan büyük olmalıdır veya çek satırları giriniz.'}), 400

        raw_tarih = (data.get('tahsilat_tarihi') or "").strip() or datetime.now().strftime("%Y-%m-%d")
        # DD.MM.YYYY veya YYYY-MM-DD -> YYYY-MM-DD
        if re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}$", raw_tarih):
            parts = raw_tarih.split(".")
            tahsilat_tarihi = f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
        else:
            tahsilat_tarihi = raw_tarih[:10] if len(raw_tarih) >= 10 else datetime.now().strftime("%Y-%m-%d")
        yil = datetime.now().year
        if len(str(tahsilat_tarihi)) >= 4:
            try:
                yil = int(str(tahsilat_tarihi)[:4])
            except ValueError:
                pass
        makbuz_no = get_next_makbuz_no(yil)

        cek_detay_str = json.dumps(cek_list) if cek_list else ""
        havale_banka = (data.get('havale_banka') or "").strip()[:200]

        row = execute_returning("""
            INSERT INTO tahsilatlar (
                musteri_id, customer_id, fatura_id, tutar, odeme_turu,
                tahsilat_tarihi, aciklama, makbuz_no, cek_detay, havale_banka
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, fatura_id, makbuz_no, tutar, odeme_turu, tahsilat_tarihi, aciklama, created_at, cek_detay, havale_banka
        """, (
            musteri_id,
            musteri_id,
            data.get('fatura_id') or None,
            tutar,
            odeme_turu,
            tahsilat_tarihi,
            data.get('aciklama') or '',
            makbuz_no,
            cek_detay_str or None,
            havale_banka or None
        ))
        if not row:
            return jsonify({'ok': False, 'mesaj': 'Kayıt oluşturulamadı.'}), 500

        musteri = fetch_one("SELECT name FROM customers WHERE id = %s", (musteri_id,))
        musteri_adi = (musteri or {}).get("name") or musteri_adi or "Müşteri"
        fatura_no = None
        if row.get("fatura_id"):
            f = fetch_one("SELECT fatura_no FROM faturalar WHERE id = %s", (row["fatura_id"],))
            fatura_no = (f or {}).get("fatura_no")

        banka_hesaplar = fetch_all(
            "SELECT banka_adi, hesap_adi, iban FROM banka_hesaplar WHERE COALESCE(is_active::int, 1) = 1 AND (iban IS NOT NULL AND iban != '') ORDER BY banka_adi"
        )
        pdf_bytes = build_makbuz_pdf(row, musteri_adi, fatura_no, banka_hesaplar=banka_hesaplar)
        os.makedirs(UPLOAD_MUSTERI_DOSYALARI, exist_ok=True)
        safe_name = re.sub(r'[^\w\s-]', '', musteri_adi)[:40].strip() or "musteri"
        safe_name = re.sub(r'[-\s]+', '_', safe_name)
        pdf_filename = f"Tahsilat_{makbuz_no}_{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        pdf_path = os.path.join(UPLOAD_MUSTERI_DOSYALARI, pdf_filename)
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

        return jsonify({
            'ok': True,
            'mesaj': 'Tahsilat eklendi. Makbuz PDF müşteri dosyalarına kaydedildi.',
            'tahsilat_id': row['id'],
            'makbuz_no': makbuz_no,
            'pdf_dosya': pdf_filename
        })
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': str(e)}), 500


@bp.route('/tahsilat-pdf/<int:tahsilat_id>')
@faturalar_gerekli
def tahsilat_pdf(tahsilat_id):
    """Tahsilat makbuzu A5 PDF'ini oluşturup döndürür (yazdır / indir)."""
    row = fetch_one("""
        SELECT t.id, t.makbuz_no, t.tutar, t.odeme_turu, t.tahsilat_tarihi, t.aciklama, t.created_at, t.fatura_id,
               t.cek_detay, t.havale_banka, c.name as musteri_adi
        FROM tahsilatlar t
        LEFT JOIN customers c ON COALESCE(t.customer_id, t.musteri_id) = c.id
        WHERE t.id = %s
    """, (tahsilat_id,))
    if not row:
        return jsonify({'ok': False, 'mesaj': 'Tahsilat bulunamadı.'}), 404
    fatura_no = None
    if row.get("fatura_id"):
        f = fetch_one("SELECT fatura_no FROM faturalar WHERE id = %s", (row["fatura_id"],))
        fatura_no = (f or {}).get("fatura_no")
    banka_hesaplar = fetch_all(
        "SELECT banka_adi, hesap_adi, iban FROM banka_hesaplar WHERE COALESCE(is_active::int, 1) = 1 AND (iban IS NOT NULL AND iban != '') ORDER BY banka_adi"
    )
    pdf_bytes = build_makbuz_pdf(row, row.get("musteri_adi"), fatura_no, banka_hesaplar=banka_hesaplar)
    indir = request.args.get("indir", "").lower() in ("1", "true", "yes")
    disposition = "attachment" if indir else "inline"
    return Response(pdf_bytes, mimetype="application/pdf", headers={
        "Content-Disposition": f"{disposition}; filename=Tahsilat_{row.get('makbuz_no', tahsilat_id)}.pdf"
    })


@bp.route('/tahsilat-makbuz-onizle', methods=['POST'])
@faturalar_gerekli
def tahsilat_makbuz_onizle():
    """Form verileriyle tahsilat makbuzu PDF önizlemesi (kaydetmeden)."""
    try:
        data = request.get_json()
        musteri_id = data.get('musteri_id')
        musteri_adi = (data.get('musteri_adi') or '').strip()
        if not musteri_adi and musteri_id:
            m = fetch_one("SELECT name FROM customers WHERE id = %s", (musteri_id,))
            musteri_adi = (m or {}).get("name") or "Müşteri"
        if not musteri_adi:
            musteri_adi = "Müşteri"
        tutar = float(data.get('tutar') or 0)
        odeme_turu = (data.get('odeme_turu') or 'nakit').strip().lower().replace(" ", "_")
        raw_tarih = (data.get('tahsilat_tarihi') or "").strip() or datetime.now().strftime("%Y-%m-%d")
        if re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}$", raw_tarih):
            parts = raw_tarih.split(".")
            tahsilat_tarihi = f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
        else:
            tahsilat_tarihi = raw_tarih[:10] if len(raw_tarih) >= 10 else datetime.now().strftime("%Y-%m-%d")
        aciklama = (data.get('aciklama') or '').strip()
        cek_detay_raw = data.get('cek_detay')
        cek_list = cek_detay_raw if isinstance(cek_detay_raw, list) else []
        havale_banka = (data.get('havale_banka') or '').strip()[:200]
        fake_row = {
            "makbuz_no": "Önizleme",
            "tutar": tutar,
            "odeme_turu": odeme_turu,
            "tahsilat_tarihi": tahsilat_tarihi,
            "aciklama": aciklama,
            "created_at": datetime.now(),
            "cek_detay": cek_list,
            "havale_banka": havale_banka
        }
        banka_hesaplar = fetch_all(
            "SELECT banka_adi, hesap_adi, iban FROM banka_hesaplar WHERE COALESCE(is_active::int, 1) = 1 AND (iban IS NOT NULL AND iban != '') ORDER BY banka_adi"
        )
        pdf_bytes = build_makbuz_pdf(fake_row, musteri_adi, None, banka_hesaplar=banka_hesaplar)
        return Response(pdf_bytes, mimetype="application/pdf", headers={
            "Content-Disposition": "inline; filename=Tahsilat_Onizleme.pdf"
        })
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': str(e)}), 500


# --- GİB e-Arşiv Fatura (taslak + SMS onay) ---
def _extract_gib_fatura_no_from_obj(obj):
    """GİB yanıt/durum sözlüğünden belge/fatura no alanını dayanıklı şekilde çıkar."""
    if not isinstance(obj, dict):
        return ""
    direct_keys = (
        "belgeNumarasi",
        "faturaNo",
        "fatura_no",
        "belge_no",
        "belgeno",
        "belgeNo",
        "invoiceNumber",
    )
    for k in direct_keys:
        v = obj.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    # Anahtar adını normalize edip yeniden dene (büyük/küçük, Türkçe farkları vb.)
    normalized = {}
    for k, v in obj.items():
        nk = re.sub(r"[^a-z0-9]", "", str(k or "").lower())
        normalized[nk] = v
    for nk in ("belgenumarasi", "faturano", "belgeno", "invoicenumber"):
        v = normalized.get(nk)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _extract_gib_ettn_from_obj(obj):
    """GİB yanıt/durum sözlüğünden ETTN/UUID alanını dayanıklı şekilde çıkar."""
    if not isinstance(obj, dict):
        return ""
    for k in ("ettn", "uuid", "faturaUuid", "fatura_uuid"):
        v = obj.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    normalized = {}
    for k, v in obj.items():
        nk = re.sub(r"[^a-z0-9]", "", str(k or "").lower())
        normalized[nk] = v
    for nk in ("ettn", "uuid", "faturauuid"):
        v = normalized.get(nk)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _gib_kayit_bekle(gib, uuid, deneme=10, bekleme_s=1.2):
    """UUID için GİB durum kaydını birkaç kez dener; bulunduğunda döndürür."""
    if not gib or not uuid:
        return {}
    for _ in range(max(1, int(deneme))):
        try:
            st = gib.fatura_durum_getir(uuid, days_back=370) or {}
            if st:
                return st
        except Exception:
            pass
        try:
            import time
            time.sleep(float(bekleme_s))
        except Exception:
            pass
    return {}


def _fatura_gib_bilgilerini_yaz(fatura_id, ettn=None, gib_fatura_no=None):
    """GİB ETTN + GİB Fatura No bilgisini faturaya ve notlara tekil şekilde yazar/günceller."""
    try:
        fid = int(fatura_id)
    except Exception:
        return
    row = fetch_one("SELECT notlar, ettn, fatura_no FROM faturalar WHERE id = %s", (fid,)) or {}
    notlar = str(row.get("notlar") or "")
    ettn_val = (ettn or "").strip() or str(row.get("ettn") or "").strip()
    gib_no_val = (gib_fatura_no or "").strip() or str(row.get("fatura_no") or "").strip()

    # Eski ETTN etiketlerini temizleyip tek bir güncel satır bırak.
    cleaned = re.sub(r"\s*\|\s*G[İI]B\s*ETTN:\s*[^|]*", "", notlar, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s*\|\s*G[İI]B\s*FATURA\s*NO:\s*[^|]*", "", cleaned, flags=re.IGNORECASE).strip()
    tags = []
    if ettn_val:
        tags.append(f"GİB ETTN: {ettn_val}")
    if gib_no_val:
        tags.append(f"GİB FATURA NO: {gib_no_val}")
    yeni = cleaned
    if tags:
        yeni = f"{cleaned} | {' | '.join(tags)}".strip(" |") if cleaned else " | ".join(tags)

    execute(
        "UPDATE faturalar SET notlar = %s, ettn = %s, fatura_no = %s WHERE id = %s",
        (yeni, ettn_val or None, gib_no_val or row.get("fatura_no"), fid),
    )


@bp.route('/api/gib-taslak', methods=['POST'])
@faturalar_gerekli
def api_gib_taslak():
    """Fatura ID ile GİB'de taslak oluşturur. Döner: ok, uuid (ETTN), mesaj."""
    try:
        from gib_earsiv import BestOfficeGIBManager, build_fatura_data_from_db
        def _mask_vkn(v):
            s = str(v or "").strip()
            if len(s) <= 4:
                return s
            return ("*" * (len(s) - 4)) + s[-4:]

        def _payload_debug_text(fd):
            items = fd.get("items") or []
            first = (items[0] if items else {}) or {}
            dbg = {
                "tarih": fd.get("tarih"),
                "saat": fd.get("saat"),
                "vkn_veya_tckn": _mask_vkn(fd.get("vkn")),
                "unvan": fd.get("unvan"),
                "vd": fd.get("vd"),
                "satir_sayisi": len(items),
                "ilk_satir": {
                    "name": first.get("name"),
                    "quantity": first.get("quantity"),
                    "unit_price": first.get("unit_price"),
                    "tax_rate": first.get("tax_rate"),
                    "discount_rate": first.get("discount_rate"),
                    "discount_amount": first.get("discount_amount"),
                },
                "toplam": fd.get("toplam"),
                "kdv_orani_genel": fd.get("kdv_orani"),
            }
            try:
                return json.dumps(dbg, ensure_ascii=False)[:900]
            except Exception:
                return str(dbg)[:900]

        data = request.get_json() or {}
        fatura_id = data.get("fatura_id") or request.form.get("fatura_id")
        if not fatura_id:
            return jsonify({"ok": False, "mesaj": "fatura_id gerekli."}), 400
        fatura_id = int(fatura_id)
        f_row = fetch_one(
            "SELECT musteri_id, fatura_tarihi, satirlar_json, notlar FROM faturalar WHERE id = %s",
            (fatura_id,),
        )
        if not f_row:
            return jsonify({"ok": False, "mesaj": "Fatura bulunamadı."}), 404
        try:
            sj = f_row.get("satirlar_json")
            satirlar_db = json.loads(sj) if sj else []
        except Exception:
            satirlar_db = []
        ft = f_row.get("fatura_tarihi")
        ft_iso = ft.strftime("%Y-%m-%d") if hasattr(ft, "strftime") else str(ft)[:10]
        donemler_gib = _fatura_kira_donemleri_topla(satirlar_db, ft_iso, f_row.get("notlar") or "")
        mid_gib = f_row.get("musteri_id")
        if donemler_gib and mid_gib:
            try:
                mid_i = int(mid_gib)
                for yy, mm in sorted(donemler_gib):
                    dup = _musteri_icin_ayda_baska_fatura(mid_i, yy, mm, exclude_fatura_id=fatura_id)
                    if dup:
                        fn = dup.get("fatura_no") or ""
                        fid = dup.get("id")
                        return jsonify({
                            "ok": False,
                            "mesaj": (
                                f"Bu müşteri için {mm:02d}.{yy} dönemi başka bir fatura kaydıyla zaten kapatılmış "
                                f"({fn or ('#' + str(fid))}). GİB taslağı oluşturulmadı."
                            ),
                        }), 409
            except Exception as ex_gib_dup:
                logging.getLogger(__name__).exception("gib-taslak mükerrer kontrol: %s", ex_gib_dup)

        fatura_data = build_fatura_data_from_db(fatura_id, fetch_one)
        payload_debug = _payload_debug_text(fatura_data)
        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return jsonify({
                "ok": False,
                "mesaj": "GİB modülü kullanılamıyor. .env içinde GIB_USER ve GIB_PASS tanımlayın ve eArsivPortal kurulumunu kontrol edin."
            }), 503
        uuid = gib.fatura_taslak_olustur(fatura_data)
        taslak_raw = getattr(gib, "last_taslak_raw", None)
        gonderilen_payload = getattr(gib, "last_gonderilen_payload", None)
        raw_type = type(taslak_raw).__name__
        try:
            if isinstance(taslak_raw, (dict, list)):
                taslak_raw_text = json.dumps(taslak_raw, ensure_ascii=False)[:700]
            else:
                taslak_raw_text = str(taslak_raw or "")[:700]
        except Exception:
            taslak_raw_text = str(taslak_raw or "")[:700]
        try:
            if isinstance(gonderilen_payload, dict):
                gp = gonderilen_payload
                gmh = (gp.get("malHizmetTable") or [{}])[0]
                gonderilen_dbg = json.dumps({
                    "malHizmetTable0": {
                        "birimFiyat": gmh.get("birimFiyat"),
                        "fiyat": gmh.get("fiyat"),
                        "malHizmetTutari": gmh.get("malHizmetTutari"),
                        "kdvOrani": gmh.get("kdvOrani"),
                        "iskontoOrani": gmh.get("iskontoOrani"),
                        "iskontoTutari": gmh.get("iskontoTutari"),
                        "iskontoNedeni": gmh.get("iskontoNedeni"),
                        "iskontoArttirimOrani": gmh.get("iskontoArttirimOrani"),
                        "iskontoArttirimTutari": gmh.get("iskontoArttirimTutari"),
                        "iskontoArttirimNedeni": gmh.get("iskontoArttirimNedeni"),
                    },
                    "toplamIskonto": gp.get("toplamIskonto"),
                    "matrah": gp.get("matrah"),
                    "odenecekTutar": gp.get("odenecekTutar"),
                }, ensure_ascii=False)[:1000]
            else:
                gonderilen_dbg = ""
        except Exception:
            gonderilen_dbg = ""
        if not uuid:
            return jsonify({
                "ok": False,
                "mesaj": "GİB taslak oluşturulamadı.",
                "taslak_raw": taslak_raw_text,
                "payload_debug": payload_debug,
            }), 500
        # Önce taslak gerçekten portal listesinde bulunmalı; aksi halde SMS adımına
        # geçmek kullanıcıyı gereksiz hataya sokuyor.
        taslak_dogrulandi = None
        taslak_onay_durumu = None
        taslak_kayit = None
        if getattr(gib, "client_type", "") == "earsivportal":
            taslak_dogrulandi = False
            st = _gib_kayit_bekle(gib, uuid, deneme=8, bekleme_s=1.5)
            if st:
                taslak_dogrulandi = True
                taslak_kayit = st
                taslak_onay_durumu = (st.get("onayDurumu") or st.get("durum") or "").strip() or None
            if taslak_dogrulandi is False:
                # Pragmatik mod: doğrulama olmasa da akış dursun istemiyoruz.
                # UUID/ETTN kaydı ile SMS adımına izin ver.
                pass
        oid = None
        sms_error = None
        gib_ettn = _extract_gib_ettn_from_obj(taslak_kayit) or uuid
        gib_fatura_no = (
            _extract_gib_fatura_no_from_obj(taslak_kayit)
            or _extract_gib_fatura_no_from_obj(taslak_raw if isinstance(taslak_raw, dict) else {})
        )
        try:
            _fatura_gib_bilgilerini_yaz(fatura_id, gib_ettn, gib_fatura_no)
        except Exception:
            pass
        msg = "Taslak oluşturuldu."
        if getattr(gib, "client_type", "") == "earsivportal":
            if taslak_dogrulandi is False:
                msg = ("GİB taslak isteği gönderildi fakat portal listesinde henüz görünmedi. "
                       "Yine de UUID kaydedildi; SMS adımına devam edebilirsiniz.")
            else:
                msg = "GİB taslak isteği gönderildi ve portal kaydı doğrulandı."
        elif taslak_onay_durumu:
            msg += f" Durum: {taslak_onay_durumu}."
        # Taslak gerçekten portalda görünüyorsa SMS akışına geç.
        try:
            oid = gib.sms_kodu_gonder(uuid)
            sms_error = getattr(gib, "last_sms_error", None)
        except Exception:
            oid = None
            sms_error = getattr(gib, "last_sms_error", None) or "SMS gönderimi tetiklenemedi."
        if oid:
            msg += " SMS gönderildi, gelen kodu girin."
        else:
            msg += " SMS otomatik tetiklenemedi; 'SMS Gönder' ile tekrar deneyin."
            if sms_error:
                msg += f" Detay: {sms_error}"
        debug_meta = (
            f"[debug client={getattr(gib, 'client_type', '-')}; "
            f"raw_type={raw_type}; raw_len={len(taslak_raw_text or '')}; "
            f"verify={taslak_dogrulandi}; onay={taslak_onay_durumu or '-'}]"
        )
        msg = f"{msg} {debug_meta} | payload={payload_debug}"
        if gonderilen_dbg:
            msg += f" | gonderilen={gonderilen_dbg}"
        return jsonify({
            "ok": True,
            "uuid": gib_ettn,
            "gib_fatura_no": gib_fatura_no,
            "oid": oid,
            "sms_sent": bool(oid),
            "sms_error": sms_error,
            "taslak_dogrulandi": taslak_dogrulandi,
            "taslak_onay_durumu": taslak_onay_durumu,
            "taslak_raw": taslak_raw_text,
            "payload_debug": payload_debug,
            "gonderilen_debug": gonderilen_dbg,
            "mesaj": msg,
        })
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 401
    except Exception as e:
        return jsonify({"ok": False, "mesaj": "GİB hatası: " + str(e)}), 500


@bp.route('/api/gib-sms-onay', methods=['POST'])
@faturalar_gerekli
def api_gib_sms_onay():
    """SMS kodu ile taslak faturayı onaylar. İsteğe bağlı fatura_id ile veritabanında güncelleme yapılabilir."""
    try:
        from gib_earsiv import BestOfficeGIBManager
        data = request.get_json() or {}
        uuid = (data.get("uuid") or request.form.get("uuid") or "").strip()
        sms_kodu = (data.get("sms_kodu") or request.form.get("sms_kodu") or "").strip()
        oid = (data.get("oid") or request.form.get("oid") or "").strip()
        fatura_id = data.get("fatura_id") or request.form.get("fatura_id")
        if not uuid or not sms_kodu:
            return jsonify({"ok": False, "mesaj": "uuid ve sms_kodu gerekli."}), 400
        sms_norm = re.sub(r"\s+", "", sms_kodu).upper()
        # GİB kodu: sadece harf, sadece rakam veya harf+rakam karışık olabilir.
        # Uzunluk sağlayıcıya göre değişebildiği için 4-8 aralığı kabul edilir.
        if not re.fullmatch(r"[A-Z0-9]{4,8}", sms_norm):
            return jsonify({"ok": False, "mesaj": "SMS kodu harf/rakam içermeli (4-8 karakter, örn: B2Z7V5)."}), 400
        sms_kodu = sms_norm
        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return jsonify({"ok": False, "mesaj": "GİB modülü kullanılamıyor."}), 503
        if oid and getattr(gib, "client_type", "") == "earsivportal":
            success = gib.sms_onay_earsivportal(uuid, sms_kodu, oid)
        else:
            success = gib.sms_onay_ve_imzala(uuid, sms_kodu)
        if not success:
            det = getattr(gib, "last_sms_error", None)
            msg = "SMS onayı başarısız veya kod hatalı."
            if det:
                msg += f" Detay: {det}"
            return jsonify({"ok": False, "mesaj": msg}), 400
        if fatura_id:
            try:
                st = _gib_kayit_bekle(gib, uuid, deneme=10, bekleme_s=1.2)
                gib_ettn = _extract_gib_ettn_from_obj(st) or uuid
                gib_fatura_no = _extract_gib_fatura_no_from_obj(st)
                _fatura_gib_bilgilerini_yaz(fatura_id, gib_ettn, gib_fatura_no)
            except Exception:
                pass
        return jsonify({"ok": True, "mesaj": "Fatura GİB üzerinde imzalandı."})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route('/api/gib-sms-gonder', methods=['POST'])
@faturalar_gerekli
def api_gib_sms_gonder():
    """UUID için GİB SMS kodu gönderimini tetikler (eArsivPortal akışı)."""
    try:
        from gib_earsiv import BestOfficeGIBManager
        data = request.get_json() or {}
        uuid = (data.get("uuid") or request.form.get("uuid") or "").strip()
        if not uuid:
            return jsonify({"ok": False, "mesaj": "uuid gerekli."}), 400
        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return jsonify({"ok": False, "mesaj": "GİB modülü kullanılamıyor."}), 503
        if getattr(gib, "client_type", "") == "earsivportal":
            # Pragmatik mod: taslak doğrulaması başarısız olsa da SMS denenebilir.
            # Burada akışı kesmiyoruz.
            pass
        oid = gib.sms_kodu_gonder(uuid)
        if not oid:
            return jsonify({"ok": False, "mesaj": "SMS gönderimi başlatılamadı."}), 500
        return jsonify({"ok": True, "oid": oid, "mesaj": "SMS gönderildi. Telefonunuza gelen kodu girin."})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route('/api/gib-fatura-onizleme')
@faturalar_gerekli
def api_gib_fatura_onizleme():
    """GİB portalındaki gerçek fatura HTML önizlemesini döndürür."""
    try:
        from gib_earsiv import BestOfficeGIBManager
        uuid = (request.args.get("uuid") or "").strip()
        fatura_id = request.args.get("fatura_id", type=int)
        if not uuid and fatura_id:
            row = fetch_one("SELECT ettn FROM faturalar WHERE id = %s", (fatura_id,)) or {}
            uuid = str(row.get("ettn") or "").strip()
        if not uuid:
            return jsonify({"ok": False, "mesaj": "uuid veya fatura_id gerekli."}), 400
        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return jsonify({"ok": False, "mesaj": "GİB modülü kullanılamıyor."}), 503
        html = gib.fatura_html_getir(uuid, days_back=370)
        if not html.strip():
            return jsonify({"ok": False, "mesaj": "GİB önizleme içeriği boş döndü."}), 404
        # GİB HTML'ini bozmadan, sol üst alanı ERP şablonuna daha yakın ve kompakt göster.
        compact_patch = """
<style id="boerp-gib-compact">
  body { font-family: Arial, Helvetica, sans-serif !important; }
  td, th, div, span, p { font-size: 12px !important; line-height: 1.18 !important; }
  b, strong { font-size: 13px !important; }
  table { border-collapse: collapse !important; }
</style>
<script id="boerp-gib-compact-script">
(function(){
  function norm(s){
    return (s||'').toLowerCase()
      .replace(/ı/g,'i').replace(/İ/g,'i').replace(/ş/g,'s').replace(/ğ/g,'g')
      .replace(/ü/g,'u').replace(/ö/g,'o').replace(/ç/g,'c')
      .replace(/\\s+/g,' ').trim();
  }
  function hideNoiseRows(){
    var bad = ['no', 'kapi no', '/ turkiye', 'turkiye', 'web sitesi'];
    document.querySelectorAll('tr, div, p, span, td').forEach(function(el){
      try{
        var t = norm(el.textContent || '');
        if (!t) return;
        if (bad.indexOf(t) >= 0 || t.startsWith('kapi no') || t.startsWith('web sitesi') || t === 'no') {
          el.style.display = 'none';
          if (el.parentElement && (el.parentElement.tagName === 'TR' || el.parentElement.tagName === 'TABLE')) {
            el.parentElement.style.display = 'none';
          }
        }
      }catch(e){}
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', hideNoiseRows);
  } else {
    hideNoiseRows();
  }
})();
</script>
"""
        if "</head>" in html:
            html = html.replace("</head>", compact_patch + "</head>", 1)
        elif "</body>" in html:
            html = html.replace("</body>", compact_patch + "</body>", 1)
        else:
            html = compact_patch + html
        return Response(html, mimetype="text/html; charset=utf-8")
    except Exception as e:
        err = str(e or "")
        # Bazı eArsivPortal sürümlerinde fatura_html içinde private login method adı değiştiği için
        # '_eArsivPortal__giris_yap' hatası alınabiliyor. Kullanıcı akışını kırmamak için ERP önizlemeye düş.
        if "_eArsivPortal__giris_yap" in err:
            fatura_id_fb = request.args.get("fatura_id", type=int)
            if fatura_id_fb:
                return redirect(url_for("faturalar.fatura_onizleme_ekran", fatura_id=fatura_id_fb))
        return jsonify({"ok": False, "mesaj": "GİB önizleme hatası: " + err}), 500


@bp.route('/api/gib-fatura-olustur', methods=['POST'])
@faturalar_gerekli
def api_gib_fatura_olustur():
    """SMS onayı sonrası faturayı gerçek ETTN ile kesinleştirir."""
    try:
        from gib_earsiv import BestOfficeGIBManager
        data = request.get_json() or {}
        fatura_id = data.get("fatura_id")
        uuid = (data.get("uuid") or "").strip()
        if not fatura_id:
            return jsonify({"ok": False, "mesaj": "fatura_id gerekli."}), 400
        if not uuid:
            return jsonify({"ok": False, "mesaj": "uuid gerekli."}), 400
        fid = int(fatura_id)
        gib = BestOfficeGIBManager()
        if gib.is_available() and getattr(gib, "client_type", "") == "earsivportal":
            st = gib.fatura_durum_getir(uuid, days_back=370) or {}
            onay = str(st.get("onayDurumu") or "")
            if onay and ("onaylan" not in onay.lower()):
                return jsonify({"ok": False, "mesaj": f"GİB onayı bekleniyor ({onay}). Önce SMS onayını tamamlayın."}), 400
            gib_ettn = _extract_gib_ettn_from_obj(st) or uuid
            gib_fatura_no = _extract_gib_fatura_no_from_obj(st)
            _fatura_gib_bilgilerini_yaz(fid, gib_ettn, gib_fatura_no)
        else:
            _fatura_gib_bilgilerini_yaz(fid, uuid, None)
        return jsonify({"ok": True, "mesaj": "Fatura gerçek ETTN ile oluşturuldu/kesinleşti.", "fatura_id": fid, "onizleme_url": f"/faturalar/onizleme/{fid}"})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route('/api/gib-son-fatura')
@faturalar_gerekli
def api_gib_son_fatura():
    """GİB manuel gönderim alanı için son oluşturulan fatura kimliğini döndürür."""
    row = fetch_one(
        """
        SELECT id, fatura_no, musteri_id, musteri_adi, fatura_tarihi, toplam
        FROM faturalar
        ORDER BY id DESC
        LIMIT 1
        """
    ) or {}
    if not row:
        return jsonify({"ok": False, "mesaj": "Fatura bulunamadı."}), 404
    return jsonify({"ok": True, "fatura": _row_serializable(row)})


@bp.route('/api/auto-fatura/status')
@faturalar_gerekli
def api_auto_fatura_status():
    ensure_auto_invoice_tables()
    settings = _auto_inv_settings()
    last_run = fetch_one("SELECT * FROM auto_invoice_runs ORDER BY id DESC LIMIT 1") or {}
    recent_errors = fetch_all(
        """
        SELECT i.id, i.created_at, i.period_key, i.status, i.error_message,
               i.musteri_id, COALESCE(c.name, '—') AS musteri_adi
        FROM auto_invoice_items i
        LEFT JOIN customers c ON c.id = i.musteri_id
        WHERE i.status IN ('error', 'gib_fail', 'gib_sms_fail')
           OR COALESCE(i.error_message, '') <> ''
        ORDER BY i.id DESC
        LIMIT 20
        """
    ) or []
    return jsonify({
        "ok": True,
        "settings": settings,
        "last_run": _row_serializable(last_run) if last_run else None,
        "recent_errors": [_row_serializable(r) for r in recent_errors],
    })


@bp.route('/api/auto-fatura/settings', methods=['POST'])
@faturalar_gerekli
def api_auto_fatura_settings():
    ensure_auto_invoice_tables()
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    run_day = int(data.get("run_day") or 1)
    run_hour = int(data.get("run_hour") or 9)
    send_gib = bool(data.get("send_gib"))
    auto_sms_code = (data.get("auto_sms_code") or "").strip()
    run_day = min(28, max(1, run_day))
    run_hour = min(23, max(0, run_hour))
    row = fetch_one("SELECT id FROM auto_invoice_settings ORDER BY id LIMIT 1")
    if row:
        execute(
            """UPDATE auto_invoice_settings
               SET enabled=%s, run_day=%s, run_hour=%s, send_gib=%s, auto_sms_code=%s, updated_at=NOW()
               WHERE id=%s""",
            (enabled, run_day, run_hour, send_gib, auto_sms_code or None, row["id"]),
        )
    else:
        execute(
            """INSERT INTO auto_invoice_settings (enabled, run_day, run_hour, send_gib, auto_sms_code)
               VALUES (%s,%s,%s,%s,%s)""",
            (enabled, run_day, run_hour, send_gib, auto_sms_code or None),
        )
    return jsonify({"ok": True, "mesaj": "Otomatik fatura ayarları güncellendi.", "settings": _auto_inv_settings()})


@bp.route('/api/auto-fatura/run', methods=['POST'])
@faturalar_gerekli
def api_auto_fatura_run():
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", True))
    out = run_auto_invoice_cycle(force=force)
    return jsonify(out)


def _parse_auto_year_month(yil_raw, ay_raw):
    try:
        yil = int(yil_raw)
    except Exception:
        yil = date.today().year
    yil = max(2000, min(2100, yil))
    ay_ad = (ay_raw or "").strip()
    ay_no = None
    if ay_ad:
        try:
            ay_no = AYLAR.index(ay_ad) + 1
        except ValueError:
            ay_no = None
    if not ay_no:
        ay_no = date.today().month
    return yil, ay_no


@bp.route('/api/auto-fatura/adaylar')
@faturalar_gerekli
def api_auto_fatura_adaylar():
    yil, ay_no = _parse_auto_year_month(request.args.get("yil"), request.args.get("ay"))
    run_month_date = date(yil, ay_no, 1)
    period_key = run_month_date.strftime("%Y-%m")
    marker_like = f"%|AUTO_INV|{period_key}|%"
    rows = fetch_all(
        """
        WITH last_kyc AS (
            SELECT DISTINCT ON (k.musteri_id) k.musteri_id, k.aylik_kira
            FROM musteri_kyc k
            ORDER BY k.musteri_id, k.id DESC
        ),
        last_inv AS (
            SELECT DISTINCT ON (f.musteri_id) f.musteri_id, f.toplam
            FROM faturalar f
            WHERE COALESCE(f.toplam, 0) > 0
            ORDER BY f.musteri_id, (f.fatura_tarihi::date) DESC, f.id DESC
        )
        SELECT c.id AS musteri_id,
               COALESCE(NULLIF(TRIM(c.name), ''), 'Müşteri') AS musteri_adi,
               ROUND(
                   COALESCE(
                       NULLIF(COALESCE(lk.aylik_kira, 0), 0) * 1.2,
                       NULLIF(COALESCE(li.toplam, 0), 0),
                       NULLIF(COALESCE(c.ilk_kira_bedeli, 0), 0) * 1.2,
                       0
                   )::numeric,
                   2
               ) AS toplam
        FROM customers c
        LEFT JOIN last_kyc lk ON lk.musteri_id = c.id
        LEFT JOIN last_inv li ON (li.musteri_id::text = c.id::text)
        WHERE LOWER(COALESCE(c.durum, 'aktif')) != 'pasif'
          AND NOT EXISTS (
              SELECT 1
              FROM faturalar fx
              WHERE fx.musteri_id::text = c.id::text
                AND COALESCE(fx.notlar, '') LIKE %s
          )
        ORDER BY c.name
        """,
        (marker_like,),
    ) or []
    adaylar = [_row_serializable(r) for r in rows]

    return jsonify({
        "ok": True,
        "period_key": period_key,
        "adaylar": adaylar,
        "count": len(adaylar),
    })


@bp.route('/api/auto-fatura/secili-olustur', methods=['POST'])
@faturalar_gerekli
def api_auto_fatura_secili_olustur():
    data = request.get_json(silent=True) or {}
    yil, ay_no = _parse_auto_year_month(data.get("yil"), data.get("ay"))
    run_month_date = date(yil, ay_no, 1)
    raw_ids = data.get("musteri_ids") or []
    musteri_ids = []
    for v in raw_ids:
        try:
            iv = int(v)
            if iv > 0:
                musteri_ids.append(iv)
        except Exception:
            continue
    musteri_ids = list(dict.fromkeys(musteri_ids))
    if not musteri_ids:
        return jsonify({"ok": False, "mesaj": "En az 1 müşteri seçiniz."}), 400

    created_count = 0
    exists_count = 0
    skip_count = 0
    fail_count = 0
    detaylar = []
    for mid in musteri_ids:
        try:
            out = _auto_invoice_create_for_customer(mid, run_month_date)
            st = (out.get("status") or "").lower()
            if st == "created":
                created_count += 1
            elif st == "exists":
                exists_count += 1
            elif st == "skip":
                skip_count += 1
            else:
                fail_count += 1
            detaylar.append({
                "musteri_id": mid,
                "status": st or "error",
                "fatura_id": out.get("fatura_id"),
                "fatura_no": out.get("fatura_no"),
                "mesaj": out.get("error") or "",
            })
        except Exception as e:
            fail_count += 1
            detaylar.append({
                "musteri_id": mid,
                "status": "error",
                "mesaj": str(e),
            })

    return jsonify({
        "ok": True,
        "period_key": run_month_date.strftime("%Y-%m"),
        "created_count": created_count,
        "exists_count": exists_count,
        "skip_count": skip_count,
        "fail_count": fail_count,
        "detaylar": detaylar,
    })


@bp.route('/cron/auto-fatura')
def cron_auto_fatura():
    """Dış cron için açık endpoint (opsiyonel). ?token=CRON_TOKEN doğrulaması yapar."""
    token_cfg = (os.getenv("CRON_TOKEN") or "").strip()
    token_q = (request.args.get("token") or "").strip()
    if token_cfg and token_q != token_cfg:
        return jsonify({"ok": False, "mesaj": "Yetkisiz."}), 401
    out = run_auto_invoice_cycle(force=False)
    return jsonify(out)


@bp.route('/api/musteriler')
@faturalar_gerekli
def api_musteriler_list():
    """Tahsilat formu için müşteri listesi (WhatsApp için phone döner)."""
    arama = (request.args.get('q') or '').strip()
    base = "SELECT id, name, musteri_adi, phone, tax_number FROM customers "
    if not arama:
        rows = fetch_all(base + "ORDER BY name LIMIT 300")
    else:
        w = customers_arama_sql_3_plus_tax()
        rows = fetch_all(
            base + f"WHERE {w} ORDER BY name LIMIT 300",
            customers_arama_params_5(arama),
        )
    return jsonify(
        [
            {
                "id": r["id"],
                "name": r["name"],
                "musteri_adi": r.get("musteri_adi") or "",
                "phone": (r.get("phone") or ""),
            }
            for r in rows
        ]
    )


@bp.route('/api/banka-hesaplar')
@faturalar_gerekli
def api_banka_hesaplar():
    """Tahsilat formu için havale banka seçimi - TL IBAN hesaplar."""
    rows = fetch_all(
        "SELECT id, banka_adi, hesap_adi, iban FROM banka_hesaplar WHERE COALESCE(is_active::int, 1) = 1 ORDER BY banka_adi"
    )
    return jsonify([{
        "id": r["id"],
        "banka_adi": r.get("banka_adi") or "",
        "hesap_adi": r.get("hesap_adi") or "",
        "iban": r.get("iban") or "",
        "label": (r.get("banka_adi") or "") + " - " + (r.get("hesap_adi") or r.get("iban") or "")[:20]
    } for r in rows])