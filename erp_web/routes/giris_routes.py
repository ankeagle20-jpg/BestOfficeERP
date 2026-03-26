"""
Giriş / Müşteri Kaydı Routes
Desktop'taki gibi tam fonksiyonel + Sözleşme oluşturma
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, Response
from flask_login import current_user
from auth import giris_gerekli
from db import fetch_all, fetch_one, execute, execute_returning, ensure_hizmet_turleri_table, ensure_faturalar_amount_columns, db as get_db
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
from utils.text_utils import turkish_lower
from utils.musteri_arama import customers_arama_sql_3_plus_tax_office, customers_arama_params_5

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
    embed = str(request.args.get('embed') or '').lower() in ('1', 'true', 'yes', 'on')
    return render_template('giris/index.html', embed=embed)


@bp.route('/api/potansiyel', methods=['GET', 'POST'])
@giris_gerekli
def api_potansiyel():
    """Potansiyel müşteri (CRM lead) listesi + ekleme/güncelleme."""
    if request.method == 'GET':
        arama = (request.args.get('q') or '').strip()
        durum = (request.args.get('durum') or '').strip() or None
        params = []
        where = []
        if arama:
            norm = turkish_lower(arama)
            where.append("("
                         "LOWER(translate(ad_soyad, 'İIıŞşĞğÜüÖöÇç', 'iiissgguuoocc')) LIKE %s "
                         "OR LOWER(translate(COALESCE(firma_adi,''), 'İIıŞşĞğÜüÖöÇç', 'iiissgguuoocc')) LIKE %s "
                         "OR telefon ILIKE %s)")
            q = f"%{norm}%"
            params.extend([q, q, f"%{arama}%"])
        if durum:
            where.append("LOWER(COALESCE(lead_durumu,'')) = %s")
            params.append(durum.lower())
        sql = "SELECT * FROM crm_leads"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY COALESCE(takip_tarihi::timestamp, son_gorusme::timestamp, ilk_gorusme::timestamp, NOW()) DESC, id DESC"
        rows = fetch_all(sql, tuple(params))
        return jsonify(rows or [])

    data = request.get_json() or {}
    pid = data.get('id')
    ad_soyad = (data.get('ad_soyad') or data.get('ad') or '').strip()
    if not ad_soyad:
        return jsonify({'ok': False, 'mesaj': 'Ad Soyad zorunlu.'}), 400
    firma_adi = (data.get('firma_adi') or '').strip()
    telefon = (data.get('telefon') or '').strip()
    email = (data.get('email') or '').strip()
    sektor = (data.get('sektor') or '').strip()
    hizmet_turu = (data.get('hizmet_turu') or data.get('paket') or '').strip()
    lead_durumu = (data.get('lead_durumu') or data.get('durum') or 'Yeni Lead').strip()
    try:
        lead_skoru = int(data.get('lead_skoru') or 0)
    except Exception:
        lead_skoru = 0
    ilk_gorusme = data.get('ilk_gorusme') or None
    son_gorusme = data.get('son_gorusme') or None
    takip_tarihi = data.get('takip_tarihi') or data.get('hatirlatma_tarihi') or None
    sorumlu_satis = (data.get('sorumlu_satis') or '').strip()
    notlar = (data.get('notlar') or data.get('gorusme_notu') or '').strip()
    # Takip tarihi boşsa, varsayılan: bugün + 2 gün
    if not takip_tarihi:
        takip_tarihi = (date.today() + timedelta(days=2)).isoformat()

    if pid:
        execute(
            """UPDATE crm_leads
                   SET ad_soyad=%s, firma_adi=%s, telefon=%s, email=%s, sektor=%s,
                       hizmet_turu=%s, lead_durumu=%s, lead_skoru=%s,
                       ilk_gorusme=%s, son_gorusme=%s, takip_tarihi=%s,
                       sorumlu_satis=%s, notlar=%s
                 WHERE id=%s""",
            (
                ad_soyad, firma_adi, telefon, email, sektor,
                hizmet_turu, lead_durumu, lead_skoru,
                ilk_gorusme, son_gorusme, takip_tarihi,
                sorumlu_satis, notlar, pid,
            ),
        )
        return jsonify({'ok': True, 'mesaj': 'Potansiyel müşteri güncellendi.', 'id': pid})

    row = execute_returning(
        """INSERT INTO crm_leads (
                ad_soyad, firma_adi, telefon, email, sektor,
                hizmet_turu, lead_durumu, lead_skoru,
                ilk_gorusme, son_gorusme, takip_tarihi,
                sorumlu_satis, notlar
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id""",
        (
            ad_soyad, firma_adi, telefon, email, sektor,
            hizmet_turu, lead_durumu, lead_skoru,
            ilk_gorusme, son_gorusme, takip_tarihi,
            sorumlu_satis, notlar,
        ),
    )
    return jsonify({'ok': True, 'mesaj': 'Potansiyel müşteri eklendi.', 'id': row['id']})


@bp.route('/api/potansiyel/<int:pid>/convert', methods=['POST'])
@giris_gerekli
def api_potansiyel_convert(pid):
    """Potansiyel müşteriyi gerçek müşteriye çevir ve Cari Kart'a yönlendirme linki döndür."""
    pot = fetch_one("SELECT * FROM crm_leads WHERE id = %s", (pid,))
    if not pot:
        return jsonify({'ok': False, 'mesaj': 'Potansiyel müşteri bulunamadı.'}), 404

    ad_soyad = (pot.get('ad_soyad') or '').strip() or 'Yeni Müşteri'
    firma_adi = (pot.get('firma_adi') or '').strip()
    musteri_adi = firma_adi or ad_soyad
    telefon = pot.get('telefon')
    notes_lines = []
    if pot.get('hizmet_turu'):
        notes_lines.append(f"İlgilendiği hizmet: {pot['hizmet_turu']}")
    if pot.get('sektor'):
        notes_lines.append(f"Sektör: {pot['sektor']}")
    if pot.get('notlar'):
        notes_lines.append(f"Lead notları: {pot['notlar']}")
    notes_text = "\n".join(notes_lines) if notes_lines else None

    yeni = execute_returning(
        """INSERT INTO customers (name, phone, notes, created_at)
               VALUES (%s,%s,%s,NOW())
               RETURNING id""",
        (musteri_adi, telefon, notes_text),
    )
    mid = yeni['id']

    # Lead durumunu güncelle (kazanıldı)
    execute(
        "UPDATE crm_leads SET lead_durumu = %s WHERE id = %s",
        ('Kazanıldı', pid),
    )

    url = url_for('cari_kart.index', mid=mid)
    return jsonify({'ok': True, 'mesaj': 'Sözleşme süreci için Cari Kart açıldı.', 'mid': mid, 'cari_kart_url': url})


@bp.route('/api/potansiyel/pending')
@giris_gerekli
def api_potansiyel_pending():
    """Dashboard'da gösterilecek 'Geri Dönüş Bekleyenler' listesi.

    - lead_durumu != Kazanıldı/Kaybedildi
    - takip_tarihi <= bugün
    """
    bugun = date.today()
    rows = fetch_all(
        """SELECT id, ad_soyad, telefon, hizmet_turu, lead_durumu, takip_tarihi
               FROM crm_leads
              WHERE takip_tarihi IS NOT NULL
                AND takip_tarihi <= %s
                AND LOWER(COALESCE(lead_durumu,'')) NOT IN ('kazanıldı','kazanildi','kaybedildi')
              ORDER BY takip_tarihi ASC, id DESC""",
        (bugun,),
    )
    out = []
    for r in rows or []:
        ad = (r.get('ad_soyad') or '').strip()
        ilk = ad.split()[0] if ad else 'Merhaba'
        mesaj = f"{ilk} Bey selamlar, BestOffice'deki kahve davetimiz hala geçerli, kampanya bitmeden bir daha görüşelim mi?"
        tel_raw = (r.get('telefon') or '').strip()
        num = ''.join(ch for ch in tel_raw if ch.isdigit())
        if num.startswith('0'):
            num = '90' + num[1:]
        elif num and not num.startswith('90'):
            num = '90' + num
        whatsapp_url = f"https://wa.me/{num}?text=" + urllib.parse.quote(mesaj) if num else ''
        r['whatsapp_url'] = whatsapp_url
        r['mesaj'] = mesaj
        out.append(r)
    return jsonify(out)


@bp.route('/api/musteriler')
@giris_gerekli
def api_musteriler():
    """Müşteri listesi - AJAX için (ünvan, müşteri adı, yetkili + vergi no, ofis)."""
    arama = (request.args.get('q') or '').strip()
    base = (
        "SELECT id, name, tax_number, phone, email, office_code "
        "FROM customers "
    )
    if not arama:
        rows = fetch_all(base + "ORDER BY name LIMIT 1000")
    else:
        w = customers_arama_sql_3_plus_tax_office()
        rows = fetch_all(
            base + f"WHERE {w} ORDER BY name LIMIT 1000",
            customers_arama_params_5(arama),
        )
    return jsonify(rows or [])


def _musteri_serialize_val(v):
    """Tarih/sayı alanlarını JSON uyumlu string yap."""
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10] if v else ""
    return str(v).strip() if v else ""


def _parse_kapanis_tarihi(s):
    """Formdan gelen kapanış tarihi (YYYY-MM-DD veya GG.AA.YYYY)."""
    if not s:
        return None
    s = str(s).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_musteri_durum_kapanis(data):
    """durum: aktif|pasif; pasif değilse kapanis_tarihi None."""
    dr = (data.get("durum") or "aktif").strip().lower()
    if dr not in ("aktif", "pasif"):
        dr = "aktif"
    kap = _parse_kapanis_tarihi(data.get("kapanis_tarihi")) if dr == "pasif" else None
    return dr, kap


@bp.route("/api/hizmet-turleri", methods=["GET", "POST"])
@giris_gerekli
def api_hizmet_turleri():
    """Hizmet türü listesi (GET) veya yeni tür ekleme (POST)."""
    ensure_hizmet_turleri_table()
    if request.method == "GET":
        rows = fetch_all("SELECT id, ad FROM hizmet_turleri ORDER BY sira NULLS LAST, ad")
        return jsonify({"ok": True, "turler": [{"id": r["id"], "ad": r["ad"]} for r in (rows or [])]})
    data = request.get_json(silent=True) or {}
    ad = (data.get("ad") or "").strip()
    if not ad:
        return jsonify({"ok": False, "mesaj": "Hizmet türü adı boş olamaz."}), 400
    if len(ad) > 200:
        return jsonify({"ok": False, "mesaj": "En fazla 200 karakter girebilirsiniz."}), 400
    mx = fetch_one("SELECT COALESCE(MAX(sira), 0) + 1 AS n FROM hizmet_turleri")
    next_sira = int(mx["n"] or 1) if mx else 1
    ins = execute_returning(
        "INSERT INTO hizmet_turleri (ad, sira) VALUES (%s, %s) ON CONFLICT (ad) DO NOTHING RETURNING id, ad",
        (ad, next_sira),
    )
    if not ins:
        ins = fetch_one("SELECT id, ad FROM hizmet_turleri WHERE ad = %s", (ad,))
    rows = fetch_all("SELECT id, ad FROM hizmet_turleri ORDER BY sira NULLS LAST, ad")
    return jsonify(
        {
            "ok": True,
            "turler": [{"id": r["id"], "ad": r["ad"]} for r in (rows or [])],
            "secilen": {"id": ins.get("id"), "ad": ins.get("ad")} if ins else None,
        }
    )


@bp.route('/api/musteri/<int:mid>')
@giris_gerekli
def api_musteri_detay(mid):
    """Tek müşteri tüm alanları - customers + son musteri_kyc birleşik; forma doldurmak için."""
    row = fetch_one("SELECT * FROM customers WHERE id = %s", (mid,))
    if not row:
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı."}), 404
    kyc = fetch_one(
        "SELECT * FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
        (mid,),
    )
    out = {}
    for k, v in row.items():
        out[k] = _musteri_serialize_val(v)
    if not kyc and out.get("yetkili_tcno"):
        out["yetkili_tc"] = out["yetkili_tcno"]
    # KYC alanlarını forma uyumlu anahtarlarla birleştir (KYC öncelikli)
    if kyc:
        out["musteri_adi"] = _musteri_serialize_val(out.get("musteri_adi")) or _musteri_serialize_val(
            kyc.get("musteri_adi")
        )
        out["name"] = out.get("name") or _musteri_serialize_val(kyc.get("sirket_unvani"))
        out["tax_number"] = out.get("tax_number") or _musteri_serialize_val(kyc.get("vergi_no"))
        out["vergi_dairesi"] = _musteri_serialize_val(kyc.get("vergi_dairesi")) or out.get("vergi_dairesi", "")
        out["mersis_no"] = _musteri_serialize_val(kyc.get("mersis_no"))
        out["mersis"] = out["mersis_no"]
        out["nace_kodu"] = _musteri_serialize_val(kyc.get("nace_kodu"))
        out["nace"] = out["nace_kodu"]
        out["yetkili_kisi"] = _musteri_serialize_val(kyc.get("yetkili_adsoyad"))
        out["yetkili_ad"] = out["yetkili_kisi"]
        out["yetkili_tc"] = _musteri_serialize_val(kyc.get("yetkili_tcno"))
        out["phone"] = out.get("phone") or _musteri_serialize_val(kyc.get("yetkili_tel"))
        out["phone2"] = _musteri_serialize_val(kyc.get("yetkili_tel2"))
        out["email"] = out.get("email") or _musteri_serialize_val(kyc.get("yetkili_email"))
        out["email_sirket"] = _musteri_serialize_val(kyc.get("email"))
        out["address"] = out.get("address") or _musteri_serialize_val(kyc.get("yeni_adres"))
        out["ev_adres"] = out.get("ev_adres") or _musteri_serialize_val(kyc.get("yetkili_ikametgah"))
        out["notes"] = out.get("notes") or _musteri_serialize_val(kyc.get("notlar"))
        out["hizmet_turu"] = _musteri_serialize_val(kyc.get("hizmet_turu"))
        out["guncel_kira_bedeli"] = _musteri_serialize_val(kyc.get("aylik_kira"))
        out["ilk_kira_bedeli"] = out["guncel_kira_bedeli"]
        out["rent_start_date"] = _musteri_serialize_val(kyc.get("sozlesme_tarihi"))
        out["sozlesme_baslangic"] = out["rent_start_date"]
        out["sozlesme_bitis"] = _musteri_serialize_val(kyc.get("sozlesme_bitis"))
        out["kira_artis_tarihi"] = _musteri_serialize_val(kyc.get("kira_artis_tarihi"))
        out["ticaret_sicil"] = _musteri_serialize_val(kyc.get("ticaret_sicil_no"))
        out["kurulus_tarihi"] = _musteri_serialize_val(kyc.get("kurulus_tarihi"))
        out["faaliyet"] = _musteri_serialize_val(kyc.get("faaliyet_konusu"))
        out["onceki_adres"] = _musteri_serialize_val(kyc.get("eski_adres"))
        out["sube_merkez"] = _musteri_serialize_val(kyc.get("sube_merkez"))

    # Kaç ay ödeme yapıldı (tahsilat / aylık kira KDV dahil)
    try:
        aylik_kira = 0.0
        if out.get("guncel_kira_bedeli"):
            aylik_kira = float(str(out["guncel_kira_bedeli"]).replace(",", ".")) or 0.0
        elif out.get("ilk_kira_bedeli"):
            aylik_kira = float(str(out["ilk_kira_bedeli"]).replace(",", ".")) or 0.0
    except Exception:
        aylik_kira = 0.0
    try:
        kdv_oran = float(str(kyc.get("kdv_oran") or "20").replace(",", ".")) if kyc else 20.0
    except Exception:
        kdv_oran = 20.0
    aylik_kdv_dahil = round(aylik_kira * (1 + kdv_oran / 100), 2) if aylik_kira > 0 else 0.0

    odenen_ay_sayisi = 0
    kismi_odeme_var = False
    kismi_ay_eksik_tutar = 0.0  # Kısmi ödenen ayda kalan borç (kutuda gösterilecek)
    if aylik_kdv_dahil > 0:
        tahsilat_row = fetch_one(
            "SELECT COALESCE(SUM(tutar), 0) AS t FROM tahsilatlar WHERE musteri_id = %s OR customer_id = %s",
            (mid, mid),
        )
        try:
            toplam_tahsilat = float(tahsilat_row.get("t") or 0) if tahsilat_row else 0.0
        except Exception:
            toplam_tahsilat = 0.0
        if toplam_tahsilat > 0:
            odenen_ay_sayisi = int(toplam_tahsilat // aylik_kdv_dahil)
            kalan = toplam_tahsilat - (odenen_ay_sayisi * aylik_kdv_dahil)
            if 0 < kalan < aylik_kdv_dahil:
                kismi_odeme_var = True
                kismi_ay_eksik_tutar = round(aylik_kdv_dahil - kalan, 2)  # O aydan ne kadar eksik kaldı

    out["odenen_ay_sayisi"] = odenen_ay_sayisi
    out["odenen_tam_ay_sayisi"] = odenen_ay_sayisi
    out["kismi_odeme_var"] = kismi_odeme_var
    out["kismi_ay_eksik_tutar"] = kismi_ay_eksik_tutar

    return jsonify({"ok": True, "musteri": out})


@bp.route('/kaydet', methods=['POST'])
@giris_gerekli
def kaydet():
    """Yeni müşteri kaydı veya güncelleme"""
    try:
        data = request.get_json()
        
        musteri_id = data.get('id')
        dr, kap = _normalize_musteri_durum_kapanis(data)

        if musteri_id:
            # Güncelleme
            execute("""
                UPDATE customers SET 
                    name = %s,
                    musteri_adi = %s,
                    tax_number = %s,
                    phone = %s,
                    email = %s,
                    address = %s,
                    ev_adres = %s,
                    notes = %s,
                    durum = %s,
                    kapanis_tarihi = %s
                WHERE id = %s
            """, (
                data.get('name'),
                (data.get('musteri_adi') or '').strip() or None,
                data.get('tax_number'),
                data.get('phone'),
                data.get('email'),
                data.get('address'),
                data.get('ev_adres'),
                data.get('notes'),
                dr,
                kap,
                musteri_id
            ))
            
            return jsonify({'ok': True, 'mesaj': '✅ Müşteri güncellendi'})
        else:
            # Yeni kayıt
            result = execute_returning("""
                INSERT INTO customers (
                    name, musteri_adi, tax_number, phone, email, address,
                    ev_adres, notes, durum, kapanis_tarihi, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING id
            """, (
                data.get('name'),
                (data.get('musteri_adi') or '').strip() or None,
                data.get('tax_number'),
                data.get('phone'),
                data.get('email'),
                data.get('address'),
                data.get('ev_adres'),
                data.get('notes'),
                dr,
                kap,
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
        
        data = request.get_json() or {}

        satirlar = data.get('satirlar') or []
        baslangic_kira = float(data.get('net_kira') or data.get('baslangic_kira') or 0)
        baslangic_tarih = data.get('baslangic_tarih') or ''
        yil_sayisi = int(data.get('yil_sayisi') or (len(satirlar) or 0) or 0)
        musteri_ismi = (data.get('musteri_ismi') or '').strip()

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
        ws['A3'] = 'Müşteri İsmi:'
        ws['B3'] = musteri_ismi or '-'
        ws['A4'] = 'Başlangıç Kira:'
        ws['B4'] = baslangic_kira
        ws['A5'] = 'Başlangıç Tarihi:'
        ws['B5'] = baslangic_tarih
        ws['A6'] = 'Yıl Sayısı:'
        ws['B6'] = yil_sayisi

        # Tablo başlıkları (ekrandaki sırayla)
        headers = ['Yıl', 'Aylık Kira', 'KDV Dahil', 'Artış %', 'Yıllık Toplam', 'Artış Tutar']
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=8, column=col)
            cell.value = header
            cell.font = Font(bold=True)
            cell.fill = PatternFill(start_color='0097A7', end_color='0097A7', fill_type='solid')
            cell.alignment = Alignment(horizontal='center')

        toplam_gelir = 0.0

        if satirlar:
            # Frontend'de hesaplanan tabloyu birebir Excel'e yaz
            for i, s in enumerate(satirlar, start=1):
                try:
                    yil = int(s.get('yil'))
                except Exception:
                    yil = None
                try:
                    aylik = float(str(s.get('aylik_kira') or '0').replace('.', '').replace(',', '.'))
                except Exception:
                    aylik = 0.0
                try:
                    yillik = float(str(s.get('yillik_toplam') or '0').replace('.', '').replace(',', '.'))
                except Exception:
                    yillik = aylik * 12
                # KDV dahil aylık kira (ekranda gösterilen sütun)
                kdv_dahil = aylik * 1.20
                artis_yuzde_raw = (s.get('artis_yuzde') or '').strip()
                if artis_yuzde_raw.endswith('%'):
                    artis_yuzde_raw = artis_yuzde_raw[:-1]
                try:
                    artis_yuzde = float(artis_yuzde_raw.replace(',', '.')) / 100.0
                except Exception:
                    artis_yuzde = 0.0
                try:
                    artis_tutar = float(str(s.get('artis_tutar') or '0').replace('.', '').replace(',', '.'))
                except Exception:
                    artis_tutar = 0.0

                toplam_gelir += yillik

                row = 8 + i
                ws.cell(row=row, column=1).value = yil
                ws.cell(row=row, column=2).value = aylik
                ws.cell(row=row, column=2).number_format = '#,##0.00'
                ws.cell(row=row, column=3).value = kdv_dahil
                ws.cell(row=row, column=3).number_format = '#,##0.00'
                ws.cell(row=row, column=4).value = artis_yuzde
                ws.cell(row=row, column=4).number_format = '0.00%'
                ws.cell(row=row, column=5).value = yillik
                ws.cell(row=row, column=5).number_format = '#,##0.00'
                ws.cell(row=row, column=6).value = artis_tutar
                ws.cell(row=row, column=6).number_format = '#,##0.00'
        else:
            # Eski davranış: sabit TÜFE oranı ile hesapla (geriye dönük uyumluluk için)
            tufe_oran = float(data.get('tufe_oran') or 0) / 100.0
            yil = int((baslangic_tarih or '2000-01-01').split('-')[0])
            mevcut_kira = baslangic_kira
            for i in range(1, yil_sayisi + 1):
                yillik_toplam = mevcut_kira * 12
                toplam_gelir += yillik_toplam
                artis_oran = 0 if i == 1 else tufe_oran
                artis_tutar = 0 if i == 1 else mevcut_kira - (mevcut_kira / (1 + tufe_oran or 1))
                kdv_dahil = mevcut_kira * 1.20
                row = 8 + i
                ws.cell(row=row, column=1).value = yil + i - 1
                ws.cell(row=row, column=2).value = mevcut_kira
                ws.cell(row=row, column=2).number_format = '#,##0.00'
                ws.cell(row=row, column=3).value = kdv_dahil
                ws.cell(row=row, column=3).number_format = '#,##0.00'
                ws.cell(row=row, column=4).value = artis_oran
                ws.cell(row=row, column=4).number_format = '0.00%'
                ws.cell(row=row, column=5).value = yillik_toplam
                ws.cell(row=row, column=5).number_format = '#,##0.00'
                ws.cell(row=row, column=6).value = artis_tutar
                ws.cell(row=row, column=6).number_format = '#,##0.00'
                if i < yil_sayisi:
                    mevcut_kira = mevcut_kira * (1 + tufe_oran)

        # Toplam
        satir_sayisi = len(satirlar) or yil_sayisi
        son_satir = 8 + satir_sayisi + 2
        ws.cell(row=son_satir, column=1).value = f'TOPLAM ({satir_sayisi} Yıl):'
        ws.cell(row=son_satir, column=1).font = Font(bold=True)
        ws.cell(row=son_satir, column=5).value = toplam_gelir
        ws.cell(row=son_satir, column=5).number_format = '#,##0.00'
        ws.cell(row=son_satir, column=5).font = Font(bold=True)
        ws.cell(row=son_satir, column=5).fill = PatternFill(start_color='4CAF50', end_color='4CAF50', fill_type='solid')

        # Sütun genişlikleri
        ws.column_dimensions['A'].width = 15
        ws.column_dimensions['B'].width = 18
        ws.column_dimensions['C'].width = 18
        ws.column_dimensions['D'].width = 15
        ws.column_dimensions['E'].width = 18
        ws.column_dimensions['F'].width = 18
        
        # Dosya kaydet (bellekten gönder)
        filename = f"Kira_Senaryo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return send_file(
            buf,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        
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


def _parse_date_str(s):
    """Basit tarih parse: YYYY-MM-DD veya DD.MM.YYYY / DD/MM/YYYY -> date."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except Exception:
            continue
    return None


def _add_months(d: date, months: int) -> date:
    """Ay ekle (takvim ayı bazlı, yıl devretmeli)."""
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    # Gün sapmasını engelle: aynı gün yoksa ayın son günü
    day = min(d.day, [31,
                      29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return date(y, m, day)


def _generate_installments(contract_id: int, musteri_id: int, baslangic: date, bitis: date | None,
                            sure_ay: int | None, aylik_kira: float, odeme_gunu: int | None):
    """Verilen sözleşme için taksit planını (contract_installments) yeniden üret."""
    if not baslangic or not aylik_kira:
        return
    # Süre yoksa, başlangıç-bitişten ay farkını hesapla
    if not sure_ay and bitis:
        sure_ay = max(1, (bitis.year - baslangic.year) * 12 + (bitis.month - baslangic.month) + 1)
    if not sure_ay:
        sure_ay = 12
    # Eski planı sil
    execute("DELETE FROM contract_installments WHERE contract_id=%s", (contract_id,))
    for i in range(sure_ay):
        vade = _add_months(baslangic, i)
        if odeme_gunu:
            try:
                # Aynı ay, belirtilen gün
                vade = vade.replace(day=min(odeme_gunu, 28 if vade.month == 2 else 30 if vade.day > 30 else odeme_gunu))
            except Exception:
                pass
        execute(
            """
            INSERT INTO contract_installments
                (contract_id, musteri_id, taksit_no, vade_tarihi, tutar, odeme_durumu, odenen_tutar, kalan_tutar)
            VALUES (%s,%s,%s,%s,%s,'planlandi',0,%s)
            """,
            (contract_id, musteri_id, i + 1, vade, aylik_kira, aylik_kira),
        )


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

    soz_fmt, gec_fmt = _soz_ve_guncel_tarih(sozlesme_tarihi, gecerlilik_tarihi)

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


def _soz_ve_guncel_tarih(sozlesme_tarihi, gecerlilik_tarihi):
    """Sözleşme başlangıç tarihi (ilk tarih) ve bugünkü yıla göre güncel artış tarihini döndürür.

    - İlk tarih: sözleşme tarihi (orijinal yıl)
    - Güncel tarih: sözleşme tarihinin gün/ayı + bugünün yılı
    """
    sozlesme_date = _parse_date_str(sozlesme_tarihi)
    if sozlesme_date:
        soz_fmt = sozlesme_date.strftime("%d.%m.%Y")
        today = date.today()
        try:
            guncel = date(today.year, sozlesme_date.month, sozlesme_date.day)
        except ValueError:
            guncel = date(today.year, sozlesme_date.month, min(sozlesme_date.day, 28))
        gec_fmt = guncel.strftime("%d.%m.%Y")
    else:
        soz_fmt = _tarih_fmt(sozlesme_tarihi)
        gec_fmt = _tarih_fmt(gecerlilik_tarihi) or date.today().strftime("%d.%m.%Y")
    return soz_fmt, gec_fmt


def _kira_bildirgesi_metinleri(sozlesme_tarihi, gecerlilik_tarihi, kira_net, kdv_oran, hizmet_turu="sanal_ofis"):
    """Kira bildirgesi paragraf metinlerini döndürür (HTML şablonu için)."""
    soz_fmt, gec_fmt = _soz_ve_guncel_tarih(sozlesme_tarihi, gecerlilik_tarihi)

    kira_net = float(kira_net or 0)
    kdv_oran = float(kdv_oran or 20)
    kdv_dahil = round(kira_net * (1 + kdv_oran / 100), 2)
    yillik = round(kdv_dahil * 12, 2)

    # HTML içinde tarih ve tutarların satır ortasından bölünmesini engellemek için nowrap span'leri kullan
    soz_html = f'<span class="nowrap">{soz_fmt}</span>' if soz_fmt else ''
    gec_html = f'<span class="nowrap">{gec_fmt}</span>' if gec_fmt else ''
    kira_net_html = f'<span class="nowrap">{kira_net:,.2f} TL</span>'
    kdv_dahil_html = f'<span class="nowrap">{kdv_dahil:,.2f} TL</span>'
    yillik_html = f'<span class="nowrap">{yillik:,.2f} TL</span>'

    # Yeni metin:
    # Konu: Hizmet Bedeli Güncellemesi Hakkında Bilgilendirme
    par1 = (
        "BestOffice bünyesinde devam eden iş birliğimiz ve bize duyduğunuz güven için teşekkür ederiz.<br><br>"
        f"{soz_html} başlangıç tarihli \"Ofis Kullanım ve Hizmet Sözleşmeniz\" uyarınca, hizmet bedeliniz güncellenmiştir. "
        "Mevcut ekonomik veriler ve yasal TÜFE oranları dikkate alınarak yapılan düzenleme neticesinde; "
        f"{gec_html} itibarıyla geçerli olacak yeni dönem hizmet bedeli bilgilerinizi aşağıda bulabilirsiniz."
    )

    par2 = (
        f"Aylık Hizmet Bedeli (KDV Hariç): {kira_net_html}<br><br>"
        f"Aylık Toplam (KDV Dahil %{int(kdv_oran)}): {kdv_dahil_html}<br><br>"
        f"Yıllık Toplam (KDV Dahil): {yillik_html}"
    )

    par3 = (
        "Yeni döneme ait ödemelerinizi mevcut sözleşme şartlarında belirtilen hesap numaralarımıza yapmanızı rica ederiz. "
        "Başarılarınızın devamını diler, her türlü sorunuz için bizimle iletişime geçmekten çekinmemenizi önemle rica ederiz."
    )
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

def _odeme_turu_harf(odeme_turu):
    """Tahsilat açıklaması için harf: EFT/Havale/Banka=B, Çek=C, Kredi Kartı=K, Nakit=N."""
    if not odeme_turu:
        return "N"
    o = str(odeme_turu).strip().lower()
    if o in ("havale", "eft", "banka"):
        return "B"
    if o == "cek":
        return "C"
    if o in ("kredi_karti", "kredi kartı"):
        return "K"
    return "N"


def _cari_hareketler(musteri_id, banka_tahsilat_only=False):
    """Fatura (borç) ve tahsilat (alacak) satırlarını tarih sırasına göre birleştirip bakiye hesaplar.
    banka_tahsilat_only=True ise sadece havale/eft/banka ödeme türlü tahsilatlar alınır."""
    faturalar = fetch_all(
        """SELECT id, fatura_no AS belge_no, fatura_tarihi AS tarih, COALESCE(toplam, tutar, 0) AS tutar, 'Fatura' AS tur, vade_tarihi
           FROM faturalar WHERE musteri_id = %s ORDER BY fatura_tarihi, id""",
        (musteri_id,)
    )
    if banka_tahsilat_only:
        # Nakit hariç tüm tahsilatlar: havale, eft, banka, kredi kartı, çek. Sadece nakit gösterilmez.
        tahsilatlar = fetch_all(
            """SELECT id, COALESCE(makbuz_no, 'Makbuz-' || id) AS belge_no, tahsilat_tarihi AS tarih, tutar, odeme_turu, 'Tahsilat' AS tur
               FROM tahsilatlar
               WHERE (musteri_id = %s OR customer_id = %s)
                 AND LOWER(TRIM(COALESCE(odeme_turu, 'nakit'))) IN ('havale', 'eft', 'banka', 'kredi_karti', 'cek')
               ORDER BY tahsilat_tarihi, id""",
            (musteri_id, musteri_id)
        )
    else:
        tahsilatlar = fetch_all(
            """SELECT id, COALESCE(makbuz_no, 'Makbuz-' || id) AS belge_no, tahsilat_tarihi AS tarih, tutar, odeme_turu, 'Tahsilat' AS tur
               FROM tahsilatlar WHERE (musteri_id = %s OR customer_id = %s) ORDER BY tahsilat_tarihi, id""",
            (musteri_id, musteri_id)
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
            "tur": "Tahsilat", "borc": 0, "alacak": float(r.get("tutar") or 0), "vade_tarihi": None, "odeme_turu": r.get("odeme_turu")
        })
    rows.sort(key=lambda x: (x["tarih"], x["tur"] == "Fatura" and 0 or 1))
    bakiye = 0
    for r in rows:
        bakiye = bakiye + r["borc"] - r["alacak"]
        r["bakiye"] = round(bakiye, 2)
    return rows


# Ay adları (ekstre açıklama için)
_AY_ADLARI = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
              "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


def _cari_ekstre_hareketler(musteri_id, baslangic, bitis, aylik_kira):
    """Cari ekstre: aylık kira borç + tahsilat alacak + devir bakiyesi."""
    try:
        bas = baslangic if isinstance(baslangic, date) else datetime.strptime(str(baslangic)[:10], "%Y-%m-%d").date()
        bit = bitis if isinstance(bitis, date) else datetime.strptime(str(bitis)[:10], "%Y-%m-%d").date()
    except Exception:
        return []
    aylik = float(aylik_kira or 0)
    rows = []

    # 1) Sözleşme başlangıcından ekstre başlangıcına kadar olan kira taksitlerini DEVİR olarak hesapla
    soz = fetch_one(
        "SELECT sozlesme_tarihi FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
        (musteri_id,),
    )
    dev_bas = bas
    if soz and soz.get("sozlesme_tarihi"):
        try:
            soz_raw = soz["sozlesme_tarihi"]
            soz_tarih = soz_raw if isinstance(soz_raw, date) else datetime.strptime(str(soz_raw)[:10], "%Y-%m-%d").date()
        except Exception:
            soz_tarih = None
        if soz_tarih and soz_tarih < bas:
            dev_bas = soz_tarih
    devreden = 0.0
    if aylik > 0 and dev_bas < bas:
        y, m = dev_bas.year, dev_bas.month
        while (y, m) < (bas.year, bas.month):
            devreden += aylik
            m += 1
            if m > 12:
                m, y = 1, y + 1
        if devreden:
            rows.append({
                "tarih": bas.isoformat(),
                "aciklama": "Devreden Bakiye",
                "belge_no": "DEVIR",
                "tur": "Devir",
                "borc": round(devreden, 2),
                "alacak": 0,
                "bakiye": None,
            })

    # 2) Ekstre aralığındaki aylık kira satırları (her ayın 1'i)
    y, m = bas.year, bas.month
    bit_y, bit_m = bit.year, bit.month
    while (y, m) <= (bit_y, bit_m):
        ilk_gun = date(y, m, 1)
        if bas <= ilk_gun <= bit:
            aciklama = f"{_AY_ADLARI[m - 1]} {y} Kira"
            rows.append({
                "tarih": ilk_gun.isoformat(),
                "aciklama": aciklama,
                "belge_no": aciklama,
                "tur": "Kira",
                "borc": round(aylik, 2),
                "alacak": 0,
                "bakiye": None,
            })
        m += 1
        if m > 12:
            m, y = 1, y + 1

    # 3) Tahsilatlar (alacak) aynı aralıkta; açıklamada ödeme türü harfi (B/C/K/N)
    tahsilatlar = fetch_all(
        """SELECT id, COALESCE(makbuz_no, 'Makbuz-' || id) AS belge_no, tahsilat_tarihi AS tarih, tutar, odeme_turu
           FROM tahsilatlar
           WHERE (musteri_id = %s OR customer_id = %s)
             AND (tahsilat_tarihi::date) >= %s AND (tahsilat_tarihi::date) <= %s
           ORDER BY tahsilat_tarihi, id""",
        (musteri_id, musteri_id, bas, bit),
    )
    for r in (tahsilatlar or []):
        tarih = str(r.get("tarih") or "")[:10]
        harf = _odeme_turu_harf(r.get("odeme_turu"))
        rows.append({
            "tarih": tarih,
            "aciklama": "Tahsilat " + harf,
            "belge_no": r.get("belge_no") or "",
            "tur": "Tahsilat",
            "borc": 0,
            "alacak": round(float(r.get("tutar") or 0), 2),
            "bakiye": None,
        })

    rows.sort(key=lambda x: (x["tarih"], 0 if x["tur"] in ("Devir", "Kira") else 1))
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
    # Sözleşme / taksit özetleri
    contracts = fetch_all(
        """SELECT id, cari_kodu, sozlesme_no, baslangic_tarihi, bitis_tarihi, sure_ay, aylik_kira,
                  toplam_tutar, para_birimi, odeme_gunu, depozito, gecikme_faizi_orani,
                  yillik_artis_orani, muacceliyet_var, durum
           FROM contracts
           WHERE musteri_id = %s
           ORDER BY created_at DESC""",
        (mid,),
    )
    plan_rows = fetch_all(
        """SELECT id, contract_id, musteri_id, taksit_no, vade_tarihi, tutar,
                  odeme_durumu, odenen_tutar, kalan_tutar
           FROM contract_installments
           WHERE musteri_id = %s
           ORDER BY vade_tarihi, taksit_no""",
        (mid,),
    )
    soz_ozet = fetch_one(
        """
        SELECT
          COALESCE(SUM(tutar),0)                         AS planlanan,
          COALESCE(SUM(CASE WHEN vade_tarihi <= %s THEN tutar END),0) AS tahakkuk,
          COALESCE(SUM(odenen_tutar),0)                  AS odenen,
          COALESCE(SUM(CASE WHEN odeme_durumu IN ('gecikmis','icrada') THEN kalan_tutar ELSE 0 END),0) AS geciken,
          COALESCE(SUM(CASE WHEN vade_tarihi > %s THEN kalan_tutar ELSE 0 END),0) AS gelecek
        FROM contract_installments
        WHERE musteri_id = %s
        """,
        (bugun, bugun, mid),
    ) or {}
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
        "contracts": contracts,
        "installments": plan_rows,
        "contracts_ozet": {
            "planlanan": float(soz_ozet.get("planlanan") or 0),
            "tahakkuk": float(soz_ozet.get("tahakkuk") or 0),
            "odenen": float(soz_ozet.get("odenen") or 0),
            "geciken": float(soz_ozet.get("geciken") or 0),
            "gelecek": float(soz_ozet.get("gelecek") or 0),
        },
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


@bp.route('/api/cari-ekstre')
@giris_gerekli
def api_cari_ekstre():
    """
    Sözleşme sayfası cari ekstre: Tarih aralığında aylık kira borç + tahsilat alacak.
    Query: musteri_id, baslangic (YYYY-MM-DD, default yıl başı), bitis (default bu ay sonu), aylik_kira.
    """
    musteri_id = request.args.get("musteri_id", type=int)
    if not musteri_id:
        return jsonify({"ok": False, "mesaj": "musteri_id gerekli."}), 400
    cust = fetch_one("SELECT id, name FROM customers WHERE id = %s", (musteri_id,))
    if not cust:
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı."}), 404
    bugun = date.today()
    yil_basi = date(bugun.year, 1, 1)
    # Bu ayın son günü
    sonraki_ay = bugun.replace(day=28) + timedelta(days=4)
    bu_ay_sonu = sonraki_ay.replace(day=1) - timedelta(days=1)
    baslangic = request.args.get("baslangic")
    bitis = request.args.get("bitis")
    try:
        bas = datetime.strptime(baslangic[:10], "%Y-%m-%d").date() if baslangic else yil_basi
        bit = datetime.strptime(bitis[:10], "%Y-%m-%d").date() if bitis else bu_ay_sonu
    except Exception:
        bas, bit = yil_basi, bu_ay_sonu
    if bas > bit:
        bas, bit = bit, bas
    aylik_kira = request.args.get("aylik_kira", type=float) or 0
    kdv_oran = request.args.get("kdv_oran", type=float) or 20
    # Borçlar KDV dahil (örn. 1000 + %20 = 1200)
    aylik_kira_kdv_dahil = round(aylik_kira * (1 + kdv_oran / 100), 2) if aylik_kira else 0
    hareketler = _cari_ekstre_hareketler(musteri_id, bas, bit, aylik_kira_kdv_dahil)
    toplam_borc = sum(h.get("borc") or 0 for h in hareketler)
    toplam_alacak = sum(h.get("alacak") or 0 for h in hareketler)
    bakiye = round(toplam_borc - toplam_alacak, 2)
    return jsonify({
        "ok": True,
        "musteri_adi": cust.get("name") or "",
        "hareketler": hareketler,
        "toplam_borc": round(toplam_borc, 2),
        "toplam_alacak": round(toplam_alacak, 2),
        "bakiye": bakiye,
    })


def _next_fatura_no_aylik(prefix="INV"):
    """Yıla göre artan fatura numarası (faturalar tablosu ile uyumlu)."""
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


def _next_makbuz_no_aylik():
    """Yıla göre bir sonraki makbuz numarası (tahsilatlar ile uyumlu)."""
    yil = datetime.now().year
    row = fetch_one(
        "SELECT makbuz_no FROM tahsilatlar WHERE makbuz_no LIKE %s ORDER BY id DESC LIMIT 1",
        (f"{yil}-%",),
    )
    if not row or not row.get("makbuz_no"):
        return f"{yil}-0001"
    try:
        num = int(row["makbuz_no"].split("-")[-1])
        return f"{yil}-{num + 1:04d}"
    except (ValueError, IndexError):
        return f"{yil}-0001"


@bp.route('/api/aylik-tutarlardan-borclandir', methods=['POST'])
@giris_gerekli
def api_aylik_tutarlardan_borclandir():
    """
    Aylık Tutarlar gridinden seçilen aylar için ayrı fatura (borç) kaydı oluşturur.
    Tutarlar KDV dahil kabul edilir; net/kdv satırları faturalar.tutar / kdv_tutar olarak bölünür.
    Cari Ekstre B ve genel cari kartta faturalar görünür.
    """
    ensure_faturalar_amount_columns()
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}
    musteri_id = data.get("musteri_id")
    try:
        musteri_id = int(musteri_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "musteri_id gerekli."}), 400
    cust = fetch_one("SELECT id, name FROM customers WHERE id = %s", (musteri_id,))
    if not cust:
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı."}), 404
    musteri_adi = (cust.get("name") or "").strip() or "—"
    satirlar = data.get("satirlar")
    if not isinstance(satirlar, list) or not satirlar:
        return jsonify({"ok": False, "mesaj": "En az bir ay satırı gerekli."}), 400

    KDV_ORAN = 20.0
    olusturulan = []
    atlanan = []

    for raw in satirlar:
        if not isinstance(raw, dict):
            continue
        try:
            yil = int(raw.get("yil"))
            ay = int(raw.get("ay"))
        except (TypeError, ValueError):
            atlanan.append({"neden": "geçersiz_yil_ay", "satir": raw})
            continue
        if ay < 1 or ay > 12 or yil < 1990 or yil > 2100:
            atlanan.append({"neden": "tarih_aralik", "satir": raw})
            continue
        try:
            tutar = float(raw.get("tutar_kdv_dahil"))
        except (TypeError, ValueError):
            atlanan.append({"neden": "geçersiz_tutar", "satir": raw})
            continue
        if tutar <= 0:
            atlanan.append({"neden": "tutar_sifir", "satir": raw})
            continue

        ay_bir = date(yil, ay, 1)
        ay_anahtar = ay_bir.isoformat()
        marker = f"|AYLIK_TUTAR|{ay_anahtar}|"
        var = fetch_one(
            "SELECT id, fatura_no FROM faturalar WHERE musteri_id = %s AND COALESCE(notlar,'') LIKE %s LIMIT 1",
            (musteri_id, f"%{marker}%"),
        )
        if var:
            atlanan.append({"neden": "zaten_faturali", "yil": yil, "ay": ay, "fatura_no": var.get("fatura_no")})
            continue

        # Ay sonu vade (yaklaşık)
        if ay == 12:
            vade = date(yil, 12, 31)
        else:
            vade = date(yil, ay + 1, 1) - timedelta(days=1)

        toplam = round(tutar, 2)
        net = round(toplam / (1 + KDV_ORAN / 100.0), 2)
        kdv_tutar = round(toplam - net, 2)
        net = round(toplam - kdv_tutar, 2)

        ay_adi = _AY_ADLARI[ay - 1]
        notlar = f"{ay_adi} {yil} kira bedeli (KDV dahil, Aylık Tutarlar){marker}"

        fatura_no = _next_fatura_no_aylik()
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
                net,
                kdv_tutar,
                toplam,
                "odenmedi",
                ay_bir,
                vade,
                notlar,
            ),
        )
        row = fetch_one("SELECT id FROM faturalar WHERE fatura_no = %s ORDER BY id DESC LIMIT 1", (fatura_no,))
        fid = row.get("id") if row else None
        olusturulan.append({"id": fid, "fatura_no": fatura_no, "yil": yil, "ay": ay, "toplam": toplam})

    return jsonify({
        "ok": True,
        "olusturulan": olusturulan,
        "atlanan": atlanan,
        "mesaj": f"{len(olusturulan)} fatura oluşturuldu, {len(atlanan)} satır atlandı.",
    })


@bp.route('/api/aylik-tutarlardan-tahsil-et', methods=['POST'])
@giris_gerekli
def api_aylik_tutarlardan_tahsil_et():
    """
    Aylık Tutarlar gridinden seçilen aylar için ayrı tahsilat (alacak) kaydı.
    Tutarlar griddeki gibi KDV dahil; cari kart ve ekstrelerde tahsilat olarak görünür.
    Aynı ay için tekrar kayıt engellenir (açıklama işaretçisi).
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}
    musteri_id = data.get("musteri_id")
    try:
        musteri_id = int(musteri_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "musteri_id gerekli."}), 400
    cust = fetch_one("SELECT id, name FROM customers WHERE id = %s", (musteri_id,))
    if not cust:
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı."}), 404

    satirlar = data.get("satirlar")
    if not isinstance(satirlar, list) or not satirlar:
        return jsonify({"ok": False, "mesaj": "En az bir ay satırı gerekli."}), 400

    tahsilat_tarihi = date.today()
    raw_tarih = (data.get("tahsilat_tarihi") or "").strip()
    if raw_tarih:
        try:
            tahsilat_tarihi = datetime.strptime(raw_tarih[:10], "%Y-%m-%d").date()
        except Exception:
            pass

    odeme = (data.get("odeme_turu") or "havale").strip().lower()
    if odeme not in ("nakit", "havale", "eft", "banka", "kredi_karti", "cek"):
        odeme = "havale"

    olusturulan = []
    atlanan = []

    for raw in satirlar:
        if not isinstance(raw, dict):
            continue
        try:
            yil = int(raw.get("yil"))
            ay = int(raw.get("ay"))
        except (TypeError, ValueError):
            atlanan.append({"neden": "geçersiz_yil_ay", "satir": raw})
            continue
        if ay < 1 or ay > 12 or yil < 1990 or yil > 2100:
            atlanan.append({"neden": "tarih_aralik", "satir": raw})
            continue
        try:
            tutar = float(raw.get("tutar_kdv_dahil"))
        except (TypeError, ValueError):
            atlanan.append({"neden": "geçersiz_tutar", "satir": raw})
            continue
        if tutar <= 0:
            atlanan.append({"neden": "tutar_sifir", "satir": raw})
            continue

        ay_bir = date(yil, ay, 1)
        ay_anahtar = ay_bir.isoformat()
        marker = f"|AYLIK_TAH|{ay_anahtar}|"
        var = fetch_one(
            "SELECT id, makbuz_no FROM tahsilatlar WHERE (musteri_id = %s OR customer_id = %s) AND COALESCE(aciklama,'') LIKE %s LIMIT 1",
            (musteri_id, musteri_id, f"%{marker}%"),
        )
        if var:
            atlanan.append({"neden": "zaten_tahsil", "yil": yil, "ay": ay, "makbuz_no": var.get("makbuz_no")})
            continue

        tutar = round(tutar, 2)
        ay_adi = _AY_ADLARI[ay - 1]
        aciklama = f"{ay_adi} {yil} kira tahsilatı (KDV dahil, Aylık Tutarlar){marker}"
        makbuz_no = _next_makbuz_no_aylik()
        row = execute_returning(
            """
            INSERT INTO tahsilatlar (musteri_id, customer_id, tutar, odeme_turu, aciklama, tahsilat_tarihi, makbuz_no)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (musteri_id, musteri_id, tutar, odeme, aciklama, tahsilat_tarihi, makbuz_no),
        )
        tid = row.get("id") if row else None
        olusturulan.append({"id": tid, "makbuz_no": makbuz_no, "yil": yil, "ay": ay, "tutar": tutar})

    return jsonify({
        "ok": True,
        "olusturulan": olusturulan,
        "atlanan": atlanan,
        "mesaj": f"{len(olusturulan)} tahsilat kaydı oluşturuldu, {len(atlanan)} satır atlandı.",
    })


@bp.route('/api/cari-ekstre-b')
@giris_gerekli
def api_cari_ekstre_b():
    """
    Cari Ekstre B: Kesilen faturalar (borç) + nakit hariç tahsilatlar (havale, EFT, banka, kredi kartı, çek) (alacak).
    Sadece nakit tahsilatlar bu ekstrede gösterilmez.
    """
    musteri_id = request.args.get("musteri_id", type=int)
    if not musteri_id:
        return jsonify({"ok": False, "mesaj": "musteri_id gerekli."}), 400
    cust = fetch_one("SELECT id, name FROM customers WHERE id = %s", (musteri_id,))
    if not cust:
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı."}), 404
    bugun = date.today()
    yil_basi = date(bugun.year, 1, 1)
    sonraki_ay = bugun.replace(day=28) + timedelta(days=4)
    bu_ay_sonu = sonraki_ay.replace(day=1) - timedelta(days=1)
    baslangic = request.args.get("baslangic")
    bitis = request.args.get("bitis")
    try:
        bas = datetime.strptime(baslangic[:10], "%Y-%m-%d").date() if baslangic else yil_basi
        bit = datetime.strptime(bitis[:10], "%Y-%m-%d").date() if bitis else bu_ay_sonu
    except Exception:
        bas, bit = yil_basi, bu_ay_sonu
    if bas > bit:
        bas, bit = bit, bas
    rows = _cari_hareketler(musteri_id, banka_tahsilat_only=True)
    # Açılış bakiyesi: bas tarihinden önceki hareketlerin net tutarı
    acilis = 0.0
    filtered = []
    for r in rows:
        tarih_str = (r.get("tarih") or "")[:10]
        try:
            t = datetime.strptime(tarih_str, "%Y-%m-%d").date() if tarih_str else None
        except Exception:
            t = None
        borc = round(float(r.get("borc") or 0), 2)
        alacak = round(float(r.get("alacak") or 0), 2)
        if t is None:
            continue
        if t < bas:
            acilis += borc - alacak
            continue
        if t > bit:
            continue
        tur = r.get("tur") or ""
        belge_no = (r.get("belge_no") or "").strip()
        if tur == "Tahsilat":
            harf = _odeme_turu_harf(r.get("odeme_turu"))
            aciklama = "Tahsilat " + harf + (" " + belge_no if belge_no else "")
        else:
            aciklama = tur + (" " + belge_no if belge_no else "")
        filtered.append({
            "tarih": tarih_str,
            "aciklama": aciklama.strip() or tur,
            "belge_no": r.get("belge_no") or "",
            "tur": r.get("tur") or "",
            "borc": borc,
            "alacak": alacak,
        })
    filtered.sort(key=lambda x: (x["tarih"], x["tur"] == "Fatura" and 0 or 1))
    bakiye = acilis
    hareketler = []
    for h in filtered:
        bakiye = round(bakiye + (h["borc"] - h["alacak"]), 2)
        h["bakiye"] = bakiye
        hareketler.append(h)
    toplam_borc = sum(h.get("borc") or 0 for h in hareketler)
    toplam_alacak = sum(h.get("alacak") or 0 for h in hareketler)
    bakiye = round(acilis + toplam_borc - toplam_alacak, 2)
    return jsonify({
        "ok": True,
        "musteri_adi": cust.get("name") or "",
        "hareketler": hareketler,
        "toplam_borc": round(toplam_borc, 2),
        "toplam_alacak": round(toplam_alacak, 2),
        "bakiye": bakiye,
    })


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


# ── Sözleşme / Taksit API ─────────────────────────────────────────────────────


@bp.route('/api/contracts/<int:mid>', methods=['GET', 'POST'])
@giris_gerekli
def api_contracts(mid):
    """Belirli bir müşteri için sözleşme ve taksit özetleri."""
    if request.method == 'GET':
        bugun = date.today()
        contracts = fetch_all(
            """SELECT * FROM contracts WHERE musteri_id=%s ORDER BY created_at DESC""",
            (mid,),
        )
        plan = fetch_all(
            """SELECT * FROM contract_installments
               WHERE musteri_id=%s
               ORDER BY vade_tarihi, taksit_no""",
            (mid,),
        )
        ozet = fetch_one(
            """
            SELECT
              COALESCE(SUM(tutar),0)                         AS planlanan,
              COALESCE(SUM(CASE WHEN vade_tarihi <= %s THEN tutar END),0) AS tahakkuk,
              COALESCE(SUM(odenen_tutar),0)                  AS odenen,
              COALESCE(SUM(CASE WHEN odeme_durumu IN ('gecikmis','icrada') THEN kalan_tutar ELSE 0 END),0) AS geciken,
              COALESCE(SUM(CASE WHEN vade_tarihi > %s THEN kalan_tutar ELSE 0 END),0) AS gelecek
            FROM contract_installments
            WHERE musteri_id = %s
            """,
            (bugun, bugun, mid),
        ) or {}
        return jsonify({
            "ok": True,
            "contracts": contracts or [],
            "installments": plan or [],
            "ozet": {
                "planlanan": float(ozet.get("planlanan") or 0),
                "tahakkuk": float(ozet.get("tahakkuk") or 0),
                "odenen": float(ozet.get("odenen") or 0),
                "geciken": float(ozet.get("geciken") or 0),
                "gelecek": float(ozet.get("gelecek") or 0),
            },
        })

    # POST: yeni sözleşme oluştur / güncelle
    data = request.get_json() or {}
    cid = data.get("id")
    baslangic = _parse_date_str(data.get("baslangic_tarihi"))
    bitis = _parse_date_str(data.get("bitis_tarihi"))
    if not baslangic:
        return jsonify({"ok": False, "mesaj": "Sözleşme başlangıç tarihi zorunlu."}), 400
    try:
        aylik_kira = float(data.get("aylik_kira") or 0)
    except Exception:
        aylik_kira = 0
    if aylik_kira <= 0:
        return jsonify({"ok": False, "mesaj": "Aylık kira tutarı zorunlu."}), 400
    sure_ay = data.get("sure_ay")
    try:
        sure_ay = int(sure_ay) if sure_ay is not None else None
    except Exception:
        sure_ay = None
    try:
        odeme_gunu = int(data.get("odeme_gunu") or 0) or None
    except Exception:
        odeme_gunu = None
    para_birimi = (data.get("para_birimi") or "TRY").strip().upper()
    depozito = data.get("depozito") or 0
    try:
        depozito = float(depozito or 0)
    except Exception:
        depozito = 0
    try:
        gecikme = float(data.get("gecikme_faizi_orani") or 0)
    except Exception:
        gecikme = 0
    try:
        artis = float(data.get("yillik_artis_orani") or 0)
    except Exception:
        artis = 0
    muacceliyet = bool(data.get("muacceliyet_var")) or str(data.get("muacceliyet_var")).lower() in ("1", "true", "evet", "on")
    durum = (data.get("durum") or "aktif").strip().lower()
    sozlesme_no = (data.get("sozlesme_no") or "").strip() or None
    cari_kodu = (data.get("cari_kodu") or "").strip() or None
    toplam_tutar = data.get("toplam_tutar")
    try:
        toplam_tutar = float(toplam_tutar or 0)
    except Exception:
        toplam_tutar = 0
    if not toplam_tutar and sure_ay:
        toplam_tutar = aylik_kira * sure_ay

    if cid:
        execute(
            """
            UPDATE contracts
               SET cari_kodu=%s, sozlesme_no=%s, baslangic_tarihi=%s, bitis_tarihi=%s,
                   sure_ay=%s, aylik_kira=%s, toplam_tutar=%s, para_birimi=%s,
                   odeme_gunu=%s, depozito=%s, gecikme_faizi_orani=%s,
                   yillik_artis_orani=%s, muacceliyet_var=%s, durum=%s,
                   updated_at=NOW()
             WHERE id=%s AND musteri_id=%s
            """,
            (
                cari_kodu, sozlesme_no, baslangic, bitis,
                sure_ay, aylik_kira, toplam_tutar, para_birimi,
                odeme_gunu, depozito, gecikme, artis,
                muacceliyet, durum, cid, mid,
            ),
        )
        contract_id = int(cid)
    else:
        row = execute_returning(
            """
            INSERT INTO contracts
                (musteri_id, cari_kodu, sozlesme_no, baslangic_tarihi, bitis_tarihi,
                 sure_ay, aylik_kira, toplam_tutar, para_birimi,
                 odeme_gunu, depozito, gecikme_faizi_orani,
                 yillik_artis_orani, muacceliyet_var, durum)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                mid, cari_kodu, sozlesme_no, baslangic, bitis,
                sure_ay, aylik_kira, toplam_tutar, para_birimi,
                odeme_gunu, depozito, gecikme, artis,
                muacceliyet, durum,
            ),
        )
        contract_id = row["id"]

    # Taksit planını üret
    _generate_installments(contract_id, mid, baslangic, bitis, sure_ay, aylik_kira, odeme_gunu)

    return jsonify({"ok": True, "id": contract_id, "mesaj": "Sözleşme ve taksit planı kaydedildi."})