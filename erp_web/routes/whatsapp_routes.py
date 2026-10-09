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

    # Çoklu yetkili (musteri_yetkililer) — arama/rapor satırı için; tablo yoksa no-op
    try:
        from db import ensure_musteri_yetkililer_table
        ensure_musteri_yetkililer_table()
    except Exception:
        pass

    rows = fetch_all("""
        SELECT c.id, c.name, c.musteri_adi, c.phone, c.phone2,
               COALESCE(c.guncel_kira_bedeli, c.ilk_kira_bedeli, mk.aylik_kira, 0) as aylik_tutar,
               mk.sozlesme_tarihi,
               COALESCE(NULLIF(TRIM(mk.hizmet_turu), ''), NULLIF(TRIM(c.hizmet_turu), ''), '') AS hizmet_turu,
               c.grup2_secimleri,
               COALESCE(
                   NULLIF(TRIM(y1.ad_soyad), ''),
                   NULLIF(TRIM(mk.yetkili_adsoyad), ''),
                   NULLIF(TRIM(c.yetkili_kisi), ''),
                   ''
               ) AS yetkili_adi,
               COALESCE(
                   NULLIF(TRIM(y1.tel), ''),
                   NULLIF(TRIM(y1.tel2), ''),
                   NULLIF(TRIM(mk.yetkili_tel), ''),
                   NULLIF(TRIM(mk.yetkili_tel2), ''),
                   ''
               ) AS yetkili_telefon,
               COALESCE(ya.yetkililer_metin, '') AS yetkililer_metin
        FROM customers c
        LEFT JOIN LATERAL (
            SELECT sozlesme_tarihi, aylik_kira, hizmet_turu,
                   yetkili_adsoyad, yetkili_tel, yetkili_tel2, yetkili_email
            FROM musteri_kyc
            WHERE musteri_id = c.id
            ORDER BY id DESC LIMIT 1
        ) mk ON TRUE
        LEFT JOIN LATERAL (
            SELECT ad_soyad, tel, tel2
            FROM musteri_yetkililer
            WHERE musteri_id = c.id
            ORDER BY birincil DESC NULLS LAST, sira ASC NULLS LAST, id ASC
            LIMIT 1
        ) y1 ON TRUE
        LEFT JOIN LATERAL (
            SELECT string_agg(
                NULLIF(TRIM(BOTH FROM CONCAT_WS(' ',
                    NULLIF(TRIM(ad_soyad), ''),
                    NULLIF(TRIM(tel), ''),
                    NULLIF(TRIM(tel2), ''),
                    NULLIF(TRIM(email), ''),
                    NULLIF(TRIM(email_sirket), '')
                )), ''),
                ' | '
                ORDER BY birincil DESC NULLS LAST, sira ASC NULLS LAST, id ASC
            ) AS yetkililer_metin
            FROM musteri_yetkililer
            WHERE musteri_id = c.id
        ) ya ON TRUE
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
    kyc_by_mid = {}
    tufe_map_local = {}
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
            kyc_by_mid[mid_k] = kyc_for_grid_k
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
        yetkili_adi = str(r.get('yetkili_adi') or '').strip()
        yetkili_telefon = str(r.get('yetkili_telefon') or '').strip()
        yetkililer_metin = str(r.get('yetkililer_metin') or '').strip()
        # Arama blob: tüm yetkililer metni + birincil ad/tel (tekrarlar zararsız)
        yetkililer_metin_arama = ' '.join(
            p for p in (yetkililer_metin, yetkili_adi, yetkili_telefon) if p
        ).strip()
        sonuc.append({
            'musteri_id': mid_r,
            'isim': isim,
            'telefon': telefon,
            'hizmet_turu': r.get('hizmet_turu') or '',
            'yetkili_adi': yetkili_adi,
            'yetkili_telefon': yetkili_telefon,
            'yetkililer_metin': yetkililer_metin_arama,
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

    if sonuc:
        try:
            from routes.giris_routes import (
                _plan_paket_yukle,
                _planli_reel_haritasi,
                geciken_satir_guncelleri,
            )

            mids_g = [s.get("musteri_id") for s in sonuc]
            _plan_paket_yukle(mids_g)
            reel_g = _planli_reel_haritasi(mids_g)
            yeni_g = geciken_satir_guncelleri(sonuc, bugun, kyc_by_mid, tufe_map_local, reel_g)
            for s, guncel in zip(sonuc, yeni_g):
                s["guncel"] = guncel
        except Exception:
            import logging
            logging.getLogger(__name__).exception("geciken plan guncel")

    sonuc.sort(key=lambda x: -x['gecikme_gun'])
    from ay_sinif import UYARI_METNI, kilitli_musteriler, siniflari_yukle
    try:
        sinif_map = siniflari_yukle([s.get("musteri_id") for s in sonuc])
        kilitli = kilitli_musteriler([s.get("musteri_id") for s in sonuc])
    except Exception:
        sinif_map = {}
        kilitli = set()
        for s in sonuc:
            s["sinif"] = "C"
            s["guvenilmez"] = True
            s["kilitli"] = False
    else:
        for s in sonuc:
            try:
                mid_s = int(s.get("musteri_id") or 0)
            except (TypeError, ValueError):
                mid_s = 0
            sinif = sinif_map.get(mid_s) or "C"
            s["sinif"] = sinif
            s["guvenilmez"] = sinif in ("B", "C")
            s["kilitli"] = mid_s in kilitli
    guvenilmez_var = any(s.get("guvenilmez") or s.get("kilitli") for s in sonuc)
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
        'grup2_etiket_map': grup2_etiket_map,
        'uyari': UYARI_METNI if guvenilmez_var else '',
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
    engellenen = 0
    from ay_sinif import UYARI_METNI, gonderime_izin, kilitli_musteriler, siniflari_yukle
    mids = []
    for item in liste:
        try:
            mids.append(int(item.get("musteri_id") or 0))
        except (TypeError, ValueError):
            mids.append(0)
    try:
        sinif_map = siniflari_yukle([m for m in mids if m > 0])
        kilit_set = kilitli_musteriler([m for m in mids if m > 0])
        sinif_hazir = True
    except Exception:
        sinif_map = {}
        kilit_set = set()
        sinif_hazir = False
    if not sinif_hazir:
        return jsonify({"ok": False, "mesaj": UYARI_METNI}), 400
    for item, mid in zip(liste, mids):
        tel = str(item.get('telefon') or '').strip()
        mesaj = str(item.get('mesaj') or '').strip()
        if not tel or not mesaj or mid <= 0:
            engellenen += 1
            continue
        onay = item.get("guvenilmez_onay") in (True, 1, "1", "true", "True", "yes", "on")
        izin, _neden = gonderime_izin(sinif_map.get(mid) or "C", onay, mid in kilit_set)
        if not izin:
            engellenen += 1
            continue
        wa_liste.append({'telefon': tel, 'mesaj': mesaj})

    if not wa_liste:
        return jsonify({'ok': False, 'mesaj': UYARI_METNI, 'engellenen': engellenen}), 400

    try:
        r = requests.post(
            _wa_url('kuyruk-toplu-ekle'),
            json={'liste': wa_liste},
            headers=_wa_internal_headers(),
            timeout=10
        )
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': f'WhatsApp servisine bağlanılamadı: {e}'}), 500

    try:
        result = r.json() if r.content else {}
    except Exception:
        result = {}
    if not isinstance(result, dict):
        result = {}
    if r.status_code >= 400 or result.get('ok') is False:
        if r.status_code >= 400:
            return jsonify({
                'ok': False,
                'mesaj': f'WhatsApp servisi hata döndü (HTTP {r.status_code}).',
            }), int(r.status_code)
        return jsonify({
            'ok': False,
            'mesaj': 'WhatsApp servisi gönderimi kabul etmedi.',
        }), 502
    return jsonify({
        'ok': True,
        'servis_yaniti': result,
        'wa_tenant_id': _wa_tenant_id(),
        'engellenen': engellenen,
    })


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
