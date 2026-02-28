from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, send_file, Response
from flask_login import login_required, current_user
from functools import wraps
from datetime import datetime
from db import fetch_all, fetch_one, execute, execute_returning
import os
import io
import re
import json
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

bp = Blueprint('faturalar', __name__, url_prefix='/faturalar')

# Tahsilat makbuzu firma bilgileri (.env'den FIRMA_VERGI_DAIRESI, FIRMA_VERGI_NO eklenebilir)
FIRMA_UNVAN = "OFİSBİR Sanal Ofis Hizmetleri"
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
    buf = io.BytesIO()
    w_pt, h_pt = A5  # 420, 595
    c = canvas.Canvas(buf, pagesize=A5)
    c.setTitle("Tahsilat Makbuzu")
    h = h_pt

    y = 15
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(w_pt / 2, h - y * mm, "TAHSİLAT MAKBUZU")
    y += 8
    c.setFont("Helvetica", 8)
    makbuz_no = tahsilat.get("makbuz_no") or f"{datetime.now().year}-????"
    tarih_saat = _tarih_saat_str(tahsilat.get("tahsilat_tarihi"), tahsilat.get("created_at"))
    c.drawCentredString(w_pt / 2, h - y * mm, f"Belge No: {makbuz_no}   Tarih: {tarih_saat}")
    y += 6
    c.line(MARGIN_MM * mm, h - y * mm, RIGHT_MM * mm, h - y * mm)
    y += 8

    c.setFont("Helvetica", 9)
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
    c.setFont("Helvetica", 8)
    c.drawString(52 * mm, h - y * mm, (tutar_yaziya(tutar) or "")[:55])
    c.setFont("Helvetica", 9)
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
        c.setFont("Helvetica-Bold", 8)
        c.drawString(MARGIN_MM * mm, h - y * mm, "Çek detayları")
        y += 5
        col_w = (RIGHT_MM - MARGIN_MM) / 7
        cols = ["SIRA", "ÇEK NO", "HESAP NO", "BANKA", "ŞUBE", "VADE", "TUTAR"]
        for i, col in enumerate(cols):
            c.drawString((MARGIN_MM + i * col_w) * mm, h - y * mm, col[:10])
        y += 4
        c.setFont("Helvetica", 7)
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
        c.setFont("Helvetica", 9)

    aciklama = (tahsilat.get("aciklama") or "").strip() or "—"
    c.drawString(MARGIN_MM * mm, h - y * mm, "Açıklama:")
    y += 4
    c.setFont("Helvetica", 8)
    for chunk in (aciklama[i:i+48] for i in range(0, len(aciklama), 48)):
        c.drawString(MARGIN_MM * mm, h - y * mm, chunk)
        y += 4
    if fatura_no:
        c.drawString(MARGIN_MM * mm, h - y * mm, f"İlgili Fatura: {fatura_no}")
        y += 4
    y += 4

    if banka_hesaplar:
        c.setFont("Helvetica-Bold", 8)
        c.drawString(MARGIN_MM * mm, h - y * mm, "Ödeme yapılabilecek hesaplarımız (TL IBAN):")
        y += 4
        c.setFont("Helvetica", 7)
        for b in banka_hesaplar[:5]:
            ad = (b.get("banka_adi") or "") + " - " + (b.get("hesap_adi") or "")
            iban = (b.get("iban") or "")[:32]
            c.drawString(MARGIN_MM * mm, h - y * mm, (ad or "—")[:40])
            y += 3
            c.drawString(MARGIN_MM * mm, h - y * mm, iban or "—")
            y += 4
        y += 2
        c.setFont("Helvetica", 9)

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


def faturalar_gerekli(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated_function


@bp.route('/')
@faturalar_gerekli
def index():
    """Finans ana sayfası - Faturalar ve Tahsilatlar sekmeleri"""
    return render_template('faturalar/finans.html')


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
    
    faturalar = fetch_all(sql, tuple(params))
    
    # Toplamlar
    toplam_tutar = sum(f['toplam'] or 0 for f in faturalar)
    toplam_odenen = sum(f['toplam'] or 0 for f in faturalar if f['durum'] == 'odendi')
    toplam_kalan = toplam_tutar - toplam_odenen
    
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
    
    tahsilatlar_list = fetch_all("""
        SELECT t.*, c.name as musteri_adi, f.fatura_no
        FROM tahsilatlar t
        LEFT JOIN customers c ON COALESCE(t.customer_id, t.musteri_id) = c.id
        LEFT JOIN faturalar f ON t.fatura_id = f.id
        WHERE EXTRACT(YEAR FROM (t.tahsilat_tarihi::date)) = %s
        ORDER BY (t.tahsilat_tarihi::date) DESC
    """, (yil,))
    
    toplam = sum(t['tutar'] for t in tahsilatlar_list)
    
    return render_template('faturalar/tahsilatlar_tab.html',
                         yil=yil,
                         tahsilatlar=tahsilatlar_list,
                         toplam=toplam)


@bp.route('/fatura-ekle', methods=['POST'])
@faturalar_gerekli
def fatura_ekle():
    """Yeni fatura ekle"""
    try:
        data = request.get_json()
        
        execute("""
            INSERT INTO faturalar (
                fatura_no, musteri_id, musteri_adi, tutar, kdv_tutar, 
                toplam, durum, fatura_tarihi, vade_tarihi, notlar
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            data.get('fatura_no'),
            data.get('musteri_id'),
            data.get('musteri_adi'),
            data.get('tutar', 0),
            data.get('kdv_tutar', 0),
            data.get('toplam', 0),
            data.get('durum', 'odenmedi'),
            data.get('fatura_tarihi'),
            data.get('vade_tarihi'),
            data.get('notlar')
        ))
        
        return jsonify({'ok': True, 'mesaj': 'Fatura eklendi!'})
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': str(e)}), 500


@bp.route('/tahsilat-ekle', methods=['POST'])
@faturalar_gerekli
def tahsilat_ekle():
    """Yeni tahsilat ekle; makbuz no üretir, A5 PDF oluşturup müşteri dosyasına kaydeder."""
    try:
        data = request.get_json()
        musteri_id = data.get('musteri_id')
        if not musteri_id:
            return jsonify({'ok': False, 'mesaj': 'Müşteri seçiniz.'}), 400
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
                customer_id, fatura_id, tutar, odeme_turu,
                tahsilat_tarihi, aciklama, makbuz_no, cek_detay, havale_banka
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, fatura_id, makbuz_no, tutar, odeme_turu, tahsilat_tarihi, aciklama, created_at, cek_detay, havale_banka
        """, (
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
        musteri_adi = (musteri or {}).get("name") or "Müşteri"
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
    """Tahsilat formu için müşteri listesi."""
    arama = request.args.get('q', '').strip()
    if arama:
        rows = fetch_all(
            "SELECT id, name FROM customers WHERE name ILIKE %s OR tax_number::text ILIKE %s ORDER BY name LIMIT 50",
            (f'%{arama}%', f'%{arama}%')
        )
    else:
        rows = fetch_all("SELECT id, name FROM customers ORDER BY name LIMIT 100")
    return jsonify([{"id": r["id"], "name": r["name"]} for r in rows])


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