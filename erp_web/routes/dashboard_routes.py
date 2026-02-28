"""
Sekreterya & Yönetim Dashboard — Tüm modülleri tek ekranda toplar.
Müşteri analizi, ödeme takibi, kargo, sözleşme alarmı, hızlı müdahale.
"""
from flask import Blueprint, render_template, request, jsonify
from auth import giris_gerekli
from db import fetch_all, fetch_one, execute_returning, ensure_faturalar_amount_columns
from datetime import date, timedelta

bp = Blueprint("dashboard", __name__)

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
         "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def _bugun():
    return date.today()


def _dashboard_istatistikler():
    """8 kart için istatistikler."""
    bugun = _bugun()
    yil = bugun.year

    # Müşteri sayısı
    r = fetch_one("SELECT COUNT(*) as n FROM customers")
    musteri_say = (r.get("n") or 0) if r else 0

    # Fatura sayısı
    r = fetch_one("SELECT COUNT(*) as n FROM faturalar")
    fatura_say = (r.get("n") or 0) if r else 0

    # Ödenmemiş toplam (alacak) — COALESCE(toplam, tutar) farklı şemalara uyum
    r = fetch_one("SELECT COALESCE(SUM(COALESCE(f.toplam, f.tutar)), 0) as toplam FROM faturalar f WHERE COALESCE(f.durum,'') != 'odendi'")
    odenmemis = float(r.get("toplam") or 0) if r else 0

    # Kargo sayısı (bugün gelen: tarih = bugün)
    r = fetch_one("SELECT COUNT(*) as n FROM kargolar WHERE (tarih::date) = %s", (bugun,))
    bugun_kargo_say = (r.get("n") or 0) if r else 0

    # Toplam kargo
    r = fetch_one("SELECT COUNT(*) as n FROM kargolar")
    kargo_say = (r.get("n") or 0) if r else 0

    # Geciken alacak (vade_tarihi < bugün ve durum != odendi)
    r = fetch_one("""
        SELECT COALESCE(SUM(COALESCE(f.toplam, f.tutar)), 0) as toplam FROM faturalar f
        WHERE COALESCE(f.durum,'') != 'odendi' AND f.vade_tarihi IS NOT NULL AND (f.vade_tarihi::date) < %s
    """, (bugun,))
    geciken_toplam = float(r.get("toplam") or 0) if r else 0

    # Kritik geciken (30+ gün)
    otuz_gun_once = bugun - timedelta(days=30)
    r = fetch_one("""
        SELECT COUNT(*) as n FROM faturalar
        WHERE COALESCE(durum,'') != 'odendi' AND vade_tarihi IS NOT NULL AND (vade_tarihi::date) <= %s
    """, (otuz_gun_once,))
    kritik_geciken_say = (r.get("n") or 0) if r else 0

    # Yakın geciken (1-30 gün)
    r = fetch_one("""
        SELECT COUNT(*) as n FROM faturalar
        WHERE COALESCE(durum,'') != 'odendi' AND vade_tarihi IS NOT NULL
        AND (vade_tarihi::date) > %s AND (vade_tarihi::date) < %s
    """, (otuz_gun_once, bugun))
    yakin_geciken_say = (r.get("n") or 0) if r else 0

    # Sözleşmesi bitecekler (15-30 gün içinde) — musteri_kyc.sozlesme_bitis
    onbes_gun = bugun + timedelta(days=15)
    otuz_gun = bugun + timedelta(days=30)
    try:
        r = fetch_one("""
            SELECT COUNT(*) as n FROM musteri_kyc k
            WHERE k.sozlesme_bitis IS NOT NULL AND k.sozlesme_bitis >= %s AND k.sozlesme_bitis <= %s
        """, (bugun, otuz_gun))
        sozlesme_bitecek_say = (r.get("n") or 0) if r else 0
    except Exception:
        sozlesme_bitecek_say = 0

    # Boş ofis sayısı (offices.status = 'bos')
    try:
        r = fetch_one("SELECT COUNT(*) as n FROM offices WHERE COALESCE(status,'') = 'bos' AND COALESCE(is_active, true) = true")
        bos_ofis_say = (r.get("n") or 0) if r else 0
        r2 = fetch_one("SELECT COUNT(*) as n FROM offices WHERE COALESCE(type,'') = 'Hazır Ofis' AND COALESCE(status,'') = 'bos'")
        bos_hazir = (r2.get("n") or 0) if r2 else 0
        r3 = fetch_one("SELECT COUNT(*) as n FROM offices WHERE COALESCE(type,'') = 'Sanal' AND COALESCE(status,'') = 'bos'")
        bos_sanal = (r3.get("n") or 0) if r3 else 0
    except Exception:
        bos_ofis_say = bos_hazir = bos_sanal = 0

    # Bugün beklenen tahsilat (vadesi bugün olan faturalar toplamı)
    r = fetch_one("""
        SELECT COALESCE(SUM(COALESCE(f.toplam, f.tutar)), 0) as toplam FROM faturalar f
        WHERE COALESCE(f.durum,'') != 'odendi' AND (f.vade_tarihi::date) = %s
    """, (bugun,))
    bugun_tahsilat = float(r.get("toplam") or 0) if r else 0

    # TÜFE güncel (son ay)
    try:
        r = fetch_one("SELECT oran FROM tufe_verileri WHERE year = %s ORDER BY month DESC LIMIT 1", (yil,))
        tufe_oran = float(r.get("oran") or 0) if r else 0
    except Exception:
        tufe_oran = 0

    return {
        "musteri_say": musteri_say,
        "fatura_say": fatura_say,
        "odenmemis": odenmemis,
        "kargo_say": kargo_say,
        "bugun_kargo_say": bugun_kargo_say,
        "geciken_toplam": geciken_toplam,
        "kritik_geciken_say": kritik_geciken_say,
        "yakin_geciken_say": yakin_geciken_say,
        "sozlesme_bitecek_say": sozlesme_bitecek_say,
        "bos_ofis_say": bos_ofis_say,
        "bos_hazir": bos_hazir,
        "bos_sanal": bos_sanal,
        "bugun_tahsilat": bugun_tahsilat,
        "tufe_oran": tufe_oran,
    }


def _dashboard_tablo_data(arama="", filtre=None):
    """
    Ana tablo: müşteri, ofis, ürün/kira, 12 aylık ödeme grid, toplam alacak, son kargo, sözleşme bitiş.
    filtre: tam | yakin | kritik | kargo | bos | sozlesme
    """
    bugun = _bugun()
    yil = bugun.year
    params = []
    sql_extra = ""
    if arama:
        sql_extra += " AND (c.name ILIKE %s OR c.phone ILIKE %s OR c.tax_number ILIKE %s)"
        params.extend([f"%{arama}%", f"%{arama}%", f"%{arama}%"])

    # Müşteri + ofis (offices.customer_id = c.id ile)
    sql = """
        SELECT c.id as musteri_id, c.name as musteri_adi, c.phone, c.office_code,
               o.code as ofis_kod, o.type as ofis_tip, o.status as ofis_durum
        FROM customers c
        LEFT JOIN offices o ON o.customer_id = c.id
        WHERE 1=1
    """ + sql_extra + " ORDER BY c.name"
    musteriler = fetch_all(sql, params) if params else fetch_all(sql)

    # Sözleşme bitiş (musteri_kyc) — son kayıt per müşteri
    try:
        kyc_list = fetch_all("""
            SELECT k.musteri_id, k.sozlesme_bitis, k.hizmet_turu FROM musteri_kyc k
            INNER JOIN (SELECT musteri_id, MAX(id) as mid FROM musteri_kyc GROUP BY musteri_id) t
            ON k.musteri_id = t.musteri_id AND k.id = t.mid
        """)
        kyc_by_cid = {r["musteri_id"]: r for r in (kyc_list or [])}
    except Exception:
        kyc_by_cid = {}
    # Son kargo per müşteri (durum sütunu yoksa teslim_alan'dan türet)
    try:
        kargo_rows = fetch_all("""
            SELECT DISTINCT ON (musteri_id) musteri_id, tarih, teslim_alan, takip_no
            FROM kargolar ORDER BY musteri_id, created_at DESC
        """)
    except Exception:
        kargo_rows = []
    for r in (kargo_rows or []):
        r["durum"] = "Teslim" if (r.get("teslim_alan") and str(r.get("teslim_alan")).strip()) else "Bekliyor"
    kargo_by_cid = {r["musteri_id"]: r for r in (kargo_rows or [])}

    # Ödenmemiş faturalar + vade (12 ay grid için)
    faturalar = fetch_all("""
        SELECT musteri_id, COALESCE(f.toplam, f.tutar) as toplam, f.vade_tarihi, f.durum, f.id as fatura_id
        FROM faturalar f WHERE COALESCE(f.durum,'') != 'odendi' AND f.vade_tarihi IS NOT NULL
    """)
    fat_by_cid = {}
    for f in (faturalar or []):
        cid = f["musteri_id"]
        if cid not in fat_by_cid:
            fat_by_cid[cid] = []
        fat_by_cid[cid].append(f)

    # Tüm faturalar (ödendi dahil) 12 ay grid için: ay bazında vade ve durum
    tum_faturalar = fetch_all("""
        SELECT musteri_id, COALESCE(f.toplam, f.tutar) as toplam, f.vade_tarihi, f.durum FROM faturalar f WHERE f.vade_tarihi IS NOT NULL
    """)
    fat_ay_by_cid = {}
    for f in (tum_faturalar or []):
        cid = f["musteri_id"]
        if cid not in fat_ay_by_cid:
            fat_ay_by_cid[cid] = {}
        try:
            vd = f["vade_tarihi"]
            if hasattr(vd, "month"):
                ay = vd.month
            else:
                ay = int(str(vd)[5:7])
            if ay not in fat_ay_by_cid[cid]:
                fat_ay_by_cid[cid][ay] = []
            fat_ay_by_cid[cid][ay].append(f)
        except Exception:
            pass

    # Filtre uygula
    if filtre == "kritik":
        musteriler = [m for m in musteriler if fat_by_cid.get(m["musteri_id"]) and any(
            (f.get("vade_tarihi") or date.min) <= (bugun - timedelta(days=30)) for f in fat_by_cid[m["musteri_id"]]
        )]
    elif filtre == "yakin":
        musteriler = [m for m in musteriler if fat_by_cid.get(m["musteri_id"]) and any(
            (f.get("vade_tarihi") or date.min) > (bugun - timedelta(days=30)) and (f.get("vade_tarihi") or date.min) < bugun
            for f in fat_by_cid[m["musteri_id"]]
        )]
    elif filtre == "tam":
        musteriler = [m for m in musteriler if not fat_by_cid.get(m["musteri_id"]) or len(fat_by_cid[m["musteri_id"]]) == 0]
    elif filtre == "kargo":
        musteriler = [m for m in musteriler if kargo_by_cid.get(m["musteri_id"]) and (kargo_by_cid[m["musteri_id"]].get("durum") or "").lower() in ("beklemede", "bekliyor", "bekleyen", "")]
    elif filtre == "bos":
        musteriler = [m for m in musteriler if (m.get("ofis_durum") or "").lower() == "bos"]
    elif filtre == "sozlesme":
        def _sozlesme_yakin(mid):
            k = kyc_by_cid.get(mid)
            if not k or not k.get("sozlesme_bitis"):
                return False
            sb = k["sozlesme_bitis"]
            if hasattr(sb, "year"):
                d = sb
            else:
                try:
                    d = date(*[int(x) for x in str(sb)[:10].split("-")])
                except Exception:
                    return False
            return bugun <= d <= bugun + timedelta(days=30)
        musteriler = [m for m in musteriler if _sozlesme_yakin(m["musteri_id"])]

    rows = []
    for m in musteriler:
        cid = m["musteri_id"]
        toplam_alacak = sum(f.get("toplam") or 0 for f in fat_by_cid.get(cid, []))
        kyc = kyc_by_cid.get(cid, {})
        sozlesme_bitis = kyc.get("sozlesme_bitis")
        hizmet_turu = kyc.get("hizmet_turu") or "-"
        son_kargo = kargo_by_cid.get(cid, {})
        kargo_durum = (son_kargo.get("durum") or "—")
        if son_kargo and son_kargo.get("teslim_alan"):
            kargo_durum = "Teslim"
        elif son_kargo and son_kargo.get("takip_no"):
            kargo_durum = "Bekliyor"

        # 12 ay grid: her ay için durum (odendi / gecikti / bugun_yakin / gelecek)
        aylik = []
        for ay in range(1, 13):
            durum = "gelecek"  # gri
            fat_list = fat_ay_by_cid.get(cid, {}).get(ay, [])
            for f in fat_list:
                vd = f.get("vade_tarihi")
                if not vd:
                    continue
                if hasattr(vd, "year"):
                    vd_date = vd
                else:
                    try:
                        vd_date = date(*[int(x) for x in str(vd)[:10].split("-")])
                    except Exception:
                        continue
                odendi = (f.get("durum") or "").lower() == "odendi"
                if odendi:
                    durum = "odendi"
                    break
                if vd_date < bugun:
                    durum = "gecikti"
                    break
                if vd_date == bugun or (vd_date - bugun).days <= 7:
                    durum = "bugun_yakin"
                    break
                durum = "gelecek"
                break
            aylik.append(durum)

        soz_str = ""
        if sozlesme_bitis:
            if hasattr(sozlesme_bitis, "strftime"):
                soz_str = sozlesme_bitis.strftime("%d.%m.%Y")
            else:
                soz_str = str(sozlesme_bitis)[:10]
        rows.append({
            "musteri_id": cid,
            "musteri_adi": m.get("musteri_adi") or "—",
            "phone": m.get("phone") or "—",
            "ofis_kod": m.get("ofis_kod") or "—",
            "ofis_tip": m.get("ofis_tip") or "—",
            "hizmet_turu": hizmet_turu,
            "aylik_durum": aylik,
            "toplam_alacak": round(toplam_alacak, 2),
            "son_kargo_durum": kargo_durum,
            "sozlesme_bitis": sozlesme_bitis,
            "sozlesme_bitis_str": soz_str or "—",
        })
    return rows


@bp.route("/")
@giris_gerekli
def index():
    """Sekreterya Dashboard ana sayfa."""
    ensure_faturalar_amount_columns()
    stats = _dashboard_istatistikler()
    tablo = _dashboard_tablo_data()
    return render_template(
        "dashboard.html",
        **stats,
        tablo_rows=tablo,
        aylar=AYLAR,
        bugun=_bugun(),
    )


@bp.route("/api/tablo")
@giris_gerekli
def api_tablo():
    """Arama/filtre ile tablo verisi (AJAX)."""
    arama = (request.args.get("q") or "").strip()
    filtre = (request.args.get("filtre") or "").strip() or None
    rows = _dashboard_tablo_data(arama=arama, filtre=filtre)
    return jsonify(rows)


@bp.route("/api/whatsapp-metin")
@giris_gerekli
def api_whatsapp_metin():
    """Gecikme süresine göre kademeli WhatsApp metni. GET: musteri_id, toplam_alacak, geciken_gun."""
    musteri_id = request.args.get("musteri_id", type=int)
    toplam_alacak = request.args.get("toplam_alacak", type=float) or 0
    geciken_gun = request.args.get("geciken_gun", type=int) or 0
    if not musteri_id:
        return jsonify({"ok": False, "metin": ""}), 400
    if geciken_gun < 7:
        metin = "Merhaba, unutkanlık olmuş olabilir; ödeme hatırlatması yapıyoruz. Lütfen kalan tutarı zamanında ödeyiniz."
    elif geciken_gun <= 30:
        metin = "Merhaba, hizmet devamı için ödemenizin yapılması gerekmektedir. Lütfen kalan tutarı ödeyiniz."
    else:
        metin = "Ödeme yapılmadığı takdirde hukuki işlem başlatılacaktır. Lütfen derhal ödeme yapınız."
    metin += f"\n\nBakiye: {toplam_alacak:,.2f} ₺"
    return jsonify({"ok": True, "metin": metin})
