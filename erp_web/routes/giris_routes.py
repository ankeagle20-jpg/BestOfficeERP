"""
Giriş / Müşteri Kaydı Routes
Desktop'taki gibi tam fonksiyonel + Sözleşme oluşturma
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
from auth import giris_gerekli
from db import fetch_all, fetch_one, execute, execute_returning
from datetime import datetime
from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
import os
from werkzeug.utils import secure_filename

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
                    office_code = %s,
                    notes = %s
                WHERE id = %s
            """, (
                data.get('name'),
                data.get('tax_number'),
                data.get('phone'),
                data.get('email'),
                data.get('address'),
                data.get('office_code'),
                data.get('notes'),
                musteri_id
            ))
            
            return jsonify({'ok': True, 'mesaj': '✅ Müşteri güncellendi'})
        else:
            # Yeni kayıt
            result = execute_returning("""
                INSERT INTO customers (
                    name, tax_number, phone, email, address, 
                    office_code, notes, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING id
            """, (
                data.get('name'),
                data.get('tax_number'),
                data.get('phone'),
                data.get('email'),
                data.get('address'),
                data.get('office_code'),
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
    """Müşteri sözleşmesi oluştur (Word)"""
    try:
        # Müşteri bilgilerini getir
        musteri = fetch_one("""
            SELECT c.*, o.office_type, o.office_number, o.monthly_rent
            FROM customers c
            LEFT JOIN offices o ON c.office_code = o.code
            WHERE c.id = %s
        """, (mid,))
        
        if not musteri:
            return "Müşteri bulunamadı", 404
        
        # Word belgesi oluştur
        doc = Document()
        
        # Başlık
        heading = doc.add_heading('OFİS KİRALAMA SÖZLEŞMESİ', 0)
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Tarih
        doc.add_paragraph(f"Tarih: {datetime.now().strftime('%d.%m.%Y')}")
        doc.add_paragraph("")
        
        # Taraflar
        doc.add_heading('TARAFLAR', 1)
        doc.add_paragraph(f"""
KİRAYA VEREN: OFİSBİR Sanal Ofis Hizmetleri
Adres: Ankara, Türkiye
        """)
        
        doc.add_paragraph(f"""
KİRACI: {musteri['name']}
Vergi No: {musteri['tax_number'] or '-'}
Adres: {musteri['address'] or '-'}
Telefon: {musteri['phone'] or '-'}
E-posta: {musteri['email'] or '-'}
        """)
        
        # Sözleşme Konusu
        doc.add_heading('SÖZLEŞME KONUSU', 1)
        doc.add_paragraph(f"""
İşbu sözleşme, kiracının {musteri.get('office_type', 'Sanal Ofis')} hizmetinden 
faydalanması amacıyla düzenlenmiştir.

Ofis Tipi: {musteri.get('office_type', '-')}
Ofis No: {musteri.get('office_number', '-')}
Aylık Kira: {musteri.get('monthly_rent', 0):.2f} TL
        """)
        
        # Madde 1 - Süre
        doc.add_heading('MADDE 1 - SÜRE', 1)
        doc.add_paragraph("""
İşbu sözleşme, imza tarihinden itibaren geçerli olup, 1 (bir) yıl süreyle yürürlükte kalacaktır.
Taraflardan herhangi biri 1 ay önceden yazılı bildirimde bulunmak kaydıyla sözleşmeyi feshedebilir.
        """)
        
        # Madde 2 - Kira Bedeli
        doc.add_heading('MADDE 2 - KİRA BEDELİ VE ÖDEME', 1)
        doc.add_paragraph(f"""
Aylık kira bedeli {musteri.get('monthly_rent', 0):.2f} TL olup, her ayın 1-5'i arasında 
peşin olarak ödenecektir. Kira ödemeleri banka havalesi veya kredi kartı ile yapılabilir.

TÜFE artışlarına göre yıllık kira artışı uygulanacaktır.
        """)
        
        # Madde 3 - Tarafların Yükümlülükleri
        doc.add_heading('MADDE 3 - TARAFLARIN YÜKÜMLÜLÜKLERİ', 1)
        doc.add_paragraph("""
Kiralayan Yükümlülükleri:
- Ofis alanını kullanıma hazır halde teslim etmek
- Telefon santralı ve sekreterlik hizmeti sağlamak
- Posta ve kargo kabul hizmeti sunmak
- İnternet ve ortak alanları kullanıma sunmak

Kiracı Yükümlülükleri:
- Kira bedelini zamanında ödemek
- Ofis kurallarına uymak
- Yasal mevzuata uygun faaliyet göstermek
- Ofis alanını iyi kullanmak ve korumak
        """)
        
        # Madde 4 - Fesih
        doc.add_heading('MADDE 4 - FESİH', 1)
        doc.add_paragraph("""
Kiracının kira bedelini 2 ay üst üste ödememesi, yasalara aykırı faaliyet göstermesi 
veya sözleşme şartlarını ağır şekilde ihlal etmesi durumunda, kiralayan sözleşmeyi 
tek taraflı olarak feshedebilir.
        """)
        
        # Madde 5 - Genel Hükümler
        doc.add_heading('MADDE 5 - GENEL HÜKÜMLER', 1)
        doc.add_paragraph("""
İşbu sözleşmeden doğabilecek ihtilaflarda Ankara Mahkemeleri ve İcra Daireleri yetkilidir.

Sözleşme 2 (iki) nüsha olarak düzenlenmiş ve taraflarca imzalanmıştır.
        """)
        
        # İmza alanları
        doc.add_paragraph("")
        doc.add_paragraph("")
        doc.add_paragraph("_" * 30 + "                    " + "_" * 30)
        doc.add_paragraph("KİRAYA VEREN" + " " * 35 + "KİRACI")
        doc.add_paragraph(f"OFİSBİR" + " " * 40 + f"{musteri['name']}")
        
        # Dosya adı
        filename = f"Sozlesme_{musteri['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.docx"
        filepath = os.path.join('uploads', 'sozlesmeler', filename)
        
        # Klasör oluştur
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        # Kaydet
        doc.save(filepath)
        
        # İndir
        return send_file(filepath, as_attachment=True, download_name=filename)
        
    except Exception as e:
        return f"Hata: {str(e)}", 500


@bp.route('/sil/<int:mid>', methods=['POST'])
@giris_gerekli
def sil(mid):
    """Müşteri sil"""
    try:
        execute("DELETE FROM customers WHERE id = %s", (mid,))
        return jsonify({'ok': True, 'mesaj': '✅ Müşteri silindi'})
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': f'❌ Hata: {str(e)}'}), 400