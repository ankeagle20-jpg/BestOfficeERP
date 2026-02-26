"""
Personel İzin Talep Formu - PDF Üretici
reportlab ile profesyonel izin formu oluşturur
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                  TableStyle, HRFlowable)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from pathlib import Path
import os


def izin_formu_olustur(data: dict, cikti_yolu: str = None) -> str:
    """
    İzin formu PDF oluşturur.
    data: {
        personel_ad, tc_no, unvan, departman,
        izin_turu, baslangic, bitis, gun_sayisi, yari_gun,
        ise_baslama, aciklama, firma_adi
    }
    """
    if not cikti_yolu:
        ad = data.get("personel_ad", "izin").replace(" ", "_")
        cikti_yolu = str(Path(__file__).parent / f"izin_formu_{ad}.pdf")

    doc = SimpleDocTemplate(
        cikti_yolu,
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm
    )

    styles = getSampleStyleSheet()

    # Özel stiller
    baslik_style = ParagraphStyle("baslik", parent=styles["Title"],
                                   fontSize=16, textColor=colors.HexColor("#0f2537"),
                                   spaceAfter=4)
    alt_baslik_style = ParagraphStyle("alt_baslik", parent=styles["Normal"],
                                       fontSize=9, textColor=colors.HexColor("#555555"),
                                       spaceAfter=12)
    bolum_style = ParagraphStyle("bolum", parent=styles["Heading2"],
                                  fontSize=11, textColor=colors.HexColor("#0f2537"),
                                  spaceBefore=12, spaceAfter=6,
                                  borderPad=4)
    alan_style = ParagraphStyle("alan", parent=styles["Normal"],
                                 fontSize=10, spaceAfter=4)
    kucuk_style = ParagraphStyle("kucuk", parent=styles["Normal"],
                                  fontSize=8, textColor=colors.HexColor("#777777"))
    imza_style = ParagraphStyle("imza", parent=styles["Normal"],
                                 fontSize=9, alignment=TA_CENTER)

    story = []

    # ── Üst logo / firma alanı ──
    firma = data.get("firma_adi", "BestOffice")
    header_data = [[
        Paragraph(f"<b>{firma}</b>", ParagraphStyle("firma", fontSize=14,
                   textColor=colors.HexColor("#4fc3f7"))),
        Paragraph(f"Döküman No: İK-{data.get('doc_no','001')}<br/>"
                  f"Tarih: {data.get('bugun','')}", kucuk_style)
    ]]
    header_table = Table(header_data, colWidths=[10*cm, 7*cm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN", (1,0), (1,0), "RIGHT"),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#0f2537")))
    story.append(Spacer(1, 0.3*cm))

    # ── Başlık ──
    story.append(Paragraph("📋 Personel İzin Talep Formu", baslik_style))
    story.append(Spacer(1, 0.2*cm))

    # ── Personel Bilgileri ──
    story.append(Paragraph("👤 Personel Bilgileri", bolum_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    story.append(Spacer(1, 0.2*cm))

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
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 3),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.HexColor("#f8f9fa"), colors.white]),
        ("LEFTPADDING", (0,0), (0,-1), 4),
    ]))
    story.append(personel_table)
    story.append(Spacer(1, 0.3*cm))

    # ── İzin Detayları ──
    story.append(Paragraph("📅 İzin Detayları", bolum_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    story.append(Spacer(1, 0.2*cm))

    # İzin türü checkboxlar
    izin_turleri = ["Yıllık Ücretli İzin", "Ücretsiz İzin",
                    "Sağlık / Rapor", "Mazeret İzni", "Yarım Gün İzin"]
    secili_tur = data.get("izin_turu", "")

    tur_satirlari = []
    for tur in izin_turleri:
        isaret = "☑" if tur == secili_tur else "☐"
        tur_satirlari.append(
            Paragraph(f"{isaret}  {tur}", alan_style)
        )

    # 2 sütunlu checkbox layout
    tur_data = []
    for i in range(0, len(tur_satirlari), 2):
        row = [tur_satirlari[i]]
        if i+1 < len(tur_satirlari):
            row.append(tur_satirlari[i+1])
        else:
            row.append(Paragraph("", alan_style))
        tur_data.append(row)

    tur_table = Table(tur_data, colWidths=[8.5*cm, 8.5*cm])
    tur_table.setStyle(TableStyle([
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    story.append(Paragraph("<b>İzin Türü:</b>", alan_style))
    story.append(tur_table)
    story.append(Spacer(1, 0.2*cm))

    # Tarih ve gün bilgileri
    gun_str = str(data.get("gun_sayisi", ""))
    if data.get("yari_gun"):
        gun_str += " (Yarım Gün)"

    tarih_data = [
        satir("İzin Başlangıç Tarihi:", data.get("baslangic", "")),
        satir("İzin Bitiş Tarihi:", data.get("bitis", "")),
        satir("İşe Başlama Tarihi:", data.get("ise_donme", "")),
        satir("Toplam İzin (Gün):", gun_str),
    ]
    tarih_table = Table(tarih_data, colWidths=[5*cm, 12*cm])
    tarih_table.setStyle(TableStyle([
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 3),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.HexColor("#f8f9fa"), colors.white]),
        ("LEFTPADDING", (0,0), (0,-1), 4),
    ]))
    story.append(tarih_table)
    story.append(Spacer(1, 0.3*cm))

    # ── İletişim ──
    story.append(Paragraph("📞 İletişim Bilgileri", bolum_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph("<i>(İzin süresince bulunacağı adres ve ulaşılabilecek telefon numarası)</i>",
                            kucuk_style))
    story.append(Spacer(1, 0.2*cm))

    iletisim_data = [
        satir("Adres:", data.get("adres", "")),
        satir("Telefon:", data.get("telefon", "")),
        satir("Açıklama:", data.get("aciklama", "")),
    ]
    iletisim_table = Table(iletisim_data, colWidths=[5*cm, 12*cm])
    iletisim_table.setStyle(TableStyle([
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 3),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.HexColor("#f8f9fa"), colors.white]),
        ("LEFTPADDING", (0,0), (0,-1), 4),
    ]))
    story.append(iletisim_table)
    story.append(Spacer(1, 0.5*cm))

    # ── Beyan ──
    story.append(Paragraph(
        '"Yukarıda belirttiğim tarihlerde izin kullanmak istediğimi beyan ederim."',
        ParagraphStyle("beyan", parent=styles["Normal"], fontSize=9,
                       textColor=colors.HexColor("#555555"), alignment=TA_CENTER)
    ))
    story.append(Spacer(1, 0.8*cm))

    # ── Onay İmzaları ──
    story.append(Paragraph("✍️ Onay İmzaları", bolum_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    story.append(Spacer(1, 0.3*cm))

    imza_data = [
        [Paragraph("<b>Personel İmzası</b>", imza_style),
         Paragraph("<b>Departman Yöneticisi</b>", imza_style),
         Paragraph("<b>İnsan Kaynakları /\nGenel Müdür</b>", imza_style)],
        [Paragraph("\n\n\n\n", imza_style),
         Paragraph("\n\n\n\n", imza_style),
         Paragraph("\n\n\n\n", imza_style)],
        [Paragraph("İmza / Tarih", kucuk_style),
         Paragraph("İmza / Tarih", kucuk_style),
         Paragraph("İmza / Tarih", kucuk_style)],
    ]
    imza_table = Table(imza_data, colWidths=[5.6*cm, 5.6*cm, 5.8*cm])
    imza_table.setStyle(TableStyle([
        ("BOX", (0,0), (0,-1), 0.5, colors.HexColor("#333333")),
        ("BOX", (1,0), (1,-1), 0.5, colors.HexColor("#333333")),
        ("BOX", (2,0), (2,-1), 0.5, colors.HexColor("#333333")),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e8f4f8")),
        ("LINEBELOW", (0,1), (-1,1), 0.5, colors.HexColor("#aaaaaa")),
    ]))
    story.append(imza_table)

    # ── İzin Bakiyesi (alt bilgi) ──
    story.append(Spacer(1, 0.5*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0f2537")))

    bakiye = data.get("izin_bakiye")
    if bakiye:
        story.append(Spacer(1, 0.2*cm))
        bakiye_data = [[
            Paragraph(f"<b>Toplam Hak:</b> {bakiye.get('toplam_hak', 14)} gün",
                      kucuk_style),
            Paragraph(f"<b>Kullanılan:</b> {bakiye.get('yillik_kullanilan', 0)} gün",
                      kucuk_style),
            Paragraph(f"<b>Kalan:</b> {bakiye.get('kalan', 14)} gün",
                      ParagraphStyle("kalan", parent=kucuk_style,
                                     textColor=colors.HexColor("#0f6b2a")
                                     if bakiye.get('kalan', 0) > 0
                                     else colors.red)),
        ]]
        bak_table = Table(bakiye_data, colWidths=[5.6*cm, 5.6*cm, 5.8*cm])
        bak_table.setStyle(TableStyle([
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f0f8ff")),
            ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#aaaaaa")),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
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
