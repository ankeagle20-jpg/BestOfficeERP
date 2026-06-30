from flask import Blueprint, jsonify, request
from datetime import date, datetime
from db import fetch_all, fetch_one
from auth import giris_gerekli

bp = Blueprint('whatsapp', __name__, url_prefix='/whatsapp')

SABLONLAR = {
    0: "Değerli iş ortağımız, bugün vadesi dolan faturanızın hatırlatmasıdır. İş birliğiniz için teşekkür ederiz.",
    3: "Sayın {isim}, ödemenizde 3 günlük bir gecikme görünüyor. Gözden kaçmış olabileceğini düşündük.",
    7: "Ödemeniz 1 haftadır gecikmededir. Mutabakat veya teknik bir sorun varsa lütfen bizimle iletişime geçiniz.",
    15: "Sayın Yetkili, gecikme süresi 15 günü bulmuştur. Hizmet sürekliliğinde aksama olmaması adına gün içinde ödeme beklemekteyiz.",
    21: "Ödemeniz 21 gündür yapılmamıştır. Borç bakiyenizin kapatılmaması durumunda sistem erişim kısıtlamaları gündeme gelecektir.",
    30: "Gecikme 1 ayı doldurmuştur. Dosyanızın hukuk birimine aktarılmaması için 24 saat içinde ödeme yapılması gerekmektedir.",
}


def _odeme_gunu_gecikme_hesapla(sozlesme_tarihi, bugun=None):
    """sözleşme tarihindeki güne göre bu ayki ödeme tarihini bulur,
    kaç gün geciktiğini döner (negatifse henüz gecikmemiş -1 döner)"""
    if not sozlesme_tarihi:
        return None
    if not bugun:
        bugun = date.today()
    if isinstance(sozlesme_tarihi, str):
        sozlesme_tarihi = datetime.strptime(sozlesme_tarihi[:10], "%Y-%m-%d").date()
    gun = sozlesme_tarihi.day
    try:
        bu_ay_odeme = date(bugun.year, bugun.month, gun)
    except ValueError:
        import calendar
        son_gun = calendar.monthrange(bugun.year, bugun.month)[1]
        bu_ay_odeme = date(bugun.year, bugun.month, min(gun, son_gun))
    fark = (bugun - bu_ay_odeme).days
    return fark


def _en_yakin_esik(gecikme_gun):
    esikler = sorted(SABLONLAR.keys())
    uygun = None
    for e in esikler:
        if gecikme_gun >= e:
            uygun = e
    return uygun


@bp.route('/api/geciken-liste')
@giris_gerekli
def api_geciken_liste():
    """Sözleşme gününe göre bugün gecikmiş olan müşterileri listeler"""
    bugun = date.today()
    rows = fetch_all("""
        SELECT c.id, c.name, c.musteri_adi, c.phone, c.phone2,
               mk.sozlesme_tarihi,
               COALESCE(c.guncel_kira_bedeli, c.ilk_kira_bedeli, mk.aylik_kira, 0) as aylik_tutar
        FROM customers c
        LEFT JOIN LATERAL (
            SELECT sozlesme_tarihi, aylik_kira
            FROM musteri_kyc
            WHERE musteri_id = c.id
            ORDER BY id DESC LIMIT 1
        ) mk ON TRUE
        WHERE c.durum = 'aktif'
        AND mk.sozlesme_tarihi IS NOT NULL
    """) or []

    sonuc = []
    for r in rows:
        gecikme = _odeme_gunu_gecikme_hesapla(r.get('sozlesme_tarihi'), bugun)
        if gecikme is None or gecikme < 0:
            continue
        esik = _en_yakin_esik(gecikme)
        if esik is None:
            continue
        isim = r.get('name') or r.get('musteri_adi') or ''
        telefon = r.get('phone') or r.get('phone2') or ''
        if not telefon:
            continue
        tutar = float(r.get('aylik_tutar') or 0)
        sablon = SABLONLAR[esik].format(isim=isim, tutar=f"{tutar:,.2f}".replace(',', '.'), gun=gecikme)
        sonuc.append({
            'musteri_id': r.get('id'),
            'isim': isim,
            'telefon': telefon,
            'gecikme_gun': gecikme,
            'esik': esik,
            'tutar': tutar,
            'mesaj': sablon,
        })

    sonuc.sort(key=lambda x: -x['gecikme_gun'])
    return jsonify({'ok': True, 'liste': sonuc, 'tarih': bugun.isoformat()})


@bp.route('/api/gonder', methods=['POST'])
@giris_gerekli
def api_gonder():
    """Onaylanan listeyi WhatsApp servisine (Node.js) iletir"""
    import requests
    data = request.get_json(silent=True) or {}
    liste = data.get('liste') or []
    if not liste:
        return jsonify({'ok': False, 'mesaj': 'Liste boş'}), 400

    wa_liste = []
    for item in liste:
        tel = str(item.get('telefon') or '').strip()
        mesaj = str(item.get('mesaj') or '').strip()
        if tel and mesaj:
            wa_liste.append({'telefon': tel, 'mesaj': mesaj})

    if not wa_liste:
        return jsonify({'ok': False, 'mesaj': 'Geçerli kayıt yok'}), 400

    try:
        r = requests.post(
            'http://127.0.0.1:3001/kuyruk-toplu-ekle',
            json={'liste': wa_liste},
            timeout=10
        )
        result = r.json()
        return jsonify({'ok': True, 'servis_yaniti': result})
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': f'WhatsApp servisine bağlanılamadı: {e}'}), 500


@bp.route('/api/servis-durum')
@giris_gerekli
def api_servis_durum():
    """WhatsApp servisinin bağlantı durumunu kontrol eder"""
    import requests
    try:
        r = requests.get('http://127.0.0.1:3001/durum', timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'bagli': False, 'hata': str(e)})
