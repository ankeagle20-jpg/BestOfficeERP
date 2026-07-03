from flask import Blueprint, jsonify, request
from datetime import date, datetime
from db import fetch_all, fetch_one, execute
from auth import giris_gerekli

bp = Blueprint('whatsapp', __name__, url_prefix='/whatsapp')

_WHATSAPP_GECIKEN_HARIC_TABLE_READY = False


def _ensure_whatsapp_geciken_haric_table():
    global _WHATSAPP_GECIKEN_HARIC_TABLE_READY
    if _WHATSAPP_GECIKEN_HARIC_TABLE_READY:
        return
    try:
        execute(
            """
            CREATE TABLE IF NOT EXISTS whatsapp_geciken_haric (
                musteri_id INTEGER PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
    except Exception:
        pass
    _WHATSAPP_GECIKEN_HARIC_TABLE_READY = True


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
    bugun = date.today()
    from routes.giris_routes import (
        musteri_firma_ozet_grid_ozet_batch,
        _aylik_grid_contract_core,
        _musteri_aylik_grid_customer_kyc_select_sql,
        _firma_ozet_kyc_dict_from_grid_sql_row,
        _tufe_map_by_year_month_cached,
    )
    haric_goster = str(request.args.get('haric_goster') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    _ensure_whatsapp_geciken_haric_table()
    bugun_key = f"{bugun.year}-{bugun.month}"

    # Grid cache'den ödenmemiş geçmiş ayları olan müşterileri çek
    haric_sql = ""
    if not haric_goster:
        haric_sql = """
        AND NOT EXISTS (
            SELECT 1 FROM whatsapp_geciken_haric h WHERE h.musteri_id = c.id
        )
        """

    rows = fetch_all("""
        SELECT c.id, c.name, c.musteri_adi, c.phone, c.phone2,
               COALESCE(c.guncel_kira_bedeli, c.ilk_kira_bedeli, mk.aylik_kira, 0) as aylik_tutar,
               mk.sozlesme_tarihi, mk.hizmet_turu, c.grup2_secimleri
        FROM customers c
        LEFT JOIN LATERAL (
            SELECT sozlesme_tarihi, aylik_kira, hizmet_turu
            FROM musteri_kyc
            WHERE musteri_id = c.id
            ORDER BY id DESC LIMIT 1
        ) mk ON TRUE
        WHERE c.durum = 'aktif'
        """ + haric_sql + """
        AND EXISTS (
            SELECT 1 FROM musteri_aylik_grid_cache gc,
            jsonb_array_elements(gc.payload::jsonb->'aylar') AS elem
            WHERE gc.musteri_id = c.id
            AND (elem->>'tahsil_edildi')::boolean = false
            AND to_date(elem->>'ay_key', 'YYYY-MM')
                <= to_date(%s, 'YYYY-MM')
            AND (elem->>'tutar_kdv_dahil')::float > 0
        )
    """, (bugun_key,)) or []

    haric_ids = set()
    if haric_goster:
        haric_rows = fetch_all("SELECT musteri_id FROM whatsapp_geciken_haric") or []
        haric_ids = {hr['musteri_id'] for hr in haric_rows}

    musteri_ids_all = [r.get('id') for r in rows if r.get('id')]
    ozet_batch = musteri_firma_ozet_grid_ozet_batch(musteri_ids_all, bugun) if musteri_ids_all else {}

    ilk_kira_by_mid = {}
    if musteri_ids_all:
        tufe_map_local = _tufe_map_by_year_month_cached()
        base_sql_local = _musteri_aylik_grid_customer_kyc_select_sql()
        kyc_rows_local = fetch_all(base_sql_local + " WHERE c.id = ANY(%s)", (musteri_ids_all,)) or []
        for kr in kyc_rows_local:
            try:
                mid_k = int(kr.get('id') or 0)
            except (TypeError, ValueError):
                continue
            if mid_k <= 0:
                continue
            kyc_for_grid_k = _firma_ozet_kyc_dict_from_grid_sql_row(kr)
            if not kyc_for_grid_k:
                continue
            try:
                core_k = _aylik_grid_contract_core(kyc_for_grid_k, tufe_map_local)
                if core_k and isinstance(core_k.get('yillik_map'), dict):
                    ik = core_k['yillik_map'].get(core_k.get('start_year'))
                    if ik is not None:
                        ilk_kira_by_mid[mid_k] = round(float(ik), 2)
            except Exception:
                continue

    sonuc = []
    for r in rows:
        # Bu müşterinin en eski ödenmemiş ayını bul
        odenme_rows = fetch_all("""
            SELECT elem->>'ay_key' as ay_key
            FROM musteri_aylik_grid_cache,
            jsonb_array_elements(payload::jsonb->'aylar') AS elem
            WHERE musteri_id = %s
            AND (elem->>'tahsil_edildi')::boolean = false
            AND to_date(elem->>'ay_key', 'YYYY-MM')
                <= to_date(%s, 'YYYY-MM')
            AND (elem->>'tutar_kdv_dahil')::float > 0
            ORDER BY elem->>'ay_key' ASC
            LIMIT 1
        """, (r['id'], bugun_key)) or []

        if not odenme_rows:
            continue

        # En eski ödenmemiş ayın sözleşme gününden gecikme hesapla
        en_eski_ay_key = odenme_rows[0]['ay_key']  # "2026-5" gibi
        yil, ay = map(int, en_eski_ay_key.split('-'))

        sozlesme_tarihi = r.get('sozlesme_tarihi')
        gun = 1
        if sozlesme_tarihi:
            if isinstance(sozlesme_tarihi, str):
                sozlesme_tarihi = datetime.strptime(
                    sozlesme_tarihi[:10], "%Y-%m-%d").date()
            gun = sozlesme_tarihi.day

        import calendar
        son_gun = calendar.monthrange(yil, ay)[1]
        odeme_gunu = min(gun, son_gun)

        try:
            odeme_tarihi = date(yil, ay, odeme_gunu)
        except ValueError:
            continue

        gecikme = (bugun - odeme_tarihi).days
        if gecikme < 0:
            continue

        esik = _en_yakin_esik(gecikme)
        if esik is None:
            continue

        isim = r.get('name') or r.get('musteri_adi') or ''
        telefon = r.get('phone') or r.get('phone2') or ''
        if not telefon:
            continue

        tutar = float(r.get('aylik_tutar') or 0)
        sablon = SABLONLAR[esik].format(
            isim=isim,
            tutar=f"{tutar:,.2f}".replace(',', '.'),
            gun=gecikme
        )
        mid_r = r.get('id')
        ozet_r = ozet_batch.get(mid_r) or {}
        sonuc.append({
            'musteri_id': mid_r,
            'isim': isim,
            'telefon': telefon,
            'hizmet_turu': r.get('hizmet_turu') or '',
            'haric': mid_r in haric_ids,
            'gecikme_gun': gecikme,
            'esik': esik,
            'tutar': tutar,
            'mesaj': sablon,
            'sozlesme_tarihi': (
                sozlesme_tarihi.isoformat()[:10]
                if isinstance(sozlesme_tarihi, date)
                else (str(sozlesme_tarihi)[:10] if sozlesme_tarihi else '')
            ),
            'grup2_secimleri': list(r.get('grup2_secimleri') or []),
            'ilk_kira': ilk_kira_by_mid.get(mid_r, 0),
            'guncel': round(float(ozet_r.get('borc_month') or 0), 2),
            'ay': int(ozet_r.get('geciken_ay') or 0),
            'toplam': round(float(ozet_r.get('toplam_borc') or 0), 2),
        })

    sonuc.sort(key=lambda x: -x['gecikme_gun'])
    grup2_etiket_map = {}
    try:
        etiket_rows = fetch_all(
            "SELECT slug, etiket FROM grup2_etiketleri WHERE COALESCE(aktif, TRUE)"
        ) or []
        grup2_etiket_map = {row['slug']: row['etiket'] for row in etiket_rows}
    except Exception:
        grup2_etiket_map = {}
    return jsonify({
        'ok': True,
        'liste': sonuc,
        'tarih': bugun.isoformat(),
        'grup2_etiket_map': grup2_etiket_map
    })


@bp.route('/api/geciken-haric-ekle', methods=['POST'])
@giris_gerekli
def api_geciken_haric_ekle():
    _ensure_whatsapp_geciken_haric_table()
    data = request.get_json(silent=True) or {}
    ids = data.get('musteri_ids') or []
    eklenen = 0
    for mid in ids:
        try:
            mid_int = int(mid)
        except (TypeError, ValueError):
            continue
        try:
            execute(
                """
                INSERT INTO whatsapp_geciken_haric (musteri_id)
                VALUES (%s)
                ON CONFLICT (musteri_id) DO NOTHING
                """,
                (mid_int,),
            )
            eklenen += 1
        except Exception:
            continue
    return jsonify({'ok': True, 'eklenen': eklenen})


@bp.route('/api/geciken-haric-cikar', methods=['POST'])
@giris_gerekli
def api_geciken_haric_cikar():
    _ensure_whatsapp_geciken_haric_table()
    data = request.get_json(silent=True) or {}
    ids = data.get('musteri_ids') or []
    cikarilan = 0
    for mid in ids:
        try:
            mid_int = int(mid)
        except (TypeError, ValueError):
            continue
        try:
            n = execute(
                "DELETE FROM whatsapp_geciken_haric WHERE musteri_id = %s",
                (mid_int,),
            )
            if n:
                cikarilan += 1
        except Exception:
            continue
    return jsonify({'ok': True, 'cikarilan': cikarilan})


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
