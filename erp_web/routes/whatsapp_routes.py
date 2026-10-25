from flask import Blueprint, jsonify, render_template, request, Response
from datetime import date, datetime
import os
import time
from db import fetch_all, fetch_one, execute, _tenant_schema_for_request
from auth import admin_gerekli, giris_gerekli, yetki_gerekli

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


def _wa_servis_etiket():
    """Sağlık ekranının baktığı servis. Tek serviste sabit etiket."""
    if _wa_tenant_id() == "default":
        return "Varsayılan servis"
    return "Kiracı servisi"


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

    dondurma_ozet = None
    dondurma_mids = set()
    try:
        from routes.giris_routes import (
            _dondurma_ay_kumesi_toplu,
            _plan_kutu,
            geciken_kayitlari_suz,
        )

        _dondurma_ay_kumesi_toplu(mids)
        kutu = (_plan_kutu().get("dondurma") or {})
        dmap = {}
        for i in mids:
            kum = kutu.get(int(i)) or set()
            if kum:
                dmap[int(i)] = set(kum)
        if dmap:
            dondurma_mids = set(dmap)
            ay_rows = fetch_all(
                """
                SELECT gc.musteri_id,
                       elem->>'ay_key' AS ay_key,
                       (elem->>'tutar_kdv_dahil')::float AS tutar
                FROM musteri_aylik_grid_cache gc,
                     jsonb_array_elements(gc.payload::jsonb->'aylar') AS elem
                WHERE gc.musteri_id = ANY(%s)
                  AND (elem->>'tahsil_edildi')::boolean = false
                  AND to_date(elem->>'ay_key', 'YYYY-MM')
                      <= to_date(%s, 'YYYY-MM')
                  AND (elem->>'tutar_kdv_dahil')::float > 0
                """,
                (list(dmap.keys()), bugun_key),
            ) or []
            grup = {}
            for ay in ay_rows:
                grup.setdefault(int(ay["musteri_id"]), []).append(ay)
            dondurma_ozet = {
                x["musteri_id"]: x
                for x in geciken_kayitlari_suz(
                    [{"musteri_id": i, "aylar": grup.get(i, [])} for i in dmap],
                    dmap,
                    bugun,
                )
            }
    except Exception:
        import logging
        logging.getLogger(__name__).exception("geciken dondurma suz")
        dondurma_ozet = None
        dondurma_mids = set()

    sonuc = []
    for r in rows:
        if dondurma_ozet is not None and int(r["id"]) in dondurma_mids:
            oz_don = dondurma_ozet.get(int(r["id"]))
            if not oz_don:
                continue
            oldest_ay_by_mid[r["id"]] = oz_don["en_eski"]
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
        if dondurma_ozet is not None and int(mid_r) in dondurma_mids:
            oz_don = dondurma_ozet.get(int(mid_r)) or {}
            tutar = round(float(oz_don.get("toplam") or 0), 2)
            ozet_r = dict(ozet_r)
            ozet_r["toplam_borc"] = tutar
            ozet_r["borc_month"] = oz_don.get("guncel") or 0
            ozet_r["geciken_ay"] = oz_don.get("ay") or 0
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
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        # İstek servise ulaşmış olabilir; başarısız sayma, sonuç doğrulanamadı.
        return jsonify({
            'ok': False,
            'durum': 'belirsiz',
            'mesaj': "Gönderim doğrulanamadı, WhatsApp'ı kontrol edin",
        }), 200
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


@bp.route('/saglik')
@yetki_gerekli("muhasebe")
def wa_saglik_sayfa():
    """WhatsApp Sağlık ekranı (yönetici/muhasebe)."""
    return render_template('whatsapp/saglik.html', wa_servis_etiket=_wa_servis_etiket())


def _saglik_servis_durumu(timeout=(2, 3)):
    """(ulasim, oturum_kodu, teshis). Oturum uyandirilmaz (durum-bak)."""
    import requests
    from wa_saglik import durum_teshis_govde, oturum_kodu_govde

    try:
        r = requests.get(
            _wa_url('durum-bak'),
            headers=_wa_internal_headers(),
            timeout=timeout,
        )
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError):
        return "baglanti_yok", None, None
    except requests.exceptions.Timeout:
        return "zaman_asimi", None, None
    except Exception:
        return "baglanti_yok", None, None
    if r.status_code >= 500:
        return "http_5xx", None, None
    if r.status_code >= 400:
        return "http_4xx", None, None
    try:
        body = r.json() if r.content else {}
    except Exception:
        body = {}
    return "ok", oturum_kodu_govde(body), durum_teshis_govde(body)


def _saglik_audit(olay: str, anlik: dict) -> None:
    try:
        from flask_login import current_user

        from audit_log import audit_yaz

        audit_yaz(
            lambda sql, p=None: execute(sql, p) if p is not None else execute(sql),
            olay=olay,
            varlik="whatsapp_servis",
            kayit_id=None,
            musteri_id=None,
            kullanici=str(getattr(current_user, "username", "") or ""),
            anlik=anlik,
        )
    except Exception:
        pass


_ROZET_TTL_SN = 12.0
_ROZET_KIRMIZI = frozenset({"baglanti_yok", "zaman_asimi", "http_5xx", "http_4xx"})
_rozet_onbellek = {}


def _rozet_onbellek_temizle():
    _rozet_onbellek.clear()


def _rozet_onbellek_al(anahtar):
    satir = _rozet_onbellek.get(anahtar)
    if not satir:
        return None
    an, seviye, etiket = satir
    if (time.monotonic() - an) > _ROZET_TTL_SN:
        return None
    return seviye, etiket


def _rozet_yanit(anahtar, seviye, etiket):
    _rozet_onbellek[anahtar] = (time.monotonic(), seviye, etiket)
    return jsonify({"ok": True, "seviye": seviye, "etiket": etiket})


@bp.route('/api/saglik-rozet')
@yetki_gerekli("muhasebe")
def api_wa_saglik_rozet():
    """Hub rozeti. Yalnız seviye. Servis kapalıyken sayaç yok."""
    import wa_saglik

    anahtar = _wa_tenant_id()
    hazir = _rozet_onbellek_al(anahtar)
    if hazir is not None:
        seviye, etiket = hazir
        return jsonify({"ok": True, "seviye": seviye, "etiket": etiket})
    etiket = _wa_servis_etiket()
    try:
        ulasim, oturum, _teshis = _saglik_servis_durumu(timeout=(1, 1.5))
    except Exception:
        return _rozet_yanit(anahtar, "bilinmiyor", etiket)
    if ulasim in _ROZET_KIRMIZI:
        return _rozet_yanit(anahtar, "kirmizi", etiket)
    if ulasim != "ok":
        return _rozet_yanit(anahtar, "bilinmiyor", etiket)
    if oturum != "hazir":
        return _rozet_yanit(anahtar, "sari", etiket)
    try:
        n = wa_saglik.rozet_takili_sayisi()
    except Exception:
        n = None
    if n is None or int(n) <= 0:
        return _rozet_yanit(anahtar, "yesil", etiket)
    return _rozet_yanit(anahtar, "sari", etiket)


@bp.route('/api/saglik-ozet')
@yetki_gerekli("muhasebe")
def api_wa_saglik_ozet():
    import wa_saglik

    ulasim, oturum, teshis = _saglik_servis_durumu()
    sayaclar = wa_saglik.saglik_sayaclari()
    return jsonify({
        'ok': True,
        'kontroller': wa_saglik.kontrolleri_uret(ulasim, oturum, sayaclar, teshis=teshis),
        'sayaclar': sayaclar,
        'durum_teshis': teshis,
    })


@bp.route('/api/saglik-uyandir', methods=['POST'])
@yetki_gerekli("muhasebe")
def api_wa_saglik_uyandir():
    import requests

    try:
        r = requests.post(
            _wa_url('uyandir'),
            headers=_wa_internal_headers(),
            timeout=(3, 22),
        )
        ok = r.status_code < 400
        sonuc = 'ok' if ok else 'http_hata'
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        ok, sonuc = False, 'ulasilamadi'
    except Exception:
        ok, sonuc = False, 'hata'
    _saglik_audit('wa_saglik_uyandir', {'sonuc': sonuc})
    return jsonify({
        'ok': ok,
        'mesaj': ('Servisi uyandırma isteği gönderildi. Birkaç saniye sonra yenileyin.'
                  if ok else 'Servise ulaşılamadı. Ofis bilgisayarı kapalı olabilir ya da tünel kopmuş.'),
    }), 200 if ok else 502


@bp.route('/api/saglik-oturum-kurtar', methods=['POST'])
@yetki_gerekli("muhasebe")
def api_wa_saglik_oturum_kurtar():
    """Servisin kendi Chrome'unu kapatip oturumu yeniden baslatir (dongu kurtarma)."""
    import requests

    try:
        r = requests.post(
            _wa_url('yeniden-baslat'),
            headers=_wa_internal_headers(),
            timeout=(3, 20),
        )
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError):
        _saglik_audit('wa_saglik_oturum_kurtar', {'sonuc': 'ulasilamadi'})
        return jsonify({
            'ok': False, 'sonuc': 'ulasilamadi',
            'mesaj': 'Servise ulaşılamadı. Ofis bilgisayarı kapalı olabilir ya da tünel kopmuş.',
        }), 502
    except requests.exceptions.Timeout:
        _saglik_audit('wa_saglik_oturum_kurtar', {'sonuc': 'zaman_asimi'})
        return jsonify({
            'ok': False, 'sonuc': 'zaman_asimi',
            'mesaj': 'Servis yanıt vermedi; kurtarma başlamış olabilir. Birkaç dakika sonra yenileyin.',
        }), 504
    except Exception:
        _saglik_audit('wa_saglik_oturum_kurtar', {'sonuc': 'hata'})
        return jsonify({
            'ok': False, 'sonuc': 'hata',
            'mesaj': 'Kurtarma isteği gönderilemedi.',
        }), 502
    if r.status_code == 404:
        _saglik_audit('wa_saglik_oturum_kurtar', {'sonuc': 'desteklenmiyor'})
        return jsonify({
            'ok': False, 'sonuc': 'desteklenmiyor', 'desteklenmiyor': True,
            'mesaj': ('Servis bu özelliği desteklemiyor, ofis bilgisayarında '
                      'servisi güncelleyip yeniden başlatın.'),
        })
    try:
        govde = r.json() if r.content else {}
    except Exception:
        govde = {}
    if not isinstance(govde, dict):
        govde = {}
    asama = str(govde.get('asama') or '')
    _saglik_audit('wa_saglik_oturum_kurtar', {'sonuc': asama or 'hata'})
    if govde.get('ok'):
        return jsonify({
            'ok': True, 'sonuc': asama,
            'mesaj': (f"Kurtarma başlatıldı (aşama: {asama}). "
                      'Oturum 1-2 dakika içinde yeniden açılır; sonra yenileyin.'),
        })
    if asama == 'zaten_calisiyor':
        return jsonify({
            'ok': False, 'sonuc': asama,
            'mesaj': 'Kurtarma zaten çalışıyor; bitince yenileyin.',
        })
    return jsonify({
        'ok': False, 'sonuc': asama or 'hata',
        'mesaj': 'Kurtarma başlatılamadı. Birazdan yeniden deneyin.',
    })


@bp.route('/api/saglik-tazele', methods=['POST'])
@yetki_gerekli("muhasebe")
def api_wa_saglik_tazele():
    import sozlesme_whatsapp as sw
    import wa_saglik

    kayitlar = wa_saglik.bekleyen_kayitlar()
    ozet = wa_saglik.tazele_calistir(
        kayitlar, sw._sonuc_varsayilan, sw._satiri_tazele,
        azami=20, butce_sn=30,
    )
    _saglik_audit('wa_saglik_tazele', dict(ozet))
    mesaj = (f"{ozet['taranan']} kayıt sorgulandı; {ozet['guncellenen']} güncellendi, "
             f"{ozet['belirsiz_kalan']} hâlâ belirsiz.")
    if ozet.get('kalan'):
        mesaj += f" {ozet['kalan']} kayıt daha var; tekrar basın."
    return jsonify({
        'ok': True,
        **ozet,
        'mesaj': mesaj,
    })


@bp.route('/api/saglik-eski-kapat', methods=['POST'])
@yetki_gerekli("muhasebe")
def api_wa_saglik_eski_kapat():
    """2 saatten eski kuyrukta/gonderiliyor kayitlari servise sormadan
    'suresi_doldu' yapar; bu kayitlar WhatsApp'a gonderilmez."""
    import wa_saglik

    sonuc = wa_saglik.eski_kapat()
    # Denetim: sayi + id listesi (icerik yok).
    _saglik_audit('wa_saglik_eski_kapat', dict(sonuc))
    return jsonify({
        'ok': True,
        **sonuc,
        'mesaj': (f"{sonuc['adet']} eski kayıt kapatıldı; "
                  "hiçbiri WhatsApp'a gönderilmedi."),
    })


@bp.route('/api/saglik-belirsiz')
@yetki_gerekli("muhasebe")
def api_wa_saglik_belirsiz():
    import wa_saglik

    return jsonify({'ok': True, 'kayitlar': wa_saglik.belirsiz_kayitlar()})


@bp.route('/api/saglik-belirsiz-isaret', methods=['POST'])
@yetki_gerekli("muhasebe")
def api_wa_saglik_belirsiz_isaret():
    """Belirsiz kaydi elle gonderildi/gonderilmedi isaretler (denetimli)."""
    import wa_saglik

    govde = request.get_json(silent=True) or {}
    sonuc = wa_saglik.belirsiz_isaretle(
        govde.get('kaynak'), govde.get('id'), govde.get('sonuc'))
    if not sonuc.get('ok'):
        neden = sonuc.get('neden') or 'isaretlenemedi'
        kod = 404 if neden == 'bulunamadi' else 409 if neden == 'belirsiz_degil' else 400
        return jsonify({'ok': False, 'hata': neden, **sonuc}), kod
    # Denetim: id + eski/yeni durum (icerik yok; kullanici audit tarafinda).
    _saglik_audit('wa_belirsiz_isaret', {
        'kaynak': sonuc['kaynak'], 'kayit_id': sonuc['kayit_id'],
        'eski_durum': sonuc['eski_durum'], 'yeni_durum': sonuc['yeni_durum'],
    })
    return jsonify({'ok': True, **sonuc, 'mesaj': 'Kayıt işaretlendi.'})


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


def _toplu_kapali():
    import wa_kampanya

    if not wa_kampanya.bayrak_acik():
        return jsonify({"ok": False}), 404
    return None


def _toplu_json(sonuc, tamam=(200,)):
    neden = (sonuc or {}).get("neden")
    if not sonuc.get("ok"):
        kod = 404 if neden == "yok" else 409 if neden in ("acik_var", "test_yok", "durum", "alici_yok") else 400
        return jsonify({"ok": False, "neden": neden or "kayit"}), kod
    govde = {"ok": True}
    for anahtar in ("id", "durum", "test_ok", "anahtar", "alici", "mid", "kapali", "kampanya"):
        if anahtar in sonuc:
            govde[anahtar] = sonuc[anahtar]
    return jsonify(govde), 200


@bp.route('/api/toplu-durum')
@admin_gerekli
def api_toplu_durum():
    kapali = _toplu_kapali()
    if kapali:
        return kapali
    import wa_kampanya
    return _toplu_json(wa_kampanya.durum_istegi())


@bp.route('/api/toplu-taslak', methods=['POST'])
@admin_gerekli
def api_toplu_taslak():
    kapali = _toplu_kapali()
    if kapali:
        return kapali
    import wa_kampanya
    from flask_login import current_user

    govde = request.get_json(silent=True) or {}
    sonuc = wa_kampanya.taslak_kaydet(
        govde.get("sablon"), govde.get("tavan"), govde.get("id"),
        getattr(current_user, "id", None),
    )
    return _toplu_json(sonuc)


@bp.route('/api/toplu-onizleme', methods=['POST'])
@admin_gerekli
def api_toplu_onizleme():
    kapali = _toplu_kapali()
    if kapali:
        return kapali
    import wa_kampanya

    govde = request.get_json(silent=True) or {}
    try:
        kid = int(govde.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "neden": "gecersiz"}), 400
    sonuc = wa_kampanya.onizleme_istegi(kid)
    if not sonuc.get("ok"):
        return _toplu_json(sonuc)
    return jsonify({"ok": True, **wa_kampanya.onizleme_govde(sonuc)}), 200


@bp.route('/api/toplu-test', methods=['POST'])
@admin_gerekli
def api_toplu_test():
    kapali = _toplu_kapali()
    if kapali:
        return kapali
    import wa_kampanya

    govde = request.get_json(silent=True) or {}
    try:
        kid = int(govde.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "neden": "gecersiz"}), 400
    return _toplu_json(wa_kampanya.test_gonder_istegi(kid, govde.get("telefon")))


@bp.route('/api/toplu-test-sonuc')
@admin_gerekli
def api_toplu_test_sonuc():
    kapali = _toplu_kapali()
    if kapali:
        return kapali
    import wa_kampanya

    try:
        kid = int(request.args.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "neden": "gecersiz"}), 400
    return _toplu_json(wa_kampanya.test_sonuc_istegi(kid, request.args.get("anahtar") or ""))


@bp.route('/api/toplu-baslat', methods=['POST'])
@admin_gerekli
def api_toplu_baslat():
    kapali = _toplu_kapali()
    if kapali:
        return kapali
    import wa_kampanya

    govde = request.get_json(silent=True) or {}
    try:
        kid = int(govde.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "neden": "gecersiz"}), 400
    return _toplu_json(wa_kampanya.baslat_istegi(kid))


@bp.route('/api/toplu-durdur', methods=['POST'])
@admin_gerekli
def api_toplu_durdur():
    kapali = _toplu_kapali()
    if kapali:
        return kapali
    import wa_kampanya

    govde = request.get_json(silent=True) or {}
    try:
        kid = int(govde.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "neden": "gecersiz"}), 400
    return _toplu_json(wa_kampanya.durdur_istegi(kid))


@bp.route('/api/toplu-devam', methods=['POST'])
@admin_gerekli
def api_toplu_devam():
    kapali = _toplu_kapali()
    if kapali:
        return kapali
    import wa_kampanya

    govde = request.get_json(silent=True) or {}
    try:
        kid = int(govde.get("id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "neden": "gecersiz"}), 400
    return _toplu_json(wa_kampanya.devam_istegi(kid))


@bp.route('/api/toplu-haric')
@admin_gerekli
def api_toplu_haric_oku():
    kapali = _toplu_kapali()
    if kapali:
        return kapali
    import wa_kampanya
    return jsonify(wa_kampanya.haric_liste())


@bp.route('/api/toplu-haric', methods=['POST'])
@admin_gerekli
def api_toplu_haric_yaz():
    kapali = _toplu_kapali()
    if kapali:
        return kapali
    import wa_kampanya

    govde = request.get_json(silent=True) or {}
    return _toplu_json(wa_kampanya.haric_yaz(govde.get("musteri_id"), bool(govde.get("kapali"))))
