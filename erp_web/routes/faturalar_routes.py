from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, send_file, Response
from flask_login import login_required, current_user
from functools import wraps
from datetime import datetime
from db import fetch_all, fetch_one, execute, execute_returning
from utils.text_utils import turkish_lower
import os
import io
import re
import json
from reportlab.lib.pagesizes import A5, A4
from reportlab.lib.units import mm
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

bp = Blueprint('faturalar', __name__, url_prefix='/faturalar')

# Tahsilat makbuzu firma bilgileri (.env'den FIRMA_VERGI_DAIRESI, FIRMA_VERGI_NO eklenebilir)
FIRMA_UNVAN = os.environ.get("FIRMA_UNVAN", "OFİSBİR OFİS VE DANIŞMANLIK HİZMETLERİ ANONİM ŞİRKETİ")
FIRMA_ADRES = os.environ.get("FIRMA_ADRES", "KAVAKLIDERE MAH. ESAT CADDESİ NO:12 KAPI NO:1 ÇANKAYA / Ankara / Türkiye")
FIRMA_TELEFON = os.environ.get("FIRMA_TELEFON", "0 (312) 000 00 00")
FIRMA_WEB = os.environ.get("FIRMA_WEB", "www.ofisbir.com.tr")
FIRMA_VERGI_DAIRESI = os.environ.get("FIRMA_VERGI_DAIRESI", "Büyük Mükellefler VD")
FIRMA_VERGI_NO = os.environ.get("FIRMA_VERGI_NO", "1234567890")
UPLOAD_MUSTERI_DOSYALARI = "uploads/musteri_dosyalari"

AYLAR = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 
         'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']


def tutar_yaziya(tutar):
    """Tutarı Türkçe yazıya çevirir (örn: 15000 -> 'On Beş Bin Türk Lirası')."""
    if tutar is None or tutar == 0:
        return "Sıfır Türk Lirası"
    try:
        t = float(tutar)
    except (TypeError, ValueError):
        return str(tutar) + " Türk Lirası"
    birler = ["", "Bir", "İki", "Üç", "Dört", "Beş", "Altı", "Yedi", "Sekiz", "Dokuz"]
    onlar = ["", "On", "Yirmi", "Otuz", "Kırk", "Elli", "Altmış", "Yetmiş", "Seksen", "Doksan"]
    yuzler = ["", "Yüz", "İki Yüz", "Üç Yüz", "Dört Yüz", "Beş Yüz", "Altı Yüz", "Yedi Yüz", "Sekiz Yüz", "Dokuz Yüz"]
    binler = ["", "Bin", "İki Bin", "Üç Bin", "Dört Bin", "Beş Bin", "Altı Bin", "Yedi Bin", "Sekiz Bin", "Dokuz Bin"]
    onbinler = ["", "On", "Yirmi", "Otuz", "Kırk", "Elli", "Altmış", "Yetmiş", "Seksen", "Doksan"]
    yuzbinler = ["", "Yüz", "İki Yüz", "Üç Yüz", "Dört Yüz", "Beş Yüz", "Altı Yüz", "Yedi Yüz", "Sekiz Yüz", "Dokuz Yüz"]
    milyonlar = ["", "Bir Milyon", "İki Milyon", "Üç Milyon", "Dört Milyon", "Beş Milyon", "Altı Milyon", "Yedi Milyon", "Sekiz Milyon", "Dokuz Milyon"]
    onmilyonlar = ["", "On", "Yirmi", "Otuz", "Kırk", "Elli", "Altmış", "Yetmiş", "Seksen", "Doksan"]

    def uce_bol(s):
        s = str(int(s))
        return s.zfill((len(s) + 2) // 3 * 3)

    k = int(t)
    kuruş = round((t - k) * 100)
    if k == 0:
        yazi = "Sıfır"
    else:
        s = uce_bol(k)
        gruplar = [s[i:i+3] for i in range(0, len(s), 3)]
        parcalar = []
        if len(gruplar) >= 3:
            g = gruplar[-3]
            if g != "000":
                y = int(g[0])
                o = int(g[1])
                b = int(g[2])
                parca = (yuzbinler[y] + " " + onbinler[o] + " " + binler[b] if b and (y or o) else (yuzbinler[y] + " " + onbinler[o] + " " + birler[b]) if o or y else birler[b]).strip()
                if parca == "Bir":
                    parca = "Bin"
                parcalar.append(parca + " Bin" if parca and parca != "Bin" else "Bin")
        if len(gruplar) >= 2:
            g = gruplar[-2]
            if g != "000":
                y, o, b = int(g[0]), int(g[1]), int(g[2])
                parca = (yuzler[y] + " " + onlar[o] + " " + birler[b]).strip()
                parcalar.append(parca)
        if len(gruplar) >= 1:
            g = gruplar[-1]
            if g != "000":
                y, o, b = int(g[0]), int(g[1]), int(g[2])
                parca = (yuzler[y] + " " + onlar[o] + " " + birler[b]).strip()
                parcalar.append(parca)
        yazi = " ".join(parcalar).replace("  ", " ").strip()
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


def build_fatura_pdf(fatura, musteri, satirlar):
    """e-Arşiv tarzı A4 fatura PDF (önizleme için)."""
    _register_arial()
    font_name = "Arial" if "Arial" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    font_bold = "Arial-Bold" if "Arial-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold"
    buf = io.BytesIO()
    w_pt, h_pt = A4
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Fatura Önizleme")

    # Kenarlar
    margin = 15 * mm
    right = (A4_W_MM - 15) * mm

    # Tarih / Saat (sol üst)
    now = datetime.now()
    ust_tarih = fatura.get("fatura_tarihi_str") or now.strftime("%d.%m.%Y %H:%M")
    c.setFont(font_name, 9)
    c.drawString(margin, h_pt - 12 * mm, ust_tarih)
    c.setFont(font_bold, 11)
    c.drawCentredString(w_pt / 2, h_pt - 12 * mm, "e-Arşiv Fatura (Önizleme)")

    # Firma bilgileri (sol blok) — okunur (9pt)
    y = 22
    c.setFont(font_bold, 10)
    c.drawString(margin, h_pt - y * mm, (FIRMA_UNVAN or "")[:80])
    y += 5
    c.setFont(font_name, 9)
    for line in (FIRMA_ADRES or "").split("\\n"):
        c.drawString(margin, h_pt - y * mm, line[:90])
        y += 4
    c.drawString(margin, h_pt - y * mm, f"Tel: {FIRMA_TELEFON or '—'}")
    y += 4
    c.drawString(margin, h_pt - y * mm, f"Web Sitesi: {FIRMA_WEB or '—'}")
    y += 4
    c.drawString(margin, h_pt - y * mm, f"Vergi Dairesi: {FIRMA_VERGI_DAIRESI or '—'}")
    y += 4
    c.drawString(margin, h_pt - y * mm, f"VKN: {FIRMA_VERGI_NO or '—'}")

    # Sağ üst: QR kod ve meta kutusu
    try:
        import qrcode
        qr_data = f"FATURA|{fatura.get('fatura_no') or ''}|{fatura.get('fatura_tarihi') or ''}"
        qr = qrcode.QRCode(box_size=2, border=1)
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        qr_buf = io.BytesIO()
        img.save(qr_buf, format="PNG")
        qr_buf.seek(0)
        qr_size = 35 * mm
        c.drawImage(qr_buf, right - qr_size, h_pt - 28 * mm, qr_size, qr_size, preserveAspectRatio=True, mask="auto")
    except Exception:
        pass

    # QR altındaki bilgiler kutusu
    box_top = h_pt - 30 * mm
    box_left = right - 60 * mm
    box_width = 60 * mm
    box_height = 26 * mm
    c.rect(box_left, box_top - box_height, box_width, box_height)
    c.setFont(font_name, 7)
    lines = [
        ("Özelleştirme No", "TR1.2"),
        ("Senaryo", "EARSIVFATURA"),
        ("Fatura Tipi", (fatura.get("fatura_tipi") or "SATIS").upper()),
        ("Fatura No", fatura.get("fatura_no") or "—"),
        ("Fatura Tarihi", fatura.get("fatura_tarihi_str") or "—"),
    ]
    row_h = box_height / len(lines)
    for idx, (lbl, val) in enumerate(lines):
        y0 = box_top - row_h * idx
        c.line(box_left, y0, box_left + box_width, y0)
        c.drawString(box_left + 2 * mm, y0 - row_h + 3 * mm, lbl)
        c.drawRightString(box_left + box_width - 2 * mm, y0 - row_h + 3 * mm, val[:28])

    # Alıcı bilgiler bloğu ("SAYIN ...") — okunur (9pt)
    y = 55
    c.setFont(font_bold, 10)
    c.drawString(margin, h_pt - y * mm, "SAYIN")
    y += 4
    c.setFont(font_name, 9)
    musteri_ad = (musteri.get("unvan") or musteri.get("sirket_unvani") or musteri.get("name") or fatura.get("musteri_adi") or "Müşteri")
    c.drawString(margin, h_pt - y * mm, musteri_ad[:90])
    y += 4
    adres = musteri.get("yeni_adres") or musteri.get("address") or ""
    for line in str(adres).split("\\n"):
        if not line:
            continue
        c.drawString(margin, h_pt - y * mm, line[:95])
        y += 4
    vergi_dairesi = musteri.get("vergi_dairesi") or "Kavaklıdere"
    vergi_no = musteri.get("vergi_no") or musteri.get("tax_number") or "—"
    c.drawString(margin, h_pt - y * mm, f"Vergi Dairesi: {vergi_dairesi}")
    y += 4
    c.drawString(margin, h_pt - y * mm, f"VKN/TCKN: {vergi_no}")
    y += 4
    ettn = fatura.get("ettn") or "Önizleme-ETTN"
    c.drawString(margin, h_pt - y * mm, f"ETTN: {ettn}")

    # Mal/Hizmet tablosu — GİB tarzı: başlık üst border, çok satırlı okunaklı başlıklar
    table_width = right - margin
    start_y = h_pt - 95 * mm
    header_row_h = 28   # pt, 2 satırlı başlıklar (İsk./Artt. Oranı vb.) üst/alt sınırlara değmesin
    row_h = 11          # pt, veri + boş satırlar
    cell_pad_vert = 3   # pt, satır altı boşluk
    data_text_offset = 0.5 * mm
    summary_row_h = 11   # pt, özet kutusu satırları
    table_bottom_target = 68 * mm
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
    first_data_row_h = 18   # pt, sadece ilk veri satırı (Sanal ofis...) daha yüksek
    for s in satirlar:
        if y_line < 50 * mm:
            c.showPage()
            y_line = h_pt - 50 * mm
            c.setFont(font_name, 9)
        current_row_h = first_data_row_h if sira == 1 else row_h
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

        values = [
            str(sira),
            (s.get("ad") or s.get("hizmet_urun_adi") or "")[:70],
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
        # İlk satırda tüm yazılar dikey ortada, üst/alt çizgiye değmesin
        draw_y_base = y_line - data_text_offset
        draw_y_center = y_line - (current_row_h / 2) - 3  # satır ortası (9pt font)
        for i, val in enumerate(values):
            w_pt = widths[i]
            dy = draw_y_center if (sira == 1 or i == 1) else draw_y_base
            if i in (0, 2, 4, 5, 6, 8, 9):
                c.drawRightString(x + w_pt - pad, dy, val)
            else:
                c.drawString(x + pad, dy, val)
            x += w_pt
        c.line(margin, y_line - current_row_h, table_right, y_line - current_row_h)
        y_line -= current_row_h
        sira += 1

    # Izgara aşağıya kadar boş satırlar
    while y_line - row_h > table_bottom_target:
        y_line -= row_h
        c.line(margin, y_line - cell_pad_vert, table_right, y_line - cell_pad_vert)
    table_bottom = max(y_line - cell_pad_vert, table_bottom_target)

    # Dikey sütun çizgileri (başlıktan kalem tablosu sonuna)
    x_pos = margin
    c.line(x_pos, start_y, x_pos, table_bottom)
    for w in widths:
        x_pos += w
        c.line(x_pos, start_y, x_pos, table_bottom)

    # Alt toplam tablosu — sayfa sağ altında ayrı kutu (kalem tablosundan büyük boşlukla)
    genel_toplam = float(fatura.get("genel_toplam") or fatura.get("toplam") or 0)
    ara_toplam = float(fatura.get("ara_toplam") or 0)
    toplam_isk = float(fatura.get("toplam_iskonto") or 0)
    kdv_toplam = float(fatura.get("kdv_toplam") or 0)
    summary_box_w = table_width * 0.45
    summary_box_x = margin + table_width - summary_box_w
    box_y = 35 * mm
    box_h = 5 * summary_row_h + 8
    label_w_pt = summary_box_w * 0.68
    c.rect(summary_box_x, box_y, summary_box_w, box_h)
    c.line(summary_box_x + label_w_pt, box_y, summary_box_x + label_w_pt, box_y + box_h)
    for i in range(1, 5):
        c.line(summary_box_x, box_y + box_h - i * summary_row_h, summary_box_x + summary_box_w, box_y + box_h - i * summary_row_h)
    labels_vals = [
        ("Mal Hizmet Toplam Tutarı", ara_toplam),
        ("Toplam İskonto", toplam_isk),
        ("Hesaplanan KDV", kdv_toplam),
        ("Vergiler Dahil Toplam Tutar", genel_toplam),
        ("Ödenecek Tutar", genel_toplam),
    ]
    c.setFont(font_name, 9)
    yy = box_y + box_h - (summary_row_h / 2) - 3
    for lbl, val in labels_vals:
        c.drawString(summary_box_x + 2 * mm, yy, (lbl or "")[:55])
        c.drawRightString(summary_box_x + summary_box_w - 2 * mm, yy, f"{val:,.2f} TL".replace(",", "X").replace(".", ",").replace("X", "."))
        yy -= summary_row_h

    # GİB: Yazı ile tutar — faturanın en altına sabit (sayfa tabanından 10mm), tam genişlik kutu
    yalniz_text = tutar_yaziya_gib(genel_toplam)
    yalniz_font_size = 9
    yalniz_box_h = 14   # pt (tek satır + üst/alt boşluk)
    yalniz_box_y = 10 * mm   # sayfa en altından 10mm (tüm kalemlerin ve özetin altında)
    c.setFont(font_name, yalniz_font_size)
    c.rect(margin, yalniz_box_y, table_width, yalniz_box_h)
    # Metni kutu içinde sola hizalı, dikey ortada (kenara yapışmasın)
    yalniz_pad_left = 2 * mm
    yalniz_baseline = yalniz_box_y + (yalniz_box_h / 2) - (yalniz_font_size * 0.35)
    c.drawString(margin + yalniz_pad_left, yalniz_baseline, (yalniz_text or "")[:120])

    c.showPage()
    c.save()
    return buf.getvalue()


def faturalar_gerekli(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated_function


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
    """Yeni fatura oluşturma ekranı (Finans > Yeni Fatura sekmesi için)."""
    now = datetime.now()
    return render_template('faturalar/yeni_fatura.html',
                           bugun=now.strftime("%Y-%m-%d"),
                           saat=now.strftime("%H:%M"))


@bp.route('/faturalar')
@faturalar_gerekli
def faturalar():
    """Faturalar sekmesi"""
    yil = request.args.get('yil', datetime.now().year, type=int)
    ay_str = request.args.get('ay', '')
    ofis_kodu = request.args.get('ofis', '')
    
    # Tüm ofisleri çek
    ofisler = fetch_all("SELECT DISTINCT code FROM offices WHERE COALESCE(is_active::int, 1) = 1 ORDER BY code")
    
    # Fatura listesi (fatura_tarihi TEXT veya DATE olabilir; ::date ile uyumlu)
    sql = """
        SELECT f.*, c.name as musteri_adi
        FROM faturalar f
        LEFT JOIN customers c ON CAST(f.musteri_id AS INTEGER) = c.id
        WHERE EXTRACT(YEAR FROM (f.fatura_tarihi::date)) = %s
    """
    params = [yil]
    
    if ay_str:
        ay_no = AYLAR.index(ay_str) + 1 if ay_str in AYLAR else 1
        sql += " AND EXTRACT(MONTH FROM (f.fatura_tarihi::date)) = %s"
        params.append(ay_no)
    
    if ofis_kodu:
        sql += " AND f.ofis_kodu = %s"
        params.append(ofis_kodu)
    
    sql += " ORDER BY (f.fatura_tarihi::date) DESC"
    
    faturalar_raw = fetch_all(sql, tuple(params))
    faturalar = [_row_serializable(f) for f in (faturalar_raw or [])]
    
    # Toplamlar
    toplam_tutar = sum(f.get('toplam') or 0 for f in faturalar)
    toplam_odenen = sum(f.get('toplam') or 0 for f in faturalar if f.get('durum') == 'odendi')
    toplam_kalan = toplam_tutar - toplam_odenen
    
    ofisler_list = list(ofisler or [])
    ofisler = [_row_serializable(o) for o in ofisler_list]
    
    return render_template('faturalar/faturalar_tab.html',
                         yil=yil,
                         ay=ay_str,
                         ofis_kodu=ofis_kodu,
                         aylar=AYLAR,
                         ofisler=ofisler,
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
        data = request.get_json() or {}
        satirlar = data.get("satirlar") or []
        musteri_id = data.get("musteri_id")
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
            "musteri_adi": data.get("musteri_adi"),
            "ara_toplam": ara_toplam,
            "toplam_iskonto": toplam_iskonto,
            "kdv_toplam": kdv_toplam,
            "genel_toplam": genel_toplam,
            "toplam": genel_toplam,
        }
        pdf_bytes = build_fatura_pdf(fatura, musteri, satirlar)
        return Response(pdf_bytes, mimetype="application/pdf", headers={
            "Content-Disposition": "inline; filename=Fatura_Onizleme.pdf"
        })
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route('/fatura-ekle', methods=['POST'])
@faturalar_gerekli
def fatura_ekle():
    """Yeni fatura ekle (satır detaylarıyla)."""
    try:
        data = request.get_json() or {}
        satirlar = data.get("satirlar") or []
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
        musteri_id = data.get("musteri_id")
        musteri_adi = (data.get("musteri_adi") or "").strip()

        execute(
            """
            INSERT INTO faturalar (
                fatura_no, musteri_id, musteri_adi, tutar, kdv_tutar,
                toplam, durum, fatura_tarihi, vade_tarihi, notlar
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            ),
        )

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
        }

        return jsonify({"ok": True, "mesaj": "Fatura eklendi!", "earsiv": earsiv_payload})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


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
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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


@bp.route('/api/musteriler')
@faturalar_gerekli
def api_musteriler_list():
    """Tahsilat formu için müşteri listesi (WhatsApp için phone döner)."""
    arama = (request.args.get('q') or '').strip()
    rows = fetch_all("SELECT id, name, phone, tax_number FROM customers ORDER BY name LIMIT 300")

    if not arama:
        return jsonify([{"id": r["id"], "name": r["name"], "phone": (r.get("phone") or "")} for r in rows])

    q_norm = turkish_lower(arama)
    filtered = []
    for r in rows:
        name = turkish_lower(r.get("name") or "")
        tax = turkish_lower(str(r.get("tax_number") or ""))
        if q_norm in name or q_norm in tax:
            filtered.append(r)

    return jsonify([{"id": r["id"], "name": r["name"], "phone": (r.get("phone") or "")} for r in filtered])


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