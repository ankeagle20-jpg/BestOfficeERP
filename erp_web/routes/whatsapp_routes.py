from flask import Blueprint, jsonify, request, Response
from datetime import date, datetime
import os
from db import fetch_all, fetch_one, execute, _tenant_schema_for_request
from auth import giris_gerekli

bp = Blueprint('whatsapp', __name__, url_prefix='/whatsapp')

_WHATSAPP_GECIKEN_HARIC_TABLE_READY = False

# Yerel varsayılan; üretimde WA_INTERNAL_TOKEN ile güçlü secret verin (Node ile aynı olmalı).
_WA_INTERNAL_TOKEN_DEFAULT = 'bestoffice-wa-internal'


def _wa_service_base_url():
    """Node WhatsApp servisi taban URL (env: WHATSAPP_SERVICE_URL)."""
    return (os.environ.get('WHATSAPP_SERVICE_URL') or 'http://127.0.0.1:3001').rstrip('/')


def _wa_tenant_id():
    """g.tenant_schema varsa onu, yoksa (tek şirket / platform) 'default'."""
    schema = _tenant_schema_for_request()
    if schema:
        return schema
    return 'default'


def _wa_url(path_suffix):
    """Örn. path_suffix='durum' → http://…/t/{tenantId}/durum"""
    suffix = str(path_suffix or '').lstrip('/')
    return f"{_wa_service_base_url()}/t/{_wa_tenant_id()}/{suffix}"


def _wa_internal_token():
    """Node ile paylaşılan secret (env: WA_INTERNAL_TOKEN)."""
    raw = (os.environ.get('WA_INTERNAL_TOKEN') or '').strip()
    return raw or _WA_INTERNAL_TOKEN_DEFAULT


def _wa_internal_headers():
    """Sunucu→Node istekleri; tarayıcıya sızmaz."""
    return {'X-WA-Internal-Token': _wa_internal_token()}


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
    0: "Sayın {isim}, bugün vadesi dolan {tutar} TL tutarındaki ödemenizin hatırlatmasıdır. İş birliğiniz için teşekkür ederiz.",
    3: "Sayın {isim}, ödemenizde {gun} günlük bir gecikme görünüyor. Bakiyeniz {tutar} TL'dir. Gözden kaçmış olabileceğini düşündük.",
    7: "Sayın {isim}, ödemeniz {gun} gündür gecikmededir. {tutar} TL tutarındaki bakiyeniz için mutabakat veya teknik bir sorun varsa lütfen bizimle iletişime geçiniz.",
    15: "Sayın {isim}, gecikme süreniz {gun} günü bulmuştur. {tutar} TL tutarındaki bakiyenizin, hizmet sürekliliğinizde aksama olmaması adına gün içinde ödenmesini beklemekteyiz.",
    21: "Sayın {isim}, ödemeniz {gun} gündür yapılmamıştır. {tutar} TL tutarındaki borç bakiyenizin kapatılmaması durumunda sistem erişim kısıtlamaları gündeme gelecektir.",
    30: "Sayın {isim}, gecikmeniz {gun} günü (1 ayı) doldurmuştur. {tutar} TL tutarındaki bakiyenizin en kısa sürede kapatılmasını rica ederiz, aksi halde takip sürecimiz başlayacaktır.",
    60: "Sayın {isim}, ödemeniz {gun} gündür gecikmede olup, tutarınız {tutar} TL'ye ulaşmıştır. Önceki hatırlatmalarımıza rağmen ödeme yapılmadığı görülmektedir. Lütfen en kısa sürede ödemenizi tamamlayınız.",
    90: "Sayın {isim}, {gun} gündür devam eden ödeme geciken bakiyeniz {tutar} TL'dir. Bu gecikme hizmet sürekliliğinizi etkileyebilecek bir aşamaya gelmiştir. 7 gün içinde ödeme yapılmadığı takdirde hizmetiniz askıya alınabilir.",
    180: "Sayın {isim}, {gun} gündür ödenmeyen {tutar} TL tutarındaki borcunuz nedeniyle dosyanız hukuk birimine devredilme aşamasına gelmiştir. Mağduriyet yaşamamanız için lütfen ivedilikle ödemenizi yapınız.",
    365: "Sayın {isim}, {gun} gündür (1 yılı aşkın süredir) ödenmeyen {tutar} TL tutarındaki borcunuz nedeniyle yasal süreç başlatılacaktır. Bu son bildirimden itibaren 48 saat içinde ödeme yapılmadığı takdirde, hukuki işlemlere resmi olarak başlanacaktır.",
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
        _tutar_tr_goster,
    )
    haric_goster = str(request.args.get('haric_goster') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    _ensure_whatsapp_geciken_haric_table()
    bugun_key = f"{bugun.year}-{bugun.month}"

    # Tahsilat raporu ile aynı: virgüllü hizmet_turleri → lower whitelist; boş = filtre yok
    raw_hizmet = (request.args.get("hizmet_turleri") or "").strip()
    secili_hizmet_turleri = []
    if raw_hizmet:
        seen_ht = set()
        for part in raw_hizmet.split(","):
            v = part.strip().lower()
            if not v or v in seen_ht:
                continue
            seen_ht.add(v)
            secili_hizmet_turleri.append(v)

    # Grid cache'den ödenmemiş geçmiş ayları olan müşterileri çek
    haric_sql = ""
    if not haric_goster:
        haric_sql = """
        AND NOT EXISTS (
            SELECT 1 FROM whatsapp_geciken_haric h WHERE h.musteri_id = c.id
        )
        """

    hizmet_sql = ""
    sql_params = []
    if secili_hizmet_turleri:
        placeholders = ", ".join(["%s"] * len(secili_hizmet_turleri))
        hizmet_sql = (
            " AND LOWER(TRIM(COALESCE(NULLIF(TRIM(mk.hizmet_turu), ''), "
            "NULLIF(TRIM(c.hizmet_turu), ''), ''))) IN (" + placeholders + ")"
        )
        sql_params.extend(secili_hizmet_turleri)
    sql_params.append(bugun_key)

    rows = fetch_all("""
        SELECT c.id, c.name, c.musteri_adi, c.phone, c.phone2,
               COALESCE(c.guncel_kira_bedeli, c.ilk_kira_bedeli, mk.aylik_kira, 0) as aylik_tutar,
               mk.sozlesme_tarihi,
               COALESCE(NULLIF(TRIM(mk.hizmet_turu), ''), NULLIF(TRIM(c.hizmet_turu), ''), '') AS hizmet_turu,
               c.grup2_secimleri
        FROM customers c
        LEFT JOIN LATERAL (
            SELECT sozlesme_tarihi, aylik_kira, hizmet_turu
            FROM musteri_kyc
            WHERE musteri_id = c.id
            ORDER BY id DESC LIMIT 1
        ) mk ON TRUE
        WHERE c.durum = 'aktif'
        """ + haric_sql + hizmet_sql + """
        AND EXISTS (
            SELECT 1 FROM musteri_aylik_grid_cache gc,
            jsonb_array_elements(gc.payload::jsonb->'aylar') AS elem
            WHERE gc.musteri_id = c.id
            AND (elem->>'tahsil_edildi')::boolean = false
            AND to_date(elem->>'ay_key', 'YYYY-MM')
                <= to_date(%s, 'YYYY-MM')
            AND (elem->>'tutar_kdv_dahil')::float > 0
        )
    """, tuple(sql_params)) or []

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

    # En eski ödenmemiş ay_key — tek batch (eski: müşteri başına N+1 sorgu)
    oldest_ay_by_mid = {}
    if musteri_ids_all:
        oldest_rows = fetch_all(
            """
            SELECT musteri_id, MIN(elem->>'ay_key') AS ay_key
            FROM musteri_aylik_grid_cache gc,
                 jsonb_array_elements(gc.payload::jsonb->'aylar') AS elem
            WHERE gc.musteri_id = ANY(%s)
              AND (elem->>'tahsil_edildi')::boolean = false
              AND to_date(elem->>'ay_key', 'YYYY-MM')
                  <= to_date(%s, 'YYYY-MM')
              AND (elem->>'tutar_kdv_dahil')::float > 0
            GROUP BY musteri_id
            """,
            (musteri_ids_all, bugun_key),
        ) or []
        for orow in oldest_rows:
            mid_o = orow.get('musteri_id')
            ay_o = orow.get('ay_key')
            if mid_o is not None and ay_o:
                oldest_ay_by_mid[mid_o] = ay_o

    sonuc = []
    for r in rows:
        en_eski_ay_key = oldest_ay_by_mid.get(r['id'])
        if not en_eski_ay_key:
            continue

        # En eski ödenmemiş ayın sözleşme gününden gecikme hesapla
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

        mid_r = r.get('id')
        ozet_r = ozet_batch.get(mid_r) or {}
        # Mesaj {tutar}: Toplam sütunu ile aynı kaynak (birikmiş gecikmiş borç)
        tutar = round(float(ozet_r.get('toplam_borc') or 0), 2)
        sablon = SABLONLAR[esik].format(
            isim=isim,
            tutar=_tutar_tr_goster(tutar),
            gun=gecikme
        )
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
            _wa_url('kuyruk-toplu-ekle'),
            json={'liste': wa_liste},
            headers=_wa_internal_headers(),
            timeout=10
        )
        result = r.json()
        return jsonify({
            'ok': True,
            'servis_yaniti': result,
            'wa_tenant_id': _wa_tenant_id(),
        })
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': f'WhatsApp servisine bağlanılamadı: {e}'}), 500


@bp.route('/api/servis-durum')
@giris_gerekli
def api_servis_durum():
    """WhatsApp servisinin bağlantı durumunu kontrol eder"""
    import requests
    try:
        r = requests.get(
            _wa_url('durum'),
            headers=_wa_internal_headers(),
            timeout=5,
        )
        data = r.json() if r.content else {}
        if isinstance(data, dict):
            data.setdefault('wa_tenant_id', _wa_tenant_id())
        return jsonify(data)
    except Exception as e:
        return jsonify({'bagli': False, 'hata': str(e), 'wa_tenant_id': _wa_tenant_id()})


@bp.route('/qr-ac')
@giris_gerekli
def api_qr_ac():
    """Kiracıya özel WhatsApp QR/bağlı sayfası — Flask proxy (Node adresi tarayıcıya sızmaz)."""
    import requests
    try:
        r = requests.get(
            _wa_url('qr-goster'),
            headers=_wa_internal_headers(),
            timeout=30,
        )
        ctype = r.headers.get('Content-Type') or 'text/html; charset=utf-8'
        return Response(r.content, status=r.status_code, content_type=ctype)
    except Exception as e:
        body = (
            '<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">'
            '<title>WhatsApp</title></head>'
            '<body style="font-family:sans-serif;padding:40px;background:#111;color:#eee">'
            '<h1 style="color:#ef5350">WhatsApp QR alınamadı</h1>'
            f'<p>{e}</p><p>Tenant: <code>{_wa_tenant_id()}</code></p></body></html>'
        )
        return Response(body, status=502, content_type='text/html; charset=utf-8')
