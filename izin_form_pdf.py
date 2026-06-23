"""
Personel İzin Talep Formu - PDF Üretici
reportlab ile profesyonel izin formu oluşturur
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                  TableStyle, HRFlowable)
from reportlab.platypus import Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pathlib import Path
import os

# Windows Arial font yolları
_FONT_REGISTERED = False


def _register_fonts():
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    font_paths = [
        ("Arial", r"C:\Windows\Fonts\arial.ttf"),
        ("Arial-Bold", r"C:\Windows\Fonts\arialbd.ttf"),
    ]
    for fname, fpath in font_paths:
        if os.path.exists(fpath):
            try:
                pdfmetrics.registerFont(TTFont(fname, fpath))
            except Exception:
                pass
    _FONT_REGISTERED = True


def _resolve_izin_logo():
    here = os.path.dirname(os.path.abspath(__file__))
    cands = []
    for nm in (
        "Ofisbir Logo.jpg", "Ofisbir Logo.png",
        "ofisbir_logo.png", "ofisbir_logo.jpg",
        "ofisbir.png", "logo.png", "logo.jpg",
    ):
        cands.append(os.path.join(here, "assets", nm))
        cands.append(os.path.join(here, "erp_web", "static", nm))
    for pth in cands:
        if os.path.isfile(pth):
            return pth
    return None


def izin_formu_olustur(data: dict, cikti_yolu: str = None) -> str:
    """
    İzin formu PDF oluşturur.
    data: {
        personel_ad, tc_no, unvan, departman,
        izin_turu, baslangic, bitis, gun_sayisi, yari_gun,
        ise_baslama, aciklama, firma_adi
    }
    """
    _register_fonts()
    if not cikti_yolu:
        ad = data.get("personel_ad", "izin").replace(" ", "_")
        cikti_yolu = str(Path(__file__).parent / f"izin_formu_{ad}.pdf")

    doc = SimpleDocTemplate(
        cikti_yolu,
        pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=0.5*cm, bottomMargin=1.5*cm
    )

    styles = getSampleStyleSheet()

    # Özel stiller
    baslik_style = ParagraphStyle("baslik", parent=styles["Title"],
                                   fontName="Arial",
                                   fontSize=15, textColor=colors.HexColor("#0f2537"),
                                   spaceAfter=2)
    alt_baslik_style = ParagraphStyle("alt_baslik", parent=styles["Normal"],
                                       fontName="Arial",
                                       fontSize=9, textColor=colors.HexColor("#555555"),
                                       spaceAfter=8)
    bolum_style = ParagraphStyle("bolum", parent=styles["Heading2"],
                                  fontName="Arial",
                                  fontSize=10, textColor=colors.HexColor("#0f2537"),
                                  spaceBefore=8, spaceAfter=4,
                                  borderPad=4)
    alan_style = ParagraphStyle("alan", parent=styles["Normal"],
                                 fontName="Arial",
                                 fontSize=9, spaceAfter=2)
    kucuk_style = ParagraphStyle("kucuk", parent=styles["Normal"],
                                  fontName="Arial",
                                  fontSize=7, textColor=colors.HexColor("#777777"))
    header_sag_style = ParagraphStyle(
        "header_sag",
        parent=kucuk_style,
        alignment=TA_RIGHT,
    )
    imza_style = ParagraphStyle("imza", parent=styles["Normal"],
                                 fontName="Arial",
                                 fontSize=8, alignment=TA_CENTER)

    story = []

    # ── Üst logo / firma alanı ──
    firma = data.get("firma_adi", "BestOffice")
    logo_path = _resolve_izin_logo()
    if logo_path:
        sol_huecre = RLImage(
            logo_path,
            width=8*cm,
            height=3*cm,
            kind="proportional",
        )
    else:
        sol_huecre = Paragraph(
            f"<b>{firma}</b>",
            ParagraphStyle(
                "firma",
                fontName="Arial-Bold",
                fontSize=13,
                textColor=colors.HexColor("#4fc3f7"),
            ),
        )
    header_data = [[
        sol_huecre,
        Paragraph(f"Döküman No: İK-{data.get('doc_no','001')}<br/>"
                  f"Tarih: {data.get('bugun','')}", header_sag_style)
    ]]
    header_table = Table(header_data, colWidths=[10*cm, 7*cm])
    header_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#0f2537")))
    story.append(Spacer(1, 0.15*cm))

    # ── Başlık ──
    story.append(Paragraph("📋 Personel İzin Talep Formu", baslik_style))
    story.append(Spacer(1, 0.12*cm))

    # ── Personel Bilgileri ──
    story.append(Paragraph("👤 Personel Bilgileri", bolum_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    story.append(Spacer(1, 0.12*cm))

    def satir(label, deger, bos_genislik=8):
        alt = "_" * bos_genislik if not deger else ""
        return [
            Paragraph(f"<b>{label}</b>", alan_style),
            Paragraph(f"{deger or alt}", alan_style)
        ]

    personel_data = [
        satir("Adı Soyadı:", data.get("personel_ad", "")),
        satir("T.C. Kimlik No:", data.get("tc_no", "")),
        satir("Bölüm / Departman:", data.get("departman", "")),
        satir("Unvan:", data.get("unvan", "")),
        satir("İşe Başlama Tarihi:", data.get("ise_baslama", "")),
    ]
    personel_table = Table(personel_data, colWidths=[5*cm, 12*cm])
    personel_table.setStyle(TableStyle([
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("TOPPADDING", (0,0), (-1,-1), 2),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.HexColor("#f8f9fa"), colors.white]),
        ("LEFTPADDING", (0,0), (0,-1), 4),
    ]))
    story.append(personel_table)
    story.append(Spacer(1, 0.15*cm))

    # ── İzin Detayları ──
    story.append(Paragraph("📅 İzin Detayları", bolum_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    story.append(Spacer(1, 0.12*cm))

    secili_tur = data.get("izin_turu", "") or "Belirtilmemiş"
    saat_sayisi = data.get("saat_sayisi") or 0
    try:
        saat_sayisi = int(saat_sayisi)
    except (TypeError, ValueError):
        saat_sayisi = 0
    if secili_tur == "Saatlik İzin" and saat_sayisi > 0:
        secili_tur = f"Saatlik İzin ({saat_sayisi} saat)"
    story.append(
        Paragraph(
            f"<b>İzin Türü:</b>  {secili_tur}",
            alan_style,
        )
    )
    story.append(Spacer(1, 0.12*cm))

    # Tarih ve gün bilgileri
    if secili_tur.startswith("Saatlik İzin") or data.get("izin_turu") == "Saatlik İzin":
        gun_str = f"{saat_sayisi} saat" if saat_sayisi > 0 else "0 saat"
        gun_label = "Toplam İzin (Saat):"
    else:
        gun_str = str(data.get("gun_sayisi", ""))
        if data.get("yari_gun"):
            gun_str += " (Yarım Gün)"
        gun_label = "Toplam İzin (Gün):"

    tarih_data = [
        satir("İzin Başlangıç Tarihi:", data.get("baslangic", "")),
        satir("İzin Bitiş Tarihi:", data.get("bitis", "")),
        satir("İşe Başlama Tarihi:", data.get("ise_donme", "")),
        satir(gun_label, gun_str),
    ]
    tarih_table = Table(tarih_data, colWidths=[5*cm, 12*cm])
    tarih_table.setStyle(TableStyle([
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("TOPPADDING", (0,0), (-1,-1), 2),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.HexColor("#f8f9fa"), colors.white]),
        ("LEFTPADDING", (0,0), (0,-1), 4),
    ]))
    story.append(tarih_table)
    story.append(Spacer(1, 0.15*cm))

    # ── İletişim ──
    story.append(Paragraph("📞 İletişim Bilgileri", bolum_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    story.append(Spacer(1, 0.12*cm))
    story.append(Paragraph("<i>(İzin süresince bulunacağı adres ve ulaşılabilecek telefon numarası)</i>",
                            kucuk_style))
    story.append(Spacer(1, 0.12*cm))

    iletisim_data = [
        satir("Adres:", data.get("adres", "")),
        satir("Telefon:", data.get("telefon", "")),
        satir("Açıklama:", data.get("aciklama", "")),
    ]
    iletisim_table = Table(iletisim_data, colWidths=[5*cm, 12*cm])
    iletisim_table.setStyle(TableStyle([
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("TOPPADDING", (0,0), (-1,-1), 2),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.HexColor("#f8f9fa"), colors.white]),
        ("LEFTPADDING", (0,0), (0,-1), 4),
    ]))
    story.append(iletisim_table)
    story.append(Spacer(1, 0.25*cm))

    # ── Beyan ──
    story.append(Paragraph(
        '"Yukarıda belirttiğim tarihlerde izin kullanmak istediğimi beyan ederim."',
        ParagraphStyle("beyan", parent=styles["Normal"], fontName="Arial", fontSize=8,
                       textColor=colors.HexColor("#555555"), alignment=TA_CENTER)
    ))
    story.append(Spacer(1, 0.35*cm))

    # ── Onay İmzaları ──
    story.append(Paragraph("✍️ Onay İmzaları", bolum_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    story.append(Spacer(1, 0.15*cm))

    imza_data = [
        [Paragraph("<b>Personel İmzası</b>", imza_style),
         Paragraph("<b>Departman Yöneticisi</b>", imza_style),
         Paragraph("<b>İnsan Kaynakları /\nGenel Müdür</b>", imza_style)],
        [Paragraph("\n\n\n\n\n\n", imza_style),
         Paragraph("\n\n\n\n\n\n", imza_style),
         Paragraph("\n\n\n\n\n\n", imza_style)],
        ["", "", ""],
    ]
    imza_table = Table(
        imza_data,
        colWidths=[5.6*cm, 5.6*cm, 5.8*cm],
        rowHeights=[0.7*cm, 2.8*cm, 0.4*cm],
    )
    imza_table.setStyle(TableStyle([
        ("BOX", (0,0), (0,-1), 0.5, colors.HexColor("#333333")),
        ("BOX", (1,0), (1,-1), 0.5, colors.HexColor("#333333")),
        ("BOX", (2,0), (2,-1), 0.5, colors.HexColor("#333333")),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,0), 4),
        ("BOTTOMPADDING", (0,0), (-1,0), 4),
        ("TOPPADDING", (0,1), (-1,1), 8),
        ("BOTTOMPADDING", (0,1), (-1,1), 4),
        ("TOPPADDING", (0,2), (-1,2), 4),
        ("BOTTOMPADDING", (0,2), (-1,2), 4),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e8f4f8")),
        ("LINEBELOW", (0,1), (-1,1), 0.5, colors.HexColor("#aaaaaa")),
    ]))
    story.append(imza_table)

    # ── İzin Bakiyesi (alt bilgi) ──
    story.append(Spacer(1, 0.2*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0f2537")))

    bakiye = data.get("izin_bakiye")
    if bakiye:
        story.append(Spacer(1, 0.1*cm))
        bakiye_data = [[
            Paragraph(f"<b>Toplam Hak:</b> {bakiye.get('toplam_hak', 14)} gün",
                      kucuk_style),
            Paragraph(f"<b>Kullanılan:</b> {bakiye.get('yillik_kullanilan', 0)} gün",
                      kucuk_style),
            Paragraph(f"<b>Kalan:</b> {bakiye.get('kalan', 14)} gün",
                      ParagraphStyle("kalan", parent=kucuk_style, fontName="Arial",
                                     textColor=colors.HexColor("#0f6b2a")
                                     if bakiye.get('kalan', 0) > 0
                                     else colors.red)),
        ]]
        bak_table = Table(bakiye_data, colWidths=[5.6*cm, 5.6*cm, 5.8*cm])
        bak_table.setStyle(TableStyle([
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f0f8ff")),
            ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#aaaaaa")),
            ("TOPPADDING", (0,0), (-1,-1), 2),
            ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ]))
        story.append(bak_table)

    doc.build(story)
    return cikti_yolu


if __name__ == "__main__":
    # Test
    from datetime import date
    test_data = {
        "firma_adi": "BestOffice ERP",
        "doc_no": "001",
        "bugun": date.today().strftime("%d.%m.%Y"),
        "personel_ad": "Ahmet Yılmaz",
        "tc_no": "12345678901",
        "unvan": "Uzman",
        "departman": "Operasyon",
        "ise_baslama": "01.01.2022",
        "izin_turu": "Yıllık Ücretli İzin",
        "baslangic": "10.03.2026",
        "bitis": "14.03.2026",
        "ise_donme": "17.03.2026",
        "gun_sayisi": 5,
        "yari_gun": 0,
        "aciklama": "Yıllık izin",
        "izin_bakiye": {"toplam_hak": 14, "yillik_kullanilan": 5, "kalan": 9},
    }
    yol = izin_formu_olustur(test_data, "/home/claude/test_izin.pdf")
    print(f"PDF oluşturuldu: {yol}")
