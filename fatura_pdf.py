"""
E-Arsiv Fatura PDF Uretici - GIB Resmi Standart Format
Ornek faturaya gore birebir ayarlanmis.
"""
import uuid
from datetime import datetime
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT


# Renkler
SIYAH    = colors.black
KOYU_GRI = colors.HexColor("#333333")
GRI      = colors.HexColor("#666666")
ACIK_GRI = colors.HexColor("#f0f0f0")
CIZGI    = colors.HexColor("#aaaaaa")
MAVI     = colors.HexColor("#003399")
BEYAZ    = colors.white


def p(text, size=8, bold=False, align=TA_LEFT, color=SIYAH, leading=None):
    font = "Helvetica-Bold" if bold else "Helvetica"
    style = ParagraphStyle(
        name=f"_{size}_{bold}_{align}",
        fontName=font, fontSize=size,
        leading=leading or (size * 1.35),
        textColor=color, alignment=align,
        wordWrap='CJK',
    )
    return Paragraph(str(text or ""), style)


def tl(val):
    return f"{float(val or 0):,.2f} TL".replace(",", "X").replace(".", ",").replace("X", ".")


def yazi_miktar(sayi: float) -> str:
    birler = ["", "BIR", "IKI", "UC", "DORT", "BES", "ALTI", "YEDI", "SEKIZ", "DOKUZ"]
    onlar  = ["", "ON", "YIRMI", "OTUZ", "KIRK", "ELLI", "ALTMIS", "YETMIS", "SEKSEN", "DOKSAN"]
    def conv(n):
        if n == 0: return ""
        parts = []
        if n >= 1000000:
            m = n // 1000000
            parts.append(conv(m) + " MILYON")
            n %= 1000000
        if n >= 1000:
            b = n // 1000
            parts.append(("" if b == 1 else conv(b) + " ") + "BIN")
            n %= 1000
        if n >= 100:
            y = n // 100
            parts.append(("" if y == 1 else birler[y]) + "YUZ")
            n %= 100
        if n >= 10:
            parts.append(onlar[n // 10])
            n %= 10
        if n > 0:
            parts.append(birler[n])
        return " ".join(filter(None, parts))
    tam = int(sayi)
    kurus = round((sayi - tam) * 100)
    yazi = conv(tam) if tam else "SIFIR"
    if kurus:
        yazi += f" TURK LIRASI {conv(kurus)} KURUS"
    else:
        yazi += " TURK LIRASIDIR"
    return "YALNIZ: " + yazi + "."


def fatura_pdf_olustur(data: dict, cikti_yolu: str) -> str:
    Path(cikti_yolu).parent.mkdir(parents=True, exist_ok=True)

    ettn  = str(uuid.uuid4())
    simdi = datetime.now().strftime("%d-%m-%Y %H:%M")

    # ── Firma bilgileri ──────────────────────────────────────────────────
    firma_adi  = data.get("firma_adi", "")
    firma_vkn  = data.get("firma_vkn", "")
    firma_adr  = data.get("firma_adres", "")
    firma_tel  = data.get("firma_tel", "")
    firma_vd   = data.get("firma_vergi_dairesi", "")
    musteri_adi = data.get("musteri_adi", "")
    musteri_vkn = str(data.get("musteri_vkn", "") or "")
    musteri_adr = data.get("musteri_adres", "")
    fatura_no   = data.get("fatura_no", "")
    fatura_tar  = data.get("fatura_tarihi", "")
    fatura_turu = data.get("fatura_turu", "SATIS")
    not_aciklama= data.get("not_aciklama", "")

    # ── Kalem hesapla ────────────────────────────────────────────────────
    hesap = []
    t_matrah = t_iskonto = t_kdv = 0.0
    kdv_grup = {}

    for k in data.get("kalemler", []):
        mik  = float(k.get("miktar", 1) or 1)
        bp   = float(k.get("birim_fiyat", 0) or 0)
        io   = float(k.get("iskonto_oran", 0) or 0)
        ko   = float(k.get("kdv_oran", 20) or 20)
        brut = mik * bp
        isk  = brut * io / 100
        mat  = brut - isk
        kdv  = mat * ko / 100
        hesap.append({"ac": k.get("aciklama",""), "mik": mik,
                      "bir": k.get("birim","Adet"), "bp": bp,
                      "io": io, "isk": isk, "ko": ko, "kdv": kdv, "mat": mat})
        t_matrah  += mat
        t_iskonto += isk
        t_kdv     += kdv
        kdv_grup.setdefault(ko, [0.0, 0.0])
        kdv_grup[ko][0] += mat
        kdv_grup[ko][1] += kdv

    genel = t_matrah + t_kdv

    # ── Döküman ──────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        cikti_yolu, pagesize=A4,
        leftMargin=1.0*cm, rightMargin=1.0*cm,
        topMargin=0.8*cm, bottomMargin=1.2*cm,
    )
    W = A4[0] - 2.0*cm
    story = []

    # ════════════════════════════════════════════
    # BÖLÜM 1: ÜST BAŞLIK
    # 3 kolon: Firma bilgisi | GIB logo | QR/boş
    # ════════════════════════════════════════════

    # Sol: Firma kutusu
    firma_rows = [
        [p(firma_adi, 8, bold=True)],
        [p(firma_adr, 7.5)],
        [p(f"Tel: {firma_tel}  Fax: ", 7.5)],
        [p("Web Sitesi:", 7.5)],
        [p("E-Posta:", 7.5)],
        [p(f"Vergi Dairesi: {firma_vd}", 7.5)],
        [p(f"MERSISNO:", 7.5)],
        [p(f"VKN: {firma_vkn}", 7.5)],
    ]
    firma_kutu = Table(firma_rows, colWidths=[7.5*cm])
    firma_kutu.setStyle(TableStyle([
        ("BOX",           (0,0), (-1,-1), 0.6, CIZGI),
        ("TOPPADDING",    (0,0), (-1,-1), 1.5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 1.5),
        ("LEFTPADDING",   (0,0), (-1,-1), 4),
    ]))

    # Orta: GIB logo
    gib_rows = [
        [p("T.C. Hazine ve Maliye Bakanligi", 6.5, bold=True, align=TA_CENTER, color=MAVI)],
        [p("Gelir Idaresi Baskanligi", 6.5, align=TA_CENTER, color=MAVI)],
        [Spacer(1, 4)],
        [p("e-Arsiv Fatura", 14, bold=True, align=TA_CENTER, color=MAVI)],
    ]
    gib_kutu = Table(gib_rows, colWidths=[5.5*cm])
    gib_kutu.setStyle(TableStyle([
        ("BOX",           (0,0), (-1,-1), 0.6, CIZGI),
        ("BACKGROUND",    (0,0), (-1,-1), ACIK_GRI),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))

    # Sag: QR alan (bos kutu)
    qr_kutu = Table([[p("", 7)]], colWidths=[4.5*cm])
    qr_kutu.setStyle(TableStyle([
        ("BOX",    (0,0), (-1,-1), 0.6, CIZGI),
        ("MINIMUM_HEIGHT", (0,0), (-1,-1), 2.5*cm),
    ]))

    ust = Table([[firma_kutu, gib_kutu, qr_kutu]],
                colWidths=[7.5*cm, 5.5*cm, 4.5*cm])
    ust.setStyle(TableStyle([
        ("VALIGN",  (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING",  (0,0), (-1,-1), 0),
        ("RIGHTPADDING", (0,0), (-1,-1), 0),
        ("INNERGRID", (0,0), (-1,-1), 0, BEYAZ),
        ("TOPPADDING",   (0,0), (-1,-1), 0),
        ("BOTTOMPADDING",(0,0), (-1,-1), 0),
    ]))
    story.append(ust)
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1.2, color=SIYAH))
    story.append(Spacer(1, 4))

    # ════════════════════════════════════════════
    # BÖLÜM 2: SAYIN (alici) | Fatura Meta
    # ════════════════════════════════════════════
    tc_vkn_etiket = "TCKN:" if len(musteri_vkn) == 11 else "VKN:"

    alici_rows = [
        [p("SAYIN", 8, bold=True)],
        [p(musteri_adi, 8, bold=True)],
        [p(musteri_adr, 7.5)],
        [Spacer(1, 3)],
        [p(f"Vergi Dairesi: {firma_vd}", 7.5)],
        [p(f"{tc_vkn_etiket} {musteri_vkn}", 7.5)],
    ]
    alici_kutu = Table(alici_rows, colWidths=[9.5*cm])
    alici_kutu.setStyle(TableStyle([
        ("BOX",           (0,0), (-1,-1), 0.6, CIZGI),
        ("TOPPADDING",    (0,0), (-1,-1), 1.5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 1.5),
        ("LEFTPADDING",   (0,0), (-1,-1), 4),
    ]))

    meta_rows = [
        [p("Ozellestime No:", 7.5, bold=True), p("TR1.2", 7.5)],
        [p("Senaryo:",         7.5, bold=True), p("EARSIVFATURA", 7.5)],
        [p("Fatura Tipi:",     7.5, bold=True), p(fatura_turu, 7.5)],
        [p("Fatura No:",       7.5, bold=True), p(fatura_no, 7.5)],
        [p("Fatura Tarihi:",   7.5, bold=True), p(simdi, 7.5)],
    ]
    meta_kutu = Table(meta_rows, colWidths=[3.2*cm, 4.8*cm])
    meta_kutu.setStyle(TableStyle([
        ("BOX",           (0,0), (-1,-1), 0.6, CIZGI),
        ("INNERGRID",     (0,0), (-1,-1), 0.3, CIZGI),
        ("TOPPADDING",    (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ("LEFTPADDING",   (0,0), (-1,-1), 3),
    ]))

    orta = Table([[alici_kutu, "", meta_kutu]],
                 colWidths=[9.5*cm, 0.3*cm, 8.2*cm])
    orta.setStyle(TableStyle([
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 0),
        ("TOPPADDING",    (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 0),
    ]))
    story.append(orta)
    story.append(Spacer(1, 3))
    story.append(p(f"ETTN: {ettn}", 6.5, color=GRI))
    story.append(Spacer(1, 5))

    # ════════════════════════════════════════════
    # BÖLÜM 3: KALEM TABLOSU
    # ════════════════════════════════════════════
    bas = [
        p("Sira\nNo",           6.5, bold=True, align=TA_CENTER),
        p("Mal Hizmet",         6.5, bold=True, align=TA_CENTER),
        p("Miktar",             6.5, bold=True, align=TA_CENTER),
        p("Birim\nFiyat",       6.5, bold=True, align=TA_CENTER),
        p("Iskonto/\nArtirim\nOrani",  6.5, bold=True, align=TA_CENTER),
        p("Iskonto/\nArtirim\nTutari", 6.5, bold=True, align=TA_CENTER),
        p("Iskonto/\nArtirim\nNedeni", 6.5, bold=True, align=TA_CENTER),
        p("KDV\nOrani",         6.5, bold=True, align=TA_CENTER),
        p("KDV\nTutari",        6.5, bold=True, align=TA_CENTER),
        p("Diger\nVergiler",    6.5, bold=True, align=TA_CENTER),
        p("Mal\nHizmet\nTutari",6.5, bold=True, align=TA_CENTER),
    ]
    wdts = [0.9*cm, 5.0*cm, 1.2*cm, 1.6*cm,
            1.4*cm, 1.4*cm, 1.3*cm,
            1.0*cm, 1.4*cm, 1.3*cm, 1.5*cm]

    rows = [bas]
    for i, k in enumerate(hesap, 1):
        rows.append([
            p(str(i),              7, align=TA_CENTER),
            p(k["ac"],             7),
            p(f"{k['mik']:.0f} {k['bir']}", 7, align=TA_CENTER),
            p(f"{k['bp']:,.0f} TL", 7, align=TA_RIGHT),
            p(f"%{k['io']:.2f}",   7, align=TA_CENTER),
            p(f"{k['isk']:,.2f} TL", 7, align=TA_RIGHT),
            p("Iskonto -" if k['io'] else "", 7, align=TA_CENTER),
            p(f"%{k['ko']:.2f}",   7, align=TA_CENTER),
            p(f"{k['kdv']:,.2f} TL", 7, align=TA_RIGHT),
            p("",                  7),
            p(f"{k['mat']:,.2f}\nTL", 7, align=TA_RIGHT),
        ])

    # Bos satirlar (en az 10 satirlik tablo gorunsun)
    for _ in range(max(0, 10 - len(hesap))):
        rows.append([p("") for _ in range(11)])

    kt = Table(rows, colWidths=wdts, repeatRows=1)
    ts = TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), ACIK_GRI),
        ("LINEBELOW",     (0,0), (-1,0), 0.8, SIYAH),
        ("BOX",           (0,0), (-1,-1), 0.8, SIYAH),
        ("INNERGRID",     (0,0), (-1,-1), 0.3, CIZGI),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ("LEFTPADDING",   (0,0), (-1,-1), 2),
        ("RIGHTPADDING",  (0,0), (-1,-1), 2),
    ])
    for i in range(1, len(rows)):
        if i % 2 == 0:
            ts.add("BACKGROUND", (0,i), (-1,i), colors.HexColor("#fafafa"))
    kt.setStyle(ts)
    story.append(kt)
    story.append(Spacer(1, 6))

    # ════════════════════════════════════════════
    # BÖLÜM 4: ÖZET TOPLAMLAR (sag alt)
    # ════════════════════════════════════════════
    ozet = [
        [p("Mal Hizmet Toplam Tutari", 7.5, bold=True, align=TA_RIGHT),
         p(f"{t_matrah:,.2f} TL", 7.5, align=TA_RIGHT)],
        [p("Toplam Iskonto", 7.5, bold=True, align=TA_RIGHT),
         p(f"{t_iskonto:,.2f} TL", 7.5, align=TA_RIGHT)],
    ]
    for oran, (mat, kdv) in sorted(kdv_grup.items()):
        ozet.append([
            p(f"Hesaplanan KDV(%{oran:.0f})", 7.5, bold=True, align=TA_RIGHT),
            p(f"{kdv:,.2f} TL", 7.5, align=TA_RIGHT)
        ])
    ozet += [
        [p("Vergiler Dahil Toplam Tutar", 8, bold=True, align=TA_RIGHT),
         p(f"{genel:,.2f} TL", 8, bold=True, align=TA_RIGHT)],
        [p("Odenecek Tutar", 8.5, bold=True, align=TA_RIGHT),
         p(f"{genel:,.2f} TL", 8.5, bold=True, align=TA_RIGHT)],
    ]

    ozet_t = Table(ozet, colWidths=[5.5*cm, 2.8*cm])
    ozet_t.setStyle(TableStyle([
        ("BOX",           (0,0), (-1,-1), 0.8, SIYAH),
        ("INNERGRID",     (0,0), (-1,-1), 0.3, CIZGI),
        ("TOPPADDING",    (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ("LEFTPADDING",   (0,0), (-1,-1), 4),
        ("RIGHTPADDING",  (0,0), (-1,-1), 4),
        ("BACKGROUND",    (0,-2), (-1,-1), ACIK_GRI),
        ("LINEABOVE",     (0,-2), (-1,-2), 0.8, SIYAH),
    ]))

    sarma = Table([["", ozet_t]], colWidths=[W - 8.6*cm, 8.6*cm])
    sarma.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"),
                                ("LEFTPADDING", (0,0), (-1,-1), 0),
                                ("RIGHTPADDING", (0,0), (-1,-1), 0)]))
    story.append(sarma)
    story.append(Spacer(1, 8))

    # ════════════════════════════════════════════
    # BÖLÜM 5: NOT KUTUSU
    # ════════════════════════════════════════════
    yazi = yazi_miktar(genel)
    not_satirlar = [[p(f"Not: {yazi}", 7.5, bold=True)]]
    if not_aciklama:
        not_satirlar.append([p(not_aciklama, 7.5)])

    not_kutu = Table(not_satirlar, colWidths=[W])
    not_kutu.setStyle(TableStyle([
        ("BOX",           (0,0), (-1,-1), 0.6, CIZGI),
        ("BACKGROUND",    (0,0), (-1,-1), ACIK_GRI),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
    ]))
    story.append(not_kutu)

    # ════════════════════════════════════════════
    # ALT BILGI
    # ════════════════════════════════════════════
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=0.5, color=CIZGI))
    story.append(Spacer(1, 3))
    story.append(Table([[
        p(f"{simdi} e-Belge", 6.5, color=GRI),
        p(f"VKN: {firma_vkn}", 6.5, color=GRI, align=TA_RIGHT),
    ]], colWidths=[W/2, W/2]))

    doc.build(story)
    return cikti_yolu
