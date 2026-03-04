"""
Giriş / Müşteri Kaydı Routes
Desktop'taki gibi tam fonksiyonel + Sözleşme oluşturma
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, Response
from flask_login import current_user
from auth import giris_gerekli
from db import fetch_all, fetch_one, execute, execute_returning, db as get_db
from datetime import datetime, date, timedelta
from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
import os
import io
import re
import urllib.parse
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

def _register_arial():
    """Türkçe karakter için Arial veya alternatif font kaydet."""
    if getattr(_register_arial, "_done", False):
        return
    candidates = []
    win = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or "C:\\Windows"
    for f in ("arial.ttf", "Arial.ttf", "ARIAL.TTF"):
        candidates.append(os.path.join(win, "Fonts", f))
    candidates.extend([
        "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ])
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                pdfmetrics.registerFont(TTFont("Arial", path))
                _register_arial._done = True
                return
            except Exception:
                pass
    _register_arial._done = True

bp = Blueprint('giris', __name__)

# Dosya yükleme ayarları
UPLOAD_FOLDER = 'uploads/musteri_dosyalari'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@bp.route('/')
@giris_gerekli
def index():
    """Giriş / Müşteri Kaydı ana sayfası"""
    return render_template('giris/index.html')


@bp.route('/api/musteriler')
@giris_gerekli
def api_musteriler():
    """Müşteri listesi - AJAX için"""
    arama = request.args.get('q', '').strip()
    
    if arama:
        musteriler = fetch_all(
            """SELECT id, name, tax_number, phone, email, office_code 
               FROM customers 
               WHERE name ILIKE %s OR tax_number ILIKE %s
               ORDER BY name LIMIT 100""",
            (f'%{arama}%', f'%{arama}%')
        )
    else:
        musteriler = fetch_all(
            """SELECT id, name, tax_number, phone, email, office_code 
               FROM customers 
               ORDER BY name LIMIT 100"""
        )
    
    return jsonify(musteriler)


@bp.route('/kaydet', methods=['POST'])
@giris_gerekli
def kaydet():
    """Yeni müşteri kaydı veya güncelleme"""
    try:
        data = request.get_json()
        
        musteri_id = data.get('id')
        
        if musteri_id:
            # Güncelleme
            execute("""
                UPDATE customers SET 
                    name = %s,
                    tax_number = %s,
                    phone = %s,
                    email = %s,
                    address = %s,
                    ev_adres = %s,
                    notes = %s
                WHERE id = %s
            """, (
                data.get('name'),
                data.get('tax_number'),
                data.get('phone'),
                data.get('email'),
                data.get('address'),
                data.get('ev_adres'),
                data.get('notes'),
                musteri_id
            ))
            
            return jsonify({'ok': True, 'mesaj': '✅ Müşteri güncellendi'})
        else:
            # Yeni kayıt
            result = execute_returning("""
                INSERT INTO customers (
                    name, tax_number, phone, email, address,
                    ev_adres, notes, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING id
            """, (
                data.get('name'),
                data.get('tax_number'),
                data.get('phone'),
                data.get('email'),
                data.get('address'),
                data.get('ev_adres'),
                data.get('notes')
            ))
            
            return jsonify({
                'ok': True, 
                'mesaj': f'✅ Müşteri kaydedildi (ID: {result["id"]})',
                'id': result['id']
            })
            
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': f'❌ Hata: {str(e)}'}), 400


@bp.route('/resim-yukle/<int:mid>', methods=['POST'])
@giris_gerekli
def resim_yukle(mid):
    """Müşteri dosyası yükle"""
    try:
        if 'file' not in request.files:
            return jsonify({'ok': False, 'mesaj': 'Dosya seçilmedi'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'ok': False, 'mesaj': 'Dosya seçilmedi'}), 400
        
        if file and allowed_file(file.filename):
            # Klasör oluştur
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            
            # Dosya adını güvenli hale getir
            filename = secure_filename(f"musteri_{mid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}")
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            
            # Kaydet
            file.save(filepath)
            
            return jsonify({'ok': True, 'mesaj': '✅ Dosya yüklendi', 'filename': filename})
        else:
            return jsonify({'ok': False, 'mesaj': 'Geçersiz dosya formatı'}), 400
            
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': f'❌ Hata: {str(e)}'}), 400


@bp.route('/sozlesme-olustur/<int:mid>')
@giris_gerekli
def sozlesme_olustur(mid):
    """Müşteri sözleşmesi oluştur (Word). ?indir=1 ile doğrudan Word indirilir; yoksa sayfa + Word İndir / WhatsApp."""
    try:
        musteri = fetch_one("SELECT c.* FROM customers c WHERE c.id = %s", (mid,))
        if not musteri:
            return "Müşteri bulunamadı", 404

        if request.args.get("indir") != "1":
            indir_url = url_for("giris.sozlesme_olustur", mid=mid, indir="1", tur=request.args.get("tur", ""))
            tel = (musteri.get("phone") or "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            num = ("90" + tel[1:]) if (tel and tel.startswith("0")) else ("90" + tel if tel else "")
            metin = "Sayın " + (musteri.get("name") or "Müşteri") + ",\n\nHizmet sözleşmeniz ekte yer almaktadır. İncelemenizi rica ederiz.\n\nİyi günler dileriz.\nBESTOFFICE"
            whatsapp_url = "https://wa.me/" + num + "?text=" + urllib.parse.quote(metin) if num else "https://wa.me/?text=" + urllib.parse.quote(metin)
            return render_template(
                "giris/sozlesme_olustur_sayfa.html",
                musteri=musteri,
                indir_url=indir_url,
                whatsapp_url=whatsapp_url,
            )

        # Sözleşme numarası otomatik oluştur
        # Hizmet türüne göre prefix belirle (SO/HO/PO)
        tur_raw = (request.args.get("tur") or "").lower()
        if "hazır" in tur_raw or "hazir" in tur_raw:
            prefix = "HO"
        elif "paylaşımlı" in tur_raw or "paylasimli" in tur_raw:
            prefix = "PO"
        else:
            prefix = "SO"

        today = datetime.now()
        tarih_kod = today.strftime("%d%m%y")  # Örn: 270225

        # Aynı gün ve aynı türdeki sözleşmeler için 600'den başlayan artan numara
        pattern = f"{prefix}{tarih_kod}-%"
        last = fetch_one(
            "SELECT sozlesme_no FROM sozlesmeler WHERE sozlesme_no LIKE %s ORDER BY sozlesme_no DESC LIMIT 1",
            (pattern,),
        )
        if last and last.get("sozlesme_no"):
            try:
                son_parca = str(last["sozlesme_no"]).split("-")[-1]
                sayac = int(son_parca) + 1
            except Exception:
                sayac = 600
        else:
            sayac = 600

        sozlesme_no = f"{prefix}{tarih_kod}-{sayac}"
        
        # Word belgesi oluştur
        doc = Document()
        
        # Başlık
        heading = doc.add_heading('OFİSBİR HİZMET SÖZLEŞMESİ', 0)
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Sözleşme No ve Tarih (aynı satırda)
        tarih_str = today.strftime('%d.%m.%Y')
        p_no = doc.add_paragraph()
        run_no = p_no.add_run(f"Sözleşme No: {sozlesme_no}    ")
        run_t = p_no.add_run(f"Tarih: {tarih_str}")
        doc.add_paragraph("")
        
        # MADDE 1 - TARAFLAR
        doc.add_heading('MADDE 1 - TARAFLAR', level=2)
        doc.add_paragraph("""
İşbu sözleşme, bir tarafta:

KİRAYA VEREN / HİZMET SAĞLAYICI:
Unvan: OFİSBİR Sanal Ofis Hizmetleri
Adres: Ankara, Türkiye

(Bundan böyle "KİRAYA VEREN" olarak anılacaktır.)

Diğer tarafta:

KİRACI / HİZMET ALAN:""")
        
        doc.add_paragraph(f"""
Unvan: {musteri['name']}
Vergi No: {musteri.get('tax_number') or '-'}
Vergi Dairesi: {musteri.get('vergi_dairesi') or '-'}
Adres: {musteri.get('address') or '-'}
Telefon: {musteri.get('phone') or '-'}
E-posta: {musteri.get('email') or '-'}

(Bundan böyle "KİRACI" olarak anılacaktır.)

arasında aşağıdaki şartlar dâhilinde akdedilmiştir.
        """)
        
        # MADDE 2 - SÖZLEŞMENİN KONUSU
        doc.add_heading('MADDE 2 - SÖZLEŞMENİN KONUSU', level=2)
        doc.add_paragraph(f"""
İşbu sözleşmenin konusu, KİRAYA VEREN'in mülkiyetinde bulunan adres üzerinde; KİRACI'nın işletme adresi olarak kullanması, posta ve kargo hizmetlerinden faydalanması, telefon santralı, sekreterlik ve diğer ofis hizmetlerinden yararlanması amacıyla tarafların hak ve yükümlülüklerinin belirlenmesidir.

Hizmet Türü: Sanal Ofis
Aylık Hizmet Bedeli: {musteri.get('aylik_kira', 0):.2f} TL + KDV
        """)
        
        # MADDE 3 - SÖZLEŞMENİN SÜRESİ
        doc.add_heading('MADDE 3 - SÖZLEŞMENİN SÜRESİ', level=2)
        doc.add_paragraph("""
İşbu sözleşme {sozlesme_baslangic} tarihinde başlamak üzere 1 (bir) yıl süre ile geçerlidir. 
Sözleşme süresi sonunda, taraflardan herhangi biri 1 ay önceden yazılı bildirimde bulunmadığı 
takdirde aynı şartlarla 1 yıl daha uzamış sayılır.
        """)
        
        # MADDE 4 - ÖDEME ŞARTLARI
        doc.add_heading('MADDE 4 - ÖDEME ŞARTLARI', level=2)
        doc.add_paragraph(f"""
4.1. Aylık hizmet bedeli {musteri.get('aylik_kira', 0):.2f} TL + KDV olup, her ayın 1-5'i 
arasında peşin olarak ödenecektir.

4.2. Ödemeler banka havalesi, EFT veya kredi kartı ile yapılabilir.

4.3. KİRACI'nın ödeme yükümlülüğünü yerine getirmemesi halinde, KİRAYA VEREN hizmetleri 
durdurma ve sözleşmeyi tek taraflı feshetme hakkına sahiptir.

4.4. Kira bedeli TÜFE artış oranına göre yıllık olarak güncellenecektir.
        """)
        
        # MADDE 5 - KİRAYA VEREN'İN YÜKÜMLÜLÜKLERİ
        doc.add_heading('MADDE 5 - KİRAYA VEREN\'İN YÜKÜMLÜLÜKLERİ', level=2)
        doc.add_paragraph("""
KİRAYA VEREN aşağıdaki hizmetleri sağlamayı taahhüt eder:

5.1. Sözleşme konusu adresin KİRACI'ya işletme adresi olarak tahsis edilmesi
5.2. Posta ve kargo kabul hizmeti
5.3. Telefon santralı ve çağrı yönlendirme hizmeti
5.4. Sekreterlik hizmeti (çalışma saatleri içinde)
5.5. Toplantı odası kullanımı (ücret karşılığı, rezervasyon ile)
5.6. Ortak alanların kullanımı
5.7. İnternet erişimi (ortak alanlarda)
        """)
        
        # MADDE 6 - KİRACI'NIN YÜKÜMLÜLÜKLERİ
        doc.add_heading('MADDE 6 - KİRACI\'NIN YÜKÜMLÜLÜKLERİ', level=2)
        doc.add_paragraph("""
6.1. Aylık hizmet bedelini zamanında ödemek
6.2. Verilen adresi yasalara ve ahlaka uygun şekilde kullanmak
6.3. Resmi kurumlardan gelen yazı ve bildirimleri düzenli takip etmek
6.4. Kargo ve posta takibini düzenli olarak yapmak
6.5. Toplantı odası kullanımını önceden rezerve ettirmek
6.6. Diğer müşterilere saygılı davranmak ve ortak alanları temiz kullanmak
6.7. Yasadışı faaliyetlerde bulunmamak
        """)
        
        # MADDE 7 - FESİH ŞARTLARI
        doc.add_heading('MADDE 7 - FESİH ŞARTLARI', level=2)
        doc.add_paragraph("""
7.1. Taraflardan herhangi biri, 1 ay önceden yazılı bildirimde bulunmak kaydıyla 
sözleşmeyi feshedebilir.

7.2. KİRACI'nın 2 ay üst üste ödeme yapmaması durumunda, KİRAYA VEREN sözleşmeyi 
tek taraflı olarak feshedebilir.

7.3. KİRACI'nın yasadışı faaliyetlerde bulunması, yasalara veya sözleşme şartlarına 
aykırı hareket etmesi durumunda KİRAYA VEREN derhal fesih hakkına sahiptir.

7.4. Fesih durumunda KİRACI, kullandığı hizmete ait tüm ödemelerini yapmakla yükümlüdür.
        """)
        
        # MADDE 8 - GİZLİLİK
        doc.add_heading('MADDE 8 - GİZLİLİK', level=2)
        doc.add_paragraph("""
8.1. Taraflar, sözleşme konusu hizmetler dolayısıyla öğrendiği karşı tarafa ait ticari 
sırları ve kişisel verileri gizli tutmayı ve üçüncü kişilerle paylaşmamayı taahhüt eder.

8.2. Bu yükümlülük sözleşmenin sona ermesinden sonra da 2 yıl süreyle devam eder.
        """)
        
        # MADDE 9 - UYUŞMAZLIKLARIN ÇÖZÜMÜ
        doc.add_heading('MADDE 9 - UYUŞMAZLIKLARIN ÇÖZÜMÜ', level=2)
        doc.add_paragraph("""
İşbu sözleşmeden doğabilecek her türlü uyuşmazlığın çözümünde Ankara Mahkemeleri 
ve İcra Daireleri yetkilidir.
        """)
        
        # MADDE 10 - YÜRÜRLÜK
        doc.add_heading('MADDE 10 - YÜRÜRLÜK', level=2)
        doc.add_paragraph(f"""
İşbu sözleşme {datetime.now().strftime('%d.%m.%Y')} tarihinde 2 (iki) nüsha olarak 
düzenlenmiş ve taraflarca okunup imzalanarak yürürlüğe girmiştir.
        """)
        
        # İmza alanları (sadeleştirilmiş)
        doc.add_paragraph("")
        doc.add_paragraph("KİRAYA VEREN / HİZMET SAĞLAYICI" + " " * 15 + "KİRACI / HİZMET ALAN")
        doc.add_paragraph(f"OFİSBİR Sanal Ofis Hizmetleri" + " " * 20 + f"{musteri['name']}")
        doc.add_paragraph("İmza: _______________" + " " * 30 + "İmza: _______________")
        
        # Dosya adı
        filename = f"Sozlesme_{sozlesme_no}_{musteri['name'].replace(' ', '_')}.docx"
        filepath = os.path.join('uploads', 'sozlesmeler', filename)
        
        # Klasör oluştur
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        # Kaydet
        doc.save(filepath)
        
        # Oluşan sözleşmeyi register et
        execute(
            "INSERT INTO sozlesmeler (musteri_id, sozlesme_no, hizmet_turu) VALUES (%s,%s,%s) ON CONFLICT (sozlesme_no) DO NOTHING",
            (mid, sozlesme_no, tur_raw or None),
        )

        # İndir
        return send_file(filepath, as_attachment=True, download_name=filename)
        
    except Exception as e:
        return f"Hata: {str(e)}", 500
@bp.route('/api/tufe-verileri')
@giris_gerekli
def api_tufe_verileri():
    """TCMB TÜFE verilerini getir"""
    try:
        veriler = fetch_all("""
            SELECT year as yil, month as ay, oran 
            FROM tufe_verileri 
            ORDER BY year DESC, 
            CASE month 
                WHEN 'Ocak' THEN 1 WHEN 'Şubat' THEN 2 WHEN 'Mart' THEN 3
                WHEN 'Nisan' THEN 4 WHEN 'Mayıs' THEN 5 WHEN 'Haziran' THEN 6
                WHEN 'Temmuz' THEN 7 WHEN 'Ağustos' THEN 8 WHEN 'Eylül' THEN 9
                WHEN 'Ekim' THEN 10 WHEN 'Kasım' THEN 11 WHEN 'Aralık' THEN 12
            END DESC
            LIMIT 60
        """)
        return jsonify(veriler)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/kira-senaryo-excel', methods=['POST'])
@giris_gerekli
def kira_senaryo_excel():
    """Kira senaryo Excel çıktısı"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        
        data = request.get_json()
        baslangic_kira = float(data.get('baslangic_kira'))
        baslangic_tarih = data.get('baslangic_tarih')
        yil_sayisi = int(data.get('yil_sayisi'))
        tufe_oran = float(data.get('tufe_oran')) / 100
        
        # Workbook oluştur
        wb = Workbook()
        ws = wb.active
        ws.title = "Kira Senaryo"
        
        # Başlık
        ws['A1'] = 'KİRA SENARYO HESAPLAMA'
        ws['A1'].font = Font(bold=True, size=14)
        ws.merge_cells('A1:E1')
        ws['A1'].alignment = Alignment(horizontal='center')
        
        # Parametreler
        ws['A3'] = 'Başlangıç Kira:'
        ws['B3'] = baslangic_kira
        ws['A4'] = 'Başlangıç Tarihi:'
        ws['B4'] = baslangic_tarih
        ws['A5'] = 'Yıl Sayısı:'
        ws['B5'] = yil_sayisi
        ws['A6'] = 'TÜFE Oranı:'
        ws['B6'] = f"{tufe_oran * 100}%"
        
        # Tablo başlıkları
        headers = ['Yıl', 'Aylık Kira', 'Yıllık Toplam', 'Artış %', 'Artış Tutar']
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=8, column=col)
            cell.value = header
            cell.font = Font(bold=True)
            cell.fill = PatternFill(start_color='0097A7', end_color='0097A7', fill_type='solid')
            cell.alignment = Alignment(horizontal='center')
        
        # Hesaplama
        toplam_gelir = 0
        mevcut_kira = baslangic_kira
        yil = int(baslangic_tarih.split('-')[0])
        
        for i in range(1, yil_sayisi + 1):
            yillik_toplam = mevcut_kira * 12
            toplam_gelir += yillik_toplam
            
            artis_oran = 0 if i == 1 else tufe_oran * 100
            artis_tutar = 0 if i == 1 else mevcut_kira - (mevcut_kira / (1 + tufe_oran))
            
            row = 8 + i
            ws.cell(row=row, column=1).value = yil + i - 1
            ws.cell(row=row, column=2).value = mevcut_kira
            ws.cell(row=row, column=2).number_format = '#,##0.00'
            ws.cell(row=row, column=3).value = yillik_toplam
            ws.cell(row=row, column=3).number_format = '#,##0.00'
            ws.cell(row=row, column=4).value = artis_oran / 100
            ws.cell(row=row, column=4).number_format = '0.00%'
            ws.cell(row=row, column=5).value = artis_tutar
            ws.cell(row=row, column=5).number_format = '#,##0.00'
            
            # Bir sonraki yıl için kira hesapla (şu anki yılın TÜFE'si ile)
            if i < yil_sayisi:
                mevcut_kira = mevcut_kira * (1 + tufe_oran)

        
        # Toplam
        son_satir = 8 + yil_sayisi + 2
        ws.cell(row=son_satir, column=1).value = f'TOPLAM ({yil_sayisi} Yıl):'
        ws.cell(row=son_satir, column=1).font = Font(bold=True)
        ws.cell(row=son_satir, column=3).value = toplam_gelir
        ws.cell(row=son_satir, column=3).number_format = '#,##0.00'
        ws.cell(row=son_satir, column=3).font = Font(bold=True)
        ws.cell(row=son_satir, column=3).fill = PatternFill(start_color='4CAF50', end_color='4CAF50', fill_type='solid')
        
        # Sütun genişlikleri
        ws.column_dimensions['A'].width = 15
        ws.column_dimensions['B'].width = 18
        ws.column_dimensions['C'].width = 18
        ws.column_dimensions['D'].width = 15
        ws.column_dimensions['E'].width = 18
        
        # Dosya kaydet
        filename = f"Kira_Senaryo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        filepath = os.path.join('uploads', 'raporlar', filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        wb.save(filepath)
        
        return send_file(filepath, as_attachment=True, download_name=filename)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _tarih_fmt(s):
    """YYYY-MM-DD veya DD.MM.YYYY -> DD.MM.YYYY"""
    if not s:
        return ""
    s = str(s).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        parts = s[:10].split("-")
        return f"{parts[2]}.{parts[1]}.{parts[0]}"
    return s[:10] if len(s) >= 10 else s


def build_kira_bildirgesi_pdf(musteri_adi, sozlesme_tarihi, gecerlilik_tarihi, kira_net, kdv_oran=20, hizmet_turu="sanal_ofis"):
    """Kira bildirgesi mektubu A4 PDF (bestoffice / Ofisbir). Arial ile Türkçe karakter desteği.
    hizmet_turu: sanal_ofis -> yıllık kira ibaresi eklenir; hazir_ofis, paylasimli_ofis, oda -> yıllık ibare yok.
    """
    _register_arial()
    buf = io.BytesIO()
    w, h = A4
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Kira Bildirgesi")
    font_name = "Arial" if "Arial" in pdfmetrics.getRegisteredFontNames() else "Helvetica"

    kira_net = float(kira_net or 0)
    kdv_oran = float(kdv_oran or 20)
    kdv_dahil = round(kira_net * (1 + kdv_oran / 100), 2)
    yillik = round(kdv_dahil * 12, 2)
    sanal_ofis = (str(hizmet_turu or "").strip().lower() == "sanal_ofis")

    soz_fmt = _tarih_fmt(sozlesme_tarihi)
    gec_fmt = _tarih_fmt(gecerlilik_tarihi)

    y = 22
    c.setFont(font_name, 9)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(15 * mm, h - y * mm, "Ofisbir Ofis ve Danışmanlık Hizmetleri A.Ş.")
    y += 20

    hitap_adi = (musteri_adi or "").strip() or "Değerli Kiracımız"
    c.setFont(font_name, 11)
    c.drawString(15 * mm, h - y * mm, "Sayın " + hitap_adi + ",")
    y += 12

    par1 = f"Tarafınızla {soz_fmt} tarihinde imzalanmış olan kira sözleşmesi gereği, {gec_fmt} tarihi itibarıyla kira bedeli güncellemesi yapılması gerekmektedir."
    c.setFont(font_name, 10)
    for chunk in (par1[i:i+95] for i in range(0, len(par1), 95)):
        c.drawString(15 * mm, h - y * mm, chunk)
        y += 5
    y += 6

    par2_base = (
        f"Mevcut ekonomik koşullar ve yasal düzenlemeler göz önüne alınarak, adı geçen tarihten itibaren uygulanacak yeni kira bedeli TÜFE Yasal Oranı çerçevesinde güncellenecektir. "
        f"Buna göre, {gec_fmt} itibarıyla aylık kira bedeliniz {kira_net:,.2f} TL + %{int(kdv_oran)} KDV dahil {kdv_dahil:,.2f} TL dir."
    )
    if sanal_ofis:
        par2 = par2_base + f" KDV Dahil yıllık {yillik:,.2f} TL dir."
    else:
        par2 = par2_base + " "
    for chunk in (par2[i:i+95] for i in range(0, len(par2), 95)):
        c.drawString(15 * mm, h - y * mm, chunk)
        y += 5
    y += 10

    par3 = "Anlayışınız ve iş birliğiniz için teşekkür eder, sorularınız veya ek talepleriniz olması durumunda bizimle iletişime geçmekten çekinmemenizi rica ederiz."
    for chunk in (par3[i:i+95] for i in range(0, len(par3), 95)):
        c.drawString(15 * mm, h - y * mm, chunk)
        y += 5
    y += 14

    right_margin = w - 20 * mm
    c.setFont(font_name, 10)
    c.drawRightString(right_margin, h - y * mm, "Saygılarımızla,")
    y += 10
    c.setFont(font_name, 11)
    c.drawRightString(right_margin, h - y * mm, "BESTOFFICE")
    y += 6
    c.setFont(font_name, 9)
    unvan_text = "Ofisbir Ofis ve Danışmanlık Hizmetleri A.Ş."
    w_best = c.stringWidth("BESTOFFICE", font_name, 11)
    w_unvan = c.stringWidth(unvan_text, font_name, 9)
    unvan_x = right_margin - w_best / 2 - w_unvan / 2
    c.drawString(unvan_x, h - y * mm, unvan_text)

    c.save()
    buf.seek(0)
    return buf.getvalue()


def _kira_bildirgesi_metinleri(sozlesme_tarihi, gecerlilik_tarihi, kira_net, kdv_oran, hizmet_turu="sanal_ofis"):
    """Kira bildirgesi paragraf metinlerini döndürür (HTML şablonu için)."""
    soz_fmt = _tarih_fmt(sozlesme_tarihi)
    gec_fmt = _tarih_fmt(gecerlilik_tarihi)
    kira_net = float(kira_net or 0)
    kdv_oran = float(kdv_oran or 20)
    kdv_dahil = round(kira_net * (1 + kdv_oran / 100), 2)
    yillik = round(kdv_dahil * 12, 2)
    sanal_ofis = (str(hizmet_turu or "").strip().lower() == "sanal_ofis")
    par1 = f"Tarafınızla {soz_fmt} tarihinde imzalanmış olan kira sözleşmesi gereği, {gec_fmt} tarihi itibarıyla kira bedeli güncellemesi yapılması gerekmektedir."
    par2 = (f"Mevcut ekonomik koşullar ve yasal düzenlemeler göz önüne alınarak, adı geçen tarihten itibaren uygulanacak yeni kira bedeli TÜFE Yasal Oranı çerçevesinde güncellenecektir. "
            f"Buna göre, {gec_fmt} itibarıyla aylık kira bedeliniz {kira_net:,.2f} TL + %{int(kdv_oran)} KDV dahil {kdv_dahil:,.2f} TL dir.")
    if sanal_ofis:
        par2 += f" KDV Dahil yıllık {yillik:,.2f} TL dir."
    par3 = "Anlayışınız ve iş birliğiniz için teşekkür eder, sorularınız veya ek talepleriniz olması durumunda bizimle iletişime geçmekten çekinmemenizi rica ederiz."
    return par1, par2, par3


@bp.route('/kira-bildirgesi-antet')
@giris_gerekli
def kira_bildirgesi_antet():
    """Antetli kira bildirgesi HTML sayfası (önizleme / yazdır)."""
    musteri_adi = (request.args.get('musteri_adi') or '').strip() or 'Müşteri Adı'
    sozlesme_tarihi = request.args.get('sozlesme_tarihi') or ''
    gecerlilik_tarihi = request.args.get('gecerlilik_tarihi') or ''
    try:
        kira_net = float(request.args.get('kira_net') or 0)
        kdv_oran = float(request.args.get('kdv_oran') or 20)
    except (TypeError, ValueError):
        kira_net, kdv_oran = 0, 20
    hizmet_turu = (request.args.get('hizmet_turu') or 'sanal_ofis').strip().lower().replace(" ", "_")
    if not gecerlilik_tarihi:
        gecerlilik_tarihi = sozlesme_tarihi or datetime.now().strftime("%Y-%m-%d")
    par1, par2, par3 = _kira_bildirgesi_metinleri(sozlesme_tarihi, gecerlilik_tarihi, kira_net, kdv_oran, hizmet_turu)
    hitap_adi = (musteri_adi or "").strip() or "Değerli Kiracımız"
    return render_template(
        'giris/kira_bildirgesi_antet.html',
        musteri_adi=musteri_adi,
        hitap_adi=hitap_adi,
        par1=par1,
        par2=par2,
        par3=par3
    )


@bp.route('/kira-bildirgesi-pdf', methods=['POST'])
@giris_gerekli
def kira_bildirgesi_pdf():
    """Kira bildirgesi PDF oluştur (önizleme / yazdır)."""
    try:
        data = request.get_json()
        musteri_adi = (data.get('musteri_adi') or '').strip() or 'Değerli Kiracımız'
        sozlesme_tarihi = data.get('sozlesme_tarihi') or ''
        gecerlilik_tarihi = data.get('gecerlilik_tarihi') or ''
        kira_net = float(data.get('kira_net') or 0)
        kdv_oran = float(data.get('kdv_oran') or 20)
        if not gecerlilik_tarihi:
            return jsonify({'ok': False, 'mesaj': 'Geçerlilik tarihi giriniz.'}), 400
        if kira_net <= 0:
            return jsonify({'ok': False, 'mesaj': 'Kira tutarı 0\'dan büyük olmalıdır.'}), 400
        hizmet_turu = (data.get('hizmet_turu') or 'sanal_ofis').strip().lower().replace(" ", "_")
        pdf_bytes = build_kira_bildirgesi_pdf(musteri_adi, sozlesme_tarihi, gecerlilik_tarihi, kira_net, kdv_oran, hizmet_turu=hizmet_turu)
        return Response(pdf_bytes, mimetype="application/pdf", headers={
            "Content-Disposition": "inline; filename=Kira_Bildirgesi.pdf"
        })
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': str(e)}), 500


# ── Cari Kart API ───────────────────────────────────────────────────────────

def _cari_hareketler(musteri_id):
    """Fatura (borç) ve tahsilat (alacak) satırlarını tarih sırasına göre birleştirip bakiye hesaplar."""
    faturalar = fetch_all(
        """SELECT id, fatura_no AS belge_no, fatura_tarihi AS tarih, COALESCE(toplam, tutar, 0) AS tutar, 'Fatura' AS tur, vade_tarihi
           FROM faturalar WHERE musteri_id = %s ORDER BY fatura_tarihi, id""",
        (musteri_id,)
    )
    tahsilatlar = fetch_all(
        """SELECT id, COALESCE(makbuz_no, 'Makbuz-' || id) AS belge_no, tahsilat_tarihi AS tarih, tutar, 'Tahsilat' AS tur
           FROM tahsilatlar WHERE musteri_id = %s ORDER BY tahsilat_tarihi, id""",
        (musteri_id,)
    )
    rows = []
    for r in faturalar:
        rows.append({
            "id": r.get("id"), "belge_no": r.get("belge_no") or "", "tarih": str(r.get("tarih") or "")[:10],
            "tur": "Fatura", "borc": float(r.get("tutar") or 0), "alacak": 0, "vade_tarihi": str(r.get("vade_tarihi") or "")[:10] if r.get("vade_tarihi") else None
        })
    for r in tahsilatlar:
        rows.append({
            "id": "t-" + str(r.get("id")), "belge_no": r.get("belge_no") or "", "tarih": str(r.get("tarih") or "")[:10],
            "tur": "Tahsilat", "borc": 0, "alacak": float(r.get("tutar") or 0), "vade_tarihi": None
        })
    rows.sort(key=lambda x: (x["tarih"], x["tur"] == "Fatura" and 0 or 1))
    bakiye = 0
    for r in rows:
        bakiye = bakiye + r["borc"] - r["alacak"]
        r["bakiye"] = round(bakiye, 2)
    return rows


def _risk_skoru_hesapla(musteri_id, gecikmis_gun, gecikmis_tutar):
    """Gecikme ve tutara göre 1-100 risk skoru. 50 altı kritik."""
    if not gecikmis_gun and (not gecikmis_tutar or gecikmis_tutar <= 0):
        return 85
    if gecikmis_gun and gecikmis_gun > 60:
        return max(1, 40 - (gecikmis_gun // 30) * 5)
    if gecikmis_gun and gecikmis_gun > 30:
        return 55
    return 70


@bp.route('/api/cari-kart/<int:mid>')
@giris_gerekli
def api_cari_kart(mid):
    """Cari kart verisi: özet (bakiye, gecikmiş, bu ay tahsilat, risk, aging), hareketler, finansal profil."""
    cust = fetch_one("SELECT * FROM customers WHERE id = %s", (mid,))
    if not cust:
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı."}), 404
    bugun = date.today()
    # Ödenmemiş faturalar toplamı (gecikmiş tutar)
    faturalar_odenmemis = fetch_all(
        """SELECT id, fatura_no, fatura_tarihi, vade_tarihi, COALESCE(toplam, tutar, 0) AS toplam
           FROM faturalar WHERE musteri_id = %s AND COALESCE(durum, '') != 'odendi'""",
        (mid,)
    )
    toplam_borc = sum(float(f.get("toplam") or 0) for f in faturalar_odenmemis)
    gecikmis_gun = 0
    min_vade = None
    for f in faturalar_odenmemis:
        vd = f.get("vade_tarihi")
        if vd:
            if hasattr(vd, "year"):
                vd = vd
            else:
                try:
                    vd = datetime.strptime(str(vd)[:10], "%Y-%m-%d").date()
                except Exception:
                    continue
            if vd < bugun:
                gun = (bugun - vd).days
                if gun > gecikmis_gun:
                    gecikmis_gun = gun
            if min_vade is None or (vd and vd < min_vade):
                min_vade = vd
    if min_vade and min_vade < bugun:
        gecikmis_gun = (bugun - min_vade).days
    bu_ay_bas = bugun.replace(day=1)
    bu_ay_tahsilat = fetch_one(
        """SELECT COALESCE(SUM(tutar), 0) AS t FROM tahsilatlar
           WHERE musteri_id = %s
             AND tahsilat_tarihi::date >= %s
             AND tahsilat_tarihi::date < %s""",
        (mid, bu_ay_bas, bu_ay_bas + timedelta(days=32))
    )
    bu_ay_tahsilat = float(bu_ay_tahsilat.get("t", 0) or 0) if bu_ay_tahsilat else 0
    aging_0_30 = aging_31_60 = aging_61_90 = aging_91 = 0
    for f in faturalar_odenmemis:
        vd = f.get("vade_tarihi")
        if not vd:
            continue
        try:
            if not hasattr(vd, "year"):
                vd = datetime.strptime(str(vd)[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        gun = (bugun - vd).days
        tutar = float(f.get("toplam") or 0)
        if gun <= 30:
            aging_0_30 += tutar
        elif gun <= 60:
            aging_31_60 += tutar
        elif gun <= 90:
            aging_61_90 += tutar
        else:
            aging_91 += tutar
    risk_skoru = _risk_skoru_hesapla(mid, gecikmis_gun, toplam_borc)
    hareketler = _cari_hareketler(mid)
    profil = fetch_one("SELECT * FROM customer_financial_profile WHERE musteri_id = %s", (mid,))
    is_admin = getattr(current_user, "role", None) == "admin"
    payload = {
        "ok": True,
        "musteri": {
            "id": cust.get("id"), "name": cust.get("name"), "tax_number": cust.get("tax_number"),
            "phone": cust.get("phone"), "email": cust.get("email"), "address": cust.get("address"),
            "vergi_dairesi": cust.get("vergi_dairesi"), "mersis_no": cust.get("mersis_no"),
            "nace_kodu": cust.get("nace_kodu"), "ofis_tipi": cust.get("ofis_tipi"),
        },
        "ozet": {
            "guncel_bakiye": round(toplam_borc, 2),
            "gecikmis_tutar": round(toplam_borc, 2),
            "gecikmis_gun": gecikmis_gun,
            "bu_ayki_tahsilat": round(bu_ay_tahsilat, 2),
            "risk_skoru": risk_skoru,
            "aging_0_30": round(aging_0_30, 2),
            "aging_31_60": round(aging_31_60, 2),
            "aging_61_90": round(aging_61_90, 2),
            "aging_91_plus": round(aging_91, 2),
        },
        "hareketler": hareketler,
        "finansal_profil": None,
    }
    if profil:
        payload["finansal_profil"] = {
            "tahmini_odeme_gunu": profil.get("tahmini_odeme_gunu"),
            "yillik_karlilik_endeksi": float(profil.get("yillik_karlilik_endeksi") or 0),
            "hukuki_esk_puan": profil.get("hukuki_esk_puan"),
            "mutabakat_tarihi": str(profil.get("mutabakat_tarihi"))[:10] if profil.get("mutabakat_tarihi") else None,
            "vade_gunu": profil.get("vade_gunu"),
        }
        if is_admin:
            payload["finansal_profil"]["ic_not"] = profil.get("ic_not")
            payload["finansal_profil"]["hukuki_surec"] = profil.get("hukuki_surec")
    return jsonify(payload)


@bp.route('/api/cari-kart-pdf/<int:mid>')
@giris_gerekli
def api_cari_kart_pdf(mid):
    """Cari hareketleri BestOffice antetli PDF ekstre olarak indir."""
    cust = fetch_one("SELECT id, name, tax_number FROM customers WHERE id = %s", (mid,))
    if not cust:
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı."}), 404
    hareketler = _cari_hareketler(mid)
    _register_arial()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 40
    try:
        c.setFont("Arial", 16)
    except Exception:
        c.setFont("Helvetica", 16)
    c.drawString(40, y, "BestOffice - Cari Ekstre")
    y -= 24
    c.setFont("Helvetica", 10)
    c.drawString(40, y, "Müşteri: " + (cust.get("name") or ""))
    c.drawString(40, y - 14, "Vergi No: " + (cust.get("tax_number") or ""))
    y -= 40
    c.drawString(40, y, "Tarih")
    c.drawString(120, y, "Belge No")
    c.drawString(220, y, "Tür")
    c.drawString(300, y, "Borç")
    c.drawString(380, y, "Alacak")
    c.drawString(460, y, "Bakiye")
    y -= 6
    c.line(40, y, 520, y)
    y -= 14
    for row in hareketler:
        if y < 80:
            c.showPage()
            y = h - 40
        c.drawString(40, y, (row.get("tarih") or "")[:10])
        c.drawString(120, y, (row.get("belge_no") or "")[:18])
        c.drawString(220, y, row.get("tur") or "")
        c.drawString(300, y, "{:,.2f}".format(row.get("borc") or 0))
        c.drawString(380, y, "{:,.2f}".format(row.get("alacak") or 0))
        c.drawString(460, y, "{:,.2f}".format(row.get("bakiye") or 0))
        y -= 14
    c.save()
    buf.seek(0)
    return Response(buf.read(), mimetype="application/pdf", headers={
        "Content-Disposition": "attachment; filename=Cari_Ekstre_%s.pdf" % (cust.get("name") or "musteri").replace(" ", "_")[:30]
    })