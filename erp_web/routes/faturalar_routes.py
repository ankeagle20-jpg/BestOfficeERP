from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, send_file, Response, abort
from flask_login import login_required, current_user
from functools import wraps
from datetime import datetime, date
from db import fetch_all, fetch_one, execute, execute_returning, ensure_faturalar_amount_columns
from utils.text_utils import turkish_lower
from utils.musteri_arama import customers_arama_sql_3_plus_tax, customers_arama_params_4
import os
import io
import re
import json
import uuid
from urllib.parse import urlencode
from reportlab.lib.pagesizes import A5, A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

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


def _resolve_gib_logo_path():
    """GİB logosu: cwd + __file__ tabanlı adaylar; sunucu konsoluna exists logu."""
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
    for p in candidates:
        if not p or p in seen:
            continue
        seen.add(p)
        ex = os.path.isfile(p)
        print(f"[fatura PDF] GIB logo yolu: {p} exists={ex}")
        if ex:
            return p
    print("[fatura PDF] GIB logo bulunamadı (tüm adaylar denendi).")
    return None


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


def build_fatura_pdf(fatura, musteri, satirlar):
    """e-Arşiv tarzı A4 fatura / e-irsaliye PDF (önizleme için)."""
    _register_arial()
    font_name = "Arial" if "Arial" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    font_bold = "Arial-Bold" if "Arial-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold"
    buf = io.BytesIO()
    w_pt, h_pt = A4
    irsaliye_modu = _pdf_irsaliye_modu(fatura)
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("İrsaliye Önizleme" if irsaliye_modu else "Fatura Önizleme")

    # Kenarlar
    margin = 15 * mm
    right = (A4_W_MM - 15) * mm

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

    def _draw_labeled_line(x_pt, y_pt, label, value, label_w_pt, value_max_w_pt=None):
        c.setFont(font_name, 9)
        c.drawString(x_pt, y_pt, (label or "")[:30])
        if value_max_w_pt:
            v_lines = _wrap_text(value, value_max_w_pt, font_name, 9)
            c.drawString(x_pt + label_w_pt, y_pt, (v_lines[0] if v_lines else "")[:95])
        else:
            c.drawString(x_pt + label_w_pt, y_pt, (value or "")[:95])

    company = (fatura.get("current_user_company") or fatura.get("company") or {}) if isinstance(fatura, dict) else {}
    sender_unvan = (company.get("unvan") or FIRMA_UNVAN or "").strip()
    sender_adres = (company.get("adres") or FIRMA_ADRES or "").strip()
    sender_tel = (company.get("telefon") or FIRMA_TELEFON or "").strip() or "—"
    sender_web = (company.get("web") or FIRMA_WEB or "").strip() or "—"
    sender_vd = (company.get("vergi_dairesi") or FIRMA_VERGI_DAIRESI or "").strip() or "—"
    sender_vkn = (company.get("vkn") or FIRMA_VERGI_NO or "").strip() or "—"

    client = (fatura.get("active_invoice_client") or fatura.get("client") or {}) if isinstance(fatura, dict) else {}
    # mevcut musteri dict'i ile birleştir (varsa client öncelikli)
    must = {}
    must.update(musteri or {})
    must.update(client or {})
    alici_unvan = (must.get("unvan") or must.get("sirket_unvani") or must.get("name") or fatura.get("musteri_adi") or "Müşteri").strip()
    alici_adres = (must.get("yeni_adres") or must.get("address") or "").strip() or "—"
    sevk_adresi_pdf = (
        str(fatura.get("sevk_adresi") or fatura.get("sevk_adres") or must.get("sevk_adresi") or "")
        .replace("\r\n", "\n")
        .strip()
    )
    alici_vd = (must.get("vergi_dairesi") or "—").strip()
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
    ar = 1.0
    if gib_path:
        try:
            from reportlab.lib.utils import ImageReader

            iw, ih = ImageReader(gib_path).getSize()
            ar = iw / float(ih or 1)
        except Exception:
            ar = 1.0
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
            print(f"[fatura PDF] QR PIL (v1, M), veri uzunluğu={len(qr_data)}")
        except Exception as e_v1:
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
            print(f"[fatura PDF] QR PIL (auto, M), veri uzunluğu={len(qr_data)}")
    except Exception as e_qr:
        print(f"[fatura PDF] QR üretilemedi: {e_qr}")
        qr_pil_image = None

    # Tarih: üst çift çizginin 3 mm üstü, unvan ile aynı sol hiza (overlay’de tekrar basılır)
    date_header_y = sender_top + 3 * mm
    c.setFont(font_name, 9)
    c.drawString(left_x, date_header_y, ust_tarih)

    _double_hr_segment(left_x, left_x + left_col_w, sender_top)
    sender_y = sender_top - 12

    # Sol sütun: şirket bilgileri (wrap'li)
    c.setFont(font_bold, 10.5)
    for ln in _wrap_text(sender_unvan, left_col_w, font_bold, 10.5)[:2]:
        c.drawString(left_x, sender_y, ln[:110])
        sender_y -= 12
    c.setFont(font_name, 9)
    addr_lines = []
    for raw_ln in sender_adres.split("\\n"):
        raw_ln = (raw_ln or "").strip()
        if not raw_ln:
            continue
        addr_lines.extend(_wrap_text(raw_ln, left_col_w, font_name, 9))
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
    c.setFont(font_bold, 10.5)
    c.drawString(margin, y_pt, "SAYIN")
    y_pt -= 12
    c.setFont(font_bold, 10)
    c.drawString(margin, y_pt, alici_unvan[:95])
    y_pt -= 12
    c.setFont(font_name, 9)
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
            sevk_lines.extend(_wrap_text(raw_ln, wrap_max, font_name, 9))
        if sevk_lines:
            sayin_hr_lower_y = sayin_bottom_hr_top - _dhr_gap_pt
            y_sevk_open_top = sayin_hr_lower_y - BLOCK_GAP_BELOW_DOUBLE_PT
            _double_hr_segment(left_x, left_x + left_col_w, y_sevk_open_top)
            y_sevk_title = y_sevk_open_top - SAYIN_TOP_PAD_BELOW_DOUBLE_PT
            c.setFont(font_bold, 9)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(left_x, y_sevk_title, "SEVK ADRESİ:")
            last_txt_baseline = y_sevk_title
            c.setFont(font_name, 9)
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
    ettn_fs = 9
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
    header_row_h = 28   # pt, 2 satırlı başlıklar (İsk./Artt. Oranı vb.) üst/alt sınırlara değmesin
    row_h = 11          # pt, veri + boş satırlar
    cell_pad_vert = 3   # pt, satır altı boşluk
    data_text_offset = 0.5 * mm
    summary_row_h = 11   # pt, özet kutusu satırları
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
    # Sütun oranları (toplam 180): Sıra No ve İsk./Artt., KDV başlıkları hücreye sığsın
    w_ratios = [8, 46, 11, 11, 18, 13, 21, 12, 20, 20]
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
    c.setFont(font_name, 9)
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

    # Logo + alt başlık + QR + tarih: en son (z-order); tarih/ başlık kaybolmasın diye tekrar
    def _flush_gib_overlay():
        c.saveState()
        try:
            c.setFillColorRGB(0, 0, 0)
            c.setFont(font_name, 9)
            c.drawString(left_x, date_header_y, ust_tarih)
            if gib_path and os.path.isfile(gib_path):
                try:
                    c.drawImage(
                        gib_path,
                        logo_ll_x,
                        logo_ll_y,
                        width=logo_draw_w,
                        height=logo_h,
                        preserveAspectRatio=True,
                        mask="auto",
                    )
                    print(f"[fatura PDF] GIB logo drawImage tamam: {gib_path}")
                except Exception as ex_logo:
                    print(f"[fatura PDF] GIB logo drawImage hata: {ex_logo}")
            else:
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
                    print("[fatura PDF] QR drawInlineImage tamam")
                except Exception as ex_qr:
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
                        print("[fatura PDF] QR drawImage (PNG buffer) tamam")
                    except Exception as ex2:
                        print(f"[fatura PDF] QR drawImage yedek hata: {ex2}")
            else:
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
        # "Mal Hizmet" sütunu: wrap + satır yüksekliği metne göre artsın (width sabit)
        mal_col_idx = 1
        mal_col_w = widths[mal_col_idx]
        mal_max_w = max(10, mal_col_w - (pad * 2))
        mal_lines = _wrap_cell_text(mal_hizmet_text, mal_max_w, font_name, 9)
        line_h = 10  # pt
        min_row_h_pt = row_h
        current_row_h = max(min_row_h_pt, (len(mal_lines) * line_h) + 6)  # üst/alt boşluk

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
                # Çok satırlı "Mal Hizmet"
                y_txt = row_top - 4  # pt
                for ln in mal_lines:
                    c.drawString(x + pad, y_txt, ln[:120])
                    y_txt -= line_h
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
    _table_bottom_min = (
        8 * mm + _yalniz_h_pre + GAP_SUMMARY_TO_YALNIZ + _box_h_sm + GAP_TABLE_TO_SUMMARY
    )
    MIN_EMPTY_GRID_ROWS = 22
    empty_row_h = float(row_h)
    c.saveState()
    c.setLineWidth(0.5)
    try:
        for _ in range(MIN_EMPTY_GRID_ROWS):
            if y_line - empty_row_h < 50 * mm:
                break
            if y_line - empty_row_h < _table_bottom_min:
                break
            ny = y_line - empty_row_h
            c.line(margin, ny, table_right, ny)
            y_line = ny
        while y_line - empty_row_h >= _table_bottom_min and y_line - empty_row_h >= 50 * mm:
            ny = y_line - empty_row_h
            c.line(margin, ny, table_right, ny)
            y_line = ny
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
    summary_top = table_bottom - GAP_TABLE_TO_SUMMARY
    box_y = summary_top - box_h
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
                ("FONTSIZE", (0, 0), (-1, -1), 9),
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
    yalniz_font_size = 9
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


@bp.route('/')
@bp.route('/finans')
@faturalar_gerekli
def index():
    """Finans ana sayfası - Faturalar ve Tahsilatlar sekmeleri (/faturalar/ ve /faturalar/finans)"""
    return render_template('faturalar/finans.html')


@bp.route('/yeni')
@faturalar_gerekli
def yeni_fatura():
    """Yeni fatura oluşturma ekranı. ?musteri_id= ile gelirse seçili müşteri bilgileri forma doldurulur."""
    now = datetime.now()
    secili_musteri = None
    default_hizmet_urun = ""
    default_birim_fiyat = 0
    musteri_id = request.args.get('musteri_id', type=int)
    if musteri_id:
        cust = fetch_one(
            "SELECT id, name, address, tax_number FROM customers WHERE id = %s",
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
            secili_musteri = {
                "id": cust["id"],
                "name": cust.get("name") or "",
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
    return render_template('faturalar/yeni_fatura.html',
                           bugun=now.strftime("%Y-%m-%d"),
                           saat=now.strftime("%H:%M"),
                           secili_musteri=_row_serializable(secili_musteri) if secili_musteri else None,
                           default_hizmet_urun=default_hizmet_urun,
                           default_birim_fiyat=default_birim_fiyat)


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
            "SELECT id, name, address, tax_number FROM customers WHERE id = %s",
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
            secili_musteri = {
                "id": cust["id"],
                "name": cust.get("name") or "",
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
        irsaliye_modu=True,
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
    
    ofisler = fetch_all("SELECT DISTINCT code FROM offices WHERE COALESCE(is_active::int, 1) = 1 ORDER BY code")
    
    sql = """
        SELECT f.*, c.name as musteri_adi
        FROM faturalar f
        LEFT JOIN customers c ON CAST(f.musteri_id AS INTEGER) = c.id
        WHERE (f.fatura_tarihi::date) >= %s AND (f.fatura_tarihi::date) <= %s
    """
    params = [baslangic, bitis]
    
    if ofis_kodu:
        sql += " AND f.ofis_kodu = %s"
        params.append(ofis_kodu)
    
    sql += " ORDER BY (f.fatura_tarihi::date) DESC"
    
    faturalar_raw = fetch_all(sql, tuple(params))
    faturalar = [_row_serializable(f) for f in (faturalar_raw or [])]
    
    toplam_tutar = sum(f.get('toplam') or 0 for f in faturalar)
    toplam_odenen = sum(f.get('toplam') or 0 for f in faturalar if f.get('durum') == 'odendi')
    toplam_kalan = toplam_tutar - toplam_odenen
    
    ofisler_list = list(ofisler or [])
    ofisler = [_row_serializable(o) for o in ofisler_list]
    yillar = list(range(today.year, today.year - 6, -1))
    musteriler = fetch_all("SELECT id, name FROM customers ORDER BY name LIMIT 500")
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
        ensure_faturalar_amount_columns()
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
        fatura = {
            "fatura_no": data.get("fatura_no") or _next_fatura_no(),
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
        pdf_bytes = build_fatura_pdf(fatura, musteri, satirlar)
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
        "SELECT id, fatura_no, fatura_tarihi, musteri_id, musteri_adi, tutar, kdv_tutar, toplam, notlar, satirlar_json, sevk_adresi FROM faturalar WHERE id = %s",
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
    ettn = ""
    if fatura.get("notlar"):
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
@bp.route('/api/gib-taslak', methods=['POST'])
@faturalar_gerekli
def api_gib_taslak():
    """Fatura ID ile GİB'de taslak oluşturur. Döner: ok, uuid (ETTN), mesaj."""
    try:
        from gib_earsiv import BestOfficeGIBManager, build_fatura_data_from_db
        data = request.get_json() or {}
        fatura_id = data.get("fatura_id") or request.form.get("fatura_id")
        if not fatura_id:
            return jsonify({"ok": False, "mesaj": "fatura_id gerekli."}), 400
        fatura_id = int(fatura_id)
        fatura_data = build_fatura_data_from_db(fatura_id, fetch_one)
        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return jsonify({
                "ok": False,
                "mesaj": "GİB modülü kullanılamıyor. .env içinde GIB_USER ve GIB_PASS tanımlayın ve 'fatura' kütüphanesini yükleyin."
            }), 503
        uuid = gib.fatura_taslak_olustur(fatura_data)
        if not uuid:
            return jsonify({"ok": False, "mesaj": "GİB taslak oluşturulamadı."}), 500
        return jsonify({"ok": True, "uuid": uuid, "mesaj": "Taslak oluşturuldu. SMS ile gelen kodu girin."})
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
        fatura_id = data.get("fatura_id") or request.form.get("fatura_id")
        if not uuid or not sms_kodu:
            return jsonify({"ok": False, "mesaj": "uuid ve sms_kodu gerekli."}), 400
        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return jsonify({"ok": False, "mesaj": "GİB modülü kullanılamıyor."}), 503
        success = gib.sms_onay_ve_imzala(uuid, sms_kodu)
        if not success:
            return jsonify({"ok": False, "mesaj": "SMS onayı başarısız veya kod hatalı."}), 400
        if fatura_id:
            try:
                execute(
                    "UPDATE faturalar SET notlar = COALESCE(notlar,'') || ' | GİB ETTN: ' || %s WHERE id = %s",
                    (uuid, int(fatura_id))
                )
            except Exception:
                pass
        return jsonify({"ok": True, "mesaj": "Fatura GİB üzerinde imzalandı."})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route('/api/musteriler')
@faturalar_gerekli
def api_musteriler_list():
    """Tahsilat formu için müşteri listesi (WhatsApp için phone döner)."""
    arama = (request.args.get('q') or '').strip()
    base = "SELECT id, name, phone, tax_number FROM customers "
    if not arama:
        rows = fetch_all(base + "ORDER BY name LIMIT 300")
    else:
        w = customers_arama_sql_3_plus_tax()
        rows = fetch_all(
            base + f"WHERE {w} ORDER BY name LIMIT 300",
            customers_arama_params_4(arama),
        )
    return jsonify([{"id": r["id"], "name": r["name"], "phone": (r.get("phone") or "")} for r in rows])


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